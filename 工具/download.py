"""Download only the URLs collected from the course document UI."""
import concurrent.futures
import hashlib
import json
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(path):
    with path.open('rb') as f:
        head = f.read(1024)
    if not head:
        raise ValueError('Source returned an empty file')
    ext = path.suffix.lower()
    if ext == '.pdf' and b'%PDF-' not in head and not head.startswith((b'CAJ', b'HN')):
        raise ValueError('Unrecognized PDF/CAJ/HN signature')
    if ext in ('.docx', '.pptx'):
        with zipfile.ZipFile(path) as z:
            if z.testzip():
                raise ValueError('Corrupt ZIP member')
    if ext == '.doc' and not (head.startswith(bytes.fromhex('d0cf11e0')) or head.startswith(b'PK') or head.startswith(b'{\\rtf')):
        raise ValueError('Unrecognized DOC signature')


def download(d):
    path = ROOT / d['local_path']
    try:
        if not path.exists():
            req = urllib.request.Request(d['url'], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=45) as response:
                content = response.read()
                d['resolved_url'] = response.url
                expected = response.headers.get('Content-Length')
                if expected and len(content) != int(expected):
                    raise ValueError('Content length mismatch')
            temp = path.with_name(path.name + '.part')
            temp.write_bytes(content)
            temp.replace(path)
        validate(path)
        d.update(download_status='ok', size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    except Exception as exc:
        d.update(download_status='failed', error=str(exc))
    return d


def main():
    source = json.loads((ROOT / '来源清单.json').read_text())
    entries = []
    seen = {}
    for group in source['groups']:
        for name in group['files']:
            filename = name if isinstance(name, str) else name['file']
            title = name if isinstance(name, str) else name['title']
            url = source['base'] + urllib.parse.quote(filename, safe='')
            id_ = f'KB{len(entries)+1:03d}'
            d = dict(id=id_, title=title, section=group['section'], category='课件文档', list_page=group['page'], url=url)
            if url in seen:
                d.update(local_path=seen[url]['local_path'], duplicate_of=seen[url]['id'])
            else:
                d['local_path'] = str(Path('原始资料') / (id_ + '__' + filename.replace('/', '／')))
                seen[url] = d
            entries.append(d)
    assert len(entries) == source['count'] == 176
    manifest = dict(course='云计算行业与创新创业对接范式', course_id='2026373645', source_page='https://www.uooc.net.cn/home/course/2026373645#/document', collected_at=datetime.now(timezone.utc).isoformat(), list_page_counts=dict(Counter(d['list_page'] for d in entries)), shared_materials_count=0, documents=entries)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for done, d in enumerate(pool.map(download, seen.values()), 1):
            if d['download_status'] != 'ok' or done % 20 == 0:
                print(json.dumps({'completed':done,'unique_total':len(seen),'id':d['id'],'status':d['download_status'],'error':d.get('error')},ensure_ascii=False), flush=True)
    for d in entries:
        if 'duplicate_of' in d:
            for k in ('download_status', 'size_bytes', 'sha256', 'error', 'resolved_url'):
                if k in seen[d['url']]:
                    d[k] = seen[d['url']][k]
    (ROOT/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'entries':len(entries),'unique_urls':len(seen),'download_status':dict(Counter(d['download_status'] for d in entries)),'unique_bytes':sum(d.get('size_bytes',0) for d in seen.values()),'formats':dict(Counter(Path(d['local_path']).suffix for d in seen.values()))},ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
