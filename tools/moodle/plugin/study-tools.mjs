// Local study tools exposed by the plugin MCP server.
//
// These wrap the existing read-only local engine commands so ordinary teaching
// needs no shell command, no approval prompt and no manual path handling. They
// never write to the school and never change completion state.
import { existsSync, readFileSync, statSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { extname, join } from 'node:path'

import { z } from 'zod'

import { jobSummary, runningJob, startJob } from './jobs.mjs'
import { remoteFailure } from './onboarding-tools.mjs'
import { engineEnv, pythonStep, venvPython } from './python-runtime.mjs'
import { programDir, readSettings, statePath, supportDir } from './settings.mjs'

const MAX_BUFFER = 64 * 1024 * 1024

const output = (data, isError = false) => ({
  content: [{ type: 'text', text: typeof data === 'string' ? data : JSON.stringify(data, null, 2) }],
  ...(isError ? { isError: true } : {}),
})

function parseJsonText(text) {
  const raw = String(text ?? '').trim()
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    // Fall through to the last JSON document for commands that print progress.
  }
  const lines = raw.split(/\r?\n/)
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index].trim()
    if (!line || (!line.startsWith('{') && !line.startsWith('['))) continue
    try {
      return JSON.parse(line)
    } catch {
      // Keep looking.
    }
  }
  return null
}

function runEngine(script, args, { stdin = null, timeoutMs = 240000 } = {}) {
  const settings = readSettings()
  const python = venvPython()
  if (!python) {
    return { ok: false, failure: { error: 'RUNTIME_NOT_READY', message: '运行依赖尚未安装或健康检查未通过，请先调用 study_setup_runtime 安装/修复；可用 study_setup_status 查看诊断。', next_step: 'study_setup_runtime' } }
  }
  if (!settings?.dataDir || !existsSync(statePath(settings.dataDir))) {
    return {
      ok: false,
      failure: {
        error: 'LOCAL_STATE_MISSING',
        message:
          '本地学习档案还没有初始化（尚未选课或初始化中断）。已下载的资料不会被删除；请调用 study_setup_status 查看下一步并继续设置。',
        next_step: 'study_setup_status',
      },
    }
  }
  const result = spawnSync(python, [join(programDir(), 'tools', 'study', script), ...args], {
    cwd: programDir(),
    env: engineEnv(settings),
    input: stdin === null ? undefined : stdin,
    encoding: 'utf8',
    timeout: timeoutMs,
    maxBuffer: MAX_BUFFER,
    windowsHide: true,
  })
  if (result.error) {
    return { ok: false, failure: { error: 'ENGINE_SPAWN_FAILED', message: String(result.error.message ?? result.error) } }
  }
  const payload = parseJsonText(result.stdout)
  if (result.status !== 0) {
    const failure = parseJsonText(result.stderr) ?? {
      error: 'ENGINE_FAILED',
      message: String(result.stderr ?? '').slice(-1500) || `退出码 ${result.status}`,
    }
    return { ok: false, failure, stderr: String(result.stderr ?? '') }
  }
  if (payload === null) {
    return {
      ok: false,
      failure: { error: 'ENGINE_OUTPUT_INVALID', message: '本地学习引擎返回了无法解析的结果。', stdout: String(result.stdout ?? '').slice(-1500) },
    }
  }
  return { ok: true, payload, stdout: String(result.stdout ?? '') }
}

function toolResult(result) {
  if (!result.ok) return output(result.failure, true)
  return output(result.payload)
}

function renderBlocks(result) {
  if (!result.ok) return output(result.failure, true)
  const payload = result.payload
  const paths = []
  if (typeof payload.path === 'string') paths.push({ path: payload.path, label: payload.material_id ?? null })
  if (Array.isArray(payload.renders)) {
    for (const row of payload.renders) {
      const path = row?.path ?? row?.image_path
      if (typeof path === 'string') paths.push({ path, label: row?.unit ?? null })
    }
  }
  const blocks = [{ type: 'text', text: JSON.stringify(payload, null, 2) }]
  const mime = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif' }
  for (const item of paths.slice(0, 8)) {
    try {
      const info = statSync(item.path)
      if (info.size > 18 * 1024 * 1024) continue
      const type = mime[extname(item.path).toLowerCase()]
      if (!type) continue
      blocks.push({ type: 'image', data: readFileSync(item.path).toString('base64'), mimeType: type })
    } catch {
      // Missing render output is reported through the JSON payload only.
    }
  }
  return { content: blocks }
}

