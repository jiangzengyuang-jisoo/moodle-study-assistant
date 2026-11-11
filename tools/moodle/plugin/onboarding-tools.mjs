// First-use, maintenance and recovery handlers for the Moodle Study Assistant.
//
// The same handlers are registered in every MCP mode. Local study tools never
// depend on a live school connection, setup steps can resume after any
// interruption, and completion is derived from real sync evidence rather than
// from files that merely exist.
//
// Nothing here reads or prints credentials: sign-in happens in the student's
// own browser and the API token is stored by the existing Windows DPAPI
// mechanism under <data-dir>/.private/.
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { courseCatalog, selectCoursesInChat } from '../chat-course-selection.mjs'
import { saveState } from '../state-bridge.mjs'
import { jobSummary, latestJob, runningJob, startJob } from './jobs.mjs'
import {
  coreRuntimeHealth,
  coreRuntimeSteps,
  engineEnv,
  loginHealthSummary,
  loginRuntimeHealth,
  loginRuntimeSteps,
  nodeStep,
  pythonStep,
  runtimeHealthSummary,
  runtimeRepairAdvice,
  venvPython,
} from './python-runtime.mjs'
import { RemoteError, createRemoteGateway } from './remote.mjs'
import {
  DEFAULT_SCHOOL_URL,
  defaultDataDir,
  localCapabilities,
  looksLikeExistingProfile,
  normalizeSchoolUrl,
  normalizeTimeZone,
  programDir,
  readConfig,
  readState,
  readSettings,
  resolveDataDir,
  runtimePython,
  supportDir,
  tokenPath,
  writeConfig,
  writeSettings,
} from './settings.mjs'
import { evaluateSyncEvidence, selectedCoursesFromState, syncSummaryText } from './sync-evidence.mjs'

const PENDING_COURSES_FILE = 'pending-courses.json'
const LOCAL_STUDY_TOOLS = [
  'study_local_context',
  'study_chapter_prep',
  'study_material_read',
  'study_material_render',
  'study_teaching_event',
  'study_local_status',
]

let defaultGateway = null
function gatewayOf(context) {
  if (context?.remote) return context.remote
  if (!defaultGateway) defaultGateway = createRemoteGateway()
  return defaultGateway
}

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

export function remoteFailure(error) {
  if (error instanceof RemoteError) {
    return {
      error: error.code,
      kind: error.kind,
      message: error.message,
      next_step: error.next_step,
      retryable: error.retryable,
      requires_login: error.requiresLogin,
    }
  }
  const message = String(error?.message ?? '')
  if (/STUDY_TOKEN_DECRYPT_FAILED|STUDY_AUTH/.test(message)) {
    return {
      error: 'SCHOOL_AUTH_REQUIRED',
      kind: 'auth',
      message: '学校登录需要重新确认。已下载的本地资料仍可学习；需要联网时请让学生本人重新连接学校。',
      next_step: 'study_connect_school',
      retryable: false,
      requires_login: true,
    }
  }
  if (/PROFILE_ACCOUNT_GUARD|同一个学习档案|学习档案已有课程|学习档案中已存在/.test(message)) {
    return {
      error: 'PROFILE_ACCOUNT_GUARD',
      kind: 'account',
      message: message.slice(0, 400),
      next_step: 'study_setup_status',
      retryable: false,
      requires_login: false,
    }
  }
  if (/学习状态已变化|课程 ID 不在|课程不存在|没有找到课程/.test(message)) {
    return {
      error: 'COURSE_LIST_STALE',
      kind: 'state',
      message: '课程列表或学习档案已变化，请重新列出课程后让学生再确认一次。',
      next_step: 'study_setup_courses',
      retryable: true,
      requires_login: false,
    }
  }
  if (/学校拒绝了|没有可用的完整资料授权|authorization/.test(message)) {
    return {
      error: 'AUTHORIZATION_REQUIRED',
      kind: 'access',
      message: '当前账号或课程资料授权不满足这次操作；请核对课程与资料范围，不要绕过学校限制。',
      next_step: 'study_setup_status',
      retryable: false,
      requires_login: false,
    }
  }
  return {
    error: 'SCHOOL_UNAVAILABLE',
    kind: 'network',
    message: '学校暂时连接不上；已下载的本地资料仍可学习，网络恢复后调用 study_reconnect 再试。',
    next_step: 'study_reconnect',
    retryable: true,
    requires_login: false,
  }
}

