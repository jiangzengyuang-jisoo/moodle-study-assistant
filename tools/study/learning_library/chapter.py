"""Chapter-scoped, read-only preparation projections for ``library.cmd chapter-prep``.

This module is deliberately local and pure: it parses a chapter label, resolves
which locally registered materials belong to that chapter, projects compact
page/outline/visual data and filters chapter-relevant continuity from the
existing context projection, and reads a summary of the last strict chapter
check receipt.  It never opens the network, never writes learning state or
receipts, and never triggers library maintenance/sync/OCR.

Chapter matching follows the same conservative rules as the Node chapter check:
explicit labels (``Topic 5``, ``Chapter 8``, ``第8章``, ``Tutorial 3``) are
chapter labels; a bare number is only a candidate and is accepted only when
every candidate points at one material family; dates (``Oct 5``) and Moodle
section positions are never chapter numbers; several interpretations return
``ambiguous`` with candidates and reasons instead of guessing.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

PREFIX_ALIASES = {
    'topic': 'topic', 'topics': 'topic', '专题': 'topic', '專題': 'topic',
    '主题': 'topic', '主題': 'topic',
    'chapter': 'chapter', 'chapters': 'chapter', '章': 'chapter',
    'lecture': 'lecture', 'lectures': 'lecture', '讲': 'lecture', '講': 'lecture',
    'week': 'week', 'weeks': 'week', '周': 'week',
    'lesson': 'lesson', 'lessons': 'lesson', '课': 'lesson', '課': 'lesson',
    'tutorial': 'tutorial', 'tutorials': 'tutorial', 'tut': 'tutorial',
    '导修': 'tutorial', '導修': 'tutorial',
    'lab': 'lab', 'labs': 'lab', 'laboratory': 'lab',
    'seminar': 'seminar', 'seminars': 'seminar',
    'workshop': 'workshop', 'workshops': 'workshop',
    'unit': 'unit', 'units': 'unit', 'module': 'module', 'modules': 'module',
    'part': 'part', 'parts': 'part', 'section': 'section', 'sections': 'section',
    'block': 'section', 'blocks': 'section',
}
CN_FAMILIES = {
    '专题': 'topic', '專題': 'topic', '主题': 'topic', '主題': 'topic',
    '单元': 'unit', '單元': 'unit', '章': 'chapter', '讲': 'lecture',
    '講': 'lecture', '课': 'lesson', '課': 'lesson', '周': 'week',
    '节': 'section', '節': 'section',
}
CN_CHAPTER_RE = re.compile(r'第\s*(\d{1,3})\s*(专题|專題|主题|主題|单元|單元|章|讲|講|课|課|周|节|節)')
CN_TOPIC_RE = re.compile(r'(专题|專題|主题|主題)\s*(\d{1,3})(?=$|[^\d])')
EN_CHAPTER_RE = re.compile(r'(?:^|[^\w])([a-z]+)[\s._:#-]*(\d{1,3})(?=$|[^\d])')
DATE_LIKE_RE = re.compile(
    r'^(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*\d{1,2}(?:st|nd|rd|th)?'
    r'|\d{1,2}\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?'
    r'|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?)$'
)

# Flags that merely say "this is extracted text, not a verified source".  They
# are kept in the read projection but dropped from chapter-prep pages so the
# model sees only the flags that carry information.
GENERIC_FLAGS = frozenset({
    'text_extraction_unverified', 'needs_visual_review',
    'layout_not_preserved', 'visual_content_not_fully_detectable',
})
MATH_CHARS = frozenset('=^·∑∫√∂∇≈≠≤≥∞±×÷→←↔∈∉∀∃')
MATH_RE = re.compile(r'[∫∑∏√≈≠≤≥∞∂∇∈∉∀∃]|\b(?:sin|cos|tan|lim)\s*\(|[A-Za-z0-9)]\s*[=^]\s*[-+(A-Za-z0-9]')
ANSWER_KEYS = frozenset({
    'answer', 'answers', 'answer_key', 'answer_keys', 'answer_check', 'solution',
    'solutions', 'correct_answer', 'correct_answer_or_method', 'expected_answer',
    'expected_answers', 'expected', 'rubric', 'marking_scheme', 'scoring_key',
    'assessment', 'feedback', 'explanation', 'worked_solution', 'user_answer',
    'user_answer_or_method', 'user_attempt', 'checks', 'recent_correct', 'correct',
    'is_correct', 'expected_result', 'rationale', 'reasoning', 'marking_points',
    'scoring_points', 'grading_key', 'hint', 'hints',
})
SECTION_HEADING_RE = re.compile(r'^##\s+\[\d+\]\s+(.+)$', re.MULTILINE)
MAX_CANDIDATES = 20


def norm(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value)).strip().lower())


def _int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _safe_record(value):
    """Drop keyed answer/marking fields without touching the caller's objects."""
    if isinstance(value, dict):
        return {
            key: _safe_record(item) for key, item in value.items()
            if str(key).lower() not in ANSWER_KEYS
        }
    if isinstance(value, list):
        return [_safe_record(item) for item in value]
    return value


