// Offline verification of the study-mode tool set that is registered by the
// plugin's MCP server. It mounts the real tool registrations on an in-memory
// MCP transport, so no school connection and no student data are involved.
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

const settings = JSON.parse(readFileSync(join(supportDir, 'settings.json'), 'utf8'))
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
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` 鈥?${JSON.stringify(detail)}` : ''}`)
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

  const server = new McpServer({ name: 'study-tools-verify', version: '0.0.0' })
  registerSetupTools(server, { mode: 'study' })
  registerStudyTools(server)
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
    'study_teaching_event',
    'study_local_status',
    'study_refresh',
    'study_job_status',
  ]
  const recoveryTools = ['study_setup_status', 'study_setup_finish', 'study_setup_sync', 'study_connect_school']
  record('study mode registers the local study tool set', studyTools.every((name) => names.includes(name)), names.filter((name) => name.startsWith('study_')))
  record('study mode keeps the recovery entry points', recoveryTools.every((name) => names.includes(name)))
  record('study mode registers the strict chapter check tool', names.includes('study_check_chapter'))
  record('full first-use wizard is not duplicated in study mode', !names.includes('study_setup_location'))

  const contextResult = await client.callTool({ name: 'study_local_context', arguments: {} })
  const contextText = contextResult.content.find((item) => item.type === 'text')?.text ?? ''
  let contextPayload = null
  try {
    contextPayload = JSON.parse(contextText)
  } catch {
    contextPayload = null
  }
  record(
    'local context tool runs the real engine offline',
    contextResult.isError !== true && contextPayload !== null,
    contextPayload ? Object.keys(contextPayload).slice(0, 8) : contextText.slice(0, 300),
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
    teaching.content?.[0]?.text?.slice(0, 200),
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
