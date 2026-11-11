// Python runtime bootstrap for the Moodle Study Assistant plugin.
//
// The plugin never assumes a developer environment: it uses the Python
// distribution shipped with DeepSeek Harness when available, falls back to a
// system Python, and installs the pinned engine dependencies into a private
// virtual environment under the plugin support directory.
//
// Readiness is never derived from the files that merely exist on disk. Every
// check is graded and local-only, with one merged child process:
//   1. the venv directory / interpreter file exists,
//   2. the interpreter actually runs and prints the expected probe document,
//   3. pip is usable (`python -m pip --version` semantics, checked in-process),
//   4. every import listed by requirements-core.txt can be imported.
// The runtime.json marker only records provenance: it is trusted only when the
// probe passes AND the recorded dependency fingerprint still matches the
// current requirements file. No probe installs packages or touches the network.
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, join } from 'node:path'

import {
  DEFAULT_TIMEZONE,
  dshHome,
  programDir,
  readRuntime,
  supportDir,
  venvPaths,
  writeRuntime,
} from './settings.mjs'

// Bumped whenever the readiness contract or the probe itself changes, so a
// marker written by an older plugin version is never silently trusted.
export const CORE_REQUIREMENTS_VERSION = 2
const CORE_PROBE_TIMEOUT_MS = 20000
const CORE_PROBE_SENTINEL = 'MOODLE_STUDY_PROBE '
const CORE_RECOVERY_PYTHON_HINT =
  '关闭并重开 Harness 后重试；仍失败请修复/重装 DeepSeek Harness 自带的 Python，或设置 MOODLE_STUDY_BASE_PYTHON 指向可用的 Python 3.11+。不要删除学习档案（修复只动插件自己的 supportDir/venv 与 runtime.json，不碰学习资料和登录凭据）。'

// Python import names that cannot be derived by replacing "-" with "_".
const IMPORT_NAME_HINTS = {
  pillow: 'PIL',
  pyyaml: 'yaml',
  'python-dateutil': 'dateutil',
  'beautifulsoup4': 'bs4',
}

// The packaged plugin ships the split requirement files at the program root.
// A source checkout keeps them next to the plugin resources instead, so a
// developer checkout can be diagnosed with the same code path.
const REQUIREMENT_FILE_CANDIDATES = {
  'requirements-core.txt': [
    'requirements-core.txt',
    'plugin/resources/pip/core-requirements.txt',
    'resources/pip/core-requirements.txt',
  ],
  'requirements-login.txt': [
    'requirements-login.txt',
    'plugin/resources/pip/login-requirements.txt',
    'resources/pip/login-requirements.txt',
  ],
}

const RUNTIME_STATUS_TEXT = {
  not_installed: '尚未安装运行环境',
  environment_incomplete: '运行环境目录不完整（可能创建中断）',
  interpreter_broken: '私有解释器无法正常运行',
  pip_missing: '解释器可用但缺少 pip',
  requirements_missing: '插件包缺少依赖清单 requirements-core.txt',
  dependencies_missing: '运行依赖缺失或无法导入',
  marker_missing: '缺少安装记录',
  marker_stale: '安装记录与当前依赖要求不一致',
  ready: '运行环境健康',
}

function candidatesFromDsh() {
  const root = join(dshHome(), 'dsh-runtimes')
  let entries = []
  try {
    entries = readdirSync(root)
  } catch {
    return []
  }
  const found = []
  for (const entry of entries) {
    const python = join(root, entry, 'dependencies', 'python', 'python.exe')
    try {
      if (statSync(python).isFile()) found.push({ python, mtime: statSync(python).mtimeMs })
    } catch {
      // Not a complete runtime directory.
    }
  }
  found.sort((a, b) => b.mtime - a.mtime)
  return found.map((item) => item.python)
}

function candidatesFromPath() {
  const names = process.platform === 'win32' ? ['python.exe', 'python3.exe'] : ['python3', 'python']
  const found = []
  for (const dir of String(process.env.PATH ?? '').split(delimiter)) {
    if (!dir) continue
    for (const name of names) {
      const candidate = join(dir, name)
      try {
        if (statSync(candidate).isFile()) found.push(candidate)
      } catch {
        // Keep scanning.
      }
    }
    if (found.length) break
  }
  return found
}