def chapter_tokens(value):
    """Return explicit chapter-like tokens ``{family, number, raw, index}``."""
    text = norm(value)
    found = []
    for match in CN_CHAPTER_RE.finditer(text):
        family = CN_FAMILIES.get(match.group(2))
        if family:
            found.append({'family': family, 'number': int(match.group(1)),
                          'raw': match.group(0), 'index': match.start()})
    for match in CN_TOPIC_RE.finditer(text):
        found.append({'family': 'topic', 'number': int(match.group(2)),
                      'raw': match.group(0), 'index': match.start()})
    for match in EN_CHAPTER_RE.finditer(text):
        family = PREFIX_ALIASES.get(match.group(1))
        if family:
            found.append({'family': family, 'number': int(match.group(2)),
                          'raw': match.group(0).strip(), 'index': match.start()})
    found.sort(key=lambda item: item['index'])
    return found


def chapter_intent(section):
    """Parse what the user asked for; never turns a date into a chapter number."""
    if not isinstance(section, str) or not section.strip():
        raise ValueError('请提供章节名称/编号，或用 --modules 指定模块')
    text = norm(section)
    chinese = re.match(
        r'^第\s*(\d{1,3})\s*(专题|專題|主题|主題|单元|單元|章|讲|講|课|課|周|节|節)(?=$|[^\w])', text)
    if chinese:
        family = CN_FAMILIES.get(chinese.group(2))
        return {'kind': family, 'family': family, 'number': int(chinese.group(1)),
                'text': text, 'basis': 'number', 'explicit': True}
    topic_prefix = re.match(r'^(专题|專題|主题|主題)\s*(\d{1,3})(?=$|[^\d])', text)
    if topic_prefix:
        return {'kind': 'topic', 'family': 'topic', 'number': int(topic_prefix.group(2)),
                'text': text, 'basis': 'number', 'explicit': True}
    if re.fullmatch(r'\d{1,3}', text):
        return {'kind': 'bare', 'family': None, 'number': int(text),
                'text': text, 'basis': 'bare', 'explicit': False}
    english = re.match(r'^([a-z]+)\s*[-_:#.]?\s*(\d{1,3})(?=$|[^\d])', text)
    if english and PREFIX_ALIASES.get(english.group(1)):
        family = PREFIX_ALIASES[english.group(1)]
        return {'kind': family, 'family': family, 'number': int(english.group(2)),
                'text': text, 'basis': 'number', 'explicit': True}
    return {'kind': 'name', 'family': None, 'number': None, 'text': text,
            'basis': 'name', 'explicit': False, 'date_like': bool(DATE_LIKE_RE.match(text))}


def resolve_course_id(state, course_ref):
    """Follow linked/merged course pages to the primary subject course id."""
    courses = {row.get('id'): row for row in state.get('courses', [])
               if isinstance(row, dict) and row.get('id')}
    current = str(course_ref or '').strip()
    seen = set()
    while current in courses and current not in seen:
        seen.add(current)
        merged = courses[current].get('merged_into')
        if not isinstance(merged, str) or not merged or merged == current or merged not in courses:
            break
        current = merged
    return current if current in courses else None