// ---------------------------------------------------------------------------
// Capability collection and next steps
// ---------------------------------------------------------------------------

function buildCapabilities({ settings, local, school, state, evidence }) {
  const selected = selectedCoursesFromState(state)
  const evidenceCourses = evidence?.courses ?? []
  const authorizationComplete =
    selected.length > 0 &&
    evidenceCourses.length === selected.length &&
    evidenceCourses.every((course) => course.status !== 'authorization_missing' && course.status !== 'authorization_invalid')
  return {
    locationSet: Boolean(local.dataDir && local.dataDirExists),
    configReady: Boolean(local.configPresent),
    runtimeReady: Boolean(local.runtimeReady),
    runtimeStatus: local.runtimeStatus ?? 'unknown',
    runtimeGuidance: runtimeRepairAdvice(local.runtimeStatus ?? 'unknown'),
    tokenPresent: Boolean(local.tokenPresent),
    schoolConnected: school.state === 'connected',
    schoolNetworkIssue: school.state === 'network',
    schoolAuthRequired: Boolean(school.requiresLogin),
    coursesSelected: selected.length > 0,
    authorizationComplete,
    syncReady: Boolean(evidence?.ready),
    setupComplete: settings?.stage === 'ready',
    localStudyAvailable: Boolean(local.studyToolsUsable),
    canFinish: Boolean(local.configPresent && local.runtimeReady && local.tokenPresent && selected.length && evidence?.ready),
    dataDir: local.dataDir,
  }
}

