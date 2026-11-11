// Remote school gateway for the plugin MCP server.
//
// Remote tools stay registered in every mode, but the school connection is
// established lazily on first use and re-established whenever the saved data
// directory, school configuration or protected credential changes. A network
// failure therefore never removes the local study tools, and recovery never
// requires replacing the credential unless the school actually rejects it.
import { createHash } from 'node:crypto'
import { existsSync, readFileSync, statSync } from 'node:fs'

import { ReadOnlyClient } from '../core.mjs'
import { connectionDiagnostic } from '../connection-errors.mjs'
import { decryptWindowsToken } from '../windows-token.mjs'
import {
  normalizeSchoolUrl,
  readConfig,
  readSettings,
  readState,
  tokenPath,
} from './settings.mjs'

const AUTH_CODES = new Set(['STUDY_AUTH', 'STUDY_TOKEN_DECRYPT_FAILED'])
const ACCESS_CODES = new Set(['STUDY_ACCESS'])
const NETWORK_CODES = new Set([
  'STUDY_DNS',
  'STUDY_TIMEOUT',
  'STUDY_TLS',
  'STUDY_HTTP',
  'STUDY_RESPONSE',
  'STUDY_NETWORK',
])

export class RemoteError extends Error {
  constructor(code, message, { kind = 'network', next_step = null, retryable = true, requiresLogin = false } = {}) {
    super(message)
    this.name = 'RemoteError'
    this.code = code
    this.kind = kind
    this.next_step = next_step
    this.retryable = retryable
    this.requiresLogin = requiresLogin
  }
}

function siteKey(value) {
  try {
    return normalizeSchoolUrl(value)
  } catch {
    return null
  }
}

function toRemoteError(error) {
  const diagnostic = connectionDiagnostic(error)
  const code = error?.code ?? diagnostic?.connection_error ?? 'STUDY_NETWORK'
  if (AUTH_CODES.has(code)) {
    const decrypt = code === 'STUDY_TOKEN_DECRYPT_FAILED'
    return new RemoteError(
      code,
      decrypt
        ? '本机保存的学校授权无法在当前 Windows 账户下解密。已下载的本地资料仍可学习；如需联网，请让学生本人在本机重新连接学校（必要时使用原来的 Windows 账户），不要复制或重建凭据。'
        : '学校登录已失效，需要学生本人重新连接学校。已下载的本地资料仍可继续学习；不要让学生把密码或验证码发到聊天里。',
      {
        kind: 'auth',
        next_step: 'study_connect_school',
        retryable: false,
        requiresLogin: true,
      },
    )
  }
  if (ACCESS_CODES.has(code)) {
    return new RemoteError(
      code,
      '学校拒绝了这次只读访问。请核对当前账号的课程权限或课程资料授权范围；不要绕过学校限制。本地资料仍可学习。',
      { kind: 'access', next_step: 'study_setup_status', retryable: false, requiresLogin: false },
    )
  }
  const safeCode = NETWORK_CODES.has(code) ? code : 'STUDY_NETWORK'
  const detail = diagnostic?.message ?? '当前环境无法连接学校。'
  return new RemoteError(
    safeCode,
    `${detail} 学校暂时连不上，已下载的本地资料仍可继续学习；网络恢复后调用 study_reconnect 或再次使用联网工具即可，不需要重新登录。`,
    { kind: 'network', next_step: 'study_reconnect', retryable: true, requiresLogin: false },
  )
}

function accountKey(account) {
  const site = siteKey(account?.site)
  const userId = Number(account?.user_id)
  if (!site || !Number.isInteger(userId) || userId <= 0) return null
  return `${site}|${userId}`
}

// Read requests pass the current account id in the request. Before the lazy
// connection `client.userId` may still fall back to the account recorded in the
// local profile, so the connected account must replace it: otherwise a re-login
// as another account would send the previous (or unknown) user id and the
// school could reject the read. Mirrors source-gateway.mjs exactly.
function currentAccountParams(params, active) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return params
  const userId = active?.userId
  if (!Number.isSafeInteger(userId) || userId <= 0) return params
  const result = { ...params }
  if (Object.hasOwn(result, 'userid')) result.userid = userId
  if (Object.hasOwn(result, 'useridto')) result.useridto = userId
  return result
}