def material_layout(material, source):
    """Source-mapping evidence for one registered material file."""
    module_id = _int(source.get('module_id'))
    pages = [row for row in (material.get('source_pages') or [])
             if isinstance(row, dict) and (row.get('module_id') is None
                                           or _int(row.get('module_id')) == module_id)]
    if not pages:
        pages = [row for row in (material.get('source_pages') or []) if isinstance(row, dict)]
    sections = []
    for row in pages:
        name = row.get('section')
        if not name:
            continue
        if not any(section['name'] == name and section.get('index') == row.get('section_index')
                   for section in sections):
            sections.append({'name': name, 'index': row.get('section_index'),
                             'moodle_courseid': row.get('moodle_courseid')})
    module_name = next((row.get('module_name') for row in pages if row.get('module_name')), None)
    evidence = [
        {'kind': 'filename', 'text': source.get('remote_filename')},
        {'kind': 'filename', 'text': material.get('filename')},
        {'kind': 'title', 'text': material.get('title')},
        {'kind': 'module', 'text': module_name},
        *({'kind': 'section', 'text': section['name']} for section in sections),
    ]
    return {'sections': sections, 'module_name': module_name,
            'evidence': [entry for entry in evidence if entry['text']]}


def _describe_hit(hit):
    label = '所在区块' if hit.get('kind') == 'section' else '文件名/标题/模块名'
    return label + '含「' + str(hit.get('raw') or hit.get('text') or '') + '」'


