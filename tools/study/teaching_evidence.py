"""Optional, append-only learning observations; never infer learner mastery.

These checks verify record links and recorded conditions, not an AI's assessment
of the answer itself. Legacy courses need no migration or automatic rating.
"""

from datetime import datetime, timezone
import json
import re

from course_links import record_course_ids

LEVELS = {'not_found', 'explained', 'assisted', 'independent', 'delayed_pass'}
_ANSWERS = {'answer', 'user_answer', 'user_answer_or_method', 'user_attempt',
            'submitted_answer', 'response', 'response_text'}
_SOLUTIONS = {'answer_key', 'answer_keys', 'correct_answer', 'correct_answers',
              'expected_answer', 'expected_answers', 'solution', 'solutions',
              'rubric', 'marking_scheme', 'answer_check', 'grading_key'}
_ATTEMPT_COLLECTIONS = {'review_attempts', 'mistakes', 'mock_attempts', 'review_sessions', 'learning_feedback'}
_COURSE_RECORDS = {'teaching_cursor', 'assistant_notes', 'learning_history', 'practice_history'}
_NON_ATTEMPT_PATHS = _SOLUTIONS | _ANSWERS | {'assessment', 'feedback', 'grading', 'scoring', 'marking'}
_ANSWER_TIMES = ('answered_at', 'submitted_at', 'attempted_at', 'reviewed_at')


def _same(first, second):
    # Python equality conflates 0/False and 1/True; observations preserve types.
    return json.dumps(first, sort_keys=True, allow_nan=False) == json.dumps(second, sort_keys=True, allow_nan=False)


def _new_records(previous, following, field):
    if field not in following and field not in previous:
        return []
    old, new = previous.get(field, []), following.get(field, [])
    if not isinstance(old, list) or not isinstance(new, list):
        raise ValueError(field + ' 必须是追加式记录列表')
    if len(new) < len(old) or not _same(new[:len(old)], old):
        raise ValueError(field + ' 只能追加；纠正另加记录，不覆盖原证据')
    ids = [r.get('id') if isinstance(r, dict) else None for r in new]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
        raise ValueError(field + ' 需要唯一记录 ID')
    return new[len(old):]


def _resolve_attempt(state, path, course_id):
    return _resolve_attempt_context(state, path, course_id)[0]


def _resolve_attempt_context(state, path, course_id):
    if not isinstance(state, dict) or not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('独立表现必须指向本状态内可核对的原答 JSON Pointer')
    raw_parts = path[1:].split('/')
    if any(re.search(r'~(?![01])', part) for part in raw_parts):
        raise ValueError('原答 JSON Pointer 转义无效')
    parts = [part.replace('~1', '/').replace('~0', '~') for part in raw_parts]
    if any(part.casefold() in _NON_ATTEMPT_PATHS for part in parts):
        raise ValueError('原答路径必须指向作答记录，不能进入答案键、评分或答案内容子字段')
    if not parts or parts[0] not in _ATTEMPT_COLLECTIONS | {'courses'}:
        raise ValueError('原答必须来自课程或学习作答记录，不能指向任意状态字段')
    if parts[0] == 'courses' and (len(parts) < 4 or parts[2] not in _COURSE_RECORDS):
        raise ValueError('课程原答必须来自教学游标或历史学习记录')
    node, owners, ancestors, top_record = state, [], [], None
    try:
        for index, part in enumerate(parts):
            if isinstance(node, list):
                if not re.fullmatch(r'0|[1-9][0-9]*', part):
                    raise ValueError('原答数组索引无效')
                node = node[int(part)]
            elif isinstance(node, dict):
                node = node[part]
            else:
                raise ValueError('原答路径未指向记录')
            if isinstance(node, dict):
                if index == 1:
                    top_record = node
                # Course/cursor routing metadata is not a parent question. From
                # the first actual record onward, retain inherited conditions.
                if index >= (3 if parts[0] == 'courses' else 1):
                    ancestors.append(node)
                if index == 1 and parts[0] == 'courses':
                    owners.append(node.get('id'))
                if 'course_id' in node:
                    owners.append(node['course_id'])
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError('原答路径不存在，不能登记独立表现') from error
    if parts[0] == 'review_attempts':
        linked_owners = record_course_ids(state, top_record, collection='review_attempts')
        if course_id not in linked_owners:
            raise ValueError('复习原答课程关联缺失、冲突或不唯一，不能登记为本科独立表现')
        owners.append(course_id)
    if not isinstance(node, dict) or not owners or any(owner != course_id for owner in owners):
        raise ValueError('独立表现的原答必须属于当前课程')
    if not _has_answer(node):
        raise ValueError('所指记录没有实际原答，不能把题目或答案键当作作答')
    return node, ancestors


