// Recovery-flow tests for the plugin setup/status logic.
//
// These run without a school connection and without the real user profile:
// temporary support and data directories are configured through the plugin's
// environment overrides, and sync evidence is produced from real-shaped
// artifact files (collection snapshot, material-library report).
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, test } from 'node:test'
import { fileURLToPath } from 'node:url'

const repoRoot = fileURLToPath(new URL('..', import.meta.url))
const support = mkdtempSync(join(tmpdir(), 'moodle-plugin-recovery-'))
// Runtime readiness is graded (interpreter + pip + imports, never a file-only
// marker), so these setup tests need a real private venv. Point the program dir
// at a throwaway copy with an empty requirements-core.txt: the runtime chain
// then has nothing to install from the network and the tests stay offline.
const program = mkdtempSync(join(tmpdir(), 'moodle-plugin-program-'))
writeFileSync(join(program, 'requirements-core.txt'), '', 'utf8')
const basePython =
  [
    process.env.MOODLE_STUDY_TEST_BASE_PYTHON,
    join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    join(repoRoot, '.venv', 'bin', 'python'),
  ]
    .filter(Boolean)
    .find((candidate) => {
      try {
        return statSync(candidate).isFile()
      } catch {
        return false
      }
    }) ?? 'python'
process.env.MOODLE_STUDY_SUPPORT_DIR = support
process.env.MOODLE_STUDY_PROGRAM_DIR = program
process.env.MOODLE_STUDY_BASE_PYTHON = basePython

const { evaluateSyncEvidence } = await import('../tools/moodle/plugin/sync-evidence.mjs')
const { nextSteps, collectStatus, finishHandler, locationHandler, remoteFailure } = await import(
  '../tools/moodle/plugin/onboarding-tools.mjs'
)
const { RemoteError } = await import('../tools/moodle/plugin/remote.mjs')
const { readSettings } = await import('../tools/moodle/plugin/settings.mjs')
const { coreRuntimeRecord } = await import('../tools/moodle/plugin/python-runtime.mjs')
const { registerSetupTools } = await import('../tools/moodle/plugin/setup-tools.mjs')
const { registerStudyTools } = await import('../tools/moodle/plugin/study-tools.mjs')

after(() => {
  rmSync(support, { recursive: true, force: true, maxRetries: 3 })
  rmSync(program, { recursive: true, force: true, maxRetries: 3 })
})

const ACCOUNT = { site: 'https://school.example', user_id: 12345 }

function writeJson(path, value) {
  mkdirSync(join(path, '..'), { recursive: true })
  writeFileSync(path, JSON.stringify(value, null, 2) + '\n', 'utf8')
}

function ensureRuntimeFixture() {
  const venvDir = join(support, 'venv')
  if (!existsSync(join(venvDir, 'Scripts', 'python.exe')) && !existsSync(join(venvDir, 'bin', 'python'))) {
    const created = spawnSync(basePython, ['-m', 'venv', venvDir], {
      encoding: 'utf8',
      windowsHide: true,
      timeout: 180000,
    })
    assert.equal(created.status, 0, `无法创建测试用 venv：${created.stderr || created.error?.message}`)
  }
  writeJson(join(support, 'runtime.json'), { core: coreRuntimeRecord(basePython) })
}

ensureRuntimeFixture()

function makeDataDir({ courses = [], grants = {}, account = ACCOUNT, stage = 'courses_selected' } = {}) {
  const dataDir = mkdtempSync(join(tmpdir(), 'moodle-plugin-data-'))
  mkdirSync(join(dataDir, 'Study', '_System'), { recursive: true })
  mkdirSync(join(dataDir, '.private'), { recursive: true })
  writeJson(join(dataDir, 'config.local.json'), { moodle_url: ACCOUNT.site, timezone: 'Asia/Hong_Kong' })
  const state = {
    schema_version: 1,
    revision: 3,
    settings: {
      profile_account_scope: { ...account, saved_at: '2026-11-01T00:00:00.000Z' },
      course_library_authorization: {
        schema: 1,
        scope_version: 1,
        courses: grants,
      },
    },
    courses,
    materials: [],
  }
  writeJson(join(dataDir, 'Study', '_System', 'state.json'), state)
  writeFileSync(join(dataDir, '.private', 'moodle-token.dpapi'), Buffer.from('fake'), 'utf8')
  writeJson(join(support, 'settings.json'), {
    schemaVersion: 1,
    dataDir,
    schoolUrl: ACCOUNT.site,
    timezone: 'Asia/Hong_Kong',
    stage,
  })
  return { dataDir, state }
}

