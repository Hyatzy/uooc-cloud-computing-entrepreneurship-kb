#!/usr/bin/env python3
"""Extract manifest documents with source locations; build JSON and SQLite indexes."""
import argparse
import hashlib
import json
import posixpath
import re
import sqlite3
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if not c.isspace() and (not unicodedata.category(c).startswith("P") or c in "./-%"))


def block(text, location, **position):
    return {"text": text.strip(), "location": location, **position}


def docx_blocks(path):
    with ZipFile(path) as archive:
        body = ET.fromstring(archive.read("word/document.xml")).find(W + "body")
        images = sum(name.startswith("word/media/") for name in archive.namelist())
    blocks, counts = [], {"paragraph": 0, "table": 0}

    def walk(parent, prefix=""):
        for node in parent:
            if node.tag == W + "p":
                counts["paragraph"] += 1
                text = "".join(n.text or "" if n.tag == W + "t" else
                               "\t" if n.tag == W + "tab" else "\n"
                               for n in node.iter() if n.tag in
                               {W + "t", W + "tab", W + "br", W + "cr"})
                if text.strip():
                    number = counts["paragraph"]
                    blocks.append(block(text, f"{prefix}段落 {number}", paragraph=number))
            elif node.tag == W + "tbl":
                counts["table"] += 1
                table = counts["table"]
                for row, tr in enumerate(node.findall(W + "tr"), 1):
                    for col, tc in enumerate(tr.findall(W + "tc"), 1):
                        walk(tc, f"{prefix}表 {table} · 行 {row} · 列 {col} · ")
            elif node.tag in {W + "sdt", W + "sdtContent", W + "customXml"}:
                walk(node, prefix)

    if body is None:
        raise ValueError("DOCX 缺少正文 body")
    walk(body)
    return blocks, [], ([f"含 {images} 个媒体文件；图片文字未做 OCR。"] if images else [])


