"""Regression cases found by the local phase-one audit; all sources are synthetic."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import exam_prep
import review
from session import Session
from session_test_fixtures import write_snapshot
from state_io import atomic, digest, read
from test_exam_sources import registered

HERE = Path(__file__).resolve().parent


class SessionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='会话 recovery ', dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.session = Session(self.root, 'root-chat')
        self.initial, self.snapshot = registered()
        self.initial.update(revision=1, settings={})
        self.initial['courses'][0]['moodle_course_ids'] = [1]
        self.initial['exam_profiles'][0]['sections'] = [{'id': 'S', 'question_type': 'multiple_choice',
            'count': 1, 'total_marks': 1, 'marks_per_question': 1, 'option_count': 4}]
        atomic(self.session.state, self.initial)

    def proposal(self):
        state = read(self.session.state)
        return {'state': state, 'base_sha256': digest(state), 'reviewed': True}

    def collect(self, path):
        write_snapshot(path, self.snapshot)

    def reviewed(self, session=None):
        session = session or self.session
        run = session.start(explicit=True, collector=self.collect)
        session.acknowledge(run['snapshot_sha256'], self.proposal())
        return run

    def test_changed_scope_today_is_pending_without_refresh(self):
        self.reviewed()
        state = read(self.session.state)
        state['courses'][0]['moodle_course_ids'] = [2]
        atomic(self.session.state, state)
        result = subprocess.run([sys.executable, str(HERE / 'session.py'), '--root', str(self.root),
                                 '--chat', 'root-chat', 'today'], capture_output=True, check=True)
        output = json.loads(result.stdout.splitlines()[-1])
        self.assertTrue(output['school_evidence_pending'])
        self.assertTrue(output['school_check']['scope_changed'])
        self.assertFalse(output['school_check']['network_started'])

    def test_unchanged_reviewed_scope_is_not_pending(self):
        self.reviewed()
        with patch.object(self.session, 'collect', side_effect=AssertionError('must not fetch')):
            output = self.session.start()
        self.assertFalse(output['school_evidence_pending'])
        self.assertFalse(output['network_started'])

    def test_request_freezes_only_task_urls_without_changing_school_scope(self):
        state = read(self.session.state)
        state['tasks'] = [{'id': 'T', 'source': {'url': 'https://school.example/mod/quiz/view.php?id=21'},
                           'answer': 'PRIVATE_LEARNING_ANSWER'}]
        atomic(self.session.state, state)
        before_scope = self.session.scope()
        result = self.session.start(explicit=True, collector=self.collect)
        request = read(Path(result['snapshot_path']).with_name('request.json'))
        self.assertEqual(request['collect_state']['tasks'], [{'source': {
            'url': 'https://school.example/mod/quiz/view.php?id=21'}}])
        self.assertNotIn('PRIVATE_LEARNING_ANSWER', json.dumps(request))
        state['tasks'][0]['source']['url'] = 'https://school.example/mod/quiz/view.php?id=22'
        atomic(self.session.state, state)
        self.assertEqual(before_scope, self.session.scope())

    def test_aba_selection_cannot_accept_wrong_course_snapshot(self):
        def collector(path):
            state = read(self.session.state)
            changed = copy.deepcopy(state); changed['courses'][0]['moodle_course_ids'] = [2]
            atomic(self.session.state, changed)
            wrong = copy.deepcopy(self.snapshot); wrong['pages'][0]['courseid'] = 2
            write_snapshot(path, wrong)
            atomic(self.session.state, state)
        result = self.session.start(explicit=True, collector=collector)
        self.assertFalse(result['scope_changed'])
        self.assertEqual(result['phase'], 'failed')
        self.assertFalse(result['binding_valid'])
        with self.assertRaisesRegex(ValueError, '范围'):
            self.session.acknowledge(result['snapshot_sha256'], self.proposal())

    def test_unbound_or_modified_request_cannot_be_reviewed(self):
        result = self.session.start(explicit=True, collector=lambda path: atomic(path, self.snapshot))
        self.assertFalse(result['binding_valid'])
        with self.assertRaisesRegex(ValueError, '绑定'):
            self.session.acknowledge(result['snapshot_sha256'], self.proposal())
        result = self.session.start(explicit=True, collector=self.collect)
        request_path = Path(result['snapshot_path']).with_name('request.json')
        request = read(request_path); request['scope']['course_ids'] = [2]; atomic(request_path, request)
        with self.assertRaisesRegex(ValueError, '请求'):
            self.session.acknowledge(result['snapshot_sha256'], self.proposal())

    def test_complete_collection_requires_all_requested_pages(self):
        value = copy.deepcopy(self.snapshot); value['pages'] = []
        result = self.session.start(explicit=True, collector=lambda path: write_snapshot(path, value))
        self.assertFalse(result['binding_valid'])
        self.assertEqual(result['phase'], 'failed')

    def test_partial_failure_preserves_evidence_and_blocks_exam_once(self):
        sources = copy.deepcopy(self.initial['exam_profiles'][0]['sources'])
        result = self.session.start(explicit=True, collector=lambda path: write_snapshot(path,
            {'collection_complete': False, 'pages': [], 'errors': ['offline']}))
        self.assertEqual(result['phase'], 'failed')
        self.assertTrue(result['binding_valid'])
        state = read(self.session.state)
        self.assertEqual(state['exam_profiles'][0]['sources'], sources)
        self.assertEqual(state['exam_profiles'][0]['verification_status'], 'needs_review')
        with self.assertRaisesRegex(ValueError, '重新核读'):
            exam_prep._profile(state, {'exam_id': 'E', 'mode': 'full_mock'})
        before = self.session.state.read_bytes()
        with patch.object(self.session, 'collect', side_effect=AssertionError('no retry')):
            self.session.start()
        self.assertEqual(before, self.session.state.read_bytes())

    def test_transport_exception_also_blocks_exam_without_dropping_evidence(self):
        def fail(_): raise OSError('synthetic offline')
        with self.assertRaises(OSError): self.session.start(explicit=True, collector=fail)
        after = read(self.session.state)
        self.assertEqual(after['exam_profiles'][0]['verification_status'], 'needs_review')
        self.assertEqual(after['exam_profiles'][0]['sources'], self.initial['exam_profiles'][0]['sources'])
        self.assertEqual(self.session.status()['phase'], 'failed')

    def test_failure_save_crash_is_recovered_once(self):
        real_save = review.save
        def committed_then_crash(*args, **kwargs):
            real_save(*args, **kwargs)
            raise OSError('response lost after commit')
        with patch.object(review, 'save', committed_then_crash):
            with self.assertRaises(OSError):
                self.session.start(explicit=True, collector=lambda path: write_snapshot(path, {'collection_complete': False}))
        before = self.session.state.read_bytes()
        self.session.start()
        self.assertEqual(before, self.session.state.read_bytes())
        self.assertEqual(read(self.session.state)['exam_profiles'][0]['verification_status'], 'needs_review')

    def test_old_failure_does_not_invalidate_newer_review(self):
        other = Session(self.root, 'newer-chat')
        def slow_failure(path):
            self.reviewed(other)
            write_snapshot(path, {'collection_complete': False})
        self.session.start(explicit=True, collector=slow_failure)
        self.assertEqual(read(self.session.state)['exam_profiles'][0]['verification_status'], 'verified')

    def test_readonly_subtask_does_not_write_failure_to_learning_state(self):
        result = self.session.start(explicit=True, collector=self.collect)
        path = Path(result['snapshot_path']); path.unlink()
        manifest = path.with_name('manifest.json'); value = read(manifest)
        value.pop('snapshot_sha256'); value['phase'] = 'collecting'; atomic(manifest, value)
        before = self.session.state.read_bytes()
        self.assertEqual(self.session.start(subtask=True)['phase'], 'interrupted')
        self.assertEqual(before, self.session.state.read_bytes())

    def test_child_finishing_after_parent_termination_is_recovered_without_fetch(self):
        # Child writes atomically only after the root explicitly releases it.
        child_code = """import sys,time
