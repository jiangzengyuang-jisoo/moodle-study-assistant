"""Synthetic conversation, crash and concurrency acceptance tests (no school API)."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import daily
import review
from session import ProfileAccountGuard, Session
from session_test_fixtures import write_snapshot
from state_io import atomic, digest, read, workspace_lock

HERE = Path(__file__).resolve().parent


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='同学 test ', dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.session = Session(self.root, 'user-chat')
        self.path = self.session.state
        atomic(self.path, {'revision': 1, 'courses': [], 'mistakes': [], 'settings': {},
                           'teaching_evidence': {'keep': ['original page 4']}})
        self.calls = 0

    def collect(self, path):
        self.calls += 1
        write_snapshot(path, {'collection_complete': True, 'reviewed': False, 'pages': [],
                      'threads': [], 'errors': [], 'finished_at': '2026-10-30T00:00:00Z'})

    def proposal(self):
        state = read(self.path)
        return {'reviewed': True, 'base_sha256': digest(state), 'state': state}

    def test_same_chat_reopen_and_subtask_do_not_refresh(self):
        self.assertFalse(self.session.start(subtask=True, collector=self.collect)['network_started'])
        self.assertEqual(self.calls, 0)
        self.session.start(collector=self.collect)
        reopened = Session(self.root, 'user-chat')
        for _ in range(3):
            result = reopened.start(collector=self.collect)
            self.assertEqual(result['phase'], 'collected')
            self.assertFalse(result['network_started'])
        self.assertEqual(self.calls, 1)
        self.session.start(explicit=True, collector=self.collect)
        self.assertEqual(self.calls, 2)
        Session(self.root, 'new-user-chat').start(collector=self.collect)
        self.assertEqual(self.calls, 3)

    def test_fresh_state_read_only_and_evidence_preserved(self):
        before = self.path.read_bytes()
        self.session.context()
        self.assertEqual(before, self.path.read_bytes())
        value = read(self.path); value['revision'] = 5; atomic(self.path, value)
        self.assertEqual(self.session.context()['state_sha256'], digest(value))

    def test_review_rejects_partial_draft_or_missing_learning_records(self):
        state = read(self.path)
        state['mistakes'] = [{'id': 'KEEP', 'source': {'material_id': 'OLD', 'pages': [4]}}]
        atomic(self.path, state)
        result = self.session.start(collector=self.collect)
        proposal = self.proposal(); proposal['state']['mistakes'] = []
        with self.assertRaisesRegex(ValueError, '遗漏已有学习记录'):
            self.session.acknowledge(result['snapshot_sha256'], proposal)
        proposal = self.proposal(); proposal['state'].pop('teaching_evidence')
        with self.assertRaisesRegex(ValueError, '遗漏已有字段'):
            self.session.acknowledge(result['snapshot_sha256'], proposal)
        self.assertEqual(read(self.path), state)

    def test_scope_changes_require_explicit_refresh(self):
        result = self.session.start(collector=self.collect)
        state = read(self.path); state['courses'] = [{'id': 'C', 'moodle_course_ids': [3]}]
        atomic(self.path, state)
        self.assertTrue(self.session.start(collector=self.collect)['scope_changed'])
        with self.assertRaisesRegex(ValueError, '范围'):
            self.session.acknowledge(result['snapshot_sha256'], self.proposal())
        self.assertEqual(self.calls, 1)

    def test_failed_collection_does_not_retry_or_claim_review(self):
        def incomplete(path): write_snapshot(path, {'collection_complete': False, 'errors': ['offline']})
        result = self.session.start(collector=incomplete)
        self.assertEqual(result['phase'], 'failed')
        self.session.start(collector=self.collect)
        self.assertEqual(self.calls, 0)
        with self.assertRaises(ValueError):
            self.session.acknowledge(result['snapshot_sha256'], self.proposal())

    def test_immutable_snapshot_and_base_conflicts(self):
        result = self.session.start(collector=self.collect)
        proposal = self.proposal()
        value = read(self.path); value['new_feedback'] = 'keep'; atomic(self.path, value)
        with self.assertRaisesRegex(ValueError, '状态已变化'):
            self.session.acknowledge(result['snapshot_sha256'], proposal)
        atomic(Path(result['snapshot_path']), {'collection_complete': True, 'changed': True})
        with self.assertRaisesRegex(ValueError, '快照已变化'):
            self.session.status()
        self.assertEqual(read(self.path)['new_feedback'], 'keep')
        repaired = self.session.start(explicit=True, collector=self.collect)
        self.assertNotEqual(repaired['run_id'], result['run_id'])
        self.assertEqual(repaired['phase'], 'collected')

    def test_late_old_run_cannot_overwrite_newer_run(self):
        other = Session(self.root, 'new-chat')
        def delayed(path):
            other.start(collector=lambda p: write_snapshot(p, {'collection_complete': True, 'source': 'new'}))
            write_snapshot(path, {'collection_complete': True, 'source': 'old'})
        old = self.session.start(collector=delayed)
        with self.assertRaisesRegex(ValueError, '较新的检查'):
            self.session.acknowledge(old['snapshot_sha256'], self.proposal())
        self.assertNotEqual(old['run_id'], read(self.session.runtime / 'latest-request.json')['run_id'])
        self.assertEqual(read(self.path.parent / 'refresh-latest.json')['source'], 'new')

    def test_actual_process_killed_during_collection_is_interrupted(self):
        script = "from session import Session; import sys,time; " \
                 "s=Session(sys.argv[1], 'killed-chat'); " \
                 "s.start(collector=lambda p: (print('fetching',flush=True),time.sleep(60)))"
        child = subprocess.Popen([sys.executable, '-c', script, str(self.root)], cwd=HERE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline().strip(), b'fetching')
            child.kill(); child.communicate(timeout=10)
            resumed = Session(self.root, 'killed-chat')
            self.assertEqual(resumed.start(collector=self.collect)['phase'], 'interrupted')
            self.assertEqual(self.calls, 0)
            self.assertEqual(resumed.start(explicit=True, collector=self.collect)['phase'], 'collected')
        finally:
            if child.poll() is None: child.kill(); child.communicate()

    def test_collect_crash_recovers_saved_snapshot_without_fetch(self):
        result = self.session.start(collector=self.collect)
        manifest = Path(result['snapshot_path']).with_name('manifest.json')
        value = read(manifest); value['phase'] = 'collecting'; value.pop('snapshot_sha256')
        atomic(manifest, value)
        self.assertEqual(self.session.start(collector=self.collect)['phase'], 'collected')
        self.assertEqual(self.calls, 1)
        Path(result['snapshot_path']).unlink(); value['phase'] = 'collecting'; atomic(manifest, value)
        self.assertEqual(self.session.start(collector=self.collect)['phase'], 'interrupted')
        self.assertEqual(self.calls, 1)

    def test_review_commit_crash_repairs_views_and_preserves_evidence(self):
        result = self.session.start(collector=self.collect)
        proposal = self.proposal()
        with patch.object(self.session, '_repair_views', side_effect=OSError('simulated crash')):
            with self.assertRaises(OSError):
                self.session.acknowledge(result['snapshot_sha256'], proposal)
        self.assertTrue(self.session.status()['views_pending'])
        recovered = self.session.start(collector=self.collect)
        self.assertFalse(recovered['views_pending'])
        self.assertEqual(self.calls, 1)
        self.assertEqual(read(self.path)['teaching_evidence'], {'keep': ['original page 4']})
        self.session.acknowledge(result['snapshot_sha256'], proposal)
        changed = copy.deepcopy(proposal); changed['state']['changed'] = True
        with self.assertRaisesRegex(ValueError, '重试内容冲突'):
            self.session.acknowledge(result['snapshot_sha256'], changed)

    def feedback(self, identifier):
        return {'feedback_id': identifier, 'completed': 'PRIVATE_ANSWER',
                'recorded_at': '2026-10-07T15:06:00+08:00', 'evidence': 'user report'}

    def test_commit_before_view_failure_is_not_repeated(self):
        now = review.timestamp('2026-10-07T15:06:00+08:00')
        payload = self.feedback('event')
        with patch.object(daily, 'write_views', side_effect=OSError('crash')):
            with self.assertRaises(OSError): daily.execute(self.path, 'feedback', now, payload=payload)
        self.assertEqual(len(read(self.path)['learning_feedback']), 1)
        result = daily.execute(self.path, 'feedback', now, payload=payload)
        self.assertTrue(result['duplicate'])
        self.assertEqual(len(read(self.path)['learning_feedback']), 1)
        self.assertTrue(daily.execute(self.path, 'check', now)['fresh'])
        with self.assertRaisesRegex(ValueError, '内容冲突'):
            daily.execute(self.path, 'feedback', now, payload={**payload, 'completed': 'different'})

    def test_two_process_feedback_writers_both_survive(self):
        children = []
        for index in range(2):
            child = subprocess.Popen([sys.executable, str(HERE / 'daily.py'), '--state', str(self.path),
                                      'feedback'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
            child.stdin.write(json.dumps(self.feedback(str(index))).encode()); child.stdin.close()
            child.stdin = None
            children.append(child)
        for child in children:
            out, error = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, error.decode())
        self.assertEqual(len(read(self.path)['learning_feedback']), 2)

    def test_os_lock_released_on_process_termination(self):
        script = "from state_io import workspace_lock; from pathlib import Path; import sys,time; " \
                 "lock=workspace_lock(Path(sys.argv[1])); lock.__enter__(); print('held',flush=True); time.sleep(60)"
        child = subprocess.Popen([sys.executable, '-c', script, str(self.path)], cwd=HERE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline().strip(), b'held')
            child.kill(); child.communicate(timeout=10)
            with workspace_lock(self.path): pass
        finally:
            if child.poll() is None: child.kill(); child.communicate()

    def test_missing_identity_refused_and_telemetry_excludes_content(self):
        with self.assertRaises(ValueError): Session(self.root, '')
        self.session.context()
        text = (self.session.runtime / 'timings.jsonl').read_text()
        for secret in ['user-chat', 'original page', 'PRIVATE_ANSWER']:
            self.assertNotIn(secret, text)
        self.assertEqual(set(json.loads(text.splitlines()[0])), {'stage', 'elapsed_ms', 'ok', 'at'})


class ChapterCheckTests(unittest.TestCase):
    """A chapter check is a narrow read: no state write, no full collection."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='章节 test ', dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.session = Session(Path(self.tmp.name), 'user-chat')

    def test_arguments_are_checked_before_any_network_call(self):
        with patch('session.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, '请提供课程'):
                self.session.chapter_check(None)
            with self.assertRaisesRegex(ValueError, '章节名称'):
                self.session.chapter_check('moodle-1')
            with self.assertRaisesRegex(ValueError, 'quick 或 strict'):
                self.session.chapter_check('moodle-1', '8', 'slow')
            run.assert_not_called()

    def test_receipt_is_returned_and_state_is_not_written(self):
        atomic(self.session.state, {'revision': 1, 'courses': []})
        before = self.session.state.read_bytes()
        payload = {'verdict': 'unchanged', 'summary': {'files': 1, 'unchanged': 1},
                   'receipt_path': 'Study/_System/ChapterChecks/moodle-1/8.json'}
        completed = subprocess.CompletedProcess(args=[], returncode=0,
                                                stdout=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                                stderr=b'')
        with patch('session.subprocess.run', return_value=completed) as run, \
                patch.dict(os.environ, {'STUDY_NODE': 'node'}):
            result = self.session.chapter_check('moodle-1', '8', 'strict')
        self.assertTrue(result['learning_state_unchanged'])
        self.assertEqual(result['verdict'], 'unchanged')
        command = run.call_args[0][0]
        self.assertIn('chapter-check', command)
        self.assertEqual(command[command.index('--section') + 1], '8')
        self.assertEqual(command[command.index('--mode') + 1], 'strict')
        self.assertEqual(before, self.session.state.read_bytes())

    def test_node_failure_surfaces_without_touching_state(self):
        atomic(self.session.state, {'revision': 1, 'courses': []})
        before = self.session.state.read_bytes()
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout=b'',
                                                stderr='没有匹配到章节“99”'.encode('utf-8'))
        with patch('session.subprocess.run', return_value=completed), \
                patch.dict(os.environ, {'STUDY_NODE': 'node'}):
            with self.assertRaisesRegex(ValueError, '没有匹配到章节'):
                self.session.chapter_check('moodle-1', '99')
        self.assertEqual(before, self.session.state.read_bytes())


