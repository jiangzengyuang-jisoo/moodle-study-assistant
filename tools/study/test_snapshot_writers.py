"""All supported exam/source writers share the same managed snapshot boundary."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import daily
import exam_prep
import exam_sources
from session import Session
from session_test_fixtures import write_snapshot
from state_io import atomic, bridge, digest, read
from test_exam_sources import registered, profile, NOW

HERE = Path(__file__).resolve().parent


class SnapshotWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='来源 writers ', dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.session = Session(self.root, 'first-chat')
        self.state, self.source = registered()
        self.state.update(revision=1, settings={})
        self.state['courses'][0]['moodle_course_ids'] = [1]
        atomic(self.session.state, self.state)

    def collect(self, session=None, source=None):
        session = session or self.session
        value = self.source if source is None else source
        run = session.start(explicit=True, collector=lambda path: write_snapshot(path, value))
        return run, read(Path(run['snapshot_path']))

    def exam(self, count=10):
        value = profile()
        value['sources'][0]['excerpt'] = f'{count} multiple choice questions'
        value['sections'] = [{'id': 'MC', 'question_type': 'multiple_choice', 'count': count,
                              'total_marks': count, 'marks_per_question': 1, 'option_count': 4}]
        return value

    def observed_files(self):
        paths = [self.session.state, self.session.state.with_name('state.previous.json')]
        paths += list(self.session.state.parent.rglob('*.md'))
        paths += list((self.session.runtime / 'operations').glob('*.json'))
        return {str(p.relative_to(self.root)): p.read_bytes() for p in paths if p.exists()}

    def reject_without_writes(self, command, snapshot, payload=None, operation_id=None):
        before = self.observed_files()
        with self.assertRaises(ValueError):
            daily.execute(self.session.state, command, NOW, payload=payload,
                          snapshot=snapshot, operation_id=operation_id)
        self.assertEqual(before, self.observed_files())

    def review(self, session, run):
        state = read(session.state)
        return session.acknowledge(run['snapshot_sha256'], {
            'reviewed': True, 'base_sha256': digest(state), 'state': state})

    def test_old_chat_cannot_overwrite_new_exam_or_source_check(self):
        old_run, old_snapshot = self.collect()
        newest = Session(self.root, 'new-chat')
        source = copy.deepcopy(self.source)
        source['threads'][0]['posts'][0]['message'] = '12 multiple choice questions'
        new_run, new_snapshot = self.collect(newest, source)
        daily.execute(self.session.state, 'exam-profile', NOW, payload=self.exam(12), snapshot=new_snapshot)
        self.review(newest, new_run)
        self.reject_without_writes('exam-profile', old_snapshot, self.exam(10))
        self.reject_without_writes('exam-sync', old_snapshot)
        self.reject_without_writes('render', old_snapshot)
        self.assertEqual(read(self.session.state)['exam_profiles'][0]['sections'][0]['count'], 12)
        self.assertEqual(read(self.session.state)['exam_profiles'][0]['verification_status'], 'verified')
        with self.assertRaisesRegex(ValueError, '较新的检查'):
            self.review(self.session, old_run)

    def test_actual_cli_rejects_stale_snapshot_without_writing(self):
        old, _ = self.collect()
        self.collect(Session(self.root, 'new-chat'))
        before = self.observed_files()
        result = subprocess.run([sys.executable, str(HERE / 'daily.py'), '--state', str(self.session.state),
            'exam-profile', '--snapshot', old['snapshot_path']], input=json.dumps(self.exam()),
            text=True, encoding='utf-8', capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('旧轮次', result.stderr)
        self.assertEqual(before, self.observed_files())

    def test_scope_change_rejects_daily_and_session_writers(self):
        run, snapshot = self.collect()
        state = read(self.session.state); state['courses'][0]['moodle_course_ids'] = [2]
        atomic(self.session.state, state)
        self.reject_without_writes('exam-profile', snapshot, self.exam())
        with self.assertRaisesRegex(ValueError, '范围已变化'):
            self.review(self.session, run)

    def test_argument_copy_or_disk_snapshot_modification_is_rejected(self):
        run, snapshot = self.collect()
        modified = copy.deepcopy(snapshot)
        modified['threads'][0]['posts'][0]['message'] = '12 multiple choice questions'
        self.reject_without_writes('exam-profile', modified, self.exam(12))
        atomic(Path(run['snapshot_path']), modified)
        self.reject_without_writes('exam-sync', modified)

    def test_mutated_request_or_pointer_is_rejected(self):
        run, snapshot = self.collect()
        pointer = self.session.runtime / 'latest-request.json'
        data = read(pointer); data['sequence'] += 1; atomic(pointer, data)
        self.reject_without_writes('exam-sync', snapshot)
        data['sequence'] -= 1; atomic(pointer, data)
        path = Path(run['snapshot_path']).with_name('request.json')
        request = read(path); request['scope']['site'] = 'https://changed.example'; atomic(path, request)
        self.reject_without_writes('exam-sync', snapshot)

    def test_missing_runtime_metadata_does_not_fall_back_to_legacy(self):
        _, snapshot = self.collect()
        unbound = copy.deepcopy(snapshot); unbound.pop('collection_binding')
        self.reject_without_writes('exam-profile', unbound, self.exam())
        (self.session.runtime / 'latest-request.json').unlink()
        self.reject_without_writes('exam-profile', unbound, self.exam())

    def test_managed_snapshot_from_another_workspace_is_rejected(self):
        _, snapshot = self.collect()
        other = self.root / 'another/Study/_System/state.json'
        atomic(other, self.state)
        before = other.read_bytes()
        with self.assertRaises(ValueError):
            daily.execute(other, 'exam-profile', NOW, payload=self.exam(), snapshot=snapshot)
        self.assertEqual(before, other.read_bytes())

    def test_idempotent_daily_replay_cannot_acknowledge_superseded_sources(self):
        _, snapshot = self.collect()
        daily.execute(self.session.state, 'exam-profile', NOW, payload=self.exam(),
                      snapshot=snapshot, operation_id='exam-event')
        same = daily.execute(self.session.state, 'exam-profile', NOW, payload=self.exam(),
                             snapshot=snapshot, operation_id='exam-event')
        self.assertTrue(same['duplicate'])
        self.collect(Session(self.root, 'new-chat'))
        self.reject_without_writes('exam-profile', snapshot, self.exam(), 'exam-event')

    def test_session_review_replay_cannot_write_views_after_new_run(self):
        run, _ = self.collect()
        state = read(self.session.state)
        proposal = {'reviewed': True, 'base_sha256': digest(state), 'state': state}
        self.session.acknowledge(run['snapshot_sha256'], proposal)
        self.collect(Session(self.root, 'new-chat'))
        before = self.observed_files()
        with self.assertRaisesRegex(ValueError, '较新的检查'):
            self.session.acknowledge(run['snapshot_sha256'], proposal)
        self.assertEqual(before, self.observed_files())

    def test_failed_current_snapshot_can_mark_needs_review_but_not_verify_profile(self):
        _, failed = self.collect(source={'collection_complete': False, 'pages': []})
        daily.execute(self.session.state, 'exam-sync', NOW, snapshot=failed)
        self.assertEqual(read(self.session.state)['exam_profiles'][0]['verification_status'], 'needs_review')
        self.reject_without_writes('exam-profile', failed, self.exam())

    def test_failure_refresh_registration_review_and_mock_recover_normally(self):
        self.collect(source={'collection_complete': False, 'pages': []})
        run = self.session.start(explicit=True, collector=lambda path: write_snapshot(path, self.source))
        snapshot = read(Path(run['snapshot_path']))
        daily.execute(self.session.state, 'exam-profile', NOW, payload=self.exam(), snapshot=snapshot)
        self.review(self.session, run)
        state = read(self.session.state)
        self.assertEqual(state['exam_profiles'][0]['verification_status'], 'verified')
        self.assertEqual(exam_prep._profile(state, {'exam_id': 'E', 'mode': 'full_mock'})[1], 'full_mock')

    def test_legacy_unmanaged_archive_still_accepts_existing_exam_workflow(self):
        self.assertFalse((self.session.runtime / 'runs').exists())
        daily.execute(self.session.state, 'exam-profile', NOW, payload=self.exam(), snapshot=self.source)
        self.assertEqual(read(self.session.state)['exam_profiles'][0]['verification_status'], 'verified')

    def test_generic_bridge_cannot_bypass_exam_source_gate(self):
        current = read(self.session.state)
        draft = copy.deepcopy(current)
        draft['exam_profiles'][0]['sources'][0]['excerpt'] = '12 multiple choice questions'
        before = self.observed_files()
        with self.assertRaises(ValueError):
            bridge(self.session.state, 'replace', {'expected': current, 'state': draft})
        self.assertEqual(before, self.observed_files())

    def test_generic_bridge_preserves_exams_during_ordinary_progress_update(self):
        current = read(self.session.state)
        draft = copy.deepcopy(current)
        draft['courses'][0]['current_topic'] = 'Chapter 2'
        bridge(self.session.state, 'replace', {'expected': current, 'state': draft})
        saved = read(self.session.state)
        self.assertEqual(saved['exam_profiles'], current['exam_profiles'])
        self.assertEqual(saved['courses'][0]['current_topic'], 'Chapter 2')


if __name__ == '__main__': unittest.main()
