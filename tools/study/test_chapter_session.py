"""Local teaching startup and chapter-check routing, with synthetic receipts only."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import session
from session import Session
from state_io import atomic

HERE = Path(__file__).resolve().parent


class ChapterSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='chapter-session-', dir=HERE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = Session(self.root, 'original-user-chat')
        atomic(self.session.state, {'revision': 1, 'courses': [], 'settings': {}, 'mistakes': [],
                                   'teaching_evidence': {'answer': 'KEEP'}})

    def test_continue_and_today_cli_never_start_school_collection_for_a_new_chat(self):
        for command in ['continue', 'today']:
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, str(HERE / 'session.py'), '--root', str(self.root),
                                         '--chat', command + '-chat', command], capture_output=True, check=True)
                value = json.loads(result.stdout.splitlines()[-1])
                self.assertFalse(value['school_check']['network_started'])
                self.assertEqual(value['school_check']['phase'], 'not_started')
                self.assertTrue(value['school_check']['school_evidence_pending'])
                self.assertFalse((self.session.runtime / 'latest-request.json').exists())
                self.assertFalse((self.session.runtime / 'runs').exists())

    def test_first_local_continue_does_not_start_archive_maintenance(self):
        before = self.session.state.read_bytes()
        with patch.object(self.session, 'maintain', side_effect=AssertionError('no maintenance')), \
                patch.object(self.session, 'collect', side_effect=AssertionError('no network')):
            result = self.session.start()
        self.assertEqual(result['phase'], 'not_started')
        self.assertEqual(self.session.state.read_bytes(), before)

    def test_chapter_check_forwards_only_explicit_scope_and_original_chat_id(self):
        before = self.session.state.read_bytes()
        receipt = {'operation': 'verify_chapter', 'ready_for_teaching': True, 'reused': False,
                   'full_refresh': False, 'materials': [{'material_id': 'A'}]}
        child = subprocess.CompletedProcess([], 0, json.dumps(receipt).encode(), b'')
        with patch('session.shutil.which', return_value='node'), \
                patch('session.subprocess.run', return_value=child) as run, \
                patch.object(self.session, 'start', side_effect=AssertionError('not a full refresh')):
            result = self.session.verify_chapter('COURSE', '8', ['A', 'B,C'], True)
        self.assertEqual(result, receipt)
        self.assertEqual(run.call_args.args[0], ['node', str(self.root / 'tools/moodle/run.mjs'),
            'verify-chapter', '--chat', 'original-user-chat', '--course', 'COURSE', '--chapter', '8',
            '--material', 'A', '--material', 'B,C', '--force'])
        self.assertEqual(self.session.state.read_bytes(), before)
        self.assertFalse(self.session.record.exists())
        self.assertFalse((self.session.runtime / 'latest-request.json').exists())

    def test_invalid_chapter_selection_never_launches_child(self):
        with patch('session.subprocess.run') as child:
            for course, chapter, ids in [('', '8', ['A']), ('C', None, ['A']), ('C', '8', []), ('C', '8', [''])]:
                with self.subTest(course=course, chapter=chapter, ids=ids):
                    with self.assertRaisesRegex(ValueError, '章节核对需要'):
                        self.session.verify_chapter(course, chapter, ids)
        child.assert_not_called()

    def test_failed_or_cached_chapter_receipts_do_not_touch_full_review_state(self):
        receipt = {'operation': 'verify_chapter', 'ready_for_teaching': False, 'reused': True,
                   'status': 'needs_review', 'full_refresh': False}
        before = self.session.state.read_bytes()
        child = subprocess.CompletedProcess([], 1, json.dumps(receipt).encode(), b'ignored raw details')
        with patch('session.shutil.which', return_value='node'), patch('session.subprocess.run', return_value=child):
            self.assertEqual(self.session.verify_chapter('C', '8', ['A']), receipt)
        self.assertEqual(self.session.state.read_bytes(), before)
        self.assertFalse(self.session.record.exists())

    def test_child_errors_and_timeout_do_not_expose_raw_output(self):
        cases = [subprocess.CompletedProcess([], 1, b'private?token=SECRET', b'SECRET'),
                 subprocess.CompletedProcess([], 0, b'{"operation":"full_refresh"}', b''),
                 subprocess.CompletedProcess([], 1, b'{"operation":"verify_chapter","ready_for_teaching":true}', b'')]
        for child in cases:
            with self.subTest(child=child), patch('session.shutil.which', return_value='node'), \
                    patch('session.subprocess.run', return_value=child):
                with self.assertRaises(ValueError) as caught:
                    self.session.verify_chapter('C', '8', ['A'])
                self.assertNotIn('SECRET', str(caught.exception))
        with patch('session.shutil.which', return_value='node'), \
                patch('session.subprocess.run', side_effect=subprocess.TimeoutExpired('SECRET', 900)):
            with self.assertRaisesRegex(ValueError, '章节核对超时') as caught:
                self.session.verify_chapter('C', '8', ['A'])
            self.assertNotIn('SECRET', str(caught.exception))

    def test_chapter_cli_propagates_failure_and_does_not_emit_local_ready_or_full_snapshot(self):
        receipt = {'operation': 'verify_chapter', 'ready_for_teaching': False, 'reused': True}
        stdout = io.StringIO()
        argv = ['session.py', '--root', str(self.root), '--chat', 'original-user-chat',
                'verify-chapter', '--course', 'C', '--chapter', '8', '--material', 'A', '--material', 'B', '--force']
        with patch('sys.argv', argv), redirect_stdout(stdout), \
                patch.object(Session, 'verify_chapter', return_value=receipt) as verify, \
                patch.object(Session, 'context', side_effect=AssertionError('no full context needed')):
            with self.assertRaises(SystemExit) as exited:
                session.main()
        self.assertEqual(exited.exception.code, 1)
        verify.assert_called_once_with('C', '8', ['A', 'B'], True)
        self.assertEqual(json.loads(stdout.getvalue()), receipt)

    def test_subtask_cannot_run_chapter_network_check(self):
        argv = ['session.py', '--root', str(self.root), '--chat', 'original-user-chat', '--subtask',
                'verify-chapter', '--course', 'C', '--chapter', '8', '--material', 'A']
        with patch('sys.argv', argv), patch.object(Session, 'verify_chapter') as verify:
            with self.assertRaisesRegex(ValueError, '内部任务只允许'):
                session.main()
        verify.assert_not_called()

    def test_chapter_options_cannot_be_silently_ignored_by_local_continue(self):
        argv = ['session.py', '--root', str(self.root), '--chat', 'chat', 'continue', '--force']
        with patch('sys.argv', argv), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exited:
                session.main()
        self.assertEqual(exited.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