def _walk(value, *, skip_answers=False):
    if isinstance(value, dict):
        yield value
        for key, child in value.items():
            if key not in _SOLUTIONS and not (skip_answers and key in _ANSWERS):
                yield from _walk(child, skip_answers=skip_answers)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, skip_answers=skip_answers)


def _has_answer(record):
    # Point to the actual answering event (or a specific answered subpart), not
    # a parent container that borrows an answer from old history or grading.
    return isinstance(record, dict) and any(key in record and record[key] is not None for key in _ANSWERS)


def _condition_conflicts(nodes, *, inherited=False):
    required = {'independent': True, 'assisted': False, 'answer_exposed': False}
    for node in nodes:
        if any(key in node and node[key] is not value and not (inherited and node[key] is None)
               for key, value in required.items()):
            raise ValueError('独立表现与原答或父题的帮助或曝光条件冲突')
        if any(node.get(key) is True for key in ('answer_previously_exposed',
               'pending_question_answer_exposed', 'answer_seen_before_attempt')):
            raise ValueError('原答记录已有答案曝光，不能登记为独立表现')
        hints = node.get('hints_used')
        if hints is True or (isinstance(hints, (int, float)) and hints > 0) or (isinstance(hints, list) and hints):
            raise ValueError('原答记录已使用提示，不能登记为独立表现')
        if node.get('kind') == 'user_report':
            raise ValueError('本人自报不能替代观察到的独立作答')


def _independent_conditions(record, ancestors=(), *, native_review=False):
    """Require explicit source conditions; missing help flags are not false."""
    containers = [record]
    if isinstance(record.get('conditions'), dict):
        containers.append(record['conditions'])
    request = record.get('request')
    if (native_review and isinstance(request, dict) and record.get('fsrs_rated') is True
            and all(row.get('independent') is True and row.get('answer_seen_before_attempt') is False
                    for row in (record, request))):
        # record_attempt's existing explicit contract uses these two flags.
        # Recognize it read-only; do not backfill missing conditions or infer a
        # correct answer from a good/hard/easy FSRS rating.
        containers.append({'independent': True, 'assisted': False, 'answer_exposed': False})
    required = {'independent': True, 'assisted': False, 'answer_exposed': False}
    if any(not any(container.get(key) is value for container in containers) for key, value in required.items()):
        raise ValueError('原答需明确记录独立、无提示和未曝光答案；缺失条件不视为独立')
    _condition_conflicts(_walk(record, skip_answers=True))
    for parent in ancestors:
        # Do not inspect siblings: another subquestion can have different help.
        _condition_conflicts([parent], inherited=True)
        for key in ('conditions', 'source'):
            if isinstance(parent.get(key), dict):
                _condition_conflicts(_walk(parent[key], skip_answers=True), inherited=True)


def _topic_values(record):
    for singular, plural in (('topic_id', 'topic_ids'), ('topic', 'topics')):
        values = []
        if singular in record:
            values.append(record[singular])
        if plural in record:
            raw = record[plural]
            values.extend(raw if isinstance(raw, list) else [raw])
        if values:
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise ValueError('原答知识点范围格式无效，需核对后再登记观察')
            return singular, {value.strip().casefold() for value in values}
    return None, set()


def _bind_topic(row, attempt, ancestors):
    # The nearest explicitly scoped question owns a subpart's scope. Generic
    # course/cursor labels are excluded by the resolver, and no text is inferred.
    for record in reversed(ancestors):
        key, allowed = _topic_values(record)
        if allowed:
            observed = row.get(key)
            if not isinstance(observed, str) or observed.strip().casefold() not in allowed:
                raise ValueError('观察知识点与原答已记录范围不一致，不能借用其他知识点作答')
            return


def _attempt_timestamp(attempt, *, required=True):
    # Prefer answer/submission time to start/review time; those can legitimately
    # differ on one attempt. A generic `at` cannot override an explicit field.
    values = [attempt[key] for key in _ANSWER_TIMES if key in attempt][:1]
    # Legacy actual attempt events sometimes use `at`. It is eligible only on
    # the same record as a direct learner answer, never an ancestor or metadata.
    if not values and any(key in attempt and attempt[key] is not None for key in _ANSWERS) and 'at' in attempt:
        values = [attempt['at']]
    if not values:
        if not required:
            return None
        raise ValueError('延迟复测需原答明确的实际作答时间；不能使用创建、修改或观察登记时间代替')
    return _timestamp(values[0])


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('延迟复测需真实 ISO 日期或时间')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except ValueError as error:
        raise ValueError('延迟复测日期或时间无效') from error


def _correction(row, earlier):
    if 'corrects' in row and (not isinstance(row['corrects'], str) or row['corrects'] not in earlier):
        raise ValueError('更正必须引用此前存在的同类观察编号')


