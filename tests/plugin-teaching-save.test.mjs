// Offline contract tests for the plugin's new teaching-save tool and for the
// data-directory protection in resolveDataDir.
//
// Everything runs against synthetic data: a throwaway support directory, a
// throwaway program copy with the repository's real tools/study, and a
// throwaway data directory with a hand-written state.json. The tool executes
// the repository's real library.py through a real private venv (the checkout's
// .venv, linked into the isolated support directory), so the engine's lock,
// transaction, readback and validation paths are the production ones. No school
// connection is opened and the student's real Study/, .private/ or
// config.local.json files are never read. All temporary directories live under
// the git-ignored repository tmp/ because the spawned Python engine may be
// confined to the workspace by the host sandbox.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { homedir } from 'node:os'
import { join, resolve } from 'node:path'
import { after, test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'

const repoRoot = fileURLToPath(new URL('..', import.meta.url))

// Never inherit the real Harness profile / chat identity / data root or a
// stale diagnostic seam: this suite only touches throwaway directories.
for (const inherited of [
  'DSH_PROFILE_DIR',
  'DSH_PROFILE',
  'DSH_SESSION_ID',
  'STUDY_CHAT_ID',
  'STUDY_DATA_ROOT',
  'MOODLE_STUDY_CORE_IMPORT_OVERRIDE',
  'MOODLE_STUDY_LOGIN_IMPORT_OVERRIDE',
]) {
  delete process.env[inherited]
}
delete process.env.DSH_HOME

const tmpRoot = join(repoRoot, 'tmp')
mkdirSync(tmpRoot, { recursive: true })
const runRoot = mkdtempSync(join(tmpRoot, 'plugin-teaching-save-'))
const support = join(runRoot, 'support')
const program = join(runRoot, 'program')
const dataDir = join(runRoot, 'data')
mkdirSync(join(support), { recursive: true })
mkdirSync(join(program, 'tools'), { recursive: true })
mkdirSync(join(dataDir, 'Study', '_System'), { recursive: true })
process.env.MOODLE_STUDY_SUPPORT_DIR = support
process.env.MOODLE_STUDY_PROGRAM_DIR = program
process.env.MOODLE_STUDY_DSH_HOME = join(runRoot, 'dsh-home')

// The real engine must be copied into the isolated program directory. Only the
// files on the teaching-save import path are copied (the entry point, the
// shared state transaction, the evidence guards and learning_library), so the
// suite also cannot be disturbed by other engine tests creating/removing
// temporary folders inside tools/study concurrently. A vanished entry is
// skipped instead of failing the whole suite.
const ENGINE_FILES = [
  'library.py',
  'teaching_progress.py',
  'state_io.py',
  'cursor_evidence.py',
  'teaching_evidence.py',
  'course_links.py',
  'evidence_bindings.py',
]

function copyProgramTree(source, target) {
  mkdirSync(target, { recursive: true })
  let entries = []
  try {
    entries = readdirSync(source, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of entries) {
    if (entry.name === '__pycache__') continue
    const from = join(source, entry.name)
    const to = join(target, entry.name)
    let info
    try {
      info = statSync(from)
    } catch {
      continue // A concurrent test removed its temporary folder.
    }
    if (info.isDirectory()) copyProgramTree(from, to)
    else if (info.isFile()) {
      try {
        copyFileSync(from, to)
      } catch {
        // Same concurrent-removal race; the file is not needed by this suite.
      }
    }
  }
}

const programStudyDir = join(program, 'tools', 'study')
mkdirSync(programStudyDir, { recursive: true })
for (const name of ENGINE_FILES) {
  copyFileSync(join(repoRoot, 'tools', 'study', name), join(programStudyDir, name))
}
copyProgramTree(join(repoRoot, 'tools', 'study', 'learning_library'), join(programStudyDir, 'learning_library'))

// The plugin's private venv is only discovered by path, so link the checkout's
// real .venv into the throwaway support directory (a fresh venv is the offline
// fallback when linking is not permitted). The engine teaching-save path needs
// no third-party dependency, but it is still executed by the repository's real
// Python interpreter.
const repoVenvPython = [
  join(repoRoot, '.venv', 'Scripts', 'python.exe'),
  join(repoRoot, '.venv', 'bin', 'python'),
].find((candidate) => {
  try {
    return statSync(candidate).isFile()
  } catch {
    return false
  }
})
function prepareVenv() {
  const target = join(support, 'venv')
  try {
    symlinkSync(join(repoRoot, '.venv'), target, process.platform === 'win32' ? 'junction' : 'dir')
    return
  } catch {
    // Fall back to creating a real private venv below.
  }
  const basePython = process.env.MOODLE_STUDY_TEST_BASE_PYTHON || repoVenvPython || 'python'
  const created = spawnSync(basePython, ['-m', 'venv', target], {
    encoding: 'utf8',
    windowsHide: true,
    timeout: 180000,
  })
  assert.equal(created.status, 0, `无法创建测试用 venv：${created.stderr || created.error?.message}`)
}
prepareVenv()

const { readSettings, resolveDataDir, statePath, writeSettings } = await import('../tools/moodle/plugin/settings.mjs')
const { registerStudyTools } = await import('../tools/moodle/plugin/study-tools.mjs')

after(() => {
  // Remove the venv link first so a cleanup can never walk into the checkout's
  // real .venv through the junction.
  try {
    rmSync(join(support, 'venv'), { recursive: true, force: true })
  } catch {
    // A real venv may still be briefly locked on Windows; the parent removal
    // below is best effort and tmp/ is git-ignored.
  }
  rmSync(runRoot, { recursive: true, force: true, maxRetries: 3 })
})

// Synthetic state: one taught course, one untouched course, and legacy
// collections that a teaching-save must never rewrite.
const LEGACY_NOTE = 'original synthetic legacy note'
const LEGACY_STATE = {
  revision: 7,
  settings: {},
  courses: [
    {
      id: 'moodle-201',
      name: 'Synthetic Course',
      shortname: 'SYN201',
      current_topic: 'Assignment chapter 2',
      teaching_cursor: {
        topic: 'Chapter 3',
        chapter: '3',
        updated_at: '2026-01-01T00:00:00Z',
        next_step: 'practice',
        stage: 'explained',
      },
      mastery: { legacy: 'unchanged' },
    },
    { id: 'moodle-999', name: 'Other untouched course' },
  ],
  materials: [],
  mistakes: [{ id: 'M1', user_answer: 'original mistake' }],
  review_attempts: [{ id: 'R1', user_answer: 'original answer' }],
  review_queue: [],
  fsrs: { legacy: 'unchanged' },
  notes: [{ id: 'NOTE1', text: LEGACY_NOTE }],
}

const stateFile = statePath(dataDir)

function resetState() {
  for (const name of ['state.json', 'state.previous.json', '.workspace.lock']) {
    rmSync(join(dataDir, 'Study', '_System', name), { force: true })
  }
  rmSync(join(dataDir, 'Study', '_System', 'Runtime'), { recursive: true, force: true })
  writeFileSync(stateFile, JSON.stringify(LEGACY_STATE, null, 2) + '\n', 'utf8')
}

function readStateFile() {
  return JSON.parse(readFileSync(stateFile, 'utf8').replace(/^\uFEFF/, ''))
}

function readStateBytes() {
  return readFileSync(stateFile)
}

resetState()
writeSettings({
  dataDir,
  schoolUrl: 'https://school.example',
  timezone: 'Asia/Hong_Kong',
  stage: 'ready',
})

// Capture the registrations exactly as the MCP server would, then call the
// real handler directly (the SDK only wraps these handlers).
const tools = new Map()
registerStudyTools(
  {
    registerTool(name, definition, handler) {
      tools.set(name, { definition, handler })
      return { name }
    },
  },
  {},
)

const SAVE_TOOL = 'study_teaching_save'
const LEGACY_TOOL = 'study_teaching_event'
const COURSE = 'moodle-201'
const CHAT = 'chat-synthetic'

// Actual event time has to be in the past for the engine; compute it instead of
// hard-coding a date so the fixture also works with a different host clock.
function recentIso() {
  return new Date(Date.now() - 60 * 60 * 1000).toISOString()
}

function teachingEvent(overrides = {}) {
  return {
    id: 'evt-synthetic-1',
    occurred_at: recentIso(),
    event_type: 'explanation',
    affects_current: true,
    turn_ids: ['turn-1', 'turn-2'],
    position: {
      topic: 'Synthetic integration topic',
      chapter: '5',
      section: '5.1',
      stage: 'awaiting_independent_attempt',
      next_step: 'Try a new synthetic integral',
      pending_question: { id: 'Q1', prompt: 'Synthetic integral question', status: 'pending' },
    },
    ...overrides,
  }
}

function saveArgs(event, extra = {}) {
  return { course_id: COURSE, chat_id: CHAT, event, ...extra }
}

async function callSave(args) {
  const result = await tools.get(SAVE_TOOL).handler(args)
  const text = (result.content ?? [])
    .filter((item) => item.type === 'text')
    .map((item) => item.text)
    .join('\n')
  let payload = null
  try {
    payload = JSON.parse(text)
  } catch {
    payload = { raw: text }
  }
  return { isError: Boolean(result.isError), payload }
}

test('study_teaching_save is registered next to the unchanged legacy tool', () => {
  assert.ok(tools.has(SAVE_TOOL), 'new tool must be registered in every server mode')
  assert.ok(tools.has(LEGACY_TOOL), 'study_teaching_event must stay registered for legacy flows')
  assert.notEqual(tools.get(SAVE_TOOL).handler, tools.get(LEGACY_TOOL).handler)
  const definition = tools.get(SAVE_TOOL).definition
  assert.equal(definition.annotations.idempotentHint, true)
  assert.equal(definition.annotations.openWorldHint, false)

  const parsed = definition.inputSchema.parse(saveArgs({ ...teachingEvent(), mastery: 'independent' }))
  assert.equal(parsed.course_id, COURSE)
  assert.equal(parsed.chat_id, CHAT)
  assert.equal(parsed.expected_revision, undefined, 'expected_revision is optional')
  assert.equal(parsed.event.id, 'evt-synthetic-1')
  assert.equal(parsed.event.mastery, undefined, 'unknown event keys cannot rewrite state')
  assert.equal(
    definition.inputSchema.parse(saveArgs(teachingEvent(), { expected_revision: 7 })).expected_revision,
    7,
  )
  // The caller must provide the stable event id itself; nothing is generated.
  const withoutId = { ...teachingEvent() }
  delete withoutId.id
  assert.throws(() => definition.inputSchema.parse(saveArgs(withoutId)), /required|id/i)
})

test('the tool is served through the real MCP SDK with a valid schema', async () => {
  resetState()
  const server = new McpServer({ name: 'study-teaching-save-test', version: '0.0.0' })
  registerStudyTools(server, {})
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair()
  const client = new Client({ name: 'study-teaching-save-client', version: '0.0.0' })
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)])
  try {
    const listed = await client.listTools()
    const names = listed.tools.map((tool) => tool.name)
    assert.ok(names.includes(SAVE_TOOL), 'the MCP handshake must expose study_teaching_save')
    assert.ok(names.includes(LEGACY_TOOL), 'the legacy tool stays exposed')
    const definition = listed.tools.find((tool) => tool.name === SAVE_TOOL)
    assert.deepEqual(definition.inputSchema.required, ['course_id', 'chat_id', 'event'])
    assert.ok(definition.inputSchema.properties.expected_revision)

    const event = teachingEvent({ id: 'evt-mcp-1' })
    const result = await client.callTool({ name: SAVE_TOOL, arguments: saveArgs(event) })
    assert.notEqual(result.isError, true)
    const payload = JSON.parse(result.content[0].text)
    assert.equal(payload.saved, true)
    assert.equal(payload.readback_verified, true)
    assert.equal(payload.revision, 8)

    // A stale revision is still a real error through the MCP path.
    const stale = await client.callTool({
      name: SAVE_TOOL,
      arguments: saveArgs(teachingEvent({ id: 'evt-mcp-2' }), { expected_revision: 7 }),
    })
    assert.equal(stale.isError, true)
    assert.match(String(JSON.parse(stale.content[0].text).error), /状态已更新/)
  } finally {
    await client.close()
    await server.close()
  }
})

