"""DeepSeek Harness (DSH) integration: structural status and validated merge.

The public project keeps working in Codex: this module never changes the Codex
entry point and never touches ``Study/``, ``.private/``, course material or
learning records. It only reads (``status``) or explicitly merges (``install``)
two entries into the user's DSH profile patch:

* an MCP client entry that starts this deployment's read-only Moodle server;
* a skill provider override that adds this deployment's ``skills/`` directory so
  the bundled teaching skill appears in the DSH skill catalog.

Configuration handling is structural: the profile patch is parsed as a YAML
document and inspected entry by entry, so comments, commented-out examples and
unrelated text never count as configuration. ``install`` merges additively,
keeps every existing entry, writes a timestamped backup, and validates the
complete final document before replacing the original file. A failed parse or
failed validation leaves the original file untouched.

The reported status distinguishes "configuration is correct" from "the running
client has loaded it": only a restarted client can prove runtime loading.
"""

import argparse
import copy
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # bootstrap diagnostics before setup installs dependencies
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
STUDY_TOOLS = Path(__file__).resolve().parents[1] / 'study'
if str(STUDY_TOOLS) not in sys.path:
    sys.path.insert(0, str(STUDY_TOOLS))

import chat_identity  # noqa: E402  (needs the path insertion above)

BEGIN = '# >>> moodle-study-assistant DSH integration (generated) >>>'
END = '# <<< moodle-study-assistant DSH integration (generated) <<<'
SERVER_NAME = 'moodle_study'
MCP_CLIENT = '@deepseek-ai/dsh-mcp-client'
SKILL_PROVIDER = '@deepseek-ai/dsh-skill-filesystem'
BLOCK_RE = re.compile(re.escape(BEGIN) + r'.*?' + re.escape(END) + r'\n?', re.DOTALL)
EMPTY_FLOW_LIST = re.compile(r'^[ \t]*\[\][ \t]*(?:#.*)?$')

RUNTIME_NOTE = '配置正确本身不证明运行时已加载；需要重启 DeepSeek Harness 后在新聊天中实际确认。'

_JS_TAG = 'tag:yaml.org,2002:js'


def _make_loader():
    """Safe YAML loader that accepts DSH's ``!!js`` expressions and other tags."""
    class DshSafeLoader(yaml.SafeLoader):
        pass

    def js_value(loader, node):
        return {'__dsh_js__': loader.construct_scalar(node)}

    DshSafeLoader.add_constructor(_JS_TAG, js_value)
    DshSafeLoader.add_constructor('!js', js_value)

    def unknown_tag(loader, suffix, node):
        if isinstance(node, yaml.ScalarNode):
            value = loader.construct_scalar(node)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node, deep=True)
        else:
            value = loader.construct_mapping(node, deep=True)
        return {'__dsh_tag__': suffix, 'value': value}

    DshSafeLoader.add_multi_constructor('!', unknown_tag)
    DshSafeLoader.add_multi_constructor('tag:yaml.org,2002:', unknown_tag)
    return DshSafeLoader


def _yaml_loader():
    global _LOADER
    if _LOADER is None:
        _LOADER = _make_loader()
    return _LOADER


_LOADER = None


class PatchError(ValueError):
    """The profile patch cannot be safely read, merged or validated."""


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
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _node_command():
    found = shutil.which('node')
    return Path(found).as_posix() if found else 'node'


def _yaml_quote(value):
    text = str(value).replace("'", "''")
    return "'" + text + "'"


def _require_yaml():
    if yaml is None:
        raise PatchError('缺少 YAML 解析依赖（PyYAML）；请先完成安装（setup）。')


def _load_document(text, label='profile patch'):
    _require_yaml()
    try:
        data = yaml.load(text, Loader=_yaml_loader()) if text.strip() else []
    except yaml.YAMLError as error:
        raise PatchError('%s 不是有效 YAML：%s' % (label, _yaml_error_summary(error)))
    if data is None:
        data = []
    if not isinstance(data, list):
        raise PatchError('%s 的顶层不是 YAML 列表，未做任何修改；请手动合并。' % label)
    return data


def _yaml_error_summary(error):
    mark = getattr(error, 'problem_mark', None)
    problem = getattr(error, 'problem', None) or 'syntax'
    if mark is not None:
        return '%s（行 %d，列 %d）' % (str(problem)[:120], mark.line + 1, mark.column + 1)
    return str(problem)[:120]


