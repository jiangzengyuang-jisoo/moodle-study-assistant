// Entry point of the bundled Moodle Study Assistant MCP server.
//
// Two modes:
//   * setup  — no complete student setup yet (data location, Python runtime or
//              school connection missing). Exposes the first-use tools only,
//              so setup never depends on a working school connection.
//   * study  — full local study tools plus the same setup/recovery tools.
//
// The module derives every path from the plugin support directory, so plugin
// upgrades (new package directory) and data-directory changes are picked up on
// the next connection without editing any configuration file.
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'

import { ReadOnlyClient } from './core.mjs'
import { createMcpChapterContext } from './mcp-workspace.mjs'
import { serve } from './server.mjs'
import { decryptWindowsToken } from './windows-token.mjs'
import { registerSetupTools } from './plugin/setup-tools.mjs'
import { registerStudyTools } from './plugin/study-tools.mjs'
import { venvPython } from './plugin/python-runtime.mjs'
import {
  readConfig,
  readSettings,
  statePath,
  studyReadiness,
  tokenPath,
} from './plugin/settings.mjs'

const settings = readSettings()

// Child processes (Python engine, Node self-spawns) inherit these: they are
// not sensitive names, so the MCP client's env scrubbing keeps them.
if (settings?.dataDir) process.env.STUDY_DATA_ROOT = settings.dataDir
const python = venvPython()
if (python) process.env.STUDY_PYTHON = python
process.env.STUDY_NODE = process.execPath
process.env.ELECTRON_RUN_AS_NODE = '1'

function readStateFile(dataDir) {
  const path = statePath(dataDir)
  if (!existsSync(path)) throw new Error('state_missing')
  return JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''))
}

async function connectStudy(dataDir) {
  const config = readConfig(dataDir)
  if (!config?.moodle_url) throw new Error('config_missing')
  process.env.TZ = config.timezone || 'Asia/Hong_Kong'
  const credential = readFileSync(tokenPath(dataDir))
  const client = await new ReadOnlyClient(config.moodle_url, decryptWindowsToken(credential)).connect()
  return { client, config, credential }
}

async function serveStudy(dataDir) {
  const connection = await connectStudy(dataDir)
  const context = createMcpChapterContext({
    root: dataDir,
    loadState: () => readStateFile(dataDir),
    client: connection.client,
    config: connection.config,
    credential: connection.credential,
  })
  context.beforeCheck()
  return serve(connection.client, undefined, context, (server) => {
    registerSetupTools(server, { mode: 'study' })
    registerStudyTools(server)
  })
}

async function serveSetup() {
  const server = new McpServer({
    name: 'moodle-study-assistant-setup',
    version: process.env.MOODLE_STUDY_PLUGIN_VERSION ?? '0.0.0',
  })
  registerSetupTools(server, { mode: 'setup' })
  await server.connect(new StdioServerTransport())
  return server
}

async function main() {
  const readiness = studyReadiness(settings)
  if (readiness.ready) {
    try {
      await serveStudy(settings.dataDir)
      return
    } catch (error) {
      // A broken or expired school connection must not remove the recovery
      // tools: fall back to setup mode so the student can reconnect.
      process.stderr.write(
        `[moodle-study] study mode unavailable (${error?.message ?? 'connection_failed'}); serving setup tools\n`,
      )
    }
  }
  await serveSetup()
}

main().catch((error) => {
  process.stderr.write(`[moodle-study] fatal: ${error?.message ?? error}\n`)
  process.exitCode = 1
})
