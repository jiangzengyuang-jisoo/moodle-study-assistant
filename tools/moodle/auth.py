"""Windows-only official API authentication; never reads or exports browser cookies."""
import argparse
import base64
import getpass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from urllib.parse import urlencode, urlsplit, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[2]


def parse_callback(value, site, passport):
    # Official Moodle mobile launch protocol. Bind the response to this site/session.
    match = re.fullmatch(r'[a-zA-Z][a-zA-Z0-9+\-]*://token=([A-Za-z0-9+/=%]+)', value)
    if not match or value.lower().startswith(('http:', 'https:')):
        raise ValueError('无效的官方登录回调')
    try:
        parts = base64.b64decode(unquote(match[1]), validate=True).decode('utf-8').split(':::')
    except (ValueError, UnicodeError):
        raise ValueError('无法解析官方登录回调') from None
    expected = hashlib.md5((site + passport).encode()).hexdigest()
    if len(parts) not in (2, 3) or not secrets.compare_digest(parts[0], expected):
        raise ValueError('登录回调与本次学校/授权请求不匹配')
    if not re.fullmatch(r'[A-Za-z0-9]+', parts[1]):
        raise ValueError('API Token 格式无效')
    return parts[1]


def find_callback(body):
    for value in re.findall(r'href=["\']([^"\']+)["\']', body, flags=re.I):
        value = html.unescape(value)
        if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9+\-]*://token=[A-Za-z0-9+/=%]+', value):
            if not value.lower().startswith(('http:', 'https:')):
                return value
    return None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_token(site, token):
    body = urlencode({'wstoken': token, 'wsfunction': 'core_webservice_get_site_info', 'moodlewsrestformat': 'json'}).encode()
    try:
        with build_opener(NoRedirect).open(Request(site + '/webservice/rest/server.php', data=body), timeout=30) as response:
            data = json.load(response)
        if data.get('exception') or not isinstance(data.get('userid'), int) or data['userid'] <= 0:
            raise ValueError()
    except Exception:
        raise ValueError('Token 验证失败：检查学校地址、Token 有效期和移动服务/API 权限。原有凭据未改动。') from None
    return data


def site_key(value):
    """Normalize a school HTTPS URL the same way the Node tools do."""
    parts = urlsplit(str(value or ''))
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment):
        raise ValueError('无效的学校地址')
    try:
        port = parts.port
    except ValueError:
        raise ValueError('无效的学校地址') from None
    host = parts.hostname.encode('idna').decode().lower()
    if ':' in host:
        host = '[' + host + ']'
    authority = host + (':' + str(port) if port and port != 443 else '')
    return ('https://' + authority + parts.path).rstrip('/')


