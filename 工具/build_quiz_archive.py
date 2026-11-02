"""Build a content-mapped quiz archive from the checked visible-page transcript."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = ROOT / '题集/页面记录.json'
raw = json.loads(RAW_PATH.read_text())
parsed = []
for quiz in raw['quizzes']:
    questions = []
    for kind, number, stem, options, submitted, answer, explanation, score, maximum in quiz['questions']:
        questions.append(dict(type=kind, number=number, stem=stem,
                              options=[dict(label=chr(65+i), text=t) for i, t in enumerate(options)],
                              submitted_answer=list(submitted), platform_answer=list(answer),
                              platform_explanation=explanation, teacher_comment=raw['teacher_comment_all'],
                              awarded_score=score, max_score=maximum))
    parsed.append(dict(section=quiz['section'], source_url=quiz['source_url'], submitted_at=quiz['submitted_at'],
                       score=quiz['score'], questions=questions))
canonical = json.dumps(parsed, ensure_ascii=False, separators=(',', ':'))
checksum = 2166136261
for char in canonical:
    checksum = ((checksum ^ ord(char)) * 16777619) & 0xffffffff
assert len(canonical) == raw['canonical_characters'] and f'{checksum:08x}' == raw['canonical_fnv1a32']

archive = dict(schema_version=1, course='云计算行业与创新创业对接范式', course_id='2026373645',
               chapter='第1章 云计算行业创业认识', source_kind='quiz_platform', captured_at=raw['captured_at'],
               source_record_path='题集/页面记录.json', source_record_sha256=hashlib.sha256(RAW_PATH.read_bytes()).hexdigest(),
               canonical_characters=len(canonical), canonical_fnv1a32=f'{checksum:08x}', quizzes=[])
prefixes = {'单选题': 'S', '多选题': 'M', '判断题': 'T'}
common = '平台答案仅表示页面公布的答案键；字母只对应本次保存的选项顺序。后续先核对题干与选项实际内容，再映射当前字母。'
combined = ['# 第一章小节测验题集', '', common, '', '共 4 份已提交试卷、24 题；不含“第一章测验”。采集日期：2026-09-25。', '']
navigation = ['# 已做测验题集', '', common, '', '| 试卷 | 题数 | 已提交得分 | 提交日期 |', '|---|---:|---:|---|']


def mapped(question, labels):
    options = {o['label']: o['text'] for o in question['options']}
    return [dict(label=label, text=options[label]) for label in labels]


def readable(items):
    return '；'.join(f'{item["label"]}「{item["text"]}」' for item in items)


for quiz in parsed:
    section = quiz['section']
    quiz.update(id=f'QUIZ-{section}', title=f'{section}小节测验', max_score=100, body_path=f'题集/{section}小节测验.md')
    assert len(quiz['questions']) == 6 and sum(q['awarded_score'] for q in quiz['questions']) == quiz['score']
    assert sum(q['max_score'] for q in quiz['questions']) == 100
    lines = [f'# {quiz["title"]}', '', f'课程：{archive["course"]}。{common}', '',
             f'[已提交试卷来源]({quiz["source_url"]}) · 提交日期：{quiz["submitted_at"]} · 得分：{quiz["score"]}/100', '',
             '采集日期：2026-09-25。题干、选项、解析保留页面原措辞；整理备注另行标注。', '']
    for q in quiz['questions']:
        q.update(id=f'Q{section}-{prefixes[q["type"]]}{q["number"]}', conflict=None, notes=[], review_status='platform_recorded')
        q['submitted_answer_contents'] = mapped(q, q['submitted_answer'])
        q['platform_answer_contents'] = mapped(q, q['platform_answer'])
        if q['id'] == 'Q1.3-M2':
            q['conflict'] = dict(status='platform_key_explanation_conflict', explanation_mapped_answer=['A', 'B', 'D'],
                                 note='平台答案键为 ACD；解析列出的“更低的成本、新技术优势、利用更小的基础设施规模”对应 ABD。C“多样的数据类型”未见于解析，不能把答案键和解析合并成确定结论。')
            q['review_status'] = 'conflict_unresolved'
        if q['id'] == 'Q1.4-S1':
            q['notes'].append('此前误用通用产业发展信息选择“高速发展阶段”；本课程平台解析使用“新兴萌芽阶段”。保留课程及时间语境，不能推广为当前行业阶段。')
        if q['id'] == 'Q1.4-T1':
            q['notes'].append('题干是“信息化政务”，解析是“移动电子政务”；平台判“错误”。后续必须逐字核对术语。')
        if q['id'] == 'Q1.4-S2':
            q['notes'].append('D 选项原页面写作“分布式式售后服务和商业模块”，保留重复字，不擅自改写。')
        if q['id'] in {'Q1.2-S2', 'Q1.3-T1'}:
            q['notes'].append('保留题目中的历史年份及平台表述；此次归档未独立验证历史统计来源。')
        lines += [f'## {q["id"]} · {q["type"]}第 {q["number"]} 题', '', q['stem'], '', '| 选项 | 原始内容 |', '|---|---|']
        lines += [f'| {o["label"]} | {o["text"]} |' for o in q['options']]
        lines += ['', f'**实际作答：** {readable(q["submitted_answer_contents"])}', '',
                  f'**平台答案键：** {readable(q["platform_answer_contents"])}', '',
                  f'**得分：** {q["awarded_score"]}/{q["max_score"]}', '', '**平台解析原文：**', '',
                  '> ' + q['platform_explanation'], '', f'**教师评语：** {q["teacher_comment"]}', '']
        if q['conflict']:
            lines += ['**未解决冲突：** ' + q['conflict']['note'], '']
        for note in q['notes']:
            lines += ['**整理备注：** ' + note, '']
    (ROOT / quiz['body_path']).write_text('\n'.join(lines) + '\n')
    combined += [line.replace('# ', '## ', 1) if line.startswith('# ') else line.replace('## ', '### ', 1) if line.startswith('## ') else line for line in lines]
    navigation.append(f'| [{quiz["title"]}](<{section}小节测验.md>) | 6 | {quiz["score"]}/100 | {quiz["submitted_at"]} |')
    archive['quizzes'].append(quiz)
navigation += ['', '[一次查看全部 24 题](<第一章小节测验.md>) · [结构化 JSON](<第一章小节测验.json>) · [返回知识库](<../README.md>)', '',
               '平台共判对 21 题、判错 3 题。1.3 多选第 2 题存在答案键与解析冲突；题集不把平台判分直接等同于知识正确性。', '',
               '每题保存完整题干、全部选项、原选项顺序、实际作答及内容、平台答案键及内容、解析、教师评语、分值、来源和整理备注。', '',
               f'24 题的正文数据与浏览器可见结果页规范化记录一致：{raw["canonical_characters"]:,} 字符，FNV-1a 校验值 {raw["canonical_fnv1a32"]}。采集未修改选项或提交新试卷。', '']
(ROOT / '题集/README.md').write_text('\n'.join(navigation))
(ROOT / '题集/第一章小节测验.md').write_text('\n'.join(combined))
(ROOT / '题集/第一章小节测验.json').write_text(json.dumps(archive, ensure_ascii=False, indent=2))
print(f'Archived 4 quizzes / 24 questions; visible-page canonical checksum matched: {raw["canonical_fnv1a32"]}.')
