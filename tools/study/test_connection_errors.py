"""Synthetic diagnostics and first-learning-entry regressions; no school connection."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from connection_errors import safe_connection_error
from session import Session
from session_test_fixtures import write_snapshot
from state_io import atomic


class ConnectionTests(unittest.TestCase):
    def test_only_allowlisted_codes_cross_process_boundary(self):
        self.assertIsNone(safe_connection_error('private output\n{"connection_error":"unknown"}'))
        self.assertIsNone(safe_connection_error('["STUDY_DNS"]\n{"connection_error":[]}'))
        detail = safe_connection_error(b'noise\n{"connection_error":"STUDY_DNS","message":"SECRET","next_step":"SECRET"}')
        self.assertEqual('STUDY_DNS', detail[0])
        self.assertNotIn('SECRET', str(detail))

    def test_connection_failure_preserves_state_and_does_not_automatically_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder, 'original-chat')
            atomic(session.state, {'revision': 1, 'courses': [], 'mistakes': [], 'settings': {}})
            before = session.state.read_bytes()
            error = json.dumps({'connection_error': 'STUDY_DNS', 'message': 'SECRET'}).encode()
            result = subprocess.CompletedProcess([], 1, b'SECRET', error)
            with patch('session.shutil.which', return_value='node'), patch('session.subprocess.run', return_value=result) as child:
                with self.assertRaisesRegex(ValueError, 'STUDY_DNS') as caught:
                    session.start(explicit=True)
                self.assertNotIn('SECRET', str(caught.exception))
                self.assertEqual('failed', session.start()['phase'])
                self.assertEqual(1, child.call_count)
                self.assertEqual(before, session.state.read_bytes())
            result = session.start(explicit=True, collector=lambda path: write_snapshot(path, {'collection_complete': True}))
            self.assertEqual('collected', result['phase'])

    def test_unknown_child_error_is_not_forwarded(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder, 'original-chat')
            with patch('session.shutil.which', return_value='node'), patch('session.subprocess.run', return_value=subprocess.CompletedProcess([], 1, b'', b'SECRET')):
                with self.assertRaises(ValueError) as caught:
                    session.collect(Path(folder) / 'snapshot.json')
                self.assertNotIn('SECRET', str(caught.exception))

    def test_readonly_diagnosis_and_local_continue_leave_explicit_refresh_available(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder, 'original-chat')
            atomic(session.state, {'revision': 1, 'courses': [], 'mistakes': [], 'settings': {}})
            before = session.state.read_bytes()
            with patch.object(session, 'collect') as network:
                session.status()
                session.context()
                session.start()
                session.start(subtask=True)
                network.assert_not_called()
            self.assertFalse(session.record.exists())
            self.assertEqual(before, session.state.read_bytes())
            calls = []
            def collect(path):
                calls.append(path)
                write_snapshot(path, {'collection_complete': True})
            self.assertTrue(session.start(explicit=True, collector=collect)['network_started'])
            self.assertFalse(session.start(collector=collect)['network_started'])
            self.assertEqual(1, len(calls))
