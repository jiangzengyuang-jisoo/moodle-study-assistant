"""Shared writes preserve files on failure and avoid replacing unchanged views."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import library
import planning
import review
import state_io
from learning_library import versions


class AtomicWriteTests(unittest.TestCase):
    def test_all_writers_preserve_original_and_remove_temporary_file_on_failure(self):
        writers = [lambda p: state_io.atomic(p, {'text': '新内容'}),
                   lambda p: library.atomic_json(p, {'text': '新内容'}),
                   lambda p: review.atomic_text(p, '{"text":"新内容"}'),
                   lambda p: versions._write(p, b'{"text":"new"}'),
                   lambda p: planning.write_views({p.name: '{"text":"new"}'}, p.parent)]
        for writer in writers:
            with self.subTest(writer=writer), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'data.json'
                original = b'\xef\xbb\xbf{"text":"original"}\r\n'
                path.write_bytes(original)
                with patch.object(state_io.os, 'replace', side_effect=OSError('injected failure')):
                    with self.assertRaises(OSError):
                        writer(path)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_invalid_json_never_replaces_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            path.write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                state_io.atomic_text(path, 'invalid')
            self.assertEqual(path.read_text(), '{}')
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_skip_unchanged_does_not_skip_changed_unicode_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'page.md'
            self.assertTrue(state_io.atomic_text(path, '原页\n', skip_unchanged=True))
            stamp = path.stat().st_mtime_ns
            with patch.object(state_io.os, 'replace', side_effect=AssertionError('unnecessary write')):
                self.assertFalse(state_io.atomic_text(path, '原页\n', skip_unchanged=True))
            self.assertEqual(path.stat().st_mtime_ns, stamp)
            self.assertTrue(state_io.atomic_text(path, '新版\n', skip_unchanged=True))
            self.assertEqual(path.read_text(encoding='utf-8'), '新版\n')


if __name__ == '__main__':
    unittest.main()
