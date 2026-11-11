// Regression tests for the plugin host's fallback recovery tool
// (`moodle_study_status`), which must report the same graded runtime readiness
// as the MCP server even when the MCP server process cannot start.
//
// The tool is loaded through `apply(fakeCtx, {supportDir})` exactly like the
// DeepSeek Harness host does, but against a throwaway support directory with a
// real private venv. Everything is local and offline: PIP_NO_INDEX=1, no school
// network, no real Harness profile, no real Study/ data.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { after, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const repoRoot = fileURLToPath(new URL('..', import.meta.url))

function writableTempRoot() {
  const preferred = process.env.TEMP || process.env.TMP || tmpdir()
  // `python -m venv` refuses an environment whose target is reached through an
  // 8.3 short name (the DSH sandbox %TEMP% may look like C:\Users\PETERJ~1\...):
  // fall back to the repository tmp/ directory, which is writable and long.
  if (!/~\d+/.test(preferred)) {
    try {
      return realpathSync(mkdtempSync(join(realpathSync(preferred), 'moodle-plugin-status-')))
    } catch {
      // Fall back below.
    }
  }
  const fallback = join(repoRoot, 'tmp', 'plugin-status-temp')
  mkdirSync(fallback, { recursive: true })
  return realpathSync(mkdtempSync(join(fallback, 'run-')))
}

const tempRoot = writableTempRoot()
process.env.TEMP = tempRoot
process.env.TMP = tempRoot
process.env.TMPDIR = tempRoot
process.env.PIP_NO_INDEX = '1'
process.env.PIP_NO_INPUT = '1'
process.env.PIP_DISABLE_PIP_VERSION_CHECK = '1'
// The real plugin host has no MOODLE_STUDY_* overrides; only per-test isolated
// paths are set below. Inherited Harness/chat identity is stripped so nothing
// can resolve into the student's real profile.
for (const inherited of [
  'DSH_PROFILE_DIR',
  'DSH_PROFILE',
  'DSH_SESSION_ID',
  'STUDY_CHAT_ID',
  'MOODLE_STUDY_PROGRAM_DIR',
  'MOODLE_STUDY_CORE_IMPORT_OVERRIDE',
  'MOODLE_STUDY_LOGIN_IMPORT_OVERRIDE',
]) {
  delete process.env[inherited]
}
delete process.env.DSH_HOME
process.env.MOODLE_STUDY_DSH_HOME = join(tempRoot, 'dsh-home')

function findBasePython() {
  const candidates = [
    process.env.MOODLE_STUDY_TEST_BASE_PYTHON,
    join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    join(repoRoot, '.venv', 'bin', 'python'),
  ].filter(Boolean)
  return (
    candidates.find((candidate) => {
      try {
        return statSync(candidate).isFile()
      } catch {
        return false
      }
    }) ?? null
  )
}

const basePython = findBasePython()
const skipReason = basePython ? false : '需要可用的 Python 3.11+ 才能验证真实隔离 venv 的分级诊断'

const support = join(tempRoot, 'support')
const otherSupport = join(tempRoot, 'other-support')
const dataDir = join(tempRoot, 'data')
const programPlaceholder = join(tempRoot, 'program-without-tools')
for (const dir of [support, otherSupport, join(dataDir, 'Study', '_System'), join(dataDir, '.private'), programPlaceholder]) {
  mkdirSync(dir, { recursive: true })
}

function writeJson(path, value) {
  mkdirSync(join(path, '..'), { recursive: true })
  writeFileSync(path, JSON.stringify(value, null, 2) + '\n', 'utf8')
}

function venvPythonPath() {
  return process.platform === 'win32' ? join(support, 'venv', 'Scripts', 'python.exe') : join(support, 'venv', 'bin', 'python')
}

function createRuntimeFixture() {
  const created = spawnSync(basePython, ['-m', 'venv', join(support, 'venv')], {
    encoding: 'utf8',
    windowsHide: true,
    timeout: 180000,
    env: { ...process.env },
  })
  assert.equal(created.status, 0, `无法创建测试用 venv：${created.stderr || created.error?.message}`)
}

// Fake host context: the tool registrations are plain objects, so a tiny stub
// captures them exactly the way the real Cordis context would.
function fakeContext() {
  const tools = new Map()
  const ctx = {
    provide() {},
    effect(fn) {
      fn()
    },
    skills: { register() {} },
    systemPrompt: { section() {} },
    tools: {
      register(tool) {
        tools.set(tool.name, tool)
      },
    },
  }
  return { ctx, tools }
}

async function loadHostPlugin() {
  const { apply } = await import(pathToFileURL(join(repoRoot, 'plugin', 'index.js')).href)
  const { ctx, tools } = fakeContext()
  apply(ctx, { supportDir: support })
  const tool = tools.get('moodle_study_status')
  assert.ok(tool, 'moodle_study_status 未注册')
  return tool
}

after(() => {
  try {
    rmSync(tempRoot, { recursive: true, force: true, maxRetries: 3 })
  } catch {
    // A venv file may still be locked briefly on Windows.
  }
})

