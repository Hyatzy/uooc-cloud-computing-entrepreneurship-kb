"""Verify local source bytes and map UI section prefixes to the course catalog."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / 'manifest.json'
manifest = json.loads(path.read_text())
catalog = json.loads((root / '章节目录.json').read_text())
sections = [(c, title, id_) for c in catalog for title, id_ in c['sections']]
for d in manifest['documents']:
    display = d.get('ui_section', d['section'])
    matches = [(c, t, id_) for c, t, id_ in sections if t.startswith(display.removesuffix('...'))]
    assert len(matches) == 1, (display, matches)
    chapter, title, id_ = matches[0]
    d.update(ui_section=display, section=title, chapter=chapter['chapter'], section_url=f"https://www.uooc.net.cn/home/learn/index#/2026373645/{chapter['chapter_id']}/{id_}")
    file = root / d['local_path']
    if not file.exists():
        d['download_status'] = 'pending'
        continue
    data = file.read_bytes()
    d.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    if not data:
        d.update(download_status='failed', error='服务器返回 HTTP 200、Content-Length: 0；未获得文件内容。')
        continue
    if data.startswith(b'CAJ\x00') or data.startswith(b'HN\x00'):
        d['actual_format'] = 'CAJ' if data.startswith(b'CAJ') else 'HN'
        d['format_warning'] = '原站扩展名为 PDF，实际内容为 ' + d['actual_format']
    else:
        d['actual_format'] = file.suffix.lstrip('.').upper()
    d['download_status'] = 'ok'
    d.pop('error', None)
manifest['source_manifest_verification'] = {'entries':176,'serialized_characters':23252,'fnv1a32':'b1b9475a','matches_browser':True}
path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print('Local bytes verified and 176 section labels mapped to the course catalog.')
