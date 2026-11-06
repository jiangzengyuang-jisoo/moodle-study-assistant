import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
import sys
import tempfile
import subprocess
import os
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('auth', Path(__file__).resolve().parents[1] / 'tools/moodle/auth.py')
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)

SITE = 'https://school.example'


def write_profile(root, *, state=None, snapshot=None):
    system = Path(root) / 'Study/_System'
    system.mkdir(parents=True, exist_ok=True)
    if state is not None:
        (system / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    if snapshot is not None:
        (system / 'refresh-latest.json').write_text(json.dumps(snapshot), encoding='utf-8')


def bound_state(site=SITE, user=17):
    return {
        'settings': {'course_library_authorization': {'schema': 1, 'courses': {
            'moodle-8': {'moodle_courseid': 8, 'mode': 'full',
                         'account_scope': {'site': site, 'user_id': user}}}}},
        'courses': [{'id': 'moodle-8', 'moodle_course_ids': [8]}],
    }


class AuthProfileIdentityTests(unittest.TestCase):
    """One folder keeps one school account; numeric IDs are account-local."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='study auth profile ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_fresh_profile_and_same_account_reconnect_are_allowed(self):
        auth.assert_connect_identity(SITE, root=self.root)  # nothing recorded yet
        write_profile(self.root, state=bound_state())
        auth.assert_connect_identity(SITE, root=self.root)  # site matches before login
        auth.assert_connect_identity(SITE, 17, root=self.root)  # same student account

    def test_other_school_is_refused_before_the_credential_is_replaced(self):
        write_profile(self.root, state=bound_state())
        with self.assertRaises(ValueError) as error:
            auth.assert_connect_identity('https://other.example', root=self.root)
        self.assertIn('另一所学校', str(error.exception))
        self.assertIn('独立', str(error.exception))
        with self.assertRaises(ValueError):
            auth.assert_connect_identity('https://other.example', 17, root=self.root)

    def test_another_account_at_the_same_school_is_refused(self):
        write_profile(self.root, state=bound_state())
        with self.assertRaises(ValueError) as error:
            auth.assert_connect_identity(SITE, 18, root=self.root)
        self.assertIn('另一个账号', str(error.exception))
        self.assertNotIn('18', str(error.exception))  # no account IDs echoed

    def test_legacy_snapshot_site_evidence_still_blocks_a_switch(self):
        write_profile(self.root, snapshot={'moodle_url': SITE,
                                           'account_scope': {'site': SITE, 'user_id': 17}})
        auth.assert_connect_identity(SITE, 17, root=self.root)
        with self.assertRaises(ValueError):
            auth.assert_connect_identity('https://other.example', root=self.root)
        # A snapshot without a user ID still records the school.
        write_profile(self.root, snapshot={'moodle_url': SITE})
        auth.assert_connect_identity(SITE, 17, root=self.root)
        with self.assertRaises(ValueError):
            auth.assert_connect_identity('https://other.example', root=self.root)

    def test_mixed_profile_is_not_guessed(self):
        write_profile(self.root, state=bound_state(),
                      snapshot={'moodle_url': 'https://legacy.example'})
        with self.assertRaises(ValueError) as error:
            auth.assert_connect_identity(SITE, root=self.root)
        self.assertIn('不同学校', str(error.exception))


class AuthTests(unittest.TestCase):
    def test_official_callback_checks_site_and_passport_and_discards_private_token(self):
        site, passport = 'https://school.example', '123456'
        digest = hashlib.md5((site + passport).encode()).hexdigest()
        encoded = base64.b64encode(f'{digest}:::abc123:::discardme'.encode()).decode()
        callback = 'moodlemobile://token=' + encoded
        self.assertEqual(auth.parse_callback(callback, site, passport), 'abc123')
        with self.assertRaises(ValueError): auth.parse_callback(callback, site, 'wrong')
        with self.assertRaises(ValueError): auth.parse_callback('https://evil.example/?token=' + encoded, site, passport)
        with self.assertRaises(ValueError): auth.parse_callback('moodlestudy://token=invalid', site, passport)

    def test_only_complete_callback_links_are_extracted(self):
        self.assertEqual(auth.find_callback('<a id="launchapp" href="moodlestudy://token=YWJj">Open</a>'), 'moodlestudy://token=YWJj')
        self.assertIsNone(auth.find_callback('<p>token=abc</p>'))

    def test_resume_only_matches_this_sites_landing_pages(self):
        site = 'https://school.example/moodle'
        for suffix in ('', '/', '/index.php', '/my/', '/my/index.php'):
            self.assertTrue(auth.is_moodle_landing(site + suffix, site))
        for url in ('https://login.example/', 'http://school.example/moodle/',
                    'https://school.example/moodle/login/index.php',
                    'https://school.example/moodle/course/view.php?id=1'):
            self.assertFalse(auth.is_moodle_landing(url, site))

    @unittest.skipUnless(sys.platform == 'win32', 'DPAPI is Windows-only')
    def test_dpapi_stores_ciphertext_and_round_trips_without_plaintext_file(self):
        with tempfile.TemporaryDirectory(prefix='study auth ') as directory:
            root = Path(directory)
            with patch.object(auth, 'ROOT', root):
                auth.store_token('synthetic456')
            path = root / '.private/moodle-token.dpapi'
            self.assertNotIn(b'synthetic456', path.read_bytes())
            self.assertEqual(len(list(path.parent.iterdir())), 1)
            target = str(path).replace("'", "''")
            script = ("Add-Type -AssemblyName System.Security; [Console]::Out.Write([Text.Encoding]::UTF8.GetString("
                      f"[Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes('{target}'),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)))")
            shell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, 'synthetic456')

if __name__ == '__main__': unittest.main()
