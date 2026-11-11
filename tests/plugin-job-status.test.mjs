// Offline contract tests for the `study_job_status` tool's job-id lookup.
//
// The real in-process job runner is exercised with tiny Node child processes
// (no school connection, no Python, no student data). The regression this
// guards: a caller that asks for an older job id must get that job — or an
// explicit JOB_NOT_FOUND — never the state of whatever job happens to be
// running or newest.
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { after, test } from 'node:test'
import { fileURLToPath } from 'node:url'

const repoRoot = fileURLToPath(new URL('..', import.meta.url))
const tmpRoot = join(repoRoot, 'tmp')
mkdirSync(tmpRoot, { recursive: true })
const runRoot = mkdtempSync(join(tmpRoot, 'plugin-job-status-'))

const { jobView, startJob } = await import('../tools/moodle/plugin/jobs.mjs')
const { registerSetupTools } = await import('../tools/moodle/plugin/setup-tools.mjs')

after(() => {
  rmSync(runRoot, { recursive: true, force: true, maxRetries: 3 })
})

const handlers = new Map()
registerSetupTools(
  {
    registerTool(name, _spec, handler) {
      handlers.set(name, handler)
    },
  },
  {},
)
const jobStatus = handlers.get('study_job_status')
assert.ok(jobStatus, 'study_job_status must be registered')

function parse(result) {
  const text = result?.content?.find((item) => item.type === 'text')?.text ?? '{}'
  return JSON.parse(text)
}

let logSeq = 0
function startQuick(name) {
  logSeq += 1
  return startJob({
    name,
    steps: [{ label: 'quick', command: process.execPath, args: ['-e', 'process.exit(0)'], cwd: repoRoot }],
    logPath: join(runRoot, `${logSeq}-${name}.log`),
  })
}

function startSlow(name, milliseconds = 1500) {
  logSeq += 1
  return startJob({
    name,
    steps: [
      {
        label: 'slow',
        command: process.execPath,
        args: ['-e', `setTimeout(() => {}, ${milliseconds})`],
        cwd: repoRoot,
      },
    ],
    logPath: join(runRoot, `${logSeq}-${name}.log`),
  })
}

async function waitForFinished(id, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const view = jobView(id)
    if (view && view.state !== 'running') return view
    if (Date.now() > deadline) throw new Error(`job ${id} did not finish in time`)
    await new Promise((resolve) => setTimeout(resolve, 40))
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

test('an explicit job_id returns that job, not another running job', async () => {
  const older = startQuick('setup-runtime')
  await waitForFinished(older.id)
  const active = startSlow('setup-sync')

  const runningLookup = parse(await jobStatus({ job_id: active.id }))
  assert.equal(runningLookup.ok, true)
  assert.equal(runningLookup.running, true)
  assert.equal(runningLookup.job.id, active.id)

  const finishedLookup = parse(await jobStatus({ job_id: older.id }))
  assert.equal(finishedLookup.ok, true)
  assert.equal(finishedLookup.running, false)
  assert.equal(finishedLookup.job.id, older.id, 'the requested older job must be returned')
  assert.notEqual(finishedLookup.job.id, active.id, 'the running job must not replace the requested one')
  assert.equal(finishedLookup.job.state, 'done')
  assert.match(finishedLookup.job.log, /quick/)

  await waitForFinished(active.id)
})

test('an unknown job_id is reported instead of falling back to another job', async () => {
  const active = startSlow('setup-sync')
  try {
    const result = await jobStatus({ job_id: 'missing-job-id' })
    const payload = parse(result)
    assert.equal(result.isError, true)
    assert.equal(payload.ok, false)
    assert.equal(payload.error, 'JOB_NOT_FOUND')
    assert.equal(payload.job_id, 'missing-job-id')
    assert.equal(payload.job, undefined)
    assert.ok(payload.message.includes('job_id'), 'the message must explain how to query the latest job')
  } finally {
    await waitForFinished(active.id)
  }
})

test('without a job_id the running job is returned, then the most recent one', async () => {
  const active = startSlow('setup-login')
  const running = parse(await jobStatus({}))
  assert.equal(running.ok, true)
  assert.equal(running.running, true)
  assert.equal(running.job.id, active.id)
  await waitForFinished(active.id)

  const first = startQuick('setup-sync')
  await waitForFinished(first.id)
  await sleep(30)
  const second = startQuick('study-refresh')
  await waitForFinished(second.id)

  const latest = parse(await jobStatus({}))
  assert.equal(latest.ok, true)
  assert.equal(latest.running, false)
  assert.equal(latest.job.id, second.id)
})