function fullGrant(course, extra = {}) {
  return {
    moodle_courseid: course.moodle_course_ids[0],
    moodle_courseids: course.moodle_course_ids,
    mode: 'full',
    authorized_at: '2026-11-02T00:00:00.000Z',
    scope_version: 1,
    account_scope: ACCOUNT,
    ...extra,
  }
}

function infoOnlyGrant(course, extra = {}) {
  return { ...fullGrant(course, extra), mode: 'info_only' }
}

function course(id, moodleId) {
  return { id, name: `Course ${id}`, shortname: id, moodle_course_ids: [moodleId] }
}

function writeSnapshot(dataDir, { complete = true, courses = [101], finishedAt = '2026-11-03T00:00:00.000Z', file = 'refresh-latest.json' } = {}) {
  writeJson(join(dataDir, 'Study', '_System', file), {
    started_at: '2026-11-02T23:50:00.000Z',
    finished_at: finishedAt,
    collection_complete: complete,
    account_scope: ACCOUNT,
    pages: courses.map((moodleId) => ({ course_id: `moodle-${moodleId}`, courseid: moodleId, sections: [] })),
    threads: [],
    assignments: { courses: [] },
    quizzes: { quizzes: [] },
    errors: complete ? [] : [{ scope: 'assignment_catalog', error: 'timeout' }],
    collection_binding: { protocol: 1, run_id: 'a'.repeat(32), request_sha256: 'b'.repeat(64) },
  })
}

function writeReport(
  dataDir,
  { courses = [], totals = {}, errors = [], finishedAt = '2026-11-03T00:10:00.000Z', textSource = { source: 'snapshot' } } = {},
) {
  writeJson(join(dataDir, 'Study', '_System', 'LibrarySync', 'latest.json'), {
    schema: 1,
    run_id: 'c'.repeat(32),
    started_at: '2026-11-03T00:00:00.000Z',
    finished_at: finishedAt,
    authorization: { scope_version: 1, mode: 'full' },
    text_source: textSource,
    courses,
    totals: { failed: 0, blocked_courses: 0, ...totals },
    errors,
    index: { courses: {} },
  })
}

function readyRow(courseId, moodleId, extra = {}) {
  return {
    course_id: courseId,
    moodle_courseid: moodleId,
    modules_visible: 2,
    file_candidates: 0,
    installed: 0,
    identical: 0,
    duplicate: 0,
    already_current: 0,
    failed: 0,
    captures: 2,
    gaps: [],
    ...extra,
  }
}

test('empty cache directory is not sync evidence', () => {
  const c = course('moodle-101', 101)
  const { dataDir, state } = makeDataDir({ courses: [c], grants: { 'moodle-101': fullGrant(c) } })
  mkdirSync(join(dataDir, 'Study', '_System', 'Library'), { recursive: true })
  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, false)
  assert.ok(evidence.blockers.some((item) => item.code === 'sync_not_started'))
  rmSync(dataDir, { recursive: true, force: true })
})

test('full sync with a clean report is ready, including a course without downloadable files', () => {
  const c = course('moodle-101', 101)
  const { dataDir, state } = makeDataDir({ courses: [c], grants: { 'moodle-101': fullGrant(c) } })
  writeSnapshot(dataDir)
  writeReport(dataDir, { courses: [readyRow('moodle-101', 101)] })
  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, true)
  assert.equal(evidence.mode, 'full')
  assert.equal(evidence.courses[0].status, 'ready')
  rmSync(dataDir, { recursive: true, force: true })
})

