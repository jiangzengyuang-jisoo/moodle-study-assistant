"""Read-only diagnostics and an explicit, resumable public-project setup menu.

No independent learning-state writer, background work or automatic networking.
Diagnostic output is an allowlist of static messages; raw subprocess errors and
user-provided JSON values must never be included in a shareable report.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
_ONBOARDING_DIR = str(Path(__file__).resolve().parent)
if _ONBOARDING_DIR not in sys.path:
    sys.path.insert(0, _ONBOARDING_DIR)
try:  # Tolerate a partial copy: diagnosis must never crash on a missing helper.
    import dsh_integration  # noqa: E402  (same directory; explicit path for loaders)
except ImportError:  # pragma: no cover - only for incomplete file copies
    dsh_integration = None

INSTALL_URLS = 'Node.js：https://nodejs.org/；Python：https://www.python.org/downloads/windows/'
TIPS = '''学习时请记住：
1. 同题追问和同一知识点练习留在原聊天；换课程或独立任务可在同一项目里新建聊天。
2. 上下文压缩不等于记录丢失，不必因压缩马上重开；接续不准时先重读记录与原题。
3. 换聊天前保存实际进度、原答、卡点和下一步，确认保存成功；关键作答不要等到聊天快满才保存。
4. 新聊天首次进入学习时先读取记录并检查一次 Moodle；无关问答跳过，维修只做必要诊断。
5. 同聊天追问复用上次检查；要看老师刚发的内容，明确说“刷新 Moodle”。没有后台监控。
6. 选课时选择完整初始化，会自动保存所选课程可获取的正文和文件并建立索引，以后同步增量补齐；也可选择仅同步信息。下载完成不等于已经核读资料。
7. 看过答案、提示后答对不算独立掌握；告诉 AI 哪些是你独立完成的，保留原答。
8. 升级前备份 Study 和配置，保留个人目录；不要把整个使用文件夹、密码或 Token 发给别人。
完整说明：docs/使用须知.md。学习仍在可操作本地项目的 AI 客户端中进行。'''
HANDOFF = '''下一步：在 AI 客户端打开当前项目文件夹（含 AGENTS.md 的这一层）。
已有聊天就回到原聊天；换独立任务才在同一项目新建聊天，不要重新建档。
可复制给 AI：
请读取 AGENTS.md、docs/WORKFLOW.md 和我的最新学习记录，按 docs/SESSION.md 使用当前原始聊天 ID 接续。
如无法取得稳定聊天 ID，请先与我约定会话名称，不要随机生成。按规则完成本聊天首次采集与核读。
我想学习的课程和目标是：〔请填写〕。已有进度请接着学；缺少个人情况时询问，不推测掌握。
先选一份资料、一个知识点，核对原页后讲解并让我独立尝试，保存实际作答和下一步。
本向导不会替你创建聊天，也不会把“环境就绪”当作“学校内容已核读”。'''


def read_json(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return json.load(stream)


def probe(args, root, timeout=20):
    """Capture locally; callers publish only allowlisted conclusions."""
    try:
        result = subprocess.run([str(arg) for arg in args], cwd=root, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=timeout,
                                env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'},
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, '', 'diagnostic_timeout'
    except OSError:
        return 127, '', 'diagnostic_launch_failed'


def item(key, status, message, next_step=''):
    return {'id': key, 'status': status, 'message': message, 'next_step': next_step}


def online_result(code, stdout, stderr):
    if code == 0:
        return item('online', 'ok', '本次 Moodle API 连接验证成功。', '只验证连接，未采集或核读课程内容。')
    error = stdout + '\n' + stderr
    study_tools = str(Path(__file__).resolve().parents[1] / 'study')
    if study_tools not in sys.path:
        sys.path.insert(0, study_tools)
    from connection_errors import safe_connection_error
    detail = safe_connection_error(error)
    if detail:
        return item('online', 'error', detail[1], detail[2])
    if 'invalidtoken' in error or 'expiredtoken' in error:
        return item('online', 'error', '学校授权无效或已过期。', '选择“重新连接学校”；已有学习记录保留。')
    if any(key in error for key in ('accessexception', 'accesscontrol', 'webservice_access_exception', 'nopermissions')):
        return item('online', 'error', '学校未允许此次 API 访问。', '核对学校地址和移动服务权限；必要时联系学校 IT，不绕过限制。')
    if ('STUDY_TOKEN_DECRYPT_FAILED' in error or 'Token 只能由保存它的 Windows 账户使用' in error
            or any(key in error.lower() for key in ('decrypt', 'dpapi', 'unprotect'))):
        return item('online', 'error', '当前 Windows 账户无法解密已保存的授权。', '先核对原 Windows 用户及执行环境；确认需要重新授权后再选择“重新连接学校”，不复制凭据。')
    if code == 124 or 'network request failed' in error:
        return item('online', 'error', '网络、超时或学校响应异常，本次未验证成功。', '检查网络和学校网站，稍后主动重试；本工具不自动重试。')
    return item('online', 'error', '本次连接检查未完成，原因尚不能确定。', '先解决本地诊断问题；仍失败再重新连接或向维护者提供此摘要。')


class App:
    def __init__(self, root=ROOT, run_probe=probe, locate=shutil.which, environ=None):
        self.root = Path(root).resolve()
        self.probe = run_probe
        self.locate = locate
        # Injectable environment keeps diagnostics deterministic in tests; real
        # runs use the process environment so DSH_* facts are visible.
        self.environ = dict(os.environ if environ is None else environ)
        self._last_online = None
        self._online_scope = None
        self._pip_repair_failed = False

    @property
    def python(self):
        return self.root / '.venv/Scripts/python.exe'

    def inspect(self, online=False):
        checks = []
        add = lambda *args: checks.append(item(*args))
        node = self.locate('node')
        if node:
            code, out, _ = self.probe([node, '-p', 'process.versions.node.split(".")[0]'], self.root)
            node_ok = code == 0 and out.strip().isdigit() and int(out.strip()) >= 22
        else:
            node_ok = False
        add('node', 'ok' if node_ok else 'error', 'Node.js 环境可用。' if node_ok else '缺少可用的 Node.js 22 或更新版本。',
            '' if node_ok else INSTALL_URLS + ' 安装后重新打开入口。')
        npm_ok = bool(self.locate('npm.cmd') or self.locate('npm'))
        add('npm', 'ok' if npm_ok else 'error', 'Node 安装工具可用。' if npm_ok else '缺少 npm 安装工具。',
            '' if npm_ok else '重新安装官方 Node.js，并保留 npm 组件。')
        py_ok = False
        py_broken = False
        if self.python.is_file():
            code, _, _ = self.probe([self.python, '-B', '-c',
                'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'], self.root)
            py_broken = code != 0
            code, _, _ = self.probe([self.python, '-B', '-c',
                'import sys, fsrs, sympy, pypdf, pypdfium2, PIL, playwright.sync_api; '
                'from zoneinfo import ZoneInfo; ZoneInfo("Asia/Hong_Kong"); '
                'sys.exit(0 if sys.version_info >= (3,11) else 1)'], self.root)
            py_ok = code == 0 and not py_broken
        add('python_packages', 'error' if py_broken else 'ok' if py_ok else 'missing',
            '项目 Python 解释器损坏或版本过低。' if py_broken else '项目 Python 环境及核心依赖可用。' if py_ok else '项目 Python 环境或依赖尚未就绪。',
            '让 AI 修复项目环境，保留 Study、授权与配置；不要反复初始化。' if py_broken else '' if py_ok else '选择“安装或更新依赖”；不删除已有学习目录。')
        pip_ok = False
        if self.python.is_file() and not py_broken:
            pip_ok = self.probe([self.python, '-B', '-m', 'pip', '--version'], self.root)[0] == 0
        if pip_ok:
            self._pip_repair_failed = False
        add('pip', 'error' if self._pip_repair_failed else 'ok' if pip_ok else 'missing',
            'pip 内置修复未成功，需要单独修复项目环境。' if self._pip_repair_failed else '项目 pip 安装工具可用。' if pip_ok else '项目 pip 安装工具尚未就绪。',
            '让 AI 修复 .venv，保留学习档案；不要反复执行同一步。' if self._pip_repair_failed else '' if pip_ok else '选择安装时会先尝试使用 Python 内置 ensurepip 恢复，不联网、不删除学习记录。')
        node_packages = False
        if node_ok:
            code, _, _ = self.probe([node, '--input-type=module', '-e',
                'await import("./tools/moodle/core.mjs"); await import("@modelcontextprotocol/sdk/server/mcp.js");'], self.root)
            node_packages = code == 0
        add('node_packages', 'ok' if node_packages else 'missing', '项目 Node 依赖可用。' if node_packages else '项目 Node 依赖尚未就绪。',
            '' if node_packages else '环境安装完成后选择“安装或更新依赖”。')

        config = state = None
        config_path = self.root / 'config.local.json'
        state_path = self.root / 'Study/_System/state.json'
        for key, path in (('config', config_path), ('state', state_path)):
            if not path.exists():
                if key == 'config' and state_path.exists():
                    add(key, 'error', '已有学习档案，但学校配置缺失。', '先恢复或核对原学校配置，不能按默认学校重新初始化。')
                    continue
                if key == 'state' and (state_path.with_name('state.previous.json').exists() or (state_path.parent / 'Runtime').exists()):
                    add(key, 'error', '学习档案缺失，但仍有历史备份或运行记录。', '停止初始化，先让 AI 核对并恢复原档案，不用空白档案替代。')
                    continue
                add(key, 'missing', '本地配置尚未建立。' if key == 'config' else '学习档案尚未建立。', '选择“安装或更新依赖”，只建立缺少的文件。')
                continue
            try:
                value = read_json(path)
                if not isinstance(value, dict):
                    raise ValueError()
                if key == 'config':
                    url = urlsplit(value['moodle_url'])
                    if (url.scheme != 'https' or not url.hostname or url.username or url.password
                            or url.query or url.fragment or not isinstance(value.get('timezone'), str)):
                        raise ValueError()
                    config = value
                else:
                    if (not isinstance(value.get('courses'), list) or not isinstance(value.get('timezone'), str)
                            or any(not isinstance(c, dict) or not isinstance(c.get('id'), str) or not c['id']
                                   or not isinstance(c.get('moodle_course_ids', []), list)
                                   or any(type(i) is not int or i <= 0 for i in c.get('moodle_course_ids', []))
                                   for c in value['courses'])):
                        raise ValueError()
                    arrays = ('materials', 'tasks', 'mistakes', 'review_queue', 'review_attempts', 'learning_feedback', 'exam_profiles')
                    if any(k in value and not isinstance(value[k], list) for k in arrays):
                        raise ValueError()
                    if 'settings' in value and not isinstance(value['settings'], dict):
                        raise ValueError()
                    state = value
                add(key, 'ok', '本地学校配置可读取。' if key == 'config' else '已有学习档案可读取。')
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                add(key, 'error', '本地配置结构异常。' if key == 'config' else '学习档案无法安全读取。',
                    '停止初始化和写入，先备份并让 AI 核对文件；不要删除后重建。')
        if config and state and config['timezone'] != state['timezone']:
            add('timezone', 'error', '配置与学习档案的时区不一致。', '让 AI 核对原日期含义后统一设置，不自动平移旧记录。')
        elif py_ok and (config or state):
            zones = sorted({value['timezone'] for value in (config, state) if value is not None})
            # Validate actual settings with the same interpreter/database used by
            # the study tools. Values are arguments, never interpolated as code.
            code, _, _ = self.probe([self.python, '-B', '-c',
                'import sys; from zoneinfo import ZoneInfo, ZoneInfoNotFoundError\n'
                'try:\n for key in sys.argv[1:]: ZoneInfo(key)\n'
                'except (ValueError, ZoneInfoNotFoundError): sys.exit(2)', *zones], self.root)
            if code == 0:
                add('timezone', 'ok', '实际配置的学习时区可用。')
            elif code == 2:
                add('timezone', 'error', '配置的学习时区名称无效或当前环境无法提供该时区。', '核对 IANA 时区名称和 tzdata；不要自动平移已有日期。')
            else:
                add('timezone', 'error', '本次无法完成学习时区检查。', '先核对项目 Python 环境，再重新运行本地诊断。')

        credential = self.root / '.private/moodle-token.dpapi'
        try:
            credential_stat = credential.stat() if credential.is_file() else None
            credential_ok = credential_stat is not None and credential_stat.st_size > 0
            credential_generation = ((credential_stat.st_dev, credential_stat.st_ino, credential_stat.st_size,
                                      credential_stat.st_mtime_ns, credential_stat.st_ctime_ns)
                                     if credential_stat else None)
        except OSError:
            credential_ok = False
            credential_generation = None
        # Session-only connection evidence. Do not decrypt/read credentials just
        # to retain a diagnosis; successful auth replaces this file atomically.
        scope = (config.get('moodle_url') if config is not None else None, credential_generation)
        if scope != self._online_scope:
            self._last_online = None
            self._online_scope = scope
        add('credential', 'unknown' if credential_ok else 'missing',
            '已发现本机加密授权；有效性尚未联网验证。' if credential_ok else '尚未保存学校授权。',
            '可主动选择“联网检查”，仅验证连接。' if credential_ok else '选择“重新连接学校”，本人完成登录验证。')
        selected = bool(state and any(c.get('moodle_course_ids') for c in state['courses']))
        add('courses', 'ok' if selected else 'missing', '已有选定课程，原选择保留。' if selected else '尚未选定需要跟踪的课程。',
            '' if selected else '连接学校后选择课程；取消选课不会清空旧记录。')
        self._client_check(add)
        self._school_check(state, add)
        self._content_check(config, state, add)
        result = {'schema_version': 1, 'version': self._version(),
                  'checked_at': datetime.now(timezone.utc).isoformat(),
                  'mode': 'online' if online else 'local', 'checks': checks}
        if online:
            prerequisites = (node_ok and node_packages and py_ok and config and state and credential_ok
                             and not any(row['id'] == 'timezone' and row['status'] == 'error' for row in checks))
            if prerequisites:
                row = online_result(*self.probe([node, 'tools/moodle/run.mjs', 'check'], self.root, timeout=45))
                row.update(checked_at=result['checked_at'], reused=False)
                self._last_online = dict(row)
                checks.append(row)
            else:
                add('online', 'skipped', '尚不具备连接检查条件，没有联网。', '先处理环境、配置、学习档案和学校授权问题。')
        elif self._last_online:
            row = dict(self._last_online)
            row.update(message='本次向导此前检查：' + row['message'], reused=True)
            checks.append(row)
        result['next_action'] = next_action(result)
        return result

    def _content_check(self, config, state, add):
        """Report saved initialization evidence, without a network probe or upgrade."""
        if not config or not state:
            return
        selected = {i for course in state.get('courses', []) for i in course.get('moodle_course_ids', [])}
        if not selected:
            return
        policy = state.get('settings', {}).get('course_content_sync', {})
        grants = policy.get('grants', []) if isinstance(policy, dict) and policy.get('version') == 1 else []
        relevant = [g for g in grants if isinstance(g, dict)
                    and g.get('site') == config['moodle_url'].rstrip('/')
                    and g.get('moodle_course_id') in selected] if isinstance(grants, list) else []
        if not any(g.get('mode') == 'full' for g in relevant):
            add('course_content', 'unknown', '尚未记录所选课程的完整下载授权；已有选课不会自动升级为下载授权。',
                '如需自动建立资料库，选择“选择课程”，再选择“完整初始化”；也可继续仅同步信息。')
            return
        path = self.root / 'Study/_System/CourseContent/latest.json'
        try:
            report = read_json(path)
            account = report.get('account_scope', {})
            ids = {g['moodle_course_id'] for g in relevant if g.get('mode') == 'full'
                   and g.get('user_id') == account.get('user_id')}
            pointer = read_json(self.root / 'Study/_System/Runtime/latest-request.json')
            binding = report.get('collection_binding', {})
            matching = (account.get('site') == config['moodle_url'].rstrip('/') and ids
                        and set(report.get('course_ids', [])) == ids
                        and binding.get('run_id') == pointer.get('run_id'))
            if not matching:
                raise ValueError('stale scope')
            if report.get('status') == 'complete':
                add('course_content', 'ok', '最近一次记录显示：已保存授权范围内当时可获取的资料并处理索引。',
                    '这不是本次联网检查，也不表示 AI 已核读全部资料；新发布内容会在下一次正常同步时增量处理。')
            elif report.get('status') == 'partial':
                add('course_content', 'warning', '最近一次课程资料初始化未全部完成，已有文件和学习记录保留。',
                    '让 AI 查看 CourseContent/latest.json 的缺口；明确刷新 Moodle 可按已有文件增量重试。')
            else:
                raise ValueError('not completed')
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            add('course_content', 'unknown', '已记录完整下载授权，但当前范围的资料初始化结果尚未确认。',
                '让 AI 明确刷新 Moodle 完成同步；当前账户和授权范围会在同步时再次校验。')

    def _version(self):
        try:
            value = read_json(self.root / 'package.json').get('version', '')
            return value if isinstance(value, str) and re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?', value) else '待确认'
        except (OSError, ValueError, AttributeError):
            return '待确认'

    def _codex_client_status(self):
        """Return ok/missing/warning for the Codex project-level MCP config."""
        path = self.root / '.codex/config.toml'
        try:
            with path.open('rb') as stream:
                server = tomllib.load(stream)['mcp_servers']['moodle_study']
            args = server['args']
            command = server['command']
            if (not isinstance(args, list) or len(args) != 2 or args[1] != 'mcp'
                    or Path(args[0]).resolve() != self.root / 'tools/moodle/run.mjs'
                    or not (Path(command).is_file() or self.locate(command))):
                raise ValueError()
            return 'ok'
        except FileNotFoundError:
            return 'missing'
        except (OSError, ValueError, KeyError, TypeError):
            return 'warning'

    def _client_check(self, add):
        """Report Codex and DeepSeek Harness wiring together; both are read only."""
        codex = self._codex_client_status()
        dsh = self._dsh_status(add)
        if codex == 'ok':
            add('client', 'ok', 'Codex 本地 AI 工具配置路径可用；客户端是否加载仍需本人确认。')
        elif dsh == 'ok':
            add('client', 'ok', 'DeepSeek Harness 工具配置路径可用；客户端是否加载仍需本人确认。')
        elif 'warning' in (codex, dsh):
            add('client', 'warning', '已有 AI 工具配置需要核对。', '移动项目或更新环境后让 AI 核对路径；向导不覆盖自定义配置。')
        else:
            add('client', 'missing', '尚未发现本地 AI 工具配置。', '运行安装后，在 AI 客户端打开项目并确认工具连接。')

    def _dsh_status(self, add):
        if dsh_integration is None:
            add('dsh', 'warning', 'DeepSeek Harness 诊断组件缺失；未做 DSH 配置检查。', '重新解压完整项目后再运行诊断。')
            return 'warning'
        status = dsh_integration.inspect(self.root, self.environ)
        chat = status['chat']
        if not status['detected']:
            add('dsh', 'ok', '未检测到 DeepSeek Harness；Codex 本地项目入口不受影响。')
            return 'not-detected'
        if status.get('patch_parse') == 'unavailable':
            add('dsh', 'warning', 'DeepSeek Harness 配置检查需要未安装的 YAML 解析依赖。',
                '完成安装（setup）后再运行本地诊断；本次没有修改任何配置。')
            return 'warning'
        if status.get('patch_parse') == 'invalid':
            add('dsh', 'warning', 'DeepSeek Harness profile 配置无法解析，未做任何修改。',
                '按 docs/DSH.md 核对 profile patch；不要删除现有配置来重试安装。')
            return 'warning'
        configured = bool(status.get('configuration_ok'))
        if configured:
            add('dsh', 'ok', 'DeepSeek Harness 的 MCP 服务与技能目录配置已指向当前项目；'
                              '配置正确不等于已加载，需重启后在新聊天中实际确认。',
                '' if chat['auto_resolvable'] else '当前会话标识不能直接当原始聊天 ID；内部任务显式传入主聊天 ID 并加 --subtask。')
            return 'ok'
        if status['mcp']['status'] == 'conflict':
            add('dsh', 'warning', 'DeepSeek Harness 已有同名 MCP 配置，但未指向当前项目。',
                '按 docs/DSH.md 手动核对；向导不会覆盖已有自定义配置。')
            return 'warning'
        add('dsh', 'missing', '尚未完成 DeepSeek Harness 的 MCP 与技能配置。',
            '选择“安装或更新依赖”（会合并配置并备份），或运行 python tools/onboarding/dsh_integration.py install。')
        return 'missing'

    def _school_check(self, state, add):
        runtime = self.root / 'Study/_System/Runtime'
        pointer_path = runtime / 'latest-request.json'
        if not pointer_path.exists():
            add('school', 'unknown', '尚无本轮学校检查记录。', '在 AI 聊天中按统一入口采集并核读；本地诊断不会代为刷新。')
            return
        try:
            pointer = read_json(pointer_path)
            run_id = pointer['run_id']
            if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
                raise ValueError()
            folder = runtime / 'runs' / run_id
            if folder.resolve() != folder:
                raise ValueError()
            manifest = read_json(folder / 'manifest.json')
            if manifest['run_id'] != run_id or manifest['sequence'] != pointer['sequence']:
                raise ValueError()
            phase = manifest['phase']
            if phase in ('collected', 'reviewed'):
                # Reuse the source gate without locks, recovery or writes.
                sys.path.insert(0, str(ROOT / 'tools/study')) if str(ROOT / 'tools/study') not in sys.path else None
                from snapshot_guard import validate_current_snapshot
                validate_current_snapshot(self.root / 'Study/_System/state.json', read_json(folder / 'snapshot.json'), state, allow_legacy=False)
                receipt = state.get('refresh_receipts', {}).get(run_id, {})
                saved_review = (receipt.get('phase') == 'reviewed'
                                and receipt.get('snapshot_sha256') == manifest.get('snapshot_sha256'))
                if phase == 'reviewed' and not saved_review:
                    raise ValueError()
                if saved_review:
                    marker = folder / 'review-status.json'
                    if not marker.exists() or read_json(marker).get('phase') != 'complete':
                        add('school', 'warning', '学校核读已保存，页面生成仍待恢复。', '回到原聊天接续恢复页面，不重复登记作答或重新采集。')
                    else:
                        add('school', 'ok', '最近保存的学校快照已有核读回执；这不是本次联网检查。', '老师此后可能更新，需要最新内容时在聊天中明确刷新。')
                else:
                    add('school', 'warning', '已采集学校信息，仍待 AI 核读。', '回到发起采集的聊天继续核读，不能把采集完成当作内容已确认。')
            elif phase in ('collecting', 'interrupted'):
                add('school', 'warning', '记录显示采集尚未结束，也可能曾被中断。', '回到原聊天接续并检查状态，不并行重开刷新。')
            elif phase == 'failed':
                add('school', 'warning', '最近一次学校采集失败或不完整。', '旧证据保留；在原聊天确认原因后明确要求刷新，不自动重试。')
            else:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError):
            add('school', 'warning', '学校检查记录缺失、范围变化或无法验证。', '保留已有记录，让 AI 核对；需要更新时明确刷新，不手动补写“已核读”。')

    def act(self, action):
        """Actions are selected by the user, use existing writers, and never retry."""
        current = self.inspect()
        checks = {row['id']: row for row in current['checks']}
        if any(checks.get(k, {}).get('status') == 'error' for k in ('config', 'state', 'timezone', 'python_packages', 'pip')):
            return False, '环境、配置或学习档案异常，已停止操作；先核对并修复，保留学习档案。'
        if any(checks[k]['status'] != 'ok' for k in ('node', 'npm')):
            return False, '请先安装 Node.js 和 npm，再重新打开入口。'
        if action == 'setup':
            args = [powershell(), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(self.root / 'setup.ps1'),
                    '-PythonExecutable', sys.executable]
        else:
            if any(checks[k]['status'] != 'ok' for k in ('python_packages', 'node_packages', 'config', 'state')):
                return False, '请先完成安装并处理本地诊断中的问题。'
            if action in ('edge', 'chrome', 'manual'):
                args = [str(self.python), 'tools/moodle/auth.py']
                args += ['--manual'] if action == 'manual' else ['--browser', 'msedge' if action == 'edge' else 'chrome']
            elif action == 'courses':
                if checks['credential']['status'] == 'missing':
                    return False, '请先连接学校，再选择课程。'
                args = [self.locate('node'), 'tools/moodle/run.mjs', 'courses']
            else:
                return False, '未识别的操作，没有执行。'
        try:
            code = subprocess.call(args, cwd=self.root, env={**os.environ, 'PYTHONUTF8': '1'})
        except OSError:
            code = 1
        if action == 'setup' and code == 3:
            self._pip_repair_failed = True
            return False, 'pip 内置恢复失败。请先让 AI 修复 .venv，保留学习记录；本次向导不再重复建议安装。'
        if code:
            return False, '此步骤未完成。请查看上方提示，选 2 做本地诊断；学校连接问题可选 3 联网检查。修复后可继续，不会自动重试。'
        return True, '此步骤已返回，正在重新检查实际完成情况；取消操作不视为完成。'


def powershell():
    return str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')


def next_action(report):
    statuses = {row['id']: row['status'] for row in report['checks']}
    if any(statuses.get(k) == 'error' for k in ('config', 'state', 'timezone', 'python_packages', 'pip')):
        return 'repair'
    if any(statuses.get(k) != 'ok' for k in ('node', 'npm')):
        return 'install_runtime'
    if any(statuses.get(k) != 'ok' for k in ('python_packages', 'pip', 'node_packages', 'config', 'state')) or statuses.get('client') == 'missing':
        return 'setup'
    if statuses.get('credential') == 'missing':
        return 'connect'
    if statuses.get('online') == 'error':
        return 'connection_help'
    if statuses.get('courses') != 'ok':
        return 'courses'
    return 'handoff'


def show(report, output=print):
    labels = {'ok': '通过', 'error': '需处理', 'missing': '未完成', 'warning': '提醒', 'unknown': '待确认', 'skipped': '未执行'}
    output('学习助手中文诊断 · 版本 ' + report['version'])
    output('本地检查不联网、不解密授权、不写入学习记录。' if report['mode'] == 'local' else '已请求连接检查；不采集课程，不修改学习记录。')
    for row in report['checks']:
        output('[' + labels[row['status']] + '] ' + row['message'])
        if row['next_step']:
            output('  下一步：' + row['next_step'])
    output('此摘要不含账号、课程正文、原答、凭据或本机路径；不会自动发送给维护者。')


def guide(app, ask=input, output=print):
    output('欢迎使用学习助手。只在你选择后安装、登录或联网；退出后可从现有文件继续。')
    output('首次请先看“学习注意事项”。原有入口仍可使用，已有档案不会因重新打开向导而重建。')
    report = app.inspect()
    while True:
        action = report['next_action']
        labels = {'repair': '先修复环境、配置或档案', 'install_runtime': '先安装 Node.js / npm', 'setup': '安装或更新依赖',
                  'connect': '连接学校', 'courses': '选择课程', 'handoff': '进入 AI 学习（不重复安装或刷新）',
                  'connection_help': '处理学校连接问题；已有本地资料仍可复习'}
        output('\n下一步：' + labels[action])
        output('1 继续下一步　2 本地诊断　3 联网检查　4 学习注意事项\n5 重新连接学校　6 调整课程　7 安装或更新依赖　0 退出')
        choice = ask('请选择：').strip()
        if choice == '0':
            return 0
        if choice == '2':
            report = app.inspect()
            show(report, output); continue
        if choice == '3':
            output('正在验证学校连接；不采集课程。')
            report = app.inspect(online=True)
            show(report, output); continue
        if choice == '4':
            output(TIPS); continue
        if choice not in ('1', '5', '6', '7'):
            output('请输入菜单中的数字。'); continue
        action = {'5': 'connect', '6': 'courses', '7': 'setup'}.get(choice, action)
        if action in ('repair', 'install_runtime', 'connection_help'):
            show(report, output); continue
        if action == 'handoff':
            for row in report['checks']:
                if row['id'] == 'school' or row['id'] == 'client' and row['status'] != 'ok':
                    output(row['message'] + ' ' + row['next_step'])
            output(HANDOFF); output(TIPS); continue
        if action == 'setup':
            output('将安装/更新项目依赖并运行现有初始化流程，已有学习记录保留。已有资料的缓存同步可能耗时。'
                   '如检测到 DeepSeek Harness，会合并 MCP 与技能配置（保留原有配置并写备份）。')
            if ask('继续请输入 Y，其他输入返回：').strip().lower() != 'y':
                continue
        if action == 'connect':
            output('请核对 config.local.json 中的学校地址；新安装默认 HKU，其他学校先按新手指南修改。')
            output('1 使用 Edge 登录　2 使用 Chrome 登录　3 隐藏输入学校官方 Token　0 返回')
            action = {'1': 'edge', '2': 'chrome', '3': 'manual'}.get(ask('选择连接方式：').strip())
            if not action:
                continue
        _, message = app.act(action)
        output(message)
        report = app.inspect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('guide', 'diagnose'), default='guide')
    parser.add_argument('--online', action='store_true', help='明确请求一次学校连接验证，不刷新课程')
    parser.add_argument('--json', action='store_true', help='仅输出允许分享的结构化诊断摘要')
    args = parser.parse_args()
    if args.mode == 'guide' and (args.online or args.json):
        parser.error('--online/--json 仅用于诊断模式')
    app = App()
    if args.mode == 'guide':
        return guide(app)
    report = app.inspect(online=args.online)
    print(json.dumps(report, ensure_ascii=False, indent=2)) if args.json else show(report)
    return 1 if any(row['status'] in ('error', 'missing', 'skipped') for row in report['checks']) else 0


if __name__ == '__main__':
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print('\n已退出。再次打开入口可检查并接续未完成的步骤。')
        sys.exit(130)
    except Exception:
        print('入口遇到未识别的问题；没有输出原始异常，以免暴露本机或学习信息。请向维护者说明发生在哪一步。', file=sys.stderr)
        sys.exit(1)
