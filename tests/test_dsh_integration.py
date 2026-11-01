"""Offline regression tests for the DeepSeek Harness adaptation.

Synthetic only: isolated temporary roots, a fake DSH home, no network, no
credentials and no real study records. Covered contract areas:

* stable conversation identity (explicit / agreed name / Harness main and
  internal session IDs);
* structural profile-patch reading: comments never count as configuration;
* additive, validated, backup-protected DSH profile merge that never replaces
  the original file when parsing or validation fails.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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


def _yaml_load(text):
    import yaml
    return yaml.safe_load(text)


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
        self.other = base / 'old-deployment'
        (self.other / 'tools/moodle').mkdir(parents=True)
        (self.other / 'tools/moodle/run.mjs').write_text('// old\n', encoding='utf-8')
        self.dsh_home = base / 'home'
        self.profile = self.dsh_home / 'profiles/desktop'
        self.profile.mkdir(parents=True)
        self.patch = self.profile / 'cordis.patch.yml'
        self.env = {'DSH_HOME': str(self.dsh_home)}

    def _write(self, text):
        self.patch.write_text(text, encoding='utf-8')

    def _mcp_entry_text(self, run_mjs=None):
        path = Path(run_mjs or (self.other / 'tools/moodle/run.mjs')).as_posix()
        return ("- insert:\n    - id: moodle-study\n      name: '@deepseek-ai/dsh-mcp-client'\n"
                '      config:\n        serverName: moodle_study\n        transport: stdio\n'
                "        command: 'node'\n        args:\n          - '" + path + "'\n"
                "          - 'mcp'\n        cwd: '" + Path(self.other).as_posix() + "'\n")

    def test_paths_resolve_from_injected_environment(self):
        self.assertEqual(dsh.dsh_home(self.env), self.dsh_home)
        self.assertEqual(dsh.profile_dir(self.env), self.profile)
        self.assertEqual(dsh.patch_path(self.env), self.patch)
        self.assertEqual(dsh.profile_dir({'DSH_PROFILE_DIR': str(self.profile)}), self.profile)
        self.assertEqual(dsh.profile_dir({'DSH_HOME': str(self.dsh_home), 'DSH_PROFILE': 'other'}),
                         self.dsh_home / 'profiles/other')

    def test_install_replaces_legal_empty_flow_list_with_valid_yaml(self):
        self._write('# 用户注释保留\n[]\n')
        result = dsh.install(self.root, self.env)
        self.assertEqual('installed', result['status'])
        self.assertTrue(result['validated'])
        self.assertTrue(Path(result['backup']).exists())
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn('# 用户注释保留', text)
        self.assertNotRegex(text, r'(?m)^\s*\[\]\s*$')
        data = _yaml_load(text)
        self.assertIsInstance(data, list)
        status = dsh.inspect(self.root, self.env)
        self.assertEqual('configured', status['mcp']['status'])
        self.assertEqual('configured', status['skills']['status'])
        self.assertTrue(status['configuration_ok'])

    def test_install_handles_document_marker_and_empty_list(self):
        self._write('---\n[]\n')
        result = dsh.install(self.root, self.env)
        self.assertEqual('installed', result['status'])
        data = _yaml_load(self.patch.read_text(encoding='utf-8'))
        self.assertIsInstance(data, list)
        self.assertEqual(2, len(data))

    def test_install_preserves_existing_entries_and_backs_up(self):
        original = ('# user comment\n- id: agent-default-model\n'
                    '  name: "@deepseek-ai/dsh-agent-default-model"\n'
                    '  config:\n    provider: deepseek-account\n    model: deepseek-flash\n')
        self._write(original)
        result = dsh.install(self.root, self.env)
        self.assertEqual('installed', result['status'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertTrue(text.startswith(original.rstrip('\n')))
        self.assertEqual(original, Path(result['backup']).read_text(encoding='utf-8'))
        data = _yaml_load(text)
        self.assertEqual('deepseek-flash', data[0]['config']['model'])
        self.assertEqual('deepseek-account', dsh._model_status(data)['provider'])
        self.assertEqual(3, len(data))

    def test_install_is_idempotent(self):
        self._write('# existing\n')
        self.assertEqual('installed', dsh.install(self.root, self.env)['status'])
        before = self.patch.read_bytes()
        second = dsh.install(self.root, self.env)
        self.assertEqual('unchanged', second['status'])
        self.assertEqual(before, self.patch.read_bytes())

    def test_install_does_not_overwrite_comments_or_conflicting_entries(self):
        original = ('# moodle_study in a comment must not count: ' + Path(self.root / 'tools/moodle/run.mjs').as_posix() + '\n'
                    + self._mcp_entry_text()
                    + "- id: skill-filesystem\n  name: '@deepseek-ai/dsh-skill-filesystem'\n"
                      "  config:\n    customSkillDirs: ['D:/old/skills']\n")
        self._write(original)
        before = self.patch.read_bytes()
        result = dsh.install(self.root, self.env)
        self.assertEqual('unchanged', result['status'])
        self.assertGreaterEqual(len(result['warnings']), 2)
        self.assertEqual(before, self.patch.read_bytes())
        status = dsh.inspect(self.root, self.env)
        self.assertEqual('conflict', status['mcp']['status'])
        self.assertFalse(status['configuration_ok'])

    def test_comment_only_configuration_is_not_reported_as_configured(self):
        self._write('# serverName: moodle_study\n'
                    "# args: ['" + Path(self.root / 'tools/moodle/run.mjs').as_posix() + "', 'mcp']\n"
                    '# customSkillDirs: ["' + Path(self.root / 'skills').as_posix() + '"]\n')
        status = dsh.inspect(self.root, self.env)
        self.assertEqual('ok', status['patch_parse'])
        self.assertEqual('missing', status['mcp']['status'])
        self.assertEqual('missing', status['skills']['status'])
        self.assertFalse(status['configuration_ok'])

    def test_parse_failure_keeps_original_file(self):
        original = 'x: [unclosed\n'
        self._write(original)
        status = dsh.inspect(self.root, self.env)
        self.assertEqual('invalid', status['patch_parse'])
        self.assertFalse(status['configuration_ok'])
        before = self.patch.read_bytes()
        result = dsh.install(self.root, self.env)
        self.assertEqual('error', result['status'])
        self.assertEqual(before, self.patch.read_bytes())
        self.assertIsNone(result['backup'])

    def test_non_sequence_document_keeps_original_file(self):
        original = 'serverName: moodle_study\n'
        self._write(original)
        result = dsh.install(self.root, self.env)
        self.assertEqual('error', result['status'])
        self.assertEqual(original, self.patch.read_text(encoding='utf-8'))

    def test_failed_final_validation_keeps_original_file(self):
        original = '# keep me\n[]\n'
        self._write(original)
        before = self.patch.read_bytes()
        with mock.patch.object(dsh, '_render_block', return_value='# broken\n[unclosed\n'):
            result = dsh.install(self.root, self.env)
        self.assertEqual('error', result['status'])
        self.assertEqual(before, self.patch.read_bytes())
        self.assertIsNone(result['backup'])

    def test_update_existing_repoints_only_the_project_entry(self):
        original = ('# user settings kept\n- id: ui-chat\n  name: "@deepseek-ai/dsh-client-ui-chat"\n'
                    '  config:\n    transcriptView: standard\n'
                    + self._mcp_entry_text())
        self._write(original)
        result = dsh.install(self.root, self.env, update_existing=True)
        self.assertEqual('installed', result['status'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn('# user settings kept', text)
        self.assertIn('transcriptView: standard', text)
        self.assertNotIn((self.other / 'tools/moodle/run.mjs').as_posix(), text)
        self.assertIn((self.root / 'tools/moodle/run.mjs').as_posix(), text)
        self.assertIsNotNone(result['backup'])
        status = dsh.inspect(self.root, self.env)
        self.assertEqual('configured', status['mcp']['status'])
        self.assertEqual('configured', status['skills']['status'])

    def test_update_existing_handles_folded_old_path(self):
        old_path = (self.other / 'tools/moodle/run.mjs').as_posix()
        folded = old_path[:60] + '\n      ' + old_path[60:]
        original = ('- insert:\n    - id: moodle-study\n      name: \'@deepseek-ai/dsh-mcp-client\'\n'
                    '      config:\n        serverName: moodle_study\n        transport: stdio\n'
                    "        command: 'node'\n        args:\n          - '" + folded + "'\n"
                    "          - 'mcp'\n        cwd: !!js process.cwd()\n"
                    '- id: ui-settings\n  name: "@deepseek-ai/dsh-client-ui-settings"\n  config:\n    enabled: true\n')
        self._write(original)
        result = dsh.install(self.root, self.env, update_existing=True)
        self.assertEqual('installed', result['status'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertNotIn('!!js process.cwd()', text)
        self.assertIn('enabled: true', text)
        self.assertEqual('configured', dsh.inspect(self.root, self.env)['mcp']['status'])

    def test_status_separates_configuration_from_runtime_loading(self):
        self._write('- id: agent-default-model\n'
                    '  name: "@deepseek-ai/dsh-agent-default-model"\n'
                    '  config:\n    provider: deepseek-account\n    model: deepseek-flash\n')
        dsh.install(self.root, self.env)
        status = dsh.inspect(self.root, self.env)
        self.assertTrue(status['configuration_ok'])
        self.assertEqual('unverified', status['runtime_loaded'])
        self.assertEqual('unverified', status['runtime']['loaded'])
        self.assertIn('重启', status['runtime']['note'])
        self.assertEqual({'provider': 'deepseek-account', 'model': 'deepseek-flash'},
                         {key: status['model'][key] for key in ('provider', 'model')})

    def test_structured_existing_entries_are_recognized(self):
        self._write(self._mcp_entry_text(self.root / 'tools/moodle/run.mjs')
                    + "- id: skill-filesystem\n  name: '@deepseek-ai/dsh-skill-filesystem'\n"
                      '  config:\n    customSkillDirs:\n      - ' + Path(self.root / 'skills').as_posix() + '\n')
        status = dsh.inspect(self.root, self.env)
        self.assertEqual('configured', status['mcp']['status'])
        self.assertEqual('configured', status['skills']['status'])
        self.assertTrue(status['configuration_ok'])

    def test_missing_yaml_dependency_never_writes(self):
        self._write('# keep\n[]\n')
        before = self.patch.read_bytes()
        with mock.patch.object(dsh, 'yaml', None):
            status = dsh.inspect(self.root, self.env)
            result = dsh.install(self.root, self.env)
        self.assertEqual('unavailable', status['patch_parse'])
        self.assertEqual('error', result['status'])
        self.assertEqual(before, self.patch.read_bytes())

    def test_existing_js_expression_is_preserved(self):
        original = ('# user config with DSH expression\n'
                    '- id: moodle-other\n  name: "@deepseek-ai/dsh-mcp-client"\n'
                    '  config:\n    serverName: other_server\n    transport: stdio\n'
                    '    command: node\n    args: ["D:/x/run.mjs", "mcp"]\n'
                    '    cwd: !!js process.cwd()\n')
        self._write(original)
        result = dsh.install(self.root, self.env)
        self.assertEqual('installed', result['status'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn('!!js process.cwd()', text)
        data = dsh._load_document(text)
        self.assertIsInstance(data, list)
        self.assertEqual('configured', dsh.inspect(self.root, self.env)['mcp']['status'])

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
