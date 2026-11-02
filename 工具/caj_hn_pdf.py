#!/usr/bin/env python3
"""Reconstruct four authorized HN documents as page-image PDFs.
Native text is independently preserved by caj_hn_text_extract.py.
The required JBIG/JBIG2 libraries are compiled from the upstream source.
"""
import contextlib
import hashlib
import io
import os
from pathlib import Path
import tempfile
import fitz
import caj_convert

BASE = caj_convert.BASE
OUT = caj_convert.OUT
UPSTREAM = BASE / "工具/caj2pdf-upstream"

def main():
    caj_convert.cajparser.add_outlines = caj_convert.outlines
    for kid in ["KB067", "KB069", "KB070", "KB076"]:
        src = next((BASE / "原始资料").glob(kid + "__*"))
        before = hashlib.sha256(src.read_bytes()).hexdigest()
        dest = OUT / (src.stem + ".converted.pdf")
        log = io.StringIO()
        parser = caj_convert.cajparser.CAJParser(str(src))
        assert parser.format == "HN"
        with tempfile.TemporaryDirectory(prefix="hn-convert-") as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            for lib in ["libjbigdec.so", "libjbig2codec.so"]:
                Path(lib).symlink_to(UPSTREAM / lib)
            try:
                with contextlib.redirect_stdout(log):
                    parser.convert(str(dest))
            finally:
                os.chdir(old)
        assert before == hashlib.sha256(src.read_bytes()).hexdigest()
        (OUT / (kid + ".conversion.log")).write_text(log.getvalue(), encoding="utf-8")
        with fitz.open(dest) as doc:
            assert len(doc) == parser.page_num
            print(kid, len(doc), "pages")

if __name__ == "__main__":
    main()