def _dedupe(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _match_evidence(intent, evidence, include_sections=True):
    """Match one chapter intent against labelled evidence entries."""
    usable = [entry for entry in evidence if entry.get('text')
              and (include_sections or entry.get('kind') != 'section')]
    if intent['kind'] == 'name':
        wanted = norm(intent['text'])
        hits = []
        for entry in usable:
            text = norm(entry['text'])
            if text == wanted or (wanted and wanted in text):
                hits.append({'kind': entry['kind'], 'text': entry['text'],
                             'exact': text == wanted})
        return {'matched': bool(hits), 'hits': hits, 'conflicts': [],
                'families': [], 'exact': any(hit['exact'] for hit in hits)}
    if intent['kind'] == 'bare':
        number = intent['number']
        hits, families = [], set()
        for entry in usable:
            for token in chapter_tokens(entry['text']):
                if token['number'] != number:
                    continue
                hits.append({'kind': entry['kind'], 'text': entry['text'],
                             'raw': token['raw'], 'family': token['family']})
                families.add(token['family'])
        return {'matched': bool(hits),
                'hits': _dedupe(hits), 'conflicts': [],
                'families': sorted(families), 'exact': False}
    family = intent.get('family') or intent['kind']
    number = intent['number']
    hits, conflicts = [], []
    for entry in usable:
        for token in chapter_tokens(entry['text']):
            if token['family'] != family:
                continue
            hit = {'kind': entry['kind'], 'text': entry['text'], 'raw': token['raw']}
            (hits if token['number'] == number else conflicts).append(hit)
    return {'matched': bool(hits), 'hits': _dedupe(hits), 'conflicts': _dedupe(conflicts),
            'families': [family] if hits else [], 'exact': False}


def candidate_summary(candidate):
    reasons = []
    for hit in candidate['match']['hits']:
        reasons.append(_describe_hit(hit))
    for hit in candidate['match']['conflicts']:
        reasons.append('同一族编号不一致：' + _describe_hit(hit))
    return {
        'filename': candidate['filename'],
        'module_id': candidate['module_id'],
        'module_name': candidate.get('module_name'),
        'relative_path': candidate.get('relative_path'),
        'sections': [section['name'] for section in candidate.get('sections') or []],
        'reasons': _dedupe(reasons),
        'conflict': bool(candidate['match']['conflicts']),
        'families': list(candidate['match'].get('families') or []),
    }


def _resolved(state, subject, course_row, intent, basis, candidates, module_ids):
    return {
        'status': 'resolved', 'basis': basis, 'subject': subject, 'course': course_row,
        'intent': intent, 'module_ids': sorted(set(module_ids)),
        'materials': candidates,
        'files': [{'module_id': item['module_id'], 'filename': item['filename'],
                   'relative_path': item.get('relative_path'),
                   'filepath': item.get('filepath'),
                   'sha256': (item.get('material') or {}).get('sha256')}
                  for item in candidates],
        'candidates': [candidate_summary(item) for item in candidates[:MAX_CANDIDATES]],
        'candidate_count': len(candidates),
    }


def _ambiguous(state, subject, course_row, intent, basis, candidates, reason):
    return {
        'status': 'ambiguous', 'basis': basis, 'subject': subject, 'course': course_row,
        'intent': intent, 'reason': reason, 'module_ids': [], 'materials': [], 'files': [],
        'candidates': [candidate_summary(item) for item in candidates[:MAX_CANDIDATES]],
        'candidate_count': len(candidates),
    }


def resolve_chapter_materials(state, course, section=None, modules=None, include_archived=False):
    """Resolve one chapter to locally registered materials without guessing."""
    subject = resolve_course_id(state, course)
    course_row = next((row for row in state.get('courses', [])
                       if isinstance(row, dict) and row.get('id') == subject), None)
    if course_row is None:
        raise ValueError('Unknown course: ' + str(course))
    wanted = None
    if modules is not None:
        wanted = {number for number in (_int(value) for value in modules) if number is not None}
        if not wanted:
            raise ValueError('--modules 需要至少一个有效的模块编号')
    if wanted is None and not section:
        raise ValueError('请提供章节名称/编号，或用 --modules 指定模块')
    intent = ({'kind': 'modules', 'family': None, 'number': None, 'text': None,
               'basis': 'modules', 'explicit': True}
              if wanted is not None else chapter_intent(section))
    if wanted is None and intent['kind'] == 'name' and intent.get('date_like'):
        return {'status': 'none', 'basis': 'local_materials', 'subject': subject,
                'course': course_row, 'intent': intent, 'candidates': [],
                'candidate_count': 0, 'module_ids': [], 'materials': [], 'files': [],
                'date_like': True}
    candidates, seen = [], set()
    for material in state.get('materials') or []:
        if not isinstance(material, dict) or material.get('course_id') != subject:
            continue
        if (not include_archived
                and (material.get('superseded_by')
                     or material.get('version_status') in {'archived', 'deleted'})):
            continue
        for source in material.get('moodle_file_sources') or []:
            if not isinstance(source, dict):
                continue
            remote = source.get('remote_filename')
            module_id = _int(source.get('module_id'))
            if not remote or module_id is None:
                continue
            if wanted is not None and module_id not in wanted:
                continue
            layout = material_layout(material, source)
            if wanted is not None:
                match = {'matched': True, 'hits': [], 'conflicts': [],
                         'families': [], 'exact': True}
            else:
                match = _match_evidence(intent, layout['evidence'],
                                        include_sections=intent['kind'] != 'name')
            if not match['matched']:
                continue
            key = (module_id, material.get('relative_path') or '', remote)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                'module_id': module_id, 'filename': remote,
                'filepath': source.get('filepath') or '/',
                'relative_path': material.get('relative_path'),
                'material_id': material.get('id'), 'material': material,
                'module_name': layout['module_name'],
                'sections': layout['sections'],
                'evidence': layout['evidence'], 'match': match,
            })
    if not candidates:
        if wanted is not None:
            return _resolved(state, subject, course_row, intent, 'explicit_modules', [], wanted)
        return {'status': 'none', 'basis': 'local_materials', 'subject': subject,
                'course': course_row, 'intent': intent, 'candidates': [],
                'candidate_count': 0, 'module_ids': [], 'materials': [], 'files': [],
                'available_sections': None}
    if wanted is not None:
        # Mirror the Node resolver: an explicit --modules scope keeps every
        # requested module id, even when no local material is mapped to it.
        return _resolved(state, subject, course_row, intent, 'explicit_modules',
                         candidates, wanted)
    chosen = candidates
    if intent['kind'] == 'name':
        exact = [item for item in chosen if item['match']['exact']]
        chosen = exact or chosen
        if len({item['module_id'] for item in chosen}) > 1:
            return _ambiguous(state, subject, course_row, intent, 'local_materials', chosen,
                              f'名称“{intent["text"]}”匹配到多个模块，无法确定本章文件')
    if any(item['match']['conflicts'] for item in chosen):
        return _ambiguous(state, subject, course_row, intent, 'local_materials', chosen,
                          '部分候选同时出现本章编号和其他同族编号（或区块编号不一致），无法安全确定整章文件')
    if intent['kind'] == 'bare':
        families = {hit['family'] for item in chosen for hit in item['match']['hits']
                    if hit.get('family')}
        if len(families) > 1:
            return _ambiguous(state, subject, course_row, intent, 'local_materials', chosen,
                              f'纯数字“{intent["number"]}”同时对应 '
                              + '、'.join(sorted(families))
                              + ' 等不同资料类型；请改用明确的章节名或 --modules')
    module_ids = {item['module_id'] for item in chosen}
    return _resolved(state, subject, course_row, intent, 'local_materials', chosen, module_ids)


