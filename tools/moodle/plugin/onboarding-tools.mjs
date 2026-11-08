// First-use setup and recovery handlers for the Moodle Study Assistant plugin.
//
// These tools are registered both before setup completes (the MCP server runs
// in a reduced "setup" mode) and in the normal study server, so a broken
// setup always has a visible entry point and no shell command is required.
//
// Nothing here reads or prints credentials: sign-in happens in the student's
// own browser and the API token is stored by the existing Windows DPAPI
// mechanism under <data-dir>/.private/.
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { ReadOnlyClient } from '../core.mjs'
import { decryptWindowsToken } from '../windows-token.mjs'
import { jobSummary, latestJob, runningJob, startJob } from './jobs.mjs'
import {
  coreRuntimeReady,
  coreRuntimeSteps,
  engineEnv,
  loginRuntimeReady,
  loginRuntimeSteps,
  nodeStep,
  pythonStep,
  venvPython,
} from './python-runtime.mjs'
import {
  DEFAULT_SCHOOL_URL,
  defaultDataDir,
  looksLikeExistingProfile,
  normalizeSchoolUrl,
  normalizeTimeZone,
  programDir,
  readConfig,
  readRuntime,
  readSettings,
  readState,
  resolveDataDir,
  runtimePython,
  studyReadiness,
  supportDir,
  tokenPath,
  writeConfig,
  writeSettings,
} from './settings.mjs'

const PENDING_COURSES_FILE = 'pending-courses.json'

function pendingPath() {
  return join(supportDir(), 'pending', PENDING_COURSES_FILE)
}

function writePending(value) {
  const path = pendingPath()
  mkdirSync(join(supportDir(), 'pending'), { recursive: true })
  writeFileSync(path, JSON.stringify(value, null, 2) + '\n', 'utf8')
}

function readPending() {
  try {
    return JSON.parse(readFileSync(pendingPath(), 'utf8'))
  } catch {
    return null
  }
}

function runSync(step, timeoutMs = 240000) {
  const result = spawnSync(step.command, step.args, {
    cwd: step.cwd,
    env: { ...process.env, ...(step.env ?? {}) },
    encoding: 'utf8',
    timeout: timeoutMs,
    windowsHide: true,
    maxBuffer: 64 * 1024 * 1024,
  })
  return {
    ok: result.status === 0,
    exitCode: result.status,
    stdout: String(result.stdout ?? ''),
    stderr: String(result.stderr ?? ''),
    error: result.error ? String(result.error.message ?? result.error) : null,
  }
}