// Returns an executable plus any fixed leading arguments (for example py.exe).
export function findBasePython() {
  const override = process.env.MOODLE_STUDY_BASE_PYTHON
  if (override) {
    return existsSync(override) ? { command: override, prefix: [], source: 'configured' } : null
  }
  const fromDsh = candidatesFromDsh()
  if (fromDsh.length) return { command: fromDsh[0], prefix: [], source: 'deepseek-harness' }
  const fromPath = candidatesFromPath()
  if (fromPath.length) return { command: fromPath[0], prefix: [], source: 'system' }
  if (process.platform === 'win32') {
    const launcher = join(process.env.SystemRoot || 'C:\\Windows', 'py.exe')
    if (existsSync(launcher)) return { command: launcher, prefix: ['-3'], source: 'system-launcher' }
  }
  return null
}

function isDirectory(path) {
  try {
    return statSync(path).isDirectory()
  } catch {
    return false
  }
}

function isFile(path) {
  try {
    return statSync(path).isFile()
  } catch {
    return false
  }
}

function resolveRequirementFile(name) {
  const candidates = REQUIREMENT_FILE_CANDIDATES[name] ?? [name]
  for (const relative of candidates) {
    const candidate = join(programDir(), ...relative.split('/'))
    if (isFile(candidate)) return candidate
  }
  return join(programDir(), name)
}

function requirements(name) {
  return resolveRequirementFile(name)
}

function runtimePythonPath() {
  const paths = venvPaths()
  if (existsSync(paths.windows)) return paths.windows
  if (existsSync(paths.posix)) return paths.posix
  return null
}

// Import name for one requirement line: "fsrs==6.3.2", "moodle-study-stubdep
// @ file:///..." or a plain VCS/path line (ignored). Non-package option lines
// (-r, -c, --hash, ...) are skipped by the caller.
function requirementImportName(line) {
  const match = /^([A-Za-z0-9][A-Za-z0-9._-]*)/.exec(line)
  if (!match) return null
  const lower = match[1].toLowerCase()
  if (IMPORT_NAME_HINTS[lower]) return IMPORT_NAME_HINTS[lower]
  return lower.replace(/[.-]+/g, '_')
}

