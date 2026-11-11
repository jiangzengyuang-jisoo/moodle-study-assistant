// Offline verification of the study-mode tool set registered by the plugin's
// MCP server. It mounts the real tool registrations on an in-memory MCP
// transport, so no school connection and no student data are involved.
import { existsSync, readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'

const repoRoot = dirname(dirname(dirname(fileURLToPath(import.meta.url))))
const programDir = join(repoRoot, 'dist', 'moodle-study-assistant', 'program')
const supportDir = join(repoRoot, 'tmp', 'plugin-verify', 'support')
const dataDir = join(repoRoot, 'tmp', 'plugin-verify', 'data')
process.env.MOODLE_STUDY_SUPPORT_DIR = supportDir
process.env.MOODLE_STUDY_PROGRAM_DIR = programDir
process.env.STUDY_DATA_ROOT = dataDir

const settingsFile = join(supportDir, 'settings.json')
if (!existsSync(settingsFile)) {
  console.error('verify-study-tools: run verify-plugin.mjs first (support/settings.json is missing)')
  process.exit(1)
}
const venvPython = join(supportDir, 'venv', 'Scripts', 'python.exe')
if (!existsSync(venvPython)) {
  console.error('verify-study-tools: run verify-plugin.mjs first (Python runtime is missing)')
  process.exit(1)
}
process.env.STUDY_PYTHON = venvPython
process.env.STUDY_NODE = process.execPath
process.env.ELECTRON_RUN_AS_NODE = '1'

const results = []
const record = (name, ok, detail = null) => {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` - ${JSON.stringify(detail)}` : ''}`)
}

function parseTool(result) {
  const text = result?.content?.find((item) => item.type === 'text')?.text ?? '{}'
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

async function main() {
  // Initialize the local study state in the isolated data dir through the real
  // engine entry point (same command the first-use flow runs).
  const init = spawnSync(process.execPath, [join(programDir, 'tools', 'moodle', 'run.mjs'), 'init'], {
    cwd: programDir,
    env: { ...process.env, STUDY_DATA_ROOT: dataDir, STUDY_PYTHON: venvPython, PYTHONDONTWRITEBYTECODE: '1' },
    encoding: 'utf8',
    timeout: 120000,
  })
  record('engine init writes the isolated data dir', init.status === 0, String(init.stderr ?? '').slice(-400))
  record('state exists in the data dir after init', existsSync(join(dataDir, 'Study', '_System', 'state.json')))

  const moduleUrl = (relativePath) => pathToFileURL(join(programDir, relativePath)).href
  const { registerSetupTools } = await import(moduleUrl('tools/moodle/plugin/setup-tools.mjs'))
  const { registerStudyTools } = await import(moduleUrl('tools/moodle/plugin/study-tools.mjs'))
  const { registerChapterCheckTool } = await import(moduleUrl('tools/moodle/chapter-check-tool.mjs'))

  const remoteStub = {
    connect: async () => {
      throw new Error('offline in registration test')
    },
    probe: async () => ({ state: 'network', connected: false }),
    status: () => ({ state: 'network', connected: false }),
    invalidate() {},
  }

  const server = new McpServer({ name: 'study-tools-verify', version: '0.0.0' })
  registerSetupTools(server, { remote: remoteStub })
  registerStudyTools(server, { remote: remoteStub })
  registerChapterCheckTool(server, {
    root: dataDir,
    loadState: () => JSON.parse(readFileSync(join(dataDir, 'Study', '_System', 'state.json'), 'utf8')),
    connect: async () => null,
    beforeCheck: () => {},
  })

  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair()
  const client = new Client({ name: 'study-tools-verify-client', version: '0.0.0' })
  await Promise.all([server.connect(serverTransport), client.connect(clientTransport)])
  const listed = await client.listTools()
  const names = listed.tools.map((tool) => tool.name)
  const studyTools = [
    'study_local_context',
    'study_chapter_prep',
    'study_material_read',
    'study_material_render',
    'study_teaching_save',
    'study_teaching_event',
    'study_local_status',
    'study_refresh',
  ]
  const recoveryTools = [
    'study_setup_status',
    'study_setup_location',
    'study_setup_runtime',
    'study_setup_courses',
    'study_setup_select_courses',
    'study_connect_school',
    'study_reconnect',
    'study_setup_sync',
    'study_setup_finish',
    'study_job_status',
  ]
  record('local study tools are registered', studyTools.every((name) => names.includes(name)), names.filter((name) => name.startsWith('study_')))
  record(
    'every setup/recovery tool stays available after setup (course change, location change, repair)',
    recoveryTools.every((name) => names.includes(name)),
    recoveryTools.filter((name) => !names.includes(name)),
  )
  record('strict chapter check tool is registered', names.includes('study_check_chapter'))
  record('no duplicated tool registration', new Set(names).size === names.length)

  const contextResult = await client.callTool({ name: 'study_local_context', arguments: {} })
  const contextPayload = parseTool(contextResult)
  record(
    'local context tool runs the real engine offline',
    contextResult.isError !== true && contextPayload !== null,
    contextPayload ? Object.keys(contextPayload).slice(0, 8) : null,
  )

  const teaching = await client.callTool({
    name: 'study_teaching_event',
    arguments: {
      chat_id: 'session-00000000-0000-4000-8000-000000000000',
      event: { course_id: 'missing-course', fields: { topic: 'verify' } },
    },
  })
  record(
    'teaching event tool returns a structured engine result',
    typeof teaching.content?.[0]?.text === 'string' && teaching.content[0].text.length > 0,
    teaching.content?.[0]?.text?.slice(0, 160),
  )

  // New teaching-save contract: exercise the real tool against the real engine
  // in the isolated verification profile (a synthetic course is added only to
  // that throwaway data directory).
  const studyDir = join(programDir, 'tools', 'study')
  const statePath = join(dataDir, 'Study', '_System', 'state.json')
  const addCourse = spawnSync(venvPython, ['-B', '-c', [
    'import json, sys',
    `sys.path.insert(0, ${JSON.stringify(studyDir)})`,
    'import state_io',
    `path = ${JSON.stringify(statePath)}`,
    "state = json.loads(open(path, encoding='utf-8-sig').read())",
    'draft = json.loads(json.dumps(state))',
    "draft['courses'].append({'id': 'verify-course', 'name': 'Verification course'})",
    "state_io.bridge(path, 'replace', {'expected': state, 'state': draft})",
    'print("course-added")',
  ].join('\n')], { env: { ...process.env }, encoding: 'utf8', timeout: 120000, windowsHide: true })
  record('verification course is prepared through the shared state writer', addCourse.status === 0, String(addCourse.stderr ?? '').slice(-400))

  const saveEvent = {
    id: 'verify-teaching-save-1',
    occurred_at: new Date(Date.now() - 60 * 1000).toISOString(),
    event_type: 'explanation',
    affects_current: true,
    turn_ids: ['verify-turn-1'],
    position: {
      topic: 'Verification topic',
      stage: 'explanation_completed_check_pending',
      next_step: 'verification only',
    },
  }
  const saveResult = await client.callTool({
    name: 'study_teaching_save',
    arguments: {
      course_id: 'verify-course',
      chat_id: 'session-00000000-0000-4000-8000-000000000000',
      event: saveEvent,
    },
  })
  const savePayload = parseTool(saveResult)
  record(
    'teaching-save tool saves through the real engine and verifies readback',
    saveResult.isError !== true && savePayload?.saved === true && savePayload?.readback_verified === true
      && savePayload?.event_id === saveEvent.id && typeof savePayload?.revision === 'number',
    savePayload,
  )
  const savedState = JSON.parse(readFileSync(statePath, 'utf8'))
  const savedCourse = savedState.courses.find((course) => course.id === 'verify-course')
  record(
    'teaching event is appended to the isolated state (append-only)',
    Array.isArray(savedCourse?.teaching_events) && savedCourse.teaching_events.length === 1
      && savedCourse.teaching_events[0].id === saveEvent.id,
    savedCourse?.teaching_events?.length ?? null,
  )
  const retry = await client.callTool({
    name: 'study_teaching_save',
    arguments: {
      course_id: 'verify-course',
      chat_id: 'session-00000000-0000-4000-8000-000000000000',
      event: saveEvent,
    },
  })
  const retryPayload = parseTool(retry)
  record(
    'teaching-save retry is idempotent (no duplicate event)',
    retry.isError !== true && retryPayload?.already_saved === true
      && retryPayload?.revision === savePayload?.revision,
    retryPayload,
  )

  await client.close()
  await server.close()

  const failed = results.filter((item) => !item.ok)
  console.log(`\nverify-study-tools: ${results.length - failed.length}/${results.length} checks passed`)
  if (failed.length) {
    console.error('verify-study-tools: failed checks:', failed.map((item) => item.name))
    process.exitCode = 1
  }
}

main().catch((error) => {
  console.error('verify-study-tools: fatal', error)
  process.exitCode = 1
})
