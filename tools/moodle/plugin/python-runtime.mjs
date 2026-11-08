// Python runtime bootstrap for the Moodle Study Assistant plugin.
//
// The plugin never assumes a developer environment: it uses the Python
// distribution shipped with DeepSeek Harness when available, falls back to a
// system Python, and installs the pinned engine dependencies into a private
// virtual environment under the plugin support directory.
import { existsSync, readdirSync, statSync } from 'node:fs'
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

function requirements(name) {
  return join(programDir(), name)
}

function runtimePythonPath() {
  const paths = venvPaths()
  if (existsSync(paths.windows)) return paths.windows
  if (existsSync(paths.posix)) return paths.posix
  return null
}

// Build the sequential setup steps for the core engine runtime. The caller
// skips the whole chain when runtimeReady() is already true.
export function coreRuntimeSteps({ markRuntime = true } = {}) {
  const base = findBasePython()
  if (!base) {
    return {
      error:
        '找不到可用的 Python 运行时。请重新安装 DeepSeek Harness（自带 Python），或安装 Python 3.11+ 后重试。',
      steps: [],
    }
  }
  const paths = venvPaths()
  const steps = []
  if (!runtimePythonPath()) {
    steps.push({
      label: '创建本地 Python 环境',
      command: base.command,
      args: [...base.prefix, '-m', 'venv', paths.venv],
      cwd: supportDir(),
    })
  }
  const python = paths.windows
  const onWindows = process.platform === 'win32'
  const venvPython = onWindows ? paths.windows : paths.posix
  const coreReqs = requirements('requirements-core.txt')
  const installArgs = ['-m', 'pip', 'install', '--no-cache-dir', '--disable-pip-version-check']
  steps.push({
    label: '安装学习引擎依赖',
    command: venvPython,
    args: [...installArgs, '-r', coreReqs],
    cwd: supportDir(),
  })
  steps.push({
    label: '验证学习引擎依赖',
    command: venvPython,
    args: ['-c', 'import fsrs, pypdf, pypdfium2, PIL, tzdata'],
    cwd: supportDir(),
  })
  return {
    error: null,
    steps,
    markRuntime: () => {
      if (!markRuntime) return
      writeRuntime({ core: { installed: true, basePython: base.command, at: new Date().toISOString() } })
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

export function coreRuntimeReady() {
  return Boolean(runtimePythonPath()) && Boolean(readRuntime().core?.installed)
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
