"""Hold a chapter OS lock until the requesting Node process closes its pipe.

The persistent marker excludes older clients that used existence-only locks.
An old empty marker has no trustworthy owner identity: leave it for explicit
inspection instead of guessing from its age or deleting a possibly live lock.
"""
import json
from pathlib import Path
import sys

from state_io import file_lock


def emit(status):
    print(json.dumps({'status': status}), flush=True)


def main():
    path = Path(sys.argv[1])
    guard = path.with_name(path.name + '.os')
    if any(p.resolve() != p.absolute() for p in (path, guard)):
        raise ValueError('Unsafe chapter lock path')
    try:
        # This file is never unlinked: all contenders lock the same OS object.
        with file_lock(guard, timeout=0):
            if path.exists():
                if not path.is_dir():
                    emit('legacy_lock_needs_review')
                    return
            else:
                try:
                    # mkdir atomically distinguishes this protocol from legacy
                    # files, with no partially written marker after a crash.
                    # Older clients' exclusive file creation also fails here.
                    path.mkdir()
                except FileExistsError:
                    # An older client may have started after our existence check.
                    emit('legacy_lock_needs_review')
                    return
            emit('acquired')
            # Dedicated pipe, no stdin sharing. Parent crash closes its writer;
            # helper crash releases the OS lock without any PID-based recovery.
            sys.stdin.buffer.read()
    except BlockingIOError:
        emit('busy')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Do not expose local paths or subprocess error payloads.
        emit('lock_failed')
        sys.exit(1)
