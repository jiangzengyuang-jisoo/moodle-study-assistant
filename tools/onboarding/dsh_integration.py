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

``install`` reports one of: ``installed`` (entries merged or repaired; both the
MCP entry and the skills directory are configured), ``unchanged`` (already
correct and fully validated; nothing written), ``conflict`` (an existing entry
conflicts with this project, including two entries sharing the ``moodle_study``
serverName; nothing written), ``error`` (e.g. this project's ``skills/``
directory is missing) or ``skipped`` (no complete result). Every result carries
``configuration_ok`` covering both parts plus the per-part ``mcp``/``skills``
status and actionable ``warnings``/``next_steps``. When a previously generated
block is found, install rebuilds or removes it and validates the complete
persisted document instead of trusting the stripped copy; it never reports
``unchanged`` for a document that still parses to a duplicated or missing
entry. The CLI exits 0 only for a successful and fully configured merge;
conflicts and errors exit non-zero so an installer cannot report a false
success. ``--update-existing`` refuses to guess when several same-name entries
exist and points at manual deduplication instead.
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
SUCCESS_STATUSES = ('installed', 'unchanged', 'dry-run')
FAILURE_STATUSES = ('conflict', 'skipped', 'error')
DEFAULT_NEXT_STEPS = [
    '修改 profile 配置后需重启 DeepSeek Harness 桌面版；新聊天才会加载技能与 MCP。',
    '重启后回到项目聊天，由聊天内助手完成学校登录；MCP 工具在登录后才可用。',
    '配置正确不等于运行时已验证：请在重启后的新聊天中确认技能与 moodle_study MCP 工具已加载。',
]

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
    status = {
        'status': 'missing',
        'entry_count': len(configs),
        'path_matches': bool(matching),
        'command_available': False,
        'duplicate': len(configs) > 1,
        'warnings': [],
    }
    if status['duplicate']:
        # Never silently pick or overwrite one of several same-name servers.
        status['status'] = 'conflict'
        status['warnings'].append(
            '发现 %d 条同名 moodle_study MCP 配置；同一 serverName 不唯一，'
            '不会自动选择或覆盖任何一条。' % len(configs))
        return status
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


def _skill_entries(data):
    """Return every user entry that defines the DSH skill provider."""
    entries = []
    for item in _walk_dicts(data):
        if item.get('id') != 'skill-filesystem' and item.get('name') != SKILL_PROVIDER:
            continue
        entries.append(item)
    return entries


def _skill_status(data, project):
    expected = (Path(project) / 'skills').resolve()
    entries = _skill_entries(data)
    overrides = [item['config'] for item in entries if isinstance(item.get('config'), dict)]
    status = {'status': 'missing', 'override_count': len(entries),
              'expected_dir': expected.as_posix(), 'warnings': []}
    includes_expected = False
    for config in overrides:
        dirs = config.get('customSkillDirs')
        if not isinstance(dirs, list):
            continue
        for value in dirs:
            if not isinstance(value, str):
                continue
            try:
                if Path(value).expanduser().resolve() == expected:
                    includes_expected = True
                    break
            except (OSError, ValueError):
                continue
        if includes_expected:
            break
    if not expected.is_dir():
        # A configuration that points at a missing directory can never load the
        # bundled skill: report it as invalid, not configured, and never build a
        # profile entry around it.
        status['status'] = 'invalid'
        status['warnings'].append('当前项目的 skills 目录不存在：' + expected.as_posix()
                                  + '；技能无法加载，不能报告配置成功。')
        if includes_expected:
            status['warnings'].append('现有 skill-filesystem 配置指向了不存在的目录；'
                                      '恢复完整项目文件后才会生效。')
        return status
    if includes_expected:
        status['status'] = 'configured'
        return status
    if entries:
        status['status'] = 'conflict'
        status['warnings'].append('已有 skill-filesystem 覆盖但未包含当前项目 skills 目录；不会覆盖，也不会重复添加。')
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

