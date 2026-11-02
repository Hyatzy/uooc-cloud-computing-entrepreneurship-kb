#!/usr/bin/env python3
"""Convert the two authorized CAJ containers; never modify the originals.
Uses upstream caj2pdf for PDF-object reconstruction and existing PyMuPDF
instead of mutool/PyPDF2 for xref repair and bookmarks. No decryption path.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import fitz

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "工具/caj2pdf-upstream"))
import cajparser
OUT = BASE / "派生资料/CAJ转换"

def repair(args, **kwargs):
    if args[:2] != ["mutool", "clean"]:
        raise ValueError(args)
    with fitz.open(args[2]) as doc:
        doc.save(args[3], garbage=4, deflate=True)
    return b""

def outlines(toc, src, dest):
    with fitz.open(src) as doc:
        doc.set_toc([[x["level"], x["title"].decode("utf-8"), x["page"]] for x in toc])
        doc.save(dest, garbage=4, deflate=True)

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cajparser.check_output = repair
    cajparser.add_outlines = outlines
    for kid in ["KB062", "KB072"]:
        src = next((BASE / "原始资料").glob(kid + "__*"))
        before = hashlib.sha256(src.read_bytes()).hexdigest()
        dest = OUT / (src.stem + ".converted.pdf")
        log = io.StringIO()
        parser = cajparser.CAJParser(str(src))
        assert parser.format == "CAJ"
        with tempfile.TemporaryDirectory(prefix="caj-convert-") as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                with contextlib.redirect_stdout(log):
                    parser.convert(str(dest))
            finally:
                os.chdir(old)
        assert before == hashlib.sha256(src.read_bytes()).hexdigest()
        (OUT / (kid + ".conversion.log")).write_text(log.getvalue(), encoding="utf-8")
        with fitz.open(dest) as doc:
            assert len(doc) == parser.page_num
            pages = [{"physical_page": i + 1, "text": p.get_text()} for i, p in enumerate(doc)]
            result = {"id": kid, "source_path": str(src.relative_to(BASE)),
                      "source_sha256": before, "source_format": "CAJ",
                      "expected_pages": parser.page_num, "pdf_pages": len(doc),
                      "pdf_path": str(dest.relative_to(BASE)),
                      "page_numbering": "one-based physical sequence; not printed thesis page numbers",
                      "empty_text_pages": [p["physical_page"] for p in pages if not p["text"].strip()],
                      "pages": pages}
        (OUT / (src.stem + ".pages.json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / (src.stem + ".txt")).write_text("\n\n".join("=== 文件物理页 %d ===\n%s" % (p["physical_page"], p["text"]) for p in pages), encoding="utf-8")
        print(kid, result["pdf_pages"], "pages", sum(len(p["text"]) for p in pages), "characters")

if __name__ == "__main__":
    main()
