"""Inspectable course memory and append-only user corrections, no mastery inference."""
from copy import deepcopy
from datetime import datetime, timezone
import json

from learning_library.evidence import _course, _walk, record_roots, record_hash, search_evidence


def records(state, course_id):
    index, course = _course(state, course_id)
    result = {}
    for base, value, _, _ in record_roots(state, index, course):
        result.update(_walk(value, base, course['id']))
    if course.get('current_topic') is not None:
        result[f'/courses/{index}/current_topic'] = course['current_topic']
    return result


def correction_rows(state, course_id):
    index, course = _course(state, course_id)
    locations = {}
    for path, value in records(state, course_id).items():
        locations.setdefault(record_hash(value), []).append(path)
    result = []
    for i, row in enumerate(course.get('memory_corrections', [])):
        matches = locations.get(row['target_sha256'], [])
        path = f'/courses/{index}/memory_corrections/{i}'
        snapshot_status = ('preserved' if record_hash(row['target_snapshot']) == row['target_sha256']
                           else 'invalid') if 'target_snapshot' in row else 'unavailable_legacy'
        # Original snapshots may include answers. Only explicit detail reads
        # expose them; they are not current learning records or new evidence.
        summary = {key: deepcopy(value) for key, value in row.items() if key != 'target_snapshot'}
        result.append({**summary, 'path': path, 'snapshot_status': snapshot_status,
                       'snapshot_path': path + '/target_snapshot' if snapshot_status == 'preserved' else None,
                       'current_target_path': matches[0] if len(matches) == 1 else None,
                       'target_status': 'located' if len(matches) == 1 else 'ambiguous' if matches else 'changed_or_missing'})
    return result


def attach_corrections(state, course_id, matches):
    rows = correction_rows(state, course_id)
    for match in matches:
        related = [row for row in rows if row['target_sha256'] == match['record_sha256']]
        if related:
            # A note cannot silently turn an assisted answer into independent evidence.
            match['correction_review_required'] = True
            match['correction_paths'] = [row['path'] for row in related[-8:]]
            match['correction_count'] = len(related)


def correction_context(course, course_path):
    rows = course.get('memory_corrections', [])
    return {'total': len(rows), 'review_required': bool(rows),
            'paths': [f'{course_path}/memory_corrections/{i}' for i in range(max(0, len(rows)-8), len(rows))],
            'truncated': len(rows) > 8,
            'usage': 'memory --course ' + course['id'],
            'policy': 'Read user corrections before relying on the old claim; no automatic mastery changes.'}


def inspect_memory(state, course_id, query=None, limit=8):
    from learning_library.context import _Projection, _POSITION_KEYS, _progress_dimensions, read_detail, teaching_progress
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('Memory limit must be between 1 and 20')
    index, course = _course(state, course_id)
    base = f'/courses/{index}'
    progress = teaching_progress(course, base)
    claims = []
    for field in ('current_topic', 'teaching_cursor', 'learning_progress'):
        value = course.get(field)
        path = base + '/' + field
        if field == 'teaching_cursor' and value is None and course.get('teaching_events'):
            value = course['teaching_events']
            path = base + '/teaching_events'
        if value is None:
            continue
        root_record = {'path': path, 'record_sha256': record_hash(value)}
        projection = _Projection()
        if field == 'teaching_cursor':
            projection.mapping(value, path, _POSITION_KEYS)
            if progress['status'] in ('conflict', 'missing'):
                interpretation = ('conflicting_positions_review_sources' if progress['conflict']
                                  else 'no_saved_teaching_position')
                claims.append({'field': field, 'value': None, 'path': None,
                               'root_record': root_record, 'interpretation': interpretation})
                continue
            if progress['path']:
                path = progress['path']
                value = read_detail(state, 'path', path)['record']
        preview = projection.mapping(value, path,
                                     (*_POSITION_KEYS, 'current_chapter', 'chapter_progress_status'))
        if len(json.dumps(preview, ensure_ascii=False)) > 1400:
            preview = {'omitted': True, 'detail_path': path}
        claims.append({'field': field, 'value': preview, 'path': path,
                       'record_sha256': record_hash(value), 'root_record': root_record,
                       'omitted': projection.omitted, 'interpretation': 'recorded_not_reverified'})
    all_rows = correction_rows(state, course['id'])
    visible, used = [], 0
    for row in reversed(all_rows):
        size = len(json.dumps(row, ensure_ascii=False))
        if len(visible) >= limit or used + size > 6000:
            break
        visible.append(row); used += size
    answer = {'state_revision': state.get('revision'), 'course_id': course['id'],
              'state_written': False, 'network_started': False, 'claims': claims,
              'teaching_progress': progress, **_progress_dimensions(course, base),
              'corrections': visible, 'correction_count': len(all_rows),
              'corrections_truncated': len(visible) < len(all_rows),
              'learning_conclusion': 'not_assessed',
              'note': '作业范围、已记录教学位置与掌握分别显示。记录缺失不等于未学；旧记录不代表全部聊天已核读。纠正须核对，不修改原答、评分或掌握等级。',
              'correction_usage': 'memory-correct --course ID; stdin: id, expected_revision, target_path, target_sha256, statement'}
    if query is not None:
        answer['evidence'] = search_evidence(state, course['id'], query, limit)
    return answer


