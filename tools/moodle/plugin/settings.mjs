// Local settings for the Moodle Study Assistant plugin.
//
// Program files live inside the installed plugin package (read-only for the
// plugin), personal learning data lives in a student-chosen data directory,
// and this support directory holds machine-local runtime state (Python venv,
// logs, setup progress). The three are kept separate so plugin upgrades never
// touch learning data.
import { existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const DEFAULT_SCHOOL_URL = 'https://moodle.hku.hk'
export const DEFAULT_TIMEZONE = 'Asia/Hong_Kong'
export const SETTINGS_VERSION = 1

export function programDir() {
  if (process.env.MOODLE_STUDY_PROGRAM_DIR) return resolve(process.env.MOODLE_STUDY_PROGRAM_DIR)
  // tools/moodle/plugin/settings.mjs -> plugin package / program root
  return fileURLToPath(new URL('../../..', import.meta.url))
}

export function supportDir() {
  if (process.env.MOODLE_STUDY_SUPPORT_DIR) return resolve(process.env.MOODLE_STUDY_SUPPORT_DIR)
  const local = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local')
  return resolve(join(local, 'MoodleStudyAssistant'))
}

export function dshHome() {
  if (process.env.MOODLE_STUDY_DSH_HOME) return resolve(process.env.MOODLE_STUDY_DSH_HOME)
  if (process.env.DSH_HOME) return resolve(process.env.DSH_HOME)
  return join(homedir(), '.dsh')
}

export function ensureSupportDir() {
  const dir = supportDir()
  mkdirSync(dir, { recursive: true })
  mkdirSync(join(dir, 'logs'), { recursive: true })
  return dir
}

export function settingsPath() {
  return join(supportDir(), 'settings.json')
}

export function readSettings() {
  try {
    const value = JSON.parse(readFileSync(settingsPath(), 'utf8'))
    return value && typeof value === 'object' ? value : null
  } catch {
    return null
  }
}

export function writeSettings(patch) {
  const dir = ensureSupportDir()
  const current = readSettings() ?? { schemaVersion: SETTINGS_VERSION, stage: 'new' }
  const next = { ...current, ...patch, schemaVersion: SETTINGS_VERSION, updatedAt: new Date().toISOString() }
  if (!next.createdAt) next.createdAt = next.updatedAt
  const path = join(dir, 'settings.json')
  const temp = `${path}.${process.pid}.tmp`
  writeFileSync(temp, JSON.stringify(next, null, 2) + '\n', 'utf8')
  renameSync(temp, path)
  return next
}

export function resolveDataDir(value) {
  const raw = String(value ?? '').trim()
  if (!raw) throw new Error('请提供资料保存文件夹的绝对路径')
  const expanded = raw.replace(/^~(?=$|[\\/])/, homedir())
  if (!isAbsolute(expanded)) throw new Error('资料保存位置必须是绝对路径，例如 C:\\Users\\你\\Documents\\MoodleStudy')
  return resolve(expanded)
}

export function defaultDataDir() {
  const documents = join(homedir(), 'Documents')
  const base = existsSync(documents) ? documents : homedir()
  return join(base, 'MoodleStudy')
}

export function normalizeTimeZone(value) {
  const text = String(value ?? '').trim()
  if (!text) return DEFAULT_TIMEZONE
  if (!/^[A-Za-z0-9_+\-/]+$/.test(text)) throw new Error('时区格式无效')
  return text
}

export function normalizeSchoolUrl(value) {
  const text = String(value ?? '').trim()
  const url = new URL(text)
  if (url.protocol !== 'https:') throw new Error('学校地址必须以 https:// 开头')
  if (url.username || url.password || url.search || url.hash) throw new Error('学校地址不能包含账号、参数或片段')
  return url.href.replace(/\/+$/, '')
}

export function configPath(dataDir) {
  return join(dataDir, 'config.local.json')
}

export function tokenPath(dataDir) {
  return join(dataDir, '.private', 'moodle-token.dpapi')
}

export function statePath(dataDir) {
  return join(dataDir, 'Study', '_System', 'state.json')
}

export function readConfig(dataDir) {
  try {
    return JSON.parse(readFileSync(configPath(dataDir), 'utf8').replace(/^\uFEFF/, ''))
  } catch {
    return null
  }
}

export function writeConfig(dataDir, config) {
  const path = configPath(dataDir)
  mkdirSync(dirname(path), { recursive: true })
  const temp = `${path}.${process.pid}.tmp`
  writeFileSync(temp, JSON.stringify(config, null, 2) + '\n', 'utf8')
  renameSync(temp, path)
}

export function readState(dataDir) {
  try {
    return JSON.parse(readFileSync(statePath(dataDir), 'utf8').replace(/^\uFEFF/, ''))
  } catch {
    return null
  }
}

export function looksLikeExistingProfile(dir) {
  try {
    if (!statSync(dir).isDirectory()) return false
  } catch {
    return false
  }
  return existsSync(statePath(dir)) || existsSync(configPath(dir)) || existsSync(join(dir, 'Study'))
}

export function venvPaths() {
  const root = join(supportDir(), 'venv')
  return {
    venv: root,
    windows: join(root, 'Scripts', 'python.exe'),
    posix: join(root, 'bin', 'python'),
  }
}

export function runtimePython() {
  const paths = venvPaths()
  if (existsSync(paths.windows)) return paths.windows
  if (existsSync(paths.posix)) return paths.posix
  return null
}

export function runtimePath() {
  return join(supportDir(), 'runtime.json')
}

export function readRuntime() {
  try {
    return JSON.parse(readFileSync(runtimePath(), 'utf8'))
  } catch {
    return {}
  }
}

export function writeRuntime(patch) {
  const dir = ensureSupportDir()
  const current = readRuntime()
  const next = { ...current, ...patch, updatedAt: new Date().toISOString() }
  const path = join(dir, 'runtime.json')
  const temp = `${path}.${process.pid}.tmp`
  writeFileSync(temp, JSON.stringify(next, null, 2) + '\n', 'utf8')
  renameSync(temp, path)
  return next
}

export function logPath(name) {
  return join(supportDir(), 'logs', `${name}.log`)
}

// Ready for the full study tool set: data location chosen, Python runtime
// installed and the school account connected.
export function studyReadiness(settings = readSettings()) {
  if (!settings?.dataDir) return { ready: false, reason: 'location_missing' }
  try {
    if (!statSync(settings.dataDir).isDirectory()) return { ready: false, reason: 'data_dir_missing' }
  } catch {
    return { ready: false, reason: 'data_dir_missing' }
  }
  if (!readConfig(settings.dataDir)) return { ready: false, reason: 'config_missing' }
  if (!runtimePython()) return { ready: false, reason: 'runtime_missing' }
  if (!existsSync(tokenPath(settings.dataDir))) return { ready: false, reason: 'token_missing' }
  return { ready: true, reason: null }
}
