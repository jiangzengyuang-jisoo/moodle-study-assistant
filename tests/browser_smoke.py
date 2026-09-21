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
        passport = parse_qs(urlsplit(self.path).query).get('passport', [''])[0]
        digest = hashlib.md5((site + passport).encode()).hexdigest()
        payload = base64.b64encode((digest + ':::synthetic123:::discard').encode()).decode()
        self.send_response(302)
        self.send_header('Location', 'moodlestudy://token=' + payload)
        self.end_headers()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
site = f'http://127.0.0.1:{server.server_port}'
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
            assert auth.browser_token(site, 'msedge') == 'synthetic123'
    print('PASS: official redirect interception and session-bound callback parsing, local mock only')
finally:
    server.shutdown()
    server.server_close()
