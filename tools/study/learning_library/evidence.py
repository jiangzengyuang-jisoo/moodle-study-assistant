"""Bounded, read-only locators for learning evidence across a course's chapters.

Matches locate records, not mastery. Legacy prose is never classified, and
answers/solutions are searched locally but are not returned in the preview.
"""
from __future__ import annotations

import json
import re
import unicodedata

from course_links import record_course_ids, review_attempt_context


_COURSE_FIELDS = ('topic_evidence', 'assistant_notes', 'teaching_cursor', 'teaching_events',
                  'teaching_history', 'history', 'learning_progress',
                  'review_backlog', 'coverage_records', 'learning_history', 'practice_history', 'memory_corrections')
_COLLECTIONS = ('learning_feedback', 'mistakes', 'review_attempts')
_META = ('id', 'topic', 'display_title', 'knowledge_point', 'chapter', 'type', 'entry_type', 'at', 'date',
         'recorded_at', 'occurred_at', 'event_type', 'answered_at', 'updated_at')
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


def _own_text(value, path=''):
    """A record's direct scalar fields; nested records get their own pointers."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'target_snapshot' and re.fullmatch(r'/courses/[0-9]+/memory_corrections/[0-9]+', path):
                continue
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
            if key == 'target_snapshot' and re.fullmatch(r'/courses/[0-9]+/memory_corrections/[0-9]+', path):
                # Correction snapshots preserve old evidence; they are not live
                # records to match or relocate a correction onto again.
                continue
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


def record_hash(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def record_roots(state, index, course):
    roots = [(f'/courses/{index}/{key}', course[key], key == 'topic_evidence', None)
             for key in _COURSE_FIELDS if key in course]
    for collection in _COLLECTIONS:
        rows = state.get(collection) or []
        if isinstance(rows, list):
            for i, row in reversed(list(enumerate(rows))):
                if course['id'] not in record_course_ids(state, row, collection):
                    continue
                linked = review_attempt_context(state, row) if collection == 'review_attempts' else None
                roots.append((f'/{collection}/{i}', row, False, linked))
    return roots


def _attach_topic_corrections(index, course, matches):
    """Link every visible observation to its corrections before ranking limits.

    Reverse links are built from all same-course observations, including rows
    whose text does not match the query. Links never select a winning level.
    """
    base = f'/courses/{index}/topic_evidence'
    rows = course.get('topic_evidence', [])
    if not isinstance(rows, list):
        return
    events = {base + '/' + str(i): row for i, row in enumerate(rows)
              if isinstance(row, dict) and row.get('course_id') in (None, course['id'])
              and (not isinstance(row.get('course_ids'), list) or course['id'] in row['course_ids'])}
    reverse = {}
    for path, row in events.items():
        targets = row.get('corrects', [])
        targets = [targets] if isinstance(targets, str) else targets if isinstance(targets, list) else []
        for target in targets:
            if isinstance(target, str) and target:
                reverse.setdefault(target, set()).add(path)
    if not reverse:
        return
    resolved = {}
    for preview in matches:
        if not preview['path'].startswith(base + '/'):
            continue
        event_path = base + '/' + preview['path'][len(base) + 1:].split('/')[0]
        record_id = events.get(event_path, {}).get('id')
        if not isinstance(record_id, str) or not record_id:
            continue
        if event_path not in resolved:
            pending, seen_ids, links = [record_id], set(), set()
            while pending:
                current = pending.pop()
                if current in seen_ids:
                    continue
                seen_ids.add(current)
                for path in reverse.get(current, ()):
                    if path == event_path:
                        continue
                    links.add(path)
                    next_id = events[path].get('id')
                    if isinstance(next_id, str) and next_id:
                        pending.append(next_id)
            resolved[event_path] = sorted(links, key=lambda path: int(path.rsplit('/', 1)[1]), reverse=True)
        paths = resolved[event_path]
        if paths:
            preview['correction_review_required'] = True
            preview['topic_corrections'] = {
                'count': len(paths), 'paths': paths[:8], 'truncated': len(paths) > 8,
                'all_records_path': base, 'relation': 'direct_or_chained_corrects'}


def search_evidence(state, course_id, query, limit=8):
    """Rank source locators by exact phrase, explicit aliases, then keywords."""
    from learning_library.recall import plan, match
    if not isinstance(query, str) or not _norm(query) or len(query) > 240:
        raise ValueError('Evidence query must contain 1-240 characters')
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('Evidence limit must be between 1 and 20')
    index, course = _course(state, course_id)
    canonical_id = course['id']
    query_plan = plan(query, course.get('memory_aliases', []))
    candidates = []
    for base, records, structured, linked in record_roots(state, index, course):
        for path, record in _walk(records, base, canonical_id):
            score, reason = match(_own_text(record, path), query_plan)
            linked_score, linked_reason = match(linked['search_text'], query_plan) if path == base and linked else (0, None)
            if not score and not linked_score:
                continue
            if linked_score > score:
                score, reason = linked_score, linked_reason
            is_event = structured and path.count('/') == base.count('/') + 1
            preview = _preview(record, path, is_event)
            preview.update(record_sha256=record_hash(record), match_reason=reason)
            if is_event and isinstance(record.get('source'), dict) and record['source'].get('kind') == 'observed_attempt':
                binding = course.get('evidence_bindings', {}).get(record.get('id'), {})
                preview['source_binding_status'] = binding.get('status', 'unresolved_legacy')
                preview['source_detail_usage'] = 'library.cmd evidence-source --course ' + canonical_id + ' --observation ' + str(record.get('id'))
            if linked_score:
                preview['linked_question_paths'] = list(linked['linked_paths'])
            # Prefer a topic/title match over an incidental match in an answer.
            headings = []
            if isinstance(record, dict):
                for key in ('topic', 'title', 'knowledge_point', 'display_title', 'covered'):
                    value = record.get(key)
                    headings.extend([value] if isinstance(value, str) else
                                    [s for s in value if isinstance(s, str)] if isinstance(value, list) else [])
            title_score, _ = match(headings, query_plan)
            candidates.append((score + (10 if title_score else 0), preview))
    candidates.sort(key=lambda item: item[0], reverse=True)  # stable within equal relevance
    _attach_topic_corrections(index, course, [row for _, row in candidates])
    if course.get('memory_corrections'):
        from learning_library.memory import attach_corrections
        attach_corrections(state, canonical_id, [row for _, row in candidates])
    matches, used = [], 0
    for _, preview in candidates:
        size = len(json.dumps(preview, ensure_ascii=False))
        if len(matches) < limit and used + size <= 6000:
            matches.append(preview)
            used += size
    return {'state_revision': state.get('revision'), 'course_id': canonical_id,
            'query': query, 'match_mode': query_plan['mode'],
            'expanded_terms': query_plan['aliases'],
            'result_order': 'phrase/alias relevance, topic match, then source order; not a mastery ranking',
            'searched_paths': [f'/courses/{index}/{key}' for key in _COURSE_FIELDS if key in course]
                              + ['/' + key for key in _COLLECTIONS if isinstance(state.get(key), list)],
            'matches': matches,
            'total_matching_locations': len(candidates), 'shown': len(matches), 'truncated': len(candidates) > len(matches),
            'status': 'candidates_found' if candidates else 'no_matching_record',
            'learning_conclusion': 'not_assessed', 'state_written': False,
            'note': '未查到记录不等于没学过；命中仅定位证据。旧笔记不自动推断等级，已讲解不等于独立掌握；按路径补读并核对本人纠正。',
            'detail_usage': 'library.cmd detail path /PATH; inspect corrections with memory --course ID (internal review; may expose answers)'}
