"""The directly edited mistake bank must survive all generated-view operations."""
import json
from pathlib import Path
import tempfile
import unittest

import daily
import test_daily
from learning_library import versions
import course_content


class MistakeBankPreservationTests(unittest.TestCase):
    def test_refresh_preserves_notes_and_does_not_import_them_as_mastery(self):
        fixture = test_daily.DailyIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with tempfile.TemporaryDirectory() as folder:
            study = Path(folder) / 'Study'
            state_path = study / '_System' / 'state.json'
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps(fixture.state), encoding='utf-8')
            bank = study / '错题库.md'
            original = '\ufeff# 错题库\r\n\r\n## 真实原答\r\n答：0；更正：待确认。\r\n'.encode('utf-8')
            bank.write_bytes(original)
            modified = bank.stat().st_mtime_ns
            for command in ('run', 'render', 'check', 'run'):
                daily.execute(state_path, command, fixture.now)
                self.assertEqual(bank.read_bytes(), original)
                self.assertEqual(bank.stat().st_mtime_ns, modified)
            saved = json.loads(state_path.read_text(encoding='utf-8'))
            for key in ('mistakes', 'courses'):
                self.assertEqual(saved.get(key), fixture.state.get(key))
            self.assertEqual(saved.get('review_attempts', []), [])

    def test_bank_cannot_be_replaced_or_restored_as_course_material(self):
        with tempfile.TemporaryDirectory() as folder:
            study = Path(folder)
            bank = study / '错题库.md'
            bank.write_text('# 原答', encoding='utf-8')
            row = {'id': 'INVALID', 'relative_path': bank.name, 'sha256': versions.sha(bank)}
            with self.assertRaisesRegex(ValueError, 'Learning records'):
                versions.verify(study, row)
            staged = study / 'staged.md'
            bank.rename(staged)
            with self.assertRaisesRegex(ValueError, 'learning records'):
                course_content.restore_missing(study, row, staged)
            self.assertFalse(bank.exists())
            self.assertEqual(staged.read_text(encoding='utf-8'), '# 原答')


if __name__ == '__main__':
    unittest.main()