function lastJsonLine(text) {
  const lines = String(text ?? '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    if (!lines[index].startsWith('{')) continue
    try {
      return JSON.parse(lines[index])
    } catch {
      // Keep looking upward for the final JSON document.
    }
  }
  return null
}

function readableError(result) {
  const stderr = String(result.stderr ?? '').trim()
  if (stderr) return stderr.slice(-1200)
  if (result.error) return result.error
  return `命令失败（退出码 ${result.exitCode}）`
}

// ---------------------------------------------------------------------------
// State inspection
// ---------------------------------------------------------------------------

let schoolCache = { at: 0, value: null }

async function schoolStatus(settings, { force = false } = {}) {
  const now = Date.now()
  if (!force && schoolCache.value && now - schoolCache.at < 20000) return schoolCache.value
  const base = {
    configured: Boolean(readConfig(settings.dataDir)),
    tokenPresent: existsSync(tokenPath(settings.dataDir)),
    connected: false,
    siteName: null,
    userId: null,
    error: null,
  }
  if (!base.configured || !base.tokenPresent) {
    schoolCache = { at: now, value: base }
    return base
  }
  const config = readConfig(settings.dataDir)
  try {
    const credential = readFileSync(tokenPath(settings.dataDir))
    const client = await new ReadOnlyClient(config.moodle_url, decryptWindowsToken(credential)).connect()
    base.connected = true
    base.siteName = client.siteName ?? null
    base.userId = client.userId ?? null
  } catch (error) {
    const message = String(error?.message ?? '')
    base.error = /STUDY_TOKEN_DECRYPT_FAILED|decrypt/i.test(message)
      ? '本机保存的学校授权无法解密（可能换了 Windows 账户）；请重新连接学校。'
      : /network|fetch|timeout|ENOTFOUND|ETIMEDOUT|ECONN/i.test(message)
        ? '学校连接暂时失败，请检查网络后重试。'
        : '学校连接验证失败，请重新连接学校。'
  }
  schoolCache = { at: now, value: base }
  return base
}

function stateSummary(settings) {
  const state = settings.dataDir ? readState(settings.dataDir) : null
  const courses = Array.isArray(state?.courses)
    ? state.courses.map((course) => ({
        id: course.id,
        name: course.name,
        shortname: course.shortname,
        moodle_course_ids: course.moodle_course_ids ?? [],
      }))
    : []
  const authorization = state?.settings?.course_library_authorization ?? null
  const granted = authorization?.courses && typeof authorization.courses === 'object' ? authorization.courses : {}
  const materialCount = Array.isArray(state?.materials) ? state.materials.length : 0
  const hasSnapshot = Boolean(
    settings.dataDir &&
      (existsSync(join(settings.dataDir, 'Study', '_System', 'LibrarySync', 'latest.json')) ||
        existsSync(join(settings.dataDir, 'Study', '_System', 'refresh-latest.json')) ||
        existsSync(join(settings.dataDir, 'Study', '_System', 'Library'))),
  )
  return {
    initialized: Boolean(state),
    courses,
    authorization: {
      mode: granted && Object.keys(granted).length ? granted[Object.keys(granted)[0]].mode ?? null : null,
      authorizedCourses: Object.keys(granted).length,
    },
    materialCount,
    hasSnapshot,
  }
}

function nextSteps({ settings, runtime, school, state }) {
  const steps = []
  if (!settings.dataDir) {
    steps.push({
      step: 'choose_location',
      title: '选择资料保存位置',
      tool: 'study_setup_location',
      hint: `默认建议：${defaultDataDir()}。学生也可以指定其他文件夹；旧用户可以选择原来的项目文件夹以继续使用已有资料。`,
    })
    return steps
  }
  if (!runtime.coreReady) {
    steps.push({
      step: 'install_runtime',
      title: '安装学习引擎运行依赖',
      tool: 'study_setup_runtime',
      hint: '首次安装需要联网下载约 20MB，请调用后用 study_job_status 查看进度。',
    })
    return steps
  }
  if (!school.tokenPresent) {
    steps.push({
      step: 'connect_school',
      title: '连接学校账号',
      tool: 'study_connect_school',
      hint: '调用后学校网页会在浏览器中打开，由学生本人完成登录（含学校的验证码/双重验证）。登录组件缺失时会先自动安装。',
    })
    return steps
  }
  if (!school.connected) {
    steps.push({
      step: 'reconnect_school',
      title: '学校连接需要重新确认',
      tool: 'study_connect_school',
      hint: school.error ?? '请重新连接学校账号后再继续。',
    })
    return steps
  }
  if (!state.courses.length) {
    steps.push({
      step: 'list_courses',
      title: '列出课程并让学生选择',
      tool: 'study_setup_courses',
      hint: '列出后让学生按课程名称选择本学期要跟踪的课程。',
    })
    steps.push({
      step: 'save_courses',
      title: '保存课程选择与资料范围',
      tool: 'study_setup_select_courses',
      hint: '学生确认课程后，再让学生选择“完整资料同步”或“只同步课程信息”。',
    })
    return steps
  }
  if (!state.hasSnapshot && !state.materialCount) {
    steps.push({
      step: 'sync',
      title: '首次同步学校信息与资料',
      tool: 'study_setup_sync',
      hint: '需要先调用 moodle_study_chat 取得 chat_id，再把 chat_id 传给 study_setup_sync。',
    })
  }
  steps.push({
    step: 'finish',
    title: '完成设置',
    tool: 'study_setup_finish',
    hint: '完成设置后工具会自动重新连接，即可开始学习。',
  })
  return steps
}

export async function statusPayload({ verifySchool = true } = {}) {
  const settings = readSettings() ?? {}
  const runtime = readRuntime()
  const coreReady = coreRuntimeReady()
  const loginReady = loginRuntimeReady()
  const activeJob = runningJob()
  const lastJobs = ['setup-runtime', 'setup-login', 'setup-sync']
    .map((name) => latestJob(name))
    .filter(Boolean)
    .map((job) => jobSummary(job))
  let school = { configured: false, tokenPresent: false, connected: false, error: null }
  if (settings.dataDir && verifySchool) {
    school = await schoolStatus(settings, { force: false })
  } else if (settings.dataDir) {
    school = {
      configured: Boolean(readConfig(settings.dataDir)),
      tokenPresent: existsSync(tokenPath(settings.dataDir)),
      connected: false,
      error: null,
    }
  }
  const state = stateSummary(settings)
  const steps = nextSteps({ settings, runtime: { coreReady }, school, state })
  return {
    pluginVersion: process.env.MOODLE_STUDY_PLUGIN_VERSION ?? null,
    supportDir: supportDir(),
    dataDirDefault: defaultDataDir(),
    settings: {
      dataDir: settings.dataDir ?? null,
      schoolUrl: settings.schoolUrl ?? null,
      timezone: settings.timezone ?? null,
      stage: settings.stage ?? 'new',
      syncedAt: settings.syncedAt ?? null,
      onboardedAt: settings.onboardedAt ?? null,
    },
    dataDir: settings.dataDir ?? null,
    runtime: {
      python: runtimePython(),
      coreReady,
      loginReady,
      basePython: runtime.core?.basePython ?? null,
    },
    school,
    courses: state.courses,
    authorization: state.authorization,
    materialCount: state.materialCount,
    hasSnapshot: state.hasSnapshot,
    next_steps: steps,
    job: activeJob ? jobSummary(activeJob) : null,
    recent_jobs: lastJobs.filter((job) => job.state === 'failed').slice(-2),
    recovery: [
      '资料位置、学校登录和运行依赖都可以在这里重新设置，不会删除已有学习资料。',
      '如果工具一直没有出现，请完全退出并重新打开 DeepSeek Harness（插件会自动重新连接）。',
      '运行依赖安装失败时，确认网络可用后再次调用 study_setup_runtime。',
    ],
  }
}

// ---------------------------------------------------------------------------
// Setup actions
// ---------------------------------------------------------------------------

function initSteps(settings) {
  return [
    nodeStep('初始化学习档案', 'tools/moodle/run.mjs', ['init'], settings),
    pythonStep('初始化复习状态', 'tools/study/review.py', ['init'], settings),
    pythonStep('生成今日学习视图', 'tools/study/daily.py', ['run'], settings),
    pythonStep('建立本地资料索引', 'tools/study/library.py', ['sync'], settings),
  ]
}

function ensureInitializedSync(settings) {
  if (readState(settings.dataDir)) return { ok: true, steps: [] }
  const results = []
  for (const step of initSteps(settings)) {
    const result = runSync(step)
    results.push({ label: step.label, ok: result.ok, error: result.ok ? null : readableError(result) })
    if (!result.ok) return { ok: false, steps: results, error: result.error ?? '初始化失败' }
  }
  return { ok: true, steps: results }
}

function engineNode(args, settings) {
  return nodeStep('学校只读接口', 'tools/moodle/run.mjs', args, settings)
}

export function locationHandler(args) {
  const settings = readSettings() ?? {}
  const dataDir = resolveDataDir(args.data_dir ?? settings.dataDir ?? defaultDataDir())
  const existing = looksLikeExistingProfile(dataDir)
  const existingConfig = readConfig(dataDir)
  const requestedUrl = args.moodle_url ? normalizeSchoolUrl(args.moodle_url) : null
  if (existingConfig && requestedUrl && normalizeSchoolUrl(existingConfig.moodle_url) !== requestedUrl) {
    return {
      error: 'SCHOOL_CONFLICT',
      message:
        '这个文件夹里已有另一所学校的连接配置；一个资料目录只能对应一所学校的一个账号。请选择新的空文件夹，或继续使用原来的学校地址。',
      currentSchoolUrl: existingConfig.moodle_url,
    }
  }
  mkdirSync(dataDir, { recursive: true })
  for (const dir of ['Study/_System', 'Study/_Inbox', '.private']) mkdirSync(join(dataDir, ...dir.split('/')), { recursive: true })
  let config = existingConfig
  if (!config) {
    config = {
      moodle_url: requestedUrl ?? DEFAULT_SCHOOL_URL,
      timezone: normalizeTimeZone(settings.timezone),
    }
    writeConfig(dataDir, config)
  }
  const next = writeSettings({
    dataDir,
    schoolUrl: config.moodle_url,
    timezone: config.timezone ?? normalizeTimeZone(settings.timezone),
    stage: existing ? 'adopted' : 'located',
  })
  return {
    ok: true,
    dataDir,
    schoolUrl: next.schoolUrl,
    adoptedExistingProfile: existing,
    message: existing
      ? '已使用这个文件夹里的现有学习资料；不会覆盖或重建已有记录。'
      : '资料保存位置已设置。下一步安装学习引擎运行依赖。',
    next_step: coreRuntimeReady() ? 'connect_school' : 'install_runtime',
  }
}

export function runtimeHandler() {
  const settings = readSettings()
  if (!settings?.dataDir) {
    return { error: 'LOCATION_REQUIRED', message: '请先调用 study_setup_location 选择资料保存位置。' }
  }
  if (coreRuntimeReady()) {
    return { ok: true, alreadyReady: true, message: '运行依赖已安装。', next_step: 'connect_school' }
  }
  const chain = coreRuntimeSteps()
  if (chain.error) return { error: 'PYTHON_NOT_FOUND', message: chain.error }
  const job = startJob({
    name: 'setup-runtime',
    steps: chain.steps,
    logPath: join(supportDir(), 'logs', 'setup-runtime.log'),
    onSuccess: () => {
      chain.markRuntime()
      writeSettings({ stage: 'runtime_ready' })
    },
    onFailure: (message) => writeSettings({ lastError: message }),
  })
  return {
    ok: true,
    job: jobSummary(job),
    message:
      '正在安装学习引擎运行依赖（首次约 20MB 下载，通常 1-3 分钟）。请调用 study_job_status 查看进度；完成后继续连接学校。',
  }
}

export function connectHandler(args, { mode } = {}) {
  const settings = readSettings()
  if (!settings?.dataDir) {
    return { error: 'LOCATION_REQUIRED', message: '请先调用 study_setup_location 选择资料保存位置。' }
  }
  if (!coreRuntimeReady()) {
    return { error: 'RUNTIME_REQUIRED', message: '请先调用 study_setup_runtime 安装运行依赖。' }
  }
  const steps = []
  if (!loginRuntimeReady()) {
    const login = loginRuntimeSteps()
    if (login.error) return { error: 'LOGIN_RUNTIME_FAILED', message: login.error }
    steps.push(...login.steps)
  }
  steps.push(...initSteps(settings))
  steps.push({
    label: '学校网页登录',
    command: venvPython() ?? 'python',
    args: [join(programDir(), 'tools', 'moodle', 'auth.py'), '--browser', args.browser ?? 'msedge'],
    cwd: programDir(),
    env: engineEnv(settings),
  })
  const job = startJob({
    name: 'setup-login',
    steps,
    logPath: join(supportDir(), 'logs', 'setup-login.log'),
    onSuccess: () => {
      loginRuntimeSteps().markRuntime?.()
      writeSettings({ stage: 'connected' })
      schoolCache = { at: 0, value: null }
    },
    onFailure: (message) => writeSettings({ lastError: message }),
  })
  return {
    ok: true,
    job: jobSummary(job),
    message:
      '正在准备学校登录：如果日志显示“请在弹出的学校网页完成登录”，请让学生在本机弹出的浏览器窗口里完成学校登录（可含验证码/双重验证）。登录完成后调用 study_setup_status 查看结果。工具不会读取或保存学生密码。',
    next_step: mode === 'study' ? 'relogin_then_finish' : 'list_courses',
  }
}

export function coursesHandler() {
  const settings = readSettings()
  if (!settings?.dataDir) return { error: 'LOCATION_REQUIRED', message: '请先完成资料位置设置。' }
  if (!existsSync(tokenPath(settings.dataDir))) {
    return { error: 'SCHOOL_REQUIRED', message: '请先连接学校账号。' }
  }
  const init = ensureInitializedSync(settings)
  if (!init.ok) {
    return { error: 'INIT_FAILED', message: '学习档案初始化失败，请重试或查看运行日志。', detail: init }
  }
  const result = runSync(engineNode(['courses', '--list'], settings))
  const payload = result.ok ? lastJsonLine(result.stdout) : null
  if (!payload) {
    return {
      error: 'COURSE_LIST_FAILED',
      message: '课程列表读取失败；请确认学校连接可用后重试。',
      detail: readableError(result),
    }
  }
  writePending({
    account_scope: payload.account_scope,
    state_fingerprint: payload.state_fingerprint,
    listed_at: new Date().toISOString(),
    course_ids: payload.courses.map((course) => course.id),
  })
  return {
    ok: true,
    instruction:
      '请把下面的课程名称告诉学生，让学生本人选择本学期要跟踪的课程（可以多门），并确认资料范围：“完整资料同步”会下载所选课程的资料，“只同步课程信息”不下载。学生明确选择后再调用 study_setup_select_courses。',
    courses: payload.courses,
    modes: payload.modes,
    scope: payload.scope,
    selected: payload.courses.filter((course) => course.selected).map((course) => course.id),
  }
}

export function selectCoursesHandler(args) {
  const settings = readSettings()
  if (!settings?.dataDir) return { error: 'LOCATION_REQUIRED', message: '请先完成资料位置设置。' }
  const pending = readPending()
  if (!pending) {
    return { error: 'COURSE_LIST_REQUIRED', message: '请先调用 study_setup_courses 列出课程。' }
  }
  const unknown = args.course_ids.filter((id) => !pending.course_ids.includes(id))
  if (unknown.length) {
    return {
      error: 'UNKNOWN_COURSE',
      message: '选择的课程不在刚才的列表中，请重新列出课程让学生确认。',
      unknown,
    }
  }
  const file = join(supportDir(), 'pending', 'course-selection.json')
  writeFileSync(
    file,
    JSON.stringify(
      {
        version: 1,
        course_ids: [...new Set(args.course_ids)],
        mode: args.mode,
        account_scope: pending.account_scope,
        state_fingerprint: pending.state_fingerprint,
      },
      null,
      2,
    ),
    'utf8',
  )
  const result = runSync(engineNode(['courses', '--select-file', file], settings))
  const payload = result.ok ? lastJsonLine(result.stdout) : null
  if (!payload || payload.status !== 'saved') {
    return {
      error: 'COURSE_SELECT_FAILED',
      message: '课程选择保存失败；如果提示学习状态已变化，请重新列出课程再让学生确认。',
      detail: payload ?? readableError(result),
    }
  }
  writeSettings({
    stage: 'courses_selected',
    courses: { mode: args.mode, ids: payload.course_ids, savedAt: new Date().toISOString() },
  })
  return {
    ok: true,
    mode: payload.mode,
    course_ids: payload.course_ids,
    message:
      args.mode === 'full'
        ? '课程与完整资料授权已保存。下一步同步学校信息和资料（首次可能较慢）。'
        : '课程与“只同步课程信息”授权已保存，不会下载资料。下一步同步学校信息。',
    next_step: 'sync',
  }
}

export function syncHandler(args) {
  const settings = readSettings()
  if (!settings?.dataDir) return { error: 'LOCATION_REQUIRED', message: '请先完成资料位置设置。' }
  if (!existsSync(tokenPath(settings.dataDir))) {
    return { error: 'SCHOOL_REQUIRED', message: '请先连接学校账号。' }
  }
  const chatId = String(args.chat_id ?? '').trim()
  if (!/^(session-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(chatId)) {
    return {
      error: 'CHAT_ID_REQUIRED',
      message: '需要当前聊天标识才能开始首次同步：请先调用 moodle_study_chat，把它返回的 chat_id 原样传入。',
    }
  }
  const running = runningJob('setup-sync')
  if (running) return { ok: true, job: jobSummary(running), message: '同步已在进行中，请继续用 study_job_status 查看进度。' }
  const steps = [nodeStep('采集学校信息', 'tools/moodle/run.mjs', ['refresh', '--chat', chatId], settings)]
  const job = startJob({
    name: 'setup-sync',
    steps,
    logPath: join(supportDir(), 'logs', 'setup-sync.log'),
    onSuccess: () => writeSettings({ stage: 'synced', syncedAt: new Date().toISOString() }),
    onFailure: (message) => writeSettings({ lastError: message }),
  })
  return {
    ok: true,
    job: jobSummary(job),
    message:
      '正在采集学校信息并同步资料（完整授权时会下载、归档并建立索引，可能几分钟到十几分钟）。请用 study_job_status 查看进度；资料下载成功不等于已核读。',
  }
}

export async function finishHandler() {
  const settings = readSettings()
  if (!settings?.dataDir) return { error: 'LOCATION_REQUIRED', message: '请先完成资料位置设置。' }
  const readiness = studyReadiness(settings)
  if (!readiness.ready) {
    return {
      error: 'SETUP_INCOMPLETE',
      message: '设置尚未完成，请按 next_steps 继续。',
      reason: readiness.reason,
    }
  }
  const state = stateSummary(settings)
  if (!state.courses.length) {
    return { error: 'COURSES_REQUIRED', message: '还没有保存课程选择与资料授权。' }
  }
  const job = runningJob()
  if (job) {
    return { error: 'JOB_RUNNING', message: `还有设置任务在运行（${job.name}），请等待完成后再结束设置。` }
  }
  writeSettings({ stage: 'ready', onboardedAt: new Date().toISOString() })
  setTimeout(() => process.exit(0), 1500)
  return {
    ok: true,
    ready: true,
    dataDir: settings.dataDir,
    message:
      '设置完成。Moodle 工具正在重新连接（约几秒），连接后就可以开始在聊天里学习。学生资料保存在选择的文件夹，插件升级或卸载不会删除它。',
    next_step: 'restart_reconnect',
  }
}

export { nextSteps }