def pdf_blocks(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    blocks, pages = [], []
    fallback = None
    try:
        for number, page in enumerate(reader.pages, 1):
            info = {"page": number, "extractor": "pypdf"}
            try:
                try:
                    text = page.extract_text() or ""
                except Exception as primary_error:
                    import pdfplumber
                    if fallback is None:
                        fallback = pdfplumber.open(path)
                    text = fallback.pages[number - 1].extract_text() or ""
                    info.update(extractor="pdfplumber", primary_error=str(primary_error))
                suspicious = sum(char == "\ufffd" or unicodedata.category(char) == "Co" for char in text)
                status = "extracted" if text.strip() else "needs_ocr"
                if suspicious >= 20 and suspicious / max(len(text), 1) > 0.03:
                    status = "needs_review"
                    info["warning"] = f"含 {suspicious} 个替代/私用区字形；文字或公式需对照原件。"
                info["status"] = status
                pages.append(info)
                if text.strip():
                    blocks.append(block(text, f"第 {number} 页", page=number, text_status=status))
            except Exception as exc:
                pages.append({**info, "status": "extraction_failed", "error": str(exc)})
    finally:
        if fallback is not None:
            fallback.close()
    return blocks, pages, ["仅索引可提取文字；图片、公式和图表的视觉内容未做 OCR。"]


def pptx_blocks(path):
    blocks, pages = [], []
    with ZipFile(path) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        relationships = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        targets = {n.attrib["Id"]: n.attrib["Target"] for n in relationships}
        slides = presentation.find(P + "sldIdLst")
        if slides is None:
            return [], [], ["PPTX 没有幻灯片。"]
        for number, slide in enumerate(slides, 1):
            target = targets[slide.attrib[R + "id"]]
            name = target.lstrip("/") if target.startswith("/") else posixpath.normpath("ppt/" + target)
            root = ET.fromstring(archive.read(name))
            before = len(blocks)
            for paragraph, node in enumerate(root.iter(A + "p"), 1):
                text = "".join(n.text or "" if n.tag == A + "t" else "\n"
                               for n in node.iter() if n.tag in {A + "t", A + "br"})
                if text.strip():
                    blocks.append(block(text, f"幻灯片 {number} · 文本段落 {paragraph}",
                                        page=number, paragraph=paragraph))
            pages.append({"page": number, "status": "extracted" if len(blocks) > before else "needs_ocr"})
    return blocks, pages, ["按幻灯片及 XML 文本顺序提取；图片、图表和演讲者备注未纳入正文。"]


def doc_blocks(path):
    result = subprocess.run(["/usr/bin/textutil", "-convert", "txt", "-stdout", "-encoding", "UTF-8", str(path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=90, check=True)
    blocks = [block(text, f"转换文本行 {number}", paragraph=number)
              for number, text in enumerate(result.stdout.splitlines(), 1) if text.strip()]
    return blocks, [], ["由 macOS textutil 转换；定位为转换文本行，原始分页及表格结构未保留。"]


def md_blocks(path):
    content = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^## 第 (\d+) 页\s*$", content, re.M))
    blocks, pages = [], []
    for idx, match in enumerate(matches):
        number = int(match.group(1))
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        text = content[start:end].strip()
        pages.append({"page": number, "status": "extracted", "extractor": "markdown_ocr"})
        if text:
            blocks.append(block(text, f"第 {number} 页", page=number, text_status="extracted"))
    return blocks, pages, ["由原大体积幻灯片 PDF 经高精度 OCR 与逐页视觉校对转换为 Markdown。"]


def converted_blocks(root, conversion, source_hash):
    if conversion["original_sha256"] != source_hash:
        raise ValueError("CAJ/HN 转换记录的原件 SHA-256 不一致")
    metadata_path = (root / conversion["pages_metadata_path"]).resolve()
    text_path = (root / conversion["text_path"]).resolve()
    if not metadata_path.is_relative_to(root) or not text_path.is_relative_to(root):
        raise ValueError("CAJ/HN 派生资料路径超出知识库")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata["source_sha256"] != source_hash:
        raise ValueError("CAJ/HN 逐页文本的原件 SHA-256 不一致")
    raw, starts = "", {}
    if conversion["actual_format"] == "HN":
        raw = text_path.read_bytes().decode("utf-8")
        starts = {int(match.group(1)): match.end() for match in re.finditer(
            r"^=== Physical page (\d+) \| HN page_no [^\n]+ ===\n", raw, re.M)}
    blocks, pages = [], []
    for item in metadata["pages"]:
        number = item["physical_page"]
        if conversion["actual_format"] == "HN":
            start = starts[number]
            text = raw[start:start + item["character_count"]]
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != item["text_sha256"]:
                raise ValueError(f"HN 物理页 {number} 文本 SHA-256 不一致")
        else:
            text = item["text"]
        status = "extracted" if text.strip() else "needs_ocr"
        warning = ""
        if item.get("placeholder_count", 0) or item.get("nul_count", 0):
            status = "needs_review"
            warning = f"原文字层有 {item.get('placeholder_count', 0)} 个未知字形占位符及 {item.get('nul_count', 0)} 个 NUL；原样保留。"
        pages.append({"page": number, "status": status, "method": "caj_hn", "warning": warning})
        if text.strip():
            blocks.append(block(text, f"文件物理页 {number} · {conversion['actual_format']} 文字层",
                                page=number, text_status=status, method="caj_hn",
                                conversion_method=conversion["method"]))
    if len(pages) != conversion["page_count"]:
        raise ValueError("CAJ/HN 转换页数与验证记录不一致")
    return blocks, pages, ["CAJ/HN 原文字层经转换导入，未校正；物理页码不一定等于论文印刷页码。",
                          "原识别错误、重复叠印标题和公式文字可能保留，引用请核对转换 PDF。"]


def append_ocr(root, document, report, blocks, pages):
    relative = document.get("ocr_path") or f"派生资料/OCR/{document['id']}.json"
    path = (root / relative).resolve()
    if not path.is_file():
        return
    try:
        if not path.is_relative_to(root):
            raise ValueError("OCR 路径超出知识库目录")
        data = json.loads(path.read_text(encoding="utf-8"))
        if str(data["document_id"]) != document["id"] or data["source_sha256"] != report.get("actual_sha256"):
            raise ValueError("OCR 的文档 ID 或 source_sha256 与原件不一致")
        page_map = {item["page"]: item for item in pages}
        seen = set()
        for item in data["pages"]:
            number = item["page"]
            if not isinstance(number, int) or number not in page_map or number in seen:
                raise ValueError(f"OCR 页码重复或超出原件范围：{number}")
            if not isinstance(item.get("text"), str):
                raise ValueError(f"OCR 第 {number} 页的 text 不是字符串")
            seen.add(number)
        added = 0
        for item in data["pages"]:
            number, text = item["page"], item["text"]
            if not text.strip():
                report["warnings"].append(f"第 {number} 页 OCR 仍无文本，保留原状态。")
                continue
            page = page_map[number]
            page["original_status"] = page["status"]
            page["status"] = "ocr_unverified"
            blocks.append(block(text, f"第 {number} 页 · OCR（未人工核验）", page=number,
                                text_status="ocr_unverified", ocr_method=item.get("method", "unknown"),
                                ocr_confidence=item.get("confidence"), ocr_warnings=item.get("warnings", [])))
            added += 1
        report.update(ocr_path=str(path.relative_to(root)), ocr_status="imported", ocr_page_count=added)
        if added:
            report["warnings"].append("OCR 是补充识别文本，未人工核验；原提取正文完整保留，引用请对照原件。")
            statuses = {page["status"] for page in pages}
            report["extraction_status"] = next((status for status in
                ("extraction_failed", "needs_ocr", "needs_review", "ocr_unverified") if status in statuses),
                report["extraction_status"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["ocr_status"] = "rejected"
        report["warnings"].append(f"未导入 OCR：{exc}")


def write_body(root, document, report, blocks, pages):
    path = root / "正文" / f"{document['id']}.md"
    lines = [f"# {document.get('title', document['id'])}", "",
             f"- 文档 ID：{document['id']}", f"- 来源：{document.get('url', '')}",
             f"- 章：{document.get('chapter', '')}",
             f"- 章节：{document.get('section', '')}", f"- 分类：{document.get('category', '')}",
             f"- 原文件：{document.get('local_path', '')}",
             f"- 提取文件：{report.get('extraction_source', document.get('local_path', ''))}",
             f"- 补充 OCR：{report.get('ocr_path', '无')}",
             f"- 提取状态：{report['extraction_status']}", ""]
    lines += [f"> {note}" for note in report.get("warnings", [])]
    if report.get("converted_pdf_path"):
        lines += [f"- 校对用转换 PDF：{report['converted_pdf_path']}"]
    if report.get("error"):
        lines += [f"> 未完整提取：{report['error']}"]
    if pages:
        for page in pages:
            page_blocks = [item for item in blocks if item.get("page") == page["page"]]
            if page["status"] != "extracted":
                note = page.get("warning") or page.get("error") or "未提取到可靠正文；不能据此认定页面没有内容。"
                if page["status"] == "ocr_unverified":
                    note = f"原始状态：{page['original_status']}；附加 OCR 文本未人工核验，未替换原提取内容。"
                lines += ["", f"## 第 {page['page']} 页 / 幻灯片", "",
                          f"[{page['status']}] {note}"]
            for item in page_blocks:
                lines += ["", f"## {item['location']}", "", item["text"]]
    else:
        for item in blocks:
            lines += ["", f"## {item['location']}", "", item["text"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path.relative_to(root))


def quiz_file_records(root, path):
    relative = str(path.relative_to(root))
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("source_kind") != "quiz_platform":
        return [], []
    if data.get("schema_version") != 1:
        raise ValueError("题集必须使用 schema_version=1、source_kind=quiz_platform")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    reports, chunks, question_ids = [], [], set()
    for quiz in data["quizzes"]:
        body_path = (root / quiz["body_path"]).resolve()
        if not body_path.is_relative_to(root) or not body_path.is_file():
            raise ValueError(f"题集正文不存在或超出知识库：{quiz['body_path']}")
        common = {"source_kind": "quiz_platform", "document_id": quiz["id"],
                  "title": quiz["title"], "url": quiz["source_url"], "section": quiz["section"],
                  "chapter": data["chapter"], "course": data["course"], "course_id": data["course_id"],
                  "category": "小节测验" if "小节" in quiz["title"] else "章节测验",
                  "local_path": relative, "body_path": quiz["body_path"],
                  "source_sha256": source_hash, "captured_at": data["captured_at"],
                  "submitted_at": quiz["submitted_at"]}
        reports.append({**{key: value for key, value in quiz.items() if key != "questions"},
                        **common, "extraction_status": "platform_recorded", "pages": [],
                        "chunk_count": len(quiz["questions"]),
                        "warnings": ["平台记录仅证明页面显示内容；平台答案键不自动等同知识正确。"]})
        for question in quiz["questions"]:
            if question["id"] in question_ids:
                raise ValueError(f"重复题目 ID：{question['id']}")
            question_ids.add(question["id"])
            options = {option["label"]: option["text"] for option in question["options"]}
            if len(options) != len(question["options"]):
                raise ValueError(f"选项标签重复：{question['id']}")

            def answer_text(answer):
                return "、".join(f"{label}（{options[label]}）" if label in options else label
                                 for label in answer)

            lines = [f"题干：{question['stem']}", "选项："]
            lines += [f"{option['label']}. {option['text']}" for option in question["options"]]
            lines += [f"实际作答（平台记录）：{answer_text(question['submitted_answer'])}",
                      f"平台答案键（不自动等同知识正确）：{answer_text(question['platform_answer'])}",
                      f"平台解析：{question['platform_explanation']}",
                      f"教师评语：{question['teacher_comment']}",
                      f"平台得分：{question['awarded_score']} / {question['max_score']}"]
            conflict = question.get("conflict")
            if conflict:
                lines += [f"冲突状态：{conflict['status']}",
                          f"解析对应选项（独立记录）：{answer_text(conflict['explanation_mapped_answer'])}",
                          f"冲突说明：{conflict['note']}"]
            lines += [f"备注：{note}" for note in question.get("notes", [])]
            chunks.append({**question, **common, "question_id": question["id"],
                           "location": f"{question['type']} 第 {question['number']} 题 · {question['id']}",
                           "text_status": "platform_recorded", "text": "\n".join(lines)})
    return reports, chunks


def quiz_records(root):
    reports, chunks, question_ids = [], [], set()
    for path in sorted((root / "题集").glob("*.json")):
        file_reports, file_chunks = quiz_file_records(root, path)
        for question in file_chunks:
            if question["question_id"] in question_ids:
                raise ValueError(f"重复题目 ID：{question['question_id']}")
            question_ids.add(question["question_id"])
        reports.extend(file_reports)
        chunks.extend(file_chunks)
    return reports, chunks


def build(root):
    root = root.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    documents = manifest["documents"]
    quiz_reports, quiz_chunks = quiz_records(root)
    conversion_path = root / "派生资料/CAJ转换/转换来源与验证.json"
    conversions = ({item["id"]: item for item in json.loads(conversion_path.read_text(encoding="utf-8"))["documents"]}
                   if conversion_path.is_file() else {})
    ids = [str(item["id"]) for item in documents + quiz_reports]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r"[\w.-]+", i) or i in {".", ".."} for i in ids):
        raise ValueError("资料和题集 ID 必须唯一，且不能包含路径分隔符")
    (root / "正文").mkdir(parents=True, exist_ok=True)
    extractors = {".docx": docx_blocks, ".pdf": pdf_blocks, ".pptx": pptx_blocks, ".doc": doc_blocks, ".md": md_blocks}
    reports, chunks = [], []
    for original in documents:
        document = {**original, "id": str(original["id"]), "source_kind": "course_material"}
        report = {**document, "extraction_status": "not_extracted", "warnings": []}
        blocks, pages = [], []
        try:
            if document.get("download_status") not in {"ok", "downloaded", "success", "complete", "completed", "cached", "reused", "existing"}:
                raise ValueError(f"资料未成功下载：{document.get('download_status', 'unknown')}")
            relative = document.get("local_path")
            if not relative:
                raise ValueError("manifest 没有 local_path，资料未下载")
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError("local_path 超出知识库目录")
            if not path.is_file():
                raise FileNotFoundError("原始资料文件尚不存在")
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            report["actual_sha256"] = actual_hash
            if document.get("sha256") and actual_hash != document["sha256"]:
                raise ValueError("原文件 SHA-256 与 manifest 不一致")
            actual_format = str(document.get("actual_format") or "").upper()
            conversion = conversions.get(document["id"]) if actual_format in {"CAJ", "HN"} else None
            extraction_path = document.get("extraction_path")
            if extraction_path:
                path = (root / extraction_path).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError("extraction_path 超出知识库或对应文件不存在")
                report["extraction_source"] = str(path.relative_to(root))
                report["extraction_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            extractor = extractors.get(path.suffix.lower())
            if actual_format and actual_format not in {"DOCX", "PDF", "DOC", "PPTX", "MD"} and not extraction_path and not conversion:
                extractor = None
            if extractor is None:
                report["extraction_status"] = "unsupported_format"
                report["error"] = f"不支持此实际格式（{actual_format or path.suffix}）；未推测正文。"
            else:
                if conversion:
                    blocks, pages, warnings = converted_blocks(root, conversion, actual_hash)
                    report.update(extraction_source=conversion["pages_metadata_path"],
                                  converted_pdf_path=conversion["pdf_path"], method="caj_hn")
                else:
                    blocks, pages, warnings = extractor(path)
                report["warnings"] = warnings
                statuses = {page["status"] for page in pages}
                report["extraction_status"] = (
                    "extraction_failed" if "extraction_failed" in statuses else
                    "needs_ocr" if "needs_ocr" in statuses else
                    "needs_review" if "needs_review" in statuses else
                    "extracted" if blocks else "no_text")
                if not blocks and not pages:
                    report["warnings"].append("未提取到正文；可能需要 OCR 或人工检查。")
        except Exception as exc:
            report["extraction_status"] = "extraction_failed"
            report["error"] = f"{type(exc).__name__}: {exc}"
        append_ocr(root, document, report, blocks, pages)
        report["pages"] = pages
        report["chunk_count"] = len(blocks)
        report["body_path"] = write_body(root, document, report, blocks, pages)
        reports.append(report)
        for item in blocks:
            chunks.append({"chunk_id": len(chunks) + 1, "document_id": document["id"],
                           "source_kind": "course_material",
                           **{key: document.get(key, "") for key in ("title", "url", "chapter", "section", "section_url", "category", "local_path", "extraction_path")},
                           "body_path": report["body_path"], "source_sha256": report.get("actual_sha256"),
                           "converted_pdf_path": report.get("converted_pdf_path"), **item})
        print(f"{document['id']}: {report['extraction_status']} ({len(blocks)} 段)", flush=True)
    reports.extend(quiz_reports)
    for item in quiz_chunks:
        chunks.append({**item, "chunk_id": len(chunks) + 1})
    for report in quiz_reports:
        print(f"{report['id']}: platform_recorded ({report['chunk_count']} 题)", flush=True)
    database = root / "index.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("DROP TABLE IF EXISTS chunks_fts; DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS documents;")
        connection.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
        connection.executemany("INSERT INTO documents VALUES (?, ?)",
                               [(item["id"], json.dumps(item, ensure_ascii=False)) for item in reports])
        connection.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id TEXT, normalized_text TEXT, normalized_title TEXT, metadata TEXT NOT NULL)")
        connection.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                               [(item["chunk_id"], item["document_id"], normalize(item["text"]), normalize(item["title"]),
                                 json.dumps(item, ensure_ascii=False)) for item in chunks])
        try:
            connection.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(normalized_text, normalized_title, tokenize='trigram')")
            connection.execute("INSERT INTO chunks_fts(rowid, normalized_text, normalized_title) SELECT id, normalized_text, normalized_title FROM chunks")
            engine = "fts5_trigram"
        except sqlite3.OperationalError:
            connection.execute("DROP TABLE IF EXISTS chunks_fts")
            engine = "substring"
    payload = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
               "search_engine": engine, "documents": reports, "chunks": chunks}
    (root / "chunks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {}
    for item in reports:
        status = item["extraction_status"]
        summary[status] = summary.get(status, 0) + 1
    print(json.dumps({"documents": len(reports), "chunks": len(chunks), "statuses": summary,
                      "search_engine": engine}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    build(parser.parse_args().root.resolve())
