"""Shared OS locks and dependency-free state bridge for Python and Node writers.

Order: conversation lock -> workspace lock -> material/library lock. Never hold
the workspace lock over network or AI review. OS releases locks after a crash.
"""
from contextlib import contextmanager
from collections import Counter
from functools import wraps
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

from cursor_evidence import PENDING_KEYS, validate_cursor_transition, validate_cursor_history, legacy_history_migration
from teaching_evidence import validate_course_evidence

_held = threading.local()


@contextmanager
def timed(state_path, stage):
    start, ok = time.perf_counter(), False
    try:
        yield
        ok = True
    finally:
        runtime = Path(state_path).parent / 'Runtime'
        with file_lock(runtime / '.timing.lock'):
            with (runtime / 'timings.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'stage': stage, 'elapsed_ms': round((time.perf_counter()-start)*1000, 3),
                                         'ok': ok, 'at': datetime.now(timezone.utc).isoformat()}) + '\n')


@contextmanager
def file_lock(path, timeout=10):
    path = Path(path).resolve()
    held = getattr(_held, 'paths', None)
    if held is None:
        held = _held.paths = set()
    if path in held:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise BlockingIOError('工作区正在处理另一项操作，请稍后重试')
                time.sleep(0.03)
        held.add(path)
        try:
            yield
        finally:
            held.remove(path)
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def workspace_lock(state_path):
    return file_lock(Path(state_path).resolve().parent / '.workspace.lock')


def locked(function):
    @wraps(function)
    def wrapped(state_path, *args, **kwargs):
        with workspace_lock(state_path):
            return function(state_path, *args, **kwargs)
    return wrapped


