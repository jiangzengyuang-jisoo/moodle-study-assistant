// Entry point of the bundled Moodle Study Assistant MCP server.
//
// One tool set is registered in every situation. Local study tools depend only
// on the chosen data directory and the Python runtime; remote school tools go
// through a lazy gateway that connects when needed and reports a structured
// network/login state instead of removing the local tools. First-use, course
// change, data-location change and dependency repair tools are always present,
// so a restart at any point of setup continues from the same place.
import { existsSync, readFileSync } from 'node:fs'

import { registerChapterCheckTool } from './chapter-check-tool.mjs'
import { createMcpChapterContext } from './mcp-workspace.mjs'
import { serve } from './server.mjs'
import { registerSetupTools } from './plugin/setup-tools.mjs'
import { registerStudyTools } from './plugin/study-tools.mjs'
import { venvPython } from './plugin/python-runtime.mjs'
import { createRemoteGateway } from './plugin/remote.mjs'
import { readConfig, readSettings, readState, statePath, tokenPath } from './plugin/settings.mjs'

const settings = readSettings()

// Child processes (Python engine, Node self-spawns) inherit these: they are
// not sensitive names, so the MCP client's env scrubbing keeps them.
if (settings?.dataDir) process.env.STUDY_DATA_ROOT = settings.dataDir
const python = venvPython()
if (python) process.env.STUDY_PYTHON = python
process.env.STUDY_NODE = process.execPath
process.env.ELECTRON_RUN_AS_NODE = '1'

let reconnectRequested = false
function requestReconnect(reason) {
  if (reconnectRequested) return
  reconnectRequested = true
  process.stderr.write(`[moodle-study] reconnect requested: ${reason ?? 'unknown'}\n`)
  // Give the current tool response time to flush before the MCP client
  // reconnects; the client respawns this server with the same configuration.
  setTimeout(() => process.exit(0), 1500)
}

function readStateFile(dataDir) {
  return readState(dataDir) ?? JSON.parse(readFileSync(statePath(dataDir), 'utf8').replace(/^\uFEFF/, ''))
}

function buildChapterContext(gateway) {
  const current = readSettings()
  if (!current?.dataDir) return null
  const config = readConfig(current.dataDir)
  if (!config?.moodle_url) return null
  if (!existsSync(tokenPath(current.dataDir))) return null
  try {
    const credential = readFileSync(tokenPath(current.dataDir))
    return createMcpChapterContext({
      root: current.dataDir,
      loadState: () => readStateFile(current.dataDir),
      client: gateway,
      config,
      credential,
    })
  } catch {
    return null
  }
}

async function main() {
  const gateway = createRemoteGateway()
  const context = { remote: gateway, requestReconnect }
  const chapterContext = buildChapterContext(gateway)

  const registerExtras = (server) => {
    registerSetupTools(server, context)
    registerStudyTools(server, context)
    if (chapterContext) registerChapterCheckTool(server, chapterContext)
  }

  // The remote gateway is passed as the client facade: every remote tool
  // resolves the school connection lazily on call, so registering them does
  // not require the school to be reachable at startup.
  const server = await serve(gateway, undefined, null, registerExtras)
  return server
}

main().catch((error) => {
  process.stderr.write(`[moodle-study] fatal: ${error?.message ?? error}\n`)
  process.exitCode = 1
})
