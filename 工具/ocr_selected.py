"""OCR pages flagged by the extractor, retaining provenance and OCR warnings."""
import concurrent.futures
import json
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parents[1]
manifest={d['id']:d for d in json.loads((root/'manifest.json').read_text())['documents']}
reports=json.loads((root/'chunks.json').read_text())['documents']
conversions={d['id']:d for d in json.loads((root/'派生资料'/'CAJ转换'/'转换来源与验证.json').read_text())['documents']}
output=root/'派生资料'/'OCR'
output.mkdir(parents=True,exist_ok=True)
jobs=[]
for report in reports:
    if report['id'] not in manifest: continue
    pages=[p['page'] for p in report.get('pages',[]) if p.get('status') in {'needs_ocr','needs_review'}]
    if pages and Path(manifest[report['id']]['local_path']).suffix.lower()=='.pdf':
        jobs.append((report['id'],pages))

def run(job):
    id_,pages=job
    doc=manifest[id_]
    raw=output/f'{id_}.raw.json'
    input_path=conversions[id_]['pdf_path'] if id_ in conversions else doc['local_path']
    subprocess.run([str(root/'工具'/'ocr_pdf'),str(root/input_path),str(raw),','.join(map(str,pages))],check=True,capture_output=True,text=True,timeout=240)
    recognized=json.loads(raw.read_text())
    assert sorted(p['page'] for p in recognized)==sorted(pages)
    dest=output/f'{id_}.json'
    previous=json.loads(dest.read_text()) if dest.exists() else {'source_sha256':doc['sha256'],'pages':[]}
    assert previous['source_sha256']==doc['sha256']
    merged={p['page']:p for p in previous['pages']}
    merged.update({p['page']:p for p in recognized})
    data={'document_id':id_,'source_sha256':doc['sha256'],'rendered_from':input_path,'pages':[merged[k] for k in sorted(merged)]}
    dest.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    raw.unlink()
    return {'document_id':id_,'pages':len(pages),'recognized_pages':sum(bool(p.get('text','').strip()) for p in recognized)}

print(f'OCR targets: {len(jobs)} documents, {sum(len(p) for _,p in jobs)} pages',flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    for result in pool.map(run,jobs): print(json.dumps(result,ensure_ascii=False),flush=True)
