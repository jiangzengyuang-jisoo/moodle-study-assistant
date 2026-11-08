"""Small, declarative teaching-position events merged into the shared state.

The caller (``daily.py`` / ``session.py``) owns the workspace lock, the latest
read, the atomic save and the one view rebuild. This module only validates one
event's payload and merges it into a private deep copy, then reports what
changed. It never writes files and never changes learner evidence: answers,
sources, mastery, history, other collections and other courses are out of scope.
Descriptive lesson records (user_request/lesson_scope/worked_example/
coverage_note) may be created or updated; they are not mastery evidence.
"""
import copy
from datetime import datetime
import json

import study_time
from cursor_evidence import PENDING_KEYS, active_snapshot, has_attempt

# One event describes a teaching position, at most a handful of archived
# attempts and one old-chapter backlog item; anything larger is not "small".
TOP_KEYS = {'course_id', 'chat_id', 'event_id', 'fields', 'checks_append',
            'review_backlog_append', 'archive_pending', 'onboarding'}
FIELD_KEYS = {'chapter', 'topic', 'stage', 'next_step', 'material_id', 'pages',
              'source', 'pending_check', 'user_request', 'lesson_scope',
              'worked_example', 'coverage_note'}
ONBOARDING_OUTCOMES = {'shown', 'skipped'}
BACKLOG_KEYS = {'id', 'topic', 'chapter', 'status', 'next_step'}
ANSWER_KEYS = {'answer', 'user_answer', 'user_answer_or_method', 'user_attempt',
               'submitted_answer', 'response', 'response_text'}
MAX_CHECKS = 5
MAX_BACKLOG = 3
MAX_CHAPTER = 300
MAX_STEP = 800
MAX_PROMPT = 4000
MAX_PAGES = 500
MAX_USER_REQUEST = 500
MAX_SCOPE_ITEMS = 12
MAX_SCOPE_ITEM = 300
MAX_WORKED_BYTES = 4000
MAX_COVERAGE_NOTE = 800
MAX_CHECK_BYTES = 8000
MAX_PENDING_BYTES = 8000
MAX_SOURCE_BYTES = 4000


