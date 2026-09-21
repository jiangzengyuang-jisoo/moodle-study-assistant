import base64
import hashlib
import importlib.util
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