def apply_correction(state, course_id, payload):
    """Return a draft. The caller must use its existing transactional state writer."""
    expected_keys = {'id', 'expected_revision', 'target_path', 'target_sha256', 'statement'}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError('Correction requires exactly: ' + ', '.join(sorted(expected_keys)))
    for key, maximum in (('id', 120), ('target_path', 600), ('target_sha256', 64), ('statement', 1200)):
        if not isinstance(payload[key], str) or not payload[key].strip() or len(payload[key]) > maximum:
            raise ValueError('Invalid correction field: ' + key)
    if type(payload['expected_revision']) is not int:
        raise ValueError('expected_revision must be an integer')
    index, course = _course(state, course_id)
    request_hash = record_hash({'course_id': course['id'], **payload})
    for row in course.get('memory_corrections', []):
        if row['id'] == payload['id']:
            if row['request_sha256'] != request_hash:
                raise ValueError('Correction ID already used with different content')
            return deepcopy(state), False
    if payload['expected_revision'] != state.get('revision'):
        raise ValueError('学习状态已更新，请重读记忆后再纠正')
    candidates = records(state, course['id'])
    path = payload['target_path']
    if path not in candidates or record_hash(candidates[path]) != payload['target_sha256']:
        raise ValueError('纠正目标不属于本科或已经变化，请重新定位原记录')
    draft = deepcopy(state)
    draft['courses'][index].setdefault('memory_corrections', []).append({
        'id': payload['id'], 'target_path': path, 'target_sha256': payload['target_sha256'],
        'target_snapshot': deepcopy(candidates[path]),
        'statement': payload['statement'], 'kind': 'user_correction',
        'verification': 'user_reported_not_independently_verified',
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'request_sha256': request_hash})
    return draft, True


def validate_preserved(current, draft):
    """Normal saves preserve previous corrections and teaching events exactly."""
    from teaching_progress import validate_events_preserved
    validate_events_preserved(current, draft)
    updated = {c.get('id'): c for c in draft.get('courses', []) if isinstance(c, dict)}
    for course in current.get('courses', []):
        previous = course.get('memory_corrections', [])
        if not previous:
            continue
        following = updated.get(course.get('id'), {}).get('memory_corrections', [])
        if not isinstance(following, list) or record_hash(following[:len(previous)]) != record_hash(previous):
            raise ValueError('记忆纠正只能追加，不能改写或删除历史；请追加新的说明')
    previous_courses = {c.get('id'): c for c in current.get('courses', []) if isinstance(c, dict)}
    for course_id, course in updated.items():
        rows = course.get('memory_corrections', [])
        if not isinstance(rows, list):
            raise ValueError('记忆纠正必须保存为记录列表')
        offset = len(previous_courses.get(course_id, {}).get('memory_corrections', []))
        for row in rows[offset:]:
            if not isinstance(row, dict):
                raise ValueError('记忆纠正必须是完整记录')
            if 'target_snapshot' in row and record_hash(row['target_snapshot']) != row.get('target_sha256'):
                raise ValueError('记忆纠正的原记录快照与内容指纹不符')
