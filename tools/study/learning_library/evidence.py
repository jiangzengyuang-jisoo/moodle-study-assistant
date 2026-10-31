"""Bounded, read-only locators for learning evidence across a course's chapters.

Matches locate records, not mastery. Legacy prose is never classified, and
answers/solutions are searched locally but are not returned in the preview.
"""
from __future__ import annotations

import json
import re
import unicodedata


_COURSE_FIELDS = ('topic_evidence', 'assistant_notes', 'teaching_cursor',
                  'teaching_history', 'history', 'learning_progress',
                  'review_backlog', 'coverage_records', 'learning_history', 'practice_history')
_COLLECTIONS = ('learning_feedback', 'mistakes', 'review_attempts')
_META = ('id', 'topic', 'chapter', 'type', 'entry_type', 'at', 'date',
         'recorded_at', 'answered_at', 'updated_at')
_SOURCE_META = ('material_id', 'task_id', 'pages', 'slides', 'source_type')


def _norm(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value).casefold()).strip()


def _escape(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def _course(state, requested):
    key = _norm(requested).replace(' ', '')
    found = []
    for index, row in enumerate(state.get('courses', [])):
        aliases = row.get('aliases') or []
        aliases = [aliases] if isinstance(aliases, str) else aliases
        if any(isinstance(item, str) and _norm(item).replace(' ', '') == key
               for item in (row.get('id'), row.get('name'), *aliases)):
            found.append((index, row))
    if len(found) != 1:
        raise ValueError('Course is unknown or ambiguous: ' + requested)
    return found[0]


def _belongs(row, course_id):
    if not isinstance(row, dict):
        return False
    if row.get('course_id') is not None:
        return row['course_id'] == course_id
    values = row.get('course_ids')
    return isinstance(values, list) and course_id in values


def _own_text(value):
    """A record's direct scalar fields; nested records get their own pointers."""
    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, str):
                yield item
            elif isinstance(item, list):
                yield from (part for part in item if isinstance(part, str))
    elif isinstance(value, str):
        yield value


def _walk(value, path, course_id):
    if isinstance(value, dict):
        if value.get('course_id') not in (None, course_id):
            return
        if isinstance(value.get('course_ids'), list) and course_id not in value['course_ids']:
            return
        yield path, value
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                yield from _walk(child, path + '/' + _escape(key), course_id)
    elif isinstance(value, list):
        # Append-only observations put later corrections after old events. Show
        # later positions first without claiming a timestamp or mastery order.
        for index, child in reversed(list(enumerate(value))):
            yield from _walk(child, path + '/' + str(index), course_id)
    elif isinstance(value, str):
        yield path, value


def _contains(value, terms):
    for raw in _own_text(value):
        text = _norm(raw)
        for term in terms:
            if re.fullmatch(r'[a-z0-9]+', term):
                if re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text):
                    return True
            elif term in text:
                return True
    return False


def _scalar(value):
    if isinstance(value, str):
        # Long fields are omitted as a whole, never paraphrased into evidence.
        return value if len(value) <= 160 else None
    if value is None or type(value) in (bool, int, float):
        return value
    return None


def _preview(value, path, structured):
    result = {'path': path, 'record_kind': 'topic_evidence' if structured else 'legacy_candidate',
              'level_inferred': False, 'detail_required': True}
    if not isinstance(value, dict):
        return result
    metadata = {}
    for key in (*_META, *(('level', 'corrects') if structured else ())):
        if key in value:
            compact = _scalar(value[key])
            if key == 'corrects' and isinstance(value[key], list):
                compact = (list(value[key]) if len(value[key]) <= 12 and
                           all(isinstance(item, str) and len(item) <= 160 for item in value[key])
                           else {'omitted': True, 'path': path + '/corrects'})
            if compact is not None:
                metadata[key] = compact
    source = value.get('source')
    if source is not None:
        summary = {'path': path + '/source'}
        if isinstance(source, dict):
            for key in _SOURCE_META:
                if key not in source:
                    continue
                raw = source[key]
                if key in ('pages', 'slides'):
                    if isinstance(raw, list) and len(raw) <= 12 and all(type(x) is int for x in raw):
                        summary[key] = list(raw)
                else:
                    compact = _scalar(raw)
                    if compact is not None:
                        summary[key] = compact
        metadata['source'] = summary
    result['metadata'] = metadata
    return result


def search_evidence(state, course_id, query, limit=8):
    """Find all matching record locations, then return a bounded safe preview.

    Matching is NFKC/case-insensitive: full query phrase OR any whitespace-
    separated term. There is no synonym expansion or semantic classification.
    """
    if not isinstance(query, str) or not _norm(query) or len(query) > 240:
        raise ValueError('Evidence query must contain 1-240 characters')
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('Evidence limit must be between 1 and 20')
    index, course = _course(state, course_id)
    canonical_id = course['id']
    terms = list(dict.fromkeys([_norm(query), *_norm(query).split(' ')]))
    roots = [(f'/courses/{index}/{key}', course[key], key == 'topic_evidence')
             for key in _COURSE_FIELDS if key in course]
    for collection in _COLLECTIONS:
        rows = state.get(collection) or []
        if isinstance(rows, list):
            roots.extend((f'/{collection}/{i}', row, False) for i, row in reversed(list(enumerate(rows)))
                         if _belongs(row, canonical_id))
    matches, total, used = [], 0, 0
    for base, records, structured in roots:
        for path, record in _walk(records, base, canonical_id):
            if not _contains(record, terms):
                continue
            total += 1
            # Only a direct topic_evidence row can supply a recorded level.
            is_event = structured and path.count('/') == base.count('/') + 1
            preview = _preview(record, path, is_event)
            size = len(json.dumps(preview, ensure_ascii=False))
            if len(matches) < limit and used + size <= 6000:
                matches.append(preview)
                used += size
    return {'state_revision': state.get('revision'), 'course_id': canonical_id,
            'query': query, 'match_mode': 'normalized phrase OR any whitespace-separated term; literal matching only',
            'result_order': 'source groups, later list positions first; not a mastery ranking',
            'searched_paths': [f'/courses/{index}/{key}' for key in _COURSE_FIELDS if key in course]
                              + ['/' + key for key in _COLLECTIONS if isinstance(state.get(key), list)],
            'matches': matches,
            'total_matching_locations': total, 'shown': len(matches), 'truncated': total > len(matches),
            'status': 'candidates_found' if total else 'no_matching_record',
            'learning_conclusion': 'not_assessed', 'state_written': False,
            'note': '未查到记录不等于没学过；命中仅定位证据。旧笔记不自动推断等级，已讲解不等于独立掌握；按路径补读并核对本人纠正。',
            'detail_usage': 'tools/study/library.cmd detail path /PATH (may expose answers; use for internal evidence review)'}