def profile_accounts(root=None):
    """Read-only record of which school/account this Study profile has used.

    The same profile folder must not silently switch school or student account:
    numeric course/module IDs are only meaningful inside the account that created
    them. Missing legacy account information is never guessed or back-filled.
    """
    root = Path(root) if root is not None else ROOT
    accounts = []
    state_path = root / 'Study/_System/state.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        state = {}
    settings = state.get('settings') if isinstance(state.get('settings'), dict) else {}

    def add(value, source):
        if not isinstance(value, dict):
            return
        try:
            site = site_key(value.get('site'))
        except (ValueError, TypeError):
            return
        user = value.get('user_id')
        accounts.append({'site': site, 'user_id': user if type(user) is int and user > 0 else None,
                         'source': source})

    add(settings.get('profile_account_scope'), 'profile_account_scope')
    authorization = settings.get('course_library_authorization')
    if isinstance(authorization, dict) and isinstance(authorization.get('courses'), dict):
        for grant in authorization['courses'].values():
            if isinstance(grant, dict) and 'account_scope' in grant:
                add(grant.get('account_scope'), 'course_library_authorization')
    policy = settings.get('course_content_sync')
    if isinstance(policy, dict) and policy.get('version') == 1 and isinstance(policy.get('grants'), list):
        for grant in policy['grants']:
            if isinstance(grant, dict):
                add({'site': grant.get('site'), 'user_id': grant.get('user_id')}, 'course_content_sync')
    for name in ('refresh-latest.json', 'refresh-last-complete.json'):
        try:
            snapshot = json.loads((root / 'Study/_System' / name).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
        if not isinstance(snapshot, dict):
            continue
        add(snapshot.get('account_scope'), name)
        add({'site': snapshot.get('moodle_url'), 'user_id': None}, name)
    unique = []
    for account in accounts:
        key = (account['site'], account['user_id'])
        if all((row['site'], row['user_id']) != key for row in unique):
            unique.append(account)
    return unique


def assert_connect_identity(site, user_id=None, root=None):
    """Refuse to attach a different school/account to this existing profile."""
    normalized = site_key(site)
    accounts = profile_accounts(root)
    sites = sorted({account['site'] for account in accounts})
    if not sites:
        return
    if len(sites) > 1:
        raise ValueError('学习档案中已存在来自不同学校的记录（' + '、'.join(sites)
                         + '），无法安全确定数字课程/模块 ID 属于哪所学校；请恢复备份，或在独立目录使用独立档案。原有凭据未改动。')
    if sites[0] != normalized:
        raise ValueError('同一学习档案此前用于另一所学校（' + sites[0] + '），当前学校是 ' + normalized
                         + '。数字课程/模块 ID 在不同学校可能指向不同课程；换学校请复制/新建独立项目目录并使用独立学习档案，'
                           '重新 setup 和登录，不要在同一 Study 档案来回切换。原有凭据未改动。')
    known_users = {account['user_id'] for account in accounts if account['user_id'] is not None}
    if type(user_id) is int and user_id > 0 and known_users and user_id not in known_users:
        raise ValueError('同一学习档案此前使用的是同一学校的另一个账号，当前登录账号不同；'
                         '请恢复原账号，或为当前账号使用独立目录和独立档案。原有凭据未改动。')


def store_token(token):
    if sys.platform != 'win32':
        raise ValueError('此版本凭据保护仅支持 Windows')
    path = ROOT / '.private' / 'moodle-token.dpapi'
    path.parent.mkdir(parents=True, exist_ok=True)
    target = str(path).replace("'", "''")
    # stdin only: the token never appears in process arguments or a plaintext file.
    script = ("Add-Type -AssemblyName System.Security; $s=[Console]::In.ReadToEnd(); "
              "$b=[Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($s),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser); "
              f"[IO.File]::WriteAllBytes('{target}.tmp',$b); Move-Item -LiteralPath '{target}.tmp' -Destination '{target}' -Force")
    shell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', script],
                            input=token.encode('utf-8'), capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError('无法保存本机加密 Token')


def is_moodle_landing(current_url, site):
    """Only resume on this Moodle site's home/dashboard, never an identity provider."""
    current, expected = urlsplit(current_url), urlsplit(site)
    base = expected.path.rstrip('/')
    return (current.scheme == expected.scheme and current.netloc.lower() == expected.netloc.lower()
            and current.path in {base, base + '/', base + '/index.php',
                                 base + '/my/', base + '/my/index.php'})


def browser_token(site, channel):
    from playwright.sync_api import sync_playwright
    passport = str(secrets.randbelow(900000000) + 100000000)
    launch = site + '/admin/tool/mobile/launch.php'
    url = launch + '?' + urlencode({'service': 'moodle_mobile_app', 'passport': passport, 'urlscheme': 'moodlestudy'})
    result = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, headless=False)
        context = browser.new_context()
        page = context.new_page()

        def intercept(route):
            try:
                response = route.fetch(max_redirects=0)
                callback = response.headers.get('location', '')
                if not re.match(r'[a-zA-Z][a-zA-Z0-9+\-]*://token=', callback):
                    callback = find_callback(response.text())
                if callback:
                    result['token'] = parse_callback(callback, site, passport)
                    route.fulfill(status=200, content_type='text/html', body='<h2>Connected. You may close this window.</h2>')
                else:
                    route.fulfill(response=response)
            except Exception:
                result['error'] = True
                route.fulfill(status=400, body='Authentication callback failed. Return to the terminal.')

        # Only the school's official launch endpoint is intercepted; SSO credentials remain in the browser.
        context.route(launch + '?*', intercept)
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
            print('请在弹出的学校网页完成登录和验证。如提示打开 App，请先完成学校页面上的确认。', flush=True)
            deadline = time.monotonic() + 600
            resumed = False
            landing_ticks = 0
            while not result and time.monotonic() < deadline and not page.is_closed():
                page.wait_for_timeout(250)
                if result or page.is_closed():
                    break
                landing_ticks = landing_ticks + 1 if is_moodle_landing(page.url, site) else 0
                if landing_ticks < 2:
                    continue
                if resumed:
                    raise ValueError('学校页面仍返回首页，未完成 API 授权；已停止自动重试。请检查学校移动服务授权是否可用。')
                # SSO can lose the original return URL (including after a manual refresh).
                # Reuse this browser context and the original passport; never read cookies.
                resumed = True
                landing_ticks = 0
                print('检测到学校首页，正在继续本次 API 授权，请保留此窗口。', flush=True)
                page.goto(url, wait_until='domcontentloaded', timeout=60000)
            if 'token' not in result:
                raise ValueError('未收到有效授权；可重试，或使用 connect.cmd --manual 输入官方 Token。')
            return result['token']
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manual', action='store_true', help='输入学校提供的官方 Token，不显示输入')
    parser.add_argument('--browser', choices=['msedge', 'chrome'], default='msedge')
    args = parser.parse_args()
    config = json.loads((ROOT / 'config.local.json').read_text(encoding='utf-8-sig'))
    site = config['moodle_url'].rstrip('/')
    url = urlsplit(site)
    if url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError('config.local.json 需要干净的学校 HTTPS 地址')
    print('学校地址：' + site, flush=True)
    # Reject a detected school switch before touching the stored credential.
    assert_connect_identity(site)
    token = getpass.getpass('粘贴官方 API Token（输入隐藏；不要粘贴密码）：').strip() if args.manual else browser_token(site, args.browser)
    if not re.fullmatch(r'[A-Za-z0-9]+', token):
        raise ValueError('Token 格式无效')
    info = verify_token(site, token)
    assert_connect_identity(site, info.get('userid'))
    store_token(token)
    print('Moodle API 已验证并加密保存。请回到当前 Harness 聊天，助手会接着列出课程，让你在聊天中选择并讲解系统用法。')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except KeyboardInterrupt:
        print('已取消，未完成授权。', file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        # Browser/network error strings may contain an authorization URL; never print them.
        message = str(error) if isinstance(error, ValueError) else '连接未完成。确认已安装 Edge/Chrome、网络可用，并查看 docs/新手使用指南.md。'
        print(message, file=sys.stderr)
        sys.exit(1)