// Pure next-step computation: every referenced tool is always registered by
// the MCP server, so a restart at any setup stage can continue correctly.
export function nextSteps(capabilities, evidence = null) {
  const steps = []
  if (!capabilities.locationSet) {
    steps.push({
      step: 'choose_location',
      title: '选择资料保存位置',
      tool: 'study_setup_location',
      hint: `默认建议：${defaultDataDir()}。学生也可以指定其他文件夹；旧用户可以选择原来的项目文件夹以继续使用已有资料。`,
    })
    return steps
  }
  if (!capabilities.configReady) {
    steps.push({
      step: 'choose_location',
      title: '补全资料目录的学校配置',
      tool: 'study_setup_location',
      hint: '选择原来的资料文件夹后再次确认即可补全学校连接配置；已有资料不会被覆盖。',
    })
    return steps
  }
  if (!capabilities.runtimeReady) {
    const guidance = capabilities.runtimeGuidance ?? runtimeRepairAdvice(capabilities.runtimeStatus)
    steps.push({
      step: 'install_runtime',
      title: '安装/修复学习引擎运行依赖',
      tool: 'study_setup_runtime',
      hint: guidance.hint,
      recovery: guidance.recovery,
    })
    return steps
  }
  if (!capabilities.tokenPresent) {
    steps.push({
      step: 'connect_school',
      title: '连接学校账号',
      tool: 'study_connect_school',
      hint: '学校网页会在浏览器中打开，由学生本人完成登录（可含验证码/双重验证）。',
    })
    return steps
  }
  if (capabilities.schoolAuthRequired) {
    steps.push({
      step: 'connect_school',
      title: '重新连接学校账号',
      tool: 'study_connect_school',
      hint: '已保存的学校登录失效或无法解密，需要学生本人重新登录。已下载的本地资料仍可学习。',
    })
    return steps
  }
  if (capabilities.schoolNetworkIssue && !capabilities.coursesSelected) {
    steps.push({
      step: 'reconnect_school',
      title: '恢复学校连接',
      tool: 'study_reconnect',
      hint: '学校暂时连接不上；恢复网络后重试即可，不需要重新登录。已下载的本地资料仍可学习。',
    })
    return steps
  }
  if (!capabilities.coursesSelected) {
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
  if (!capabilities.authorizationComplete) {
    steps.push({
      step: 'save_courses',
      title: '确认课程资料范围',
      tool: 'study_setup_select_courses',
      hint: '所选课程缺少当前账号的资料范围授权；请重新列出课程并让学生确认“完整同步”或“只同步信息”。',
    })
    return steps
  }
  if (!capabilities.syncReady) {
    steps.push({
      step: 'sync',
      title: '完成首次学校信息与资料同步',
      tool: 'study_setup_sync',
      hint: '需要先调用 moodle_study_chat 取得 chat_id，再传给 study_setup_sync；中断或部分失败后重跑会增量补齐已成功的内容。',
    })
    if (evidence) steps.push({ step: 'sync_detail', title: '当前同步范围', tool: 'study_setup_status', hint: syncSummaryText(evidence) })
    return steps
  }
  if (!capabilities.setupComplete) {
    steps.push({
      step: 'finish',
      title: '完成设置',
      tool: 'study_setup_finish',
      hint: '同步证据已匹配当前账号和课程授权，可以完成设置并开始学习。',
    })
    return steps
  }
  return steps
}

export async function collectStatus({ remote = gatewayOf(null), verifySchool = true } = {}) {
  const settings = readSettings() ?? {}
  const runtimeHealth = coreRuntimeHealth()
  // Sign-in readiness is graded too: after a core rebuild Playwright is gone
  // even though an old `login.installed` flag may still be on disk.
  const loginHealth = loginRuntimeHealth()
  const local = localCapabilities(settings, runtimeHealth, loginHealth)
  const state = local.dataDirExists ? readState(local.dataDir) : null
  const activeJob = runningJob()
  const failedJobs = ['setup-runtime', 'setup-login', 'setup-sync']
    .map((name) => latestJob(name))
    .filter(Boolean)
    .filter((job) => job.state === 'failed')
    .map((job) => jobSummary(job))
  const runtimeAdvice = runtimeRepairAdvice(runtimeHealth)

  let school
  if (!local.dataDirExists || !local.configPresent) {
    school = { state: 'setup', connected: false, message: '还没有完成资料位置或学校配置。', requiresLogin: false, retryable: false, next_step: 'study_setup_location' }
  } else if (verifySchool) {
    school = await remote.probe()
  } else {
    school = remote.status()
  }

  const connectedAccount =
    school.state === 'connected' && school.baseUrl && school.userId
      ? { site: school.baseUrl, user_id: school.userId }
      : null
  const profileAccount = state?.settings?.profile_account_scope ?? null
  const evidence = local.dataDirExists
    ? evaluateSyncEvidence({ dataDir: local.dataDir, state, connectedAccount, profileAccount })
    : null
  const capabilities = buildCapabilities({ settings, local, school, state, evidence })
  const steps = nextSteps(capabilities, evidence)
  return {
    pluginVersion: process.env.MOODLE_STUDY_PLUGIN_VERSION ?? null,
    supportDir: supportDir(),
    settings: {
      dataDir: settings.dataDir ?? null,
      schoolUrl: settings.schoolUrl ?? null,
      timezone: settings.timezone ?? null,
      stage: settings.stage ?? 'new',
      onboardingAt: settings.onboardedAt ?? null,
    },
    local: {
      dataDir: local.dataDir,
      dataDirExists: local.dataDirExists,
      dataDirDefault: local.dataDirDefault,
      configPresent: local.configPresent,
      pythonPresent: local.pythonPresent,
      pythonExecutable: local.pythonExecutable,
      pipAvailable: local.pipAvailable,
      dependenciesOk: local.dependenciesOk,
      coreInstalled: local.coreInstalled,
      runtimeStatus: local.runtimeStatus,
      runtimeHealth: local.runtimeHealth,
      runtimeReady: local.runtimeReady,
      loginRuntimeStatus: local.loginRuntimeStatus,
      loginRuntimeHealth: local.loginRuntimeHealth,
      loginRuntimeReady: local.loginRuntimeReady,
      python: runtimePython(),
      tokenPresent: local.tokenPresent,
      stateInitialized: local.stateInitialized,
      studyToolsUsable: local.studyToolsUsable,
    },
    school,
    courses: state?.courses ?? [],
    authorization: {
      mode: state?.settings?.course_library_authorization?.courses
        ? selectedCoursesFromState(state).map((course) => course.mode).filter(Boolean)[0] ?? null
        : null,
      authorizedCourses: Object.keys(state?.settings?.course_library_authorization?.courses ?? {}).length,
    },
    sync: evidence
      ? {
          ready: evidence.ready,
          mode: evidence.mode,
          summary: evidence.summary,
          blockers: evidence.blockers,
          courses: evidence.courses.map((course) => ({
            id: course.id,
            name: course.name,
            mode: course.mode,
            status: course.status,
            issues: course.issues,
            available: course.available,
          })),
          evidence: { snapshot: evidence.snapshot, report: evidence.report },
          checkedAt: evidence.checkedAt,
          text: syncSummaryText(evidence),
        }
      : null,
    capabilities,
    local_study: {
      available: Boolean(local.studyToolsUsable),
      stateInitialized: Boolean(state),
      tools: LOCAL_STUDY_TOOLS,
      note: '学校暂时连不上时，本地资料读取、章节准备、原页渲染和教学记录仍可使用；但采集/核对不是最新线上结果。',
    },
    next_steps: steps,
    job: activeJob ? jobSummary(activeJob) : null,
    recent_failed_jobs: failedJobs.slice(-2),
    recovery: [
      '任何一步中断后重新调用 study_setup_status，会从当前进度继续，不会删除已有资料。',
      // Only add the long runtime guidance once setup actually progressed past
      // choosing the data location; a brand-new chat should see the next step,
      // not a repair lecture.
      ...(local.runtimeReady || !local.configPresent ? [] : [runtimeAdvice.recovery]),
      '学校网络故障用 study_reconnect 重试；只有登录失效或凭据无法解密才需要学生本人重新连接学校。',
      '资料同步中断或部分失败时重跑 study_setup_sync 会增量补齐，不会重复下载已成功内容。',
    ],
  }
}

export async function statusPayload(args = {}, context = {}) {
  return collectStatus({ remote: gatewayOf(context), verifySchool: true })
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

function scheduleReconnect(context, reason) {
  if (typeof context?.requestReconnect !== 'function') return false
  context.requestReconnect(reason)
  return true
}

export function locationHandler(args, context = {}) {
  const settings = readSettings() ?? {}
  const previous = settings.dataDir ?? null
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
  for (const dir of ['Study/_System', 'Study/_Inbox', '.private']) {
    mkdirSync(join(dataDir, ...dir.split('/')), { recursive: true })
  }
  let config = existingConfig
  if (!config) {
    config = {
      moodle_url: requestedUrl ?? DEFAULT_SCHOOL_URL,
      timezone: normalizeTimeZone(settings.timezone),
    }
    writeConfig(dataDir, config)
  }
  const changed = previous !== dataDir
  writeSettings({
    dataDir,
    schoolUrl: config.moodle_url,
    timezone: config.timezone ?? normalizeTimeZone(settings.timezone),
    stage: !changed && settings.stage === 'ready' ? 'ready' : existing ? 'adopted' : 'located',
    syncEpoch: previous === dataDir ? settings.syncEpoch ?? null : null,
  })
  const reconnecting = changed ? scheduleReconnect(context, 'data-location-changed') : false
  const gateway = gatewayOf(context)
  if (changed) gateway.invalidate()
  const state = readState(dataDir)
  const evidence = evaluateSyncEvidence({ dataDir, state, profileAccount: state?.settings?.profile_account_scope ?? null })
  const school = gateway.status()
  const currentSettings = readSettings()
  const runtimeHealth = coreRuntimeHealth()
  const capabilities = buildCapabilities({
    settings: currentSettings,
    local: localCapabilities(currentSettings, runtimeHealth),
    school,
    state,
    evidence,
  })
  const steps = nextSteps(capabilities, evidence)
  return {
    ok: true,
    dataDir,
    schoolUrl: config.moodle_url,
    adoptedExistingProfile: existing,
    changed,
    reconnecting,
    message: changed
      ? '已切换到新的资料位置并保留原目录的全部资料。工具会自动重新连接；新位置不会沿用旧文件夹的登录凭据，需要联网时请学生本人重新连接学校。'
      : existing
        ? '已使用这个文件夹里的现有学习资料；不会覆盖或重建已有记录。'
        : '资料保存位置已设置。',
    next_steps: steps,
  }
}

// Setup stages that already depend on a working runtime: repairing the runtime
// must never downgrade them (a previously complete setup stays complete).
const POST_RUNTIME_STAGES = new Set(['connected', 'courses_selected', 'synced', 'ready'])

function latestRuntimeFailure() {
  const job = latestJob('setup-runtime')
  if (!job || job.state !== 'failed') return null
  const failedStep = job.steps.find((step) => step.state === 'failed') ?? null
  return {
    id: job.id,
    error: job.error,
    failedStep: failedStep?.label ?? null,
    log: String(job.log ?? '').slice(-1500),
    startedAt: job.startedAt,
    finishedAt: job.finishedAt,
  }
}

function capabilitiesWith(settings, health, context) {
  const local = localCapabilities(settings, health)
  return buildCapabilities({
    settings,
    local,
    school: gatewayOf(context).status(),
    state: readState(settings.dataDir),
    evidence: null,
  })
}

export function runtimeHandler(args, context = {}) {
  const settings = readSettings()
  if (!settings?.dataDir) {
    return { error: 'LOCATION_REQUIRED', message: '请先调用 study_setup_location 选择资料保存位置。' }
  }
  // The old marker + existing python.exe is never enough: this graded local
  // probe decides whether the runtime is healthy.
  const health = coreRuntimeHealth()
  const runtime = runtimeHealthSummary(health)
  if (health.ready) {
    return {
      ok: true,
      alreadyReady: true,
      runtime,
      message: '运行依赖已安装，健康检查通过。',
      next_steps: nextSteps(capabilitiesWith(settings, health, context)),
    }
  }
  const chain = coreRuntimeSteps({ health })
  if (chain.error) {
    return {
      error: chain.error.code,
      message: chain.error.message,
      next_step: chain.error.nextStep,
      runtime,
    }
  }
  const previousFailure = latestRuntimeFailure()
  const advice = runtimeRepairAdvice(health)
  const job = startJob({
    name: 'setup-runtime',
    steps: chain.steps,
    logPath: join(supportDir(), 'logs', 'setup-runtime.log'),
    onSuccess: () => {
      chain.markRuntime()
      const current = readSettings() ?? {}
      const stage = POST_RUNTIME_STAGES.has(current.stage) ? current.stage : 'runtime_ready'
      writeSettings({ stage, lastError: null })
    },
    onFailure: (message) => writeSettings({ lastError: `运行依赖修复失败：${message}` }),
  })
  return {
    ok: true,
    repairing: true,
    runtime,
    job: jobSummary(job),
    previous_failure: previousFailure,
    message: `正在修复学习引擎运行依赖（诊断：${runtime?.statusText ?? health.status}）。${advice.hint}`,
    next_step: 'study_job_status',
    recovery: advice.recovery,
  }
}

export function connectHandler(args, context = {}) {
  const settings = readSettings()
  if (!settings?.dataDir) {
    return { error: 'LOCATION_REQUIRED', message: '请先调用 study_setup_location 选择资料保存位置。' }
  }
  const health = coreRuntimeHealth()
  if (!health.ready) {
    const advice = runtimeRepairAdvice(health)
    return {
      error: 'RUNTIME_REQUIRED',
      message: '请先调用 study_setup_runtime 安装/修复运行依赖后再登录学校。',
      next_step: 'study_setup_runtime',
      runtime: runtimeHealthSummary(health),
      recovery: advice.recovery,
    }
  }
  // Sign-in readiness is graded (interpreter runs + playwright.sync_api imports
  // + marker fingerprint), never derived from the bare login.installed flag.
  const loginHealth = loginRuntimeHealth()
  const steps = []
  let loginChain = null
  if (!loginHealth.ready) {
    loginChain = loginRuntimeSteps({ health: loginHealth })
    if (loginChain.error) {
      return {
        error: 'LOGIN_RUNTIME_FAILED',
        message: loginChain.error,
        next_step: 'study_setup_runtime',
        loginRuntime: loginHealthSummary(loginHealth),
      }
    }
    steps.push(...loginChain.steps)
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
      // Only refresh the sign-in marker when this job actually installed or
      // re-verified the component; skipping a ready component keeps its marker.
      loginChain?.markRuntime?.()
      writeSettings({ stage: 'connected', lastError: null })
      gatewayOf(context).invalidate()
      // Restart the MCP server so the new credential generation is loaded by
      // the gateway and the chapter-check context. Tool names stay identical;
      // after reconnecting the student continues exactly where they stopped.
      scheduleReconnect(context, 'school-sign-in')
    },
    onFailure: (message) => writeSettings({ lastError: message }),
  })
  return {
    ok: true,
    job: jobSummary(job),
    loginRuntime: loginHealthSummary(loginHealth),
    message:
      '正在准备学校登录：如果日志显示“请在弹出的学校网页完成登录”，请让学生在本机弹出的浏览器窗口里完成学校登录（可含验证码/双重验证）。登录成功后工具会自动重新连接（约几秒），然后可以继续选课。工具不会读取或保存学生密码。',
    next_step: 'list_courses',
  }
}

