"""Shared writer regressions: synthetic files only, including injected failures."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import state_io
import library
import review
from learning_library import versions


class AtomicIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / '页面.md'
        self.path.write_bytes(b'original')

    def test_text_bytes_json_and_compatibility_aliases(self):
        self.assertIs(review.atomic_text, state_io.atomic_text)
        state_io.atomic_text(self.path, '中文\nnext\n')
        self.assertEqual(self.path.read_bytes(), '中文\nnext\n'.encode())
        versions._write(self.path, b'\xef\xbb\xbfkeep\r\n')
        self.assertEqual(self.path.read_bytes(), b'\xef\xbb\xbfkeep\r\n')
        target = self.root / 'nested' / 'cache.json'
        library.atomic_json(target, {'text': '中文'})
        self.assertEqual(json.loads(target.read_bytes()), {'text': '中文'})

    def test_unchanged_text_keeps_mtime_and_legacy_newlines(self):
        for data in (b'hello\n', b'hello\r\n', b'\xef\xbb\xbfhello\r\n'):
            self.path.write_bytes(data)
            before = self.path.stat().st_mtime_ns
            with patch.object(state_io.os, 'replace', side_effect=AssertionError('unneeded replace')):
                self.assertFalse(state_io.atomic_text(self.path, 'hello\n', skip_unchanged=True))
            self.assertEqual(self.path.read_bytes(), data)
            self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_invalid_json_and_nonfinite_serialization_preserve_original(self):
        target = self.root / 'state.json'
        target.write_bytes(b'{"old":1}')
        for write in (lambda: state_io.atomic_text(target, '{broken'),
                      lambda: state_io.atomic(target, {'bad': float('nan')})):
            with self.assertRaises(ValueError):
                write()
            self.assertEqual(target.read_bytes(), b'{"old":1}')
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_replace_failure_keeps_original_and_only_cleans_owned_temp(self):
        other = self.root / 'other.tmp'
        other.write_bytes(b'not ours')
        def failed_replace(source, target):
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertEqual(Path(target), self.path)
            raise OSError('replace failure')
        with patch.object(state_io.os, 'replace', side_effect=failed_replace):
            with self.assertRaises(OSError):
                state_io.atomic_text(self.path, 'changed')
        self.assertEqual(self.path.read_bytes(), b'original')
        self.assertEqual(list(self.root.glob('*.tmp')), [other])

    def test_fsync_failure_preserves_original(self):
        with patch.object(state_io.os, 'fsync', side_effect=OSError('flush failure')):
            with self.assertRaises(OSError):
                state_io.atomic_text(self.path, 'changed')
        self.assertEqual(self.path.read_bytes(), b'original')
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_flush_precedes_replace(self):
        events = []
        real_fsync, real_replace = os.fsync, os.replace
        def fsync(fd):
            events.append('flush')
            return real_fsync(fd)
        def replace(source, target):
            events.append('replace')
            return real_replace(source, target)
        with patch.object(state_io.os, 'fsync', side_effect=fsync), patch.object(state_io.os, 'replace', side_effect=replace):
            state_io.atomic_bytes(self.path, b'new')
        self.assertEqual(events, ['flush', 'replace'])


if __name__ == '__main__':
    unittest.main()