test('first save appends one event through the real engine and verifies readback', async () => {
  resetState()
  const event = teachingEvent()
  const result = await callSave(saveArgs(event))
  assert.equal(result.isError, false, JSON.stringify(result.payload))

  const payload = result.payload
  assert.equal(payload.saved, true)
  assert.equal(payload.already_saved, false)
  assert.equal(payload.readback_verified, true)
  assert.equal(payload.revision, 8, 'the state revision advances by one')
  assert.equal(payload.event_id, event.id)
  assert.equal(payload.event_path, '/courses/0/teaching_events/0')
  assert.equal(payload.active_advanced, true)
  assert.equal(payload.views_need_refresh, true)
  assert.equal(payload.mastery_updated, false)
  assert.equal(payload.network_started, false)
  assert.equal(payload.original_evidence_preserved, true)
  assert.equal(payload.current_progress.record.chapter, '5')

  const state = readStateFile()
  assert.equal(state.revision, 8)
  assert.equal(state.courses[0].teaching_events.length, 1)
  const saved = state.courses[0].teaching_events[0]
  assert.equal(saved.id, event.id)
  assert.equal(saved.occurred_at, event.occurred_at)
  assert.equal(saved.event_type, 'explanation')
  assert.equal(saved.affects_current, true)
  assert.deepEqual(saved.source, { kind: 'chat', thread_id: CHAT, turn_ids: ['turn-1', 'turn-2'] })
  assert.deepEqual(saved.position, event.position)
  assert.equal(typeof saved.recorded_at, 'string')
  assert.equal(typeof saved.request_sha256, 'string')
  assert.equal(saved.expected_revision, undefined, 'the concurrency token is not persisted')

  // Append-only: the previous teaching position, the other course and every
  // other evidence collection stay byte-for-value unchanged.
  assert.deepEqual(state.courses[0].teaching_cursor, LEGACY_STATE.courses[0].teaching_cursor)
  assert.deepEqual(state.courses[0].mastery, LEGACY_STATE.courses[0].mastery)
  assert.deepEqual(state.courses[1], LEGACY_STATE.courses[1])
  for (const key of ['materials', 'mistakes', 'review_attempts', 'review_queue', 'fsrs', 'notes']) {
    assert.deepEqual(state[key], LEGACY_STATE[key], `${key} must be preserved`)
  }

  // The transaction keeps a full previous-state backup.
  const previous = JSON.parse(readFileSync(join(dataDir, 'Study', '_System', 'state.previous.json'), 'utf8'))
  assert.deepEqual(previous, LEGACY_STATE)
})

