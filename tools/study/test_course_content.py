"""Bulk installation tests use invented courses and actual temporary file bytes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from course_content import index_course
from material_versions import install_download
from learning_library import versions


class CourseContentTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[2] / 'tmp/content-install-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='course-content-', dir=base)
        self.addCleanup(self.temp.cleanup)
        self.study = Path(self.temp.name) / 'Study'
        self.path = self.study / '_System/state.json'
        self.path.parent.mkdir(parents=True)
        self.state = {'revision': 1, 'courses': [{'id': 'A', 'moodle_course_ids': [100],
                      'teaching_cursor': {'topic': 'untouched'}}], 'materials': [],
                      'mistakes': [{'id': 'M1', 'answer': 'keep original'}],
                      'settings': {'course_content_sync': {'version': 1, 'grants': [{
                          'site': 'https://school.example', 'user_id': 77, 'moodle_course_id': 100,
                          'mode': 'full', 'authorized_at': '2026-01-01T00:00:00Z'}]}}}
        self.save(self.state)
        self.count = 0

    def save(self, state):
        self.path.write_text(json.dumps(state), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def data(self, text=b'First course body', module=501, filename='lesson.txt', post=None):
        self.count += 1
        target = self.study / '_Inbox' / str(self.count) / filename
        target.parent.mkdir(parents=True)
        target.write_bytes(text)
        source = {'course_id': 'A', 'module_id': module, 'filename': filename, 'category': 'Lectures',
                  'fileurl': 'https://school.example/pluginfile.php/' + str(module) + '/' + filename,
                  'scope': {'site': 'https://school.example', 'user_id': 77, 'page_id': 100}}
        if post is not None:
            source['post_id'] = post
        return {'source': source, 'relative_path': target.relative_to(self.study).as_posix(),
                'sha256': versions.sha(target), 'bytes': len(text),
                'defer_index': True, 'source_subdirectory': True, 'retain_unparsed': True}

    def test_actual_files_same_name_different_sources_do_not_collide(self):
        with patch('material_versions.Library.sync', side_effect=AssertionError('must defer index')):
            first = install_download(self.path, self.data())
            second = install_download(self.path, self.data(b'Different course body', module=502))
        self.assertNotEqual(first['relative_path'], second['relative_path'])
        for result, content in [(first, b'First course body'), (second, b'Different course body')]:
            self.assertEqual((self.study / result['relative_path']).name, 'lesson.txt')
            self.assertEqual((self.study / result['relative_path']).read_bytes(), content)
        self.assertEqual(self.read()['courses'], self.state['courses'])
        self.assertEqual(self.read()['mistakes'], self.state['mistakes'])

    def test_identical_reuses_id_and_retains_all_source_identities(self):
        first = install_download(self.path, self.data())
        second = install_download(self.path, self.data(module=502))
        self.assertEqual(second['material_id'], first['material_id'])
        row = self.read()['materials'][0]
        self.assertEqual({s['module_id'] for s in row['moodle_file_sources']}, {501, 502})
        before = self.path.read_bytes()
        again = install_download(self.path, self.data(module=502))
        self.assertEqual(again['status'], 'identical')
        self.assertEqual(before, self.path.read_bytes())

    def test_shared_source_changed_reports_conflict_and_keeps_originals(self):
        first = install_download(self.path, self.data())
        install_download(self.path, self.data(module=502))
        incoming = self.data(b'New version', module=501)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Shared material source changed'):
            install_download(self.path, incoming)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual((self.study / first['relative_path']).read_bytes(), b'First course body')
        self.assertEqual((self.study / incoming['relative_path']).read_bytes(), b'New version')

    def test_changed_single_source_archives_old_without_rewriting_evidence(self):
        first = install_download(self.path, self.data())
        state = self.read()
        state['mistakes'][0]['material_id'] = first['material_id']
        self.save(state)
        second = install_download(self.path, self.data(b'Updated original'))
        self.assertEqual(second['archived_id'], first['material_id'])
        state = self.read()
        old = next(m for m in state['materials'] if m['id'] == first['material_id'])
        self.assertEqual((self.study / old['relative_path']).read_bytes(), b'First course body')
        self.assertEqual(state['mistakes'][0]['material_id'], first['material_id'])

    def test_post_identity_avoids_same_forum_same_name_collision(self):
        first = install_download(self.path, self.data(post=1))
        second = install_download(self.path, self.data(b'Other post', post=2))
        self.assertNotEqual(first['relative_path'], second['relative_path'])
        self.assertIsNone(second['archived_id'])

    def test_permission_checked_at_install_not_only_at_download(self):
        for change in ('revoked', 'account', 'site', 'selection', 'duplicated'):
            with self.subTest(change=change):
                state = copy.deepcopy(self.state)
                grant = state['settings']['course_content_sync']['grants'][0]
                if change == 'revoked': grant['mode'] = 'metadata'
                if change == 'account': grant['user_id'] = 78
                if change == 'site': grant['site'] = 'https://different.example'
                if change == 'selection': state['courses'][0]['moodle_course_ids'] = []
                if change == 'duplicated': state['settings']['course_content_sync']['grants'].append(copy.deepcopy(grant))
                self.save(state)
                before = self.path.read_bytes()
                incoming = self.data()
                with self.assertRaises(ValueError): install_download(self.path, incoming)
                self.assertEqual(before, self.path.read_bytes())
                self.assertTrue((self.study / incoming['relative_path']).is_file())

    def test_personal_course_mapping_supported(self):
        state = copy.deepcopy(self.state)
        state['courses'][0].pop('moodle_course_ids')
        state['courses'][0]['moodle_progress'] = {'pages': [{'course_id': 100}]}
        self.save(state)
        result = install_download(self.path, self.data(module='course:100:section:2'))
        self.assertEqual(result['status'], 'promoted')

    def test_broken_pdf_retained_index_reports_error_without_claiming_review(self):
        data = self.data(b'%PDF-1.4 broken', filename='original.pdf')
        result = install_download(self.path, data)
        self.assertEqual((self.study / result['relative_path']).read_bytes(), b'%PDF-1.4 broken')
        before = self.path.read_bytes()
        report = index_course(self.path, 'A')
        self.assertEqual(report['materials'][0]['status'], 'extraction_error')
        self.assertFalse(report['reviewed'])
        self.assertFalse(report['mastery_updated'])
        self.assertEqual(before, self.path.read_bytes())

    def test_unknown_binary_saved_and_marked_not_text_indexed(self):
        install_download(self.path, self.data(b'opaque archive bytes', filename='original.zip'))
        report = index_course(self.path, 'A')
        self.assertEqual(report['status_counts'], {'not_text_indexed': 1})

    def test_bulk_install_never_runs_cleanup(self):
        with patch.object(versions, 'cleanup', side_effect=AssertionError('must not delete archives')):
            install_download(self.path, self.data())

    def test_missing_original_restored_to_same_path_and_identity(self):
        first = install_download(self.path, self.data())
        target = self.study / first['relative_path']
        target.unlink()
        state = self.read()
        state['mistakes'][0]['material_id'] = first['material_id']
        self.save(state)
        before = self.path.read_bytes()
        result = install_download(self.path, self.data())
        self.assertEqual(result['status'], 'restored')
        self.assertEqual(result['material_id'], first['material_id'])
        self.assertEqual(result['relative_path'], first['relative_path'])
        self.assertEqual(target.read_bytes(), b'First course body')
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(index_course(self.path, 'A')['status_counts'], {'indexed': 1})

    def test_local_modified_file_is_not_overwritten(self):
        result = install_download(self.path, self.data())
        local = self.study / result['relative_path']
        local.write_bytes(b'My local annotation')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'modified'):
            install_download(self.path, self.data(b'New school version'))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(local.read_bytes(), b'My local annotation')

    def test_missing_old_original_accepts_new_version_without_rewriting_history(self):
        first = install_download(self.path, self.data())
        state = self.read()
        old = copy.deepcopy(state['materials'][0])
        state['mistakes'][0].update(material_id=old['id'], source=old['relative_path'])
        self.save(state)
        note = self.study / 'A/notes.md'
        note.write_text('[original](' + old['relative_path'] + ')', encoding='utf-8')
        original_note = note.read_bytes()
        self.assertEqual(index_course(self.path, 'A')['status_counts'], {'indexed': 1})
        missing = self.study / old['relative_path']
        missing.unlink()
        second = install_download(self.path, self.data(b'Updated original'))
        after = self.read()
        historical = next(m for m in after['materials'] if m['id'] == old['id'])
        for key in ('id', 'sha256', 'relative_path', 'moodle_file_sources'):
            self.assertEqual(historical[key], old[key])
        self.assertEqual(historical['version_status'], 'missing')
        self.assertEqual(historical['superseded_by'], second['material_id'])
        self.assertIn('missing_detected_at', historical)
        self.assertNotIn('archived_at', historical)
        self.assertNotIn('archive_sha256', historical)
        self.assertIsNone(second['archived_id'])
        self.assertEqual(second['missing_original_id'], old['id'])
        self.assertEqual(after['mistakes'], state['mistakes'])
        self.assertEqual(after['courses'], state['courses'])
        self.assertEqual(note.read_bytes(), original_note)
        self.assertFalse(missing.exists())
        self.assertNotEqual(second['relative_path'], old['relative_path'])
        self.assertEqual((self.study / second['relative_path']).read_bytes(), b'Updated original')
        self.assertEqual(index_course(self.path, 'A')['status_counts'], {'indexed': 1})
        from library import Library
        with self.assertRaisesRegex(ValueError, 'unavailable|stale'):
            Library(self.path).read(old['id'], include_archived=True)
        # A later ordinary update must not put different bytes at a historical missing path.
        third = install_download(self.path, self.data(b'Third original'))
        self.assertFalse(missing.exists())
        self.assertEqual(third['archived_id'], second['material_id'])
        self.assertEqual(next(m for m in self.read()['materials'] if m['id'] == old['id']), historical)
        before = self.path.read_bytes()
        self.assertEqual(install_download(self.path, self.data(b'Third original'))['status'], 'identical')
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_update_requires_one_matching_account_bound_source(self):
        first = install_download(self.path, self.data())
        (self.study / first['relative_path']).unlink()
        original = self.read()
        for change in ('site', 'account', 'page', 'unbound', 'multiple', 'different_module'):
            with self.subTest(change=change):
                state = copy.deepcopy(original)
                source = state['materials'][0]['moodle_file_sources'][0]
                if change == 'site': source['site'] = 'https://other.example'
                if change == 'account': source['user_id'] = 78
                if change == 'page': source['page_id'] = 101
                if change == 'unbound': source.pop('site')
                if change == 'multiple':
                    state['materials'][0]['moodle_file_sources'].append({**source, 'module_id': 999})
                self.save(state)
                incoming = self.data(b'New bytes', module=999 if change == 'different_module' else 501)
                incoming['old_id'] = first['material_id']
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    install_download(self.path, incoming)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertTrue((self.study / incoming['relative_path']).is_file())

    def test_missing_update_transaction_failure_restores_staged_file_and_state(self):
        first = install_download(self.path, self.data())
        (self.study / first['relative_path']).unlink()
        incoming = self.data(b'Updated original')
        before = self.path.read_bytes()
        write = versions._write
        def fail_state(path, value):
            if Path(path) == self.path:
                raise OSError('simulated state write failure')
            return write(path, value)
        with patch.object(versions, '_write', side_effect=fail_state):
            with self.assertRaisesRegex(OSError, 'simulated'):
                install_download(self.path, incoming)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual((self.study / incoming['relative_path']).read_bytes(), b'Updated original')
        self.assertFalse((self.study / first['relative_path']).exists())


    def test_ensure_index_reuses_valid_payload_without_rebuilding_or_writing(self):
        from library import Library
        item = install_download(self.path, self.data())
        index_course(self.path, 'A', [item['material_id']])
        catalog = self.study / '_System/Library/catalog.json'
        before = catalog.read_bytes(), self.path.read_bytes()
        with patch.object(Library, 'sync', side_effect=AssertionError('valid index must not be rebuilt')):
            result = index_course(self.path, 'A', [item['material_id']], ensure=True)
        self.assertEqual(result['status_counts'], {'indexed': 1})
        self.assertEqual(result['extracted'], 0)
        self.assertEqual(result['reused'], 1)
        self.assertEqual((catalog.read_bytes(), self.path.read_bytes()), before)

    def test_ensure_index_repairs_only_selected_payload_and_keeps_other_index(self):
        from learning_library.extract import extract_document
        first = install_download(self.path, self.data())
        second = install_download(self.path, self.data(b'Other original', module=502, filename='other.txt'))
        index_course(self.path, 'A')
        catalog = self.study / '_System/Library/catalog.json'
        rows = json.loads(catalog.read_bytes())['materials']
        first_row = next(m for m in rows if m['id'] == first['material_id'])
        other_row = next(m for m in rows if m['id'] == second['material_id'])
        cache_root = catalog.parent
        other_bytes = (cache_root / other_row['cache']).read_bytes()
        (cache_root / first_row['cache']).unlink()
        before = self.path.read_bytes()
        with patch('learning_library.extract.extract_document', wraps=extract_document) as extract:
            result = index_course(self.path, 'A', [first['material_id']], ensure=True)
        self.assertEqual(result['extracted'], 1)
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(Path(extract.call_args.args[0]), self.study / first['relative_path'])
        self.assertEqual((cache_root / other_row['cache']).read_bytes(), other_bytes)
        self.assertEqual(next(m for m in json.loads(catalog.read_bytes())['materials'] if m['id'] == second['material_id']), other_row)
        self.assertEqual(self.path.read_bytes(), before)



if __name__ == '__main__':
    unittest.main()