def _validate_final_document(data, project, label='最终配置'):
    """Validate the document that would actually be persisted.

    Completion requires exactly one moodle_study MCP entry that points at this
    project and a skills override whose directory exists. Anything else is an
    error; callers must not report success for a document that fails this.
    """
    mcp = _mcp_status(data, project)
    skills = _skill_status(data, project)
    if mcp.get('status') != 'configured' or int(mcp.get('entry_count') or 0) != 1:
        raise PatchError('%s的 MCP 配置不唯一或未指向当前项目（status=%s，条目数=%s）；未做任何修改。'
                         % (label, mcp.get('status'), mcp.get('entry_count')))
    if skills.get('status') != 'configured':
        raise PatchError('%s的技能目录配置不完整（status=%s）；未做任何修改。'
                         % (label, skills.get('status')))
    return mcp, skills


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
        _validate_final_document(final_data, project, '更新现有条目后的 profile patch')
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
    _validate_final_document(final_data, project, '合并后的 profile patch')
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
        if entries:
            return base_text, 0, ('发现 %d 条同名 moodle_study MCP 条目，--update-existing 无法唯一定位；'
                                  '请手动去重后重试' % len(entries))
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


def _next_steps_for_conflict(mcp_part, skills_part):
    """Actionable repairs for a conflicting profile; never asks for a delete."""
    steps = []
    if mcp_part.get('status') == 'conflict':
        if mcp_part.get('duplicate'):
            steps.append('MCP 重复：profile 中有 %d 条同名 moodle_study MCP 条目，无法唯一确定使用哪一条；'
                         '--update-existing 也无法唯一定位。请按 docs/DSH.md 手动去重：只保留一条指向当前项目的'
                         '条目，删除或改掉旧部署的重复条目后重试。' % int(mcp_part.get('entry_count') or 0))
        else:
            steps.append('MCP 冲突：现有 moodle_study MCP 条目未指向当前项目。确认它是旧部署后运行'
                         ' & .venv/Scripts/python.exe tools/onboarding/dsh_integration.py install --update-existing '
                         '（只更新该条目的 run.mjs 路径）；否则按 docs/DSH.md 手动合并。')
    if skills_part.get('status') == 'conflict':
        steps.append('技能冲突：现有 skill-filesystem 覆盖未包含当前项目 skills 目录。'
                     '按 docs/DSH.md 合并或移除旧覆盖（把当前项目 skills 目录加入 customSkillDirs）后重试。')
    steps.append('不要删除现有配置后重试；冲突未解决前不会写入任何内容，原文件保持不变。')
    return steps