test('omitting expected_revision uses the current state revision for every new event', async () => {
  resetState()
  let result = await callSave(saveArgs(teachingEvent({ id: 'evt-no-rev-1' })))
  assert.equal(result.isError, false, JSON.stringify(result.payload))
  assert.equal(result.payload.revision, 8)
  result = await callSave(saveArgs(teachingEvent({ id: 'evt-no-rev-2' })))
  assert.equal(result.isError, false, JSON.stringify(result.payload))
  assert.equal(result.payload.revision, 9)
  result = await callSave(saveArgs(teachingEvent({ id: 'evt-no-rev-3' })))
  assert.equal(result.isError, false, JSON.stringify(result.payload))
  assert.equal(result.payload.revision, 10)
  assert.equal(readStateFile().revision, 10)
})

test('retrying the same event id and content is idempotent and performs no write', async () => {
  resetState()
  const event = teachingEvent()
  const first = await callSave(saveArgs(event))
  assert.equal(first.isError, false, JSON.stringify(first.payload))
  assert.equal(first.payload.revision, 8)
  const snapshot = readStateBytes()

  for (const expectedRevision of [undefined, 8]) {
    const retryArgs = saveArgs(event, expectedRevision === undefined ? {} : { expected_revision: expectedRevision })
    const retry = await callSave(retryArgs)
    assert.equal(retry.isError, false, JSON.stringify(retry.payload))
    assert.equal(retry.payload.saved, true)
    assert.equal(retry.payload.already_saved, true, 'the duplicate must not append a second event')
    assert.equal(retry.payload.readback_verified, true)
    assert.equal(retry.payload.revision, 8, 'a duplicate leaves the revision unchanged')
    assert.equal(retry.payload.active_advanced, false)
    assert.deepEqual(readStateBytes(), snapshot, 'a duplicate must not rewrite the state file')
  }
  assert.equal(readStateFile().courses[0].teaching_events.length, 1)
})