// Read requirements-core.txt: dependency fingerprint plus the import names the
// health probe must be able to import. MOODLE_STUDY_CORE_IMPORT_OVERRIDE is a
// diagnostics/testing seam only (normal installs must leave it unset).
export function coreRequirementsInfo() {
  const path = requirements('requirements-core.txt')
  let text = null
  try {
    text = readFileSync(path, 'utf8')
  } catch {
    text = null
  }
  const imports = []
  if (text !== null) {
    for (const rawLine of text.replace(/^\uFEFF/, '').split(/\r?\n/)) {
      const line = rawLine.split(/\s+#/)[0].trim()
      if (!line || line.startsWith('#')) continue
      if (line.startsWith('-')) continue
      const name = requirementImportName(line)
      if (name && !imports.includes(name)) imports.push(name)
    }
  }
  const override = String(process.env.MOODLE_STUDY_CORE_IMPORT_OVERRIDE ?? '').trim()
  if (override) {
    imports.length = 0
    for (const name of override.split(/[\s,]+/)) {
      if (name && !imports.includes(name)) imports.push(name)
    }
  }
  const normalized = text === null ? null : text.replace(/\r\n/g, '\n')
  return {
    path,
    exists: text !== null,
    version: CORE_REQUIREMENTS_VERSION,
    sha256: normalized === null ? null : createHash('sha256').update(normalized, 'utf8').digest('hex'),
    imports,
    overriddenImports: Boolean(override),
  }
}

// Marker accessor: runtime.json is provenance only. A marker is "matching" when
// it was written for the same requirement fingerprint and probe contract.
function markerState(requirementsInfo) {
  const marker = readRuntime()?.core ?? null
  const installed = Boolean(marker?.installed)
  const versionMatches = Boolean(marker) && marker.requirementsVersion === requirementsInfo.version
  const shaMatches =
    Boolean(marker) && requirementsInfo.sha256 !== null && marker.requirementsSha256 === requirementsInfo.sha256
  return {
    installed,
    requirementsVersion: marker?.requirementsVersion ?? null,
    requirementsSha256: marker?.requirementsSha256 ?? null,
    basePython: marker?.basePython ?? null,
    at: marker?.at ?? null,
    versionMatches,
    shaMatches,
    matches: installed && versionMatches && shaMatches,
  }
}

// One merged local probe. It never installs anything and never uses the
// network; a missing pip / missing import is reported inside the JSON document
// instead of a failing exit code, so one spawn covers every graded check.
function coreProbeScript(imports) {
  return [
    'import json, sys',
    `names = ${JSON.stringify(imports)}`,
    'result = {',
    '    "ok": True,',
    '    "executable": sys.executable,',
    '    "version": list(sys.version_info[:3]),',
    '    "pip": {"available": False, "version": None, "error": None},',
    '    "imports": {},',
    '}',
    'try:',
    '    import contextlib, io, runpy',
    '    buffer = io.StringIO()',
    '    previous_argv = sys.argv',
    '    sys.argv = ["pip", "--version"]',
    '    code = 1',
    '    try:',
    '        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):',
    '            try:',
    '                runpy.run_module("pip", run_name="__main__")',
    '                code = 0',
    '            except SystemExit as exc:',
    '                if exc.code is None or exc.code == 0:',
    '                    code = 0',
    '                elif isinstance(exc.code, int):',
    '                    code = exc.code',
    '                else:',
    '                    code = "error:" + str(exc.code)',
    '            except BaseException as exc:',
    '                code = "error:" + type(exc).__name__ + ": " + str(exc)',
    '    finally:',
    '        sys.argv = previous_argv',
    '    text = buffer.getvalue().strip()',
    '    if code == 0:',
    '        result["pip"] = {"available": True, "version": text.splitlines()[-1] if text else "unknown", "error": None}',
    '    else:',
    '        result["pip"] = {"available": False, "version": None, "error": str(code) + ": " + text[-500:]}',
    'except BaseException as exc:',
    '    result["pip"] = {"available": False, "version": None, "error": type(exc).__name__ + ": " + str(exc)}',
    'for name in names:',
    '    try:',
    '        __import__(name)',
    '        result["imports"][name] = {"ok": True, "error": None}',
    '    except BaseException as exc:',
    '        result["imports"][name] = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}',
    'print("' + CORE_PROBE_SENTINEL + '" + json.dumps(result))',
  ].join('\n')
}

function probeInterpreter(python, imports, timeoutMs) {
  const cwd = isDirectory(supportDir()) ? supportDir() : programDir()
  let result
  try {
    result = spawnSync(python, ['-c', coreProbeScript(imports)], {
      cwd,
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8' },
      encoding: 'utf8',
      timeout: timeoutMs,
      windowsHide: true,
      maxBuffer: 8 * 1024 * 1024,
    })
  } catch (error) {
    return { ok: false, timedOut: false, exitCode: null, reason: `无法启动解释器：${error?.message ?? error}`, pipAvailable: false, pipVersion: null, pipError: null, dependenciesOk: false, missingImports: [] }
  }
  if (result.error) {
    const code = result.error.code
    const reason =
      code === 'ETIMEDOUT'
        ? `解释器探测超时（${Math.round(timeoutMs / 1000)} 秒内无结果）`
        : code === 'ENOENT'
          ? '解释器文件不存在'
          : `无法启动解释器（${code ?? 'UNKNOWN'}）：${result.error.message}`
    return {
      ok: false,
      timedOut: code === 'ETIMEDOUT',
      exitCode: null,
      reason,
      pipAvailable: false,
      pipVersion: null,
      pipError: null,
      dependenciesOk: false,
      missingImports: [],
    }
  }
  const stdout = String(result.stdout ?? '')
  const stderr = String(result.stderr ?? '').trim()
  const sentinelLine = stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith(CORE_PROBE_SENTINEL))
    .pop()
  if (!sentinelLine) {
    return {
      ok: false,
      timedOut: false,
      exitCode: result.status,
      reason: `解释器未返回预期探测结果（退出码 ${result.status}${stderr ? `：${stderr.slice(-300)}` : ''}）`,
      pipAvailable: false,
      pipVersion: null,
      pipError: null,
      dependenciesOk: false,
      missingImports: [],
    }
  }
  let document
  try {
    document = JSON.parse(sentinelLine.slice(CORE_PROBE_SENTINEL.length))
  } catch {
    return {
      ok: false,
      timedOut: false,
      exitCode: result.status,
      reason: '解释器探测结果无法解析',
      pipAvailable: false,
      pipVersion: null,
      pipError: null,
      dependenciesOk: false,
      missingImports: [],
    }
  }
  const importResults = document.imports && typeof document.imports === 'object' ? document.imports : {}
  const missingImports = imports.filter((name) => importResults[name]?.ok !== true)
  return {
    ok: true,
    timedOut: false,
    exitCode: result.status,
    reason: null,
    version: Array.isArray(document.version) ? document.version.join('.') : null,
    executable: typeof document.executable === 'string' ? document.executable : null,
    pipAvailable: Boolean(document.pip?.available),
    pipVersion: document.pip?.version ?? null,
    pipError: document.pip?.error ?? null,
    dependenciesOk: missingImports.length === 0,
    missingImports,
    importErrors: Object.fromEntries(
      missingImports.map((name) => [name, importResults[name]?.error ?? '无法导入']),
    ),
  }
}

