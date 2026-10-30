"""Shared source-write gate for session review and daily exam commands.

Call while holding the workspace lock, before reading/modifying derived state.
Legacy standalone archives remain usable only until managed refresh runs exist.
"""
import hashlib
import json
from pathlib import Path
import re

from state_io import digest, read


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scope_details(root, state):
    root = Path(root)
    config = root / 'config.local.json'
    credential = root / '.private/moodle-token.dpapi'
    courses = [{'id': c['id'], 'moodle_course_ids': sorted(c.get('moodle_course_ids', []))}
               for c in state.get('courses', [])]
    return {'site': read(config).get('moodle_url') if config.exists() else None,
            'credential_generation': file_sha(credential) if credential.exists() else None,
            'courses': courses,
            'course_ids': sorted({i for c in courses for i in c['moodle_course_ids']})}


def run_directory(runtime, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ValueError('快照轮次编号无效，请明确重新检查')
    folder = Path(runtime).resolve() / 'runs' / run_id
    if folder.resolve() != folder:
        raise ValueError('快照轮次路径不能使用外部链接')
    return folder


def validate_binding(manifest, snapshot, folder):
    """Validate an immutable request and its actual collected course coverage."""
    folder = Path(folder)
    request_path = folder / 'request.json'
    if not request_path.exists() or file_sha(request_path) != manifest.get('request_sha256'):
        raise ValueError('采集请求缺失或变化，请明确重新检查')
    request = read(request_path)
    if (request.get('protocol') != 1 or request.get('run_id') != manifest.get('run_id')
            or digest(request.get('scope')) != manifest.get('scope_sha256')):
        raise ValueError('采集请求范围与轮次清单不一致')
    if not isinstance(snapshot, dict) or snapshot.get('collection_binding') != {
            'protocol': 1, 'run_id': manifest['run_id'], 'request_sha256': manifest['request_sha256']}:
        raise ValueError('快照未绑定本轮实际采集请求，请明确重新检查')
    expected = {i: c['id'] for c in request['scope']['courses'] for i in c['moodle_course_ids']}
    pages = snapshot.get('pages', [])
    if not isinstance(pages, list) or any(not isinstance(p, dict) for p in pages):
        raise ValueError('快照课程范围结构无效')
    actual = [p.get('courseid') for p in pages]
    if (any(p.get('course_id') != expected.get(p.get('courseid')) or p.get('courseid') not in expected
            for p in pages) or len(set(actual)) != len(actual)):
        raise ValueError('快照课程范围与采集请求不一致')
    if snapshot.get('collection_complete') is True and set(actual) != set(expected):
        raise ValueError('完整采集快照没有覆盖请求的全部课程')


def validate_current_snapshot(state_path, snapshot, state=None, *, allow_legacy=True):
    """Reject stale/foreign/modified sources before any state, view or receipt write.

    Supplied dictionaries must exactly match the sealed on-disk snapshot. A
    copied compatibility snapshot is acceptable only when it has identical data.
    The latest *started* run supersedes earlier ones, including on failure.
    """
    path = Path(state_path).resolve()
    state = read(path) if state is None else state
    runtime = path.parent / 'Runtime'
    pointer_path = runtime / 'latest-request.json'
    binding = snapshot.get('collection_binding') if isinstance(snapshot, dict) else None
    managed = (pointer_path.exists() or (runtime / 'runs').exists()
               or bool(state.get('refresh_receipts')) or binding is not None)
    if not managed and allow_legacy:
        return None
    if not pointer_path.exists():
        raise ValueError('缺少当前刷新轮次，不能用旧快照写入；请明确重新检查')
    pointer = read(pointer_path)
    if not isinstance(binding, dict):
        raise ValueError('受管理工作区的来源写入必须使用本轮绑定快照')
    run_id = binding.get('run_id')
    folder = run_directory(runtime, run_id)
    if run_id != pointer.get('run_id'):
        raise ValueError('已有较新的检查；旧轮次不能覆盖新来源')
    manifest_path = folder / 'manifest.json'
    snapshot_path = folder / 'snapshot.json'
    if not manifest_path.is_file() or not snapshot_path.is_file():
        raise ValueError('本轮快照或清单缺失，不能登记来源')
    manifest = read(manifest_path)
    if manifest.get('run_id') != run_id or manifest.get('sequence') != pointer.get('sequence'):
        raise ValueError('本轮来源序号不一致，请重新检查')
    if manifest.get('phase') not in {'collected', 'reviewed', 'failed'}:
        raise ValueError('本轮采集尚未完成接续，不能写入来源')
    data = snapshot_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest.get('snapshot_sha256'):
        raise ValueError('本轮快照已变化，不能继续核读或复用')
    stored = json.loads(data.decode('utf-8-sig'))
    if digest(stored) != digest(snapshot):
        raise ValueError('传入快照与本轮已封存内容不一致')
    if manifest.get('scope_sha256') != digest(scope_details(path.parent.parent.parent, state)):
        raise ValueError('账户或课程范围已变化，请明确重新检查')
    validate_binding(manifest, stored, folder)
    if manifest.get('phase') == 'failed' and stored.get('collection_complete') is True:
        raise ValueError('失败轮次不能登记完整来源，请明确重新检查')
    return manifest