test('the same event id with different content is rejected without touching state', async () => {
  resetState()
  const event = teachingEvent()
  const first = await callSave(saveArgs(event))
  assert.equal(first.isError, false, JSON.stringify(first.payload))
  const snapshot = readStateBytes()

  const changed = {
    ...teachingEvent({ id: event.id }),
    occurred_at: event.occurred_at,
    position: { ...event.position, topic: 'A different synthetic topic' },
  }
  const result = await callSave(saveArgs(changed))
  assert.equal(result.isError, true)
  assert.equal(result.payload.type, 'ValueError')
  assert.match(String(result.payload.error), /different content/i)
  assert.deepEqual(readStateBytes(), snapshot)
  assert.equal(readStateFile().courses[0].teaching_events.length, 1)
})

test('stale or concurrently outdated expected_revision is rejected, bytes unchanged', async () => {
  resetState()
  const first = await callSave(saveArgs(teachingEvent()))
  assert.equal(first.isError, false, JSON.stringify(first.payload))
  assert.equal(first.payload.revision, 8)
  const snapshot = readStateBytes()

  // A caller that read the old revision must be refused, not silently merged.
  const stale = await callSave(saveArgs(teachingEvent({ id: 'evt-stale-1' }), { expected_revision: 7 }))
  assert.equal(stale.isError, true)
  assert.equal(stale.payload.type, 'ValueError')
  assert.match(String(stale.payload.error), /状态已更新/)
  assert.deepEqual(readStateBytes(), snapshot)
  assert.equal(readStateFile().courses[0].teaching_events.length, 1)

  // Two writers that both read revision 8: the first appends, the second is
  // rejected by the engine's workspace transaction instead of overwriting.
  const writerOne = await callSave(saveArgs(teachingEvent({ id: 'evt-race-1' }), { expected_revision: 8 }))
  assert.equal(writerOne.isError, false, JSON.stringify(writerOne.payload))
  assert.equal(writerOne.payload.revision, 9)
  const afterFirstWriter = readStateBytes()
  const writerTwo = await callSave(saveArgs(teachingEvent({ id: 'evt-race-2' }), { expected_revision: 8 }))
  assert.equal(writerTwo.isError, true, 'the outdated concurrent writer must be rejected')
  assert.match(String(writerTwo.payload.error), /状态已更新/)
  assert.deepEqual(readStateBytes(), afterFirstWriter)
  assert.equal(readStateFile().revision, 9)
  assert.equal(readStateFile().courses[0].teaching_events.length, 2)
})

