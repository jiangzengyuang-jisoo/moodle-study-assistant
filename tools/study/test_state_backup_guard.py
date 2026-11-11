"""Initialization must not replace a lost primary state with a blank profile.

Regression for the reproduced data-destruction boundary: when state.json was
missing but state.previous.json (or another recovery artifact) still existed, a
fresh blank state was created and the following review initialization wrote that
blank state over the last recoverable backup.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import state_io


class StateBackupGuardTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='state-backup-guard-'))
        self.system = self.root / 'Study' / '_System'
        self.system.mkdir(parents=True)
        self.path = self.system / 'state.json'
        self.previous = self.system / 'state.previous.json'
        self.backup = json.dumps({'revision': 42, 'courses': [
            {'id': 'sentinel', 'name': 'Last recoverable profile'}], 'audit_sentinel': 'must-survive'}).encode('utf-8')
        self.previous.write_bytes(self.backup)
        self.payload = {'schema_version': 1, 'revision': 0, 'courses': []}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_init_refuses_to_create_blank_state_over_existing_backup(self):
        with self.assertRaisesRegex(ValueError, '备份|恢复'):
            state_io.bridge(self.path, 'init', self.payload)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.previous.read_bytes(), self.backup)

    def test_init_refuses_other_recovery_artifacts(self):
        self.previous.unlink()
        (self.system / 'LibrarySync').mkdir()
        (self.system / 'LibrarySync' / 'latest.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '恢复'):
            state_io.bridge(self.path, 'init', self.payload)
        self.assertFalse(self.path.exists())

    def test_init_still_works_for_a_fresh_profile(self):
        self.previous.unlink()
        state_io.bridge(self.path, 'init', self.payload)
        self.assertTrue(self.path.exists())
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['courses'], [])
        # A repeated init never rewrites an existing state.
        state_io.bridge(self.path, 'init', {'schema_version': 1, 'revision': 99})
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['revision'], 0)

    def test_existing_state_is_never_treated_as_lost(self):
        state_io.atomic(self.path, {'revision': 7, 'courses': [{'id': 'keep'}]})
        state_io.bridge(self.path, 'init', self.payload)
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['revision'], 7)
        self.assertEqual(saved['courses'], [{'id': 'keep'}])
        self.assertEqual(self.previous.read_bytes(), self.backup)


if __name__ == '__main__':
    unittest.main()
