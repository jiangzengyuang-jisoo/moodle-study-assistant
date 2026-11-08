// Registration of the first-use / recovery tools, filtered by server mode.
import { z } from 'zod'

import { jobSummary, latestJob, runningJob } from './jobs.mjs'
import {
  connectHandler,
  coursesHandler,
  finishHandler,
  locationHandler,
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
const wrap = (handler) => async (args) => {
  const value = await handler(args)
  return output(value, Boolean(value?.error))
}

function registerStatus(server) {
  server.registerTool(
    'study_setup_status',
    {
      title: '查看 Moodle 学习助手设置状态',
      description:
        'Read the Moodle Study Assistant setup status from the local plugin: data location, Python runtime, school connection, course selection, current setup job log, and the exact next step. Call this first when the student starts using the plugin, when Moodle tools are missing, or when a setup step fails.',
      inputSchema: z.object({}),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async () => wrap(statusPayload)(),
  )
}

function registerJobStatus(server) {
  server.registerTool(
    'study_job_status',
    {
      title: '查看后台任务进度',
      description:
        'Read the progress and log tail of a running setup/refresh job, or the most recent one when no job id is given.',
      inputSchema: z.object({
        job_id: z.string().trim().max(100).optional(),
      }),
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
          : '当前没有后台任务记录（设置工具重连后记录会清空，但已完成的状态仍可从 study_setup_status 看出）。',
      })
    },
  )
}

function registerConnect(server) {
  server.registerTool(
    'study_connect_school',
    {
      title: '连接学校账号',
      description:
        'Start the school sign-in flow in the browser so the student本人 completes SSO/MFA on the school page. Installs the login component first if needed, initializes local study files, then opens the official Moodle mobile-launch page and waits for the student. Credentials stay in the student browser and the result token is stored with Windows DPAPI by the existing mechanism; never ask for a password or token in chat. Long-running: poll study_job_status.',
      inputSchema: z.object({
        browser: z.enum(['msedge', 'chrome']).default('msedge').describe('Installed browser used for the school page.'),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
    },
    async (args) => wrap((value) => connectHandler(value, { mode: 'setup' }))(args ?? {}),
  )
}

function registerSync(server) {
  server.registerTool(
    'study_setup_sync',
    {
      title: '首次同步学校信息与资料',
      description:
        'Collect the current school snapshot for the selected courses and, when the saved authorization is full, download/archive/index authorized course materials. Needs the current chat id: call moodle_study_chat first and pass its chat_id unchanged. Long-running: poll study_job_status.',
      inputSchema: z.object({
        chat_id: z.string().trim().min(1).max(500).describe('Current main-chat identifier returned by moodle_study_chat.'),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
    },
    async (args) => wrap(syncHandler)(args),
  )
}

function registerFinish(server) {
  server.registerTool(
    'study_setup_finish',
    {
      title: '完成首次设置',
      description:
        'Verify that the data location, Python runtime, school connection and course selection are all present, then mark setup complete. The Moodle tools reconnect automatically. Never claim setup success before this returns ready.',
      inputSchema: z.object({}),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async () => wrap(finishHandler)(),
  )
}

function registerFullSetup(server) {
  registerStatus(server)
  registerJobStatus(server)
  server.registerTool(
    'study_setup_location',
    {
      title: '选择资料保存位置',
      description:
        'Choose where this student keeps personal learning data (Study folder, school connection config and Windows-protected token). Defaults to Documents/MoodleStudy. An existing folder that already contains a study profile is adopted without overwriting records. Ask the student before choosing a location; do not pass a path without their agreement.',
      inputSchema: z.object({
        data_dir: z.string().trim().max(400).optional().describe('Absolute folder path chosen by the student.'),
        moodle_url: z.string().trim().max(300).optional().describe('School Moodle base URL, for example https://moodle.hku.hk.'),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (args) => wrap(locationHandler)(args ?? {}),
  )
  server.registerTool(
    'study_setup_runtime',
    {
      title: '安装学习引擎运行依赖',
      description:
        'Install the local Python runtime dependencies used by the study engine (about 20 MB on first use, pinned versions) into a private environment managed by the plugin. Requires a chosen data location and network access. Long-running: start it, then poll study_job_status.',
      inputSchema: z.object({}),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    },
    async () => wrap(runtimeHandler)(),
  )
  registerConnect(server)
  server.registerTool(
    'study_setup_courses',
    {
      title: '列出学校课程',
      description:
        'List the courses visible to the connected school account so the student can choose which courses to follow this term. Read-only. Show the course names to the student and let them choose; do not choose for them.',
      inputSchema: z.object({}),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    },
    async () => wrap(coursesHandler)(),
  )
  server.registerTool(
    'study_setup_select_courses',
    {
      title: '保存课程选择与资料范围',
      description:
        'Save the courses the student explicitly confirmed together with the material scope: full downloads and indexes that course content; info_only synchronizes course information only. Must be called after study_setup_courses and after the student confirms both the courses and the scope.',
      inputSchema: z.object({
        course_ids: z.array(z.number().int().positive().safe()).min(1).max(50).describe('Course ids the student explicitly selected.'),
        mode: z.enum(['full', 'info_only']).describe('full = download and index course materials; info_only = course information only.'),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    },
    async (args) => wrap(selectCoursesHandler)(args),
  )
  registerSync(server)
  registerFinish(server)
}

// Study mode keeps only the recovery actions that make sense while learning;
// the full first-use wizard is not repeated in the normal tool set.
function registerRecovery(server) {
  registerStatus(server)
  registerJobStatus(server)
  registerConnect(server)
  registerSync(server)
  registerFinish(server)
}

export function registerSetupTools(server, { mode = 'setup' } = {}) {
  if (mode === 'setup') registerFullSetup(server)
  else registerRecovery(server)
}