test('real original answers in attempts are appended verbatim with help conditions', async () => {
  resetState()
  const event = teachingEvent({ id: 'evt-attempt-1', event_type: 'attempt' })
  event.position = {
    ...event.position,
    pending_question: { id: 'Q1', prompt: 'Synthetic integral question', status: 'answered' },
  }
  event.attempts = [
    {
      id: 'ATTEMPT-1',
      question: 'Evaluate the synthetic integral',
      user_answer: 'My original uncertain answer',
      conditions: { independent: null, assisted: true, answer_exposed: null },
      topic: 'Synthetic integration',
      answered_at: null,
      source_turn_id: 'turn-2',
    },
  ]
  const result = await callSave(saveArgs(event))
  assert.equal(result.isError, false, JSON.stringify(result.payload))
  assert.equal(result.payload.saved, true)
  assert.equal(result.payload.readback_verified, true)
  assert.equal(result.payload.original_evidence_preserved, true)
  assert.equal(result.payload.mastery_updated, false)

  const saved = readStateFile().courses[0].teaching_events[0]
  assert.deepEqual(saved.attempts, event.attempts, 'the original answer and its conditions are stored verbatim')
  assert.equal(saved.position.pending_question.status, 'answered')
  assert.equal(saved.attempts[0].rating, undefined, 'no rating or mastery is invented')
  assert.deepEqual(readStateFile().courses[0].mastery, LEGACY_STATE.courses[0].mastery)
})

