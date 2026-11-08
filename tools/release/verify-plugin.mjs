// Offline verification of the built plugin package's MCP server.
//
// Uses an isolated support directory and data directory under tmp/, so no
// student data, credentials or the user's existing study profile are touched.
// It exercises the real MCP handshake, first-use tools, the Python runtime
// bootstrap and the graceful fallback when the school connection is absent.
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

const repoRoot = dirname(dirname(dirname(fileURLToPath(import.meta.url))))
const packageDir = join(repoRoot, 'dist', 'moodle-study-assistant')
const entry = join(packageDir, 'program', 'tools', 'moodle', 'mcp-entry.mjs')
const workRoot = join(repoRoot, 'tmp', 'plugin-verify')
const supportDir = join(workRoot, 'support')
const dataDir = join(workRoot, 'data')

const results = []
const record = (name, ok, detail = null) => {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` 鈥?${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`)
}

function parseTool(result) {
  const text = result?.content?.find((item) => item.type === 'text')?.text ?? '{}'
  try {
    return JSON.parse(text)
  } catch {
    return { raw: text }
  }
}

async function main() {
  if (!existsSync(entry)) {
    console.error('verify-plugin: build the plugin first (npm run build:plugin)')
    process.exit(1)
  }
  rmSync(workRoot, { recursive: true, force: true })
  mkdirSync(supportDir, { recursive: true })
  mkdirSync(dataDir, { recursive: true })

  const client = new Client({ name: 'moodle-plugin-verify', version: '0.0.0' })
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [entry, 'mcp'],
    cwd: join(packageDir, 'program'),
    env: {
      ...process.env,
      MOODLE_STUDY_SUPPORT_DIR: supportDir,
      MOODLE_STUDY_PROGRAM_DIR: join(packageDir, 'program'),
      MOODLE_STUDY_PLUGIN_VERSION: 'verify',
      MOODLE_STUDY_DSH_HOME: join(workRoot, 'dsh-home'),
      PYTHONDONTWRITEBYTECODE: '1',
    },
    stderr: 'pipe',
  })
  await client.connect(transport)
  transport.stderr?.on('data', (chunk) => process.stderr.write(String(chunk)))

  const listed = await client.listTools()
  const names = listed.tools.map((tool) => tool.name).sort()
  record('MCP handshake + tool list', names.length > 0, { toolCount: names.length })
  record(
    'setup mode exposes first-use tools',
    ['study_setup_status', 'study_setup_location', 'study_setup_runtime', 'study_connect_school', 'study_setup_finish'].every((name) =>
      names.includes(name),
    ),
    names,
  )
  record('setup mode does not expose study tools', !names.includes('study_chapter_prep'))

  const status0 = parseTool(await client.callTool({ name: 'study_setup_status', arguments: {} }))
  record('status before setup asks for a data location', status0.next_steps?.[0]?.tool === 'study_setup_location', status0.next_steps?.[0])
  record('no personal data dir is created before the student chooses one', !existsSync(join(dataDir, 'Study')))

  const finishEarly = parseTool(await client.callTool({ name: 'study_setup_finish', arguments: {} }))
  record('finish before setup fails with a reason', finishEarly.error === 'LOCATION_REQUIRED', finishEarly.error)

  const located = parseTool(await client.callTool({ name: 'study_setup_location', arguments: { data_dir: dataDir } }))
  record('location tool accepts the student-chosen folder', located.ok === true, located.dataDir)
  record('config.local.json created in the data dir', existsSync(join(dataDir, 'config.local.json')))
  record('Study tree created in the data dir', existsSync(join(dataDir, 'Study', '_System')))
  record('program directory stays free of personal data', !existsSync(join(packageDir, 'program', 'Study')))
  record('settings.json written to the support dir', existsSync(join(supportDir, 'settings.json')))

  const status1 = parseTool(await client.callTool({ name: 'study_setup_status', arguments: {} }))
  record('status now asks for runtime installation', status1.next_steps?.[0]?.tool === 'study_setup_runtime', status1.next_steps?.[0])

  const selectEarly = parseTool(
    await client.callTool({ name: 'study_setup_select_courses', arguments: { course_ids: [1], mode: 'info_only' } }),
  )
  record('course selection is refused before listing courses', selectEarly.error === 'COURSE_LIST_REQUIRED', selectEarly.error)

  const runtime = parseTool(await client.callTool({ name: 'study_setup_runtime', arguments: {} }))
  record('runtime install starts as a background job', runtime.ok === true && Boolean(runtime.job?.id), runtime.job?.state)

  let runtimeDone = false
  let runtimeState = null
  const deadline = Date.now() + 300000
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5000))
    const status = parseTool(await client.callTool({ name: 'study_setup_status', arguments: {} }))
    runtimeState = status
    if (status.runtime?.coreReady) {
      runtimeDone = true
      break
    }
    if (status.job && status.job.state === 'failed') break
    if (status.recent_jobs?.some((job) => job.state === 'failed')) break
  }
  record('Python runtime installed from the DSH Python distribution', runtimeDone, {
    python: runtimeState?.runtime?.python ?? null,
    coreReady: runtimeState?.runtime?.coreReady ?? null,
    jobState: runtimeState?.job?.state ?? null,
    logTail: (runtimeState?.job?.log ?? '').slice(-600),
  })
  record('runtime marker stored in support dir', existsSync(join(supportDir, 'runtime.json')))

  const status2 = parseTool(await client.callTool({ name: 'study_setup_status', arguments: {} }))
  record('status after runtime asks to connect the school', status2.next_steps?.[0]?.tool === 'study_connect_school', status2.next_steps?.[0])

  const finishPending = parseTool(await client.callTool({ name: 'study_setup_finish', arguments: {} }))
  record('finish is refused while the school connection is missing', finishPending.error === 'SETUP_INCOMPLETE', finishPending.reason)

  const job = parseTool(await client.callTool({ name: 'study_job_status', arguments: {} }))
  record('job status tool responds', job.ok === true, job.running)

  const settingsFile = join(supportDir, 'settings.json')
  if (existsSync(settingsFile)) {
    const supportSettings = JSON.parse(readFileSync(settingsFile, 'utf8'))
    record('support settings never contain credentials', !/token|password|secret|wstoken/i.test(JSON.stringify(supportSettings)))
  } else {
    record('support settings never contain credentials', false, 'settings.json missing')
  }

  await client.close()

  // The package must not grow personal artifacts during verification.
  record('package audit: no Study or .private directories', !existsSync(join(packageDir, 'program', 'Study')) && !existsSync(join(packageDir, 'program', '.private')))
  const programBytes = (() => {
    let total = 0
    const walk = (current) => {
      for (const name of readdirSync(current)) {
        const path = join(current, name)
        try {
          if (statSync(path).isDirectory()) walk(path)
          else total += statSync(path).size
        } catch {
          // Removed between listing and stat.
        }
      }
    }
    walk(join(packageDir, 'program'))
    return total
  })()
  record('program size is plausible for a self-contained package', programBytes > 5 * 1024 * 1024 && programBytes < 80 * 1024 * 1024, `${Math.round((programBytes / 1024 / 1024) * 10) / 10} MB`)

  const failed = results.filter((item) => !item.ok)
  console.log(`\nverify-plugin: ${results.length - failed.length}/${results.length} checks passed`)
  if (failed.length) {
    console.error('verify-plugin: failed checks:', failed.map((item) => item.name))
    process.exitCode = 1
  }
}

main().catch((error) => {
  console.error('verify-plugin: fatal', error)
  process.exitCode = 1
})