def strict_file_rows(state, module_ids, include_archived=False):
    """All registered files whose source module is inside the strict scope.

    ``chapter-check`` filters the freshly enumerated course catalog by module
    ids; a folder module therefore verifies the whole folder even when only a
    few filenames matched the chapter label.  This helper mirrors that wider
    scope from local state so callers can show it honestly.
    """
    wanted = {number for number in (_int(value) for value in module_ids or [])
              if number is not None}
    rows, seen = [], set()
    if not wanted:
        return rows
    for material in state.get('materials') or []:
        if not isinstance(material, dict):
            continue
        if (not include_archived
                and (material.get('superseded_by')
                     or material.get('version_status') in {'archived', 'deleted'})):
            continue
        for source in material.get('moodle_file_sources') or []:
            if not isinstance(source, dict):
                continue
            module_id = _int(source.get('module_id'))
            remote = source.get('remote_filename')
            if module_id is None or module_id not in wanted or not remote:
                continue
            key = (module_id, material.get('relative_path') or '', remote)
            if key in seen:
                continue
            seen.add(key)
            rows.append({'module_id': module_id, 'filename': remote,
                         'relative_path': material.get('relative_path'),
                         'sha256': material.get('sha256')})
    rows.sort(key=lambda row: (row['module_id'], row.get('relative_path') or ''))
    return rows


def compact_flags(flags):
    """Keep only flags that carry information beyond "unverified extraction"."""
    result = []
    for flag in flags or []:
        text = str(flag)
        if text in GENERIC_FLAGS or text in result:
            continue
        result.append(text)
    return result


def compact_page(page):
    return {'page': page.get('page'), 'heading': page.get('heading') or '',
            'text': page.get('text') or '', 'flags': compact_flags(page.get('flags'))}


def _math_dense(text):
    if not text:
        return False
    if sum(character in MATH_CHARS for character in text) >= 2:
        return True
    return bool(MATH_RE.search(text))


def visual_candidates(data):
    """Formula/figure/OCR candidate pages, highest priority first."""
    result = []
    for page in data.get('pages') or []:
        if not isinstance(page, dict):
            continue
        flags = {str(flag) for flag in page.get('flags') or []}
        text = page.get('text') or ''
        why, priority = [], 0
        if 'no_extractable_text' in flags:
            why.append('no_text'); priority = max(priority, 100)
        if 'contains_chart' in flags:
            why.append('chart'); priority = max(priority, 90)
        if 'contains_formula' in flags:
            why.append('formula'); priority = max(priority, 90)
        if 'contains_table' in flags:
            why.append('table'); priority = max(priority, 85)
        if 'contains_images_or_drawings' in flags:
            why.append('image'); priority = max(priority, 80)
        if 'possible_formula' in flags or _math_dense(text):
            why.append('math'); priority = max(priority, 70)
        if 'needs_ocr' in flags:
            why.append('ocr'); priority = max(priority, 60)
        if not why:
            continue
        result.append({'unit': page.get('page'), 'why': _dedupe(why),
                       'priority': priority, 'verified': False})
    result.sort(key=lambda item: (-item['priority'], item['unit']))
    return result