function healthStatus(health, requirementsInfo) {
  if (!health.pythonExists) return health.venvDirExists ? 'environment_incomplete' : 'not_installed'
  if (!health.probe.ok) return 'interpreter_broken'
  if (!health.pipAvailable) return 'pip_missing'
  if (!requirementsInfo.exists) return 'requirements_missing'
  if (!health.dependenciesOk) return 'dependencies_missing'
  if (!health.marker.installed) return 'marker_missing'
  if (!health.marker.matches) return 'marker_stale'
  return 'ready'
}

// Graded local diagnosis. The returned object is the single source of truth for
// readiness: `ready` implies the interpreter runs, pip works, every current
// requirement imports, and the marker fingerprint matches the requirements.
export function coreRuntimeHealth({ timeoutMs = CORE_PROBE_TIMEOUT_MS } = {}) {
  const paths = venvPaths()
  const requirementsInfo = coreRequirementsInfo()
  const venvDirExists = isDirectory(paths.venv)
  const python = runtimePythonPath()
  const pythonExists = Boolean(python)
  const health = {
    venvDir: paths.venv,
    venvDirExists,
    python,
    pythonExists,
    pythonExecutable: false,
    pythonVersion: null,
    pipAvailable: false,
    pipVersion: null,
    pipError: null,
    dependenciesOk: false,
    missingImports: [],
    importErrors: {},
    probe: { ok: false, timedOut: false, exitCode: null, reason: null },
    requirements: requirementsInfo,
    marker: markerState(requirementsInfo),
    status: null,
    ready: false,
    checkedAt: new Date().toISOString(),
  }
  if (pythonExists) {
    const probe = probeInterpreter(python, requirementsInfo.imports, timeoutMs)
    health.pythonExecutable = probe.ok
    health.pythonVersion = probe.version ?? null
    health.pipAvailable = probe.pipAvailable
    health.pipVersion = probe.pipVersion ?? null
    health.pipError = probe.pipError ?? null
    health.dependenciesOk = probe.ok && probe.dependenciesOk
    health.missingImports = probe.missingImports ?? []
    health.importErrors = probe.importErrors ?? {}
    health.probe = { ok: probe.ok, timedOut: probe.timedOut, exitCode: probe.exitCode, reason: probe.reason }
  }
  health.status = healthStatus(health, requirementsInfo)
  health.ready = health.status === 'ready'
  return health
}

