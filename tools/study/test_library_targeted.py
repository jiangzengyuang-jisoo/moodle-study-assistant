"""Chapter updates index only selected originals and preserve other cached work."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from course_content import index_course
import library


class TargetedIndexTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(prefix='.targeted-index-', dir=self.base)
        self.root = Path(self.temp.name)
        self.study = self.root / 'Study'
        self.state_path = self.study / '_System/state.json'
        self.state_path.parent.mkdir(parents=True)
        self.state = {'revision': 1, 'courses': [{'id': 'A'}, {'id': 'B'}], 'materials': []}
        self.save()
        self.lib = library.Library(self.state_path)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.base))
        self.temp.cleanup()

    def save(self):
        self.state_path.write_text(json.dumps(self.state), encoding='utf-8')

    def material(self, mid, course='A', text=None, **fields):
        path = self.study / course / 'Lectures' / (mid + '.txt')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text or 'Original ' + mid, encoding='utf-8')
        row = {'id': mid, 'course_id': course, 'title': mid, 'filename': path.name,
               'relative_path': path.relative_to(self.study).as_posix(),
               'sha256': library.digest(path), **fields}
        self.state['materials'].append(row)
        self.save()
        return path, row

    def test_targeted_update_does_not_read_or_reextract_unrelated_sources(self):
        path, row = self.material('target')
        same_course, _ = self.material('other-chapter')
        other_course, _ = self.material('other-course', 'B')
        self.lib.sync()
        before_catalog = {m['id']: m for m in self.lib.catalog()['materials']}
        path.write_text('Updated current chapter', encoding='utf-8')
        row['sha256'] = library.digest(path)
        self.save()
        before_state = self.state_path.read_bytes()
        actual_digest = library.digest

        def selected_only(source):
            self.assertNotIn(Path(source), [same_course, other_course])
            return actual_digest(source)

        with patch('library.digest', side_effect=selected_only):
            result = index_course(self.state_path, 'A', ['target'])
        self.assertEqual(result['extracted'], 1)
        self.assertEqual(result['reused'], 0)
        self.assertEqual([m['id'] for m in result['materials']], ['target'])
        after_catalog = {m['id']: m for m in self.lib.catalog()['materials']}
        for mid in ['other-chapter', 'other-course']:
            self.assertEqual(after_catalog[mid], before_catalog[mid])
        self.assertEqual(self.state_path.read_bytes(), before_state)
        self.assertEqual(self.lib.read('target', around=0)['pages'][0]['text'], 'Updated current chapter')

    def test_invalid_selection_never_falls_back_to_full_course(self):
        self.material('target')
        self.material('outside', 'B')
        self.material('old', version_status='archived', superseded_by='target')
        self.lib.sync()
        before = (self.lib.folder / 'catalog.json').read_bytes()
        for ids in [[], (), set(), '', 'target', [''], [None], ['missing'], ['outside'], ['old'], ['target', 'missing']]:
            with self.subTest(ids=ids):
                with patch('learning_library.extract.extract_document', side_effect=AssertionError('must not extract')):
                    with self.assertRaises(ValueError):
                        index_course(self.state_path, 'A', ids)
                self.assertEqual((self.lib.folder / 'catalog.json').read_bytes(), before)

    def test_archive_move_preserves_explicit_history_but_default_uses_new_version(self):
        old_path, old = self.material('old', text='Old evidence text')
        self.lib.sync()
        archive = self.study / 'A/_Archive/old.txt'
        archive.parent.mkdir(parents=True)
        old_path.rename(archive)
        old.update(relative_path=archive.relative_to(self.study).as_posix(),
                   version_status='archived', superseded_by='new')
        self.material('new', text='New chapter content')
        original_digest = library.digest

        def skip_archive(source):
            self.assertNotEqual(Path(source), archive)
            return original_digest(source)

        with patch('library.digest', side_effect=skip_archive):
            report = index_course(self.state_path, 'A', ['new'])
        self.assertEqual(report['extracted'], 1)
        self.assertEqual([m['id'] for m in self.lib.outline('A')['materials']], ['new'])
        self.assertEqual(self.lib.search('evidence', 'A')['results'], [])
        self.assertEqual(self.lib.read('old', around=0, include_archived=True)['pages'][0]['text'], 'Old evidence text')

    def test_repeated_ids_index_once_and_unchanged_target_reuses_cache(self):
        self.material('target')
        self.material('other')
        first = index_course(self.state_path, 'A', ['target', 'target'])
        self.assertEqual(first['extracted'], 1)
        self.assertEqual(len(first['materials']), 1)
        self.assertEqual([m['id'] for m in self.lib.catalog()['materials']], ['target'])
        second = index_course(self.state_path, 'A', ['target'])
        self.assertEqual(second['extracted'], 0)
        self.assertEqual(second['reused'], 1)

    def test_unfiltered_index_keeps_existing_course_behavior(self):
        self.material('first')
        self.material('second')
        self.material('outside', 'B')
        result = index_course(self.state_path, 'A')
        self.assertEqual(result['extracted'], 2)
        self.assertEqual({m['id'] for m in result['materials']}, {'first', 'second'})

    def test_cli_repeated_material_options_only_index_requested_files(self):
        self.material('one')
        self.material('two')
        self.material('three')
        before_state = self.state_path.read_bytes()
        child = subprocess.run([sys.executable, str(self.base / 'course_content.py'),
                                '--state', str(self.state_path), 'index', '--course', 'A',
                                '--material', 'one', '--material', 'two'],
                               capture_output=True, text=True, encoding='utf-8', check=True)
        result = json.loads(child.stdout)
        self.assertEqual({m['id'] for m in result['materials']}, {'one', 'two'})
        self.assertEqual(result['extracted'], 2)
        self.assertEqual(self.state_path.read_bytes(), before_state)


if __name__ == '__main__':
    unittest.main()
