// Regression tests for the plugin's Python runtime health check and recovery.
//
// Everything here is local and offline: real virtual environments are created
// from the checked-in development Python, requirement installations point at a
// tiny locally-built wheel (PIP_NO_INDEX=1), and support/program/data dirs are
// throwaway directories under the OS temp dir. No school network, no real
// .private/ credential and no real Study/ data is read.
//
// The DSH sandbox may make the system %TEMP% unwritable, so the writable test
// temp dir is bootstrapped below and exported to child processes (Node's
// os.tmpdir() and Python both inherit TEMP/TMP).
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
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
      return realpathSync(mkdtempSync(join(realpathSync(preferred), 'moodle-runtime-')))
    } catch {
      // Fall back below.
    }
  }
  const fallback = join(repoRoot, 'tmp', 'runtime-recovery-temp')
  mkdirSync(fallback, { recursive: true })
  return realpathSync(mkdtempSync(join(fallback, 'run-')))
}

const tempRoot = writableTempRoot()
process.env.TEMP = tempRoot
process.env.TMP = tempRoot
process.env.TMPDIR = tempRoot
// The repair chain installs only from local sources in these tests; refuse any
// network access from pip so a missing third-party package fails loudly.
process.env.PIP_NO_INDEX = '1'
process.env.PIP_NO_INPUT = '1'
process.env.PIP_DISABLE_PIP_VERSION_CHECK = '1'
// Never inherit the real Harness profile / chat identity: an isolated DSH home
// keeps base-Python discovery and any accidental profile lookup inside tmp.
for (const inherited of [
  'DSH_PROFILE_DIR',
  'DSH_PROFILE',
  'DSH_SESSION_ID',
  'STUDY_CHAT_ID',
  'MOODLE_STUDY_LOGIN_IMPORT_OVERRIDE',
]) {
  delete process.env[inherited]
}
delete process.env.DSH_HOME
process.env.MOODLE_STUDY_DSH_HOME = join(tempRoot, 'dsh-home')