def digest(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def same_json(first, second):
    """JSON observations preserve types: Python equates 0/False and 1/True."""
    return digest(first) == digest(second)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


@locked
def prepare_state(path):
    """Resolve file/state transactions before another supported writer reads state.

    No journal means no library imports or maintenance work. Recovery failure
    stops the caller; it must not advance the journal's state hash underneath it.
    """
    path = Path(path)
    if path.with_name('material-version-transaction.json').exists():
        from learning_library.versions import recover
        recover(path)


def _identity(row):
    if not isinstance(row, dict):
        return None
    return next(((key, str(row[key])) for key in ('id', 'attempt_id', 'feedback_id', 'session_id')
                 if row.get(key)), None)


_EVIDENCE_FIELDS = {'fsrs', 'source', 'sources', 'original_question', 'user_answer',
                    'user_answer_or_method', 'user_attempt', 'answer', 'evidence'}
_ANSWER_FIELDS = {'user_answer', 'user_answer_or_method', 'user_attempt', 'answer',
                  'submitted_answer', 'response', 'response_text'}
_IMMUTABLE_COLLECTIONS = {'review_attempts', 'learning_feedback', 'mock_attempts'}
_COURSE_UPDATES = {'moodle_course_ids', 'assignment_focus', 'current_topic', 'orientation'}
_CURSOR_ROUTING = {'pages', 'slides', 'material_id', 'task_id', 'work_unit_id', 'next_step',
                   'pending_question_answer_exposed'}


def _source_enrichment(previous, following):
    """An original source may gain verified pages/details, not lose old evidence."""
    if previous in (None, '', [], {}):
        return True
    if isinstance(previous, dict):
        return isinstance(following, dict) and all(
            key in following and _source_enrichment(value, following[key]) for key, value in previous.items())
    if isinstance(previous, list):
        if not isinstance(following, list):
            return False
        remaining = list(following)
        for value in previous:
            match = next((i for i, item in enumerate(remaining) if _source_enrichment(value, item)), None)
            if match is None:
                return False
            remaining.pop(match)
        return True
    return same_json(previous, following)


def _preserve_details(previous, following, location):
    """Ordinary drafts may enrich records, never silently truncate evidence."""
    if isinstance(previous, dict):
        cursor_root = location.endswith('.teaching_cursor')
        archived = False
        if cursor_root:
            validate_cursor_transition(previous, following)
            archived = validate_cursor_history(previous, following)
        routing = set(_CURSOR_ROUTING) if cursor_root else set()
        if archived:
            routing.update(set(previous) - {'checks', 'history'})
        # The root transition check covers nested handoffs too. Archived checks
        # remain ordinary protected records and never receive this exemption.
        if '.teaching_cursor' in location and '.checks' not in location:
            routing.update(PENDING_KEYS)
        if (not isinstance(following, dict) or (previous and not following and not cursor_root)
                or set(previous) - set(following) - routing):
            raise ValueError('完整草稿遗漏已有学习详情：' + location)
        for key, value in previous.items():
            if key in routing:
                continue  # Attempt preservation was checked at the cursor root.
            if cursor_root and key == 'history' and legacy_history_migration(previous, following):
                # The old dictionary and active snapshot were verified exactly.
                # No other dict-to-list conversion gets this exemption.
                continue
            # School selection/focus legitimately changes without erasing progress.
            if location.startswith('courses[') and location.endswith(']') and key in _COURSE_UPDATES:
                continue
            if key in {'source', 'sources'}:
                if not _source_enrichment(value, following[key]):
                    raise ValueError('既有来源证据不能删除或改写：' + location + '.' + key)
                continue
            protected = (key in _ANSWER_FIELDS and value is not None) or (
                key in _EVIDENCE_FIELDS and value not in (None, '', [], {}))
            if protected and not same_json(following[key], value):
                raise ValueError('既有学习证据不可由完整草稿改写，请用专用工具或追加更正：' + location + '.' + key)
            _preserve_details(value, following[key], location + '.' + key)
    elif isinstance(previous, list):
        if not isinstance(following, list):
            raise ValueError('不能清空已有学习详情：' + location)
        if location.endswith('.teaching_cursor.checks'):
            # A question may produce several real attempts with the same id.
            # This is an append-only event history, not a unique-question table.
            if (len(following) < len(previous)
                    or any(digest(old) != digest(new) for old, new in zip(previous, following))):
                raise ValueError('教学作答历史只能追加，不能改写或删除已有记录：' + location)
            return
        old_ids = [_identity(row) for row in previous]
        if previous and all(old_ids):
            new_ids = [_identity(row) for row in following]
            if any(x is None for x in new_ids) or len(set(new_ids)) != len(new_ids):
                raise ValueError('学习记录编号缺失或重复：' + location)
            indexed = dict(zip(new_ids, following))
            for identity, row in zip(old_ids, previous):
                if identity not in indexed:
                    raise ValueError('完整草稿遗漏已有学习记录：' + location)
                new = indexed[identity]
                if location in _IMMUTABLE_COLLECTIONS and not same_json(new, row):
                    raise ValueError('历史学习事件不能由完整草稿改写：' + location)
                _preserve_details(row, new, location + '[' + identity[1] + ']')
        else:
            # Unidentified historical entries cannot safely be matched by position.
            old_items, new_items = Counter(map(digest, previous)), Counter(map(digest, following))
            if old_items - new_items:
                raise ValueError('完整草稿遗漏或改写无编号的学习详情：' + location)
    elif previous is not None and previous != '' and (following is None or following == ''):
        raise ValueError('不能清空已有学习详情：' + location)
    elif previous is not None and isinstance(following, (dict, list)):
        raise ValueError('不能改变已有学习详情的类型：' + location)


def validate_draft(current, draft):
    """Reject accidentally partial replacements; deletion needs a separate workflow."""
    if not isinstance(draft, dict) or set(current) - set(draft):
        raise ValueError('必须提交完整状态，不能遗漏已有字段')
    for name in ('courses', 'materials', 'mistakes', 'review_queue', 'review_attempts',
                 'learning_feedback', 'mock_attempts', 'review_sessions', 'mock_exams',
                 'tasks', 'deadlines', 'discussion_notes'):
        previous, following = current.get(name, []), draft.get(name, [])
        if not isinstance(previous, list):
            continue  # Legacy formats are left to their existing migration tools.
        if not isinstance(following, list):
            raise ValueError('不能替换已有学习记录集合：' + name)
        _preserve_details(previous, following, name)
        if name == 'courses':
            old_courses = {row.get('id'): row for row in previous if isinstance(row, dict)}
            for course in following:
                if isinstance(course, dict):
                    validate_course_evidence(old_courses.get(course.get('id'), {}), course, state=draft)
        if name == 'materials':
            indexed = {_identity(row): row for row in following if isinstance(row, dict)}
            for row in previous:
                if isinstance(row, dict) and row.get('sha256') and indexed[_identity(row)].get('sha256') != row['sha256']:
                    raise ValueError('资料版本哈希不可改写，请使用资料版本工具')
    from evidence_bindings import bind_source_changes
    bind_source_changes(current, draft)


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@locked
def bridge(path, mode, payload):
    path = Path(path)
    prepare_state(path)
    if mode == 'init':
        if not path.exists():
            atomic(path, payload)
        return
    if mode != 'replace':
        raise ValueError('Unknown state operation')
    original = path.read_bytes()
    if not same_json(json.loads(original.decode('utf-8-sig')), payload['expected']):
        raise ValueError('学习状态已更新，请重读后重试；未覆盖任何记录')
    if not same_json(payload['state'].get('exam_profiles', []), payload['expected'].get('exam_profiles', [])):
        raise ValueError('考试要求必须通过绑定本轮快照的 daily.py exam-profile/exam-sync 或会话核读入口更新')
    validate_draft(payload['expected'], payload['state'])
    for key in ('operation_receipts', 'refresh_receipts'):
        if not same_json(payload['state'].get(key), payload['expected'].get(key)):
            raise ValueError('操作回执由工具维护，不能直接修改')
    state = payload['state']
    state['revision'] = payload['expected'].get('revision', 0) + 1
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    with timed(path, 'state_save'):
        atomic(path.with_name('state.previous.json'), payload['expected'])
        atomic(path, state)


if __name__ == '__main__':
    # Lazy recovery imports state_io through versions; share the same reentrant
    # lock registry when this module was launched by the Node bridge as a script.
    sys.modules.setdefault('state_io', sys.modules[__name__])
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        bridge(Path(sys.argv[1]), sys.argv[2], json.load(sys.stdin))
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