def page_text_cost(page):
    return len(page.get('text') or '')


def parse_sections_from_course_page(text):
    """Local section names from a captured Moodle course page (offline hint)."""
    names = []
    for match in SECTION_HEADING_RE.finditer(text or ''):
        name = re.sub(r'\s+', ' ', match.group(1)).strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= 40:
            break
    return names


def _matches_intent(intent, text):
    if not intent or not text:
        return False
    return _match_evidence(intent, [{'kind': 'title', 'text': str(text)}],
                           include_sections=True)['matched']


def _record_relevant(record, intent, material_ids):
    if not isinstance(record, dict):
        return False
    if material_ids and record.get('material_id') is not None:
        if str(record.get('material_id')) in material_ids:
            return True
    if not intent:
        return False
    evidence = [{'kind': 'title', 'text': str(record.get(key) or '')}
                for key in ('chapter', 'topic', 'scope', 'title')]
    evidence = [entry for entry in evidence if entry['text']]
    return _match_evidence(intent, evidence, include_sections=True)['matched']


def _pick(record, keys):
    return {key: _safe_record(record[key]) for key in keys if key in record}


def contextual_continuity(context, course_index, intent, material_ids, limit=5):
    """Filter the course context projection down to chapter-relevant continuity."""
    course = (context.get('sections') or {}).get('course') or {}
    cursor = course.get('teaching_cursor') or {}
    mats = {str(value) for value in material_ids or []}
    same_chapter = _matches_intent(intent, cursor.get('chapter')) if cursor else False
    current_position = None
    if cursor:
        current_position = _pick(cursor, ('chapter', 'topic', 'stage', 'material_id',
                                          'pages', 'next_step', 'updated_at'))
    pending_question = None
    pending = cursor.get('pending_questions') or []
    if pending and isinstance(pending[0], dict):
        record = _safe_record(pending[0].get('record') or {})
        pending_question = {'id': record.get('id'), 'status': record.get('status'),
                            'conditions': record.get('conditions'),
                            'same_chapter': bool(same_chapter)}
        if same_chapter and record.get('prompt'):
            pending_question['prompt'] = record['prompt']
    latest_continuation = None
    continuation = cursor.get('latest_continuation') or {}
    if isinstance(continuation, dict) and continuation.get('record'):
        latest_continuation = _safe_record(continuation['record'])
    checks = []
    attempt = cursor.get('latest_attempt_context') or {}
    if isinstance(attempt, dict) and attempt.get('record'):
        record = _safe_record(attempt['record'])
        if record and (_record_relevant(record, intent, mats) or same_chapter):
            keys = ('id', 'topic', 'chapter', 'material_id', 'type', 'entry_type',
                    'stage', 'status', 'at', 'date', 'recorded_at', 'answered_at',
                    'conditions', 'scaffolding', 'assisted', 'independent',
                    'answer_exposed', 'answer_previously_exposed', 'hints_used',
                    'mastery_verified', 'mastery_scope', 'mastery_limits',
                    'fsrs_rating', 'fsrs_rated', 'actual_minutes')
            checks = [_pick(record, keys)]
    topic_evidence = [_safe_record(row) for row in (course.get('topic_evidence') or [])
                      if _record_relevant(row, intent, mats)][:limit]
    coverage_records = [_safe_record(row) for row in (course.get('coverage_records') or [])
                        if _record_relevant(row, intent, mats)][:limit]
    review_backlog = [_safe_record(row) for row in (course.get('review_backlog') or [])
                      if _record_relevant(row, intent, mats)][:limit]
    omitted = []
    if course_index is not None:
        omitted.extend([
            {'path': f'/courses/{course_index}/teaching_cursor/checks',
             'reason': 'only_chapter_relevant_checks_loaded',
             'next_read': f'library.cmd detail path /courses/{course_index}/teaching_cursor/checks'},
            {'path': f'/courses/{course_index}/teaching_cursor/history',
             'reason': 'historical_positions_not_loaded',
             'next_read': f'library.cmd detail path /courses/{course_index}/teaching_cursor/history'},
        ])
    omitted.extend([
        {'path': '/tasks', 'reason': 'background_not_loaded_for_chapter_teaching'},
        {'path': '/deadlines', 'reason': 'background_not_loaded_for_chapter_teaching'},
        {'path': '/exam_profiles', 'reason': 'background_not_loaded_for_chapter_teaching'},
    ])
    return {
        'current_position': current_position,
        'chapter_label_matches_current_position': bool(same_chapter),
        'pending_question': pending_question,
        'latest_continuation': latest_continuation,
        'checks': checks,
        'topic_evidence': topic_evidence,
        'coverage_records': coverage_records,
        'review_backlog': review_backlog,
        'omitted': omitted,
        'note': ('只加载本章相关接续与最近记录；未查到记录不等于未学过，'
                 '跨章节证据用 library.cmd evidence --course <id> --query "<知识点>" 定位。'),
    }


