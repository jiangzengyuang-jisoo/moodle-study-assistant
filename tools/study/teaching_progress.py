"""Append actual teaching events using the existing state transaction and readback."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

EVENT_TYPES = {'explanation', 'attempt', 'feedback', 'topic_change', 'session_end',
               'history_recovery', 'answer_exposure'}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError('Invalid fields; required: ' + ', '.join(sorted(required)))


def _text(value, name, maximum=1200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Invalid text field: ' + name)


def _timestamp(value, name):
    _text(value, name, 64)
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(name + ' must be an ISO timestamp with timezone') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(name + ' must include a timezone')
    if result > datetime.now(timezone.utc):
        raise ValueError(name + ' cannot describe a future event')
    return result


def _validate(payload):
    _fields(payload, {'id', 'expected_revision', 'occurred_at', 'event_type',
                      'affects_current', 'source', 'position'}, {'attempts'})
    _text(payload['id'], 'id', 120)
    if type(payload['expected_revision']) is not int or payload['expected_revision'] < 0:
        raise ValueError('expected_revision must be a nonnegative integer')
    _timestamp(payload['occurred_at'], 'occurred_at')
    if payload['event_type'] not in EVENT_TYPES:
        raise ValueError('event_type must describe an actual teaching event')
    if type(payload['affects_current']) is not bool:
        raise ValueError('affects_current must be a boolean')
    if payload['event_type'] == 'answer_exposure' and payload['affects_current']:
        raise ValueError('Answer exposure alone cannot advance systematic teaching')
    source = payload['source']
    _fields(source, {'kind', 'thread_id', 'turn_ids'}, {'reviewed_at'})
    if source['kind'] != 'chat':
        raise ValueError('source.kind must be chat')
    _text(source['thread_id'], 'thread_id', 200)
    turns = source['turn_ids']
    if not isinstance(turns, list) or not 1 <= len(turns) <= 100:
        raise ValueError('source.turn_ids must identify the inspected turns')
    for turn in turns:
        _text(turn, 'turn_id', 200)
    if len(set(turns)) != len(turns):
        raise ValueError('source.turn_ids must be unique')
    if 'reviewed_at' in source:
        _timestamp(source['reviewed_at'], 'reviewed_at')
    position = payload['position']
    _fields(position, {'topic', 'stage', 'next_step'},
            {'chapter', 'section', 'material_id', 'pages', 'pending_question'})
    for key in ('topic', 'stage', 'next_step'):
        _text(position[key], key)
    for key in ('chapter', 'section', 'material_id'):
        if key in position and position[key] is not None:
            _text(position[key], key, 300)
    if 'pages' in position:
        pages = position['pages']
        if not isinstance(pages, list) or len(pages) > 100 or any(type(p) is not int or p < 1 for p in pages):
            raise ValueError('pages must be positive page numbers')
    pending = position.get('pending_question')
    if pending is not None:
        _fields(pending, {'id', 'prompt', 'status'})
        _text(pending['id'], 'pending_question.id', 120)
        _text(pending['prompt'], 'pending_question.prompt', 12000)
        if pending['status'] not in {'pending', 'skipped', 'answered', 'answer_shown'}:
            raise ValueError('Invalid pending question status')
    attempts = payload.get('attempts', [])
    if not isinstance(attempts, list) or len(attempts) > 20:
        raise ValueError('attempts must be a short list of actual answers')
    ids = set()
    for attempt in attempts:
        _fields(attempt, {'id', 'question', 'user_answer', 'conditions', 'source_turn_id'},
                {'topic', 'answered_at'})
        for key, maximum in (('id', 120), ('question', 12000), ('user_answer', 20000), ('source_turn_id', 200)):
            _text(attempt[key], 'attempt.' + key, maximum)
        if attempt['id'] in ids:
            raise ValueError('Attempt IDs must be unique')
        ids.add(attempt['id'])
        if attempt['source_turn_id'] not in turns:
            raise ValueError('Each original answer must reference an inspected source turn')
        if attempt.get('topic') is not None:
            _text(attempt['topic'], 'attempt.topic')
        if attempt.get('answered_at') is not None:
            _timestamp(attempt['answered_at'], 'attempt.answered_at')
        _fields(attempt['conditions'], {'assisted', 'answer_exposed'}, {'independent'})
        if any(value is not None and type(value) is not bool for value in attempt['conditions'].values()):
            raise ValueError('Answer conditions must be boolean or null when unknown')
    if payload['event_type'] == 'attempt' and not attempts:
        raise ValueError('An attempt event requires an actual user answer')
    return {key: deepcopy(value) for key, value in payload.items() if key != 'expected_revision'}


def _course(state, course_id):
    for index, course in enumerate(state.get('courses', [])):
        if str(course.get('id')) == str(course_id):
            return index, course
    raise ValueError('Unknown course: ' + str(course_id))


def apply_teaching(state, course_id, payload):
    """Only append evidence; never infer or update mastery, mistakes or FSRS."""
    content = _validate(payload)
    index, course = _course(state, course_id)
    request_hash = _hash({'course_id': course['id'], **content})
    events = course.get('teaching_events', [])
    if not isinstance(events, list):
        raise ValueError('teaching_events must be a list')
    for event in events:
        if event['id'] == payload['id']:
            if event.get('request_sha256') != request_hash:
                raise ValueError('Teaching event ID already used with different content')
            return deepcopy(state), False
    if payload['expected_revision'] != state.get('revision', 0):
        raise ValueError('学习状态已更新，请重读后合并教学记录')
    old_ids = {attempt['id'] for event in events for attempt in event.get('attempts', [])}
    if any(attempt['id'] in old_ids for attempt in content.get('attempts', [])):
        raise ValueError('An original attempt ID is already recorded; reuse the original event ID for retry')
    draft = deepcopy(state)
    draft['courses'][index].setdefault('teaching_events', []).append({
        **content, 'recorded_at': datetime.now(timezone.utc).isoformat(),
        'request_sha256': request_hash})
    return draft, True


def validate_events_preserved(current, draft):
    """All writers retain existing events byte-for-value and validate appended rows."""
    old = {str(c.get('id')): c for c in current.get('courses', [])}
    new = {str(c.get('id')): c for c in draft.get('courses', [])}
    for course_id in old.keys() | new.keys():
        previous = old.get(course_id, {}).get('teaching_events', [])
        following = new.get(course_id, {}).get('teaching_events', [])
        if not isinstance(following, list) or _hash(following[:len(previous)]) != _hash(previous):
            raise ValueError('Teaching events and original answers are append-only')
        ids = {event['id'] for event in previous}
        attempt_ids = {attempt['id'] for event in previous for attempt in event.get('attempts', [])}
        for event in following[len(previous):]:
            _fields(event, {'id', 'occurred_at', 'event_type', 'affects_current', 'source',
                            'position', 'recorded_at', 'request_sha256'}, {'attempts'})
            _timestamp(event['recorded_at'], 'recorded_at')
            request = {k: v for k, v in event.items() if k not in {'recorded_at', 'request_sha256'}}
            content = _validate({**request, 'expected_revision': 0})
            if event['request_sha256'] != _hash({'course_id': new[course_id]['id'], **content}):
                raise ValueError('Teaching event content hash does not match')
            if event['id'] in ids:
                raise ValueError('Teaching event IDs must be unique')
            ids.add(event['id'])
            for attempt in event.get('attempts', []):
                if attempt['id'] in attempt_ids:
                    raise ValueError('Original attempt IDs must be unique')
                attempt_ids.add(attempt['id'])


def _receipt(state, persisted, course_id, payload, draft, changed):
    from learning_library.context import teaching_progress as select_progress
    index, course = _course(persisted, course_id)
    _, expected_course = _course(draft, course_id)
    expected = next(e for e in expected_course['teaching_events'] if e['id'] == payload['id'])
    rows = course.get('teaching_events', [])
    hits = [(i, e) for i, e in enumerate(rows) if e.get('id') == payload['id']]
    expected_revision = state.get('revision', 0) + (1 if changed else 0)
    if (len(hits) != 1 or hits[0][1] != expected
            or persisted.get('revision', 0) != expected_revision or persisted != draft):
        raise RuntimeError('教学记录保存后回读不一致；不能报告已保存，请重读状态核查')
    path = f'/courses/{index}/teaching_events/{hits[0][0]}'
    before_index, before_course = _course(state, course_id)
    before = select_progress(before_course, f'/courses/{before_index}')
    progress = select_progress(course, f'/courses/{index}')
    advanced = (changed and progress['status'] == 'recorded'
                and progress.get('path') == path + '/position'
                and before.get('path') != progress.get('path'))
    return {'saved': True, 'already_saved': not changed, 'event_id': payload['id'],
            'revision': persisted.get('revision', 0), 'readback_verified': True,
            'event_path': path, 'active_advanced': advanced, 'current_progress': progress,
            'original_evidence_preserved': True, 'mastery_updated': False,
            'network_started': False, 'views_need_refresh': True}


def _write(path, draft, original):
    from state_io import bridge
    bridge(path, 'replace', {'expected': original, 'state': draft})


def save_teaching(path, course_id, payload):
    from state_io import workspace_lock, prepare_state, read
    path = Path(path)
    with workspace_lock(path):
        prepare_state(path)
        state = read(path)
        draft, changed = apply_teaching(state, course_id, payload)
        if changed:
            _write(path, draft, state)
        persisted = read(path)
        return _receipt(state, persisted, course_id, payload, draft, changed)