test('failed or partial sync is reported per course instead of ready', () => {
  const c = course('moodle-101', 101)
  const { dataDir, state } = makeDataDir({ courses: [c], grants: { 'moodle-101': fullGrant(c) } })
  writeSnapshot(dataDir)
  writeReport(dataDir, { courses: [readyRow('moodle-101', 101, { failed: 2 })], totals: { failed: 2 } })
  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, false)
  assert.equal(evidence.courses[0].status, 'partial')
  assert.ok(evidence.blockers.some((item) => item.code === 'sync_errors'))
  rmSync(dataDir, { recursive: true, force: true })
})

test('sync evidence older than the current authorization is stale', () => {
  const c = course('moodle-101', 101)
  const { dataDir, state } = makeDataDir({
    courses: [c],
    grants: { 'moodle-101': fullGrant(c, { authorized_at: '2026-11-05T00:00:00.000Z' }) },
  })
  writeSnapshot(dataDir)
  writeReport(dataDir, { courses: [readyRow('moodle-101', 101)] })
  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, false)
  assert.equal(evidence.courses[0].status, 'stale')
  rmSync(dataDir, { recursive: true, force: true })
})

test('account mismatch blocks reuse of another account sync records', () => {
  const c = course('moodle-101', 101)
  const other = { site: 'https://school.example', user_id: 99999 }
  const { dataDir, state } = makeDataDir({
    courses: [c],
    grants: { 'moodle-101': fullGrant(c, { account_scope: other }) },
  })
  writeSnapshot(dataDir)
  writeReport(dataDir, { courses: [readyRow('moodle-101', 101)] })
  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, false)
  assert.equal(evidence.courses[0].status, 'account_mismatch')
  rmSync(dataDir, { recursive: true, force: true })
})

test('info_only completes from a matching collection snapshot without downloads', () => {
  const c = course('moodle-101', 101)
  const { dataDir, state } = makeDataDir({ courses: [c], grants: { 'moodle-101': infoOnlyGrant(c) } })
  writeSnapshot(dataDir)
  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, true)
  assert.equal(evidence.mode, 'info_only')
  assert.equal(evidence.courses[0].available.materials, 0)
  rmSync(dataDir, { recursive: true, force: true })
})

test('incomplete collection snapshot is not ready', () => {
  const c = course('moodle-101', 101)
  const { dataDir, state } = makeDataDir({ courses: [c], grants: { 'moodle-101': infoOnlyGrant(c) } })
  writeSnapshot(dataDir, { complete: false })
  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, false)
  assert.equal(evidence.courses[0].status, 'partial')
  rmSync(dataDir, { recursive: true, force: true })
})

