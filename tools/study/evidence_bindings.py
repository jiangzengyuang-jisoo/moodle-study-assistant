"""Tool-managed content bindings for observations whose original paths can move.

The old JSON Pointer remains provenance, never a live identity. No answers are
copied into bindings; matching is confined to records owned by the same course.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from course_links import record_course_ids
from cursor_evidence import workflow_status_is_routing, status_is_protected
from teaching_evidence import (_COURSE_RECORDS, _ATTEMPT_COLLECTIONS,
                               _NON_ATTEMPT_PATHS, _resolve_attempt)

_ROUTING = {'next_step', 'stage', 'updated_at'}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def _digest(record, fields=None):
    # Keep the legacy default for bindings without an explicit field list.
    # New bindings choose fields with the same workflow/verdict rule as cursors.
    fields = fields if fields is not None else sorted(set(record) - _ROUTING)
    if any(key not in record for key in fields):
        return None
    content = {key: record[key] for key in fields}
    return hashlib.sha256(_canonical(content).encode('utf-8')).hexdigest()


def _binding_fields(record, branch=None):
    fields = set(record) - _ROUTING - {branch}
    if workflow_status_is_routing(record):
        fields.discard('status')
    return sorted(fields)


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


def _anchors(parents):
    return [{'fields': _binding_fields(parent, branch),
             'sha256': _digest(parent, _binding_fields(parent, branch))}
            for parent, branch in parents if set(parent) - {branch} - _ROUTING]


def _index(state, course_id, fields=None, anchors=()):
    result = {}
    for path, record, parents in _candidates(state, course_id):
        parent_records = _parent_records(parents)
        if len(parent_records) != len(anchors) or any(
                _digest(parent, anchor['fields']) != anchor['sha256']
                for parent, anchor in zip(parent_records, anchors)):
            continue
        digest = _digest(record, fields)
        if digest is not None:
            result.setdefault(digest, (path, record))
    return result


def _upgrade_workflow_binding(current, course_id, binding):
    """Normalize legacy workflow fields only after verifying their old hashes.

    The trusted current state supplies the original answer and parent questions;
    a changed draft must never be used to re-sign an unverified historical link.
    All previously bound non-routing fields retain their original values.
    """
    if binding.get('course_id') != course_id:
        return binding
    found = _index(current, course_id, binding.get('fields'),
                   binding.get('ancestors', [])).get(binding.get('sha256'))
    if found is None:
        return binding  # Existing missing evidence remains missing, not guessed.
    path, record = found
    parents = next(parents for candidate, _, parents in _candidates(current, course_id)
                   if candidate == path)
    upgraded = deepcopy(binding)
    fields = binding.get('fields', sorted(set(record) - _ROUTING))
    if 'status' in fields and workflow_status_is_routing(record):
        upgraded['fields'] = [key for key in fields if key != 'status']
        upgraded['sha256'] = _digest(record, upgraded['fields'])
    elif 'status' not in fields and status_is_protected(record):
        upgraded['fields'] = sorted([*fields, 'status'])
        upgraded['sha256'] = _digest(record, upgraded['fields'])
    parent_contexts = [(parent, branch) for parent, branch in parents
                       if set(parent) - {branch} - _ROUTING]
    for anchor, (parent, _) in zip(upgraded.get('ancestors', []), parent_contexts):
        if 'status' in anchor['fields'] and workflow_status_is_routing(parent):
            anchor['fields'] = [key for key in anchor['fields'] if key != 'status']
            anchor['sha256'] = _digest(parent, anchor['fields'])
        elif 'status' not in anchor['fields'] and status_is_protected(parent):
            anchor['fields'] = sorted([*anchor['fields'], 'status'])
            anchor['sha256'] = _digest(parent, anchor['fields'])
    return upgraded


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
        observations = course.get('topic_evidence', [])
        if not isinstance(observations, list):
            raise ValueError('topic_evidence 必须是追加式记录列表')
        for row in observations:
            if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                    or not row['id'].strip() or not isinstance(row.get('source', {}), dict)):
                raise ValueError('知识观察需要唯一记录 ID 和有效来源')
            source = row.get('source', {})
            if row['id'] in old_map:
                binding = old_map[row['id']]
                if binding.get('status') == 'bound':
                    binding = _upgrade_workflow_binding(current, course['id'], binding)
                    expected[row['id']] = binding
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
                                           'fields': _binding_fields(record),
                                           'ancestors': _anchors(parents),
                                           'sha256': _digest(record, _binding_fields(record))}
        supplied = course.get('evidence_bindings', {})
        if _canonical(supplied) not in (_canonical(old_map), _canonical(expected)):
            raise ValueError('原答绑定由保存工具维护，不能手动修改或伪造')
        if expected:
            pending.append((course, expected))
    # Do not partially enrich a rejected multi-course draft.
    for course, expected in pending:
        course['evidence_bindings'] = expected


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
