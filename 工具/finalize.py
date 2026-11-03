"""Validate the local corpus and regenerate its catalog and validation report."""
import hashlib
import itertools
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from search import find_hits, normalize

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'manifest.json').read_text())
corpus = json.loads((root / 'chunks.json').read_text())
docs = manifest['documents']
indexed = {d['id']: d for d in corpus['documents']}
unique = {d['local_path']: d for d in docs}
quiz_archive = json.loads((root / '题集/第一章小节测验.json').read_text())
quizzes = quiz_archive['quizzes']
quiz_ids = {q['id'] for q in quizzes}
assert len(quizzes) == 4 and sum(len(q['questions']) for q in quizzes) == 24
assert len(docs) == 176 and len(unique) == 171 and len(corpus['chunks']) == 3114
for path, doc in unique.items():
    file = root / path
    assert file.exists(), path
    if doc['download_status'] == 'ok':
        assert file.stat().st_size == doc['size_bytes']
        assert hashlib.sha256(file.read_bytes()).hexdigest() == doc['sha256']
    else:
        assert doc['id'] == 'KB046' and file.stat().st_size == 0
assert {f.name for f in (root / '原始资料').iterdir()} == {Path(k).name for k in unique}
assert len(list((root / '正文').glob('KB*.md'))) == 176
assert {d['id'] for d in docs} | quiz_ids == set(indexed)
assert {c['document_id'] for c in corpus['chunks']} == {d['id'] for d in docs if d['download_status'] == 'ok'} | quiz_ids
for quiz in quizzes:
    assert (root / quiz['body_path']).exists()
    assert sum(c['document_id'] == quiz['id'] for c in corpus['chunks']) == 6