test('invalid event fields are rejected by the engine and nothing is written', async () => {
  resetState()
  const snapshot = readStateBytes()
  const result = await callSave(saveArgs(teachingEvent({ id: 'evt-bad-1', event_type: 'planned' })))
  assert.equal(result.isError, true)
  assert.match(String(result.payload.error), /event_type must describe an actual teaching event/)
  assert.deepEqual(readStateBytes(), snapshot)
})

test('a save without an initialized profile returns the real precondition failure', async () => {
  resetState()
  const emptyDataDir = mkdtempSync(join(tmpRoot, 'plugin-teaching-save-empty-'))
  const settings = readSettings()
  try {
    writeSettings({ dataDir: emptyDataDir })
    const result = await callSave(saveArgs(teachingEvent()))
    assert.equal(result.isError, true, 'nothing may be reported as saved without a local profile')
    assert.equal(result.payload.error, 'LOCAL_STATE_MISSING')
    assert.equal(result.payload.next_step, 'study_setup_status')
    assert.equal(result.payload.saved, undefined)
    assert.equal(existsSync(join(emptyDataDir, 'Study')), false, 'the tool must not create a blank profile')
  } finally {
    writeSettings({ dataDir: settings.dataDir })
    rmSync(emptyDataDir, { recursive: true, force: true })
  }
})