// The account this data directory belongs to: recorded when courses were
// selected, so guards still work while the school is unreachable.
function readSavedAccount(dataDir) {
  if (!dataDir) return null
  const state = readState(dataDir)
  const scope = state?.settings?.profile_account_scope
  if (scope && siteKey(scope.site) && Number.isInteger(Number(scope.user_id))) {
    return { site: siteKey(scope.site), user_id: Number(scope.user_id) }
  }
  return null
}

export function createRemoteGateway({ decrypt = decryptWindowsToken, connectClient = null } = {}) {
  const openClient = connectClient ?? (async (config, token) => new ReadOnlyClient(config.moodle_url, token).connect())
  let client = null
  let generation = null
  let connecting = null
  let connectingGeneration = null
  let lastError = null
  let connectedAt = null
  let cachedStatus = { at: 0, value: null }

  const settings = () => readSettings()
  const activeDataDir = () => settings()?.dataDir ?? null

  function currentGeneration(dataDir = activeDataDir()) {
    if (!dataDir) return null
    const configBytes = (() => {
      try {
        return readFileSync(`${dataDir}/config.local.json`)
      } catch {
        return null
      }
    })()
    const credentialBytes = (() => {
      try {
        return readFileSync(tokenPath(dataDir))
      } catch {
        return null
      }
    })()
    if (!configBytes || !credentialBytes) return null
    return createHash('sha256')
      .update(dataDir)
      .update('\0')
      .update(configBytes)
      .update('\0')
      .update(credentialBytes)
      .digest('hex')
  }

  function dropClient() {
    client = null
    generation = null
    connectedAt = null
  }

  // Validate the current data location and resolve the config used for one
  // connection attempt. Every retry re-reads these files so a data-location or
  // config change is never silently connected against the wrong folder.
  function requiredSetup() {
    const dataDir = activeDataDir()
    if (!dataDir) {
      throw new RemoteError('LOCATION_REQUIRED', '还没有选择资料保存位置。请先调用 study_setup_status 完成首次设置；本地资料目录也可继续使用。', {
        kind: 'setup',
        next_step: 'study_setup_status',
        retryable: false,
      })
    }
    const config = readConfig(dataDir)
    if (!config?.moodle_url) {
      throw new RemoteError('CONFIG_REQUIRED', '资料目录里还没有学校连接配置。请先重新选择学校连接；已下载的本地资料不受影响。', {
        kind: 'setup',
        next_step: 'study_setup_location',
        retryable: false,
      })
    }
    if (!existsSync(tokenPath(dataDir))) {
      throw new RemoteError('TOKEN_REQUIRED', '还没有连接学校账号。已下载的本地资料仍可学习；需要联网时请让学生本人连接学校。', {
        kind: 'auth',
        next_step: 'study_connect_school',
        retryable: false,
        requiresLogin: true,
      })
    }
    return { dataDir, config }
  }

  function startConnect(dataDir, config, fingerprint) {
    const run = (async () => {
      const credential = readFileSync(tokenPath(dataDir))
      const token = decrypt(credential)
      const next = await openClient(config, token)
      if (!next || typeof next.call !== 'function') throw new Error('invalid connected client')
      client = next
      generation = fingerprint
      connectedAt = new Date().toISOString()
      lastError = null
      cachedStatus = { at: 0, value: null }
      return next
    })()
    connecting = run
    connectingGeneration = fingerprint
    const clear = () => {
      if (connecting !== run) return
      connecting = null
      connectingGeneration = null
    }
    run.then(clear, clear)
    return run
  }

  async function attemptConnect(dataDir, config, fingerprint) {
    try {
      return await startConnect(dataDir, config, fingerprint)
    } catch (error) {
      dropClient()
      lastError = toRemoteError(error)
      throw lastError
    }
  }

  async function connect({ force = false } = {}) {
    let attempts = 0
    for (;;) {
      // A session that was opened for an older saved config/credential must not
      // be handed to a request that started after a new credential was saved:
      // wait for the in-flight attempt, then compare the generation again.
      while (connecting) {
        const attemptGeneration = connectingGeneration
        let failure = null
        try { await connecting } catch (error) { failure = error }
        if (!failure) continue
        let current = null
        try { current = currentGeneration() } catch { current = null }
        if (current !== null && current === attemptGeneration) throw failure
      }
      const { dataDir, config } = requiredSetup()
      const fingerprint = currentGeneration(dataDir)
      if (!force && client && generation === fingerprint) return client
      if (fingerprint !== generation) dropClient()
      // Bounded rechecks: a credential saved while connecting is retried once
      // with the new files, but unbounded churn must still terminate.
      if (attempts >= 3) return attemptConnect(dataDir, config, fingerprint)
      attempts += 1
      try {
        return await attemptConnect(dataDir, config, fingerprint)
      } catch (error) {
        let next = null
        try { next = currentGeneration() } catch { next = null }
        if (next === null || next === fingerprint) throw error
      }
    }
  }

  function statusSnapshot() {
    const dataDir = activeDataDir()
    const config = dataDir ? readConfig(dataDir) : null
    const tokenPresent = Boolean(dataDir && existsSync(tokenPath(dataDir)))
    const saved = readSavedAccount(dataDir)
    const base = {
      dataDir,
      baseUrl: config?.moodle_url ?? null,
      tokenPresent,
      savedAccount: saved,
      connected: Boolean(client),
      connectedAt,
      siteName: client?.siteName ?? null,
      userId: client?.userId ?? saved?.user_id ?? null,
      lastError: lastError ? { ...lastError, message: lastError.message } : null,
      state: client ? 'connected' : lastError ? lastError.kind : tokenPresent ? 'not_connected' : 'not_connected',
      requiresLogin: lastError?.requiresLogin ?? false,
      retryable: lastError?.retryable ?? true,
      next_step: lastError?.next_step ?? (tokenPresent ? 'study_reconnect' : 'study_connect_school'),
    }
    if (!dataDir) return { ...base, state: 'setup', message: '还没有选择资料保存位置。' }
    if (!config) return { ...base, state: 'setup', message: '资料目录里没有学校连接配置。' }
    if (!tokenPresent) return { ...base, state: 'not_connected', message: '还没有连接学校账号。' }
    return base
  }

  // Cached read-only view for status tools; never throws.
  async function probe({ force = false, maxAgeMs = 20000 } = {}) {
    if (!force && cachedStatus.value && Date.now() - cachedStatus.at < maxAgeMs) return cachedStatus.value
    let value
    try {
      await connect({ force })
      value = statusSnapshot()
    } catch (error) {
      value = { ...statusSnapshot(), state: error.kind ?? 'network', message: error.message }
    }
    cachedStatus = { at: Date.now(), value }
    return value
  }

  const gateway = {
    async call(method, params) {
      const active = await connect()
      const usedGeneration = generation
      try {
        return await active.call(method, currentAccountParams(params, active))
      } catch (error) {
        // A re-login can complete while this request already holds the previous
        // session. Retry this read-only call once with the newly saved
        // credential; unchanged saved files keep the no-retry contract.
        let changed = false
        if (usedGeneration) {
          let fingerprint = null
          try { fingerprint = currentGeneration() } catch { fingerprint = null }
          changed = Boolean(fingerprint) && fingerprint !== usedGeneration
        }
        if (!changed) throw error
        dropClient()
        const retried = await connect()
        return retried.call(method, currentAccountParams(params, retried))
      }
    },
    async connect(options) {
      return connect(options)
    },
    supports(wsfunction) {
      if (!client) return true
      // A cached API list belongs to the config/credential it was connected
      // with. Once a new login has been saved, drop the stale client and report
      // "optimistic" so the tool call reconnects instead of returning
      // api_not_enabled forever. Unreadable files behave the same way.
      let fingerprint = null
      try { fingerprint = currentGeneration() } catch { fingerprint = null }
      if (!fingerprint || fingerprint !== generation) {
        dropClient()
        return true
      }
      try {
        return typeof client.supports === 'function' ? Boolean(client.supports(wsfunction)) : true
      } catch {
        dropClient()
        return true
      }
    },
    get baseUrl() {
      return readConfig(activeDataDir())?.moodle_url ?? null
    },
    get userId() {
      return client?.userId ?? readSavedAccount(activeDataDir())?.user_id ?? null
    },
    get siteName() {
      return client?.siteName ?? null
    },
    get connected() {
      return Boolean(client)
    },
    get savedAccountKey() {
      return accountKey(readSavedAccount(activeDataDir()))
    },
    probe,
    status: statusSnapshot,
    invalidate() {
      dropClient()
      lastError = null
      cachedStatus = { at: 0, value: null }
    },
  }
  return gateway
}

export function accountKeyOf(account) {
  return accountKey(account)
}
