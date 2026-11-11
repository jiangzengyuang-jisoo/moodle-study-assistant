// Registration of every first-use, maintenance and recovery tool.
//
// All tools are registered in every server mode: local study never depends on
// the school connection, and a student who restarts in the middle of setup
// must still see the tool that the status output tells them to call.
import { z } from 'zod'

import { jobSummary, latestJob, runningJob } from './jobs.mjs'
import {
  collectStatus,
  connectHandler,
  coursesHandler,
  finishHandler,
  locationHandler,
  reconnectHandler,
  runtimeHandler,
  selectCoursesHandler,
  statusPayload,
  syncHandler,
} from './onboarding-tools.mjs'

const output = (data, isError = false) => ({
  content: [{ type: 'text', text: JSON.stringify(data, null, 2) }],
  ...(isError ? { isError: true } : {}),
})

// Handlers return plain JSON; the MCP SDK needs the canonical content shape.
const wrap = (handler, context) => async (args) => {
  const value = await handler(args, context)
  return output(value, Boolean(value?.error))
}

function registerStatus(server, context) {
  server.registerTool(
    'study_setup_status',
    {
      title: '查看 Moodle 学习助手设置状态',
      description:
        'Read the full setup and capability status: data location, local study availability, Python runtime, school connection (connected / network failure / login required), selected courses, real sync evidence, setup job logs and the exact next step with the tool to call. Call this first after a restart, when a setup step was interrupted, when Moodle tools report a connection problem, or before claiming setup is complete.',
      inputSchema: z.object({}),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    },
    async () => wrap(statusPayload, context)({}),
  )
}

function registerJobStatus(server) {
  server.registerTool(
    'study_job_status',
    {
      title: '查看后台任务进度',
      description: 'Read the progress and log tail of a running setup/sync job, or the most recent one when no job id is given.',
      inputSchema: z.object({ job_id: z.string().trim().max(100).optional() }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async () => {
      const active = runningJob()
      if (active) return output({ ok: true, running: true, job: jobSummary(active) })
      const candidates = ['setup-runtime', 'setup-login', 'setup-sync', 'study-refresh']
        .map((name) => latestJob(name))
        .filter(Boolean)
        .sort((a, b) => String(b.startedAt).localeCompare(String(a.startedAt)))
      const latest = candidates[0]
      return output({
        ok: true,
        running: false,
        job: latest ? jobSummary(latest) : null,
        message: latest
          ? null
          : '当前没有后台任务记录（工具重连后记录会清空，但已完成的状态仍可从 study_setup_status 看出）。',
      })
    },
  )
}

function registerConnect(server, context) {
  server.registerTool(
    'study_connect_school',
    {
      title: '连接学校账号',
      description:
        'Start the school sign-in flow in the browser so the student本人 completes SSO/MFA. Use it when no account is connected, or when the saved sign-in is invalid/undecryptable (status says login required). A temporary network failure is handled by study_reconnect instead. After a successful sign-in the MCP tools reconnect automatically and the student continues at the same setup step. Never ask for a password or token in chat.',
      inputSchema: z.object({
        browser: z.enum(['msedge', 'chrome']).default('msedge').describe('Installed browser used for the school page.'),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
    },
    async (args) => wrap(connectHandler, context)(args ?? {}),
  )
  server.registerTool(
    'study_reconnect',
    {
      title: '重试学校连接',
      description:
        'Retry the saved school connection without signing in again. Use it for temporary network/interface failures reported by remote tools or by study_setup_status (state=network). It never replaces or resets credentials; if the saved sign-in is invalid, it reports that the student must reconnect the school account. Local study tools keep working during the outage.',
      inputSchema: z.object({}),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    },
    async () => wrap(reconnectHandler, context)({}),
  )
}

function registerSync(server, context) {
  server.registerTool(
    'study_setup_sync',
    {
      title: '同步学校信息与资料（可重跑）',
      description:
        'Collect the current school snapshot for the selected courses and, when the saved authorization is full, download/archive/index authorized materials. Re-running after an interruption is incremental and does not re-download successful content. Needs the current chat id from moodle_study_chat. Long-running: poll study_job_status, then check study_setup_status which reports the real per-course sync evidence.',
      inputSchema: z.object({
        chat_id: z.string().trim().min(1).max(500).describe('Current main-chat identifier returned by moodle_study_chat.'),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
    },
    async (args) => wrap(syncHandler, context)(args),
  )
}

function registerFinish(server, context) {
  server.registerTool(
    'study_setup_finish',
    {
      title: '完成首次设置',
      description:
        'Mark setup complete only when the real sync evidence matches the current account, course selection and authorization. Returns the per-course evidence and blockers instead of ready when sync has not started, failed, was interrupted, is stale, or the cache is empty. Local study does not require this step.',
      inputSchema: z.object({}),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    },
    async () => wrap(finishHandler, context)({}),
  )
}

function registerLocation(server, context) {
  server.registerTool(
    'study_setup_location',
    {
      title: '选择/更换资料保存位置',
      description:
        'Choose where personal learning data lives, or switch to another study folder (for example an existing project folder to reuse earlier records). Switching keeps the old folder untouched, does not copy credentials, and reconnects the tools so the old connection is never used for the new profile. Ask the student before choosing or changing a location.',
      inputSchema: z.object({
        data_dir: z.string().trim().max(400).optional().describe('Absolute folder path chosen by the student.'),
        moodle_url: z.string().trim().max(300).optional().describe('School Moodle base URL, for example https://moodle.hku.hk.'),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (args) => wrap(locationHandler, context)(args ?? {}),
  )
  server.registerTool(
    'study_setup_runtime',
    {
      title: '安装/修复学习引擎运行依赖',
      description:
        'Install or repair the local Python runtime dependencies used by the study engine (locked versions, about 20 MB on first use) into a private environment managed by the plugin. Safe to re-run after an interrupted install: it re-checks the current interpreter, pip and dependency imports, then repairs only what is missing (interrupted venv creation, missing pip via offline ensurepip, damaged interpreter or missing packages). It reports ready only after a local health check, and a failed repair is retried on the next call. Start it, then poll study_job_status.',
      inputSchema: z.object({}),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    },
    async () => wrap(runtimeHandler, context)({}),
  )
}

function registerCourses(server, context) {
  server.registerTool(
    'study_setup_courses',
    {
      title: '列出学校课程',
      description:
        'List the courses visible to the connected school account so the student can choose which courses to follow this term. Read-only; show the course names to the student and let them choose.',
      inputSchema: z.object({}),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    },
    async () => wrap(coursesHandler, context)({}),
  )
  server.registerTool(
    'study_setup_select_courses',
    {
      title: '保存课程选择与资料范围',
      description:
        'Save the courses the student explicitly confirmed together with the material scope: full downloads and indexes course content; info_only synchronizes course information only. Used for both first selection and later course changes; the previous records stay unless replaced by the confirmed selection. Requires a fresh study_setup_courses listing in this session.',
      inputSchema: z.object({
        course_ids: z.array(z.number().int().positive().safe()).min(1).max(50).describe('Course ids the student explicitly selected.'),
        mode: z.enum(['full', 'info_only']).describe('full = download and index materials; info_only = course information only.'),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    },
    async (args) => wrap(selectCoursesHandler, context)(args),
  )
}

export function registerSetupTools(server, context = {}) {
  registerStatus(server, context)
  registerJobStatus(server)
  registerConnect(server, context)
  registerLocation(server, context)
  registerCourses(server, context)
  registerSync(server, context)
  registerFinish(server, context)
}

export { collectStatus }