test('moodle_study_status reports graded readiness from an isolated, healthy venv', { skip: skipReason }, async () => {
  createRuntimeFixture()
  // The resource module the host tool imports reads the same support/program
  // env vars; prepare the marker while they point at this isolated support dir.
  process.env.MOODLE_STUDY_SUPPORT_DIR = support
  process.env.MOODLE_STUDY_CORE_IMPORT_OVERRIDE = 'json'
  process.env.MOODLE_STUDY_LOGIN_IMPORT_OVERRIDE = 'json'
  const runtimeModule = await import('../tools/moodle/plugin/python-runtime.mjs')
  const settingsModule = await import('../tools/moodle/plugin/settings.mjs')
  settingsModule.writeRuntime({
    core: runtimeModule.coreRuntimeRecord(basePython),
    login: runtimeModule.loginRuntimeRecord(),
  })
  assert.equal(runtimeModule.coreRuntimeHealth().ready, true, '健康夹具应通过分级检查')
  assert.equal(runtimeModule.loginRuntimeHealth().ready, true, '登录健康夹具应通过分级检查')
  writeJson(join(support, 'settings.json'), { schemaVersion: 1, dataDir, stage: 'located' })

  // Deliberately misalign the inherited support dir and leave the program dir
  // unset: the tool must align paths for the probe and fall back to the source
  // checkout layout (<repo>/plugin -> <repo>) instead of <plugin>/program.
  process.env.MOODLE_STUDY_SUPPORT_DIR = otherSupport
  const tool = await loadHostPlugin()
  assert.equal(process.env.MOODLE_STUDY_PROGRAM_DIR, undefined)

  const status = await tool.execute()
  assert.equal(status.runtimeReady, true)
  assert.equal(status.runtimeStatus, 'ready')
  assert.equal(status.loginReady, true)
  assert.equal(status.loginRuntimeStatus, 'ready')
  assert.ok(status.runtimeHealth && typeof status.runtimeHealth === 'object')
  assert.equal(status.runtimeHealth.status, 'ready')
  assert.equal(status.runtimeHealth.ready, true)
  assert.equal(status.pythonPresent, true)
  assert.equal(status.probeError, undefined)
  // Source checkout fallback: the package dir is <repo>/plugin, so the program
  // root reported/used for diagnostics is the repository root.
  assert.equal(status.programDir, resolve(repoRoot))
  assert.equal(status.programPresent, true)
  assert.equal(status.supportDir, support)
  // The tool only aligns the environment for the health probe; afterwards the
  // caller's environment is restored.
  assert.equal(process.env.MOODLE_STUDY_SUPPORT_DIR, otherSupport, '探测后应恢复 MOODLE_STUDY_SUPPORT_DIR')
  assert.equal(process.env.MOODLE_STUDY_PROGRAM_DIR, undefined, '探测后应恢复 MOODLE_STUDY_PROGRAM_DIR')

  // A damaged private interpreter is reported as broken, not as "installed".
  writeFileSync(venvPythonPath(), 'not-a-real-executable', 'utf8')
  const broken = await tool.execute()
  assert.equal(broken.runtimeReady, false, '损坏解释器不得报告 runtimeReady=true')
  assert.equal(broken.runtimeStatus, 'interpreter_broken')
  assert.equal(broken.runtimeHealth.status, 'interpreter_broken')
  assert.equal(broken.runtimeHealth.pythonExecutable, false)
  assert.equal(broken.loginReady, false)
  assert.equal(broken.nextStep, 'install_runtime')
  assert.ok(
    broken.recovery.some((line) => line.includes('study_setup_runtime')),
    '损坏解释器应给出 study_setup_runtime 恢复入口',
  )
  assert.equal(process.env.MOODLE_STUDY_SUPPORT_DIR, otherSupport, '探测后应恢复 MOODLE_STUDY_SUPPORT_DIR')
  assert.equal(process.env.MOODLE_STUDY_PROGRAM_DIR, undefined, '探测后应恢复 MOODLE_STUDY_PROGRAM_DIR')
})

test('moodle_study_status is conservative when the graded module cannot be loaded', { skip: skipReason }, async () => {
  // A copy of the host entry outside the package layout cannot locate
  // tools/moodle/plugin/python-runtime.mjs; readiness must fall back to false
  // instead of trusting pythonPresent + the old marker.
  const copyDir = join(tempRoot, 'plugin-copy')
  mkdirSync(copyDir, { recursive: true })
  copyFileSync(join(repoRoot, 'plugin', 'index.js'), join(copyDir, 'index.js'))
  const { apply } = await import(pathToFileURL(join(copyDir, 'index.js')).href)
  const { ctx, tools } = fakeContext()
  process.env.MOODLE_STUDY_PROGRAM_DIR = programPlaceholder
  try {
    apply(ctx, { supportDir: support })
    const tool = tools.get('moodle_study_status')
    assert.ok(tool)
    const status = await tool.execute()
    assert.equal(status.runtimeReady, false, '诊断模块不可用时必须保守报告未就绪')
    assert.equal(status.loginReady, false)
    assert.equal(status.runtimeStatus, 'probe_failed')
    assert.ok(status.probeError, '应说明诊断模块加载失败')
    assert.equal(status.nextStep, 'install_runtime')
  } finally {
    delete process.env.MOODLE_STUDY_PROGRAM_DIR
  }
})