test('full sync with downloaded files but an incomplete collection is not complete', async () => {
  const c = course('moodle-101', 101)
  const { dataDir, state } = makeDataDir({ courses: [c], grants: { 'moodle-101': fullGrant(c) }, stage: 'synced' })
  // Downloads succeeded ...
  writeReport(dataDir, { courses: [readyRow('moodle-101', 101)] })
  const materialDir = join(dataDir, 'Study', 'moodle-101')
  mkdirSync(materialDir, { recursive: true })
  const material = join(materialDir, 'lesson.pdf')
  writeFileSync(material, 'downloaded-bytes', 'utf8')
  // ... but the school collection clearly failed (e.g. assignment query timeout).
  writeSnapshot(dataDir, { complete: false })

  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, false)
  assert.equal(evidence.courses[0].status, 'partial')
  assert.ok(evidence.courses[0].issues.some((issue) => issue.code === 'collection_incomplete'))
  assert.ok(evidence.blockers.some((item) => item.code === 'collection_incomplete'))
  // The downloaded material scope is still reported as available.
  assert.equal(evidence.courses[0].available.textUnits, 2)

  const school = {
    state: 'connected',
    connected: true,
    baseUrl: ACCOUNT.site,
    userId: ACCOUNT.user_id,
    siteName: 'School',
    requiresLogin: false,
    retryable: false,
  }
  const refused = await finishHandler({}, { remote: stubGateway(school) })
  assert.equal(refused.error, 'SETUP_INCOMPLETE')
  assert.equal(refused.reason, 'sync_incomplete')
  assert.notEqual(readSettings().stage, 'ready')
  // Downloads and the material report are preserved; nothing is deleted.
  assert.equal(readFileSync(material, 'utf8'), 'downloaded-bytes')
  assert.ok(existsSync(join(dataDir, 'Study', '_System', 'LibrarySync', 'latest.json')))

  // A later complete collection (with the incremental material report) allows
  // setup completion without re-downloading anything.
  writeSnapshot(dataDir, { complete: true })
  const repaired = await finishHandler({}, { remote: stubGateway(school) })
  assert.equal(repaired.ok, true)
  assert.equal(repaired.ready, true)
  assert.equal(readFileSync(material, 'utf8'), 'downloaded-bytes')
  rmSync(dataDir, { recursive: true, force: true })
})

test('full sync report without any collection snapshot is not complete', () => {
  const c = course('moodle-101', 101)
  const { dataDir, state } = makeDataDir({ courses: [c], grants: { 'moodle-101': fullGrant(c) } })
  writeReport(dataDir, { courses: [readyRow('moodle-101', 101)] })
  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, false)
  assert.ok(evidence.blockers.some((item) => item.code === 'collection_incomplete'))
  rmSync(dataDir, { recursive: true, force: true })
})

test('existing report with a complete recorded collection snapshot stays reusable', () => {
  const c = course('moodle-101', 101)
  const { dataDir, state } = makeDataDir({ courses: [c], grants: { 'moodle-101': fullGrant(c) }, stage: 'ready' })
  const runId = 'd'.repeat(32)
  const snapshotPath = `Study/_System/Runtime/runs/${runId}/snapshot.json`
  writeJson(join(dataDir, snapshotPath), {
    started_at: '2026-11-02T23:50:00.000Z',
    finished_at: '2026-11-03T00:00:00.000Z',
    collection_complete: true,
    account_scope: ACCOUNT,
    pages: [{ course_id: 'moodle-101', courseid: 101, sections: [] }],
    threads: [],
    assignments: { courses: [] },
    quizzes: { quizzes: [] },
    errors: [],
    collection_binding: { protocol: 1, run_id: runId, request_sha256: 'b'.repeat(64) },
  })
  writeReport(dataDir, {
    courses: [readyRow('moodle-101', 101)],
    textSource: {
      source: 'snapshot',
      captured_at: '2026-11-03T00:00:00.000Z',
      snapshot_run_id: runId,
      snapshot: { path: snapshotPath, run_id: runId, checked_at: '2026-11-03T00:00:00.000Z', collection_complete: true },
    },
  })
  // No refresh-latest.json / refresh-last-complete.json pointer exists.
  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, true)
  assert.equal(evidence.courses[0].status, 'ready')
  rmSync(dataDir, { recursive: true, force: true })
})

test('mixed full and info_only courses require both kinds of evidence', () => {
  const full = course('moodle-101', 101)
  const info = course('moodle-102', 102)
  const { dataDir, state } = makeDataDir({
    courses: [full, info],
    grants: { 'moodle-101': fullGrant(full), 'moodle-102': infoOnlyGrant(info) },
  })
  writeSnapshot(dataDir, { courses: [101, 102] })
  writeReport(dataDir, { courses: [readyRow('moodle-101', 101)] })
  const evidence = evaluateSyncEvidence({ dataDir, state })
  assert.equal(evidence.ready, true)
  assert.equal(evidence.mode, 'mixed')
  assert.equal(evidence.courses.length, 2)
  rmSync(dataDir, { recursive: true, force: true })
})