test('resolveDataDir refuses program/support directories in both directions', () => {
  // Existing absolute-path and ~ expansion validation is preserved.
  assert.throws(() => resolveDataDir(''), /绝对路径/)
  assert.throws(() => resolveDataDir('   '), /绝对路径/)
  assert.throws(() => resolveDataDir('relative/folder'), /绝对路径/)
  assert.equal(resolveDataDir(dataDir), resolve(dataDir))

  // Normal independent absolute folders still work, including the default
  // style location and a student-selected sibling folder.
  const documentsStyle = join(homedir(), 'Documents', 'MoodleStudy')
  assert.equal(resolveDataDir(documentsStyle), resolve(documentsStyle))
  assert.equal(resolveDataDir(`  ${join(runRoot, 'student-folder')}  `), resolve(join(runRoot, 'student-folder')))
  assert.equal(resolveDataDir('~/Documents/MoodleStudy'), resolve(join(homedir(), 'Documents', 'MoodleStudy')))

  // The plugin program directory itself and anything inside it. A child whose
  // name starts with ".." must not slip past the containment check.
  for (const value of [program, join(program, 'tools'), join(program, 'tools', 'study'), join(program, '..hidden')]) {
    assert.throws(() => resolveDataDir(value), /插件程序目录/, `expected refusal for ${value}`)
  }
  // The runtime support directory itself and anything inside it.
  for (const value of [support, join(support, 'venv'), join(support, 'logs')]) {
    assert.throws(() => resolveDataDir(value), /运行支持目录/, `expected refusal for ${value}`)
  }
  // A folder that CONTAINS the protected directories is refused too.
  for (const value of [runRoot, resolve(program, '..'), resolve(support, '..')]) {
    assert.throws(
      () => resolveDataDir(value),
      /不能包含插件程序目录|不能包含运行支持目录/,
      `expected refusal for containing folder ${value}`,
    )
  }
  // The support-directory branch reports its own kind of guidance. Support is
  // located from the environment, so point it at a nested fixture for a
  // synchronous check and restore it afterwards (no save runs in between).
  const nestedRoot = mkdtempSync(join(tmpRoot, 'plugin-teaching-save-nested-'))
  const nestedSupport = join(nestedRoot, 'a', 'b', 'support')
  mkdirSync(nestedSupport, { recursive: true })
  const previousSupport = process.env.MOODLE_STUDY_SUPPORT_DIR
  try {
    process.env.MOODLE_STUDY_SUPPORT_DIR = nestedSupport
    assert.throws(() => resolveDataDir(nestedSupport), /运行支持目录/)
    assert.throws(() => resolveDataDir(join(nestedSupport, 'venv')), /运行支持目录/)
    assert.throws(() => resolveDataDir(join(nestedRoot, 'a')), /不能包含运行支持目录/)
  } finally {
    process.env.MOODLE_STUDY_SUPPORT_DIR = previousSupport
    rmSync(nestedRoot, { recursive: true, force: true })
  }
  // Windows paths cannot bypass the guard with case or separators.
  if (process.platform === 'win32') {
    for (const value of [program.toUpperCase(), program.replace(/\\/g, '/'), `${program}\\`]) {
      assert.throws(() => resolveDataDir(value), /插件程序目录/, `expected refusal for ${value}`)
    }
    assert.throws(() => resolveDataDir(support.toUpperCase()), /运行支持目录/)
  }
  // An installed bundle ships program files under <package>/program, but the
  // whole package directory is replaced on upgrade/uninstall. A data folder
  // anywhere inside the package (not only under program/) must be refused.
  const installedRoot = mkdtempSync(join(tmpRoot, 'plugin-teaching-save-installed-'))
  const packageDir = join(installedRoot, 'dsh-moodle-study')
  const packagedProgram = join(packageDir, 'program')
  mkdirSync(packagedProgram, { recursive: true })
  writeFileSync(
    join(packageDir, 'package.json'),
    JSON.stringify({ name: 'dsh-moodle-study', version: '0.0.0-test', type: 'module' }),
  )
  const previousProgram = process.env.MOODLE_STUDY_PROGRAM_DIR
  try {
    process.env.MOODLE_STUDY_PROGRAM_DIR = packagedProgram
    for (const value of [
      packageDir,
      join(packageDir, 'data'),
      join(packageDir, 'resources'),
      join(packageDir, 'node_modules'),
      join(packageDir, 'docs'),
    ]) {
      assert.throws(() => resolveDataDir(value), /插件安装包目录/, `expected package refusal for ${value}`)
    }
    // A folder containing the package is refused too, with actionable advice.
    assert.throws(() => resolveDataDir(installedRoot), /不能包含插件安装包目录/)
    assert.throws(() => resolveDataDir(packageDir), (error) => {
      assert.match(error.message, /独立文件夹/)
      assert.match(error.message, /Documents[\\/]MoodleStudy/)
      return true
    })
    // An independent sibling folder stays valid.
    assert.equal(resolveDataDir(join(installedRoot, 'student')), resolve(join(installedRoot, 'student')))
  } finally {
    if (previousProgram === undefined) delete process.env.MOODLE_STUDY_PROGRAM_DIR
    else process.env.MOODLE_STUDY_PROGRAM_DIR = previousProgram
    rmSync(installedRoot, { recursive: true, force: true })
  }
  // The refusal is actionable Chinese guidance, not a generic message.
  assert.throws(() => resolveDataDir(program), (error) => {
    assert.match(error.message, /独立文件夹/)
    assert.match(error.message, /Documents[\\/]MoodleStudy/)
    return true
  })
})