export async function coursesHandler(args, context = {}) {
  const settings = readSettings()
  const local = localCapabilities(settings)
  if (!local.dataDir) return { error: 'LOCATION_REQUIRED', message: '请先完成资料位置设置。' }
  if (!local.tokenPresent) return { error: 'SCHOOL_REQUIRED', message: '请先连接学校账号。' }
  const init = ensureInitializedSync(settings)
  if (!init.ok) {
    return { error: 'INIT_FAILED', message: '学习档案初始化失败；可重试或先修复运行依赖。', detail: init }
  }
  const gateway = gatewayOf(context)
  try {
    // Connect first so the account identity used for the catalog request is
    // the real signed-in account, not the saved account fallback.
    await gateway.connect()
    const payload = await courseCatalog({
      client: gateway,
      readState: () => readState(settings.dataDir),
      root: settings.dataDir,
    })
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
  } catch (error) {
    return remoteFailure(error)
  }
}

export async function selectCoursesHandler(args, context = {}) {
  const settings = readSettings()
  const local = localCapabilities(settings)
  if (!local.dataDir) return { error: 'LOCATION_REQUIRED', message: '请先完成资料位置设置。' }
  const pending = readPending()
  if (!pending) {
    return { error: 'COURSE_LIST_REQUIRED', message: '请先调用 study_setup_courses 列出课程。' }
  }
  const unknown = args.course_ids.filter((id) => !pending.course_ids.includes(id))
  if (unknown.length) {
    return { error: 'UNKNOWN_COURSE', message: '选择的课程不在刚才的列表中，请重新列出课程让学生确认。', unknown }
  }
  const gateway = gatewayOf(context)
  // The state writer spawns Python through the shared state bridge, which
  // resolves STUDY_PYTHON or a venv next to the data dir. The runtime may have
  // been installed after this MCP server started, so refresh the env here.
  const runtimePath = venvPython()
  if (runtimePath) process.env.STUDY_PYTHON = runtimePath
  const selection = {
    version: 1,
    course_ids: [...new Set(args.course_ids)],
    mode: args.mode,
    account_scope: pending.account_scope,
    state_fingerprint: pending.state_fingerprint,
  }
  try {
    await gateway.connect()
    const payload = await selectCoursesInChat({
      client: gateway,
      readState: () => readState(settings.dataDir),
      writeState: (expected, state) => saveState(settings.dataDir, 'replace', { expected, state }),
      root: settings.dataDir,
      selection,
    })
    writeSettings({
      stage: 'courses_selected',
      courses: { mode: args.mode, ids: payload.course_ids, savedAt: new Date().toISOString() },
      syncEpoch: null,
    })
    return {
      ok: true,
      mode: payload.mode,
      course_ids: payload.course_ids,
      message:
        args.mode === 'full'
          ? '课程与完整资料授权已保存。下一步同步学校信息和资料（首次可能较慢，中断后可重跑增量补齐）。'
          : '课程与“只同步课程信息”授权已保存，不会下载资料。下一步同步学校信息。',
      next_step: 'sync',
    }
  } catch (error) {
    return remoteFailure(error)
  }
}