from pathlib import Path
from session_test_fixtures import write_snapshot
p=Path(sys.argv[1]); release=Path(sys.argv[2])
while not release.exists(): time.sleep(.01)
write_snapshot(p, {'collection_complete':True,'threads':[],'errors':[]})
"""
        parent_code = """import sys,time,subprocess
from pathlib import Path
from session import Session
root=Path(sys.argv[1]); code=sys.argv[2]
def collect(path):
 child=subprocess.Popen([sys.executable,'-c',code,str(path),str(root/'release')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 (root/'child-pid').write_text(str(child.pid))
 print('child-started',flush=True)
 time.sleep(60)
Session(root,'root-chat').start(explicit=True, collector=collect)
"""
        env = os.environ.copy(); env['PYTHONPATH'] = str(HERE)
        parent = subprocess.Popen([sys.executable, '-c', parent_code, str(self.root), child_code],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(parent.stdout.readline().strip(), b'child-started')
            parent.kill(); parent.communicate(timeout=10)
            with patch.object(self.session, 'collect', side_effect=AssertionError('must not fetch')):
                self.assertEqual(self.session.start()['phase'], 'interrupted')
                (self.root / 'release').touch()
                snapshot = self.session._manifest(read(self.session.record)).with_name('snapshot.json')
                deadline = time.monotonic() + 10
                while not snapshot.exists() and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(snapshot.exists())
                recovered = self.session.start()
                self.assertEqual(recovered['phase'], 'collected')
                self.assertTrue(recovered['binding_valid'])
                self.assertFalse(recovered['network_started'])
        finally:
            (self.root / 'release').touch()
            if parent.poll() is None: parent.kill(); parent.communicate(timeout=10)


if __name__ == '__main__': unittest.main()
