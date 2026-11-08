"""Conversation entry point. Collection is never equivalent to semantic review."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid

from chat_identity import ChatIdentityError, resolve_chat_id
from state_io import atomic, digest, file_lock, read, workspace_lock, timed, validate_draft, prepare_state

from snapshot_guard import file_sha, scope_details, validate_binding, validate_current_snapshot

ROOT = Path(__file__).resolve().parents[2]

# Stable marker printed by the Node collector when the one-profile-one-school-
# account guard stops a collection on purpose. This is not a network failure:
# surface the real explanation and keep learning state byte-identical.
PROFILE_ACCOUNT_GUARD = 'PROFILE_ACCOUNT_GUARD:'


class ProfileAccountGuard(ValueError):
    """The learning profile is deliberately bound to another school/account."""


def profile_account_guard(stderr):
    """Extract the deliberate guard message from a failed collector's stderr."""
    if isinstance(stderr, bytes):
        text = stderr.decode('utf-8', 'replace')
    elif isinstance(stderr, str):
        text = stderr
    else:
        return None
    for line in text.splitlines():
        if line.startswith(PROFILE_ACCOUNT_GUARD):
            detail = ' '.join(line[len(PROFILE_ACCOUNT_GUARD):].split())[:800]
            return detail or '学习档案与当前学校/账号不一致，已停止采集；未写入任何记录。'
    return None


def stamp():
    return datetime.now(timezone.utc).isoformat()


def stream_command(command, cwd, timeout, output=None):
    """Run one child process, forwarding its progress lines while it runs.

    A chapter check is network-bound: Node reports each stage on stderr, and the
    user should see that progress immediately instead of only when the command
    ends. stdout is the structured channel and is collected verbatim; it is
    never mixed with progress text. Both pipes are drained by threads so a chatty
    or hung child cannot defeat the timeout.
    """
    if output is None:
        output = sys.stderr
    process = subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout_chunks = []
    stderr_chunks = []

    def read_stderr():
        for raw in iter(process.stderr.readline, b''):
            text = raw.decode('utf-8', 'replace')
            stderr_chunks.append(text)
            output.write(text)
            output.flush()

    def read_stdout():
        stdout_chunks.append(process.stdout.read())

    stderr_reader = threading.Thread(target=read_stderr, name='chapter-check-stderr', daemon=True)
    stdout_reader = threading.Thread(target=read_stdout, name='chapter-check-stdout', daemon=True)
    stderr_reader.start()
    stdout_reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise ValueError('章节核对超时；本地资料保留，可稍后明确重查') from None
    finally:
        stderr_reader.join(timeout=5)
        stdout_reader.join(timeout=5)
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()
    return returncode, b''.join(stdout_chunks), ''.join(stderr_chunks)


