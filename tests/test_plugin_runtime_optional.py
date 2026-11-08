"""The minimal plugin runtime must initialize even when optional tools are absent.

core-requirements.txt intentionally installs the small engine runtime only:
fsrs and the document readers. The mathematical verification package sympy is
optional (used by math_check.py), so review initialization must record a
missing version instead of failing the whole first-use initialization chain.
"""
import sys
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/study'))

import review  # noqa: E402


class OptionalPackageInitializationTest(unittest.TestCase):
    def test_missing_optional_sympy_does_not_block_review_initialization(self):
        state = {'settings': {}, 'integrations': {}}

        def fake_version(name):
            if name == 'sympy':
                raise PackageNotFoundError(name)
            return 'test-' + name

        with mock.patch.object(review, 'version', side_effect=fake_version):
            review.initialize(state)

        study_tools = state['integrations']['study_tools']
        self.assertIsNone(study_tools['sympy_version'])
        self.assertEqual(study_tools['fsrs_version'], 'test-fsrs')
        self.assertEqual(study_tools['status'], 'configured')
        self.assertIn('spaced_repetition', state['settings'])

    def test_present_optional_packages_are_still_recorded(self):
        state = {'settings': {}, 'integrations': {}}

        with mock.patch.object(review, 'version', side_effect=lambda name: 'v-' + name):
            review.initialize(state)

        study_tools = state['integrations']['study_tools']
        self.assertEqual(study_tools['sympy_version'], 'v-sympy')
        self.assertEqual(study_tools['fsrs_version'], 'v-fsrs')


if __name__ == '__main__':
    unittest.main()
