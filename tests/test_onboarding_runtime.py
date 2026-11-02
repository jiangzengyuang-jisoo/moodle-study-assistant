"""Real Windows bootstrap/setup recovery; dependency downloads are disabled.

Only Node/npm and post-install study commands are fixture substitutes. Python,
venv, pip/ensurepip, PowerShell and the shipped setup/launcher are executed.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


@unittest.skipUnless(os.name == 'nt', 'Windows setup')
class RuntimeRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        cls.compiled = tempfile.TemporaryDirectory(prefix='runtime compilers ', dir=ROOT / 'tmp')
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
        self.temp = tempfile.TemporaryDirectory(prefix='恢复 中文 ', dir=ROOT / 'tmp')
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
        self.env.pop('DSH_HOME', None)
        self.env.pop('DSH_SESSION_ID', None)
        helper = self.root / 'tools/onboarding/dsh_integration.py'
        helper.write_text('from pathlib import Path; Path("dsh-called").write_text("yes")', encoding='utf-8')
        result = self.run_ps(self.root / 'setup.ps1')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('yes', (self.root / 'dsh-called').read_text())
        helper.write_text('raise SystemExit(2)', encoding='utf-8')
        result = self.run_ps(self.root / 'setup.ps1')
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn('Setup complete', result.stdout)

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
        python.parent.mkdir(parents=True)
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