def chat_key(chat):
    """Stable digest of the original chat id, matching state_io.digest semantics."""
    payload = json.dumps(chat, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _safe_segment(value):
    text = re.sub(r'[^0-9a-z\u3400-\u9fff]+', '-',
                  unicodedata.normalize('NFKC', str(value or '')).strip().lower())
    text = text.strip('-')[:40]
    slug = text or 'section'
    if re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])', slug, re.IGNORECASE):
        return '_' + slug
    return slug


def receipt_summary(root, course_id, section=None, modules=None, chat=None):
    """Read-only summary of the latest strict chapter-check receipt.

    This is background information only: it is never a verification for the
    current turn and it does not write or extend any receipt.
    """
    note = ('上次回执只是背景信息，不是本轮核验；本章文件内容哈希仍必须由 '
            'study_check_chapter 核对（chapter-check --mode strict 仅为备用；同聊天同范围且本地未变时复用回执、不联网）。')
    summary = {'found': False, 'note': note}
    if not section and not modules:
        return summary
    wanted_modules = sorted(number for number in (_int(value) for value in modules or [])
                            if number is not None)
    folder = Path(root) / 'Study' / '_System' / 'ChapterChecks' / _safe_segment(course_id)
    if not folder.is_dir():
        return summary
    wanted_section = norm(section) if section else None
    matches = []
    for path in sorted(folder.glob('*.json')):
        if path.name.endswith('.ambiguous.json'):
            continue
        try:
            receipt = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
        if not isinstance(receipt, dict) or receipt.get('mode') != 'strict':
            continue
        request = receipt.get('request') or {}
        if wanted_modules:
            stored = sorted(number for number in
                            (_int(value) for value in request.get('modules') or [])
                            if number is not None)
            if stored != wanted_modules:
                continue
        elif norm(request.get('section')) != wanted_section:
            continue
        matches.append((str(receipt.get('checked_at') or ''), path, receipt))
    if not matches:
        return summary
    matches.sort(key=lambda item: item[0], reverse=True)
    _, path, receipt = matches[0]
    scope = receipt.get('scope') or {}
    verified = receipt.get('verified') or {}
    summary.update({
        'found': True,
        'receipt_path': str(path.relative_to(Path(root))).replace('\\', '/'),
        'mode': receipt.get('mode'),
        'verdict': receipt.get('verdict'),
        'verified_complete': verified.get('complete') is True,
        'checked_at': receipt.get('checked_at'),
        'scope_module_ids': scope.get('module_ids'),
        'section': (receipt.get('request') or {}).get('section'),
        'modules': (receipt.get('request') or {}).get('modules'),
        'chat_key_matches': ((receipt.get('chat') or {}).get('key') == chat_key(chat)
                             if chat else None),
        'note': note,
    })
    return summary