export function coreRuntimeReady() {
  return coreRuntimeHealth().ready
}

function runtimeStatusText(health) {
  return RUNTIME_STATUS_TEXT[health?.status] ?? health?.status ?? '未知状态'
}

// Compact diagnostics for tool payloads / logs (no raw import errors).
export function runtimeHealthSummary(health) {
  if (!health) return null
  return {
    status: health.status,
    statusText: runtimeStatusText(health),
    ready: health.ready,
    venvDirExists: health.venvDirExists,
    pythonPresent: health.pythonExists,
    pythonExecutable: health.pythonExecutable,
    pythonVersion: health.pythonVersion,
    pipAvailable: health.pipAvailable,
    pipVersion: health.pipVersion,
    dependenciesOk: health.dependenciesOk,
    missingImports: health.missingImports,
    markerMatches: health.marker?.matches ?? false,
    requirementsVersion: health.requirements?.version ?? null,
    requirementsSha256: health.requirements?.sha256 ?? null,
    requirementsFile: health.requirements?.path ?? null,
    checkedAt: health.checkedAt,
  }
}

// Accurate recovery wording for the graded diagnosis (status level).
export function runtimeRepairAdvice(health) {
  const status = typeof health === 'string' ? health : health?.status
  switch (status) {
    case 'not_installed':
    case 'environment_incomplete':
      return {
        hint: '首次安装需要联网下载依赖（约 20MB）；中断过的环境会被重建，已完成的步骤不会重复下载。',
        recovery: `安装中断后重新调用 study_setup_runtime 会从当前诊断继续修复。若反复失败：${CORE_RECOVERY_PYTHON_HINT}`,
      }
    case 'interpreter_broken':
      return {
        hint: '私有解释器无法运行：会先把损坏的 venv 改名保留诊断，再用插件自带 Python 重新创建并安装依赖；不触碰学习资料与登录凭据。',
        recovery: `若自动重建仍失败：${CORE_RECOVERY_PYTHON_HINT}`,
      }
    case 'pip_missing':
      return {
        hint: '解释器可用但缺少 pip：会先用离线 ensurepip 修复 pip，再用 pip 安装/验证依赖，不会反复调用不存在的 pip。',
        recovery: `若离线 ensurepip 仍失败：${CORE_RECOVERY_PYTHON_HINT}`,
      }
    case 'dependencies_missing':
      return {
        hint: '运行依赖缺失或导入失败：会重新安装依赖并逐项验证，健康检查通过前不会报告已完成。',
        recovery: `若依赖安装一直失败（例如一直连不上软件源）：${CORE_RECOVERY_PYTHON_HINT}`,
      }
    case 'marker_missing':
    case 'marker_stale':
      return {
        hint: '已安装环境需要按当前依赖清单重新验证并刷新安装记录；旧标记不会被当作已完成。',
        recovery: `若验证反复失败：${CORE_RECOVERY_PYTHON_HINT}`,
      }
    case 'requirements_missing':
      return {
        hint: '插件包缺少 requirements-core.txt：请重新安装 Moodle Study Assistant 插件（学习资料和登录凭据不受影响）。',
        recovery: '重新安装 Moodle Study Assistant 插件后再调用 study_setup_runtime。不要删除学习档案。',
      }
    default:
      return {
        hint: '安装中断或损坏时可重复调用，已完成的步骤会跳过；每次都会重新做健康检查。',
        recovery: CORE_RECOVERY_PYTHON_HINT,
      }
  }
}

function verificationScript(imports) {
  // ASCII-only output: job logs are written as UTF-8 but child stdout uses the
  // Windows console code page, so non-ASCII progress could be garbled.
  return [
    'import sys',
    `names = ${JSON.stringify(imports)}`,
    'missing = []',
    'for name in names:',
    '    try:',
    '        __import__(name)',
    '    except BaseException as exc:',
    '        missing.append(name + ": " + type(exc).__name__ + ": " + str(exc))',
    'if missing:',
    '    print("missing imports: " + "; ".join(missing), file=sys.stderr)',
    '    sys.exit(1)',
    'print("dependency check passed: " + (", ".join(names) if names else "no third-party dependencies"))',
  ].join('\n')
}

