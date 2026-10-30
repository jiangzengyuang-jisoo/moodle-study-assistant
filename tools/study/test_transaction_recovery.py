"""State writers reconcile interrupted material moves before touching learning data."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import daily
import review
import state_io
from learning_library import versions

HERE = Path(__file__).resolve().parent
NOW = review.timestamp('2026-10-07T15:06:00+08:00')


class SimulatedProcessExit(BaseException):
    """Bypass ordinary Exception rollback, like process termination does."""


class TransactionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='recover 学习 ', dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.study = Path(self.tmp.name) / 'Study'
        self.path = self.study / '_System/state.json'
        self.old = self.study / 'C/Lectures/lesson.txt'
        self.incoming = self.study / '_Inbox/new/lesson.txt'
        self.old.parent.mkdir(parents=True)
        self.incoming.parent.mkdir(parents=True)
        self.old.write_text('OLD ORIGINAL', encoding='utf-8')
        self.incoming.write_text('NEW ORIGINAL', encoding='utf-8')
        self.note = self.study / 'C/note.md'
        self.note_before = b'\xef\xbb\xbf[old](Lectures/lesson.txt#page=4)\r\n'
        self.note.write_bytes(self.note_before)
        self.old_row = self.row('OLD', self.old)
        self.new_row = self.row('NEW', self.incoming)
        self.initial = {
            'revision': 1, 'settings': {}, 'review_queue': [],
            'courses': [{'id': 'C', 'teaching_cursor': {'stage': 'review', 'material_id': 'OLD'}}],
            'materials': [self.old_row, self.new_row],
            'mistakes': [{'id': 'M', 'course_id': 'C', 'answer': 'original learner evidence',
                          'source': {'material_id': 'OLD', 'relative_path': self.old_row['relative_path'],
                                     'pages': [4]}}],
        }
        state_io.atomic(self.path, self.initial)
        self.journal = self.path.with_name('material-version-transaction.json')

    def row(self, identifier, path):
        return {'id': identifier, 'course_id': 'C', 'filename': 'lesson.txt', 'category': 'Lectures',
                'relative_path': path.relative_to(self.study).as_posix(), 'sha256': versions.sha(path),
                'moodle_file_sources': [{'module_id': 123, 'remote_filename': 'lesson.txt'}]}

    def crash(self, after_state=False):
        original_write = versions._write
        def interrupted(path, value):
            if Path(path) == self.path:
                if after_state:
                    original_write(path, value)
                raise SimulatedProcessExit()
            original_write(path, value)
        with patch.object(versions, '_write', side_effect=interrupted):
            with self.assertRaises(SimulatedProcessExit):
                versions.promote(self.path, self.new_row)
        self.assertTrue(self.journal.exists())
        self.assertEqual(self.old.read_text(encoding='utf-8'), 'NEW ORIGINAL')

    def feedback(self):
        return daily.execute(self.path, 'feedback', NOW, payload={
            'feedback_id': 'F', 'completed': 'normal study feedback', 'evidence': 'synthetic user report',
            'recorded_at': NOW.isoformat()})

    def assert_consistent_files(self):
        for row in state_io.read(self.path)['materials']:
            self.assertEqual(versions.sha(self.study / row['relative_path']), row['sha256'], row['id'])
        self.assertFalse(self.journal.exists())

    def test_feedback_recovers_precommit_moves_before_changing_state_hash(self):
        self.crash()
        self.feedback()
        saved = state_io.read(self.path)
        self.assert_consistent_files()
        self.assertEqual(saved['courses'], self.initial['courses'])
        self.assertEqual(saved['mistakes'], self.initial['mistakes'])
        self.assertEqual(len(saved['learning_feedback']), 1)
        self.assertEqual(self.note.read_bytes(), self.note_before)
        self.assertEqual(self.old.read_text(encoding='utf-8'), 'OLD ORIGINAL')
        self.assertEqual(self.incoming.read_text(encoding='utf-8'), 'NEW ORIGINAL')
        # The repaired state remains usable; an explicit material upgrade now succeeds.
        versions.promote(self.path, self.new_row)
        self.assert_consistent_files()

    def test_postcommit_crash_keeps_committed_versions_and_feedback(self):
        self.crash(after_state=True)
        committed = state_io.read(self.path)
        self.feedback()
        saved = state_io.read(self.path)
        self.assert_consistent_files()
        self.assertEqual(saved['materials'], committed['materials'])
        self.assertEqual(saved['mistakes'], committed['mistakes'])
        self.assertEqual(saved['mistakes'][0]['answer'], 'original learner evidence')
        self.assertEqual(saved['mistakes'][0]['source']['material_id'], 'OLD')
        self.assertEqual(saved['mistakes'][0]['source']['pages'], [4])
        self.assertEqual(self.old.read_text(encoding='utf-8'), 'NEW ORIGINAL')
        self.assertEqual(len(saved['learning_feedback']), 1)

    def test_direct_review_save_recovers_before_its_cas(self):
        original = self.path.read_bytes()
        draft = copy.deepcopy(self.initial)
        draft['tasks'] = [{'id': 'T', 'status': 'pending'}]
        self.crash()
        review.save(self.path, draft, original)
        self.assert_consistent_files()
        self.assertEqual(state_io.read(self.path)['tasks'], draft['tasks'])
        self.assertEqual(state_io.read(self.path)['mistakes'], self.initial['mistakes'])

    def test_stale_draft_after_committed_crash_is_rejected(self):
        original = self.path.read_bytes()
        self.crash(after_state=True)
        committed = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '另一任务更新'):
            review.save(self.path, copy.deepcopy(self.initial), original)
        self.assertEqual(self.path.read_bytes(), committed)
        self.assert_consistent_files()

    def test_bridge_cli_recovers_before_saving(self):
        self.crash()
        draft = copy.deepcopy(self.initial)
        draft['courses'][0]['moodle_course_ids'] = [1]
        result = subprocess.run([sys.executable, str(HERE / 'state_io.py'), str(self.path), 'replace'],
                                input=json.dumps({'expected': self.initial, 'state': draft}),
                                text=True, encoding='utf-8', capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_consistent_files()
        self.assertEqual(state_io.read(self.path)['courses'][0]['moodle_course_ids'], [1])

    def test_legacy_review_cli_recovers_before_loading_state(self):
        self.crash()
        result = subprocess.run([sys.executable, str(HERE / 'review.py'), '--state', str(self.path), 'init'],
                                text=True, encoding='utf-8', capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_consistent_files()
        self.assertEqual(state_io.read(self.path)['mistakes'], self.initial['mistakes'])

    def test_modified_original_blocks_writer_without_overwriting_evidence(self):
        self.crash()
        archive = self.study / state_io.read(self.journal)['moves'][0]['to']
        archive.write_text('USER EDIT AFTER INTERRUPTION', encoding='utf-8')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Version move conflict'):
            self.feedback()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.journal.exists())
        self.assertEqual(archive.read_text(encoding='utf-8'), 'USER EDIT AFTER INTERRUPTION')
        self.assertNotIn('learning_feedback', state_io.read(self.path))


if __name__ == '__main__':
    unittest.main()