class ProfileAccountGuardTests(unittest.TestCase):
    """A deliberate one-profile-one-school-account stop is not a collection failure."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='身份 test ', dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.session = Session(Path(self.tmp.name), 'user-chat')

    def bind_other_school(self):
        atomic(self.session.state, {
            'revision': 3, 'courses': [{'id': 'moodle-8', 'moodle_course_ids': [8]}],
            'settings': {'profile_account_scope': {'site': 'https://school.example', 'user_id': 17}},
            'exam_profiles': [{'id': 'E'}], 'refresh_receipts': {}})
        return self.session.state.read_bytes()

    def test_guard_reason_is_surfaced_and_state_stays_byte_identical(self):
        before = self.bind_other_school()
        message = ('同一学习档案此前用于另一所学校（https://old.example），当前学校是 '
                   'https://school.example；已停止采集，未写入任何记录。')
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout=b'',
            stderr=('PROFILE_ACCOUNT_GUARD:' + message).encode('utf-8'))
        with patch('session.subprocess.run', return_value=completed), \
                patch.dict(os.environ, {'STUDY_NODE': 'node'}):
            with self.assertRaises(ProfileAccountGuard) as caught:
                self.session.start()
        # The user sees the real explanation, not a generic connection error.
        self.assertIn('另一所学校', str(caught.exception))
        self.assertEqual(before, self.session.state.read_bytes())
        self.assertFalse(read(self.session.state).get('refresh_receipts'))
        # A later continuation must not turn the deliberate stop into a failure
        # receipt or exam-source staleness; it reports the stopped run, not a fetch.
        resumed = self.session.start()
        self.assertFalse(resumed['network_started'])
        self.assertEqual(before, self.session.state.read_bytes())
        self.assertFalse(read(self.session.state).get('refresh_receipts'))

    def test_guard_message_is_bounded_and_optional(self):
        from session import profile_account_guard
        self.assertIsNone(profile_account_guard(b''))
        self.assertIsNone(profile_account_guard('学校检查失败'))
        self.assertEqual(profile_account_guard(b'PROFILE_ACCOUNT_GUARD:\xe5\x81\x9c\xe6\xad\xa2'),
                         '停止')
        long = 'X' * 2000
        self.assertEqual(len(profile_account_guard(f'PROFILE_ACCOUNT_GUARD:{long}')), 800)


if __name__ == '__main__': unittest.main()
