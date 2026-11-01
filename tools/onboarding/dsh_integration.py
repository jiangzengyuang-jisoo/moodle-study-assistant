"""DeepSeek Harness (DSH) integration: read-only status and additive config merge.

The public project keeps working in Codex: this module never changes the Codex
entry point and never touches ``Study/``, ``.private/``, course material or
learning records. It only reads (``status``) or explicitly merges (``install``)
two entries into the user's DSH profile patch:

* an MCP client entry that starts this deployment's read-only Moodle server;
* a skill provider override that adds this deployment's ``skills/`` directory so
  the bundled teaching skill appears in the DSH skill catalog.

``install`` is idempotent and additive: it keeps every existing line, writes a
timestamped backup before changing the file, and wraps its own additions in
markers so a rerun replaces only that block. Existing entries that would clash
(a different ``moodle_study`` server or a user ``skill-filesystem`` override)
are reported instead of overwritten.
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDY_TOOLS = Path(__file__).resolve().parents[1] / 'study'
if str(STUDY_TOOLS) not in sys.path:
    sys.path.insert(0, str(STUDY_TOOLS))

import chat_identity  # noqa: E402  (needs the path insertion above)

BEGIN = '# >>> moodle-study-assistant DSH integration (generated) >>>'
END = '# <<< moodle-study-assistant DSH integration (generated) <<<'
SERVER_NAME = 'moodle_study'
BLOCK_RE = re.compile(re.escape(BEGIN) + r'.*?' + re.escape(END), re.DOTALL)
MODEL_ROW_RE = re.compile(r'-\s+id:\s*agent-default-model\b(?P<body>(?:\n\s+.*)*)')


def dsh_home(env=None):
    environment = os.environ if env is None else env
    value = (environment.get('DSH_HOME') or '').strip()
    return Path(value) if value else Path.home() / '.dsh'


def profile_dir(env=None):
    environment = os.environ if env is None else env
    value = (environment.get('DSH_PROFILE_DIR') or '').strip()
    if value:
        return Path(value)
    return dsh_home(environment) / 'profiles' / ((environment.get('DSH_PROFILE') or 'desktop').strip() or 'desktop')


def patch_path(env=None):
    return profile_dir(env) / 'cordis.patch.yml'


def _state_stem():
    """Directories created by DSH-style managed temp areas may be transient."""
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _node_command():
    found = shutil.which('node')
    return Path(found).as_posix() if found else 'node'


def _yaml_quote(value):
    text = str(value).replace("'", "''")
    return "'" + text + "'"


def _mcp_matches(text, project):
    """Return ('configured'|'conflict'|'missing', matching server names)."""
    servers = re.findall(r'serverName:\s*([A-Za-z0-9_-]+)', text)
    expected = (Path(project) / 'tools/moodle/run.mjs').resolve()
    run_paths = re.findall(r'''['"]([^'"]*run\.mjs)['"]''', text)
    matching = False
    for candidate in run_paths:
        try:
            if Path(candidate).expanduser().resolve() == expected:
                matching = True
                break
        except (OSError, ValueError):
            continue
    if matching and SERVER_NAME in servers:
        return 'configured', servers
    if SERVER_NAME in servers or any(path.lower().endswith('moodle') for path in run_paths):
        return 'conflict', servers
    if matching:
        return 'configured', servers
    return 'missing', servers


def _skills_configured(text, project):
    expected = (Path(project) / 'skills').resolve()
    for candidate in re.findall(r'''['"]([^'"]*skills)['"]''', text):
        try:
            if Path(candidate).expanduser().resolve() == expected:
                return True
        except (OSError, ValueError):
            continue
    return False


def _model_config(text):
    match = MODEL_ROW_RE.search(text)
    if not match:
        return {}
    body = match.group('body') or ''
    provider = re.search(r'provider:\s*([^\s#]+)', body)
    model = re.search(r'model:\s*([^\s#]+)', body)
    return {key: value.group(1) for key, value in
            (('provider', provider), ('model', model)) if value}


def inspect(project=None, env=None):
    """Return a shareable status snapshot; never reads credentials or records."""
    environment = os.environ if env is None else env
    root = Path(project or ROOT).resolve()
    home = dsh_home(environment)
    profile = profile_dir(environment)
    patch = profile / 'cordis.patch.yml'
    detected = patch.exists() or home.exists() or bool((environment.get('DSH_SESSION_ID') or '').strip())
    result = {
        'detected': detected,
        'dsh_home': home.as_posix(),
        'profile_dir': profile.as_posix(),
        'patch_path': patch.as_posix(),
        'patch_exists': patch.exists(),
        'project_root': root.as_posix(),
        'mcp': {'status': 'missing', 'servers': []},
        'skills': {'status': 'missing', 'expected_dir': (root / 'skills').as_posix()},
        'model': {},
        'chat': chat_identity.describe(environment),
        'restart_required': False,
        'warnings': [],
    }
    if not detected:
        result['warnings'].append('未检测到 DeepSeek Harness 配置目录；项目仍可用 Codex 入口。')
        return result
    if not patch.exists():
        result['warnings'].append('尚未建立 DSH profile patch；运行 install 会创建最小配置。')
        return result
    try:
        text = patch.read_text(encoding='utf-8')
    except OSError:
        result['warnings'].append('无法读取 DSH profile patch；未做任何修改。')
        return result
    mcp_status, servers = _mcp_matches(text, root)
    result['mcp'].update(status=mcp_status, servers=servers)
    result['skills']['status'] = 'configured' if _skills_configured(text, root) else 'missing'
    result['model'] = _model_config(text)
    if mcp_status == 'conflict':
        result['warnings'].append('已有同名 MCP 服务配置未指向当前项目；install 不会覆盖，请按 docs/DSH.md 手动核对。')
    if result['skills']['status'] == 'missing':
        result['warnings'].append('当前项目的 skills 目录尚未加入 DSH 技能扫描根；运行 install 后会加入并需要重启。')
    return result


def _build_block(project, add_mcp=True, add_skills=True):
    root = Path(project).resolve()
    lines = [BEGIN,
             '# 由 tools/onboarding/dsh_integration.py install 生成；重跑只更新本段，其他配置保留。',
             '# 删除本段即可回滚。']
    if add_mcp:
        lines += [
            '- insert:',
            '    - id: moodle-study',
            "      name: '@deepseek-ai/dsh-mcp-client'",
            '      config:',
            '        serverName: moodle_study',
            '        transport: stdio',
            '        command: ' + _yaml_quote(_node_command()),
            '        args:',
            '          - ' + _yaml_quote((root / 'tools/moodle/run.mjs').as_posix()),
            "          - 'mcp'",
            '        env: {}',
            '        cwd: ' + _yaml_quote(root.as_posix()),
            '        failOnStartupError: true',
        ]
    if add_skills:
        lines += [
            '- id: skill-filesystem',
            "  name: '@deepseek-ai/dsh-skill-filesystem'",
            '  config:',
            '    customSkillDirs:',
            '      - ' + _yaml_quote((root / 'skills').as_posix()),
        ]
    lines.append(END)
    return '\n'.join(lines) + '\n'


def install(project=None, env=None, dry_run=False):
    """Additively merge the DSH entries, preserving all existing configuration."""
    environment = os.environ if env is None else env
    root = Path(project or ROOT).resolve()
    profile = profile_dir(environment)
    patch = profile / 'cordis.patch.yml'
    base = {
        'project_root': root.as_posix(),
        'patch_path': patch.as_posix(),
        'backup': None,
        'warnings': [],
        'restart_required': False,
        'next_steps': ['修改 profile 配置后需重启 DeepSeek Harness 桌面版，新建聊天才会加载技能与 MCP。'],
    }
    status = inspect(root, environment)
    if not status['detected']:
        base.update(status='skipped', reason='未检测到 DeepSeek Harness；项目仍可用 Codex 入口，未写入任何配置。')
        return base
    if not profile.exists():
        base.update(status='skipped', reason='未找到 DSH profile 目录；请先启动过一次 DeepSeek Harness。')
        return base
    try:
        original = patch.read_text(encoding='utf-8') if patch.exists() else ''
    except OSError:
        base.update(status='skipped', reason='无法读取现有 DSH profile patch；未覆盖任何配置。')
        return base

    outside = BLOCK_RE.sub('', original)
    outside_servers = re.findall(r'serverName:\s*([A-Za-z0-9_-]+)', outside)
    add_mcp = SERVER_NAME not in outside_servers
    add_skills = not re.search(r'-\s+id:\s*skill-filesystem\b', outside)
    if not add_mcp:
        base['warnings'].append('已有同名 MCP 服务配置（未在生成段内），未覆盖；如它未指向当前项目，请按 docs/DSH.md 手动调整。')
    if not add_skills:
        base['warnings'].append('已有用户自定义 skill-filesystem 配置，未覆盖；请手动把当前项目 skills 目录加入 customSkillDirs。')
    block = _build_block(root, add_mcp=add_mcp, add_skills=add_skills)
    if not add_mcp and not add_skills:
        base.update(status='skipped', reason='现有配置与生成内容冲突，未修改；请按 docs/DSH.md 手动合并。')
        return base

    new_text = (BLOCK_RE.sub('', original).rstrip('\n') + '\n\n' + block) if original.strip() else block
    if new_text == original:
        base.update(status='unchanged')
        return base
    base['mcp_added'] = add_mcp
    base['skills_added'] = add_skills
    base['restart_required'] = True
    if dry_run:
        base.update(status='dry-run', content=new_text)
        return base
    try:
        if patch.exists():
            target = patch.with_name(patch.name + '.bak-' + _state_stem())
            target.write_bytes(patch.read_bytes())
            base['backup'] = target.as_posix()
        temporary = patch.with_name(patch.name + '.tmp')
        temporary.write_text(new_text, encoding='utf-8', newline='\n')
        os.replace(temporary, patch)
    except OSError as error:
        base.update(status='error', reason='写入 DSH profile patch 失败：%s。原配置未删除。' % type(error).__name__)
        return base
    base.update(status='installed')
    return base


def _print_human(result):
    if result.get('status') in ('installed', 'unchanged', 'dry-run'):
        print('DSH 配置：' + result['status'])
        print('  profile patch：' + result['patch_path'])
        if result.get('backup'):
            print('  备份：' + result['backup'])
        for warning in result.get('warnings', []):
            print('  提醒：' + warning)
        for step in result.get('next_steps', []):
            print('  下一步：' + step)
    else:
        print('DSH 配置未修改：' + result.get('reason', result.get('status', 'unknown')))
        for warning in result.get('warnings', []):
            print('  提醒：' + warning)
    if result.get('content'):
        print('\n需要手动粘贴的配置段：\n' + result['content'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'install', 'print'])
    parser.add_argument('--project', type=Path, default=ROOT, help='本次用于 DSH 的项目根目录')
    parser.add_argument('--dry-run', action='store_true', help='只显示将要写入的内容，不修改文件')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args(argv)
    if args.command == 'status':
        result = inspect(args.project)
    elif args.command == 'install':
        result = install(args.project, dry_run=args.dry_run)
    else:
        result = {'status': 'print', 'content': _build_block(args.project),
                  'patch_path': patch_path().as_posix()}
    if args.json or args.command != 'install':
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0 if result.get('status') not in ('skipped', 'error') else 1


if __name__ == '__main__':
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    sys.exit(main())
