"""Conversation entry point. Collection is never equivalent to semantic review."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from state_io import atomic, digest, file_lock, read, workspace_lock, timed, validate_draft

ROOT = Path(__file__).resolve().parents[2]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Session:
    def __init__(self, root, chat):
        if not chat or not chat.strip():
            raise ValueError('请明确提供原始用户聊天 ID；不自动使用子任务 ID 或随机 ID')
        self.root = Path(root).resolve()
        self.state = self.root / 'Study/_System/state.json'
        self.runtime = self.state.parent / 'Runtime'
        self.key = digest(chat)
        self.record = self.runtime / 'chats' / (self.key + '.json')
        self.lock = self.record.with_suffix('.lock')

    def timing(self, stage):
        return timed(self.state, stage)

    def scope(self):
        state = read(self.state)
        config = self.root / 'config.local.json'
        # Runtime code fingerprints the encrypted file; it never decrypts/logs it.
        credential = self.root / '.private/moodle-token.dpapi'
        return digest({'site': read(config).get('moodle_url') if config.exists() else None,
                       'credential_generation': file_sha(credential) if credential.exists() else None,
                       'course_ids': sorted({i for c in state.get('courses', [])
                                             for i in c.get('moodle_course_ids', [])})})

    def _manifest(self, record):
        run = record['run_id']
        if len(run) != 32 or any(c not in '0123456789abcdef' for c in run):
            raise ValueError('Invalid run ID')
        return self.runtime / 'runs' / run / 'manifest.json'

    def _save(self, manifest):
        atomic(self._manifest(manifest), manifest)
        atomic(self.record, {'run_id': manifest['run_id']})

    def _load(self):
        if not self.record.exists():
            return None
        manifest = read(self._manifest(read(self.record)))
        snapshot = self._manifest(manifest).with_name('snapshot.json')
        if manifest['phase'] == 'collecting':
            # Called only while holding the conversation OS lock. Its old owner
            # is gone; no timeout/reboot heuristic and no automatic network retry.
            if snapshot.exists():
                value = read(snapshot)
                manifest.update(phase='collected' if value.get('collection_complete') else 'failed',
                                snapshot_sha256=file_sha(snapshot))
            else:
                manifest['phase'] = 'interrupted'
            self._save(manifest)
        if manifest.get('snapshot_sha256') and file_sha(snapshot) != manifest['snapshot_sha256']:
            raise ValueError('本轮快照已变化，不能继续核读或复用')
        receipt = read(self.state).get('refresh_receipts', {}).get(manifest['run_id'])
        if receipt and receipt['snapshot_sha256'] == manifest.get('snapshot_sha256'):
            manifest['phase'] = 'reviewed'
            manifest['reviewed_revision'] = receipt['revision']
            marker = self._manifest(manifest).with_name('review-status.json')
            manifest['views_pending'] = not marker.exists() or read(marker).get('phase') != 'complete'
        return manifest

    def describe(self, manifest):
        if not manifest:
            return {'phase': 'not_started'}
        return {**manifest, 'snapshot_path': str(self._manifest(manifest).with_name('snapshot.json')),
                'scope_changed': manifest['scope_sha256'] != self.scope(),
                'automatic_retry': False}

    def status(self):
        try:
            with file_lock(self.lock, timeout=0):
                return self.describe(self._load())
        except BlockingIOError:
            return {'phase': 'running', 'automatic_retry': False}

    def collect(self, snapshot_path):
        node = os.environ.get('STUDY_NODE') or shutil.which('node')
        if not node:
            raise ValueError('未找到 Node；请先运行 setup.cmd')
        result = subprocess.run([node, str(self.root / 'tools/moodle/run.mjs'), 'collect',
                                 '--output', str(snapshot_path)], cwd=self.root,
                                capture_output=True, timeout=300)
        if result.returncode not in (0, 2) or not snapshot_path.exists():
            raise ValueError('学校检查失败；请检查连接后明确重试，旧学习记录保留')

    def start(self, explicit=False, subtask=False, collector=None):
        if subtask:
            return {**self.status(), 'subtask': True, 'network_started': False}
        lock = file_lock(self.lock, timeout=0)
        try:
            lock.__enter__()
        except BlockingIOError:
            return {'phase': 'running', 'network_started': False}
        try:
            manifest = None if explicit else self._load()
            if manifest and not explicit:
                if manifest.get('views_pending'):
                    self._repair_views()
                    atomic(self._manifest(manifest).with_name('review-status.json'), {'phase': 'complete'})
                    manifest['views_pending'] = False
                return {**self.describe(manifest), 'network_started': False}
            self.maintain()
            with workspace_lock(self.state):
                pointer = self.runtime / 'latest-request.json'
                sequence = (read(pointer)['sequence'] if pointer.exists() else 0) + 1
                manifest = {'run_id': uuid.uuid4().hex, 'sequence': sequence,
                            'scope_sha256': self.scope(), 'phase': 'collecting', 'started_at': stamp()}
                self._save(manifest)
                atomic(pointer, {'sequence': sequence, 'run_id': manifest['run_id']})
            snapshot = self._manifest(manifest).with_name('snapshot.json')
            try:
                with self.timing('moodle_collection'):
                    (collector or self.collect)(snapshot)
                value = read(snapshot)
                manifest.update(snapshot_sha256=file_sha(snapshot), finished_at=stamp(),
                                phase='collected' if value.get('collection_complete') else 'failed')
            except Exception:
                # A fully written snapshot remains recoverable without another fetch.
                if snapshot.exists():
                    value = read(snapshot)
                    manifest.update(snapshot_sha256=file_sha(snapshot),
                                    phase='collected' if value.get('collection_complete') else 'failed')
                else:
                    manifest['phase'] = 'failed'
                self._save(manifest)
                raise
            self._save(manifest)
            with workspace_lock(self.state):
                if read(self.runtime / 'latest-request.json')['run_id'] == manifest['run_id']:
                    atomic(self.state.parent / 'refresh-latest.json', value)
                    if value.get('collection_complete'):
                        atomic(self.state.parent / 'refresh-last-complete.json', value)
            return {**self.describe(manifest), 'network_started': True}
        finally:
            lock.__exit__(None, None, None)

    def context(self, course=None):
        with self.timing('local_context'):
            from learning_library.context import build_context
            state = read(self.state)
            return {'context': build_context(state, course), 'state_sha256': digest(state),
                    'read_only': True}

    def acknowledge(self, snapshot_hash, proposal):
        """AI supplies a fully reviewed draft and the hash of its base state.

        The caller must actually inspect evidence. This gate only verifies lineage,
        completeness and conflicts; it cannot prove the quality of an AI review.
        """
        with file_lock(self.lock), workspace_lock(self.state), self.timing('review_commit'):
            manifest = self._load()
            if not manifest or manifest.get('snapshot_sha256') != snapshot_hash:
                raise ValueError('核读回执必须对应本轮快照哈希')
            if manifest['phase'] == 'reviewed':
                receipt = read(self.state)['refresh_receipts'][manifest['run_id']]
                if receipt['request_sha256'] != digest(proposal):
                    raise ValueError('本轮已提交，重试内容冲突')
                self._repair_views()
                atomic(self._manifest(manifest).with_name('review-status.json'), {'phase': 'complete'})
                manifest['views_pending'] = False
                return self.describe(manifest)
            if manifest['phase'] != 'collected' or proposal.get('reviewed') is not True:
                raise ValueError('采集不完整或尚未逐项核读，不能标记已检查')
            if manifest['scope_sha256'] != self.scope():
                raise ValueError('账户或课程范围已变化，请明确重新检查')
            if read(self.runtime / 'latest-request.json')['run_id'] != manifest['run_id']:
                raise ValueError('已有较新的检查；旧轮次不能覆盖新来源')
            original = self.state.read_bytes()
            current = json.loads(original.decode('utf-8-sig'))
            if digest(current) != proposal.get('base_sha256'):
                raise ValueError('学习状态已变化，请基于最新状态重新合并核读结果')
            request_sha = digest(proposal)
            import copy
            draft = copy.deepcopy(proposal['state'])
            if not isinstance(draft, dict):
                raise ValueError('核读结果必须是完整状态对象')
            validate_draft(current, draft)
            # Receipts are tool-owned, never trusted from an AI-provided draft.
            for key in ('operation_receipts', 'refresh_receipts', 'revision'):
                if key in current:
                    draft[key] = current[key]
                else:
                    draft.pop(key, None)
            import exam_sources
            import review
            snapshot = read(self._manifest(manifest).with_name('snapshot.json'))
            exam_sources.sync_profiles(draft, snapshot, review.timestamp())
            receipt = {'request_sha256': request_sha, 'snapshot_sha256': snapshot_hash, 'scope_sha256': manifest['scope_sha256'],
                       'sequence': manifest['sequence'], 'reviewed_at': stamp(),
                       'revision': current.get('revision', 0) + 1}
            draft.setdefault('refresh_receipts', {})[manifest['run_id']] = receipt
            review.save(self.state, draft, original)
            # State receipt is authoritative even if a crash happens on this line.
            self._repair_views()
            atomic(self._manifest(manifest).with_name('review-status.json'), {'phase': 'complete'})
            manifest.update(phase='reviewed', views_pending=False, reviewed_revision=receipt['revision'])
            self._save(manifest)
            return self.describe(manifest)

    def _repair_views(self):
        import daily
        import review
        daily.execute(self.state, 'render', review.timestamp())

    def maintain(self):
        with self.timing('archive_maintenance'):
            from learning_library.versions import cleanup
            return cleanup(self.state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--chat', required=True, help='原始用户聊天 ID；内部任务继承此 ID')
    parser.add_argument('--subtask', action='store_true')
    parser.add_argument('command', choices=['continue', 'today', 'refresh', 'status', 'review', 'maintain', 'operation-id'])
    parser.add_argument('--course')
    parser.add_argument('--snapshot-hash')
    args = parser.parse_args()
    session = Session(args.root, args.chat)
    if args.subtask and args.command not in {'continue', 'status'}:
        raise ValueError('内部任务只允许读取接续和状态；写入交给原始聊天')
    if args.command == 'status':
        output = session.status()
    elif args.command == 'operation-id':
        # Allocate and persist before submitting any learning event.
        operation = uuid.uuid4().hex
        atomic(session.runtime / 'operations' / (digest(operation) + '.json'), {'phase': 'prepared'})
        output = {'operation_id': operation}
    elif args.command == 'maintain':
        output = session.maintain()
    elif args.command == 'review':
        output = session.acknowledge(args.snapshot_hash, json.load(sys.stdin))
    else:
        # Emit local context before waiting on school networking. No school claim yet.
        output = session.context(args.course)
        print(json.dumps({'stage': 'local_ready', **output}, ensure_ascii=False), flush=True)
        output['school_check'] = session.start(explicit=args.command == 'refresh', subtask=args.subtask)
        if args.command == 'today':
            import daily
            import review
            with session.timing('today_views'):
                output['today'] = daily.execute(session.state, 'run', review.timestamp(), course=args.course)
            output['school_evidence_pending'] = output['school_check']['phase'] != 'reviewed'
    print(json.dumps(output, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
