"""Tool-managed content bindings for observations whose original paths can move.

The old JSON Pointer remains provenance, never a live identity. No answers are
copied into bindings; matching is confined to records owned by the same course.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from course_links import record_course_ids
from teaching_evidence import (_COURSE_RECORDS, _ATTEMPT_COLLECTIONS,
                               _NON_ATTEMPT_PATHS, _resolve_attempt)

_ROUTING = {'next_step', 'stage', 'updated_at'}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def _digest(record, fields=None):
    # These direct fields route the next action; answer/condition/assessment and
    # all nested fields remain part of identity. In particular status is retained.
    fields = fields if fields is not None else sorted(set(record) - _ROUTING)
    if any(key not in record for key in fields):
        return None
    content = {key: record[key] for key in fields}
    return hashlib.sha256(_canonical(content).encode('utf-8')).hexdigest()


def _escape(key):
    return str(key).replace('~', '~0').replace('/', '~1')


def _candidates(state, course_id):
    def walk(node, path, parents=(), *, anchor_parent=True):
        if isinstance(node, dict):
            if node.get('course_id') not in (None, course_id):
                return
            if 'course_ids' in node and course_id not in (node['course_ids'] or []):
                return
            yield path, node, parents
            for key, value in node.items():
                if key.casefold() not in _NON_ATTEMPT_PATHS:
                    chain = (*parents, (node, key)) if anchor_parent else parents
                    yield from walk(value, path + '/' + _escape(key), chain)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from walk(value, path + '/' + str(index), parents)

    for index, course in enumerate(state.get('courses', [])):
        if course.get('id') == course_id:
            for field in sorted(_COURSE_RECORDS):
                if field in course:
                    yield from walk(course[field], f'/courses/{index}/{field}', anchor_parent=False)
    for collection in sorted(_ATTEMPT_COLLECTIONS):
        for index, record in enumerate(state.get(collection, [])):
            if course_id in record_course_ids(state, record, collection):
                yield from walk(record, f'/{collection}/{index}')


def _parent_records(parents):
    return [{key: value for key, value in parent.items() if key != branch and key not in _ROUTING}
            for parent, branch in parents
            if set(parent) - {branch} - _ROUTING]


def _anchor_records(parent_records):
    return [{'fields': sorted(parent), 'sha256': _digest(parent)}
            for parent in parent_records]


def _anchors(parents):
    return _anchor_records(_parent_records(parents))


def _anchor_matches(state, course_id, anchors):
    """Yield candidates whose non-routing parent records match stored anchors."""
    for path, record, parents in _candidates(state, course_id):
        parent_records = _parent_records(parents)
        if len(parent_records) != len(anchors):
            continue
        if any(_digest(parent, anchor['fields']) != anchor['sha256']
               for parent, anchor in zip(parent_records, anchors)):
            continue
        yield path, record, parent_records


def _index(state, course_id, fields=None, anchors=()):
    result = {}
    for path, record, _ in _anchor_matches(state, course_id, anchors):
        digest = _digest(record, fields)
        if digest is not None:
            result.setdefault(digest, (path, record))
    return result


def bind_source_changes(current, draft):
    """Called after ordinary draft validation, before any persistent write.

    Recompute the expected tool-owned map, so retries of an already validated
    draft are harmless but callers cannot replace a binding with a new answer.
    Unbound legacy observations stay explicitly unverified instead of guessing.
    """
    previous = {course['id']: course for course in current.get('courses', [])}
    pending = []
    for course in draft.get('courses', []):
        old = previous.get(course['id'], {})
        old_map = old.get('evidence_bindings', {})
        if not isinstance(old_map, dict):
            raise ValueError('知识观察来源绑定格式无效，需核对原档案')
        expected = deepcopy(old_map)
        old_ids = {row.get('id') for row in old.get('topic_evidence', [])}
        indexes = {}
        for row in course.get('topic_evidence', []):
            source = row.get('source', {})
            if row['id'] in old_map:
                binding = old_map[row['id']]
                if binding.get('status') == 'bound':
                    fields = tuple(binding['fields']) if 'fields' in binding else None
                    anchors = binding.get('ancestors', [])
                    index_key = (fields, _canonical(anchors))
                    if index_key not in indexes:
                        indexes[index_key] = _index(draft, course['id'], fields, anchors)
                    targets = indexes[index_key]
                    if binding.get('course_id') != course['id'] or binding.get('sha256') not in targets:
                        raise ValueError('已绑定的原答必须保留在同课程；换题前完整归档原记录，不能改指另一题')
            elif (row.get('level') in {'independent', 'delayed_pass'}
                  and source.get('kind') == 'observed_attempt'):
                if row['id'] in old_ids:
                    expected[row['id']] = {'status': 'unresolved_legacy', 'course_id': course['id']}
                else:
                    record = _resolve_attempt(draft, source.get('record_path'), course['id'])
                    parents = next(parents for path, _, parents in _candidates(draft, course['id'])
                                   if path == source['record_path'])
                    expected[row['id']] = {'status': 'bound', 'course_id': course['id'],
                                           'record_path_at_binding': source['record_path'],
                                           'fields': sorted(set(record) - _ROUTING),
                                           'ancestors': _anchors(parents),
                                           'sha256': _digest(record)}
        supplied = course.get('evidence_bindings', {})
        if _canonical(supplied) not in (_canonical(old_map), _canonical(expected)):
            raise ValueError('原答绑定由保存工具维护，不能手动修改或伪造')
        if expected:
            pending.append((course, expected))
    # Do not partially enrich a rejected multi-course draft.
    for course, expected in pending:
        course['evidence_bindings'] = expected


def migrate_archive_bindings(before, after, transform):
    """Move tool-owned bindings across a documented material path relocation.

    Called by the material version transaction right after state record strings
    were rewritten. ``transform(value)`` must apply exactly those documented
    replacements to a value. A binding moves only when the pre-move snapshot
    contains exactly one record with the binding's old digest and ancestor
    anchors (so the old target is unambiguous) and the moved state contains
    exactly one record whose content is the old content with those replacements
    applied. Missing, legacy, stale or ambiguous bindings stay byte-identical;
    later saves keep refusing them instead of guessing. Returns the count moved.
    """
    old_courses = {course.get('id'): course for course in before.get('courses', [])
                   if isinstance(course, dict)}
    moved = 0
    for course in after.get('courses', []):
        if not isinstance(course, dict):
            continue
        old_course = old_courses.get(course.get('id'))
        old_map = old_course.get('evidence_bindings') if isinstance(old_course, dict) else None
        bindings = course.get('evidence_bindings')
        if not isinstance(old_map, dict) or not isinstance(bindings, dict):
            continue
        for row in course.get('topic_evidence', []):
            observation_id = row.get('id') if isinstance(row, dict) else None
            binding = bindings.get(observation_id)
            if observation_id not in old_map or not isinstance(binding, dict):
                continue
            if binding.get('status') != 'bound' or binding.get('course_id') != course.get('id'):
                continue
            fields = tuple(binding['fields']) if 'fields' in binding else None
            anchors, sha = binding.get('ancestors', []), binding.get('sha256')
            if not isinstance(anchors, list) or not isinstance(sha, str):
                continue
            old_targets = [(record, parents)
                           for _, record, parents in _anchor_matches(before, course['id'], anchors)
                           if _digest(record, fields) == sha]
            if len(old_targets) != 1:
                continue
            record, parents = old_targets[0]
            expected = transform(deepcopy(record))
            expected_parents = [transform(deepcopy(parent)) for parent in parents]
            expected_anchors = _anchor_records(expected_parents)
            expected_sha = _digest(expected, fields)
            if expected_sha is None:
                continue
            if expected_sha == sha and _canonical(expected_anchors) == _canonical(anchors):
                continue  # No relocated path inside this record: fingerprint intact.
            new_targets = [item for _, item, item_parents
                           in _anchor_matches(after, course['id'], expected_anchors)
                           if item_parents == expected_parents
                           and _digest(item, fields) == expected_sha]
            if len(new_targets) != 1:
                continue
            binding['sha256'] = expected_sha
            binding['ancestors'] = expected_anchors
            moved += 1
    return moved


def resolve_observation(state, course_id, observation_id, *, include_record=False):
    courses = [row for row in state.get('courses', []) if row.get('id') == course_id]
    if len(courses) != 1:
        raise ValueError('课程不存在或不唯一')
    course = courses[0]
    rows = [row for row in course.get('topic_evidence', []) if row.get('id') == observation_id]
    if len(rows) != 1:
        raise ValueError('知识观察不存在或不唯一')
    binding = course.get('evidence_bindings', {}).get(observation_id, {})
    result = {'course_id': course_id, 'observation_id': observation_id,
              'state_written': False, 'status': 'unresolved_legacy',
              'note': '旧路径仅为历史位置；未绑定记录需人工核对，不能据此认定掌握。'}
    if binding.get('status') != 'bound':
        return result
    found = _index(state, course_id, binding.get('fields'), binding.get('ancestors', [])).get(binding.get('sha256'))
    if binding.get('course_id') != course_id or found is None:
        result['status'] = 'source_missing'
        return result
    path, record = found
    result.update(status='resolved', record_path=path,
                  note='已按同课程原记录内容核对；独立作答仍不等于正确或掌握。')
    if include_record:
        result.update(record=deepcopy(record), contains_answers=True)
    return result