class Session:
    def __init__(self, root, chat):
        if not chat or not chat.strip():
            raise ValueError('请明确提供原始用户聊天 ID；不自动使用子任务 ID 或随机 ID')
        self.root = Path(root).resolve()
        self.chat = chat
        self.state = self.root / 'Study/_System/state.json'
        self.runtime = self.state.parent / 'Runtime'
        self.key = digest(chat)
        self.record = self.runtime / 'chats' / (self.key + '.json')
        self.lock = self.record.with_suffix('.lock')

    def timing(self, stage):
        return timed(self.state, stage)

    def scope_details(self, state=None):
        return scope_details(self.root, read(self.state) if state is None else state)

    def scope(self):
        return digest(self.scope_details())

    def _validate_snapshot(self, manifest, snapshot):
        validate_binding(manifest, snapshot, self._manifest(manifest).parent)

    def _finish_snapshot(self, manifest):
        snapshot = self._manifest(manifest).with_name('snapshot.json')
        value = read(snapshot)
        manifest.update(snapshot_sha256=file_sha(snapshot), finished_at=stamp())
        try:
            self._validate_snapshot(manifest, value)
        except ValueError:
            manifest.update(phase='failed', binding_valid=False)
        else:
            manifest.update(phase='collected' if value.get('collection_complete') else 'failed', binding_valid=True)
        self._save(manifest)
        return value

    def _record_failure(self, manifest):
        # This is a learning-state write: call only from the main continuation,
        # never the read-only status/subtask path. Older runs cannot stale newer evidence.
        # A deliberate identity guard stop is not a collection failure; it must not
        # write a failed receipt or mark exam sources as needing review.
        if manifest.get('identity_guard'):
            return
        with workspace_lock(self.state):
            prepare_state(self.state)
            pointer = self.runtime / 'latest-request.json'
            if not pointer.exists() or read(pointer)['run_id'] != manifest['run_id']:
                return
            original = self.state.read_bytes()
            state = json.loads(original.decode('utf-8-sig'))
            old = state.get('refresh_receipts', {}).get(manifest['run_id'])
            if old:
                return
            if not state.get('exam_profiles'):
                return
            import exam_sources
            import review
            exam_sources.sync_profiles(state, {'collection_complete': False,
                'finished_at': manifest.get('finished_at')}, review.timestamp())
            state.setdefault('refresh_receipts', {})[manifest['run_id']] = {
                'phase': 'failed', 'sequence': manifest['sequence'],
                'snapshot_sha256': manifest.get('snapshot_sha256'),
                'revision': state.get('revision', 0) + 1}
            review.save(self.state, state, original)


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
        if manifest['phase'] in {'collecting', 'interrupted', 'failed'} and not manifest.get('snapshot_sha256'):
            # Called only while holding the conversation OS lock. Its old owner
            # is gone; no timeout/reboot heuristic and no automatic network retry.
            if snapshot.exists():
                self._finish_snapshot(manifest)
            elif manifest['phase'] == 'collecting':
                manifest['phase'] = 'interrupted'
            self._save(manifest)
        if manifest.get('snapshot_sha256') and file_sha(snapshot) != manifest['snapshot_sha256']:
            raise ValueError('本轮快照已变化，不能继续核读或复用')
        if manifest.get('content_sync', {}).get('status') == 'running':
            manifest['content_sync'] = {'status': 'partial', 'reason': 'interrupted',
                                        'automatic_retry': False}
            self._save(manifest)
        receipt = read(self.state).get('refresh_receipts', {}).get(manifest['run_id'])
        if receipt and receipt.get('phase', 'reviewed') == 'reviewed' and receipt['snapshot_sha256'] == manifest.get('snapshot_sha256'):
            manifest['phase'] = 'reviewed'
            manifest['reviewed_revision'] = receipt['revision']
            marker = self._manifest(manifest).with_name('review-status.json')
            manifest['views_pending'] = not marker.exists() or read(marker).get('phase') != 'complete'
        return manifest

    def describe(self, manifest):
        if not manifest:
            return {'phase': 'not_started'}
        scope_changed = manifest['scope_sha256'] != self.scope()
        return {**manifest, 'snapshot_path': str(self._manifest(manifest).with_name('snapshot.json')),
                'scope_changed': scope_changed,
                'school_evidence_pending': manifest['phase'] != 'reviewed' or scope_changed,
                'automatic_retry': False}

    def status(self):
        try:
            with file_lock(self.lock, timeout=0):
                return self.describe(self._load())
        except BlockingIOError:
            return {'phase': 'running', 'automatic_retry': False}

    def collect(self, snapshot_path):
        from connection_errors import safe_connection_error
        node = os.environ.get('STUDY_NODE') or shutil.which('node')
        if not node:
            raise ValueError('未找到 Node；请先运行 setup.cmd')
        result = subprocess.run([node, str(self.root / 'tools/moodle/run.mjs'), 'collect',
                                 '--output', str(snapshot_path), '--request', str(snapshot_path.with_name('request.json'))], cwd=self.root,
                                capture_output=True, timeout=300)
        if result.returncode not in (0, 2) or not snapshot_path.exists():
            guard = profile_account_guard(result.stderr)
            if guard:
                raise ProfileAccountGuard(guard)
            detail = safe_connection_error(result.stderr)
            if detail:
                raise ValueError(f'{detail[0]}: {detail[1]} {detail[2]} 旧学习记录保留；需明确重试。')
            raise ValueError('学校检查失败；请检查连接后明确重试，旧学习记录保留')

    def sync_content(self, snapshot_path):
        """A separate, resumable stage; metadata collection keeps its own limit."""
        if os.environ.get('STUDY_DEFER_CONTENT_SYNC') == '1':
            return {'status': 'not_authorized', 'reason': 'deferred_to_course_chooser', 'automatic_retry': False}
        snapshot = read(snapshot_path)
        account = snapshot.get('account_scope', {})
        state = read(self.state)
        policy = state.get('settings', {}).get('course_content_sync', {})
        selected = {i for course in state.get('courses', []) for i in course.get('moodle_course_ids', [])}
        captured = {page.get('courseid') for page in snapshot.get('pages', [])}
        grants = policy.get('grants', []) if policy.get('version') == 1 else []
        # This is only a no-network fast path. The Node core rechecks exact
        # account/site consent and current selection before every download.
        permitted = any(isinstance(grant, dict) and grant.get('mode') == 'full'
                        and grant.get('user_id') == account.get('user_id')
                        and grant.get('moodle_course_id') in selected & captured
                        for grant in grants) if isinstance(grants, list) else False
        library_policy = state.get('settings', {}).get('course_library_authorization')
        library_mode = 'course_library_authorization' in state.get('settings', {})
        if library_mode:
            library_grants = library_policy.get('courses', {}) if isinstance(library_policy, dict) and library_policy.get('schema') == 1 else {}
            permitted = isinstance(library_grants, dict) and any(
                isinstance(library_grants.get(course.get('id')), dict)
                and library_grants[course['id']].get('mode') == 'full'
                and library_grants[course['id']].get('moodle_courseid') in set(course.get('moodle_course_ids', [])) & captured
                for course in state.get('courses', []))
        if not permitted:
            return {'status': 'not_authorized', 'automatic_retry': False}
        node = os.environ.get('STUDY_NODE') or shutil.which('node')
        if not node:
            raise ValueError('未找到 Node')
        report_folder = 'LibrarySync' if library_mode else 'CourseContent'
        print(json.dumps({'stage': 'course_content_sync',
                          'message': f'正在下载、归档课程资料并建立索引，首次可能需要较长时间。结果保存在 Study/_System/{report_folder}/latest.json；失败的资料可在明确刷新时增量补齐。'},
                         ensure_ascii=False), flush=True)
        result = subprocess.run([node, str(self.root / 'tools/moodle/run.mjs'), 'content-sync',
                                 '--snapshot', str(snapshot_path)], cwd=self.root,
                                capture_output=True, timeout=7200)
        if result.returncode not in (0, 2):
            raise ValueError('课程资料同步未完成')
        try:
            output = json.loads(result.stdout.decode('utf-8-sig').strip())
        except (ValueError, UnicodeError):
            raise ValueError('课程资料同步未返回有效结果') from None
        if output.get('status') not in {'complete', 'partial', 'not_authorized'}:
            raise ValueError('课程资料同步状态无效')
        return {'status': output['status'], 'counts': output.get('counts', {}),
                'report_path': str(self.state.parent / report_folder / 'latest.json'), 'automatic_retry': False}

    def start(self, explicit=False, subtask=False, collector=None, content_collector=None):
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
                if manifest['phase'] in {'failed', 'interrupted'}:
                    self._record_failure(manifest)
                if manifest.get('views_pending'):
                    self._repair_views()
                    atomic(self._manifest(manifest).with_name('review-status.json'), {'phase': 'complete'})
                    manifest['views_pending'] = False
                return {**self.describe(manifest), 'network_started': False}
            self.maintain()
            with workspace_lock(self.state):
                prepare_state(self.state)
                pointer = self.runtime / 'latest-request.json'
                sequence = (read(pointer)['sequence'] if pointer.exists() else 0) + 1
                state = read(self.state)
                details = self.scope_details(state)
                manifest = {'run_id': uuid.uuid4().hex, 'sequence': sequence,
                            'scope_sha256': digest(details), 'phase': 'collecting', 'started_at': stamp()}
                request_path = self._manifest(manifest).with_name('request.json')
                # Quiz collection also consults already tracked task URLs. Freeze only
                # that necessary metadata; learning answers/history are not copied.
                task_urls = sorted({t.get('source', {}).get('url') for t in state.get('tasks', [])
                                    if isinstance(t.get('source'), dict)
                                    and isinstance(t['source'].get('url'), str)})
                collect_state = {'tasks': [{'source': {'url': url}} for url in task_urls]}
                atomic(request_path, {'protocol': 1, 'run_id': manifest['run_id'], 'scope': details,
                                      'collect_state': collect_state})
                manifest['request_sha256'] = file_sha(request_path)
                self._save(manifest)
                atomic(pointer, {'sequence': sequence, 'run_id': manifest['run_id']})
            snapshot = self._manifest(manifest).with_name('snapshot.json')
            try:
                with self.timing('moodle_collection'):
                    (collector or self.collect)(snapshot)
                value = self._finish_snapshot(manifest)
            except ProfileAccountGuard:
                # Not a network/collection failure: keep the runtime manifest in a
                # failed state but never write a failed planning receipt or mark
                # exam sources stale. The caller surfaces the guard explanation.
                manifest['phase'] = 'failed'
                manifest['identity_guard'] = True
                self._save(manifest)
                raise
            except Exception:
                # A child may atomically finish writing after the parent exits. A later
                # continuation checks failed/interrupted runs again without fetching.
                if snapshot.exists():
                    self._finish_snapshot(manifest)
                else:
                    manifest['phase'] = 'failed'
                    self._save(manifest)
                if manifest['phase'] == 'failed':
                    self._record_failure(manifest)
                raise
            if manifest['phase'] == 'failed':
                self._record_failure(manifest)
            self._save(manifest)
            with workspace_lock(self.state):
                if manifest.get('binding_valid') and read(self.runtime / 'latest-request.json')['run_id'] == manifest['run_id']:
                    atomic(self.state.parent / 'refresh-latest.json', value)
                    if value.get('collection_complete'):
                        atomic(self.state.parent / 'refresh-last-complete.json', value)
            if manifest.get('binding_valid'):
                manifest['content_sync'] = {'status': 'running', 'started_at': stamp()}
                self._save(manifest)
                try:
                    with self.timing('course_content_sync'):
                        manifest['content_sync'] = (content_collector or self.sync_content)(snapshot)
                except (ValueError, OSError, subprocess.TimeoutExpired):
                    # Download/index failure never erases the collected snapshot
                    # or falsely promotes it to a reviewed learning record.
                    manifest['content_sync'] = {'status': 'partial', 'reason': 'download_or_index_failed',
                                                'automatic_retry': False,
                                                'next': '资料未全部保存；明确刷新 Moodle 可按已有文件增量重试。'}
                self._save(manifest)
            return {**self.describe(manifest), 'network_started': True}
        finally:
            lock.__exit__(None, None, None)

    def context(self, course=None):
        with self.timing('local_context'):
            from learning_library.context import build_context
            state = read(self.state)
            from product_tour import tour_status
            return {'context': build_context(state, course), 'state_sha256': digest(state),
                    'read_only': True, 'product_onboarding': tour_status(state)}

    def acknowledge(self, snapshot_hash, proposal):
        """AI supplies a fully reviewed draft and the hash of its base state.

        The caller must actually inspect evidence. This gate only verifies lineage,
        completeness and conflicts; it cannot prove the quality of an AI review.
        """
        with file_lock(self.lock), workspace_lock(self.state), self.timing('review_commit'):
            prepare_state(self.state)
            manifest = self._load()
            if not manifest or manifest.get('snapshot_sha256') != snapshot_hash:
                raise ValueError('核读回执必须对应本轮快照哈希')
            snapshot = read(self._manifest(manifest).with_name('snapshot.json'))
            validate_current_snapshot(self.state, snapshot, allow_legacy=False)
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
            receipt = {'phase': 'reviewed', 'request_sha256': request_sha, 'snapshot_sha256': snapshot_hash, 'scope_sha256': manifest['scope_sha256'],
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

    def teaching_event(self, payload, operation_id=None):
        """Merge one small teaching-position event through the shared save path.

        The caller supplies only this turn's change; ``daily.execute`` reads the
        latest state under the shared workspace lock, merges, validates the full
        draft, saves atomically once and rebuilds views at most once. A payload
        chat ID must match this chat, because the chat identity belongs to the
        entry point, not to the model. Without an explicit operation ID the same
        content derives the same ID, so a retry is a duplicate instead of a
        second event.
        """
        if not isinstance(payload, dict):
            raise ValueError('teaching-event 需要标准输入 JSON 事件对象')
        provided = payload.get('chat_id')
        if provided is not None and provided != self.chat:
            raise ValueError('事件里的聊天 ID 与入口身份不一致；请由当前聊天直接提交，不要手填聊天 ID')
        body = dict(payload)
        body['chat_id'] = self.chat
        content = {key: value for key, value in body.items() if key != 'event_id'}
        effective = operation_id or ('teach-' + digest({'chat': self.chat, 'payload': content}))
        import daily
        import review
        with self.timing('teaching_event'):
            result = daily.execute(self.state, 'teaching-event', review.timestamp(),
                                   payload=body, operation_id=effective)
        if result.get('duplicate'):
            # The stored receipt describes the original recorded save; expose a
            # single retry status so the caller can tell "already saved" apart.
            result['status'] = 'duplicate'
        return result

    def maintain(self):
        with self.timing('archive_maintenance'):
            from learning_library.versions import cleanup
            return cleanup(self.state)

    def chapter_check(self, course, section=None, mode='strict', modules=None, force=False):
        """Narrow, read-only verification of one chapter's already local files.

        The receipt is bound to the original chat key so the same chat reuses a
        recent check without touching the network, and ``force`` asks for a real
        re-check after the user explicitly requested one. A chapter check never
        writes learning state: strict still compares file content hashes.
        """
        if not course:
            raise ValueError('请提供课程 ID；章节核对只查这一门课')
        if not section and not modules:
            raise ValueError('请提供章节名称/编号，或用 --module 指定模块')
        if mode not in {'quick', 'strict'}:
            raise ValueError('核对强度只能是 quick 或 strict')
        node = os.environ.get('STUDY_NODE') or shutil.which('node')
        if not node:
            raise ValueError('未找到 Node；请先运行 setup.cmd')
        command = [node, str(self.root / 'tools/moodle/run.mjs'), 'chapter-check',
                   '--course', str(course), '--mode', mode,
                   '--chat-key', self.key]
        if section:
            command += ['--section', str(section)]
        if modules:
            command += ['--module', str(modules)]
        if force:
            command.append('--force')
        print('正在核对本章资料范围；同聊天且本地文件未变时复用回执，不联网…',
              file=sys.stderr, flush=True)
        with self.timing('chapter_check'):
            returncode, stdout, stderr_text = stream_command(command, self.root, timeout=900)
        if returncode != 0 or not stdout.strip():
            from connection_errors import safe_connection_error
            detail = safe_connection_error(stderr_text)
            if detail:
                raise ValueError(f'{detail[0]}: {detail[1]} {detail[2]} 本地资料保留')
            lines = [line for line in stderr_text.splitlines() if line.strip()]
            raise ValueError(lines[-1] if lines else '章节核对失败；本地资料保留')
        try:
            receipt = json.loads(stdout.decode('utf-8-sig').strip())
        except (ValueError, UnicodeError):
            raise ValueError('章节核对未返回有效结果') from None
        # A chapter check never touches learning state; it only writes its receipt.
        receipt['learning_state_unchanged'] = True
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--chat', help='原始用户聊天 ID；缺省时按稳定会话标识解析（见 docs/SESSION.md）')
    parser.add_argument('--subtask', action='store_true')
    parser.add_argument('command', choices=['continue', 'today', 'refresh', 'status', 'review', 'maintain',
                                            'chapter-check', 'teaching-event', 'operation-id', 'chat-id'])
    parser.add_argument('--course')
    parser.add_argument('--section')
    parser.add_argument('--mode', choices=['quick', 'strict'])
    parser.add_argument('--force', action='store_true',
                        help='chapter-check 明确重查；忽略同聊天回执缓存')
    parser.add_argument('--modules')
    parser.add_argument('--snapshot-hash')
    parser.add_argument('--operation-id',
                        help='teaching-event 等学习事件重试时沿用原编号；省略则按事件内容派生')
    args = parser.parse_args()
    try:
        chat, chat_source = resolve_chat_id(args.chat)
    except ChatIdentityError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
    if args.subtask and chat_source != 'argument':
        # Delegated tasks must carry the original chat ID explicitly. Using the
        # internally-derived session ID would start a second first collection.
        print('内部任务必须显式传入原始聊天 ID 并加 --subtask；没有使用自动推断的会话标识。', file=sys.stderr)
        raise SystemExit(2)
    session = Session(args.root, chat)
    if args.subtask and args.command not in {'continue', 'status'}:
        raise ValueError('内部任务只允许读取接续和状态；写入交给原始聊天')
    if args.command == 'chat-id':
        output = {'chat_id': chat, 'chat_source': chat_source}
    elif args.command == 'status':
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
    elif args.command == 'teaching-event':
        # One small learning event: only this turn's change, merged and saved by
        # the shared pipeline (workspace lock, byte-CAS, receipts, one view build).
        try:
            payload = json.load(sys.stdin)
        except ValueError:
            raise ValueError('teaching-event 需要标准输入的有效 JSON 事件') from None
        output = session.teaching_event(payload, args.operation_id)
    elif args.command == 'chapter-check':
        output = session.chapter_check(args.course, args.section, args.mode or 'strict',
                                       args.modules, force=args.force)
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
            output['school_evidence_pending'] = output['school_check'].get('school_evidence_pending', True)
    print(json.dumps(output, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