def install(project=None, env=None, dry_run=False, update_existing=False):
    """Additively merge the DSH entries; validate before replacing the file.

    ``configuration_ok`` describes the resulting persisted configuration (for
    ``dry-run``, the configuration the shown content would produce). An existing
    conflicting entry (including duplicated same-name MCP entries) or a missing
    project ``skills/`` directory stops the merge before any write: nothing is
    overwritten and the original file is kept. A previously generated block is
    rebuilt or removed so the persisted document is validated as a whole. Only
    ``installed``/``unchanged``/``dry-run`` with ``configuration_ok`` true are
    complete; ``conflict``, ``skipped`` and ``error`` are not.
    """
    environment = os.environ if env is None else env
    root = Path(project or ROOT).resolve()
    folder = profile_dir(environment)
    patch = folder / 'cordis.patch.yml'
    base = {
        'project_root': root.as_posix(),
        'patch_path': patch.as_posix(),
        'backup': None,
        'validated': False,
        'configuration_ok': False,
        'mcp': {'status': 'unknown'},
        'skills': {'status': 'unknown'},
        'warnings': [],
        'restart_required': False,
        'next_steps': list(DEFAULT_NEXT_STEPS),
    }
    status = inspect(root, environment)
    if not status['detected']:
        base.update(status='skipped', reason='未检测到 DeepSeek Harness；项目仍可用 Codex 入口，未写入任何配置。',
                    next_steps=['在 DeepSeek Harness 聊天中重新运行安装；项目仍可用 Codex 入口，无需删除任何配置。'])
        return base
    if yaml is None:
        base.update(status='error', reason='缺少 YAML 解析依赖（PyYAML）；未修改任何配置。请先完成安装。',
                    next_steps=['先完成安装（setup 会安装 PyYAML）后重试；不要删除现有配置。'])
        return base
    if not folder.exists():
        base.update(status='skipped', reason='未找到 DSH profile 目录；请先启动过一次 DeepSeek Harness。',
                    next_steps=['先启动一次 DeepSeek Harness 创建 profile 目录，再重新运行安装；不要删除现有配置。'])
        return base
    try:
        original = patch.read_text(encoding='utf-8-sig') if patch.exists() else ''
    except OSError:
        base.update(status='skipped', reason='无法读取现有 DSH profile patch；未覆盖任何配置。',
                    next_steps=['检查 profile patch 的文件权限后重试；不要删除现有配置。'])
        return base
    had_bom = patch.exists() and patch.read_bytes().startswith(b'\xef\xbb\xbf')

    try:
        base_text, had_block = _strip_generated(original)
        base_data = _load_document(base_text, '现有 profile patch')
        mcp_part = _mcp_status(base_data, root)
        skills_part = _skill_status(base_data, root)
        adopted = 0
        if update_existing and mcp_part['status'] == 'conflict':
            base_text, adopted, adopt_problem = _adopt_existing_mcp(base_text, base_data, root)
            if adopted:
                base_data = _load_document(base_text, '更新现有 MCP 条目后的 profile patch')
                mcp_part = _mcp_status(base_data, root)
                skills_part = _skill_status(base_data, root)
                base['warnings'].append('已将现有 moodle_study MCP 条目的 args 路径更新为当前项目；只改该字段。')
            elif adopt_problem:
                base['warnings'].append('未能自动更新现有 MCP 条目：' + adopt_problem + '；未覆盖任何配置。')
        base['mcp'] = mcp_part
        base['skills'] = skills_part
        base['warnings'].extend(mcp_part.get('warnings', []))
        base['warnings'].extend(skills_part.get('warnings', []))
        conflicting = []
        if mcp_part.get('status') == 'conflict':
            conflicting.append('MCP 条目')
        if skills_part.get('status') == 'conflict':
            conflicting.append('技能目录')
        if conflicting:
            base.update(status='conflict',
                        reason='未写入任何内容（原文件保持不变）：' + '、'.join(conflicting)
                               + '。请按下一步提示修复后重试。',
                        next_steps=_next_steps_for_conflict(mcp_part, skills_part))
            return base
        if skills_part.get('status') == 'invalid':
            # Writing an entry that points at a missing directory would only
            # produce a false success followed by an invalid status.
            base.update(status='error',
                        reason='未写入任何内容（原文件保持不变）：当前项目的 skills 目录不存在（'
                               + str(skills_part.get('expected_dir', '')) + '），技能无法加载。',
                        next_steps=['恢复完整的项目文件（当前项目缺少 skills/ 目录），然后重新运行安装。',
                                    '不要删除现有配置；项目文件恢复后 MCP 与技能目录都需要重新验证。'])
            return base
        add_mcp = mcp_part.get('status') == 'missing'
        add_skills = skills_part.get('status') == 'missing'
        if not add_mcp:
            base['warnings'].append('已有指向当前项目的 moodle_study MCP 条目；未重复添加。')
        if not add_skills:
            base['warnings'].append('已有包含当前项目 skills 目录的 skill-filesystem 配置；未重复添加。')
        if not add_mcp and not add_skills and not adopted and not had_block:
            # Only a profile without a stripped generated block can be judged
            # from the original document alone: it parsed completely and its
            # persisted semantics are uniquely configured.
            base.update(status='unchanged', configuration_ok=True,
                        reason='现有配置已满足要求（MCP 与技能目录均已配置），未修改文件。')
            return base
        new_text, composed_base, generated = _compose(base_text, root, add_mcp=add_mcp, add_skills=add_skills)
        final_data = _load_document(new_text, '合并后的 profile patch')
        if final_data != composed_base + generated:
            raise PatchError('合并后的 profile 结构与预期不一致，未做任何修改。')
        final_mcp, final_skills = _validate_final_document(final_data, root, '合并后的 profile patch')
        base['mcp'] = final_mcp
        base['skills'] = final_skills
    except PatchError as error:
        base.update(status='error', reason=str(error),
                    next_steps=['按错误信息修复 DSH profile 或按 docs/DSH.md 手动合并后重试；不要删除现有配置。'])
        return base

    changed = not patch.exists() or new_text.rstrip('\n') != original.rstrip('\n')
    base['mcp_added'] = add_mcp
    base['skills_added'] = add_skills
    base['updated_existing'] = bool(adopted)
    base['restart_required'] = True
    base['configuration_ok'] = True
    if had_block and changed:
        base['warnings'].append('检测到旧的生成配置段；已重建或移除，避免重复/陈旧的 MCP 与技能配置，其他条目保留。')
    if not changed:
        base.update(status='unchanged',
                    reason='现有配置与应写入内容逐字一致，且完整文档校验通过，未修改文件。')
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
        # Validate the exact bytes that will replace the original file: the full
        # persisted document must have exactly one matching MCP entry and a
        # skills directory that exists, and configuration_ok must hold.
        written = temporary.read_text(encoding='utf-8-sig')
        if written.rstrip('\n') != new_text.rstrip('\n'):
            raise PatchError('临时目标文件内容与已校验内容不一致，保留原文件。')
        final_data = _load_document(written, '临时目标文件')
        if final_data != (composed_base + generated):
            raise PatchError('临时目标文件的复核结构与预期不一致，保留原文件。')
        persisted_mcp, persisted_skills = _validate_final_document(final_data, root, '临时目标文件')
        os.replace(temporary, patch)
        base['mcp'] = persisted_mcp
        base['skills'] = persisted_skills
        base['validated'] = True
    except (OSError, PatchError) as error:
        try:
            temporary = patch.with_name(patch.name + '.tmp')
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        base.update(status='error', configuration_ok=False,
                    reason='写入或校验 DSH profile patch 失败：%s。原文件未替换。' % str(error)[:160],
                    next_steps=['检查 profile 目录权限或按 docs/DSH.md 手动合并后重试；不要删除现有配置。'])
        return base
    base.update(status='installed')
    return base