def _walk_dicts(value):
    """Yield every mapping inside a parsed patch, including nested insert lists."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _mcp_entries(data):
    entries = []
    for item in _walk_dicts(data):
        if item.get('name') != MCP_CLIENT:
            continue
        config = item.get('config')
        if not isinstance(config, dict):
            continue
        if config.get('serverName') != SERVER_NAME:
            continue
        entries.append(config)
    return entries


def _entry_path_matches(config, project):
    args = config.get('args')
    if not isinstance(args, list) or len(args) < 2 or args[0] != str(args[0]):
        return False
    if str(args[1]) != 'mcp':
        return False
    try:
        return Path(str(args[0])).expanduser().resolve() == (Path(project) / 'tools/moodle/run.mjs').resolve()
    except (OSError, ValueError):
        return False


def _mcp_status(data, project):
    configs = _mcp_entries(data)
    matching = [config for config in configs if _entry_path_matches(config, project)]
    commands = {str(config.get('command')) for config in configs if config.get('command')}
    status = {
        'status': 'missing',
        'entry_count': len(configs),
        'path_matches': bool(matching),
        'command_available': False,
        'warnings': [],
    }
    if matching:
        status['status'] = 'configured'
        status['command_available'] = any(_command_available(str(config.get('command') or '')) for config in matching)
        if not status['command_available']:
            status['warnings'].append('MCP 启动命令在当前环境不可用；运行时可能无法启动服务器。')
    elif configs:
        status['status'] = 'conflict'
        status['warnings'].append('已有同名 MCP 服务配置，但未指向当前项目；不会覆盖。')
    return status


def _command_available(command):
    if not command.strip():
        return False
    candidate = Path(os.path.expanduser(command))
    if candidate.is_absolute():
        return candidate.is_file()
    return shutil.which(command) is not None


def _skill_status(data, project):
    expected = (Path(project) / 'skills').resolve()
    overrides = []
    for item in _walk_dicts(data):
        if item.get('id') != 'skill-filesystem' and item.get('name') != SKILL_PROVIDER:
            continue
        config = item.get('config')
        if isinstance(config, dict):
            overrides.append(config)
    status = {'status': 'missing', 'override_count': len(overrides),
              'expected_dir': expected.as_posix(), 'warnings': []}
    if not expected.is_dir():
        status['warnings'].append('当前项目的 skills 目录不存在。')
    for config in overrides:
        dirs = config.get('customSkillDirs')
        if not isinstance(dirs, list):
            continue
        for value in dirs:
            if not isinstance(value, str):
                continue
            try:
                if Path(value).expanduser().resolve() == expected:
                    status['status'] = 'configured'
                    return status
            except (OSError, ValueError):
                continue
    if overrides:
        status['warnings'].append('已有 skill-filesystem 覆盖但未包含当前项目 skills 目录；不会覆盖。')
    return status


def _model_status(data):
    for item in _walk_dicts(data):
        if item.get('id') != 'agent-default-model':
            continue
        config = item.get('config')
        if not isinstance(config, dict):
            continue
        result = {}
        for key in ('provider', 'model', 'reasoningEffort'):
            if isinstance(config.get(key), str):
                result[key] = config[key]
        return result
    return {}


def _runtime_status(env):
    environment = os.environ if env is None else env
    inside = bool((environment.get('DSH_SESSION_ID') or '').strip())
    return {'in_harness_chat': inside, 'loaded': 'unverified', 'note': RUNTIME_NOTE}


def inspect(project=None, env=None):
    """Return a shareable status snapshot; never reads credentials or records."""
    environment = os.environ if env is None else env
    root = Path(project or ROOT).resolve()
    folder = profile_dir(environment)
    patch = folder / 'cordis.patch.yml'
    detected = patch.exists() or dsh_home(environment).exists() or bool((environment.get('DSH_SESSION_ID') or '').strip())
    result = {
        'detected': detected,
        'dsh_home': dsh_home(environment).as_posix(),
        'profile_dir': folder.as_posix(),
        'patch_path': patch.as_posix(),
        'patch_exists': patch.exists(),
        'project_root': root.as_posix(),
        'patch_parse': 'unavailable' if yaml is None else 'missing',
        'configuration_ok': False,
        'runtime_loaded': 'unverified',
        'runtime': _runtime_status(environment),
        'mcp': {'status': 'missing', 'entry_count': 0, 'path_matches': False},
        'skills': {'status': 'missing', 'expected_dir': (root / 'skills').as_posix()},
        'model': {},
        'chat': chat_identity.describe(environment),
        'warnings': [],
    }
    if yaml is None:
        result['warnings'].append('缺少 YAML 解析依赖（PyYAML）；完成安装后可检查 DSH 配置。')
        return result
    if not detected:
        result['warnings'].append('未检测到 DeepSeek Harness 配置目录；项目仍可用 Codex 入口。')
        return result
    if not patch.exists():
        result['patch_parse'] = 'missing'
        result['warnings'].append('尚未建立 DSH profile patch；运行 install 会创建最小配置。')
        return result
    try:
        text = patch.read_text(encoding='utf-8-sig')
        data = _load_document(text)
    except (OSError, PatchError) as error:
        result['patch_parse'] = 'invalid'
        result['warnings'].append('无法解析 DSH profile patch：%s；未做任何修改。' % str(error))
        return result
    result['patch_parse'] = 'ok'
    result['mcp'] = _mcp_status(data, root)
    result['skills'] = _skill_status(data, root)
    result['model'] = _model_status(data)
    result['configuration_ok'] = (result['mcp']['status'] == 'configured'
                                  and result['skills']['status'] == 'configured')
    for part in (result['mcp'], result['skills']):
        result['warnings'].extend(part.get('warnings', []))
    return result


def _strip_generated(text):
    """Remove a previously generated block; returns (text, had_block)."""
    if BEGIN not in text:
        return text, False
    cleaned = BLOCK_RE.sub('', text)
    if BEGIN in cleaned:
        raise PatchError('生成的配置段标记重复或未闭合，未做任何修改；请手动清理。')
    return cleaned, True


def _normalise_empty_list(text, data):
    """Turn a standalone legal ``[]`` document into comments + space for entries."""
    if data:
        return text
    lines = text.split('\n')
    kept = []
    for line in lines:
        if EMPTY_FLOW_LIST.match(line):
            comment = line.split('#', 1)[1].strip() if '#' in line else ''
            if comment:
                kept.append('# ' + comment)
            continue
        kept.append(line)
    return '\n'.join(kept)


def _build_entries(project, add_mcp=True, add_skills=True):
    """Return the YAML data entries the generated block represents."""
    root = Path(project).resolve()
    entries = []
    if add_mcp:
        entries.append({
            'insert': [{
                'id': 'moodle-study',
                'name': MCP_CLIENT,
                'config': {
                    'serverName': SERVER_NAME,
                    'transport': 'stdio',
                    'command': _node_command(),
                    'args': [(root / 'tools/moodle/run.mjs').as_posix(), 'mcp'],
                    'env': {},
                    'cwd': root.as_posix(),
                    'failOnStartupError': True,
                },
            }],
        })
    if add_skills:
        entries.append({
            'id': 'skill-filesystem',
            'name': SKILL_PROVIDER,
            'config': {'customSkillDirs': [(root / 'skills').as_posix()]},
        })
    return entries


def _render_block(project, add_mcp=True, add_skills=True, plain=False):
    """Render the generated block as text; validated by the caller.

    ``plain`` omits the generated-block markers so an adopted entry stays an
    ordinary user-visible configuration entry (it is not stripped on rerun).
    """
    lines = []
    if not plain:
        lines += [BEGIN,
                  '# 由 tools/onboarding/dsh_integration.py install 生成；重跑只更新本段，其他配置保留。',
                  '# 删除本段即可回滚。']
    if add_mcp:
        lines += [
            '- insert:',
            '    - id: moodle-study',
            "      name: '%s'" % MCP_CLIENT,
            '      config:',
            '        serverName: %s' % SERVER_NAME,
            '        transport: stdio',
            '        command: ' + _yaml_quote(_node_command()),
            '        args:',
            '          - ' + _yaml_quote((Path(project) / 'tools/moodle/run.mjs').as_posix()),
            "          - 'mcp'",
            '        env: {}',
            '        cwd: ' + _yaml_quote(Path(project).as_posix()),
            '        failOnStartupError: true',
        ]
    if add_skills:
        lines += [
            '- id: skill-filesystem',
            "  name: '%s'" % SKILL_PROVIDER,
            '  config:',
            '    customSkillDirs:',
            '      - ' + _yaml_quote((Path(project) / 'skills').as_posix()),
        ]
    if not plain:
        lines.append(END)
    return '\n'.join(lines) + '\n'

def _compose(base_text, project, add_mcp, add_skills):
    """Compose and fully validate the final document; never writes anything."""
    base_text, _ = _strip_generated(base_text)
    base_data = _load_document(base_text, '移除生成段后的 profile patch')
    normalised = _normalise_empty_list(base_text, base_data)
    if not add_mcp and not add_skills:
        # An adopted existing entry changed other text already; do not append a
        # generated block, but still validate the complete document.
        new_text = normalised.rstrip('\n') + '\n'
        final_data = _load_document(new_text, '更新现有条目后的 profile patch')
        if final_data != base_data:
            raise PatchError('更新现有条目后的 profile 结构与预期不一致，未做任何修改。')
        return new_text, base_data, []
    block = _render_block(project, add_mcp=add_mcp, add_skills=add_skills)
    generated = _load_document(block, '生成的配置段')
    if not isinstance(generated, list) or len(generated) != (int(add_mcp) + int(add_skills)):
        raise PatchError('生成的配置段结构异常，未做任何修改。')
    if normalised.strip():
        new_text = normalised.rstrip('\n') + '\n\n' + block
    else:
        new_text = block
    if not new_text.endswith('\n'):
        new_text += '\n'
    final_data = _load_document(new_text, '合并后的 profile patch')
    expected = base_data + generated
    if final_data != expected:
        raise PatchError('合并后的 profile 结构与预期不一致，未做任何修改。')
    if add_mcp:
        if [config for config in _mcp_entries(final_data) if _entry_path_matches(config, project)] == []:
            raise PatchError('合并后仍未发现指向当前项目的 MCP 条目，未做任何修改。')
    if add_skills and _skill_status(final_data, project)['status'] != 'configured':
        raise PatchError('合并后仍未发现当前项目的技能目录配置，未做任何修改。')
    return new_text, base_data, generated


def _entry_region(lines, start, end):
    """Return (entry_start, entry_end, indent) for the server line inside a block."""
    index = None
    for candidate in range(start, end):
        if re.match(r'\s*serverName:\s*[\'"]?' + re.escape(SERVER_NAME) + r'[\'"]?\s*(?:#.*)?$',
                    lines[candidate]):
            index = candidate
            break
    if index is None:
        return None
    server_indent = len(lines[index]) - len(lines[index].lstrip(' \t'))
    entry_start = start
    entry_indent = 0
    for candidate in range(index - 1, start - 1, -1):
        match = re.match(r'^([ \t]*)-\s', lines[candidate])
        if match and len(match.group(1)) < server_indent:
            entry_start = candidate
            entry_indent = len(match.group(1))
            break
    entry_end = entry_start + 1
    prefix = ' ' * entry_indent
    while entry_end < end and not re.match('^' + re.escape(prefix) + r'-[ \t]', lines[entry_end]):
        entry_end += 1
    return entry_start, entry_end, entry_indent


def _find_args_region(lines, start, end):
    """Locate the ``args:`` value region inside one MCP entry."""
    for index in range(start, end):
        match = re.match(r'^([ \t]*)args\s*:(.*)$', lines[index])
        if not match:
            continue
        indent = match.group(1)
        rest = match.group(2).strip()
        if rest:
            depth = 0
            quote = None
            escaped = False
            cursor = index
            text = rest
            while True:
                for char in text:
                    if quote:
                        if escaped:
                            escaped = False
                        elif char == '\\' and quote == '"':
                            escaped = True
                        elif char == quote:
                            quote = None
                        continue
                    if char in '\'"':
                        quote = char
                    elif char in '[{':
                        depth += 1
                    elif char in ']}':
                        depth -= 1
                if depth <= 0 and quote is None:
                    return index, cursor + 1, indent
                cursor += 1
                if cursor >= end:
                    return None
                text = lines[cursor]
        region_end = index + 1
        cursor = index + 1
        indent_width = len(indent)
        while cursor < end:
            line = lines[cursor]
            if not line.strip():
                cursor += 1
                continue
            line_indent = len(line) - len(line.lstrip(' \t'))
            if line_indent <= indent_width:
                break
            region_end = cursor + 1
            cursor += 1
        return index, region_end, indent
    return None


def _adopt_existing_mcp(base_text, base_data, project):
    """Update only the target Moodle MCP entry's ``args`` field.

    Returns ``(new_text, changed, problem)``. The update is limited to the
    ``args`` value of the one MCP client entry whose ``serverName`` is
    ``moodle_study``; other entries, comments, fields and settings are kept
    byte-for-byte. The caller re-parses and validates the complete result.
    """
    current = (Path(project) / 'tools/moodle/run.mjs').resolve()
    entries = _mcp_entries(base_data)
    if len(entries) != 1:
        return base_text, 0, '无法唯一定位现有的 moodle_study MCP 条目'
    args = entries[0].get('args')
    if not isinstance(args, list) or len(args) < 2 or str(args[1]) != 'mcp':
        return base_text, 0, '现有 moodle_study 条目的 args 结构不可识别'
    old = str(args[0])
    if not old.replace('\\', '/').endswith('/tools/moodle/run.mjs'):
        return base_text, 0, '现有 moodle_study 条目的 args 不是本项目 run.mjs'
    try:
        if Path(old).expanduser().resolve() == current:
            return base_text, 0, ''
    except (OSError, ValueError):
        return base_text, 0, '现有 moodle_study 条目的 args 路径不可解析'
    if any(not isinstance(value, (str, int, float, bool)) for value in args):
        return base_text, 0, '现有 moodle_study 条目的 args 含非标量值，未自动改写'

    lines = base_text.split('\n')
    blocks = []
    index = 0
    while index < len(lines):
        if re.match(r'^-[ \t]', lines[index]):
            block_end = index + 1
            while block_end < len(lines) and not re.match(r'^-[ \t]', lines[block_end]):
                block_end += 1
            if any(re.search(r'name:\s*[\'"]?' + re.escape(MCP_CLIENT), line)
                   for line in lines[index:block_end]):
                if any(re.match(r'\s*serverName:\s*[\'"]?' + re.escape(SERVER_NAME), line)
                       for line in lines[index:block_end]):
                    blocks.append((index, block_end))
            index = block_end
        else:
            index += 1
    if len(blocks) != 1:
        return base_text, 0, '无法唯一定位现有的 moodle_study MCP 条目'
    block_start, block_end = blocks[0]
    region = _entry_region(lines, block_start, block_end)
    if region is None:
        return base_text, 0, '现有条目缺少 serverName'
    entry_start, entry_end, _ = region
    args_region = _find_args_region(lines, entry_start, entry_end)
    if args_region is None:
        return base_text, 0, '现有条目缺少可识别的 args 字段'
    args_line, args_end, indent = args_region
    new_args = [current.as_posix()] + [value for value in args[1:]]
    replacement = [indent + 'args:'] + [
        indent + '  - ' + _yaml_quote(value) for value in new_args]
    new_text = '\n'.join(lines[:args_line] + replacement + lines[args_end:])
    try:
        after = _load_document(new_text, '更新现有 MCP 条目后的 profile patch')
    except PatchError as error:
        return base_text, 0, str(error)
    expected = copy.deepcopy(base_data)
    expected_configs = _mcp_entries(expected)
    if len(expected_configs) != 1:
        return base_text, 0, '内部校验无法定位目标条目'
    expected_configs[0]['args'] = new_args
    if after != expected:
        return base_text, 0, '替换 args 会改变目标以外的配置，未做任何修改'
    return new_text, 1, ''


def install(project=None, env=None, dry_run=False, update_existing=False):
    """Additively merge the DSH entries; validate before replacing the file."""
    environment = os.environ if env is None else env
    root = Path(project or ROOT).resolve()
    folder = profile_dir(environment)
    patch = folder / 'cordis.patch.yml'
    base = {
        'project_root': root.as_posix(),
        'patch_path': patch.as_posix(),
        'backup': None,
        'validated': False,
        'warnings': [],
        'restart_required': False,
        'next_steps': ['修改 profile 配置后需重启 DeepSeek Harness 桌面版；新聊天才会加载技能与 MCP。'],
    }
    status = inspect(root, environment)
    if not status['detected']:
        base.update(status='skipped', reason='未检测到 DeepSeek Harness；项目仍可用 Codex 入口，未写入任何配置。')
        return base
    if yaml is None:
        base.update(status='error', reason='缺少 YAML 解析依赖（PyYAML）；未修改任何配置。请先完成安装。')
        return base
    if not folder.exists():
        base.update(status='skipped', reason='未找到 DSH profile 目录；请先启动过一次 DeepSeek Harness。')
        return base
    try:
        original = patch.read_text(encoding='utf-8-sig') if patch.exists() else ''
    except OSError:
        base.update(status='skipped', reason='无法读取现有 DSH profile patch；未覆盖任何配置。')
        return base
    had_bom = patch.exists() and patch.read_bytes().startswith(b'\xef\xbb\xbf')

    try:
        base_text, _ = _strip_generated(original)
        base_data = _load_document(base_text, '现有 profile patch')
        adopted = 0
        adopt_problem = ''
        if update_existing:
            base_text, adopted, adopt_problem = _adopt_existing_mcp(base_text, base_data, root)
            if adopted:
                base_data = _load_document(base_text, '更新现有 MCP 条目后的 profile patch')
                base['warnings'].append('已将现有 moodle_study MCP 条目的 args 路径更新为当前项目；只改该字段。')
            elif adopt_problem:
                base['warnings'].append('未能自动更新现有条目：' + adopt_problem + '；未覆盖任何配置。')
        existing_mcp = _mcp_entries(base_data)
        matching_mcp = [config for config in existing_mcp if _entry_path_matches(config, root)]
        if matching_mcp:
            add_mcp = False
            base['warnings'].append('已有指向当前项目的 moodle_study MCP 条目；未重复添加。')
        elif existing_mcp:
            add_mcp = False
            base['warnings'].append('已有同名 MCP 服务配置，但未指向当前项目；未覆盖。'
                                    '确认这是旧部署后可用 --update-existing 只更新该路径。')
        else:
            add_mcp = True
        add_skills = _skill_status(base_data, root)['status'] != 'configured'
        if not add_skills:
            base['warnings'].append('已有用户自定义 skill-filesystem 配置包含当前项目目录；未重复添加。')
        elif any(item.get('id') == 'skill-filesystem' for item in _walk_dicts(base_data)):
            add_skills = False
            base['warnings'].append('已有用户自定义 skill-filesystem 配置但未包含当前项目 skills 目录；'
                                    '未覆盖，请按 docs/DSH.md 手动合并。')
        if not add_mcp and not add_skills and not adopted:
            base.update(status='unchanged', reason='现有配置已满足要求，未修改文件。')
            return base
        new_text, base_data, generated = _compose(base_text, root, add_mcp=add_mcp, add_skills=add_skills)
        if adopted and not [config for config in _mcp_entries(base_data)
                            if _entry_path_matches(config, root)]:
            raise PatchError('更新现有条目后仍未发现指向当前项目的 MCP 配置，保留原文件。')
    except PatchError as error:
        base.update(status='error', reason=str(error))
        return base

    base['mcp_added'] = add_mcp
    base['skills_added'] = add_skills
    base['updated_existing'] = bool(adopted)
    base['restart_required'] = True
    if patch.exists() and new_text.rstrip('\n') == original.rstrip('\n'):
        base.update(status='unchanged', reason='现有配置与生成内容一致，未修改文件。')
        return base
    if dry_run:
        base.update(status='dry-run', validated=True, content=new_text)
        return base
    try:
        if patch.exists():
            backup = patch.with_name(patch.name + '.bak-' + _state_stem())
            backup.write_bytes(patch.read_bytes())
            base['backup'] = backup.as_posix()
        temporary = patch.with_name(patch.name + '.tmp')
        temporary.write_text(('\ufeff' if had_bom else '') + new_text, encoding='utf-8', newline='\n')
        # Validate the exact bytes that will replace the original file.
        written = temporary.read_text(encoding='utf-8-sig')
        final_data = _load_document(written, '临时目标文件')
        if final_data != (base_data + generated):
            raise PatchError('临时目标文件的复核结构与预期不一致，保留原文件。')
        os.replace(temporary, patch)
        base['validated'] = True
    except (OSError, PatchError) as error:
        try:
            temporary = patch.with_name(patch.name + '.tmp')
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        base.update(status='error', reason='写入或校验 DSH profile patch 失败：%s。原文件未替换。' % str(error)[:160])
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
    parser.add_argument('--update-existing', action='store_true',
                        help='仅把现有同名 moodle_study MCP 条目的 run.mjs 路径更新为当前项目')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args(argv)
    if args.command == 'status':
        result = inspect(args.project)
    elif args.command == 'install':
        result = install(args.project, dry_run=args.dry_run, update_existing=args.update_existing)
    else:
        result = {'status': 'print', 'content': _render_block(args.project),
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