export async function syncHandler(args, context = {}) {
  const settings = readSettings()
  if (!settings?.dataDir) return { error: 'LOCATION_REQUIRED', message: '请先完成资料位置设置。' }
  if (!existsSync(tokenPath(settings.dataDir))) {
    return { error: 'SCHOOL_REQUIRED', message: '请先连接学校账号。' }
  }
  const chatId = String(args.chat_id ?? '').trim()
  if (!/^(session-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(chatId)) {
    return {
      error: 'CHAT_ID_REQUIRED',
      message: '需要当前聊天标识才能开始同步：请先调用 moodle_study_chat，把它返回的 chat_id 原样传入。',
    }
  }
  const running = runningJob('setup-sync')
  if (running) return { ok: true, job: jobSummary(running), message: '同步已在进行中，请继续用 study_job_status 查看进度。' }
  // Sync needs the school, but the recovery advice differs from a plain setup
  // step: network failures keep local study available and never ask for login.
  try {
    await gatewayOf(context).connect({ force: false })
  } catch (error) {
    return remoteFailure(error)
  }
  const steps = [nodeStep('采集学校信息', 'tools/moodle/run.mjs', ['refresh', '--chat', chatId], settings)]
  const job = startJob({
    name: 'setup-sync',
    steps,
    logPath: join(supportDir(), 'logs', 'setup-sync.log'),
    onSuccess: () => writeSettings({ stage: 'synced', lastError: null }),
    onFailure: (message) => writeSettings({ lastError: message }),
  })
  return {
    ok: true,
    job: jobSummary(job),
    message:
      '正在采集学校信息并同步资料（完整授权时会下载、归档并建立索引，可能几分钟）。中断或部分失败后重跑会增量补齐，不会重复下载已成功内容；请用 study_job_status 查看进度。下载成功不等于已核读。',
  }
}

export async function reconnectHandler(args, context = {}) {
  const settings = readSettings()
  const local = localCapabilities(settings)
  if (!local.dataDir) {
    return { error: 'LOCATION_REQUIRED', kind: 'setup', message: '还没有选择资料保存位置。', next_step: 'study_setup_location', requires_login: false }
  }
  if (!local.configPresent) {
    return { error: 'CONFIG_REQUIRED', kind: 'setup', message: '资料目录还没有学校连接配置。', next_step: 'study_setup_location', requires_login: false }
  }
  if (!local.tokenPresent) {
    return {
      error: 'TOKEN_REQUIRED',
      kind: 'auth',
      message: '还没有可重试的学校登录：请让学生本人先连接学校（study_connect_school）；本地资料仍可学习。',
      next_step: 'study_connect_school',
      requires_login: true,
    }
  }
  const gateway = gatewayOf(context)
  gateway.invalidate()
  const snapshot = await gateway.probe({ force: true })
  if (snapshot.state === 'connected') {
    return {
      ok: true,
      connected: true,
      siteName: snapshot.siteName,
      userId: snapshot.userId,
      message: '学校连接已恢复；联网工具可以继续使用，本地资料与教学记录不受影响。',
    }
  }
  const failure = remoteFailure(
    new RemoteError(snapshot.lastError?.code ?? 'STUDY_NETWORK', snapshot.message ?? '学校暂时连接不上。', {
      kind: snapshot.requiresLogin ? 'auth' : 'network',
      next_step: snapshot.next_step,
      retryable: snapshot.retryable,
      requiresLogin: snapshot.requiresLogin,
    }),
  )
  return {
    ok: false,
    connected: false,
    ...failure,
    message: snapshot.requiresLogin
      ? '学校登录失效，需要学生本人重新连接学校；本地资料仍可学习。'
      : '学校暂时连接不上；恢复网络后再次调用 study_reconnect，不需要重新登录。本地资料仍可学习。',
  }
}

export async function finishHandler(args, context = {}) {
  const settings = readSettings()
  if (!settings?.dataDir) return { error: 'LOCATION_REQUIRED', message: '请先完成资料位置设置。' }
  const job = runningJob()
  if (job) {
    return { error: 'JOB_RUNNING', message: `还有设置任务在运行（${job.name}），请等待完成后再结束设置。` }
  }
  const status = await collectStatus({ remote: gatewayOf(context), verifySchool: true })
  const capabilities = status.capabilities
  if (!capabilities.locationSet) {
    return { error: 'LOCATION_REQUIRED', reason: 'location_missing', message: '还没有选择资料保存位置。', next_steps: status.next_steps }
  }
  if (!capabilities.configReady) {
    return { error: 'SETUP_INCOMPLETE', reason: 'config_missing', message: '资料目录还没有学校连接配置。', next_steps: status.next_steps }
  }
  if (!capabilities.runtimeReady) {
    return { error: 'SETUP_INCOMPLETE', reason: 'runtime_missing', message: '运行依赖尚未安装或不完整。', next_steps: status.next_steps }
  }
  if (!capabilities.tokenPresent) {
    return { error: 'SETUP_INCOMPLETE', reason: 'school_not_connected', message: '还没有连接学校账号。', next_steps: status.next_steps }
  }
  if (!capabilities.coursesSelected) {
    return { error: 'SETUP_INCOMPLETE', reason: 'courses_missing', message: '还没有保存课程选择与资料授权。', next_steps: status.next_steps }
  }
  if (!capabilities.syncReady) {
    return {
      error: 'SETUP_INCOMPLETE',
      reason: 'sync_incomplete',
      message: `首次同步尚未完成或证据不匹配，不能标记为设置完成。${syncSummaryText(status.sync)}`,
      sync: status.sync
        ? {
            ready: status.sync.ready,
            summary: status.sync.summary,
            courses: status.sync.courses,
            blockers: status.sync.blockers,
          }
        : null,
      next_steps: status.next_steps,
    }
  }
  if (capabilities.schoolAuthRequired) {
    return {
      error: 'SETUP_INCOMPLETE',
      reason: 'school_auth_required',
      message: '本地同步资料已就绪，但学校登录已失效。请学生本人重新连接学校后再完成设置。',
      next_steps: status.next_steps,
    }
  }
  writeSettings({ stage: 'ready', onboardedAt: new Date().toISOString() })
  const warning = capabilities.schoolConnected
    ? null
    : '学校当前连接不上；本地资料仍可继续学习，需要最新学校信息时用 study_reconnect 重试。'
  return {
    ok: true,
    ready: true,
    dataDir: settings.dataDir,
    sync: status.sync
      ? {
          mode: status.sync.mode,
          summary: status.sync.summary,
          text: status.sync.text,
        }
      : null,
    warning,
    message: warning
      ? `设置完成（本地资料与授权已核对）。${warning}`
      : '设置完成。可以开始在聊天里学习；学校信息会按当前授权增量同步。',
  }
}

export { syncSummaryText }
