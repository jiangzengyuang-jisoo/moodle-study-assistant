"""Offline guide/diagnostic integration with synthetic, isolated public roots."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('onboarding', ROOT / 'tools/onboarding/entry.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
sys.path.insert(0, str(ROOT / 'tools/study'))
from session import Session
from session_test_fixtures import write_snapshot
from state_io import atomic, digest, read


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='上手 测试 ', dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.app = entry.App(self.root, run_probe=self.fake_probe, locate=lambda name: 'node.exe' if name == 'node' else 'npm.cmd')

    def fake_probe(self, args, root, timeout=20):
        self.assertEqual(self.root, root)
        self.calls.append([str(a) for a in args])
        if args[1] == '-p':
            return 0, '22\n', ''
        if args[-1] == 'check':
            return 0, 'connected', ''
        return 0, '', ''

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def prepared(self, token=True, courses=True):
        self.write('package.json', {'version': '0.3.0'})
        self.write('config.local.json', {'moodle_url': 'https://synthetic.example', 'timezone': 'Asia/Hong_Kong'})
        self.write('Study/_System/state.json', {'revision': 3, 'timezone': 'Asia/Hong_Kong', 'courses': [
            {'id': 'synthetic', 'moodle_course_ids': [9], 'teaching_cursor': {'checks': [{'id': 'Q1', 'answer': '原答'}]}}
        ] if courses else [], 'mistakes': [{'id': 'M1', 'source': {'material_id': 'old'}, 'user_answer': '原始错误'}]})
        path = self.root / '.venv/Scripts/python.exe'
        path.parent.mkdir(parents=True); path.write_bytes(b'not-executed')
        path = self.root / '.codex/config.toml'
        path.parent.mkdir()
        path.write_text('[mcp_servers.moodle_study]\ncommand = "node"\nargs = [' +
                        json.dumps(str(self.root / 'tools/moodle/run.mjs').replace('\\', '/')) + ', "mcp"]\n', encoding='utf-8')
        if token:
            path = self.root / '.private/moodle-token.dpapi'
            path.parent.mkdir(); path.write_bytes(b'synthetic-ciphertext-not-a-token')

    def tree(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def checks(self, report):
        return {row['id']: row for row in report['checks']}

    def test_blank_diagnosis_does_not_initialize_or_network(self):
        before = self.tree()
        report = self.app.inspect()
        self.assertEqual('setup', report['next_action'])
        self.assertEqual(before, self.tree())
        self.assertFalse(any(c[-1] == 'check' for c in self.calls))

    def test_existing_records_and_ciphertext_unchanged_and_connection_unknown(self):
        self.prepared()
        before = self.tree()
        report = self.app.inspect()
        self.assertEqual('handoff', report['next_action'])
        self.assertEqual('unknown', self.checks(report)['credential']['status'])
        self.assertEqual(before, self.tree())

    def test_credential_is_only_statted_in_local_diagnosis(self):
        self.prepared()
        original = Path.open
        def guarded(path, *args, **kwargs):
            self.assertNotIn('.private', path.parts)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            self.assertEqual('handoff', self.app.inspect()['next_action'])

    def test_missing_authorization_then_missing_selection_resume(self):
        self.prepared(token=False, courses=False)
        self.assertEqual('connect', self.app.inspect()['next_action'])
        token = self.root / '.private/moodle-token.dpapi'
        token.parent.mkdir(); token.write_bytes(b'synthetic')
        self.assertEqual('courses', self.app.inspect()['next_action'])

    def test_corrupt_existing_state_blocks_all_writers(self):
        self.prepared()
        path = self.root / 'Study/_System/state.json'
        path.write_text('{"secret": "DO-NOT-ECHO', encoding='utf-8')
        before = self.tree()
        with patch.object(entry.subprocess, 'call') as child:
            for action in ('setup', 'edge', 'courses'):
                self.assertFalse(self.app.act(action)[0])
            child.assert_not_called()
        report = self.app.inspect()
        self.assertEqual('repair', report['next_action'])
        self.assertNotIn('DO-NOT-ECHO', json.dumps(report))
        self.assertEqual(before, self.tree())

    def test_wrong_array_type_and_timezone_mismatch_block_setup(self):
        self.prepared()
        state_path = self.root / 'Study/_System/state.json'
        state = read(state_path)
        for broken in ({**state, 'mistakes': {}}, {**state, 'timezone': 'UTC'}):
            self.write('Study/_System/state.json', broken)
            self.assertEqual('repair', self.app.inspect()['next_action'])

    def test_missing_files_with_history_require_recovery_not_blank_initialization(self):
        self.prepared()
        config_path = self.root / 'config.local.json'
        config = config_path.read_bytes()
        config_path.unlink()  # Synthetic fixture only.
        with patch.object(entry.subprocess, 'call') as child:
            self.assertEqual('repair', self.app.inspect()['next_action'])
            self.assertFalse(self.app.act('setup')[0])
            child.assert_not_called()
        config_path.write_bytes(config)
        path = self.root / 'Study/_System/state.json'
        path.rename(path.with_name('state.previous.json'))
        before = self.tree()
        with patch.object(entry.subprocess, 'call') as child:
            self.assertEqual('repair', self.app.inspect()['next_action'])
            self.assertFalse(self.app.act('setup')[0])
            child.assert_not_called()
        self.assertEqual(before, self.tree())

    def test_bad_site_is_not_echoed_or_contacted(self):
        self.prepared()
        self.write('config.local.json', {'moodle_url': 'https://user:SECRET@bad.invalid/?token=SECRET', 'timezone': 'UTC'})
        report = self.app.inspect(online=True)
        self.assertNotIn('SECRET', json.dumps(report))
        self.assertEqual('skipped', self.checks(report)['online']['status'])
        self.assertFalse(any(c[-1] == 'check' for c in self.calls))

    def test_non_string_school_address_gets_actionable_diagnosis(self):
        self.prepared()
        self.write('config.local.json', {'moodle_url': 123, 'timezone': 'UTC'})
        self.assertEqual('repair', self.app.inspect()['next_action'])

    def test_only_explicit_online_probe_connects_once_and_does_not_write(self):
        self.prepared()
        before = self.tree()
        self.app.inspect()
        report = self.app.inspect(online=True)
        self.assertEqual(1, sum(c[-1] == 'check' for c in self.calls))
        self.assertEqual('ok', self.checks(report)['online']['status'])
        self.assertEqual('unknown', self.checks(report)['school']['status'])
        self.assertEqual(before, self.tree())

    def test_connection_failures_are_chinese_and_never_echo_raw_output(self):
        cases = [('invalidtoken', '授权'), ('webservice_access_exception', '访问'),
                 ('Cannot decrypt token', '解密'), ('Moodle network request failed', '网络'),
                 ('arbitrary_error', '尚不能确定')]
        for error, expected in cases:
            with self.subTest(error=error):
                row = entry.online_result(1, 'SECRET student course C:/private', error)
                self.assertIn(expected, row['message'])
                self.assertNotIn('SECRET', json.dumps(row))
                self.assertNotIn('C:/private', json.dumps(row))
        self.assertIn('超时', entry.online_result(124, '', '')['message'])

    def test_failed_online_check_changes_next_step_until_explicit_recheck(self):
        self.prepared()
        def offline(args, root, timeout=20):
            if args[-1] == 'check':
                return 1, '', 'Moodle API failed: invalidtoken'
            return self.fake_probe(args, root, timeout)
        self.app.probe = offline
        self.assertEqual('connection_help', self.app.inspect(online=True)['next_action'])
        answers = iter(['3', '1', '0'])
        output = []
        entry.guide(self.app, ask=lambda _: next(answers), output=output.append)
        self.assertIn('处理学校连接问题', '\n'.join(output))
        self.assertNotIn(entry.HANDOFF, output)
        self.app.probe = self.fake_probe
        self.assertEqual('handoff', self.app.inspect(online=True)['next_action'])

    def fail_connection(self):
        def offline(args, root, timeout=20):
            if args[-1] == 'check':
                self.calls.append(list(args))
                return 1, '', 'Moodle API failed: invalidtoken'
            return self.fake_probe(args, root, timeout)
        self.app.probe = offline
        return self.app.inspect(online=True)

    def test_local_diagnosis_retains_known_connection_failure_without_network(self):
        self.prepared()
        before = self.tree()
        original = self.fail_connection()
        answers = iter(['2', '4', '2', '1', '0'])
        output = []
        entry.guide(self.app, ask=lambda _: next(answers), output=output.append)
        report = self.app.inspect()
        self.assertEqual('connection_help', report['next_action'])
        self.assertTrue(self.checks(report)['online']['reused'])
        self.assertEqual(self.checks(original)['online']['checked_at'], self.checks(report)['online']['checked_at'])
        self.assertNotIn(entry.HANDOFF, output)
        self.assertEqual(1, sum(c[-1] == 'check' for c in self.calls))
        self.assertEqual(before, self.tree())

    def test_failed_or_cancelled_reconnection_does_not_clear_failure(self):
        self.prepared()
        self.fail_connection()
        answers = iter(['5', '0', '2', '0'])
        with patch.object(self.app, 'act') as action:
            entry.guide(self.app, ask=lambda _: next(answers), output=lambda _: None)
            action.assert_not_called()
        with patch.object(entry.subprocess, 'call', return_value=1):
            self.assertFalse(self.app.act('edge')[0])
        self.assertEqual('connection_help', self.app.inspect()['next_action'])

    def test_config_and_credential_changes_invalidate_cached_connection_result(self):
        self.prepared()
        self.fail_connection()
        # An ordinary learning update must not erase connection evidence.
        path = self.root / 'Study/_System/state.json'
        state = read(path); state['revision'] += 1; self.write('Study/_System/state.json', state)
        self.assertEqual('connection_help', self.app.inspect()['next_action'])
        self.write('config.local.json', {'moodle_url': 'https://changed.example', 'timezone': 'Asia/Hong_Kong'})
        self.assertNotIn('online', self.checks(self.app.inspect()))
        self.fail_connection()
        (self.root / '.private/moodle-token.dpapi').write_bytes(b'new-synthetic-generation')
        report = self.app.inspect()
        self.assertNotIn('online', self.checks(report))
        self.assertEqual('unknown', self.checks(report)['credential']['status'])

    def test_successful_recheck_replaces_failure_and_local_reuse_is_labelled(self):
        self.prepared()
        self.fail_connection()
        self.app.probe = self.fake_probe
        online = self.app.inspect(online=True)
        local = self.app.inspect()
        self.assertEqual('handoff', local['next_action'])
        self.assertFalse(self.checks(online)['online']['reused'])
        self.assertTrue(self.checks(local)['online']['reused'])
        self.assertIn('此前检查', self.checks(local)['online']['message'])
        self.assertEqual(self.checks(online)['online']['checked_at'], self.checks(local)['online']['checked_at'])

    def test_actual_timezone_validation_blocks_matching_invalid_names(self):
        self.prepared()
        def real_timezone(args, root, timeout=20):
            if 'ZoneInfo(key)' in str(args):
                return entry.probe([sys.executable, *args[1:]], root, timeout)
            return self.fake_probe(args, root, timeout)
        self.app.probe = real_timezone
        for zone in ('Asia/HongkongTypo', '', '../outside', 'SECRET_INVALID_ZONE'):
            with self.subTest(zone=zone):
                self.write('config.local.json', {'moodle_url': 'https://synthetic.example', 'timezone': zone})
                state = read(self.root / 'Study/_System/state.json'); state['timezone'] = zone
                self.write('Study/_System/state.json', state)
                before = self.tree()
                report = self.app.inspect(online=True)
                self.assertEqual('repair', report['next_action'])
                self.assertEqual('error', self.checks(report)['timezone']['status'])
                self.assertEqual('skipped', self.checks(report)['online']['status'])
                self.assertNotIn('SECRET_INVALID_ZONE', json.dumps(report))
                with patch.object(entry.subprocess, 'call') as child:
                    self.assertFalse(self.app.act('setup')[0])
                    child.assert_not_called()
                self.assertEqual(before, self.tree())
        for zone in ('UTC', 'Asia/Hong_Kong', 'Europe/London'):
            self.write('config.local.json', {'moodle_url': 'https://synthetic.example', 'timezone': zone})
            state['timezone'] = zone; self.write('Study/_System/state.json', state)
            self.assertEqual('ok', self.checks(self.app.inspect())['timezone']['status'])

    def test_timezone_probe_failure_is_not_reported_as_ready(self):
        self.prepared()
        def fail_timezone(args, root, timeout=20):
            if 'ZoneInfo(key)' in str(args):
                return 124, '', 'diagnostic_timeout'
            return self.fake_probe(args, root, timeout)
        self.app.probe = fail_timezone
        self.assertEqual('repair', self.app.inspect()['next_action'])

    def test_pip_repair_failure_stops_recommended_install_loop_until_repaired(self):
        self.prepared()
        def no_pip(args, root, timeout=20):
            if args[-3:] == ['-m', 'pip', '--version']:
                return 1, '', 'No module named pip'
            return self.fake_probe(args, root, timeout)
        self.app.probe = no_pip
        self.assertEqual('setup', self.app.inspect()['next_action'])
        with patch.object(entry.subprocess, 'call', return_value=3) as child:
            self.assertFalse(self.app.act('setup')[0])
            self.assertEqual('repair', self.app.inspect()['next_action'])
            self.assertFalse(self.app.act('setup')[0])
            self.assertEqual(1, child.call_count)
        self.app.probe = self.fake_probe
        self.assertEqual('handoff', self.app.inspect()['next_action'])

    @unittest.skipUnless(os.name == 'nt' and shutil.which('node'), 'Windows Node/DPAPI')
    def test_real_dpapi_error_is_classified_without_credential_output(self):
        code = '''import {decryptWindowsToken} from './tools/moodle/windows-token.mjs';
try { decryptWindowsToken(Buffer.from('synthetic-invalid-ciphertext')); }
catch(error) { console.error(error.code + ': ' + error.message); process.exitCode = 1; }'''
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', code], cwd=ROOT,
                                capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(1, result.returncode)
        self.assertIn('STUDY_TOKEN_DECRYPT_FAILED', result.stderr)
        row = entry.online_result(result.returncode, result.stdout, result.stderr)
        self.assertIn('解密', row['message'])
        self.assertIn('重新连接', row['next_step'])
        self.assertNotIn('synthetic-invalid-ciphertext', json.dumps(row))
        # Older installed Node entrypoints still emit the known Chinese text.
        self.assertIn('解密', entry.online_result(1, '', '请先运行 connect.cmd；Token 只能由保存它的 Windows 账户使用')['message'])

    def test_broken_interpreter_needs_repair_not_repeated_setup(self):
        self.prepared()
        def broken(args, root, timeout=20):
            if str(args[0]).endswith('python.exe'):
                return 1, '', 'broken'
            return self.fake_probe(args, root, timeout)
        self.app.probe = broken
        self.assertEqual('repair', self.app.inspect()['next_action'])
        with patch.object(entry.subprocess, 'call') as child:
            self.assertFalse(self.app.act('setup')[0])
            child.assert_not_called()

    def test_missing_dependency_recommends_setup_and_missing_node_install(self):
        self.prepared()
        def broken(args, root, timeout=20):
            if 'import sys, fsrs' in str(args[-1]):
                return 1, '', 'ModuleNotFoundError'
            return self.fake_probe(args, root, timeout)
        self.app.probe = broken
        self.assertEqual('setup', self.app.inspect()['next_action'])
        self.app.locate = lambda _: None
        self.assertEqual('install_runtime', self.app.inspect()['next_action'])

    def test_moved_or_custom_client_configuration_is_not_overwritten(self):
        self.prepared()
        self.root.joinpath('.codex/config.toml').write_text('[mcp_servers.moodle_study]\ncommand="node"\nargs=["D:/old/run.mjs","mcp"]', encoding='utf-8')
        before = self.tree()
        self.assertEqual('warning', self.checks(self.app.inspect())['client']['status'])
        self.assertEqual(before, self.tree())

    def test_menu_tips_handoff_and_exit_never_install_or_refresh(self):
        self.prepared()
        answers = iter(['4', '1', '0'])
        output = []
        before = self.tree()
        with patch.object(self.app, 'act') as action:
            entry.guide(self.app, ask=lambda _: next(answers), output=output.append)
            action.assert_not_called()
        self.assertIn('上下文压缩', '\n'.join(output))
        self.assertIn('原始聊天 ID', '\n'.join(output))
        self.assertEqual(before, self.tree())

    def test_cancel_install_and_cancel_connection_have_no_side_effects(self):
        answers = iter(['1', 'n', '5', '0', '0'])
        with patch.object(self.app, 'act') as action:
            entry.guide(self.app, ask=lambda _: next(answers), output=lambda _: None)
            action.assert_not_called()

    def test_failed_step_does_not_auto_retry_and_next_launch_resumes(self):
        self.prepared(token=False, courses=False)
        answers = iter(['1', '1', '0'])
        with patch.object(self.app, 'act', return_value=(False, '模拟登录失败')) as action:
            entry.guide(self.app, ask=lambda _: next(answers), output=lambda _: None)
            action.assert_called_once_with('edge')
        self.assertEqual('connect', self.app.inspect()['next_action'])

    def test_actions_reuse_existing_commands_with_argument_arrays(self):
        self.prepared()
        before = self.tree()
        with patch.object(entry.subprocess, 'call', return_value=0) as child:
            for action, suffix in [('edge', ['--browser', 'msedge']), ('chrome', ['--browser', 'chrome']),
                                   ('manual', ['tools/moodle/auth.py', '--manual']), ('courses', ['tools/moodle/run.mjs', 'courses'])]:
                self.assertTrue(self.app.act(action)[0])
                self.assertEqual(suffix, child.call_args.args[0][-2:])
                self.assertEqual(self.root, child.call_args.kwargs['cwd'])
            self.assertTrue(self.app.act('setup')[0])
            self.assertEqual([str(self.root / 'setup.ps1'), '-PythonExecutable', sys.executable], child.call_args.args[0][-3:])
        self.assertEqual(before, self.tree())

    def test_snapshot_collected_reviewed_failed_and_interrupted_are_distinct(self):
        self.prepared()
        session = Session(self.root, 'synthetic-chat')
        def collect(path):
            write_snapshot(path, {'collection_complete': True, 'threads': [], 'assignments': [], 'quizzes': []})
        manifest = session.start(collector=collect)
        self.assertEqual('warning', self.checks(self.app.inspect())['school']['status'])
        state = read(session.state)
        proposal = {'reviewed': True, 'base_sha256': digest(state), 'state': copy.deepcopy(state)}
        session.acknowledge(manifest['snapshot_sha256'], proposal)
        before = self.tree()
        self.assertEqual('ok', self.checks(self.app.inspect())['school']['status'])
        self.assertEqual(before, self.tree())
        path = session._manifest(manifest)
        for phase in ('failed', 'collecting', 'interrupted'):
            current = read(path); current['phase'] = phase; atomic(path, current)
            self.assertEqual('warning', self.checks(self.app.inspect())['school']['status'])

    def test_scope_change_or_tampered_snapshot_cannot_show_reviewed(self):
        self.prepared()
        session = Session(self.root, 'scope-chat')
        manifest = session.start(collector=lambda path: write_snapshot(path, {'collection_complete': True, 'threads': [], 'assignments': [], 'quizzes': []}))
        state = read(session.state)
        session.acknowledge(manifest['snapshot_sha256'], {'reviewed': True, 'base_sha256': digest(state), 'state': state})
        snap_path = session._manifest(manifest).with_name('snapshot.json')
        original = snap_path.read_bytes()
        snap_path.write_bytes(original + b' ')
        self.assertEqual('warning', self.checks(self.app.inspect())['school']['status'])
        snap_path.write_bytes(original)
        self.write('config.local.json', {'moodle_url': 'https://other.example', 'timezone': 'Asia/Hong_Kong'})
        self.assertEqual('warning', self.checks(self.app.inspect())['school']['status'])

    def test_saved_review_is_authoritative_when_manifest_or_views_lag(self):
        self.prepared()
        session = Session(self.root, 'repair-views')
        manifest = session.start(collector=lambda path: write_snapshot(path, {'collection_complete': True, 'threads': [], 'assignments': [], 'quizzes': []}))
        state = read(session.state)
        session.acknowledge(manifest['snapshot_sha256'], {'reviewed': True, 'base_sha256': digest(state), 'state': state})
        path = session._manifest(manifest)
        value = read(path); value['phase'] = 'collected'; atomic(path, value)
        path.with_name('review-status.json').write_text('{"phase":"pending"}', encoding='utf-8')
        before = self.tree()
        row = self.checks(self.app.inspect())['school']
        self.assertEqual('warning', row['status'])
        self.assertIn('核读已保存', row['message'])
        self.assertEqual(before, self.tree())

    def test_invalid_runtime_path_does_not_read_outside_workspace(self):
        self.prepared()
        self.write('Study/_System/Runtime/latest-request.json', {'run_id': '../../../../SECRET', 'sequence': 1})
        report = self.app.inspect()
        self.assertEqual('warning', self.checks(report)['school']['status'])
        self.assertNotIn('SECRET', json.dumps(report))

    @unittest.skipUnless(os.name == 'nt', 'Windows launcher')
    def test_real_powershell_bootstrap_without_python_is_readonly_chinese(self):
        target = self.root / 'tools/onboarding/start.ps1'
        target.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'tools/onboarding/start.ps1', target)
        shutil.copyfile(ROOT / 'tools/onboarding/python-runtime.ps1', target.with_name('python-runtime.ps1'))
        before = self.tree()
        result = subprocess.run([entry.powershell(), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(target),
                                 '-Mode', 'Diagnose', '-Json', '-NoPause'], env={**os.environ, 'PATH': ''},
                                capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(1, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertIn('Python 3.11', report['checks'][0]['message'])
        self.assertEqual(before, self.tree())

    @unittest.skipUnless(os.name == 'nt', 'Windows launcher')
    def test_real_powershell_and_python_diagnose_blank_root_never_initialize(self):
        target = self.root / 'tools/onboarding'
        target.mkdir(parents=True)
        for name in ('start.ps1', 'entry.py', 'python-runtime.ps1'):
            shutil.copyfile(ROOT / 'tools/onboarding' / name, target / name)
        before = self.tree()
        result = subprocess.run([entry.powershell(), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(target / 'start.ps1'),
                                 '-Mode', 'Diagnose', '-Json', '-NoPause'],
                                env={**os.environ, 'PATH': str(Path(sys._base_executable).parent)},
                                capture_output=True, encoding='utf-8', timeout=40)
        self.assertEqual(1, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual('install_runtime', report['next_action'])
        self.assertEqual(before, self.tree())

    @unittest.skipUnless(os.name == 'nt', 'Windows launcher')
    def test_actual_cmd_launchers_show_diagnostic_and_menu_without_writes(self):
        target = self.root / 'tools/onboarding'
        target.mkdir(parents=True)
        for name in ('start.ps1', 'entry.py', 'python-runtime.ps1'):
            shutil.copyfile(ROOT / 'tools/onboarding' / name, target / name)
        for name in ('start.cmd', 'diagnose.cmd'):
            shutil.copyfile(ROOT / name, self.root / name)
        system = Path(os.environ['SystemRoot']) / 'System32'
        env = {**os.environ, 'PATH': os.pathsep.join([str(Path(sys._base_executable).parent), str(system), str(system / 'WindowsPowerShell/v1.0')])}
        before = self.tree()
        cmd = str(system / 'cmd.exe')
        result = subprocess.run([cmd, '/d', '/c', 'diagnose.cmd', '-Json', '-NoPause'], cwd=self.root,
                                env=env, capture_output=True, encoding='utf-8', timeout=40)
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual('local', json.loads(result.stdout)['mode'])
        result = subprocess.run([cmd, '/d', '/c', 'start.cmd', '-NoPause'], cwd=self.root, env=env,
                                input='4\n0\n', capture_output=True, encoding='utf-8', timeout=40)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('上下文压缩', result.stdout)
        self.assertIn('下一步', result.stdout)
        self.assertEqual(before, self.tree())


if __name__ == '__main__':
    unittest.main()