export function registerStudyTools(server, context = {}) {
  server.registerTool(
    'study_local_context',
    {
      title: '读取本地学习接续',
      description:
        'Read the local learning continuity (selected courses, current teaching position, today plan summary, materials overview) without network access. Use it when the student asks to continue learning or asks about their study state.',
      inputSchema: z.object({
        course_id: z.string().trim().max(200).optional().describe('Local course id from the study context.'),
        detail: z.boolean().default(false).describe('Include more detail for the selected course.'),
      }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (args) => {
      const cli = ['context', '--read-only']
      if (args.course_id) cli.push('--course', args.course_id)
      if (args.detail) cli.push('--detail')
      return toolResult(runEngine('library.py', cli, { timeoutMs: 120000 }))
    },
  )

  server.registerTool(
    'study_chapter_prep',
    {
      title: '批量准备本章资料',
      description:
        'Prepare one chapter in a single read-only batch: material/unit list, headings outline, budgeted page text, figure candidates and chapter continuity. Use the returned omitted/continuation hints to read more instead of reading page by page. Local only; the strict chapter check remains study_check_chapter.',
      inputSchema: z.object({
        course_id: z.string().trim().min(1).max(200).describe('Local course id.'),
        section: z.string().trim().max(300).optional().describe('Chapter label, for example Topic 5.'),
        module_ids: z.array(z.number().int().positive()).max(100).optional().describe('Explicit module ids, takes precedence over section.'),
      }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (args) => {
      const cli = ['chapter-prep', '--course', args.course_id]
      if (args.section) cli.push('--section', args.section)
      if (args.module_ids?.length) cli.push('--modules', args.module_ids.join(','))
      return toolResult(runEngine('library.py', cli, { timeoutMs: 180000 }))
    },
  )

  server.registerTool(
    'study_material_read',
    {
      title: '读取指定资料页',
      description:
        'Read a prepared material unit range from the local library (extracted text plus source locator). Use the material id from study_chapter_prep; verify formulas and figures on a rendered original page when they matter.',
      inputSchema: z.object({
        material_id: z.string().trim().min(1).max(300).describe('Material id from the local library.'),
        start: z.number().int().positive().default(1).describe('First unit/line, 1-based.'),
        end: z.number().int().positive().optional().describe('Last unit/line (inclusive).'),
        around: z.number().int().min(0).max(20).default(1).describe('Context units around a single unit.'),
      }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (args) => {
      const cli = ['read', args.material_id, '--start', String(args.start), '--around', String(args.around)]
      if (args.end) cli.push('--end', String(args.end))
      return toolResult(runEngine('library.py', cli, { timeoutMs: 120000 }))
    },
  )

  server.registerTool(
    'study_material_render',
    {
      title: '渲染资料原页',
      description:
        'Render prepared PDF pages (or return the original image file) from the local library so formulas, diagrams and equation symbols can be checked on the original page. Returns page images plus their metadata. PDF/DOCX office files that have no page image keep their text-only limitation.',
      inputSchema: z.object({
        material_id: z.string().trim().min(1).max(300).describe('Material id from the local library.'),
        units: z.array(z.number().int().positive()).min(1).max(8).describe('Unit/page numbers to render.'),
        dpi: z.number().int().min(72).max(300).default(130),
      }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (args) => {
      const cli = ['render', args.material_id, '--units', args.units.join(','), '--dpi', String(args.dpi)]
      return renderBlocks(runEngine('library.py', cli, { timeoutMs: 180000 }))
    },
  )

  server.registerTool(
    'study_teaching_event',
    {
      title: '保存教学位置',
      description:
        'Save what was actually taught in this turn as the current teaching position (small JSON event, never a whole-state rewrite). Call it after the substantive explanation was shown to the student. chat_id is the current main-chat identifier from moodle_study_chat. The engine merges, validates and rebuilds views atomically.',
      inputSchema: z.object({
        chat_id: z.string().trim().min(1).max(500).describe('Current main-chat id returned by moodle_study_chat.'),
        event: z.object({
          course_id: z.string().trim().min(1).max(200).describe('Local course id that was taught.'),
          fields: z.object({
            chapter: z.string().max(300).optional(),
            topic: z.string().max(800).optional(),
            stage: z.string().max(200).optional(),
            next_step: z.string().max(800).optional(),
            material_id: z.string().max(300).optional(),
            pages: z.array(z.number().int().positive()).max(500).optional(),
            source: z.string().max(4000).optional(),
            pending_check: z.any().optional(),
            user_request: z.string().max(500).optional(),
            lesson_scope: z.array(z.string().max(300)).max(12).optional(),
            worked_example: z.string().max(4000).optional(),
            coverage_note: z.string().max(800).optional(),
          }).describe('This turn only: what was actually taught and where.'),
          checks_append: z.array(z.record(z.any())).max(5).optional().describe('Completed archived check records (only when the student actually answered).'),
          review_backlog_append: z.array(z.record(z.any())).max(3).optional().describe('Old-chapter review items to keep.'),
          onboarding: z.enum(['shown', 'skipped']).optional(),
        }).describe('One small teaching-position event; never a whole-state rewrite.'),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (args) => {
      const event = args.event
      const payload = {
        course_id: event.course_id,
        fields: event.fields ?? {},
        chat_id: args.chat_id,
      }
      if (event.checks_append?.length) payload.checks_append = event.checks_append
      if (event.review_backlog_append?.length) payload.review_backlog_append = event.review_backlog_append
      if (event.onboarding) payload.onboarding = event.onboarding
      return toolResult(
        runEngine('session.py', ['--chat', args.chat_id, 'teaching-event'], {
          stdin: JSON.stringify(payload),
          timeoutMs: 120000,
        }),
      )
    },
  )

  server.registerTool(
    'study_local_status',
    {
      title: '本地学习状态',
      description: 'Read the local study status without contacting the school (selected courses, pending tasks, review queue summary).',
      inputSchema: z.object({
        chat_id: z.string().trim().max(500).optional().describe('Current main-chat id; omit only outside a study turn.'),
      }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (args) => {
      const cli = []
      if (args.chat_id) cli.push('--chat', args.chat_id)
      cli.push('status')
      return toolResult(runEngine('session.py', cli, { timeoutMs: 120000 }))
    },
  )

  server.registerTool(
    'study_refresh',
    {
      title: '更新学校信息',
      description:
        'Collect the current school snapshot for the selected courses and run the authorized material sync. Use only when the request needs current school information (assignments, deadlines, announcements, exam arrangements) or the student explicitly asks to update. Long-running: returns a job, poll study_job_status.',
      inputSchema: z.object({
        chat_id: z.string().trim().min(1).max(500).describe('Current main-chat id returned by moodle_study_chat.'),
        full: z.boolean().default(false).describe('True only when the student explicitly asks for a full refresh.'),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
    },
    async (args) => {
      const settings = readSettings()
      const running = runningJob('study-refresh') ?? runningJob('setup-sync')
      if (running) return output({ ok: true, job: jobSummary(running), message: '同步已在进行中。' })
      // Refresh needs the school; distinguish a temporary network failure
      // (retry with study_reconnect, keep studying locally) from an invalid
      // sign-in (the student must log in again).
      if (context.remote) {
        try {
          await context.remote.connect()
        } catch (error) {
          return output(remoteFailure(error), true)
        }
      }
      const command = args.full ? 'refresh' : 'continue'
      const job = startJob({
        name: 'study-refresh',
        steps: [pythonStep('同步学校信息', 'tools/study/session.py', ['--chat', args.chat_id, command], settings)],
        logPath: join(supportDir(), 'logs', 'study-refresh.log'),
      })
      return output({
        ok: true,
        job: jobSummary(job),
        message: '正在采集学校信息并同步已授权资料。请用 study_job_status 查看进度；采集完成不等于模型已核读。',
      })
    },
  )
}