def validate_course_evidence(previous, following, state=None):
    new_records = _new_records(previous, following, 'topic_evidence')
    earlier = {row['id']: row for row in previous.get('topic_evidence', [])}
    historical_ids = set(earlier)
    for row in new_records:
        source = row.get('source')
        if (row.get('level') not in LEVELS or not isinstance(row.get('topic'), str)
                or not row['topic'].strip() or not isinstance(source, dict) or not source):
            raise ValueError('知识观察需要 topic、level 和可追溯 source；未查到不等于未学过')
        _correction(row, earlier)
        if row['level'] in {'independent', 'delayed_pass'}:
            conditions = row.get('conditions', {})
            if (source.get('kind') != 'observed_attempt'
                    or not source.get('record_path')
                    or not isinstance(conditions, dict)
                    or conditions.get('independent') is not True
                    or conditions.get('assisted') is not False
                    or conditions.get('answer_exposed') is not False):
                raise ValueError('独立表现需原答记录路径和明确无提示条件；自报讲过不能升级')
            attempt, ancestors = _resolve_attempt_context(state, source['record_path'], following.get('id'))
            _independent_conditions(attempt, ancestors, native_review=(
                source['record_path'].startswith('/review_attempts/') and ancestors[0] is attempt))
            _bind_topic(row, attempt, ancestors)
            if row['level'] == 'independent' and row.get('at') is not None:
                actual_at = _attempt_timestamp(attempt, required=False)
                if actual_at is not None and actual_at != _timestamp(row['at']):
                    raise ValueError('独立观察时间与原答实际作答时间不一致，不能给旧作答换日期')
            if row['level'] == 'delayed_pass' and (row.get('passed') is not True
                    or not row.get('previous_event_id') or not row.get('at')):
                raise ValueError('延迟复测通过需结果、先前事件和本次时间，不能仅凭讲解登记')
            if row['level'] == 'delayed_pass':
                grading = [attempt]
                if isinstance(attempt.get('assessment'), dict):
                    grading.append(attempt['assessment'])
                if any(any(result.get(key) is False for key in ('passed', 'correct', 'is_correct'))
                       or result.get('status') in {'incorrect', 'partially_answered_correct'}
                       for result in grading):
                    raise ValueError('原答已有未通过结果，不能登记延迟复测通过；需保留原答另行更正')
                if not any(any(result.get(key) is True for key in ('passed', 'correct', 'is_correct'))
                           or result.get('status') == 'correct' for result in grading):
                    raise ValueError('原答没有明确通过结果，不能仅凭观察声明延迟复测通过')
                actual_at = _attempt_timestamp(attempt)
                if actual_at != _timestamp(row['at']):
                    raise ValueError('复测观察时间必须与原答实际作答时间一致，不能给旧作答换日期')
                prior = earlier.get(row['previous_event_id']) if isinstance(row['previous_event_id'], str) else None
                if (prior is None or prior.get('topic', '').strip().casefold() != row['topic'].strip().casefold()
                        or _timestamp(row['at']) <= _timestamp(prior.get('at'))):
                    raise ValueError('延迟复测需引用此前同知识点观察，且实际时间晚于前次')
                prior_source = prior.get('source', {})
                if prior_source.get('kind') == 'observed_attempt':
                    if prior['id'] in following.get('evidence_bindings', {}):
                        from evidence_bindings import resolve_observation
                        resolved = resolve_observation(state, following['id'], prior['id'], include_record=True)
                        if resolved['status'] != 'resolved':
                            raise ValueError('先前作答尚未可靠绑定，请核对原答后再登记延迟复测')
                        prior_attempt = resolved['record']
                    else:
                        if prior['id'] in historical_ids:
                            raise ValueError('先前作答仅有未绑定旧路径，需先核对来源，不能据此登记延迟复测')
                        prior_attempt = _resolve_attempt(state, prior_source.get('record_path'), following.get('id'))
                    if prior_attempt is attempt or _same(prior_attempt, attempt):
                        raise ValueError('同一原答不能同时充当前次学习和本次延迟复测，需新的真实作答')
        earlier[row['id']] = row
    new_records = _new_records(previous, following, 'coverage_records')
    earlier = {row['id']: row for row in previous.get('coverage_records', [])}
    for row in new_records:
        if row.get('status') not in {'partial', 'checked_complete', 'needs_review'} or not row.get('scope'):
            raise ValueError('覆盖核对需明确 scope 和 status，覆盖不等于掌握')
        _correction(row, earlier)
        if row['status'] == 'checked_complete':
            sources = row.get('sources', {})
            if not isinstance(sources, dict) or any(not sources.get(k) for k in ('materials', 'assignments', 'history')):
                raise ValueError('完整覆盖结论需原资料、实际作业要求和学习历史三类核对依据；否则记 partial')
        earlier[row['id']] = row
