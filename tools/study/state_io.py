"""Shared OS locks and dependency-free state bridge for Python and Node writers.

Order: conversation lock -> workspace lock -> material/library lock. Never hold
the workspace lock over network or AI review. OS releases locks after a crash.
"""
from contextlib import contextmanager
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


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def validate_draft(current, draft):
    """Reject accidentally partial replacements; deletion needs a separate workflow."""
    if not isinstance(draft, dict) or set(current) - set(draft):
        raise ValueError('必须提交完整状态，不能遗漏已有字段')
    for name in ('courses', 'materials', 'mistakes', 'review_queue', 'review_attempts',
                 'learning_feedback', 'mock_attempts'):
        previous, following = current.get(name, []), draft.get(name, [])
        if not isinstance(previous, list):
            continue  # Legacy formats are left to their existing migration tools.
        if not isinstance(following, list):
            raise ValueError('不能替换已有学习记录集合：' + name)
        def identity(row):
            return next((str(row[k]) for k in ('id', 'attempt_id', 'feedback_id') if row.get(k)), None)
        old_ids = {identity(row) for row in previous if isinstance(row, dict)} - {None}
        new_ids = {identity(row) for row in following if isinstance(row, dict)} - {None}
        if old_ids - new_ids:
            raise ValueError('完整草稿遗漏已有学习记录：' + name)


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
    if mode == 'init':
        if not path.exists():
            atomic(path, payload)
        return
    if mode != 'replace':
        raise ValueError('Unknown state operation')
    original = path.read_bytes()
    if json.loads(original.decode('utf-8-sig')) != payload['expected']:
        raise ValueError('学习状态已更新，请重读后重试；未覆盖任何记录')
    validate_draft(payload['expected'], payload['state'])
    for key in ('operation_receipts', 'refresh_receipts'):
        if payload['state'].get(key) != payload['expected'].get(key):
            raise ValueError('操作回执由工具维护，不能直接修改')
    state = payload['state']
    state['revision'] = payload['expected'].get('revision', 0) + 1
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    with timed(path, 'state_save'):
        atomic(path.with_name('state.previous.json'), payload['expected'])
        atomic(path, state)


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        bridge(Path(sys.argv[1]), sys.argv[2], json.load(sys.stdin))
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