function ensurePipStep(command) {
  return {
    label: '修复 pip（离线 ensurepip）',
    command,
    args: ['-m', 'ensurepip', '--upgrade'],
    cwd: supportDir(),
  }
}

function installCoreStep(command, requirementsFile) {
  return {
    label: '安装学习引擎依赖',
    command,
    args: ['-m', 'pip', 'install', '--no-cache-dir', '--disable-pip-version-check', '-r', requirementsFile],
    cwd: supportDir(),
  }
}

function verifyCoreStep(command, imports) {
  return {
    label: '验证学习引擎依赖',
    command,
    args: ['-c', verificationScript(imports)],
    cwd: supportDir(),
  }
}

// Move a broken/incomplete private venv aside instead of deleting it, so a
// failed interpreter can still be diagnosed after the rebuild. The rename runs
// as a normal job step (Node child process, no shell) with a short retry loop
// for transient Windows file locks.
function preserveBrokenVenvStep(paths) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const base = join(supportDir(), `venv.broken-${stamp}`)
  let target = base
  for (let index = 2; existsSync(target); index += 1) target = `${base}-${index}`
  const script = [
    "const fs = require('node:fs')",
    'const from = process.argv[1]',
    'const to = process.argv[2]',
    "if (!fs.existsSync(from)) { process.stdout.write('无需保留：' + from + '\\n'); process.exit(0) }",
    'let lastError = null',
    'for (let attempt = 1; attempt <= 5; attempt += 1) {',
    '  try {',
    '    fs.renameSync(from, to)',
    "    process.stdout.write('已保留损坏环境诊断：' + to + '\\n')",
    '    process.exit(0)',
    '  } catch (error) {',
    '    lastError = error',
    '    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 400)',
    '  }',
    '}',
    "process.stderr.write('无法重命名损坏环境（可能被其他程序占用）：' + ((lastError && lastError.message) || lastError) + '\\n')",
    'process.exit(1)',
  ].join('\n')
  return {
    label: '保留损坏环境诊断目录',
    command: process.execPath,
    args: ['-e', script, paths.venv, target],
    cwd: supportDir(),
  }
}

// The record written after a successful repair chain. Only installed + the
// current requirement fingerprint makes the marker authoritative again.
export function coreRuntimeRecord(basePython = null) {
  const requirementsInfo = coreRequirementsInfo()
  const now = new Date().toISOString()
  return {
    installed: true,
    requirementsVersion: requirementsInfo.version,
    requirementsSha256: requirementsInfo.sha256,
    requirementsFile: requirementsInfo.path,
    dependencies: requirementsInfo.imports,
    basePython: basePython ?? null,
    verifiedAt: now,
    at: now,
  }
}

