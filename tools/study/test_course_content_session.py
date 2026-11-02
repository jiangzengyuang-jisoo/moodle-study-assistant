"""Course initialization stage integration; synthetic sources and no network."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from session import Session
from session_test_fixtures import write_snapshot
from state_io import atomic, read


class CourseContentSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.session = Session(self.tmp.name, 'original-chat')
        atomic(self.session.root / 'config.local.json', {'moodle_url': 'https://school.example'})
        atomic(self.session.state, {'revision': 1, 'courses': [{'id': 'C', 'moodle_course_ids': [9]}],
                                   'settings': {}, 'mistakes': []})

    def collect(self, path):
        write_snapshot(path, {'collection_complete': True, 'account_scope': {'site': 'https://school.example', 'user_id': 17}})

    def authorize(self):
        state = read(self.session.state)
        state['settings']['course_content_sync'] = {'version': 1, 'grants': [
            {'site': 'https://school.example', 'user_id': 17, 'moodle_course_id': 9, 'mode': 'full',
             'authorized_at': '2026-11-02T00:00:00Z'}]}
        atomic(self.session.state, state)

    def test_legacy_metadata_only_selection_does_not_launch_downloader(self):
        with patch('session.subprocess.run') as process:
            result = self.session.start(collector=self.collect)
        process.assert_not_called()
        self.assertEqual('not_authorized', result['content_sync']['status'])
        self.assertEqual('collected', result['phase'])

    def test_download_stage_has_separate_timeout_and_runs_once_per_refresh(self):
        self.authorize()
        completed = subprocess.CompletedProcess([], 0, json.dumps({'status': 'complete', 'counts': {'downloaded': 2}}).encode(), b'')
        with patch('session.subprocess.run', return_value=completed) as process, patch('session.shutil.which', return_value='node'):
            result = self.session.start(collector=self.collect)
            self.assertEqual('complete', result['content_sync']['status'])
            self.assertEqual('collected', result['phase'])
            self.assertTrue(result['school_evidence_pending'])
            self.session.start(collector=self.collect)
            self.session.start(subtask=True, collector=self.collect)
            self.assertEqual(1, process.call_count)
            self.assertEqual(7200, process.call_args.kwargs['timeout'])
            self.assertIn('content-sync', process.call_args.args[0])

    def test_content_timeout_preserves_collected_snapshot_and_does_not_automatically_retry(self):
        self.authorize()
        with patch('session.subprocess.run', side_effect=subprocess.TimeoutExpired('node', 7200)) as process, patch('session.shutil.which', return_value='node'):
            result = self.session.start(collector=self.collect)
            self.assertEqual('partial', result['content_sync']['status'])
            self.assertEqual('collected', result['phase'])
            self.assertTrue(Path(result['snapshot_path']).is_file())
            self.assertEqual('partial', self.session.start(collector=self.collect)['content_sync']['status'])
            self.assertEqual(1, process.call_count)

    def test_changed_account_or_deselected_page_does_not_start_content_stage(self):
        self.authorize()
        state = read(self.session.state)
        state['settings']['course_content_sync']['grants'][0]['user_id'] = 18
        atomic(self.session.state, state)
        with patch('session.subprocess.run') as process:
            self.session.start(collector=self.collect)
        process.assert_not_called()

    def test_interrupted_content_stage_is_reported_without_retry(self):
        result = self.session.start(collector=self.collect)
        manifest = Path(result['snapshot_path']).with_name('manifest.json')
        value = read(manifest)
        value['content_sync'] = {'status': 'running'}
        atomic(manifest, value)
        with patch('session.subprocess.run') as process:
            result = self.session.start(collector=self.collect)
        process.assert_not_called()
        self.assertEqual('interrupted', result['content_sync']['reason'])

    def library_authorize(self, mode='full'):
        self.authorize()
        state = read(self.session.state)
        state['settings']['course_library_authorization'] = {
            'schema': 1, 'courses': {'C': {'mode': mode, 'moodle_courseid': 9}}}
        atomic(self.session.state, state)

    def test_library_consent_runs_managed_stage_and_returns_library_report(self):
        self.library_authorize()
        done = subprocess.CompletedProcess([], 0, b'{"status":"complete","counts":{"installed":3}}', b'')
        with patch('session.subprocess.run', return_value=done) as process, patch('session.shutil.which', return_value='node'):
            result = self.session.start(collector=self.collect)
            self.assertEqual('complete', result['content_sync']['status'])
            self.assertIn('LibrarySync', result['content_sync']['report_path'])
            self.session.start(collector=self.collect)
            process.assert_called_once()

    def test_new_info_only_consent_overrides_old_full_grants(self):
        self.library_authorize('info_only')
        with patch('session.subprocess.run') as process:
            result = self.session.start(collector=self.collect)
        process.assert_not_called()
        self.assertEqual('not_authorized', result['content_sync']['status'])

    def test_course_chooser_defers_download_until_its_own_library_stage(self):
        self.library_authorize()
        with patch.dict('os.environ', {'STUDY_DEFER_CONTENT_SYNC': '1'}), patch('session.subprocess.run') as process:
            result = self.session.start(collector=self.collect)
        process.assert_not_called()
        self.assertEqual('deferred_to_course_chooser', result['content_sync']['reason'])
        self.assertEqual('collected', result['phase'])