def _same(first, second):
    """JSON comparison: Python would equate 0/False and 1/True."""
    return json.dumps(first, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(
        second, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _text(value, name, maximum, *, nullable=False):
    if value is None:
        if nullable:
            return None
        raise ValueError(name + ' 不能为空')
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + ' 需要非空字符串')
    if len(value) > maximum:
        raise ValueError(name + ' 超过 ' + str(maximum) + ' 字符上限')
    return value


def _records(value, name, maximum, size_limit):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(name + ' 必须是 JSON 数组')
    if len(value) > maximum:
        raise ValueError(name + ' 最多 ' + str(maximum) + ' 条')
    rows = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError(name + ' 的每条记录必须是 JSON 对象')
        _text(row.get('id'), name + ' 记录的 id', 200)
        if _size(row) > size_limit:
            raise ValueError(name + ' 记录过大，请只追加必要的完整记录')
        rows.append(copy.deepcopy(row))
    return rows


def _validate_pending(value):
    _text(value.get('id'), 'pending_check.id', 200)
    _text(value.get('prompt'), 'pending_check.prompt', MAX_PROMPT)
    if has_attempt(value):
        raise ValueError('pending_check 表示待作答，不能携带实际作答或评分；'
                         '请改用 checks_append 保存已完成记录，或用 null 清空待答题')
    if _size(value) > MAX_PENDING_BYTES:
        raise ValueError('pending_check 过大，请只保留待回答所需信息')
    return copy.deepcopy(value)


def _validate_field(key, value):
    if key in {'chapter', 'topic'}:
        return _text(value, 'fields.' + key, MAX_CHAPTER)
    if key in {'stage', 'next_step'}:
        return _text(value, 'fields.' + key, MAX_STEP)
    if key == 'material_id':
        return _text(value, 'fields.material_id', 300, nullable=True)
    if key == 'user_request':
        return _text(value, 'fields.user_request', MAX_USER_REQUEST)
    if key == 'coverage_note':
        return _text(value, 'fields.coverage_note', MAX_COVERAGE_NOTE)
    if key == 'lesson_scope':
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_SCOPE_ITEMS:
            raise ValueError('fields.lesson_scope 需要 1–' + str(MAX_SCOPE_ITEMS)
                             + ' 条字符串的数组')
        scope = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError('fields.lesson_scope 的每一条必须是非空字符串')
            if len(item) > MAX_SCOPE_ITEM:
                raise ValueError('fields.lesson_scope 的每一条不能超过 '
                                 + str(MAX_SCOPE_ITEM) + ' 字符上限')
            scope.append(item)
        return scope
    if key == 'worked_example':
        if not isinstance(value, dict) or not value:
            raise ValueError('fields.worked_example 需要非空 JSON 对象')
        if _size(value) > MAX_WORKED_BYTES:
            raise ValueError('fields.worked_example 超过 ' + str(MAX_WORKED_BYTES)
                             + ' 字符上限')
        return copy.deepcopy(value)
    if key == 'pages':
        if value is None:
            return None
        if not isinstance(value, list) or len(value) > MAX_PAGES:
            raise ValueError('fields.pages 需要页码数组，最多 ' + str(MAX_PAGES) + ' 项')
        pages = []
        for page in value:
            if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                raise ValueError('fields.pages 的每一项必须是正整数页码')
            pages.append(page)
        return pages
    if key == 'source':
        if not isinstance(value, dict) or not value:
            raise ValueError('fields.source 需要非空对象；只能追加新键')
        for source_key, item in value.items():
            if not isinstance(source_key, str) or not source_key.strip():
                raise ValueError('fields.source 的键必须是非空字符串')
            if _size(item) > 2000:
                raise ValueError('fields.source.' + source_key + ' 过大')
        if _size(value) > MAX_SOURCE_BYTES:
            raise ValueError('fields.source 过大')
        return copy.deepcopy(value)
    if key == 'pending_check':
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError('fields.pending_check 必须是 JSON 对象或 null')
        return _validate_pending(value)
    raise ValueError('未知 fields 字段：' + key + '；只接受 ' + '、'.join(sorted(FIELD_KEYS)))


def validate_payload(payload):
    """Validate one small event; return a normalized copy. No I/O, no state change."""
    if not isinstance(payload, dict):
        raise ValueError('teaching-event 需要 JSON 对象输入')
    unknown = sorted(set(payload) - TOP_KEYS)
    if unknown:
        raise ValueError('未知事件字段：' + '、'.join(unknown) + '；只接受 '
                         + '、'.join(sorted(TOP_KEYS)))
    result = {
        'course_id': _text(payload.get('course_id'), 'course_id', 200),
        'chat_id': _text(payload.get('chat_id'), 'chat_id', 300),
        'event_id': None,
        'fields': {},
        'checks_append': [],
        'review_backlog_append': [],
        'archive_pending': False,
        'onboarding': None,
    }
    event_id = payload.get('event_id')
    if event_id is not None:
        result['event_id'] = _text(event_id, 'event_id', 200)
    fields = payload.get('fields')
    if fields is not None and not isinstance(fields, dict):
        raise ValueError('fields 必须是 JSON 对象')
    for key, value in (fields or {}).items():
        result['fields'][key] = _validate_field(key, value)
    result['checks_append'] = _records(payload.get('checks_append'), 'checks_append',
                                       MAX_CHECKS, MAX_CHECK_BYTES)
    backlog = _records(payload.get('review_backlog_append'), 'review_backlog_append',
                       MAX_BACKLOG, 2000)
    for row in backlog:
        unknown_row = sorted(set(row) - BACKLOG_KEYS)
        if unknown_row:
            raise ValueError('review_backlog_append 含未知字段：' + '、'.join(unknown_row)
                             + '；只接受 ' + '、'.join(sorted(BACKLOG_KEYS)))
        row.setdefault('status', 'pending')
        if row['status'] != 'pending':
            raise ValueError('review_backlog_append.status 目前只接受 pending')
        for key in ('topic', 'chapter', 'next_step'):
            if key in row:
                row[key] = _text(row[key], 'review_backlog_append.' + key, MAX_CHAPTER)
    result['review_backlog_append'] = backlog
    onboarding = payload.get('onboarding')
    if onboarding is not None and onboarding not in ONBOARDING_OUTCOMES:
        raise ValueError('onboarding 只能是 shown 或 skipped')
    result['onboarding'] = onboarding
    archive_pending = payload.get('archive_pending', False)
    if not isinstance(archive_pending, bool):
        raise ValueError('archive_pending 必须是 true 或 false')
    result['archive_pending'] = archive_pending
    if archive_pending and 'pending_check' not in result['fields']:
        raise ValueError('archive_pending 需要同时提供 fields.pending_check（null 表示清空并保留旧题）')
    if not (result['fields'] or result['checks_append'] or result['review_backlog_append']
            or result['onboarding']):
        raise ValueError('事件没有可应用的变化：请至少提供 fields / checks_append / '
                         'review_backlog_append / onboarding 之一')
    return result


def _find_course(state, course_id):
    courses = state.get('courses')
    if not isinstance(courses, list):
        raise ValueError('学习状态缺少课程列表，未写入任何记录')
    matches = [course for course in courses if isinstance(course, dict) and course.get('id') == course_id]
    if not matches:
        raise ValueError('课程不存在：' + course_id + '；请使用 state 中已登记的课程 ID')
    if len(matches) > 1:
        raise ValueError('课程 ID 在状态中不唯一，请先核对课程记录')
    return matches[0]


def _validate_material(state, course_id, material_id):
    if material_id is None:
        return
    rows = state.get('materials')
    if not isinstance(rows, list):
        raise ValueError('资料列表格式异常，不能登记 material_id')
    row = next((item for item in rows if isinstance(item, dict) and item.get('id') == material_id), None)
    if row is None:
        raise ValueError('material_id 未在已登记资料中找到：' + material_id)
    if row.get('course_id') not in (None, course_id):
        raise ValueError('material_id 属于其他课程，不能登记到本课程')


def _apply_source(cursor, value, replace=False):
    """Merge source text; a chapter switch may replace it as a whole.

    The previous position, including its complete source object, is already in
    ``teaching_cursor.history`` when ``replace`` is true, so refusing a new
    chapter's scope note would make a real chapter advance impossible. Within
    the same chapter an existing source key is still immutable: only new keys
    may be added.
    """
    old = cursor.get('source')
    if old is None:
        cursor['source'] = copy.deepcopy(value)
        return True
    if not isinstance(old, dict):
        raise ValueError('teaching_cursor.source 格式异常，请保留原记录并核对')
    if replace:
        if _same(old, value):
            return False
        cursor['source'] = copy.deepcopy(value)
        return True
    merged = copy.deepcopy(old)
    changed = False
    for key, item in value.items():
        if key in old:
            if not _same(old[key], item):
                raise ValueError('既有来源证据不能改写（source.' + key + '）；'
                                 '只能追加新键，或追加更正说明')
            continue
        merged[key] = copy.deepcopy(item)
        changed = True
    if changed:
        cursor['source'] = merged
    return changed


def _apply_checks(cursor, records):
    if not records:
        return 0
    existing = cursor.get('checks')
    if existing is None:
        existing = []
    elif not isinstance(existing, list):
        raise ValueError('teaching_cursor.checks 必须是追加式列表，请核对原记录')
    rows = list(existing)
    appended = 0
    for record in records:
        if any(_same(row, record) for row in rows):
            continue
        rows.append(copy.deepcopy(record))
        appended += 1
    if appended:
        cursor['checks'] = rows
    return appended


def _apply_pending(cursor, fields, appended_records, archive_pending):
    new_value = fields['pending_check']
    aliases = [key for key in PENDING_KEYS if cursor.get(key) is not None]
    old = cursor[aliases[0]] if aliases else None
    archived = False
    changed = False
    if old is not None and has_attempt(old):
        if archive_pending:
            checks = cursor.get('checks')
            if checks is None:
                checks = []
            elif not isinstance(checks, list):
                raise ValueError('teaching_cursor.checks 必须是追加式列表，请核对原记录')
            if not any(_same(row, old) for row in checks):
                cursor['checks'] = list(checks) + [copy.deepcopy(old)]
                archived = True
                changed = True
        elif not any(_same(row, old) for row in appended_records):
            raise ValueError('当前待答题已有实际作答；请先把它完整追加到同课程 '
                             'teaching_cursor.checks（checks_append 需包含该完整记录）'
                             '或传 archive_pending: true')
    if len(aliases) == 1 and aliases[0] == 'pending_check' and _same(old, new_value):
        return {'changed': changed, 'pending_id': (new_value or {}).get('id'),
                'archived': archived}
    if not aliases and new_value is None:
        return {'changed': changed, 'pending_id': None, 'archived': archived}
    for key in aliases:
        cursor.pop(key, None)
    if new_value is not None:
        cursor['pending_check'] = copy.deepcopy(new_value)
    return {'changed': True, 'pending_id': (new_value or {}).get('id'), 'archived': archived}


def _apply_backlog(course, records):
    if not records:
        return 0
    existing = course.get('review_backlog')
    if existing is None:
        existing = []
    elif not isinstance(existing, list):
        raise ValueError('review_backlog 必须是追加式列表，请核对原记录')
    rows = list(existing)
    appended = 0
    for record in records:
        match = next((row for row in rows if isinstance(row, dict)
                      and row.get('id') == record['id']), None)
        if match is not None:
            if _same(match, record):
                continue
            raise ValueError('review_backlog 编号已存在且内容不同：' + record['id'])
        rows.append(copy.deepcopy(record))
        appended += 1
    if appended:
        course['review_backlog'] = rows
    return appended


def _is_switch(cursor, fields):
    for key in ('topic', 'chapter'):
        if key not in fields:
            continue
        old = cursor.get(key)
        if old not in (None, '') and not _same(old, fields[key]):
            return True
    return False


def _archive_history(cursor):
    """Append the previous position; validate_cursor_history requires the copy
    in the newly appended tail, so a switch always appends even when an equal
    snapshot already exists in an older part of the history."""
    snapshot = copy.deepcopy(active_snapshot(cursor))
    history = cursor.get('history')
    if isinstance(history, dict):
        cursor['history'] = [copy.deepcopy(history), snapshot]
        return True
    if history in (None, ''):
        cursor['history'] = [snapshot]
        return True
    if not isinstance(history, list):
        raise ValueError('teaching_cursor.history 必须是追加式列表，请核对原记录')
    cursor['history'] = list(copy.deepcopy(history)) + [snapshot]
    return True


def merge(state, payload, now, operation_id=None):
    """Merge one validated event into a deep copy of the latest state.

    Returns ``{state, course_id, operation_id, changed, changed_fields,
    pending_check_id, history_appended, onboarding_status, created_cursor}``.
    The caller still runs ``validate_draft``, ``planning.refresh_plan_state`` and
    the shared save/view pipeline; this function proves nothing about conflicts.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('事件时间必须包含时区')
    event = validate_payload(payload)
    merged = copy.deepcopy(state)
    course = _find_course(merged, event['course_id'])
    cursor = course.get('teaching_cursor')
    created = False
    if cursor is None:
        # An onboarding-only event must not create an empty teaching position.
        if event['fields'] or event['checks_append']:
            cursor = course['teaching_cursor'] = {'checks': [], 'history': [],
                                                  'mastery_status': 'not_assessed'}
            created = True
    elif not isinstance(cursor, dict):
        raise ValueError('teaching_cursor 格式异常，请保留原记录并核对')
    fields = event['fields']
    changed_fields = []
    history_appended = False
    pending_meta = {'changed': False, 'pending_id': None, 'archived': False}
    if isinstance(cursor, dict):
        if _is_switch(cursor, fields):
            history_appended = _archive_history(cursor)
            changed_fields.append('topic' if 'topic' in fields else 'chapter')
        for key in ('chapter', 'topic', 'stage', 'next_step'):
            if key in fields and not _same(cursor.get(key), fields[key]):
                cursor[key] = fields[key]
                if key not in changed_fields:
                    changed_fields.append(key)
        if 'material_id' in fields:
            value = fields['material_id']
            _validate_material(merged, event['course_id'], value)
            if not _same(cursor.get('material_id'), value):
                cursor['material_id'] = value
                changed_fields.append('material_id')
        if 'pages' in fields:
            value = fields['pages']
            if not _same(cursor.get('pages'), value):
                cursor['pages'] = copy.deepcopy(value)
                changed_fields.append('pages')
        # Descriptive lesson records are created or updated when supplied; an
        # omitted field keeps its old value and nothing here deletes them.
        for key in ('user_request', 'lesson_scope', 'worked_example', 'coverage_note'):
            if key in fields and not _same(cursor.get(key), fields[key]):
                cursor[key] = copy.deepcopy(fields[key])
                changed_fields.append(key)
        if 'source' in fields and _apply_source(cursor, fields['source'],
                                                replace=history_appended):
            changed_fields.append('source')
        if _apply_checks(cursor, event['checks_append']):
            changed_fields.append('checks')
        if 'pending_check' in fields:
            pending_meta = _apply_pending(cursor, event['fields'], event['checks_append'],
                                          event['archive_pending'])
            if pending_meta['changed']:
                changed_fields.append('pending_check')
    if _apply_backlog(course, event['review_backlog_append']):
        changed_fields.append('review_backlog')
    onboarding_status = None
    if event['onboarding']:
        from product_tour import record_event
        onboarding_changed = record_event(merged, event['onboarding'], event['chat_id'], at=now)
        onboarding_status = 'recorded' if onboarding_changed else 'already_recorded'
        if onboarding_changed:
            changed_fields.append('onboarding')
    semantic = created or bool(changed_fields)
    if semantic and isinstance(cursor, dict):
        cursor['chat_id'] = event['chat_id']
        if operation_id is not None:
            cursor['operation_id'] = str(operation_id)
        cursor['updated_at'] = now.astimezone(study_time.local_zone()).isoformat()
    return {'state': merged, 'course_id': event['course_id'],
            'operation_id': str(operation_id) if operation_id is not None else None,
            'changed': semantic, 'changed_fields': changed_fields,
            'pending_check_id': pending_meta['pending_id'],
            'history_appended': history_appended,
            'onboarding_status': onboarding_status, 'created_cursor': created,
            'pending_archived': pending_meta['archived']}


def describe_result(meta, *, status, revision, state_written, views_updated):
    """The stable JSON shape returned by the teaching-event command."""
    return {'status': status,
            'course_id': meta.get('course_id'),
            'operation_id': meta.get('operation_id'),
            'revision': revision,
            'state_written': bool(state_written),
            'views_updated': list(views_updated),
            'changed_fields': list(meta.get('changed_fields') or []),
            'pending_check_id': meta.get('pending_check_id'),
            'history_appended': bool(meta.get('history_appended')),
            'onboarding_status': meta.get('onboarding_status'),
            'created_cursor': bool(meta.get('created_cursor')),
            'pending_archived': bool(meta.get('pending_archived')),
            'mastery_updated': False}