// Build the sequential repair steps derived from the graded diagnosis. The
// caller skips the whole chain when health.ready is true.
export function coreRuntimeSteps({ markRuntime = true, health = null } = {}) {
  let current = health ?? coreRuntimeHealth()
  // A timed-out probe is not a proven-broken interpreter: re-check once with a
  // longer timeout before the chain renames/rebuilds the environment.
  if (current.probe?.timedOut) current = coreRuntimeHealth({ timeoutMs: CORE_PROBE_TIMEOUT_MS * 2 })
  const requirementsInfo = current.requirements ?? coreRequirementsInfo()
  const paths = venvPaths()
  const venvPython = process.platform === 'win32' ? paths.windows : paths.posix

  if (!requirementsInfo.exists) {
    return {
      error: {
        code: 'CORE_REQUIREMENTS_MISSING',
        message: `插件包缺少依赖清单 ${requirementsInfo.path}，无法确认或安装运行依赖。请重新安装 Moodle Study Assistant 插件。`,
        nextStep: '重新安装 Moodle Study Assistant 插件后再调用 study_setup_runtime；不要删除学习档案。',
      },
      steps: [],
      health: current,
    }
  }

  const steps = []
  let base = null
  const needsRebuild = !current.pythonExecutable
  if (needsRebuild) {
    base = findBasePython()
    if (!base) {
      return {
        error: {
          code: 'PYTHON_NOT_FOUND',
          message:
            '私有 Python 环境需要重建，但找不到可用的基础 Python。请重新安装 DeepSeek Harness（自带 Python），或安装 Python 3.11+ 后重试。',
          nextStep:
            '关闭并重开 Harness 后重试；仍失败请修复/重装 DeepSeek Harness 自带的 Python，或设置 MOODLE_STUDY_BASE_PYTHON 指向可用的 Python 3.11+。不要删除学习档案。',
        },
        steps: [],
        health: current,
      }
    }
    if (current.venvDirExists) steps.push(preserveBrokenVenvStep(paths))
    steps.push({
      label: '创建本地 Python 环境',
      command: base.command,
      args: [...base.prefix, '-m', 'venv', paths.venv],
      cwd: supportDir(),
    })
    // Fresh venvs normally ship pip, but a base Python built without ensurepip
    // would silently create a pip-less environment; make it explicit offline.
    steps.push(ensurePipStep(venvPython))
  } else if (!current.pipAvailable) {
    steps.push(ensurePipStep(venvPython))
  }

  const needsInstall =
    needsRebuild ||
    !current.dependenciesOk ||
    !current.marker.installed ||
    !current.marker.shaMatches
  if (needsInstall) steps.push(installCoreStep(venvPython, requirementsInfo.path))
  steps.push(verifyCoreStep(venvPython, requirementsInfo.imports))

  const basePython = base?.command ?? current.marker?.basePython ?? null
  return {
    error: null,
    steps,
    health: current,
    markRuntime: () => {
      if (!markRuntime) return
      writeRuntime({ core: coreRuntimeRecord(basePython) })
    },
  }
}

// Sign-in dependency steps (Playwright + driver, no browser download).
export function loginRuntimeSteps() {
  const python = runtimePythonPath()
  if (!python) return { error: '请先完成运行依赖安装', steps: [] }
  return {
    error: null,
    steps: [
      {
        label: '安装学校登录组件',
        command: python,
        args: [
          '-m',
          'pip',
          'install',
          '--no-cache-dir',
          '--disable-pip-version-check',
          '-r',
          requirements('requirements-login.txt'),
        ],
        cwd: supportDir(),
      },
      {
        label: '验证学校登录组件',
        command: python,
        args: ['-c', 'from playwright.sync_api import sync_playwright'],
        cwd: supportDir(),
      },
    ],
    markRuntime: () => {
      writeRuntime({ login: { installed: true, at: new Date().toISOString() } })
    },
  }
}

export function loginRuntimeReady() {
  return Boolean(readRuntime().login?.installed)
}

// Environment shared by every engine child process.
export function engineEnv(settings, extra = {}) {
  return {
    ...process.env,
    STUDY_DATA_ROOT: settings?.dataDir ?? '',
    STUDY_PYTHON: runtimePythonPath() ?? '',
    STUDY_NODE: process.execPath,
    ELECTRON_RUN_AS_NODE: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    TZ: settings?.timezone || DEFAULT_TIMEZONE,
    ...extra,
  }
}

export function nodeStep(label, scriptPath, args, settings) {
  return {
    label,
    command: process.execPath,
    args: [join(programDir(), scriptPath), ...args],
    cwd: programDir(),
    env: engineEnv(settings),
  }
}

export function pythonStep(label, scriptPath, args, settings) {
  return {
    label,
    command: runtimePythonPath() ?? 'python',
    args: [join(programDir(), scriptPath), ...args],
    cwd: programDir(),
    env: engineEnv(settings),
  }
}

export function venvPython() {
  return runtimePythonPath()
}

export function pythonVersionHint() {
  if (process.platform === 'win32') {
    const local = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local')
    return `支持目录 ${supportDir()}（本机程序数据），资料目录由学生选择。`
  }
  return `支持目录 ${supportDir()}。`
}