test('every next-step tool is actually registered by the MCP server', async () => {
  const names = []
  const fakeServer = {
    registerTool(name) {
      names.push(name)
      return { name }
    },
  }
  registerSetupTools(fakeServer, {})
  registerStudyTools(fakeServer, {})
  const registered = new Set(names)
  for (const tool of [
    'study_setup_status',
    'study_setup_location',
    'study_setup_runtime',
    'study_connect_school',
    'study_reconnect',
    'study_setup_courses',
    'study_setup_select_courses',
    'study_setup_sync',
    'study_setup_finish',
    'study_job_status',
    'study_local_context',
    'study_teaching_event',
  ]) {
    assert.ok(registered.has(tool), `missing tool ${tool}`)
  }
  const scenarios = [
    { locationSet: false },
    { locationSet: true, configReady: false },
    { locationSet: true, configReady: true, runtimeReady: false },
    { locationSet: true, configReady: true, runtimeReady: true, tokenPresent: false },
    { locationSet: true, configReady: true, runtimeReady: true, tokenPresent: true, schoolAuthRequired: true },
    { locationSet: true, configReady: true, runtimeReady: true, tokenPresent: true, schoolNetworkIssue: true },
    { locationSet: true, configReady: true, runtimeReady: true, tokenPresent: true, coursesSelected: false },
    { locationSet: true, configReady: true, runtimeReady: true, tokenPresent: true, coursesSelected: true, authorizationComplete: false },
    { locationSet: true, configReady: true, runtimeReady: true, tokenPresent: true, coursesSelected: true, authorizationComplete: true, syncReady: false },
    { locationSet: true, configReady: true, runtimeReady: true, tokenPresent: true, coursesSelected: true, authorizationComplete: true, syncReady: true, setupComplete: false },
  ]
  for (const capabilities of scenarios) {
    for (const step of nextSteps(capabilities)) {
      assert.ok(registered.has(step.tool), `next step references unregistered tool ${step.tool}`)
    }
  }
})

function stubGateway(school) {
  return {
    probe: async () => school,
    status: () => school,
    invalidate() {},
  }
}

test('finish refuses completion when sync evidence is missing or partial', async () => {
  const c = course('moodle-101', 101)
  const { dataDir } = makeDataDir({ courses: [c], grants: { 'moodle-101': fullGrant(c) }, stage: 'synced' })
  const school = {
    state: 'connected',
    connected: true,
    baseUrl: ACCOUNT.site,
    userId: ACCOUNT.user_id,
    siteName: 'School',
    requiresLogin: false,
    retryable: false,
  }
  const result = await finishHandler({}, { remote: stubGateway(school) })
  assert.equal(result.error, 'SETUP_INCOMPLETE')
  assert.equal(result.reason, 'sync_incomplete')
  const status = await collectStatus({ remote: stubGateway(school), verifySchool: true })
  assert.equal(status.capabilities.syncReady, false)
  assert.ok(status.sync && status.sync.ready === false)
  rmSync(dataDir, { recursive: true, force: true })
})

test('finish succeeds with matching evidence even while the school is temporarily unreachable', async () => {
  const c = course('moodle-101', 101)
  const { dataDir } = makeDataDir({ courses: [c], grants: { 'moodle-101': fullGrant(c) }, stage: 'synced' })
  writeSnapshot(dataDir)
  writeReport(dataDir, { courses: [readyRow('moodle-101', 101)] })
  const school = {
    state: 'network',
    connected: false,
    baseUrl: ACCOUNT.site,
    userId: ACCOUNT.user_id,
    requiresLogin: false,
    retryable: true,
    lastError: { code: 'STUDY_NETWORK' },
  }
  const result = await finishHandler({}, { remote: stubGateway(school) })
  assert.equal(result.ok, true)
  assert.equal(result.ready, true)
  assert.ok(result.warning)
  assert.equal(readSettings().stage, 'ready')
  rmSync(dataDir, { recursive: true, force: true })
})

