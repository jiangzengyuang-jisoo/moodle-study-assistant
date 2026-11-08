// Moodle Study Assistant — DeepSeek Harness host plugin.
//
// Responsibilities (deliberately small; the study engine runs in the MCP
// server child process):
//   1. Provide the `moodleStudy` service used by this bundle's MCP row:
//      program directory, support directory and the runtime used to launch
//      the stdio MCP server. Nothing machine-specific is written into the
//      patch, so plugin upgrades re-resolve the current package directory.
//   2. Register the bundled teaching skill and the always-on teaching rules.
//   3. Register two small recovery tools that work even when the MCP server
//      cannot start, so a broken setup always has a visible entry point.
//
// No imports from @deepseek-ai packages are required: every registration is a
// plain object accepted by the public Cordis services.

import { existsSync, mkdirSync, readFileSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const packageDir = dirname(fileURLToPath(import.meta.url))
const resourceDir = join(packageDir, 'resources')
const defaultSupportDir = join(
  process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'),
  'MoodleStudyAssistant',
)

export const name = 'moodle-study-assistant'
export const inject = ['skills', 'systemPrompt', 'tools']

function readPackageVersion() {
  try {
    return JSON.parse(readFileSync(join(packageDir, 'package.json'), 'utf8')).version ?? '0.0.0'
  } catch {
    return '0.0.0'
  }
}

function resolveSupportDir(config) {
  const configured = config?.supportDir || process.env.MOODLE_STUDY_SUPPORT_DIR
  return configured ? resolve(configured) : resolve(defaultSupportDir)
}

function resolveDshHome() {
  if (process.env.DSH_HOME) return process.env.DSH_HOME
  return join(homedir(), '.dsh')
}

function readSettingsFile(supportDir) {
  try {
    return JSON.parse(readFileSync(join(supportDir, 'settings.json'), 'utf8'))
  } catch {
    return null
  }
}

function readRuntimeFile(supportDir) {
  try {
    return JSON.parse(readFileSync(join(supportDir, 'runtime.json'), 'utf8'))
  } catch {
    return {}
  }
}

function parseSkillFile(path) {
  const raw = readFileSync(path, 'utf8')
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(raw)
  if (!match) return { frontmatter: {}, body: raw.trim() }
  const frontmatter = {}
  for (const line of match[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line)
    if (!kv) continue
    let value = kv[2].trim()
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    frontmatter[kv[1]] = value
  }
  return { frontmatter, body: match[2].trim() }
}

function readText(path, fallback = '') {
  try {
    return readFileSync(path, 'utf8')
  } catch {
    return fallback
  }
}

export function apply(ctx, config) {
  const supportDir = resolveSupportDir(config)
  const programDir = join(packageDir, 'program')
  const version = readPackageVersion()

  try {
    mkdirSync(supportDir, { recursive: true })
  } catch {
    // The recovery tool reports a broken support directory instead of aborting
    // harness startup.
  }

  ctx.provide('moodleStudy', {
    nodePath: process.execPath,
    mcpArgs: [join(programDir, 'tools', 'moodle', 'mcp-entry.mjs'), 'mcp'],
    programDir,
    supportDir,
    version,
    dshHome: resolveDshHome(),
  })

  // Teaching skill (the detailed pedagogy lives in the bundled skill file).
  ctx.effect(() => {
    const skillPath = join(programDir, 'skills', 'self-study-tutor', 'SKILL.md')
    if (!existsSync(skillPath)) return () => {}
    const { frontmatter, body } = parseSkillFile(skillPath)
    if (!frontmatter.name || !frontmatter.description || !body) return () => {}
    return ctx.skills.register({
      name: frontmatter.name,
      description: frontmatter.description,
      whenToUse: frontmatter.whenToUse,
      content: body,
      source: 'bundled',
    })
  })

  // Plugin usage skill: how to drive this plugin's tools without any shell.
  ctx.effect(() => {
    const skillPath = join(resourceDir, 'skills', 'moodle-study.md')
    if (!existsSync(skillPath)) return () => {}
    const { frontmatter, body } = parseSkillFile(skillPath)
    if (!frontmatter.name || !frontmatter.description || !body) return () => {}
    return ctx.skills.register({
      name: frontmatter.name,
      description: frontmatter.description,
      content: body,
      source: 'bundled',
    })
  })

  // Always-on teaching rules. This is what makes the tutoring behaviour apply
  // to new users even when no project AGENTS.md exists.
  ctx.effect(() => {
    const rules = readText(join(resourceDir, 'teaching-rules.md')).trim()
    if (!rules) return () => {}
    return ctx.systemPrompt.section({
      name: 'moodle:teaching-rules',
      order: 700,
      text: rules,
    })
  })

  // Recovery entry points that do not depend on the MCP server process.
  ctx.effect(() => ctx.tools.register({
    name: 'moodle_study_status',
    description:
      'Read Moodle Study Assistant setup status from the local plugin (data directory, Python runtime marker, school sign-in files, recent setup errors) and return the concrete recovery step. Works even when the MCP server tools are missing or fail to start, and does not need the school to be reachable.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: {
      schema: { type: 'object' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }],
    },
    async execute() {
      const runtime = readRuntimeFile(supportDir)
      const pythonPresent = existsSync(join(supportDir, 'venv', 'Scripts', 'python.exe'))
      const result = {
        pluginVersion: version,
        supportDir,
        programDir,
        programPresent: existsSync(join(programDir, 'tools', 'moodle', 'mcp-entry.mjs')),
        settings: readSettingsFile(supportDir),
        dataDir: null,
        dataDirExists: null,
        pythonPresent,
        runtimeReady: pythonPresent && Boolean(runtime.core?.installed),
        tokenPresent: null,
        nextStep: null,
        recovery: [],
      }
      const settings = result.settings
      if (!settings?.dataDir) {
        result.nextStep = 'call_study_setup'
        result.recovery.push('还没有选择资料保存位置；调用 MCP 工具 study_setup_status 并按提示完成首次设置。')
        return result
      }
      result.dataDir = settings.dataDir
      try {
        result.dataDirExists = statSync(settings.dataDir).isDirectory()
      } catch {
        result.dataDirExists = false
      }
      result.tokenPresent = existsSync(join(settings.dataDir, '.private', 'moodle-token.dpapi'))
      if (!result.runtimeReady) {
        result.nextStep = 'install_runtime'
        result.recovery.push('运行依赖尚未安装或安装中断；调用 MCP 工具 study_setup_runtime 可继续安装，已完成的步骤会跳过。')
      } else if (!result.tokenPresent) {
        result.nextStep = 'connect_school'
        result.recovery.push('还没有连接学校账号；调用 study_connect_school，并在弹出的学校网页由本人完成登录。已下载的本地资料仍可学习。')
      } else {
        result.nextStep = 'call_study_setup_status'
        result.recovery.push('调用 MCP 工具 study_setup_status 查看当前设置、同步证据和下一步。')
        result.recovery.push('学校暂时连不上时本地学习工具仍可用；联网失败用 study_reconnect 重试，不需要重新登录。')
      }
      if (!result.programPresent) {
        result.recovery.push('插件程序文件缺失，请卸载后重新安装本插件（个人学习资料会保留）。')
      }
      if (!result.dataDirExists) {
        result.recovery.push('选择的资料文件夹当前不可用；重新连接该文件夹或选择其他位置。')
      }
      result.recovery.push('如果 MCP 相关工具一直不出现，请完全退出并重新打开 DeepSeek Harness；不要删除资料目录。')
      return result
    },
  }))

  ctx.effect(() => ctx.tools.register({
    name: 'moodle_study_chat',
    description:
      'Return the stable identifier of the current chat session. Call it before a chapter check that needs chat_id, then pass the returned value as chat_id to study_check_chapter.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: {
      schema: { type: 'object', properties: { chat_id: { type: 'string' } }, required: ['chat_id'] },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(_args, exec) {
      if (!exec?.agent?.id) throw new Error('moodle_study_chat must be called from a chat session')
      return { chat_id: String(exec.agent.id) }
    },
  }))
}
