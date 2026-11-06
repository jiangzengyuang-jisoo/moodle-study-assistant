"""Synthetic version transitions, history preservation and retention boundaries."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from library import Library
from library_test_fixtures import temporary_directory, write_pdf
from learning_library import versions as v
from learning_library.context import build_context
from material_versions import configure, install_download

T0 = '2026-01-01T00:00:00+00:00'


class MaterialVersionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory('versions-')
        self.addCleanup(self.temp.cleanup)
        self.study = Path(self.temp.name) / 'Study'
        self.path = self.study / '_System/state.json'
        self.path.parent.mkdir(parents=True)
        self.state = {'revision': 1, 'settings': {}, 'courses': [{'id': 'A', 'teaching_cursor': {'stage': 'pending'}}],
                      'materials': [], 'mistakes': [{'id': 'mistake1', 'course_id': 'A', 'answer': 'original answer'}]}
        self.save()
        self.old = self.material('old', 'A/Lectures/lesson.pdf', 'OLD_SENTINEL')
        self.new = self.material('new', '_Inbox/new/lesson.pdf', 'NEW_SENTINEL')
        self.lib = Library(self.path)

    def save(self):
        self.path.write_text(json.dumps(self.state, ensure_ascii=False), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def material(self, mid, relative, text):
        target = self.study / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        write_pdf(target, [text])
        row = {'id': mid, 'course_id': 'A', 'filename': 'lesson.pdf', 'category': 'Lectures',
               'title': 'Lecture', 'relative_path': relative, 'sha256': v.sha(target), 'contains_solutions': False,
               'moodle_file_sources': [{'module_id': 123, 'remote_filename': 'lesson.pdf'}]}
        self.state['materials'].append(row)
        self.save()
        return row

    def promote(self):
        return v.promote(self.path, self.new, at=T0)

    def archived(self):
        return next(m for m in self.read()['materials'] if m['id'] == 'old')

    def prepare_cleanup(self):
        configure(self.path)
        self.promote()

    def test_latest_only_even_before_rebuild_and_explicit_history_stays_original(self):
        self.state['mistakes'][0].update(material_id='old', page=1, source=self.old['relative_path'])
        self.save()
        self.lib.sync()
        self.promote()
        self.assertEqual(self.lib.search('OLD_SENTINEL', 'A')['results'], [])
        with self.assertRaisesRegex(ValueError, 'archived_version'):
            self.lib.read('old')
        self.lib.sync()
        self.assertEqual({r['material_id'] for r in self.lib.search('NEW_SENTINEL', 'A')['results']}, {'new'})
        self.assertEqual([m['id'] for m in self.lib.outline('A')['materials']], ['new'])
        context = build_context(self.read(), 'A', True)
        self.assertEqual([m['id'] for m in context['sections']['materials']], ['new'])
        history = self.lib.read('old', include_archived=True)
        self.assertTrue(history['historical_version'])
        self.assertIn('OLD_SENTINEL', history['pages'][0]['text'])
        mistake = self.read()['mistakes'][0]
        self.assertEqual((mistake['material_id'], mistake['page'], mistake['answer']), ('old', 1, 'original answer'))
        self.assertEqual(mistake['source'], self.old['relative_path'])
        self.assertEqual(self.archived()['original_relative_path'], mistake['source'])
        self.assertEqual(self.read()['courses'], self.state['courses'])

    def test_both_old_and_new_note_links_move_to_correct_original(self):
        note = self.study / 'A/notes.md'
        note.write_text('[old](Lectures/lesson.pdf#page=1)\n[new](../_Inbox/new/lesson.pdf)\n', encoding='utf-8')
        self.promote()
        text = note.read_text(encoding='utf-8')
        self.assertIn('_Archive/', text)
        self.assertIn('[new](<Lectures/lesson.pdf>)', text)
        self.assertNotIn('_Inbox', text)

    def test_same_filename_is_not_a_version_relationship(self):
        self.new['moodle_file_sources'][0]['module_id'] = 999
        self.save()
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'not proof'):
            self.promote()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(v.sha(self.study / self.old['relative_path']), self.old['sha256'])

    def test_locally_modified_old_and_invalid_new_preserve_originals(self):
        old = self.study / self.old['relative_path']
        original = old.read_bytes()
        old.write_bytes(original + b'LOCAL NOTE')
        with self.assertRaisesRegex(ValueError, 'modified'):
            self.promote()
        old.write_bytes(original)
        target = self.study / self.new['relative_path']
        target.write_bytes(b'%PDF-1.4 invalid')
        self.new['sha256'] = v.sha(target)
        with self.assertRaisesRegex(ValueError, 'readable'):
            self.promote()
        self.assertEqual(old.read_bytes(), original)

    def test_commit_failure_rolls_back_files_and_byte_exact_notes(self):
        note = self.study / 'A/note.md'
        note.write_bytes(b'\xef\xbb\xbf[old](Lectures/lesson.pdf)\r\n')
        before, note_before = self.path.read_bytes(), note.read_bytes()
        real_write = v._write
        def fail(path, value):
            if Path(path) == self.path:
                raise OSError('simulated disk failure')
            return real_write(path, value)
        with patch.object(v, '_write', side_effect=fail):
            with self.assertRaises(OSError):
                self.promote()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(note.read_bytes(), note_before)
        self.assertTrue((self.study / self.old['relative_path']).exists())
        self.assertTrue((self.study / self.new['relative_path']).exists())
        self.assertFalse((self.path.parent / 'material-version-transaction.json').exists())

    def test_partial_move_failure_rolls_back(self):
        real_rename = Path.rename
        def fail(path, destination):
            if path == self.study / self.new['relative_path']:
                raise OSError('move failed')
            return real_rename(path, destination)
        with patch.object(Path, 'rename', fail):
            with self.assertRaises(OSError):
                self.promote()
        self.assertEqual(v.sha(self.study / self.old['relative_path']), self.old['sha256'])
        self.assertEqual(v.sha(self.study / self.new['relative_path']), self.new['sha256'])

    def test_path_escape_rejected(self):
        for path in ['../outside.pdf', '_System/../A/lesson.pdf', str(self.study.parent / 'outside.pdf')]:
            with self.assertRaises(ValueError):
                v.safe(self.study, path)

    def test_legacy_source_identity_without_remote_filename(self):
        self.old['moodle_file_sources'] = [{'module_id': 123, 'url': 'https://school.example/pluginfile.php/lesson.pdf?forcedownload=1'}]
        self.save()
        self.assertEqual(self.promote()['archived_id'], 'old')

    def test_solution_protection_inherited_without_transferring_mastery(self):
        self.old['contains_solutions'] = True
        self.save()
        self.promote()
        self.lib.sync()
        self.assertEqual(self.lib.search('NEW_SENTINEL', 'A')['results'], [])
        with self.assertRaisesRegex(ValueError, 'Solution'):
            self.lib.read('new')

    def test_repeated_promotion_is_idempotent(self):
        self.promote()
        state = self.read()
        new = next(m for m in state['materials'] if m['id'] == 'new')
        result = v.promote(self.path, new, 'old', at=T0)
        self.assertEqual(result['status'], 'already_promoted')
        self.assertEqual(self.read(), state)

    def test_cleanup_30_day_boundary_and_learning_data_unchanged(self):
        self.prepare_cleanup()
        state_before = self.read()
        target = self.study / self.archived()['relative_path']
        self.assertEqual(v.cleanup(self.path, '2026-01-30T23:59:59+00:00')['deleted'], [])
        self.assertEqual(self.read(), state_before)
        result = v.cleanup(self.path, '2026-01-31T00:00:00+00:00')
        self.assertEqual(result['deleted'], ['old'])
        self.assertFalse(target.exists())
        self.assertEqual(self.read()['mistakes'], state_before['mistakes'])
        self.assertEqual(self.read()['courses'], state_before['courses'])
        self.assertEqual(self.archived()['sha256'], self.old['sha256'])

    def test_references_local_changes_and_missing_successor_protect_archives(self):
        self.prepare_cleanup()
        target = self.study / self.archived()['relative_path']
        baseline = self.read()
        for mode in ['id', 'markdown', 'local_edit', 'missing_successor']:
            self.state = copy.deepcopy(baseline)
            self.save()
            note = self.study / 'A/note.md'
            original = target.read_bytes()
            current = self.study / 'A/Lectures/lesson.pdf'
            current_bytes = current.read_bytes()
            if mode == 'id':
                self.state['mistakes'][0]['material_id'] = 'old'; self.save()
            elif mode == 'markdown':
                note.write_text('[old](../' + self.archived()['relative_path'] + ')', encoding='utf-8')
            elif mode == 'local_edit':
                target.write_bytes(original + b'annotation')
            else:
                current.unlink()
            before = self.path.read_bytes()
            self.assertEqual(v.cleanup(self.path, '2026-03-01T00:00:00+00:00', True)['deleted'], [], mode)
            self.assertTrue(target.exists(), mode)
            self.assertEqual(self.path.read_bytes(), before, mode)
            target.write_bytes(original)
            current.write_bytes(current_bytes)
            note.unlink(missing_ok=True)

    def test_cleanup_requires_opt_in_and_throttles_on_use(self):
        self.promote()
        self.assertFalse(v.cleanup(self.path, '2026-03-01T00:00:00+00:00')['enabled'])
        configure(self.path)
        v.cleanup(self.path, T0)
        before = self.path.read_bytes()
        self.assertTrue(v.cleanup(self.path, T0)['already_checked'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_download_installs_verified_bytes_and_identical_reuses(self):
        # An incoming download has not yet been registered.
        self.state['materials'] = [self.old]
        self.save()
        data = {'source': {'course_id': 'A', 'module_id': 123, 'filename': 'lesson.pdf', 'category': 'Lectures'},
                'relative_path': self.new['relative_path'], 'sha256': self.new['sha256']}
        first = install_download(self.path, data)
        self.assertEqual(first['status'], 'promoted')
        staged = self.study / data['relative_path']
        staged.write_bytes((self.study / first['relative_path']).read_bytes())
        again = install_download(self.path, data)
        self.assertEqual(again['status'], 'identical')
        self.assertEqual(len(self.read()['materials']), 2)


if __name__ == '__main__':
    unittest.main()
