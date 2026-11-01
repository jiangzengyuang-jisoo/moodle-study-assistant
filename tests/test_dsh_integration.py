# 回归测试：DeepSeek Harness（DSH）客户端适配
"""Offline regression tests for the DeepSeek Harness adaptation.

Synthetic only: isolated temporary roots, a fake DSH home, no network, no
credentials and no real study records. Covered contract areas:

* stable conversation identity (explicit / agreed name / Harness main and
  internal session IDs);
* additive, backup-protected DSH profile merge that never overwrites existing
  configuration and stays idempotent.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/study'))

from chat_identity import ChatIdentityError, describe, resolve_chat_id  # noqa: E402

spec = importlib.util.spec_from_file_location('dsh_integration', ROOT / 'tools/onboarding/dsh_integration.py')
dsh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dsh)

MAIN = 'session-43e6211a-9a48-4334-8b13-821896b1829c'
INTERNAL = '37daf969-abb2-4b9e-923e-02ede86ed164'


class ChatIdentityTests(unittest.TestCase):
    def test_explicit_argument_wins(self):
        self.assertEqual(resolve_chat_id('original-thread', {'DSH_SESSION_ID': MAIN}),
                         ('original-thread', 'argument'))

    def test_agreed_name_is_the_recoverable_fallback(self):
        self.assertEqual(resolve_chat_id(None, {'STUDY_CHAT_ID': 'autumn-course'}),
                         ('autumn-course', 'env:STUDY_CHAT_ID'))

    def test_harness_main_chat_is_automatic(self):
        self.assertEqual(resolve_chat_id(None, {'DSH_SESSION_ID': MAIN}),
                         (MAIN, 'env:DSH_SESSION_ID'))

    def test_internal_session_is_refused(self):
        with self.assertRaises(ChatIdentityError):
            resolve_chat_id(None, {'DSH_SESSION_ID': INTERNAL})

    def test_missing_identity_is_an_error_not_a_random_id(self):
        with self.assertRaises(ChatIdentityError) as caught:
            resolve_chat_id(None, {})
        self.assertIn('--chat', str(caught.exception))

    def test_describe_never_returns_the_identifier_itself(self):
        value = describe({'DSH_SESSION_ID': MAIN})
        self.assertEqual(value['dsh_session_kind'], 'main')
        self.assertNotIn(MAIN, json.dumps(value, ensure_ascii=False))


class DshProfileTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='dsh 测试 ', dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'deployment'
        (self.root / 'tools/moodle').mkdir(parents=True)
        (self.root / 'tools/moodle/run.mjs').write_text('// synthetic\n', encoding='utf-8')
        (self.root / 'skills/self-study-tutor').mkdir(parents=True)
        (self.root / 'skills/self-study-tutor/SKILL.md').write_text(
            '---\nname: self-study-tutor\ndescription: synthetic\n---\n', encoding='utf-8')
        self.dsh_home = base / 'home'
        self.profile = self.dsh_home / 'profiles/desktop'
        self.profile.mkdir(parents=True)
        self.patch = self.profile / 'cordis.patch.yml'
        self.env = {'DSH_HOME': str(self.dsh_home)}

    def test_paths_resolve_from_injected_environment(self):
        self.assertEqual(dsh.dsh_home(self.env), self.dsh_home)
        self.assertEqual(dsh.profile_dir(self.env), self.profile)
        self.assertEqual(dsh.patch_path(self.env), self.patch)
        self.assertEqual(dsh.profile_dir({'DSH_PROFILE_DIR': str(self.profile)}), self.profile)
        self.assertEqual(dsh.profile_dir({'DSH_HOME': str(self.dsh_home), 'DSH_PROFILE': 'other'}),
                         self.dsh_home / 'profiles/other')

    def test_install_creates_minimal_block_in_existing_profile(self):
        result = dsh.install(self.root, self.env)
        self.assertEqual('installed', result['status'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn(dsh.BEGIN, text)
        self.assertIn((self.root / 'tools/moodle/run.mjs').as_posix(), text)
        self.assertIn((self.root / 'skills').as_posix(), text)
        self.assertTrue(result['restart_required'])
        # A new patch has nothing to preserve, so no backup is needed.
        self.assertIsNone(result['backup'])

    def test_install_preserves_existing_entries_and_backs_up(self):
        original = ('# user comment\n- id: agent-default-model\n'
                    '  name: "@deepseek-ai/dsh-agent-default-model"\n'
                    '  config:\n    provider: deepseek-account\n    model: deepseek-flash\n')
        self.patch.write_text(original, encoding='utf-8')
        result = dsh.install(self.root, self.env)
        self.assertEqual('installed', result['status'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertTrue(text.startswith(original.rstrip('\n')))
        self.assertEqual(original, Path(result['backup']).read_text(encoding='utf-8'))
        self.assertEqual(dsh._model_config(text),
                         {'provider': 'deepseek-account', 'model': 'deepseek-flash'})

    def test_install_is_idempotent(self):
        self.patch.write_text('# existing\n', encoding='utf-8')
        self.assertEqual('installed', dsh.install(self.root, self.env)['status'])
        before = self.patch.read_bytes()
        second = dsh.install(self.root, self.env)
        self.assertEqual('unchanged', second['status'])
        self.assertEqual(before, self.patch.read_bytes())

    def test_install_does_not_overwrite_conflicting_user_entries(self):
        self.patch.write_text(
            "- id: moodle-other\n  name: '@deepseek-ai/dsh-mcp-client'\n  config:\n"
            '    serverName: moodle_study\n    transport: stdio\n'
            "    command: node\n    args: ['D:/old/run.mjs', 'mcp']\n"
            "- id: skill-filesystem\n  name: '@deepseek-ai/dsh-skill-filesystem'\n"
            "  config:\n    customSkillDirs: ['D:/old/skills']\n", encoding='utf-8')
        result = dsh.install(self.root, self.env)
        self.assertEqual('skipped', result['status'])
        self.assertEqual(2, len(result['warnings']))
        self.assertIn('moodle_study', self.patch.read_text(encoding='utf-8'))

    def test_dry_run_reports_content_without_writing(self):
        self.patch.write_text('# existing\n', encoding='utf-8')
        before = self.patch.read_bytes()
        result = dsh.install(self.root, self.env, dry_run=True)
        self.assertEqual('dry-run', result['status'])
        self.assertIn(dsh.BEGIN, result['content'])
        self.assertEqual(before, self.patch.read_bytes())

    def test_status_reports_configured_after_install(self):
        dsh.install(self.root, self.env)
        status = dsh.inspect(self.root, self.env)
        self.assertTrue(status['detected'])
        self.assertEqual('configured', status['mcp']['status'])
        self.assertEqual('configured', status['skills']['status'])
        self.assertNotIn(str(self.root), json.dumps(status['chat'], ensure_ascii=False))

    def test_status_without_harness_is_harmless(self):
        empty = Path(self.temp.name) / 'no-dsh'
        status = dsh.inspect(self.root, {'DSH_HOME': str(empty)})
        self.assertFalse(status['detected'])
        result = dsh.install(self.root, {'DSH_HOME': str(empty)})
        self.assertEqual('skipped', result['status'])
        self.assertFalse((empty / 'profiles/desktop/cordis.patch.yml').exists())


class SessionCliIdentityTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='会话 测试 ', dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _env(self, extra):
        env = {**os.environ, **extra}
        for key in ('DSH_SESSION_ID', 'STUDY_CHAT_ID'):
            if key not in extra:
                env.pop(key, None)
        return env

    def _run(self, args, extra):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'tools/study/session.py'),
                               '--root', str(self.root), *args],
                              capture_output=True, text=True, encoding='utf-8',
                              env=self._env(extra), timeout=60)

    def test_cli_resolves_harness_main_chat(self):
        result = self._run(['chat-id'], {'DSH_SESSION_ID': MAIN})
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(MAIN, payload['chat_id'])
        self.assertEqual('env:DSH_SESSION_ID', payload['chat_source'])

    def test_cli_refuses_internal_session_without_explicit_chat(self):
        result = self._run(['chat-id'], {'DSH_SESSION_ID': INTERNAL})
        self.assertEqual(2, result.returncode)
        self.assertIn('内部会话', result.stderr)

    def test_cli_subtask_requires_explicit_original_id(self):
        result = self._run(['--subtask', 'status'], {'DSH_SESSION_ID': MAIN})
        self.assertEqual(2, result.returncode)
        self.assertIn('显式传入原始聊天 ID', result.stderr)


if __name__ == '__main__':
    unittest.main()
