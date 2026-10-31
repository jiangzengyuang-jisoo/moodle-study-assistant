"""Optional, append-only learning observations; never infer learner mastery.

These checks verify record links and recorded conditions, not an AI's assessment
of the answer itself. Legacy courses need no migration or automatic rating.
"""

from datetime import datetime, timezone
import json
import re

LEVELS = {'not_found', 'explained', 'assisted', 'independent', 'delayed_pass'}
_ANSWERS = {'answer', 'user_answer', 'user_answer_or_method', 'user_attempt',
            'submitted_answer', 'response', 'response_text'}
_SOLUTIONS = {'answer_key', 'answer_keys', 'correct_answer', 'correct_answers',
              'expected_answer', 'expected_answers', 'solution', 'solutions',
              'rubric', 'marking_scheme', 'answer_check', 'grading_key'}
_ATTEMPT_COLLECTIONS = {'review_attempts', 'mistakes', 'mock_attempts', 'review_sessions', 'learning_feedback'}
_COURSE_RECORDS = {'teaching_cursor', 'assistant_notes', 'learning_history', 'practice_history'}


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
    if not isinstance(state, dict) or not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('独立表现必须指向本状态内可核对的原答 JSON Pointer')
    raw_parts = path[1:].split('/')
    if any(re.search(r'~(?![01])', part) for part in raw_parts):
        raise ValueError('原答 JSON Pointer 转义无效')
    parts = [part.replace('~1', '/').replace('~0', '~') for part in raw_parts]
    if not parts or parts[0] not in _ATTEMPT_COLLECTIONS | {'courses'}:
        raise ValueError('原答必须来自课程或学习作答记录，不能指向任意状态字段')
    if parts[0] == 'courses' and (len(parts) < 4 or parts[2] not in _COURSE_RECORDS):
        raise ValueError('课程原答必须来自教学游标或历史学习记录')
    node, owners = state, []
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
                if index == 1 and parts[0] == 'courses':
                    owners.append(node.get('id'))
                if 'course_id' in node:
                    owners.append(node['course_id'])
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError('原答路径不存在，不能登记独立表现') from error
    if not isinstance(node, dict) or not owners or any(owner != course_id for owner in owners):
        raise ValueError('独立表现的原答必须属于当前课程')
    if not _has_answer(node):
        raise ValueError('所指记录没有实际原答，不能把题目或答案键当作作答')
    return node


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
    return any(any(key in node and node[key] is not None for key in _ANSWERS) for node in _walk(record))


def _independent_conditions(record):
    """Require explicit source conditions; missing help flags are not false."""
    containers = [record]
    if isinstance(record.get('conditions'), dict):
        containers.append(record['conditions'])
    required = {'independent': True, 'assisted': False, 'answer_exposed': False}
    if any(not any(container.get(key) is value for container in containers) for key, value in required.items()):
        raise ValueError('原答需明确记录独立、无提示和未曝光答案；缺失条件不视为独立')
    for node in _walk(record, skip_answers=True):
        if any(key in node and node[key] is not value for key, value in required.items()):
            raise ValueError('独立表现与原答的帮助或曝光条件冲突')
        if node.get('answer_previously_exposed') is True or node.get('pending_question_answer_exposed') is True:
            raise ValueError('原答记录已有答案曝光，不能登记为独立表现')
        hints = node.get('hints_used')
        if hints is True or (isinstance(hints, (int, float)) and hints > 0) or (isinstance(hints, list) and hints):
            raise ValueError('原答记录已使用提示，不能登记为独立表现')
        if node.get('kind') == 'user_report':
            raise ValueError('本人自报不能替代观察到的独立作答')


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
            attempt = _resolve_attempt(state, source['record_path'], following.get('id'))
            _independent_conditions(attempt)
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
                prior = earlier.get(row['previous_event_id']) if isinstance(row['previous_event_id'], str) else None
                if (prior is None or prior.get('topic', '').strip().casefold() != row['topic'].strip().casefold()
                        or _timestamp(row['at']) <= _timestamp(prior.get('at'))):
                    raise ValueError('延迟复测需引用此前同知识点观察，且实际时间晚于前次')
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