test('finish requires a real sign-in again when the saved credential is invalid', async () => {
  const c = course('moodle-101', 101)
  const { dataDir } = makeDataDir({ courses: [c], grants: { 'moodle-101': fullGrant(c) }, stage: 'synced' })
  writeSnapshot(dataDir)
  writeReport(dataDir, { courses: [readyRow('moodle-101', 101)] })
  const school = {
    state: 'auth',
    connected: false,
    baseUrl: ACCOUNT.site,
    requiresLogin: true,
    retryable: false,
    lastError: { code: 'STUDY_AUTH' },
  }
  const result = await finishHandler({}, { remote: stubGateway(school) })
  assert.equal(result.error, 'SETUP_INCOMPLETE')
  assert.equal(result.reason, 'school_auth_required')
  rmSync(dataDir, { recursive: true, force: true })
})

test('switching data location keeps the old folder and schedules a reconnect', () => {
  const first = makeDataDir({ courses: [course('moodle-101', 101)], grants: {} })
  const oldMarker = join(first.dataDir, 'old-profile-marker.txt')
  writeFileSync(oldMarker, 'keep me', 'utf8')
  const next = mkdtempSync(join(tmpdir(), 'moodle-plugin-data2-'))
  let reconnect = 0
  const result = locationHandler(
    { data_dir: next },
    { remote: stubGateway({ state: 'not_connected', connected: false, requiresLogin: false }), requestReconnect: () => { reconnect += 1 } },
  )
  assert.equal(result.ok, true)
  assert.equal(result.changed, true)
  assert.equal(result.reconnecting, true)
  assert.equal(reconnect, 1)
  assert.ok(readFileSync(oldMarker, 'utf8').includes('keep me'))
  assert.equal(readSettings().dataDir, next)
  assert.ok(Array.isArray(result.next_steps))
  rmSync(first.dataDir, { recursive: true, force: true })
  rmSync(next, { recursive: true, force: true })
})

test('switching into a folder of another school without confirmation is refused', () => {
  const otherDir = mkdtempSync(join(tmpdir(), 'moodle-plugin-data3-'))
  writeJson(join(otherDir, 'config.local.json'), { moodle_url: 'https://other.school.example', timezone: 'Asia/Hong_Kong' })
  const result = locationHandler(
    { data_dir: otherDir, moodle_url: 'https://school.example' },
    { remote: stubGateway({ state: 'not_connected', connected: false, requiresLogin: false }) },
  )
  assert.equal(result.error, 'SCHOOL_CONFLICT')
  rmSync(otherDir, { recursive: true, force: true })
})

test('re-confirming the same data location keeps a completed setup stage', () => {
  const c = course('moodle-101', 101)
  const { dataDir } = makeDataDir({ courses: [c], grants: { 'moodle-101': fullGrant(c) }, stage: 'ready' })
  const result = locationHandler(
    { data_dir: dataDir },
    { remote: stubGateway({ state: 'connected', connected: true, requiresLogin: false }), requestReconnect: () => {} },
  )
  assert.equal(result.ok, true)
  assert.equal(result.changed, false)
  assert.equal(result.reconnecting, false)
  assert.equal(readSettings().stage, 'ready')
  rmSync(dataDir, { recursive: true, force: true })
})

test('network failures and login failures carry different recovery guidance', () => {
  const network = remoteFailure(new RemoteError('STUDY_NETWORK', 'offline', { kind: 'network', next_step: 'study_reconnect' }))
  const auth = remoteFailure(
    new RemoteError('STUDY_AUTH', 'invalid token', { kind: 'auth', next_step: 'study_connect_school', requiresLogin: true }),
  )
  assert.equal(network.kind, 'network')
  assert.equal(network.next_step, 'study_reconnect')
  assert.equal(network.requires_login, false)
  assert.equal(auth.kind, 'auth')
  assert.equal(auth.next_step, 'study_connect_school')
  assert.equal(auth.requires_login, true)
})
