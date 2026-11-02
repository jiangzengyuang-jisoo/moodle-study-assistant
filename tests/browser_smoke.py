"""Optional local-only browser test. No school account or credentials are used."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import threading
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location('auth', Path(__file__).resolve().parents[1] / 'tools/moodle/auth.py')
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == '/index.php':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<h1>Mock Moodle home after SSO</h1>')
            return
        passport = parse_qs(parsed.query).get('passport', [''])[0]
        server.passports.append(passport)
        if server.mode == 'always-home' or (server.mode == 'home-once' and len(server.passports) == 1):
            self.send_response(302)
            self.send_header('Location', '/index.php')
            self.end_headers()
            return
        digest = hashlib.md5((site + passport).encode()).hexdigest()
        payload = base64.b64encode((digest + ':::synthetic123:::discard').encode()).decode()
        self.send_response(302)
        self.send_header('Location', 'moodlestudy://token=' + payload)
        self.end_headers()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
site = f'http://127.0.0.1:{server.server_port}'
server.mode = 'direct'
server.passports = []
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    with sync_playwright() as playwright:
        launch = playwright.chromium.launch

        def headless(**kwargs):
            return launch(**dict(kwargs, headless=True))

        class Context:
            def __enter__(self): return playwright
            def __exit__(self, *args): pass

        with patch('playwright.sync_api.sync_playwright', return_value=Context()), patch.object(playwright.chromium, 'launch', side_effect=headless):
            for mode in ('direct', 'home-once', 'always-home'):
                server.mode, server.passports = mode, []
                if mode == 'always-home':
                    try:
                        auth.browser_token(site, 'chrome')
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('Repeated homepage must fail instead of looping')
                else:
                    assert auth.browser_token(site, 'chrome') == 'synthetic123'
                expected_count = 1 if mode == 'direct' else 2
                assert len(server.passports) == expected_count
                assert len(set(server.passports)) == 1, 'Resume must preserve the original passport'
                print('PASS: ' + mode)

    print('PASS: official redirect interception and session-bound callback parsing, local mock only')
finally:
    server.shutdown()
    server.server_close()