def _print_parts(result):
    print('  MCP：' + str(result.get('mcp', {}).get('status', 'unknown')))
    print('  技能目录：' + str(result.get('skills', {}).get('status', 'unknown')))
    print('  configuration_ok：' + ('true' if result.get('configuration_ok') else 'false'))


def _print_human(result):
    status = result.get('status')
    if status in SUCCESS_STATUSES:
        print('DSH 配置：' + status)
        print('  profile patch：' + result['patch_path'])
        if result.get('backup'):
            print('  备份：' + result['backup'])
        _print_parts(result)
    elif status == 'conflict':
        print('DSH 配置冲突：' + result.get('reason', ''))
        print('  profile patch：' + result['patch_path'])
        _print_parts(result)
    else:
        print('DSH 配置未完成（' + str(status) + '）：' + result.get('reason', result.get('status', 'unknown')))
        _print_parts(result)
    for warning in result.get('warnings', []):
        print('  提醒：' + warning)
    for step in result.get('next_steps', []):
        print('  下一步：' + step)
    if result.get('content'):
        print('\n需要手动粘贴的配置段：\n' + result['content'])


def _exit_code(result):
    """Exit 0 only for a successful and fully configured merge."""
    status = result.get('status')
    if status in SUCCESS_STATUSES:
        return 0 if result.get('configuration_ok') else 1
    if status in FAILURE_STATUSES:
        return 1
    return 0  # status/print are read-only informational commands


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
    return _exit_code(result)


if __name__ == '__main__':
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    sys.exit(main())