function findBasePython() {
  const candidates = [
    process.env.MOODLE_STUDY_TEST_BASE_PYTHON,
    process.env.MOODLE_STUDY_BASE_PYTHON,
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
const skipReason = basePython ? false : '需要可用的 Python 3.11+ 才能验证真实的 venv 修复链'

const runtime = await import('../tools/moodle/plugin/python-runtime.mjs')
const onboarding = await import('../tools/moodle/plugin/onboarding-tools.mjs')
const jobs = await import('../tools/moodle/plugin/jobs.mjs')
const settingsModule = await import('../tools/moodle/plugin/settings.mjs')

const {
  CORE_REQUIREMENTS_VERSION,
  LOGIN_REQUIREMENTS_VERSION,
  coreRequirementsInfo,
  coreRuntimeHealth,
  coreRuntimeRecord,
  coreRuntimeSteps,
  loginRequirementsInfo,
  loginRuntimeHealth,
  loginRuntimeReady,
  loginRuntimeRecord,
  loginRuntimeSteps,
} = runtime
const { runtimeHandler } = onboarding
const { localCapabilities, readSettings } = settingsModule

after(() => {
  try {
    rmSync(tempRoot, { recursive: true, force: true, maxRetries: 3 })
  } catch {
    // A venv file may still be locked briefly on Windows; the temp dir is
    // throwaway and the next run creates a new one.
  }
})

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function stubGateway() {
  const school = { state: 'setup', connected: false, requiresLogin: false, retryable: false }
  return { probe: async () => school, status: () => school, invalidate() {} }
}

function writeJson(path, value) {
  mkdirSync(join(path, '..'), { recursive: true })
  writeFileSync(path, JSON.stringify(value, null, 2) + '\n', 'utf8')
}

// Isolated support/program/data dirs for one scenario. The program dir has an
// empty (or custom) requirements-core.txt / requirements-login.txt so the real
// chain runs without downloading the study engine dependencies.
function createCase({ requirements = '', importOverride = null, loginRequirements = '' } = {}) {
  const root = mkdtempSync(join(tempRoot, 'case-'))
  const support = join(root, 'support')
  const program = join(root, 'program')
  const dataDir = join(root, 'data')
  mkdirSync(support, { recursive: true })
  mkdirSync(program, { recursive: true })
  mkdirSync(join(dataDir, 'Study', '_System'), { recursive: true })
  mkdirSync(join(dataDir, 'Study', '_Inbox'), { recursive: true })
  mkdirSync(join(dataDir, '.private'), { recursive: true })
  writeFileSync(join(program, 'requirements-core.txt'), requirements, 'utf8')
  writeFileSync(join(program, 'requirements-login.txt'), loginRequirements, 'utf8')
  writeJson(join(dataDir, 'config.local.json'), {
    moodle_url: 'https://school.example',
    timezone: 'Asia/Hong_Kong',
  })
  writeJson(join(support, 'settings.json'), { schemaVersion: 1, dataDir, stage: 'located' })
  const env = { root, support, program, dataDir }
  activate(env, importOverride)
  return env
}

function activate(env, importOverride = null) {
  process.env.MOODLE_STUDY_SUPPORT_DIR = env.support
  process.env.MOODLE_STUDY_PROGRAM_DIR = env.program
  process.env.MOODLE_STUDY_BASE_PYTHON = basePython ?? ''
  process.env.MOODLE_STUDY_DSH_HOME = join(env.root, 'dsh-home')
  if (importOverride) process.env.MOODLE_STUDY_CORE_IMPORT_OVERRIDE = importOverride
  else delete process.env.MOODLE_STUDY_CORE_IMPORT_OVERRIDE
}

function venvPythonPath(support) {
  return process.platform === 'win32'
    ? join(support, 'venv', 'Scripts', 'python.exe')
    : join(support, 'venv', 'bin', 'python')
}

function runVenvCreation(target, { withPip = true } = {}) {
  const args = withPip ? ['-m', 'venv', target] : ['-m', 'venv', '--without-pip', target]
  const created = spawnSync(basePython, args, {
    encoding: 'utf8',
    windowsHide: true,
    timeout: 180000,
    env: { ...process.env },
  })
  assert.equal(created.status, 0, `创建 venv 失败：${created.stderr || created.error?.message}`)
}

// One real venv is enough for the scenarios that only need a healthy starting
// point; it is copied into each isolated support dir to keep the suite fast.
let seedVenvDir = null
function seedVenv() {
  if (!seedVenvDir) {
    const root = mkdtempSync(join(tempRoot, 'seed-'))
    runVenvCreation(join(root, 'venv'))
    seedVenvDir = join(root, 'venv')
  }
  return seedVenvDir
}

function copySeedVenv(support) {
  cpSync(seedVenv(), join(support, 'venv'), { recursive: true })
}

function readRuntimeFile(env) {
  return JSON.parse(readFileSync(join(env.support, 'runtime.json'), 'utf8'))
}

function writeCoreMarker(env) {
  writeJson(join(env.support, 'runtime.json'), { core: coreRuntimeRecord(basePython) })
}

async function waitForJob(jobId, { timeoutMs = 180000 } = {}) {
  const started = Date.now()
  for (;;) {
    const view = jobs.jobView(jobId)
    if (view && view.state !== 'running') return view
    if (Date.now() - started > timeoutMs) {
      throw new Error(`任务等待超时：${jobId}\n${String(view?.log ?? '').slice(-2000)}`)
    }
    await sleep(50)
  }
}

// Run one repair-chain step exactly as the job runner would (same command,
// args, cwd and inherited offline env), but synchronously for assertions.
function runStep(step) {
  const result = spawnSync(step.command, step.args, {
    cwd: step.cwd,
    env: { ...process.env, ...(step.env ?? {}) },
    encoding: 'utf8',
    timeout: 240000,
    windowsHide: true,
    maxBuffer: 16 * 1024 * 1024,
  })
  return { ...result, ok: result.status === 0 }
}

// site-packages directory of the private venv (used to damage a module while
// leaving its dist-info installed, which is what makes plain pip install skip).
function pythonPurelib(python) {
  const probe = spawnSync(
    python,
    ['-c', 'import sys, sysconfig; sys.stdout.write(sysconfig.get_paths()["purelib"])'],
    { encoding: 'utf8', timeout: 60000, windowsHide: true, env: { ...process.env } },
  )
  assert.equal(probe.status, 0, `无法定位 venv site-packages：${probe.stderr || probe.error?.message}`)
  return probe.stdout.trim()
}

const MAKE_WHEEL_PY = String.raw`
import base64, hashlib, sys, zipfile
wheel_path = sys.argv[1]
name = 'moodle_study_stubdep'
version = '1.0.0'
dist = name + '-' + version + '.dist-info'
entries = {
    name + '.py': b'VALUE = 42\n',
    dist + '/METADATA': b'Metadata-Version: 2.1\nName: moodle-study-stubdep\nVersion: 1.0.0\nSummary: offline test dependency\n',
    dist + '/WHEEL': b'Wheel-Version: 1.0\nGenerator: moodle-study-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
}
record = []
with zipfile.ZipFile(wheel_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    for rel, data in entries.items():
        zf.writestr(rel, data)
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        record.append(rel + ',sha256=' + digest + ',' + str(len(data)))
    record.append(dist + '/RECORD,,')
    zf.writestr(dist + '/RECORD', ('\n'.join(record) + '\n').encode())
print(wheel_path)
`

// Build a tiny wheel locally so the repair chain can install a genuinely
// missing dependency through real pip without any network access.
function buildStubWheel(dir) {
  mkdirSync(dir, { recursive: true })
  const wheelPath = join(dir, 'moodle_study_stubdep-1.0.0-py3-none-any.whl')
  const scriptPath = join(dir, 'make_wheel.py')
  writeFileSync(scriptPath, MAKE_WHEEL_PY, 'utf8')
  const built = spawnSync(basePython, [scriptPath, wheelPath], {
    encoding: 'utf8',
    windowsHide: true,
    timeout: 60000,
  })
  assert.equal(built.status, 0, `构建本地测试 wheel 失败：${built.stderr || built.error?.message}`)
  return wheelPath
}

test('a fresh install builds a healthy runtime; later calls report alreadyReady without reinstalling', { skip: skipReason }, async () => {
  const env = createCase()
  const before = coreRuntimeHealth()
  assert.equal(before.venvDirExists, false)
  assert.equal(before.pythonExists, false)
  assert.equal(before.status, 'not_installed')
  assert.equal(before.ready, false)

  // A fresh install (nothing in the venv yet) must stay a plain pip install:
  // --force-reinstall is only for repairing a running interpreter's packages.
  const freshChain = coreRuntimeSteps({ health: before })
  const freshInstall = freshChain.steps.find((step) => step.label === '安装学习引擎依赖')
  assert.ok(freshInstall, '首次安装缺少依赖安装步骤')
  assert.ok(!freshInstall.args.includes('--force-reinstall'), '首次安装不应强制重装')

  const first = runtimeHandler({}, { remote: stubGateway() })
  assert.equal(first.ok, true)
  assert.notEqual(first.alreadyReady, true)
  assert.equal(first.repairing, true)
  assert.equal(first.runtime.status, 'not_installed')
  assert.ok(first.job?.id, '未启动修复任务')

  // A second call while the repair is running must not start another job.
  const second = runtimeHandler({}, { remote: stubGateway() })
  assert.ok(
    second.job ? second.job.id === first.job.id : second.alreadyReady === true,
    '重复调用不应启动第二个修复任务',
  )

  const done = await waitForJob(first.job.id)
  assert.equal(done.state, 'done', `安装任务失败：${done.error}\n${String(done.log).slice(-3000)}`)
  const labels = done.steps.map((step) => step.label)
  assert.ok(labels.includes('创建本地 Python 环境'))
  assert.ok(labels.includes('安装学习引擎依赖'))
  assert.ok(labels.includes('验证学习引擎依赖'))
  assert.ok(labels.indexOf('安装学习引擎依赖') < labels.indexOf('验证学习引擎依赖'))

  const health = coreRuntimeHealth()
  assert.equal(health.status, 'ready')
  assert.equal(health.ready, true)
  assert.equal(health.pythonExecutable, true)
  assert.equal(health.pipAvailable, true)
  assert.equal(health.dependenciesOk, true)
  assert.equal(health.marker.matches, true)
  assert.ok(existsSync(venvPythonPath(env.support)))

  const marker = readRuntimeFile(env).core
  const current = coreRequirementsInfo()
  assert.equal(marker.installed, true)
  assert.equal(marker.requirementsVersion, CORE_REQUIREMENTS_VERSION)
  assert.equal(marker.requirementsSha256, current.sha256)

  const third = runtimeHandler({}, { remote: stubGateway() })
  assert.equal(third.alreadyReady, true)
  assert.equal(third.runtime.status, 'ready')
  assert.equal(third.job, undefined)
  assert.equal(jobs.latestJob('setup-runtime').id, first.job.id, '健康后不应再启动安装任务')

  const status = await onboarding.collectStatus({ remote: stubGateway(), verifySchool: false })
  assert.equal(status.capabilities.runtimeReady, true)
  assert.equal(status.local.runtimeStatus, 'ready')
  assert.equal(status.local.runtimeHealth.pipAvailable, true)
  // Sign-in readiness is graded, never a bare marker flag: this fresh venv has
  // no Playwright yet, so it must not claim the login component is ready.
  assert.equal(status.local.loginRuntimeReady, false)
  assert.equal(status.local.loginRuntimeStatus, 'dependencies_missing')
  // A caller that does not pass a login health document gets the conservative
  // answer instead of the old marker-derived one.
  const conservative = localCapabilities(readSettings(), coreRuntimeHealth())
  assert.equal(conservative.loginRuntimeReady, false, '未传登录分级健康时必须保守 false')
  assert.equal(conservative.loginRuntimeStatus, 'unknown')
})

test('an interrupted creation without pip is repaired with offline ensurepip before installing', { skip: skipReason }, async () => {
  const env = createCase()
  runVenvCreation(join(env.support, 'venv'), { withPip: false })
  const before = coreRuntimeHealth()
  assert.equal(before.venvDirExists, true)
  assert.equal(before.pythonExists, true)
  assert.equal(before.pythonExecutable, true)
  assert.equal(before.pipAvailable, false)
  assert.equal(before.status, 'pip_missing')
  assert.equal(before.ready, false)

  const result = runtimeHandler({}, { remote: stubGateway() })
  assert.equal(result.ok, true)
  assert.notEqual(result.alreadyReady, true)
  assert.equal(result.runtime.status, 'pip_missing')
  const done = await waitForJob(result.job.id)
  assert.equal(done.state, 'done', `修复任务失败：${done.error}\n${String(done.log).slice(-3000)}`)
  const labels = done.steps.map((step) => step.label)
  assert.ok(!labels.includes('创建本地 Python 环境'), '解释器可用时不应重建 venv')
  const ensurePipIndex = labels.findIndex((label) => label.includes('ensurepip') || label.includes('pip'))
  const installIndex = labels.indexOf('安装学习引擎依赖')
  assert.ok(ensurePipIndex >= 0, '缺少 ensurepip 修复步骤')
  assert.ok(ensurePipIndex < installIndex, 'pip 修复必须先于依赖安装')

  const health = coreRuntimeHealth()
  assert.equal(health.status, 'ready')
  assert.equal(health.pipAvailable, true)
})

test('a matching old marker with a missing dependency is not reported ready and is repaired', { skip: skipReason }, async () => {
  const env = createCase()
  copySeedVenv(env.support)
  const wheelPath = buildStubWheel(env.root)
  const requirement = `moodle-study-stubdep @ ${pathToFileURL(wheelPath).href}\n`
  writeFileSync(join(env.program, 'requirements-core.txt'), requirement, 'utf8')
  // The marker claims the current fingerprint even though the package is absent:
  // a file/marker-only readiness check would wrongly answer alreadyReady.
  writeCoreMarker(env)

  const before = coreRuntimeHealth()
  assert.equal(before.marker.matches, true)
  assert.equal(before.marker.installed, true)
  assert.equal(before.dependenciesOk, false)
  assert.equal(before.status, 'dependencies_missing')
  assert.equal(before.ready, false)
  assert.deepEqual(before.missingImports, ['moodle_study_stubdep'])
  assert.ok(before.requirements.sha256)

  const result = runtimeHandler({}, { remote: stubGateway() })
  assert.notEqual(result.alreadyReady, true)
  assert.equal(result.runtime.status, 'dependencies_missing')
  // With a working interpreter + pip, a missing import cannot be "fixed" by a
  // plain install (pip may think the requirement is satisfied); the chain must
  // ask for a real reinstall and keep verify last.
  const chain = coreRuntimeSteps({ health: before })
  const chainLabels = chain.steps.map((step) => step.label)
  const installStep = chain.steps.find((step) => step.label === '安装学习引擎依赖')
  assert.ok(installStep.args.includes('--force-reinstall'))
  assert.ok(chainLabels.indexOf('安装学习引擎依赖') < chainLabels.indexOf('验证学习引擎依赖'))
  assert.equal(chainLabels[chainLabels.length - 1], '验证学习引擎依赖')
  const done = await waitForJob(result.job.id)
  assert.equal(done.state, 'done', `依赖修复任务失败：${done.error}\n${String(done.log).slice(-3000)}`)
  assert.ok(done.steps.map((step) => step.label).includes('安装学习引擎依赖'))

  const health = coreRuntimeHealth()
  assert.equal(health.dependenciesOk, true)
  assert.equal(health.missingImports.length, 0)
  assert.equal(health.ready, true)
  assert.equal(health.marker.matches, true)

  const again = runtimeHandler({}, { remote: stubGateway() })
  assert.equal(again.alreadyReady, true)
})

// A package whose dist-info is present but whose module is damaged is exactly
// the case plain `pip install -r` cannot repair: pip answers "Requirement
// already satisfied" and the verify step keeps failing. The repair chain must
// issue a real reinstall, and the same damage/repair loop must work repeatedly.
test('a damaged installed dependency is really reinstalled twice, not skipped as already satisfied', { skip: skipReason }, async () => {
  const env = createCase()
  copySeedVenv(env.support)
  const wheelPath = buildStubWheel(env.root)
  const requirement = `moodle-study-stubdep @ ${pathToFileURL(wheelPath).href}\n`
  writeFileSync(join(env.program, 'requirements-core.txt'), requirement, 'utf8')
  const python = venvPythonPath(env.support)
  const initial = spawnSync(
    python,
    ['-m', 'pip', 'install', '--no-cache-dir', '--disable-pip-version-check', '-r', join(env.program, 'requirements-core.txt')],
    { encoding: 'utf8', timeout: 180000, windowsHide: true, env: { ...process.env } },
  )
  assert.equal(initial.status, 0, `首次安装本地 wheel 失败：${initial.stderr || initial.error?.message}`)
  writeCoreMarker(env)
  assert.equal(coreRuntimeHealth().ready, true)

  const modulePath = join(pythonPurelib(python), 'moodle_study_stubdep.py')
  assert.ok(existsSync(modulePath), '本地 wheel 没有安装到 venv')

  for (const round of [1, 2]) {
    writeFileSync(modulePath, 'raise RuntimeError("synthetic damaged module")\n', 'utf8')
    const before = coreRuntimeHealth()
    assert.equal(before.status, 'dependencies_missing', `第 ${round} 次破坏后的诊断`)
    assert.equal(before.dependenciesOk, false)
    assert.equal(before.ready, false)
    // The marker still claims a matching fingerprint: only the graded probe can
    // tell that the installed dependency is now unusable.
    assert.equal(before.marker.matches, true)

    const chain = coreRuntimeSteps({ health: before })
    const labels = chain.steps.map((step) => step.label)
    const installStep = chain.steps.find((step) => step.label === '安装学习引擎依赖')
    assert.ok(installStep, `第 ${round} 次修复缺少依赖安装步骤`)
    assert.ok(
      installStep.args.includes('--force-reinstall'),
      `第 ${round} 次修复必须真正重装而不是依赖 already satisfied：${installStep.args.join(' ')}`,
    )
    assert.ok(labels.indexOf('安装学习引擎依赖') < labels.indexOf('验证学习引擎依赖'))
    assert.equal(labels[labels.length - 1], '验证学习引擎依赖')

    const result = runtimeHandler({}, { remote: stubGateway() })
    assert.notEqual(result.alreadyReady, true)
    const done = await waitForJob(result.job.id)
    assert.equal(done.state, 'done', `第 ${round} 次依赖修复任务失败：${done.error}\n${String(done.log).slice(-3000)}`)

    const importCheck = spawnSync(
      python,
      ['-c', 'import moodle_study_stubdep; assert moodle_study_stubdep.VALUE == 42'],
      { encoding: 'utf8', timeout: 60000, windowsHide: true, env: { ...process.env } },
    )
    assert.equal(importCheck.status, 0, `第 ${round} 次修复后模块仍无法导入：${importCheck.stderr || importCheck.error?.message}`)
    const after = coreRuntimeHealth()
    assert.equal(after.status, 'ready', `第 ${round} 次修复后的健康检查`)
    assert.equal(after.ready, true)
    assert.equal(after.marker.matches, true)
  }
})

// Defect B: a rebuilt venv no longer contains Playwright, but runtime.json still
// has `login: {installed: true}` from before the rebuild. Login readiness must
// be graded and the repair chain must really reinstall the sign-in component.
test('a rebuilt venv makes an old login marker stale; the login repair reinstalls and verifies', { skip: skipReason }, async () => {
  const env = createCase()
  copySeedVenv(env.support)
  writeCoreMarker(env)
  // runtime.json still carries the pre-rebuild sign-in flag (installed only,
  // like a marker written by an older plugin version).
  writeJson(join(env.support, 'runtime.json'), {
    core: coreRuntimeRecord(basePython),
    login: { installed: true, at: '2026-11-01T00:00:00.000Z' },
  })

  let health = loginRuntimeHealth()
  assert.equal(health.pythonExecutable, true)
  assert.equal(health.dependenciesOk, false, '重建后的 venv 不含 Playwright')
  assert.equal(health.marker.installed, true)
  assert.equal(health.marker.matches, false, '旧 installed-only 登录标记必须判 stale')
  assert.equal(health.status, 'dependencies_missing')
  assert.equal(health.ready, false)
  assert.equal(loginRuntimeReady(), false)

  const wheelPath = buildStubWheel(env.root)
  writeFileSync(
    join(env.program, 'requirements-login.txt'),
    `moodle-study-stubdep @ ${pathToFileURL(wheelPath).href}\n`,
    'utf8',
  )
  process.env.MOODLE_STUDY_LOGIN_IMPORT_OVERRIDE = 'moodle_study_stubdep'
  try {
    health = loginRuntimeHealth()
    assert.equal(health.status, 'dependencies_missing')
    const chain = loginRuntimeSteps({ health })
    assert.equal(chain.error, null)
    assert.deepEqual(
      chain.steps.map((step) => step.label),
      ['安装学校登录组件', '验证学校登录组件'],
    )
    assert.ok(chain.steps[0].args.includes('--force-reinstall'), '损坏的登录组件修复必须强制重装')
    for (const step of chain.steps) {
      const run = runStep(step)
      assert.equal(run.ok, true, `${step.label} 失败：${run.stderr || run.error?.message}`)
    }

    // Imports are healthy again, but the old marker still has no fingerprint:
    // readiness stays false until the marker is rewritten for the current login
    // dependency requirements (this is what makes connectHandler repair once and
    // then stop repairing on the next call).
    health = loginRuntimeHealth()
    assert.equal(health.dependenciesOk, true)
    assert.equal(health.status, 'marker_stale')
    assert.equal(health.ready, false)
    assert.equal(loginRuntimeReady(), false)

    chain.markRuntime()
    health = loginRuntimeHealth()
    assert.equal(health.status, 'ready')
    assert.equal(health.ready, true)
    assert.equal(loginRuntimeReady(), true)
    const marker = readRuntimeFile(env).login
    const info = loginRequirementsInfo()
    assert.equal(marker.installed, true)
    assert.equal(marker.requirementsVersion, LOGIN_REQUIREMENTS_VERSION)
    assert.equal(marker.requirementsSha256, info.sha256)
  } finally {
    delete process.env.MOODLE_STUDY_LOGIN_IMPORT_OVERRIDE
  }
})

test('a legacy login marker with importable dependencies is stale and refreshed by verify only', { skip: skipReason }, () => {
  const env = createCase()
  copySeedVenv(env.support)
  writeCoreMarker(env)
  const wheelPath = buildStubWheel(env.root)
  writeFileSync(
    join(env.program, 'requirements-login.txt'),
    `moodle-study-stubdep @ ${pathToFileURL(wheelPath).href}\n`,
    'utf8',
  )
  process.env.MOODLE_STUDY_LOGIN_IMPORT_OVERRIDE = 'moodle_study_stubdep'
  try {
    const python = venvPythonPath(env.support)
    const install = spawnSync(
      python,
      ['-m', 'pip', 'install', '--no-cache-dir', '--disable-pip-version-check', '-r', join(env.program, 'requirements-login.txt')],
      { encoding: 'utf8', timeout: 180000, windowsHide: true, env: { ...process.env } },
    )
    assert.equal(install.status, 0, `安装登录测试依赖失败：${install.stderr || install.error?.message}`)
    writeJson(join(env.support, 'runtime.json'), {
      core: coreRuntimeRecord(basePython),
      login: { installed: true, at: '2026-11-01T00:00:00.000Z' },
    })

    const health = loginRuntimeHealth()
    assert.equal(health.dependenciesOk, true)
    assert.equal(health.marker.installed, true)
    assert.equal(health.marker.matches, false)
    assert.equal(health.status, 'marker_stale')
    assert.equal(health.ready, false)
    assert.equal(loginRuntimeReady(), false)

    const chain = loginRuntimeSteps({ health })
    assert.equal(chain.error, null)
    assert.deepEqual(
      chain.steps.map((step) => step.label),
      ['验证学校登录组件'],
      '依赖可导入时只需重新验证并刷新记录，不应重复下载',
    )
    const verify = runStep(chain.steps[0])
    assert.equal(verify.ok, true, `验证失败：${verify.stderr}`)
    assert.equal(loginRuntimeReady(), false, '仅验证不会凭旧标记变 ready')
    chain.markRuntime()
    assert.equal(loginRuntimeReady(), true)
  } finally {
    delete process.env.MOODLE_STUDY_LOGIN_IMPORT_OVERRIDE
  }
})

// Defect B: a successful core rebuild leaves the old sign-in marker pointing at
// a Playwright copy that is gone with the old venv, so it must be invalidated.
test('a core rebuild invalidates the old login marker instead of keeping it authoritative', { skip: skipReason }, async () => {
  const env = createCase()
  copySeedVenv(env.support)
  const loginInfo = loginRequirementsInfo()
  writeJson(join(env.support, 'runtime.json'), {
    core: coreRuntimeRecord(basePython),
    login: {
      installed: true,
      requirementsVersion: loginInfo.version,
      requirementsSha256: loginInfo.sha256,
      at: '2026-11-01T00:00:00.000Z',
    },
  })
  writeFileSync(venvPythonPath(env.support), 'not-a-real-executable', 'utf8')

  const before = coreRuntimeHealth()
  assert.equal(before.status, 'interpreter_broken')
  const result = runtimeHandler({}, { remote: stubGateway() })
  const done = await waitForJob(result.job.id)
  assert.equal(done.state, 'done', `重建任务失败：${done.error}\n${String(done.log).slice(-3000)}`)

  const marker = readRuntimeFile(env)
  assert.equal(marker.login.installed, false, '核心重建后必须作废旧登录标记')
  assert.equal(marker.login.invalidatedReason, 'core_rebuilt')
  const loginHealth = loginRuntimeHealth()
  assert.equal(loginHealth.marker.installed, false)
  assert.equal(loginHealth.ready, false)
  assert.equal(loginRuntimeReady(), false)

  const chain = loginRuntimeSteps({ health: loginHealth })
  assert.equal(chain.error, null)
  assert.ok(chain.steps[0].args.includes('--force-reinstall'), '重建后的登录组件安装仍应按修复路径强制重装')
  assert.equal(chain.steps[chain.steps.length - 1].label, '验证学校登录组件')
})


test('a corrupted interpreter is rebuilt safely and the broken environment is kept for diagnosis', { skip: skipReason }, async () => {
  const env = createCase()
  copySeedVenv(env.support)
  writeCoreMarker(env)
  const learningData = join(env.dataDir, 'Study', 'my-notes.txt')
  writeFileSync(learningData, '学习资料不能被删除', 'utf8')
  const credential = join(env.dataDir, '.private', 'moodle-token.dpapi')
  writeFileSync(credential, 'fake-credential-bytes', 'utf8')
  writeFileSync(venvPythonPath(env.support), 'not-a-real-executable', 'utf8')

  const before = coreRuntimeHealth()
  assert.equal(before.pythonExists, true)
  assert.equal(before.pythonExecutable, false)
  assert.equal(before.status, 'interpreter_broken')
  assert.equal(before.ready, false)

  const result = runtimeHandler({}, { remote: stubGateway() })
  assert.equal(result.ok, true)
  assert.notEqual(result.alreadyReady, true)
  const done = await waitForJob(result.job.id)
  assert.equal(done.state, 'done', `重建任务失败：${done.error}\n${String(done.log).slice(-3000)}`)
  const labels = done.steps.map((step) => step.label)
  assert.ok(labels.some((label) => label.includes('保留') && label.includes('诊断')), '缺少保留诊断目录步骤')
  assert.ok(labels.includes('创建本地 Python 环境'))

  const brokenDirs = readdirSync(env.support).filter((name) => name.startsWith('venv.broken-'))
  assert.equal(brokenDirs.length, 1, '损坏环境应保留一个诊断目录')
  const brokenPython = process.platform === 'win32'
    ? join(env.support, brokenDirs[0], 'Scripts', 'python.exe')
    : join(env.support, brokenDirs[0], 'bin', 'python')
  assert.equal(readFileSync(brokenPython, 'utf8'), 'not-a-real-executable')
  assert.equal(readFileSync(learningData, 'utf8'), '学习资料不能被删除')
  assert.equal(readFileSync(credential, 'utf8'), 'fake-credential-bytes')

  const health = coreRuntimeHealth()
  assert.equal(health.status, 'ready')
  assert.equal(health.pythonExecutable, true)
})

test('stale or legacy markers are not accepted as ready and are refreshed after verification', { skip: skipReason }, async () => {
  const env = createCase()
  copySeedVenv(env.support)
  // Marker written by an older plugin version: installed flag only, no
  // dependency fingerprint.
  writeJson(join(env.support, 'runtime.json'), {
    core: { installed: true, at: '2026-11-01T00:00:00.000Z' },
  })
  let health = coreRuntimeHealth()
  assert.equal(health.ready, false)
  assert.equal(health.status, 'marker_stale')
  assert.equal(health.marker.matches, false)

  let result = runtimeHandler({}, { remote: stubGateway() })
  assert.notEqual(result.alreadyReady, true)
  let done = await waitForJob(result.job.id)
  assert.equal(done.state, 'done', `刷新记录任务失败：${done.error}`)
  assert.equal(coreRuntimeHealth().ready, true)

  // Requirements change after a successful install: the recorded fingerprint
  // no longer matches, so the old marker must stop being authoritative.
  writeFileSync(join(env.program, 'requirements-core.txt'), '# dependency list changed\n', 'utf8')
  health = coreRuntimeHealth()
  assert.equal(health.ready, false)
  assert.equal(health.status, 'marker_stale')
  assert.equal(health.marker.shaMatches, false)

  result = runtimeHandler({}, { remote: stubGateway() })
  assert.notEqual(result.alreadyReady, true)
  done = await waitForJob(result.job.id)
  assert.equal(done.state, 'done', `重新验证任务失败：${done.error}`)
  health = coreRuntimeHealth()
  assert.equal(health.ready, true)
  assert.equal(readRuntimeFile(env).core.requirementsSha256, coreRequirementsInfo().sha256)
})

test('a failed repair is retried on the next call instead of being reported as done', { skip: skipReason }, async () => {
  // The import override simulates a dependency that cannot become available in
  // this offline test (an install failure), while the rest of the chain is the
  // real production chain.
  const env = createCase({ importOverride: 'moodle_study_missing_dependency' })
  copySeedVenv(env.support)
  writeCoreMarker(env)

  const before = coreRuntimeHealth()
  assert.equal(before.status, 'dependencies_missing')
  assert.equal(before.ready, false)

  const first = runtimeHandler({}, { remote: stubGateway() })
  assert.notEqual(first.alreadyReady, true)
  const failed = await waitForJob(first.job.id)
  assert.equal(failed.state, 'failed', '缺少依赖时验证步骤应失败')
  assert.ok(failed.steps.some((step) => step.state === 'failed'))
  assert.ok(failed.error)
  const afterFailure = coreRuntimeHealth()
  assert.equal(afterFailure.ready, false, '失败的修复不能被标记为已完成')
  // The old marker still claims a matching fingerprint, which is exactly why a
  // marker alone must never be treated as completed: the graded probe disagrees.
  assert.equal(afterFailure.marker.matches, true)
  assert.equal(afterFailure.dependenciesOk, false)

  // The failed attempt is visible with concrete recovery guidance; the tool
  // must not claim the runtime is complete.
  const status = await onboarding.collectStatus({ remote: stubGateway(), verifySchool: false })
  assert.equal(status.capabilities.runtimeReady, false)
  assert.equal(status.local.runtimeStatus, 'dependencies_missing')
  const installStep = status.next_steps.find((step) => step.step === 'install_runtime')
  assert.ok(installStep, '失败后应给出安装/修复下一步')
  assert.equal(installStep.tool, 'study_setup_runtime')
  assert.ok(status.recovery.some((line) => line.includes('MOODLE_STUDY_BASE_PYTHON')))

  const second = runtimeHandler({}, { remote: stubGateway() })
  assert.notEqual(second.alreadyReady, true, '失败后再次调用必须重新尝试修复')
  assert.ok(second.job?.id)
  assert.notEqual(second.job.id, first.job.id, '失败后再次调用应启动新的修复任务')
  assert.ok(second.previous_failure, '应返回上一次失败信息')
  assert.ok(String(second.previous_failure.error ?? '').length > 0)
  assert.ok(String(second.recovery ?? '').includes('MOODLE_STUDY_BASE_PYTHON'))
  const failedAgain = await waitForJob(second.job.id)
  assert.equal(failedAgain.state, 'failed')

  delete process.env.MOODLE_STUDY_CORE_IMPORT_OVERRIDE
})

test('a missing requirements file fails with an explicit error and next step instead of reporting done', { skip: skipReason }, () => {
  const env = createCase()
  copySeedVenv(env.support)
  rmSync(join(env.program, 'requirements-core.txt'))
  const health = coreRuntimeHealth()
  assert.equal(health.status, 'requirements_missing')
  assert.equal(health.ready, false)
  const previousJob = jobs.latestJob('setup-runtime')
  const result = runtimeHandler({}, { remote: stubGateway() })
  assert.equal(result.error, 'CORE_REQUIREMENTS_MISSING')
  assert.ok(result.next_step && result.next_step.length > 0)
  assert.equal(result.alreadyReady, undefined)
  assert.equal(jobs.latestJob('setup-runtime')?.id ?? null, previousJob?.id ?? null, '缺少依赖清单时不应启动任务')
})

test('diagnosis distinguishes an incomplete venv directory from a missing runtime', { skip: skipReason }, () => {
  const env = createCase()
  mkdirSync(join(env.support, 'venv'), { recursive: true })
  const health = coreRuntimeHealth()
  assert.equal(health.venvDirExists, true)
  assert.equal(health.pythonExists, false)
  assert.equal(health.status, 'environment_incomplete')
  assert.equal(health.ready, false)
})
