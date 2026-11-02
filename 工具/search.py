#!/usr/bin/env python3
"""Search complete phrases in course materials and recorded quizzes with provenance."""
import argparse
import json
import sqlite3
import unicodedata
from pathlib import Path


def normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if not c.isspace() and (not unicodedata.category(c).startswith("P") or c in "./-%"))


def find_hits(root, query, section=None, document_id=None, source_kind=None):
    needle = normalize(query)
    if not needle:
        raise ValueError("查询必须包含空白、标点以外的内容")
    database = root / "index.sqlite3"
    if database.exists():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            has_fts = connection.execute("SELECT 1 FROM sqlite_master WHERE name='chunks_fts'").fetchone()
            if has_fts and len(needle) >= 3:
                rows = connection.execute(
                    "SELECT chunks.metadata FROM chunks JOIN chunks_fts ON chunks.id=chunks_fts.rowid "
                    "WHERE chunks_fts MATCH ? AND (instr(chunks.normalized_text, ?) > 0 OR instr(chunks.normalized_title, ?) > 0)",
                    ('"' + needle.replace('"', '""') + '"', needle, needle))
            else:
                # instr also supports Chinese queries shorter than three characters.
                rows = connection.execute(
                    "SELECT metadata FROM chunks WHERE instr(normalized_text, ?) > 0 OR instr(normalized_title, ?) > 0",
                    (needle, needle))
            candidates = [json.loads(row[0]) for row in rows]
    else:
        payload = json.loads((root / "chunks.json").read_text(encoding="utf-8"))
        candidates = [item for item in payload["chunks"] if needle in normalize(item["text"])
                      or needle in normalize(item["title"])]
    hits = [item for item in candidates
            if (section is None or normalize(section) in normalize(item.get("section", "")))
            and (document_id is None or item["document_id"] == document_id)
            and (source_kind is None or item.get("source_kind", "course_material") == source_kind)]
    for item in hits:
        item["match_type"] = "原文短语" if query in item["text"] else (
            "规范化正文候选（须核对原文语义）" if needle in normalize(item["text"]) else "标题短语")
    order = {"原文短语": 0, "规范化正文候选（须核对原文语义）": 1, "标题短语": 2}
    return sorted(hits, key=lambda item: (order[item["match_type"]], item["chunk_id"]))


def excerpt(text, query, max_chars):
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    # Preserve the source text while locating a match across PDF line breaks.
    characters, positions = [], []
    for index, char in enumerate(text):
        for normalized in normalize(char):
            characters.append(normalized)
            positions.append(index)
    match = "".join(characters).find(normalize(query))
    start = max(0, (positions[match] if match >= 0 else 0) - max_chars // 4)
    end = min(len(text), start + max_chars)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="完整题干或选项短语；不自动改写或拆词")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--section", help="只查匹配章节")
    parser.add_argument("--document", help="只查指定资料或题集 ID")
    parser.add_argument("--source-kind", choices=("course_material", "quiz_platform"), help="只查课程资料或平台题集")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-chars", type=int, default=1600, help="每段显示字符数，0 表示全文")
    parser.add_argument("--json", action="store_true", help="输出含完整原文的 JSON")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须大于 0")
    try:
        hits = find_hits(args.root.resolve(), args.query, args.section, args.document, args.source_kind)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"检索失败：{exc}\n请先运行 工具/build_index.py。\n")
    if args.json:
        print(json.dumps({"query": args.query, "total": len(hits), "hits": hits[:args.limit]},
                         ensure_ascii=False, indent=2))
        return
    print(f"命中 {len(hits)} 段，显示 {min(len(hits), args.limit)} 段。规范化候选仅供定位，须核对原文语义；保留 . / - % 与数学符号。")
    for index, item in enumerate(hits[:args.limit], 1):
        print(f"\n{index}. {item['title']} [{item['document_id']}]\n"
              f"章：{item.get('chapter', '')}\n章节：{item.get('section', '')}\n位置：{item['location']}\n"
              f"资料类型：{item.get('source_kind', 'course_material')}\n"
              f"匹配：{item['match_type']}\n来源：{item['url']}\n"
              f"正文：{args.root.resolve() / item['body_path']}\n\n"
              f"{excerpt(item['text'], args.query, args.max_chars)}")
        if item.get("source_kind") == "quiz_platform":
            print("[quiz_platform] 平台记录不自动等同知识正确；请先核对题干与选项内容，再映射本题字母。")
            if item.get("conflict"):
                conflict = item["conflict"]
                print(f"[冲突：{conflict['status']}] 实际作答：{item['submitted_answer']}；"
                      f"平台答案键：{item['platform_answer']}；解析对应选项：{conflict['explanation_mapped_answer']}。\n"
                      f"{conflict['note']}")
        if item.get("converted_pdf_path"):
            print(f"校对用转换 PDF：{args.root.resolve() / item['converted_pdf_path']}")
        if item.get("text_status") == "needs_review":
            print("[needs_review] 本页含异常字形；引用前请对照原文件。")
        elif item.get("text_status") == "ocr_unverified":
            print(f"[ocr_unverified] OCR 补充文本，未人工核验；方法：{item.get('ocr_method')}；"
                  f"置信度：{item.get('ocr_confidence')}。引用前请对照原文件。")
    if not hits:
        print("当前已提取文字中没有该短语；不代表原资料不存在相关内容。可缩短短语，并检查 needs_ocr 文档。")


if __name__ == "__main__":
    main()
