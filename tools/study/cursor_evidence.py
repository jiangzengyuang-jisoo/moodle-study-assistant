"""Shared current-question aliases and lossless handoff of real learner evidence.

The current question is a movable cursor. Once an attempt exists, replacing it
must leave the complete old record in this same cursor's durable ``checks`` list.
No arbitrary-field or cross-course search can satisfy that requirement.
"""

import json

PENDING_KEYS = ('pending_question', 'pending_check', 'pending_questions', 'pending_checks')
_ANSWER_KEYS = {'answer', 'user_answer', 'user_answer_or_method', 'user_attempt',
                'submitted_answer', 'response', 'response_text'}
_ATTEMPT_MARKERS = {'answered_at', 'submitted_at', 'attempted_at'}
_ASSESSMENT_KEYS = {'assessment', 'feedback', 'fsrs_rating'}
_ATTEMPT_STATUSES = {'answered', 'submitted', 'graded', 'correct', 'incorrect',
                     'partially_answered', 'partially_answered_correct', 'completed'}
_LIVE_ROUTING = {'status', 'stage', 'next_step', 'updated_at'}
_SOLUTION_KEYS = {'answer_key', 'answer_keys', 'correct_answer', 'correct_answers',
                  'expected_answer', 'expected_answers', 'solution', 'solutions',
                  'rubric', 'marking_scheme', 'answer_check', 'grading_key'}


def _has_attempt(value, include_status=True):
    if isinstance(value, dict):
        # Presence with a non-null value matters: 0, False and an explicit blank
        # response are observations, not evidence of "no attempt".
        if any(key in value and value[key] is not None for key in _ANSWER_KEYS | _ATTEMPT_MARKERS | _ASSESSMENT_KEYS):
            return True
        if include_status and isinstance(value.get('status'), str) and value['status'] in _ATTEMPT_STATUSES:
            return True
        return any(_has_attempt(item, include_status) for key, item in value.items()
                   if key not in _SOLUTION_KEYS)
    if isinstance(value, list):
        return any(_has_attempt(item, include_status) for item in value)
    return False


def _pending(cursor):
    """Read aliases exactly where the context projector accepts them, minus history."""
    if isinstance(cursor, dict):
        for key, value in cursor.items():
            if key in {'checks', 'history', 'review_backlog'}:
                continue
            if key in PENDING_KEYS:
                if value is not None:
                    yield from value if isinstance(value, list) else (value,)
            else:
                yield from _pending(value)
    elif isinstance(cursor, list):
        for item in cursor:
            yield from _pending(item)


def _preserves(previous, following, *, live=False, top=True):
    """Preserve known values and collection contents; allow additive annotation."""
    if previous is None:
        return True  # Filling an explicitly unknown value is legitimate.
    if isinstance(previous, dict):
        if not isinstance(following, dict):
            return False
        routing = _LIVE_ROUTING if live and top and _has_attempt(previous, include_status=False) else set()
        return all(key in following and _preserves(value, following[key], live=live, top=False)
                   for key, value in previous.items()
                   if key not in routing)
    if isinstance(previous, list):
        if not isinstance(following, list):
            return False
        # A record may gain additional subparts/options, but cannot lose or edit
        # any original part, including a partially answered nested subquestion.
        remaining = list(following)
        for item in previous:
            match = next((i for i, value in enumerate(remaining)
                          if _preserves(item, value, live=live, top=False)), None)
            if match is None:
                return False
            remaining.pop(match)
        return True
    # Python equates 0 and False: learner responses must preserve their type too.
    return type(previous) is type(following) and previous == following


def validate_cursor_transition(previous, following):
    """Keep active attempts or archive them before clearing/changing any alias."""
    if not isinstance(previous, dict):
        return
    old_attempts = [row for row in _pending(previous) if _has_attempt(row)]
    if not old_attempts:
        return
    if not isinstance(following, dict):
        raise ValueError('当前题已有实际作答，换题前请完整保存在同课程 teaching_cursor.checks')
    active = list(_pending(following))
    history = following.get('checks', [])
    history = list(history) if isinstance(history, list) else []
    for old in old_attempts:
        # Changes to a live status are fine if the original answer, question,
        # source, assessment and conditions remain. An archival copy is stricter.
        match = next((i for i, row in enumerate(active) if _preserves(old, row, live=True)), None)
        if match is not None:
            active.pop(match)
            continue
        match = next((i for i, row in enumerate(history) if _preserves(old, row)), None)
        if match is not None:
            history.pop(match)
            continue
        raise ValueError('当前题的作答或题源将丢失；请先完整追加到同课程 teaching_cursor.checks 再换题')


def active_snapshot(cursor):
    """Archive routing once, without recursively copying durable histories."""
    return {key: value for key, value in cursor.items() if key not in {'checks', 'history'}}


def validate_cursor_history(previous, following):
    """A chapter switch retains the previous position; history is never active."""
    if not isinstance(following, dict):
        return False
    history = following.get('history', [])
    old_history = previous.get('history', [])
    same = lambda a, b: json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)
    switched = any(previous.get(key) not in (None, '') and previous.get(key) != following.get(key)
                   for key in ('topic', 'chapter'))
    if not isinstance(history, list) or not isinstance(old_history, list):
        if not switched and same(history, old_history):
            return False  # Preserve an unchanged legacy shape, never migrate it.
        raise ValueError('teaching_cursor.history 必须是追加式列表')
    if len(history) < len(old_history) or not same(history[:len(old_history)], old_history):
        raise ValueError('旧教学位置只能追加，不能删除或改写')
    snapshot = active_snapshot(previous)
    archived = any(type(row) is dict and same(row, snapshot) for row in history[len(old_history):])
    if switched and not archived:
        raise ValueError('切换教学主题前，请把旧游标（除 checks/history）完整追加到 teaching_cursor.history；旧章待复习另存 review_backlog')
    return archived