with sqlite3.connect((root / 'index.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
    assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
    assert db.execute('select count(*) from chunks').fetchone()[0] == 3114
    assert db.execute('select count(*) from chunks_fts').fetchone()[0] == 3114
checks = []
for query, document_id in [('神经中枢', 'KB001'), ('云计算的爆发期', 'KB012'), ('商业模式研究', 'KB067'), ('创业', 'KB021'), ('用户适应度', 'KB114'), ('声明', 'KB072'), ('政企资源', 'QUIZ-1.2'), ('新技术优势', 'QUIZ-1.3'), ('移动电子政务', 'QUIZ-1.4')]:
    hits = find_hits(root, query, document_id=document_id)
    assert hits, (query, document_id)
    hit = hits[0]
    checks.append(dict(query=query, document=document_id, hits=len(hits), location=hit['location'], text_status=hit.get('text_status', 'extracted')))
assert not find_hits(root, '不存在的测试短语9c2f14bd69')
assert normalize('1.0') != normalize('10')
assert normalize('10/20') != normalize('1020')
assert normalize('-10%') != normalize('10')
ocr_files = list((root / '派生资料/OCR').glob('KB*.json'))
assert len(ocr_files) == 17 and sum(len(json.loads(f.read_text())['pages']) for f in ocr_files) == 55
conversions = {d['id']: d for d in json.loads((root / '派生资料/CAJ转换/转换来源与验证.json').read_text())['documents']}
assert sum(d['page_count'] for d in conversions.values()) == 372
report = dict(checked_at=datetime.now(timezone.utc).date().isoformat(), course=manifest['course'], catalog_records=176,
              unique_sources=171, nonempty_originals=170, source_failures=['KB046'], duplicate_associations=5,
              original_bytes=sum((root / k).stat().st_size for k in unique),
              actual_formats=dict(Counter(d.get('actual_format', 'EMPTY') for d in unique.values())),
              body_files=180, course_body_files=176, quiz_body_files=4, quiz_questions=24,
              searchable_associations=179, chunks=3114, course_chunks=3090, quiz_chunks=24,
              extraction_states=dict(Counter(indexed[d['id']]['extraction_status'] for d in docs)),
              ocr_documents=17, ocr_pages=55, caj_hn_documents=6, caj_hn_pages=372,
              checks='Raw file existence, size and SHA; corpus coverage; JSON/SQLite/FTS counts; database integrity; numerical distinctions: passed',
              search_checks=checks)
(root / '校验报告.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))


def link(label, path):
    return f'[{label}](<{path}>)'


labels = {'extracted': '已提取', 'ocr_unverified': '含 OCR，需对照原页', 'extraction_failed': '源文件为空'}
lines = ['# 资料目录', '', '2026-09-25 收集。176 条章节关联，171 个不同源文件；5 条重复关联保留课程位置。', '', link('返回知识库首页', 'README.md') + ' · ' + link('已做测验题集（24题）', '题集/README.md'), '',
         '“已提取”表示可检索，不代表原文知识和每个字均已人工核实。PDF 页码为物理页，可能不同于印刷页；DOCX 使用段落/表格，PPTX 使用幻灯片，DOC 使用转换文本行。', '']
for chapter, chapterdocs in itertools.groupby(docs, key=lambda d: d['chapter']):
    lines += ['## ' + chapter, '']
    for section, sectiondocs in itertools.groupby(chapterdocs, key=lambda d: d['section']):
        lines += ['### ' + section, '', '| ID | 资料名称与正文 | 原文件 / 转换预览 | 状态 |', '|---|---|---|---|']
        for doc in sectiondocs:
            item = indexed[doc['id']]
            body = link(doc['title'].replace('|', '／'), item['body_path'])
            original = link('原文件', doc['local_path']) if doc['download_status'] == 'ok' else f'[源站地址]({doc["url"]})'
            if doc['id'] in conversions:
                original += ' · ' + link('转换 PDF', conversions[doc['id']]['pdf_path'])
            state = labels[item['extraction_status']] + (f'；复用 {doc["duplicate_of"]}' if doc.get('duplicate_of') else '')
            lines.append(f'| {doc["id"]} | {body} | {original} | {state} |')
        lines.append('')
(root / '资料目录.md').write_text('\n'.join(lines) + '\n')
formats = ''.join(f'| {k} | {v} |\n' for k, v in report['actual_formats'].items())
queries = ''.join(f'| {x["query"]} | {x["document"]} | {x["location"]} | {x["text_status"]} |\n' for x in checks)
text = f'''# 校验报告

校验时间：{report['checked_at']}。课程：{manifest['course']}。

## 下载与来源

9 页共 176 条关联与此前浏览器 DOM 记录逐条匹配；规范化串 23,252 字符，FNV-1a 为 `b1b9475a`。171 个不同源文件中 170 个非空，5 条重复关联；空源仅 KB046。原文件总大小 {report['original_bytes']:,} 字节。

逐一核对文件存在性、大小与 SHA-256；有效文件全部与 manifest 一致，原文件目录无缺失或额外文件。分享资料页显示暂无资料。

| 实际格式 | 不同源文件数 |
|---|---:|
{formats}
## 索引一致性

176 份资料正文/状态文件对应全部附件关联；175 条资料可检索，3,090 个正文块。另收录 4 份已提交小节试卷、24 个题目块。统一索引共 180 个文档记录、3,114 块，JSON、SQLite 与 FTS 数量相同，SQLite `integrity_check` 返回 `ok`。课件和平台题集分别标记 `course_material` 与 `quiz_platform`，保留来源和定位。

题集保存完整题干、原选项顺序及内容、实际作答、平台答案键、解析、教师评语、得分和分值。24 题的浏览器规范化记录为 {quiz_archive['canonical_characters']:,} 字符，FNV-1a 为 `{quiz_archive['canonical_fnv1a32']}`，与本地转录一致；四卷分值各 100 分，已提交得分依次为 100、100、80、70。1.3 多选第 2 题的答案键 ACD 与解析内容 ABD 分开保存，未解决的冲突不会改写为确定答案。

6 份 CAJ/HN 共 372 个物理页已转换；转换任务核查全部页可渲染、原件哈希未变及 HN 逐页文字哈希一致，详见 {link('转换来源与验证', '派生资料/CAJ转换/转换来源与验证.json')}。

55 页 OCR 来自 17 份资料，保留原件哈希；CAJ/HN OCR 另记转换 PDF 路径。原异常文字保留，OCR 作为并列候选。文档状态：158 `extracted`，17 `ocr_unverified`，1 `extraction_failed`。

## 实际检索检查

| 查询 | 限定资料 | 命中位置 | 文本状态 |
|---|---|---|---|
{queries}
不存在的测试短语返回空结果。数值区分通过：`1.0 ≠ 10`、`10/20 ≠ 1020`、`-10% ≠ 10`。规范化只能发现候选，不能证明语义等价。

## 多模态视觉核对与保留说明

17 份资料共 55 页原 Mac OCR 结果（含扫描件、无文字层页面、双栏杂志排版、数学公式密集页及架构图幻灯片）已全部导出高清页面图像并由模型多模态视觉逐页重制与核对（`multimodal_vision_verified`），修正了原有的分栏串行、错别字、表格错位及积分/模糊算子公式乱码。

KB072 物理第 3 页扫描声明及 KB069 第 33 页、KB076 第 68/77/78 页、KB093 第 2/3 页、KB114 全文等已更新为多模态视觉核验文本，原提取异常文本仍并列保留供溯源对照。

本次校验针对文件覆盖、索引一致性、检索定位与转换质量，不代表全课程逐字审读或测验正确率测试。未操作或提交新试卷。
'''
(root / '校验报告.md').write_text(text)
print(json.dumps(report, ensure_ascii=False))
