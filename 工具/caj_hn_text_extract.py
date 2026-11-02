#!/usr/bin/env python3
"""Extract unchanged HN text with the upstream parser and physical-page metadata."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import zlib

sys.dont_write_bytecode = True
UPSTREAM = Path(__file__).resolve().parent / "caj2pdf-upstream"
sys.path.insert(0, str(UPSTREAM))
from cajparser import CAJParser
from HNParsePage import HNParsePage


def extract(source, output_dir):
    parser = CAJParser(str(source))
    if parser.format not in ("HN", "C8"):
        raise ValueError(f"Expected HN/C8, found {parser.format}: {source}")
    parts, pages = [], []
    with source.open("rb") as stream:
        for index in range(parser.page_num):
            stream.seek(parser._TOC_END_OFFSET + index * 20)
            offset, size, images, page_no, unknown, next_offset = struct.unpack(
                "iihhii", stream.read(20)
            )
            stream.seek(offset)
            header = stream.read(32)
            compressed = header[:12] == b"COMPRESSTEXT" or header[8:20] == b"COMPRESSTEXT"
            if compressed:
                prefix = 0 if header[:12] == b"COMPRESSTEXT" else 8
                expected = struct.unpack("i", header[12 + prefix:16 + prefix])[0]
                stream.seek(offset + 16 + prefix)
                data = zlib.decompress(stream.read(size - 16 - prefix), bufsize=expected)
                if len(data) != expected:
                    raise ValueError(f"Page {index + 1}: unexpected decompressed size")
            else:
                stream.seek(offset)
                data = stream.read(size)
            page = HNParsePage(data, next_offset > offset)
            text = page.texts
            # Keep the upstream text exactly, including NULs and carriage returns.
            parts.append(f"=== Physical page {index + 1} | HN page_no {page_no} ===\n{text}\n\n")
            pages.append({
                "physical_page": index + 1,
                "hn_page_no": page_no,
                "character_count": len(text),
                "blank": not text.strip(),
                "placeholder_count": len(re.findall(r"<0x[0-9A-Fa-f]+>", text)),
                "nul_count": text.count("\x00"),
                "carriage_return_count": text.count("\r"),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text_compressed": compressed,
                "page_data_offset": offset,
                "text_section_size": size,
                "images_per_page": images,
                "figure_count": len(page.figures),
                "figures_xywh": page.figures,
                "first_figure_at_origin": bool(page.figures and page.figures[0][:2] == [0, 0]),
                "unknown_page_field": unknown,
                "next_page_data_offset": next_offset,
            })
    base_dir = Path(__file__).resolve().parents[1]
    rel_source = str(source.resolve().relative_to(base_dir)) if source.resolve().is_relative_to(base_dir) else source.name
    metadata = {
        "source": rel_source,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "format": parser.format,
        "method": "Unmodified upstream CAJParser metadata and HNParsePage text decoder",
        "upstream": "https://github.com/caj2pdf/caj2pdf",
        "parser_sha256": hashlib.sha256((UPSTREAM / "HNParsePage.py").read_bytes()).hexdigest(),
        "page_number_note": "physical_page is 1-based file order; hn_page_no is the stored HN field, not verified printed pagination",
        "text_note": "Embedded text extracted without correction or OCR; original NULs, CRs and decoder placeholders preserved",
        "page_count": len(pages),
        "blank_pages": [p["physical_page"] for p in pages if p["blank"]],
        "placeholder_count": sum(p["placeholder_count"] for p in pages),
        "nul_count": sum(p["nul_count"] for p in pages),
        "pages": pages,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{source.stem}.txt").write_bytes("".join(parts).encode("utf-8"))
    (output_dir / f"{source.stem}.pages.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"source": source.name, **{key: metadata[key] for key in
        ("page_count", "blank_pages", "placeholder_count", "nul_count")}}, ensure_ascii=False))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("sources", type=Path, nargs="+")
    cli.add_argument("--output-dir", type=Path, required=True)
    args = cli.parse_args()
    for source in args.sources:
        extract(source, args.output_dir)
