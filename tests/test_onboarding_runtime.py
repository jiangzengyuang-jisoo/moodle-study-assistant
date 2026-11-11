"""Real Windows bootstrap/setup recovery; dependency downloads are disabled.

Only Node/npm and post-install study commands are fixture substitutes. Python,
venv, pip/ensurepip, PowerShell and the shipped setup/launcher are executed.

Real CLI/PowerShell tests always point DSH_HOME at an isolated temporary
directory and strip inherited DSH_PROFILE_DIR/DSH_PROFILE/DSH_SESSION_ID/
STUDY_CHAT_ID values, so no real Harness profile is read or written.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SHELL = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def _restricted_windows_token():
    """Detect the restricted token used by the Windows file sandbox.

    A restricted token cannot create or clean up ``tempfile`` directories, so
    ``python -m venv`` fails inside ``ensurepip``. This environment therefore
    needs a pip-enabled ``.venv`` prepared without ensurepip, while the
    production setup.ps1 still runs unchanged and offline. On a normal
    terminal the answer is False and the bootstrap path is untouched.
    """
    if os.name != 'nt':
        return False
    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.IsTokenRestricted.argtypes = [wintypes.HANDLE]
        advapi32.IsTokenRestricted.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008,
                                         ctypes.byref(token)):
            return False
        try:
            return bool(advapi32.IsTokenRestricted(token))
        finally:
            kernel32.CloseHandle(token)
    except (AttributeError, OSError):
        return False


RESTRICTED_TOKEN = _restricted_windows_token()


class IsolatedDirectory:
    """Temporary test root created with the default inherited permissions.

    ``tempfile`` creates directories with mode 0o700. Under the restricted
    Windows sandbox token used while developing this repository, such a
    directory does not carry the sandbox write grant, so fixtures cannot be
    created inside it and cleanup fails. Creating the directory like any other
    file keeps the inherited permissions; cleanup stays explicit.
    """

    def __init__(self, prefix, parent):
        directory = Path(parent)
        directory.mkdir(parents=True, exist_ok=True)
        for _ in range(100):
            candidate = directory / (prefix + uuid.uuid4().hex[:10])
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            self.name = str(candidate)
            return
        raise RuntimeError('无法创建隔离测试目录：' + str(directory))

    def cleanup(self):
        shutil.rmtree(self.name, ignore_errors=True)


@unittest.skipUnless(os.name == 'nt', 'Windows setup')
class RuntimeRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        cls.compiled = IsolatedDirectory('runtime compilers ', ROOT / 'tmp')
        cls.addClassCleanup(cls.compiled.cleanup)
        cls.bin = Path(cls.compiled.name)
        compiler = cls.bin / 'compile.ps1'
        compiler.write_text('''$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition 'public class OldPython { public static int Main(string[] a) { return 1; } }' -OutputAssembly (Join-Path $PSScriptRoot 'py.exe') -OutputType ConsoleApplication
Add-Type -TypeDefinition 'public class LocalNode { public static int Main(string[] a) { if(a.Length>0 && a[0]=="-p") System.Console.WriteLine("22"); return 0; } }' -OutputAssembly (Join-Path $PSScriptRoot 'node.exe') -OutputType ConsoleApplication
Add-Type -TypeDefinition 'public class BrokenPip { public static int Main(string[] a) { return System.Array.IndexOf(a,"-c") >= 0 ? 0 : 1; } }' -OutputAssembly (Join-Path $PSScriptRoot 'broken-pip.exe') -OutputType ConsoleApplication
''', encoding='utf-8-sig')
        result = subprocess.run([str(SHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(compiler)], capture_output=True, encoding='utf-8', timeout=45)
        if result.returncode:
            raise AssertionError(result.stderr)

    def setUp(self):
        self.temp = IsolatedDirectory('恢复 中文 ', ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        target = self.root / 'tools/onboarding'
        target.mkdir(parents=True)
        for name in ('start.ps1', 'entry.py', 'python-runtime.ps1'):
            shutil.copyfile(ROOT / 'tools/onboarding' / name, target / name)
        shutil.copyfile(ROOT / 'setup.ps1', self.root / 'setup.ps1')
        (self.root / 'client-target.json').write_text('{"version":1,"client":"codex"}', encoding='utf-8')
        (self.root / 'requirements.txt').write_text('', encoding='utf-8')
        (self.root / 'package.json').write_text('{"version":"0.3.1"}', encoding='utf-8')
        binaries = self.root / 'bin'; binaries.mkdir()
        for name in ('node.exe', 'py.exe'):
            shutil.copyfile(self.bin / name, binaries / name)
        (binaries / 'npm.cmd').write_text('@exit /b 0\n', encoding='ascii')
        study_tools = self.root / 'tools/study'; study_tools.mkdir()
        for name in ('review.py', 'daily.py', 'library.py'):
            (study_tools / name).write_text('from pathlib import Path\n'
                'with Path("post-install.log").open("a",encoding="utf-8") as f: f.write(Path(__file__).name+"\\n")\n', encoding='utf-8')
        (study_tools / 'product_tour.py').write_text('print(\'{"pending":true,"next_action":"teach_in_current_chat"}\')', encoding='utf-8')
        self.env = {**os.environ, 'PATH': os.pathsep.join([str(binaries), str(Path(sys._base_executable).parent),
                      str(Path(os.environ['SystemRoot']) / 'System32')]),
                    'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1',
                    'PIP_NO_INDEX': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PIP_CONFIG_FILE': os.devnull}
        for key in ('DSH_PROFILE_DIR', 'DSH_PROFILE', 'DSH_SESSION_ID', 'STUDY_CHAT_ID'):
            self.env.pop(key, None)
        self.env['DSH_HOME'] = str(self.root / 'dsh-home')
        if RESTRICTED_TOKEN:
            self._preseed_offline_venv()

    def _preseed_offline_venv(self):
        """Prepare ``.venv`` with a working pip without running ensurepip.

        The restricted Windows sandbox cannot let ensurepip use its temporary
        directories, so create the environment with ``--without-pip`` and copy
        the already installed pip package into it. setup.ps1 then runs its real
        code path (Python selection, pip check, post-install steps) unchanged.
        """
        try:
            import pip
        except ImportError:  # pragma: no cover - project venv always has pip
            raise unittest.SkipTest('当前解释器没有可复制的 pip，跳过需要真实 venv 的安装测试')
        target = self.root / '.venv'
        made = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(target)],
                              cwd=self.root, env=self.env, capture_output=True,
                              encoding='utf-8', timeout=120)
        if made.returncode:
            raise AssertionError(made.stderr)
        site = target / 'Lib/site-packages'
        source = Path(pip.__file__).parent
        site.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, site / 'pip')
        for dist in source.parent.glob('pip-*.dist-info'):
            shutil.copytree(dist, site / dist.name)

    def write_fake_integration(self, install_exit=0, configuration_ok=True, status_text=None):
        """Fake dsh_integration.py implementing the install and status contracts."""
        directory = self.root / 'tools/onboarding'
        directory.mkdir(parents=True, exist_ok=True)
        helper = directory / 'dsh_integration.py'
        if status_text is None:
            status_text = json.dumps({
                'configuration_ok': configuration_ok,
                'mcp': {'status': 'configured'},
                'skills': {'status': 'configured'},
            })
        helper.write_text('\n'.join([
            'import sys',
            'from pathlib import Path',
            'command = sys.argv[1] if len(sys.argv) > 1 else ""',
            'if command == "install":',
            '    Path("dsh-install-called").write_text("yes", encoding="utf-8")',
            '    sys.exit(%d)' % install_exit,
            'if command == "status":',
            '    Path("dsh-status-called").write_text("yes", encoding="utf-8")',
            '    print(%r)' % status_text,
            '    sys.exit(0)',
            'raise SystemExit(3)',
            '',
        ]), encoding='utf-8')
        return helper

    def run_ps(self, script, args=(), answer=None):
        return subprocess.run([str(SHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), *args],
                              cwd=self.root, env=self.env, input=answer, capture_output=True, encoding='utf-8', timeout=60)

    def pip_version(self):
        return subprocess.run([str(self.root / '.venv/Scripts/python.exe'), '-B', '-m', 'pip', '--version'],
                              cwd=self.root, env=self.env, capture_output=True, encoding='utf-8', timeout=20)

    def completed_steps(self):
        return (self.root / 'post-install.log').read_text(encoding='utf-8').splitlines()

    def test_deepseek_setup_uses_release_even_outside_harness_and_reports_failure(self):
        (self.root / 'client-target.json').write_text('{"version":1,"client":"deepseek"}', encoding='utf-8')
        self.env.pop('DSH_SESSION_ID', None)
        helper = self.write_fake_integration()
        result = self.run_ps(self.root / 'setup.ps1')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('yes', (self.root / 'dsh-install-called').read_text(encoding='utf-8'))
        self.assertEqual('yes', (self.root / 'dsh-status-called').read_text(encoding='utf-8'))
        self.assertIn('Setup complete', result.stdout)
        helper.write_text('raise SystemExit(2)', encoding='utf-8')
        result = self.run_ps(self.root / 'setup.ps1')
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn('Setup complete', result.stdout)

    def test_deepseek_fallback_rejects_install_zero_but_incomplete_status(self):
        # The fallback branch must not trust the install exit code alone: a
        # configuration_ok=false status means setup did NOT complete.
        (self.root / 'client-target.json').write_text('{"version":1,"client":"deepseek"}', encoding='utf-8')
        self.write_fake_integration(install_exit=0, configuration_ok=False)
        result = self.run_ps(self.root / 'setup.ps1')
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn('Setup complete', result.stdout)
        self.assertIn('fallback verification failed', result.stdout.lower())
        self.assertEqual('yes', (self.root / 'dsh-install-called').read_text(encoding='utf-8'))
        self.assertEqual('yes', (self.root / 'dsh-status-called').read_text(encoding='utf-8'))

    def test_deepseek_fallback_rejects_unparsable_status_json(self):
        (self.root / 'client-target.json').write_text('{"version":1,"client":"deepseek"}', encoding='utf-8')
        self.write_fake_integration(install_exit=0, status_text='this is not json')
        result = self.run_ps(self.root / 'setup.ps1')
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn('Setup complete', result.stdout)
        self.assertIn('fallback verification failed', result.stdout.lower())

    def test_deepseek_fallback_accepts_verified_status(self):
        (self.root / 'client-target.json').write_text('{"version":1,"client":"deepseek"}', encoding='utf-8')
        self.write_fake_integration(install_exit=0, configuration_ok=True)
        result = self.run_ps(self.root / 'setup.ps1')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('Setup complete', result.stdout)
        self.assertEqual('yes', (self.root / 'dsh-install-called').read_text(encoding='utf-8'))
        self.assertEqual('yes', (self.root / 'dsh-status-called').read_text(encoding='utf-8'))

    def test_standalone_setup_skips_old_py_and_uses_working_python(self):
        self.env['DSH_HOME'] = str(self.root / 'must-not-configure')
        (self.root / 'tools/onboarding/dsh_integration.py').write_text('raise SystemExit(91)', encoding='utf-8')
        result = self.run_ps(self.root / 'setup.ps1')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('teach_in_current_chat', result.stdout)
        self.assertIn('docs/CHAT_ONBOARDING.md', result.stdout)
        self.assertEqual(0, self.pip_version().returncode)
        self.assertEqual(['review.py', 'daily.py', 'library.py'], self.completed_steps())

    def test_harness_configuration_failure_does_not_report_setup_complete(self):
        (self.root / 'client-target.json').write_text('{"version":1,"client":"deepseek"}', encoding='utf-8')
        self.env['DSH_SESSION_ID'] = 'session-synthetic-test'
        (self.root / 'tools/onboarding/dsh_integration.py').write_text('raise SystemExit(2)', encoding='utf-8')
        result = self.run_ps(self.root / 'setup.ps1')
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn('Setup complete', result.stdout)
        self.assertNotIn('teach_in_current_chat', result.stdout)

    def test_real_guide_carries_working_interpreter_into_setup(self):
        result = self.run_ps(self.root / 'tools/onboarding/start.ps1', ['-Mode', 'Guide', '-NoPause'], '1\ny\n0\n')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('Setup complete', result.stdout)
        self.assertEqual(0, self.pip_version().returncode)
        self.assertEqual(['review.py', 'daily.py', 'library.py'], self.completed_steps())

    @unittest.skipIf(RESTRICTED_TOKEN,
                     '受限 Windows 沙箱无法运行 ensurepip 的临时目录；正常终端会执行本用例')
    def test_real_missing_pip_is_restored_offline_without_touching_learning_files(self):
        made = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(self.root / '.venv')],
                              cwd=self.root, env=self.env, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(0, made.returncode, made.stderr)
        self.assertNotEqual(0, self.pip_version().returncode)
        records = {'Study/_System/state.json': json.dumps({'timezone': 'UTC', 'revision': 8, 'courses': [],
                     'mistakes': [{'id': 'M', 'user_answer': 0, 'source': {'page': 1}}]}),
                   'config.local.json': '{"moodle_url":"https://example.invalid","timezone":"UTC"}',
                   '.private/moodle-token.dpapi': 'SYNTHETIC-CIPHERTEXT',
                   'Study/course/notes.md': 'Original learner notes'}
        for name, value in records.items():
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding='utf-8')
        before = {name: (self.root / name).read_bytes() for name in records}
        for turn in range(2):
            result = self.run_ps(self.root / 'tools/onboarding/start.ps1', ['-Mode', 'Guide', '-NoPause'], '1\ny\n0\n')
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn('Setup complete', result.stdout)
            self.assertEqual(0, self.pip_version().returncode)
            self.assertEqual(before, {name: (self.root / name).read_bytes() for name in records})
            if turn == 0:
                self.assertIn('内置安装包修复', result.stdout)
            else:
                self.assertNotIn('内置安装包修复', result.stdout)

    def test_failed_builtin_pip_repair_stops_before_post_install_writes(self):
        python = self.root / '.venv/Scripts/python.exe'
        python.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.bin / 'broken-pip.exe', python)
        sentinel = self.root / 'Study/_System/state.json'
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b'{"revision":123,"synthetic":"preserve"}')
        result = self.run_ps(self.root / 'setup.ps1')
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn('内置 pip 修复未成功', result.stdout)
        self.assertFalse((self.root / 'post-install.log').exists())
        self.assertEqual(b'{"revision":123,"synthetic":"preserve"}', sentinel.read_bytes())


if __name__ == '__main__':
    unittest.main()
