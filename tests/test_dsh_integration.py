"""Offline regression tests for the DeepSeek Harness adaptation.

Synthetic only: isolated temporary roots, a fake DSH home, no network, no
credentials and no real study records. Covered contract areas:

* stable conversation identity (explicit / agreed name / Harness main and
  internal session IDs);
* structural profile-patch reading: comments never count as configuration;
* additive, validated, backup-protected DSH profile merge that never replaces
  the original file when parsing or validation fails;
* real installer CLI contract (subprocess): installed/unchanged/dry-run with
  configuration_ok true exit 0, while conflict/skipped/error exit non-zero with
  actionable hints and an untouched profile when a conflict blocks the merge;
* no false success: duplicated same-name MCP entries are a conflict, a missing
  project ``skills/`` directory is invalid and blocks the write, and stale or
  damaged generated blocks are repaired (never reported unchanged while the
  persisted status would still be invalid);
* the extracted PowerShell setup helper (real Windows PowerShell subprocess):
  completion covers both the moodle_study MCP entry and the skills directory,
  prints the restart/login/runtime-verification reminder on success, never
  prints "Setup complete" for an incomplete configuration, and gives manual
  deduplication / project-file recovery hints for the new failure classes.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/study'))

from chat_identity import ChatIdentityError, describe, resolve_chat_id  # noqa: E402

spec = importlib.util.spec_from_file_location('dsh_integration', ROOT / 'tools/onboarding/dsh_integration.py')
dsh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dsh)

MAIN = 'session-43e6211a-9a48-4334-8b13-821896b1829c'
INTERNAL = '37daf969-abb2-4b9e-923e-02ede86ed164'


class IsolatedDirectory:
    """Temporary test root created with the default inherited permissions.

    ``tempfile`` creates directories with mode 0o700. Under the restricted
    Windows sandbox token used while developing this repository, such a
    directory does not carry the sandbox write grant, so fixture files cannot
    be created inside it and cleanup fails. Creating the directory like any
    other file keeps the inherited permissions; cleanup stays explicit.
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
        self.temp = IsolatedDirectory('dsh 测试 ', ROOT / 'tmp')
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

    def test_install_reports_conflicts_instead_of_false_success(self):
        original = ('# moodle_study in a comment must not count: ' + Path(self.root / 'tools/moodle/run.mjs').as_posix() + '\n'
                    + self._mcp_entry_text()
                    + "- id: skill-filesystem\n  name: '@deepseek-ai/dsh-skill-filesystem'\n"
                      "  config:\n    customSkillDirs: ['D:/old/skills']\n")
        self._write(original)
        before = self.patch.read_bytes()
        result = dsh.install(self.root, self.env)
        self.assertEqual('conflict', result['status'])
        self.assertFalse(result['configuration_ok'])
        self.assertEqual('conflict', result['mcp']['status'])
        self.assertEqual('conflict', result['skills']['status'])
        self.assertGreaterEqual(len(result['warnings']), 2)
        steps = '\n'.join(result['next_steps'])
        self.assertIn('--update-existing', steps)
        self.assertIn('docs/DSH.md', steps)
        self.assertIn('不要删除现有配置', steps)
        self.assertEqual(before, self.patch.read_bytes())
        status = dsh.inspect(self.root, self.env)
        self.assertEqual('conflict', status['mcp']['status'])
        self.assertEqual('conflict', status['skills']['status'])
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

    def test_duplicate_same_name_mcp_entries_are_a_conflict(self):
        # Two entries share serverName moodle_study (one this project, one an
        # older deployment). The installer must not silently pick one, so the
        # profile is a conflict and nothing is written.
        self._write(self._mcp_entry_text(self.root / 'tools/moodle/run.mjs')
                    + self._mcp_entry_text())
        status = dsh.inspect(self.root, self.env)
        self.assertEqual('conflict', status['mcp']['status'])
        self.assertEqual(2, status['mcp']['entry_count'])
        self.assertTrue(status['mcp']['duplicate'])
        self.assertFalse(status['configuration_ok'])
        before = self.patch.read_bytes()
        result = dsh.install(self.root, self.env)
        self.assertEqual('conflict', result['status'])
        self.assertFalse(result['configuration_ok'])
        self.assertEqual('conflict', result['mcp']['status'])
        self.assertEqual(before, self.patch.read_bytes())
        self.assertIsNone(result['backup'])
        warnings = '\n'.join(result['warnings'])
        self.assertIn('2', warnings)
        self.assertIn('同名', warnings)
        steps = '\n'.join(result['next_steps'])
        self.assertIn('手动', steps)
        self.assertIn('去重', steps)

    def test_update_existing_cannot_deduplicate_same_name_mcp(self):
        self._write(self._mcp_entry_text(self.root / 'tools/moodle/run.mjs')
                    + self._mcp_entry_text())
        before = self.patch.read_bytes()
        result = dsh.install(self.root, self.env, update_existing=True)
        self.assertEqual('conflict', result['status'])
        self.assertFalse(result['configuration_ok'])
        warnings = '\n'.join(result['warnings'])
        self.assertIn('无法唯一定位', warnings)
        self.assertEqual(before, self.patch.read_bytes())

    def test_missing_skills_directory_is_invalid_and_never_written(self):
        shutil.rmtree(self.root / 'skills')
        self._write('# keep me\n[]\n')
        status = dsh.inspect(self.root, self.env)
        self.assertEqual('invalid', status['skills']['status'])
        self.assertFalse(status['configuration_ok'])
        before = self.patch.read_bytes()
        result = dsh.install(self.root, self.env)
        self.assertEqual('error', result['status'])
        self.assertFalse(result['configuration_ok'])
        self.assertEqual('invalid', result['skills']['status'])
        self.assertEqual(before, self.patch.read_bytes())
        self.assertIsNone(result['backup'])
        steps = '\n'.join(result['next_steps'])
        self.assertIn('skills', steps)
        self.assertIn('不要删除', steps)

    def _broken_generated_block(self):
        return dsh.BEGIN + '\n- insert: [unclosed\n' + dsh.END + '\n'

    def test_install_repairs_invalid_generated_block(self):
        # Correct manual entries plus a damaged generated block: the complete
        # original file is invalid, but the generated block is ours to rebuild.
        self._write('# user comment\n'
                    + self._mcp_entry_text(self.root / 'tools/moodle/run.mjs')
                    + self._existing_skills_text()
                    + self._broken_generated_block())
        self.assertEqual('invalid', dsh.inspect(self.root, self.env)['patch_parse'])
        result = dsh.install(self.root, self.env)
        self.assertNotEqual('unchanged', result['status'])
        self.assertEqual('installed', result['status'])
        self.assertTrue(result['configuration_ok'])
        self.assertTrue(result['validated'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn('# user comment', text)
        self.assertNotIn(dsh.BEGIN, text)
        status = dsh.inspect(self.root, self.env)
        self.assertEqual('ok', status['patch_parse'])
        self.assertTrue(status['configuration_ok'])
        self.assertEqual(1, status['mcp']['entry_count'])
        second = dsh.install(self.root, self.env)
        self.assertEqual('unchanged', second['status'])
        self.assertEqual(text, self.patch.read_text(encoding='utf-8'))

    def test_install_repairs_stale_generated_block_without_manual_entries(self):
        # A valid generated block from an older deployment is stale, not a
        # reason to report unchanged: rebuild it for the current project.
        self._write(dsh._render_block(self.other))
        result = dsh.install(self.root, self.env)
        self.assertEqual('installed', result['status'])
        self.assertTrue(result['configuration_ok'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertNotIn((self.other / 'tools/moodle/run.mjs').as_posix(), text)
        self.assertIn((self.root / 'tools/moodle/run.mjs').as_posix(), text)
        status = dsh.inspect(self.root, self.env)
        self.assertTrue(status['configuration_ok'])
        self.assertEqual(1, status['mcp']['entry_count'])
        self.assertEqual(1, status['skills']['override_count'])
        second = dsh.install(self.root, self.env)
        self.assertEqual('unchanged', second['status'])
        self.assertEqual(text, self.patch.read_text(encoding='utf-8'))

    def test_install_repairs_stale_valid_generated_block_without_duplicates(self):
        # Correct manual entries plus a still-valid generated block that points
        # at an older deployment: the full file parses, yet writing it would
        # leave a duplicate moodle_study entry, so install must rebuild it.
        self._write('# user comment\n'
                    + self._mcp_entry_text(self.root / 'tools/moodle/run.mjs')
                    + self._existing_skills_text()
                    + dsh._render_block(self.other))
        self.assertEqual('ok', dsh.inspect(self.root, self.env)['patch_parse'])
        result = dsh.install(self.root, self.env)
        self.assertNotEqual('unchanged', result['status'])
        self.assertEqual('installed', result['status'])
        self.assertTrue(result['configuration_ok'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn('# user comment', text)
        self.assertNotIn(dsh.BEGIN, text)
        self.assertNotIn((self.other / 'tools/moodle/run.mjs').as_posix(), text)
        self.assertEqual(1, len(dsh._mcp_entries(dsh._load_document(text))))
        status = dsh.inspect(self.root, self.env)
        self.assertTrue(status['configuration_ok'])
        self.assertEqual(1, status['mcp']['entry_count'])
        self.assertEqual(1, status['skills']['override_count'])

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
        folded = old_path[:60] + '\n              ' + old_path[60:]
        original = ('- insert:\n    - id: moodle-study\n      name: \'@deepseek-ai/dsh-mcp-client\'\n'
                    '      config:\n        serverName: moodle_study\n        transport: stdio\n'
                    "        command: 'node'\n        args:\n          - '" + folded + "'\n"
                    "          - 'mcp'\n        cwd: !!js process.cwd()\n"
                    '- id: ui-settings\n  name: "@deepseek-ai/dsh-client-ui-settings"\n  config:\n    enabled: true\n')
        self._write(original)
        result = dsh.install(self.root, self.env, update_existing=True)
        self.assertEqual('installed', result['status'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn('!!js process.cwd()', text)
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

    def _existing_skills_text(self):
        return ("- id: skill-filesystem\n  name: '@deepseek-ai/dsh-skill-filesystem'\n"
                '  config:\n    customSkillDirs:\n      - ' + (self.root / 'skills').as_posix() + '\n')

    def test_update_existing_persists_change_when_skills_already_configured(self):
        self._write(self._mcp_entry_text() + self._existing_skills_text())
        skill_dir = (self.root / 'skills').as_posix()
        result = dsh.install(self.root, self.env, update_existing=True)
        # No new entries are needed, but the in-memory MCP path change must be saved.
        self.assertEqual('installed', result['status'])
        self.assertTrue(result['updated_existing'])
        self.assertFalse(result['mcp_added'])
        self.assertFalse(result['skills_added'])
        self.assertTrue(Path(result['backup']).exists())
        data = _yaml_load(self.patch.read_text(encoding='utf-8'))
        configs = dsh._mcp_entries(data)
        self.assertEqual(1, len(configs))
        self.assertEqual((self.root / 'tools/moodle/run.mjs').resolve(),
                         Path(configs[0]['args'][0]).resolve())
        self.assertEqual('mcp', configs[0]['args'][1])
        self.assertIn(skill_dir, self.patch.read_text(encoding='utf-8'))
        first_text = self.patch.read_text(encoding='utf-8')
        second = dsh.install(self.root, self.env)
        self.assertEqual('unchanged', second['status'])
        self.assertEqual(first_text, self.patch.read_text(encoding='utf-8'))

    def test_update_existing_only_changes_target_args(self):
        old_path = (self.other / 'tools/moodle/run.mjs').as_posix()
        unrelated = ('# unrelated comment still references ' + old_path + '\n'
                     "- id: unrelated-mcp\n  name: '@deepseek-ai/dsh-mcp-client'\n"
                     '  config:\n    serverName: other_server\n    transport: stdio\n'
                     "    command: node\n    args: ['" + old_path + "', 'mcp']\n"
                     "- id: ui-settings\n  name: '@deepseek-ai/dsh-client-ui-settings'\n"
                     '  config:\n    enabled: true\n')
        self._write(self._mcp_entry_text() + unrelated)
        result = dsh.install(self.root, self.env, update_existing=True)
        self.assertEqual('installed', result['status'])
        self.assertTrue(result['updated_existing'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn('# unrelated comment still references ' + old_path, text)
        self.assertIn("args: ['" + old_path + "', 'mcp']", text)
        self.assertIn('enabled: true', text)
        data = _yaml_load(text)
        target = next(config for config in dsh._mcp_entries(data)
                      if config.get('serverName') == 'moodle_study')
        self.assertEqual((self.root / 'tools/moodle/run.mjs').resolve(),
                         Path(target['args'][0]).resolve())
        unrelated_config = next(config for config in dsh._walk_dicts(data)
                                if config.get('serverName') == 'other_server')
        self.assertEqual(old_path, unrelated_config['args'][0])

    def test_update_existing_flow_style_args_keeps_other_fields(self):
        old_path = (self.other / 'tools/moodle/run.mjs').as_posix()
        folded = old_path[:60] + '\n              ' + old_path[60:]
        original = ("# temporary validation block\n- insert:\n    - id: moodle-study\n"
                    "      name: '@deepseek-ai/dsh-mcp-client'\n      config:\n"
                    '        serverName: moodle_study\n        transport: stdio\n'
                    '        command: node\n        args:\n          [\n'
                    "            '" + folded + "',\n            'mcp'\n          ]\n"
                    '        env: {}\n        cwd: !!js process.cwd()\n')
        self._write(original)
        result = dsh.install(self.root, self.env, update_existing=True)
        self.assertEqual('installed', result['status'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn('!!js process.cwd()', text)
        self.assertIn('transport: stdio', text)
        self.assertNotIn(old_path, text)
        config = dsh._mcp_entries(dsh._load_document(text))[0]
        self.assertEqual((self.root / 'tools/moodle/run.mjs').resolve(),
                         Path(config['args'][0]).resolve())

    def test_update_existing_dry_run_reports_content_without_writing(self):
        self._write(self._mcp_entry_text() + self._existing_skills_text())
        before = self.patch.read_bytes()
        result = dsh.install(self.root, self.env, update_existing=True, dry_run=True)
        self.assertEqual('dry-run', result['status'])
        self.assertTrue(result['updated_existing'])
        self.assertIn((self.root / 'tools/moodle/run.mjs').as_posix(), result['content'])
        self.assertEqual(before, self.patch.read_bytes())

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
        self.temp = IsolatedDirectory('会话 测试 ', ROOT / 'tmp')
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


VENV_PYTHON = ROOT / '.venv/Scripts/python.exe'
PYTHON = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
CLI = ROOT / 'tools/onboarding/dsh_integration.py'
SETUP_HELPER = ROOT / 'tools/onboarding/setup-dsh.ps1'
POWERSHELL = (shutil.which('pwsh') or shutil.which('powershell')
              or str(Path(os.environ.get('SystemRoot', 'C:/Windows'))
                     / 'System32/WindowsPowerShell/v1.0/powershell.exe'))


def _clean_env(extra=None):
    """Environment with no real DSH home/profile or chat identity inherited."""
    env = dict(os.environ)
    for key in ('DSH_PROFILE_DIR', 'DSH_PROFILE', 'DSH_SESSION_ID', 'STUDY_CHAT_ID', 'DSH_HOME'):
        env.pop(key, None)
    env.update(extra or {})
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


class InstallerCliContractTests(unittest.TestCase):
    """Run the real CLI as a subprocess; assert status, hints and exit codes."""

    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = IsolatedDirectory('安装器 CLI 测试 ', ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'deployment'
        (self.root / 'tools/moodle').mkdir(parents=True)
        (self.root / 'tools/moodle/run.mjs').write_text('// synthetic\n', encoding='utf-8')
        (self.root / 'skills/self-study-tutor').mkdir(parents=True)
        (self.root / 'skills/self-study-tutor/SKILL.md').write_text(
            '---\nname: synthetic\n---\n', encoding='utf-8')
        self.other = base / 'old-deployment'
        (self.other / 'tools/moodle').mkdir(parents=True)
        (self.other / 'tools/moodle/run.mjs').write_text('// old\n', encoding='utf-8')
        self.dsh_home = base / 'home'
        self.profile = self.dsh_home / 'profiles/desktop'
        self.profile.mkdir(parents=True)
        self.patch = self.profile / 'cordis.patch.yml'
        self.env = _clean_env({'DSH_HOME': str(self.dsh_home)})

    def _write(self, text):
        self.patch.write_text(text, encoding='utf-8')

    def _mcp_entry(self, run_mjs=None, cwd=None):
        target = Path(run_mjs or (self.other / 'tools/moodle/run.mjs'))
        folder = Path(cwd or self.other)
        return ("- insert:\n    - id: moodle-study\n      name: '@deepseek-ai/dsh-mcp-client'\n"
                '      config:\n        serverName: moodle_study\n        transport: stdio\n'
                "        command: 'node'\n        args:\n          - '" + target.as_posix() + "'\n"
                "          - 'mcp'\n        cwd: '" + folder.as_posix() + "'\n")

    def _correct_mcp_entry(self):
        return self._mcp_entry(self.root / 'tools/moodle/run.mjs', self.root)

    def _skills_entry(self, directory):
        return ("- id: skill-filesystem\n  name: '@deepseek-ai/dsh-skill-filesystem'\n"
                '  config:\n    customSkillDirs:\n      - ' + Path(directory).as_posix() + '\n')

    def _run(self, *extra):
        return subprocess.run([str(PYTHON), '-B', str(CLI), 'install', '--project', str(self.root), *extra],
                              cwd=str(ROOT), env=self.env, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=120)

    def _run_json(self, *extra):
        finished = self._run('--json', *extra)
        self.assertTrue(finished.stdout.strip(), 'CLI produced no JSON: ' + finished.stderr)
        return finished, json.loads(finished.stdout)

    def _run_status_json(self):
        finished = subprocess.run(
            [str(PYTHON), '-B', str(CLI), 'status', '--json', '--project', str(self.root)],
            cwd=str(ROOT), env=self.env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=120)
        return finished, json.loads(finished.stdout)

    def test_empty_configuration_installs_exit_zero_then_unchanged(self):
        result = self._run()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('DSH 配置：installed', result.stdout)
        self.assertIn('MCP：configured', result.stdout)
        self.assertIn('技能目录：configured', result.stdout)
        self.assertIn('configuration_ok：true', result.stdout)
        self.assertTrue(self.patch.exists())
        status, payload = self._run_status_json()
        self.assertEqual(0, status.returncode)
        self.assertTrue(payload['configuration_ok'])
        before = self.patch.read_bytes()
        second, second_payload = self._run_json()
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual('unchanged', second_payload['status'])
        self.assertTrue(second_payload['configuration_ok'])
        self.assertEqual(before, self.patch.read_bytes())
        third, third_payload = self._run_json()
        self.assertEqual(0, third.returncode, third.stdout + third.stderr)
        self.assertEqual('unchanged', third_payload['status'])
        self.assertEqual(before, self.patch.read_bytes())

    def test_correct_existing_configuration_is_unchanged_exit_zero(self):
        self._write(self._correct_mcp_entry() + self._skills_entry(self.root / 'skills'))
        before = self.patch.read_bytes()
        result = self._run()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('DSH 配置：unchanged', result.stdout)
        self.assertIn('configuration_ok：true', result.stdout)
        second, payload = self._run_json()
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual('unchanged', payload['status'])
        self.assertTrue(payload['configuration_ok'])
        self.assertEqual(before, self.patch.read_bytes())

    def test_mcp_conflict_is_nonzero_actionable_and_keeps_the_file(self):
        self._write(self._mcp_entry())
        before = self.patch.read_bytes()
        result = self._run()
        self.assertEqual(1, result.returncode)
        self.assertIn('DSH 配置冲突', result.stdout)
        self.assertIn('--update-existing', result.stdout)
        self.assertIn('docs/DSH.md', result.stdout)
        self.assertIn('不要删除现有配置', result.stdout)
        self.assertNotIn('DSH 配置：installed', result.stdout)
        second, payload = self._run_json()
        self.assertEqual(1, second.returncode)
        self.assertEqual('conflict', payload['status'])
        self.assertFalse(payload['configuration_ok'])
        self.assertEqual('conflict', payload['mcp']['status'])
        self.assertIn('--update-existing', '\n'.join(payload['next_steps']))
        self.assertEqual(before, self.patch.read_bytes())
        status, status_payload = self._run_status_json()
        self.assertEqual(0, status.returncode)  # status is informational
        self.assertFalse(status_payload['configuration_ok'])
        self.assertEqual('conflict', status_payload['mcp']['status'])

    def test_skills_conflict_is_nonzero_with_hints_and_keeps_the_file(self):
        self._write(self._correct_mcp_entry() + self._skills_entry(self.other / 'skills'))
        before = self.patch.read_bytes()
        result = self._run()
        self.assertEqual(1, result.returncode)
        self.assertIn('技能冲突', result.stdout)
        self.assertIn('docs/DSH.md', result.stdout)
        self.assertIn('不要删除现有配置', result.stdout)
        self.assertNotIn('DSH 配置：installed', result.stdout)
        self.assertEqual(before, self.patch.read_bytes())

    def test_both_conflicts_are_nonzero_and_keep_the_file(self):
        self._write(self._mcp_entry() + self._skills_entry(self.other / 'skills'))
        before = self.patch.read_bytes()
        finished, payload = self._run_json()
        self.assertEqual(1, finished.returncode)
        self.assertEqual('conflict', payload['status'])
        self.assertFalse(payload['configuration_ok'])
        self.assertEqual('conflict', payload['mcp']['status'])
        self.assertEqual('conflict', payload['skills']['status'])
        steps = '\n'.join(payload['next_steps'])
        self.assertIn('--update-existing', steps)
        self.assertIn('skill-filesystem', steps)
        self.assertEqual(before, self.patch.read_bytes())

    def test_update_existing_resolves_mcp_conflict_and_is_idempotent(self):
        self._write(self._mcp_entry())
        finished, payload = self._run_json('--update-existing')
        self.assertEqual(0, finished.returncode, finished.stdout + finished.stderr)
        self.assertEqual('installed', payload['status'])
        self.assertTrue(payload['configuration_ok'])
        self.assertTrue(payload['updated_existing'])
        self.assertEqual('configured', payload['mcp']['status'])
        self.assertEqual('configured', payload['skills']['status'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn((self.root / 'tools/moodle/run.mjs').as_posix(), text)
        self.assertNotIn((self.other / 'tools/moodle/run.mjs').as_posix(), text)
        again, again_payload = self._run_json()
        self.assertEqual(0, again.returncode)
        self.assertEqual('unchanged', again_payload['status'])
        self.assertTrue(again_payload['configuration_ok'])

    def test_parse_error_is_nonzero_and_keeps_the_original_file(self):
        original = 'x: [unclosed\n'
        self._write(original)
        before = self.patch.read_bytes()
        finished, payload = self._run_json()
        self.assertEqual(1, finished.returncode)
        self.assertEqual('error', payload['status'])
        self.assertFalse(payload['configuration_ok'])
        self.assertEqual(before, self.patch.read_bytes())
        self.assertIn('不要删除现有配置', '\n'.join(payload['next_steps']))

    def test_duplicate_same_name_mcp_is_conflict_nonzero_and_keeps_the_file(self):
        self._write(self._correct_mcp_entry() + self._mcp_entry())
        before = self.patch.read_bytes()
        finished, payload = self._run_json()
        self.assertEqual(1, finished.returncode, finished.stdout + finished.stderr)
        self.assertEqual('conflict', payload['status'])
        self.assertFalse(payload['configuration_ok'])
        self.assertEqual('conflict', payload['mcp']['status'])
        self.assertEqual(2, payload['mcp']['entry_count'])
        warnings = '\n'.join(payload['warnings'])
        self.assertIn('同名', warnings)
        self.assertIn('2', warnings)
        self.assertEqual(before, self.patch.read_bytes())
        # --update-existing must not guess between the two entries; it points at
        # manual deduplication instead of rewriting one of them.
        again, again_payload = self._run_json('--update-existing')
        self.assertEqual(1, again.returncode, again.stdout + again.stderr)
        self.assertEqual('conflict', again_payload['status'])
        self.assertFalse(again_payload['configuration_ok'])
        self.assertIn('无法唯一定位', '\n'.join(again_payload['warnings']))
        self.assertIn('手动', '\n'.join(again_payload['next_steps']))
        self.assertEqual(before, self.patch.read_bytes())
        status, status_payload = self._run_status_json()
        self.assertEqual(0, status.returncode)  # status stays informational
        self.assertFalse(status_payload['configuration_ok'])
        self.assertEqual('conflict', status_payload['mcp']['status'])

    def test_missing_skills_directory_never_reports_false_success(self):
        shutil.rmtree(self.root / 'skills')
        self._write('[]\n')
        before = self.patch.read_bytes()
        finished, payload = self._run_json()
        self.assertEqual(1, finished.returncode, finished.stdout + finished.stderr)
        self.assertEqual('error', payload['status'])
        self.assertNotEqual('installed', payload['status'])
        self.assertFalse(payload['configuration_ok'])
        self.assertEqual('invalid', payload['skills']['status'])
        self.assertIn('skills', '\n'.join(payload['next_steps']))
        self.assertEqual(before, self.patch.read_bytes())
        self.assertIsNone(payload['backup'])
        human = self._run()
        self.assertEqual(1, human.returncode)
        self.assertNotIn('DSH 配置：installed', human.stdout)
        self.assertNotIn('configuration_ok：true', human.stdout)
        self.assertIn('DSH 配置未完成', human.stdout)
        self.assertEqual(before, self.patch.read_bytes())
        status, status_payload = self._run_status_json()
        self.assertEqual(0, status.returncode)
        self.assertFalse(status_payload['configuration_ok'])
        self.assertEqual('invalid', status_payload['skills']['status'])

    def test_manual_correct_config_with_invalid_generated_block_is_repaired(self):
        manual = self._correct_mcp_entry() + self._skills_entry(self.root / 'skills')
        self._write('# user comment kept\n' + manual
                    + dsh.BEGIN + '\n- insert: [unclosed\n' + dsh.END + '\n')
        finished, payload = self._run_json()
        self.assertEqual(0, finished.returncode, finished.stdout + finished.stderr)
        self.assertNotEqual('unchanged', payload['status'])
        self.assertEqual('installed', payload['status'])
        self.assertTrue(payload['configuration_ok'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn('# user comment kept', text)
        self.assertNotIn(dsh.BEGIN, text)
        self.assertEqual(1, len(dsh._mcp_entries(dsh._load_document(text))))
        status, status_payload = self._run_status_json()
        self.assertEqual(0, status.returncode)
        self.assertEqual('ok', status_payload['patch_parse'])
        self.assertTrue(status_payload['configuration_ok'])
        self.assertEqual(1, status_payload['mcp']['entry_count'])
        again, again_payload = self._run_json()
        self.assertEqual(0, again.returncode, again.stdout + again.stderr)
        self.assertEqual('unchanged', again_payload['status'])
        self.assertTrue(again_payload['configuration_ok'])
        self.assertEqual(text, self.patch.read_text(encoding='utf-8'))

    def test_manual_correct_config_with_stale_valid_generated_block_has_no_duplicates(self):
        manual = self._correct_mcp_entry() + self._skills_entry(self.root / 'skills')
        self._write('# user comment kept\n' + manual + dsh._render_block(self.other))
        status, status_payload = self._run_status_json()
        self.assertEqual(0, status.returncode)
        self.assertEqual('ok', status_payload['patch_parse'])
        self.assertFalse(status_payload['configuration_ok'])
        finished, payload = self._run_json()
        self.assertEqual(0, finished.returncode, finished.stdout + finished.stderr)
        self.assertNotEqual('unchanged', payload['status'])
        self.assertTrue(payload['configuration_ok'])
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn('# user comment kept', text)
        self.assertNotIn(dsh.BEGIN, text)
        self.assertNotIn((self.other / 'tools/moodle/run.mjs').as_posix(), text)
        self.assertNotIn((self.other / 'skills').as_posix(), text)
        self.assertEqual(1, len(dsh._mcp_entries(dsh._load_document(text))))
        status, status_payload = self._run_status_json()
        self.assertEqual(0, status.returncode)
        self.assertTrue(status_payload['configuration_ok'])
        self.assertEqual(1, status_payload['mcp']['entry_count'])
        self.assertEqual('configured', status_payload['skills']['status'])

    def test_dry_run_exits_zero_without_writing(self):
        finished, payload = self._run_json('--dry-run')
        self.assertEqual(0, finished.returncode, finished.stdout + finished.stderr)
        self.assertEqual('dry-run', payload['status'])
        self.assertTrue(payload['configuration_ok'])
        self.assertFalse(self.patch.exists())
        self.assertIn('tools/moodle/run.mjs', payload['content'])

    def test_skipped_is_nonzero_and_writes_nothing(self):
        missing_home = Path(self.temp.name) / 'no-dsh-home'
        env = _clean_env({'DSH_HOME': str(missing_home)})
        finished = subprocess.run(
            [str(PYTHON), '-B', str(CLI), 'install', '--project', str(self.root), '--json'],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=120)
        self.assertEqual(1, finished.returncode)
        payload = json.loads(finished.stdout)
        self.assertEqual('skipped', payload['status'])
        self.assertFalse(payload['configuration_ok'])
        self.assertFalse((missing_home / 'profiles/desktop/cordis.patch.yml').exists())


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell setup helper')
class SetupDshHelperTests(unittest.TestCase):
    """Real PowerShell subprocess for the extracted installer completion helper."""

    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = IsolatedDirectory('setup 助手 测试 ', ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.other = base / 'old-deployment'
        self.dsh_home = base / 'home'
        self.profile = self.dsh_home / 'profiles/desktop'
        self.profile.mkdir(parents=True)
        self.patch = self.profile / 'cordis.patch.yml'
        driver = base / 'driver.ps1'
        driver.write_text('\n'.join([
            "$ErrorActionPreference = 'Stop'",
            ". '" + SETUP_HELPER.as_posix().replace("'", "''") + "'",
            "$code = Invoke-DshConfiguration -Root '" + ROOT.as_posix().replace("'", "''") + "'",
            'exit $code',
            '',
        ]), encoding='utf-8-sig')
        self.driver = driver
        self.env = _clean_env({'DSH_HOME': str(self.dsh_home)})

    def _run(self):
        return subprocess.run(
            [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(self.driver)],
            cwd=str(ROOT), env=self.env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=180)

    def _write(self, text):
        self.patch.write_text(text, encoding='utf-8')

    def test_success_merges_and_prints_restart_login_and_runtime_reminder(self):
        result = self._run()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        lower = result.stdout.lower()
        self.assertIn('configuration verified for both parts', lower)
        self.assertIn('restart deepseek harness', lower)
        self.assertIn('school login', lower)
        self.assertIn('runtime loading is not verified', lower)
        self.assertNotIn('Setup complete', result.stdout)
        self.assertNotIn('NOT complete', result.stdout)
        self.assertTrue(self.patch.exists())
        text = self.patch.read_text(encoding='utf-8')
        self.assertIn((ROOT / 'tools/moodle/run.mjs').as_posix(), text)
        self.assertIn((ROOT / 'skills').as_posix(), text)
        final_line = [line for line in result.stdout.splitlines() if line.strip()][-1]
        self.assertIn('runtime loading is not verified', final_line)

    def test_already_correct_configuration_is_verified_without_rewriting(self):
        self._write(self._correct_configuration())
        before = self.patch.read_bytes()
        result = self._run()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('configuration verified for both parts', result.stdout.lower())
        self.assertIn('restart', result.stdout.lower())
        self.assertEqual(before, self.patch.read_bytes())

    def test_conflict_returns_nonzero_prints_repair_hints_without_setup_complete(self):
        self._write(self._mcp_entry(self.other / 'tools/moodle/run.mjs', self.other)
                    + self._skills_entry(self.other / 'skills'))
        before = self.patch.read_bytes()
        result = self._run()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('NOT complete', result.stdout)
        self.assertIn('--update-existing', result.stdout)
        self.assertIn('docs/DSH.md', result.stdout)
        self.assertIn('MCP conflict', result.stdout)
        self.assertIn('Skills conflict', result.stdout)
        self.assertIn('Do not delete the existing configuration', result.stdout)
        self.assertNotIn('Setup complete', result.stdout)
        self.assertEqual(before, self.patch.read_bytes())

    def test_unparsable_profile_returns_nonzero_without_fake_success(self):
        original = 'x: [unclosed\n'
        self._write(original)
        before = self.patch.read_bytes()
        result = self._run()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('NOT complete', result.stdout)
        self.assertIn('Do not delete the existing configuration', result.stdout)
        self.assertNotIn('Setup complete', result.stdout)
        self.assertEqual(before, self.patch.read_bytes())

    def test_duplicate_same_name_mcp_gets_manual_dedupe_hint(self):
        self._write(self._mcp_entry(ROOT / 'tools/moodle/run.mjs', ROOT)
                    + self._mcp_entry(ROOT / 'tools/moodle/run.mjs', ROOT)
                    + self._skills_entry(ROOT / 'skills'))
        before = self.patch.read_bytes()
        result = self._run()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('NOT complete', result.stdout)
        self.assertIn('MCP conflict', result.stdout)
        self.assertIn('share serverName moodle_study', result.stdout)
        self.assertIn('by hand', result.stdout)
        self.assertNotIn('Setup complete', result.stdout)
        self.assertEqual(before, self.patch.read_bytes())

    def _synthetic_project(self, with_skills=True):
        project = Path(self.temp.name) / 'synthetic-deployment'
        (project / 'tools/onboarding').mkdir(parents=True)
        (project / 'tools/study').mkdir(parents=True)
        (project / 'tools/moodle').mkdir(parents=True)
        shutil.copyfile(ROOT / 'tools/onboarding/dsh_integration.py',
                        project / 'tools/onboarding/dsh_integration.py')
        shutil.copyfile(ROOT / 'tools/study/chat_identity.py',
                        project / 'tools/study/chat_identity.py')
        (project / 'tools/moodle/run.mjs').write_text('// synthetic\n', encoding='utf-8')
        if with_skills:
            (project / 'skills/self-study-tutor').mkdir(parents=True)
        return project

    def _run_project(self, project):
        driver = Path(self.temp.name) / 'driver-synthetic.ps1'
        driver.write_text('\n'.join([
            "$ErrorActionPreference = 'Stop'",
            ". '" + SETUP_HELPER.as_posix().replace("'", "''") + "'",
            "$code = Invoke-DshConfiguration -Root '"
            + project.as_posix().replace("'", "''")
            + "' -PythonExecutable '" + str(PYTHON).replace("'", "''") + "'",
            'exit $code',
            '',
        ]), encoding='utf-8-sig')
        return subprocess.run(
            [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(driver)],
            cwd=str(ROOT), env=self.env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=180)

    def test_missing_project_skills_directory_gets_actionable_invalid_hint(self):
        project = self._synthetic_project(with_skills=False)
        self._write('[]\n')
        before = self.patch.read_bytes()
        result = self._run_project(project)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('NOT complete', result.stdout)
        self.assertIn('Skills invalid', result.stdout)
        self.assertIn('Restore the complete project files', result.stdout)
        self.assertNotIn('Setup complete', result.stdout)
        self.assertEqual(before, self.patch.read_bytes())

    # Synthetic fragments point at a fake old deployment; DSH_HOME is always a
    # temporary directory, so no real DSH profile is read or written.
    def _mcp_entry(self, run_mjs, cwd):
        return ("- insert:\n    - id: moodle-study\n      name: '@deepseek-ai/dsh-mcp-client'\n"
                '      config:\n        serverName: moodle_study\n        transport: stdio\n'
                "        command: 'node'\n        args:\n          - '" + Path(run_mjs).as_posix() + "'\n"
                "          - 'mcp'\n        cwd: '" + Path(cwd).as_posix() + "'\n")

    def _skills_entry(self, directory):
        return ("- id: skill-filesystem\n  name: '@deepseek-ai/dsh-skill-filesystem'\n"
                '  config:\n    customSkillDirs:\n      - ' + Path(directory).as_posix() + '\n')

    def _correct_configuration(self):
        return (self._mcp_entry(ROOT / 'tools/moodle/run.mjs', ROOT)
                + self._skills_entry(ROOT / 'skills'))


if __name__ == '__main__':
    unittest.main()
