// Small sequential background-job runner for first-use setup work.
//
// Jobs live inside the MCP server process: the tool call returns immediately
// with a job id and the model polls study_setup_status for the log tail. If
// the server restarts (for example after a successful sign-in), readiness is
// re-derived from artifacts on disk instead of this in-memory table.
import { spawn } from 'node:child_process'
import { appendFileSync, mkdirSync, readFileSync, statSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { dirname } from 'node:path'

const jobs = new Map()

function finished(record) {
  record.finishedAt = new Date().toISOString()
}

export function startJob({ name, steps, logPath, onSuccess = null, onFailure = null }) {
  const running = [...jobs.values()].find((job) => job.name === name && job.state === 'running')
  if (running) return jobView(running.id)

  const id = randomUUID()
  const record = {
    id,
    name,
    state: 'running',
    logPath,
    steps: steps.map((step) => ({ label: step.label, state: 'pending', exitCode: null })),
    error: null,
    startedAt: new Date().toISOString(),
    finishedAt: null,
    onSuccess,
    onFailure,
  }
  jobs.set(id, record)
  mkdirSync(dirname(logPath), { recursive: true })
  runStep(record, steps, 0)
  return jobView(id)
}

function appendLog(record, text) {
  try {
    appendFileSync(record.logPath, text)
  } catch {
    // Logging must never fail the setup step itself.
  }
}

function runStep(record, steps, index) {
  if (index >= steps.length) {
    record.state = 'done'
    finished(record)
    try {
      record.onSuccess?.()
    } catch {
      // onSuccess only records progress; readiness is re-checked on read.
    }
    return
  }
  const step = steps[index]
  record.steps[index].state = 'running'
  appendLog(record, `\n=== ${step.label} ===\n`)
  let child
  try {
    child = spawn(step.command, step.args, {
      cwd: step.cwd,
      env: { ...process.env, ...(step.env ?? {}) },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
  } catch (error) {
    record.steps[index].state = 'failed'
    record.state = 'failed'
    record.error = String(error?.message ?? error)
    finished(record)
    record.onFailure?.(record.error)
    return
  }
  child.stdout?.on('data', (data) => appendLog(record, data))
  child.stderr?.on('data', (data) => appendLog(record, data))
  child.on('error', (error) => {
    record.steps[index].state = 'failed'
    record.state = 'failed'
    record.error = String(error?.message ?? error)
    finished(record)
    record.onFailure?.(record.error)
  })
  child.on('exit', (code) => {
    record.steps[index].exitCode = code
    if (code === 0) {
      record.steps[index].state = 'done'
      runStep(record, steps, index + 1)
    } else {
      record.steps[index].state = 'failed'
      record.state = 'failed'
      record.error = `${step.label} 失败（退出码 ${code}）`
      finished(record)
      record.onFailure?.(record.error)
    }
  })
}

function tail(path, limit = 4000) {
  try {
    const size = statSync(path).size
    const start = Math.max(0, size - limit)
    const data = readFileSync(path)
    return data.subarray(start).toString('utf8')
  } catch {
    return ''
  }
}

export function jobView(id) {
  const record = jobs.get(id)
  if (!record) return null
  return {
    id: record.id,
    name: record.name,
    state: record.state,
    steps: record.steps.map((step) => ({ ...step })),
    error: record.error,
    startedAt: record.startedAt,
    finishedAt: record.finishedAt,
    log: tail(record.logPath),
  }
}

export function latestJob(name) {
  const matches = [...jobs.values()].filter((job) => job.name === name)
  if (!matches.length) return null
  matches.sort((a, b) => String(b.startedAt).localeCompare(String(a.startedAt)))
  return jobView(matches[0].id)
}

export function runningJob(name = null) {
  const matches = [...jobs.values()].filter(
    (job) => job.state === 'running' && (name === null || job.name === name),
  )
  if (!matches.length) return null
  return jobView(matches[0].id)
}

export function jobSummary(view) {
  if (!view) return null
  const current = view.steps.find((step) => step.state === 'running') ?? view.steps.find((step) => step.state === 'failed')
  return {
    id: view.id,
    name: view.name,
    state: view.state,
    currentStep: current?.label ?? null,
    error: view.error,
    steps: view.steps.map((step) => `${step.label}:${step.state}`),
    log: view.log,
  }
}
