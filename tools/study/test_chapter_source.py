"""Deletion recovery uses synthetic course originals, never a live archive."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from chapter_source import finish
from material_versions import install_download
import test_course_content


class ChapterSourceTests(unittest.TestCase):
    def setUp(self):
        self.f = test_course_content.CourseContentTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def replacement(self, body=b'Readable replacement'):
        f = self.f
        old = install_download(f.path, f.data())
        data = f.data(body)
        data['source'].update(timemodified=101, chapter_timestamp_update=True)
        new = install_download(f.path, data)
        return old, new, data['source']

    def test_failed_delete_is_pending_then_explicit_retry_finishes_only_that_original(self):
        old, new, source = self.replacement()
        f = self.f
        previous = next(m for m in f.read()['materials'] if m['id'] == old['material_id'])
        target = f.study / previous['relative_path']
        original_unlink = Path.unlink
        def blocked(path, *args, **kwargs):
            if path == target:
                raise PermissionError('simulated original open in another program')
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', blocked):
            with self.assertRaises(PermissionError):
                finish(f.path, new['material_id'], source)
        pending = next(m for m in f.read()['materials'] if m['id'] == old['material_id'])
        self.assertEqual(pending['version_status'], 'deletion_pending')
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b'First course body')
        self.assertEqual(finish(f.path, new['material_id'], source)['previous_status'], 'deleted')
        self.assertFalse(target.exists())
        self.assertEqual(f.read()['mistakes'], f.state['mistakes'])

    def test_unreadable_replacement_and_local_edits_preserve_old_original(self):
        old, new, source = self.replacement(b'  ')
        f = self.f
        previous = next(m for m in f.read()['materials'] if m['id'] == old['material_id'])
        target = f.study / previous['relative_path']
        with self.assertRaises(ValueError):
            finish(f.path, new['material_id'], source)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b'First course body')

    def test_indexed_new_version_cannot_delete_a_modified_original(self):
        old, new, source = self.replacement()
        f = self.f
        previous = next(m for m in f.read()['materials'] if m['id'] == old['material_id'])
        target = f.study / previous['relative_path']
        target.write_text('MY ANNOTATIONS', encoding='utf8')
        before = f.path.read_bytes()
        with self.assertRaises(ValueError):
            finish(f.path, new['material_id'], source)
        self.assertEqual(f.path.read_bytes(), before)
        self.assertEqual(target.read_text(encoding='utf8'), 'MY ANNOTATIONS')

    def test_historical_cross_account_lineage_cannot_authorize_deletion(self):
        old, new, source = self.replacement()
        f = self.f
        state = f.read()
        previous = next(m for m in state['materials'] if m['id'] == old['material_id'])
        target = f.study / previous['relative_path']
        # Simulate an existing wrong lineage created by an older installer.
        previous['moodle_file_sources'][0]['site'] = 'https://another-school.example'
        f.save(state)
        before = f.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'another school'):
            finish(f.path, new['material_id'], source)
        self.assertEqual(f.path.read_bytes(), before)
        self.assertEqual(target.read_bytes(), b'First course body')
        self.assertTrue((f.study / new['relative_path']).exists())

    def recovery_chain(self):
        f = self.f
        history = install_download(f.path, f.data(b'Ordinary old archive'))
        original = install_download(f.path, f.data(b'Usable original A'))
        bad_data = f.data(b'  ')
        bad_data['source'].update(timemodified=101, chapter_timestamp_update=True)
        bad = install_download(f.path, bad_data)
        with self.assertRaises(ValueError):
            finish(f.path, bad['material_id'], bad_data['source'])
        recovered_data = f.data(b'Readable recovered C')
        recovered_data['source'].update(timemodified=101, chapter_timestamp_update=True)
        recovered = install_download(f.path, recovered_data)
        return history, original, bad, recovered, recovered_data['source']

    def test_ancestor_deletion_resumes_past_deleted_parent_but_keeps_ordinary_history(self):
        history, original, bad, recovered, source = self.recovery_chain()
        f = self.f
        rows = {m['id']: m for m in f.read()['materials']}
        target = f.study / rows[original['material_id']]['relative_path']
        ordinary = f.study / rows[history['material_id']]['relative_path']
        original_unlink = Path.unlink
        def blocked(path, *args, **kwargs):
            if path == target:
                raise PermissionError('ancestor is temporarily open')
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', blocked):
            with self.assertRaises(PermissionError):
                finish(f.path, recovered['material_id'], source)
        rows = {m['id']: m for m in f.read()['materials']}
        self.assertEqual(rows[bad['material_id']]['version_status'], 'deleted')
        self.assertEqual(rows[original['material_id']]['version_status'], 'deletion_pending')
        finish(f.path, recovered['material_id'], source)
        rows = {m['id']: m for m in f.read()['materials']}
        for item in (original, bad):
            row = rows[item['material_id']]
            self.assertEqual(row['version_status'], 'deleted')
            self.assertFalse((f.study / row['relative_path']).exists())
        self.assertEqual(rows[history['material_id']]['version_status'], 'archived')
        self.assertEqual(ordinary.read_bytes(), b'Ordinary old archive')
        self.assertEqual(f.read()['mistakes'], f.state['mistakes'])

    def test_modified_ancestor_blocks_whole_chain_before_deleting_any_file(self):
        _, original, bad, recovered, source = self.recovery_chain()
        f = self.f
        rows = {m['id']: m for m in f.read()['materials']}
        target = f.study / rows[original['material_id']]['relative_path']
        target.write_bytes(b'My handwritten annotations')
        before = f.path.read_bytes()
        with self.assertRaises(ValueError):
            finish(f.path, recovered['material_id'], source)
        self.assertEqual(f.path.read_bytes(), before)
        self.assertTrue((f.study / rows[bad['material_id']]['relative_path']).exists())
        self.assertEqual(target.read_bytes(), b'My handwritten annotations')


if __name__ == '__main__':
    unittest.main()
