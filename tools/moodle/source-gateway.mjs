// Lazy school-connection gateway for the source-installed MCP server.
//
// The installer registers the Moodle MCP server before the student has completed
// the school browser login. Starting the stdio server must therefore never read
// the protected credential or open a network connection: every remote tool stays
// registered, and the *current* config + credential are resolved only when a
// tool is actually called. A missing login/configuration becomes one structured
// JSON tool error instead of a crashed server, and the next call retries with the
// then-current files, so a login completed in the same chat works without a
// restart and a temporary network failure is retried on the next call.
//
// This module deliberately owns only the source-install layout:
//   STUDY_DATA_ROOT (or program root) / config.local.json
//   STUDY_DATA_ROOT (or program root) / .private/moodle-token.dpapi
//   STUDY_DATA_ROOT (or program root) / Study/_System/state.json
// It does not use the plugin's settings/support-directory semantics.
import {createHash} from 'node:crypto';
import {existsSync, readFileSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {ReadOnlyClient} from './core.mjs';
import {connectionDiagnostic} from './connection-errors.mjs';
import {createMcpChapterContext} from './mcp-workspace.mjs';
import {decryptWindowsToken} from './windows-token.mjs';

// Fixed, path-free guidance. Tool errors are returned to the model, so they must
// never contain credentials, credential bytes, absolute paths or remote bodies.
const SETUP_MESSAGE = '本机的学习档案或学校连接配置尚未就绪，联网工具暂时不可用；已下载的本地资料不受影响。';
const SETUP_NEXT_STEP = '先完成本机初始化（运行 setup.cmd 或安装器给出的初始化步骤），再在同一聊天重试；不需要重装或重建学习档案。';
const LOGIN_MESSAGE = '还没有连接学校账号，联网工具暂时不可用。已下载的本地资料仍可继续学习；不需要重装或重建学习档案。';
const LOGIN_NEXT_STEP = '请让助手在本聊天完成学校浏览器登录（账号、密码和 MFA 只由学生本人在学校页面输入），完成后直接重试这个工具；当前不需要重启 Harness。';
const REMOTE_MESSAGE = '本次学校只读请求没有完成；已下载的本地资料仍可继续学习。';
const REMOTE_NEXT_STEP = '稍后重试同一个只读请求；若持续失败，请让助手核对学校服务，或在确认登录失效后重新完成学校登录。';

// These failures leave no usable connection in this process: the next call must
// resolve the config/credential again instead of reusing a dead client.
const RETRY_WITH_FRESH_CONNECTION = new Set([
  'STUDY_DNS', 'STUDY_TIMEOUT', 'STUDY_TLS', 'STUDY_HTTP', 'STUDY_RESPONSE',
  'STUDY_NETWORK', 'STUDY_AUTH', 'STUDY_TOKEN_DECRYPT_FAILED',
]);

export class SourceGatewayError extends Error {
  constructor(code, {kind = 'remote', message = '', nextStep = null, requiresLogin = false, retryable = true, payload = null} = {}) {
    const fields = payload ?? {
      error: code,
      ...(requiresLogin ? {login_required: true} : {}),
      ...(message ? {message} : {}),
      ...(nextStep ? {next_step: nextStep} : {}),
    };
    // The MCP SDK turns a thrown tool error into its `message` text. Serializing
    // the fixed diagnostic here keeps that error machine-readable without
    // forwarding arbitrary exception text.
    super(JSON.stringify(fields));
    this.name = 'SourceGatewayError';
    this.code = code;
    this.kind = kind;
    this.next_step = nextStep ?? fields.next_step ?? null;
    this.requiresLogin = requiresLogin;
    this.retryable = retryable;
    this.payload = fields;
  }
}

export function setupRequired() {
  return new SourceGatewayError('STUDY_SETUP_REQUIRED', {
    kind: 'setup', message: SETUP_MESSAGE, nextStep: SETUP_NEXT_STEP,
  });
}

export function loginRequired() {
  return new SourceGatewayError('STUDY_LOGIN_REQUIRED', {
    kind: 'login', message: LOGIN_MESSAGE, nextStep: LOGIN_NEXT_STEP, requiresLogin: true,
  });
}

function safeSchoolUrl(value) {
  try {
    const url = new URL(String(value ?? '').trim());
    return url.protocol === 'https:' && !url.username && !url.password && !url.search && !url.hash
      ? url.href.replace(/\/+$/, '') : null;
  } catch { return null; }
}

// Read the current source-install config. Any unreadable/invalid state maps to
// one structured setup error; the parse error itself is never forwarded.
export function readSourceConfig(workspace) {
  let value;
  try {
    value = JSON.parse(readFileSync(join(resolve(workspace), 'config.local.json'), 'utf8').replace(/^\uFEFF/, ''));
  } catch { throw setupRequired(); }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw setupRequired();
  if (!safeSchoolUrl(value.moodle_url)) throw setupRequired();
  return value;
}

// Read the current protected credential. Reading is local and does not decrypt;
// decryption happens only when a school connection is actually needed.
export function readSourceCredential(workspace) {
  let bytes;
  try { bytes = readFileSync(join(resolve(workspace), '.private/moodle-token.dpapi')); }
  catch { throw loginRequired(); }
  if (!bytes.length) throw loginRequired();
  return bytes;
}

function sourceStatePath(workspace) {
  return join(resolve(workspace), 'Study/_System/state.json');
}

async function defaultConnectClient(config, token) {
  return new ReadOnlyClient(config.moodle_url, token).connect();
}

// MCP reads pass the current account id in the request. Before the first lazy
// connection the caller sees `userId === null`; substitute the authenticated
// account so read access is scoped to the account the credential belongs to.
function currentAccountParams(params, active) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return params;
  const userId = active?.userId;
  if (!Number.isSafeInteger(userId) || userId <= 0) return params;
  const result = {...params};
  if (Object.hasOwn(result, 'userid')) result.userid = userId;
  if (Object.hasOwn(result, 'useridto')) result.useridto = userId;
  return result;
}

function toSourceError(error) {
  if (error instanceof SourceGatewayError) return error;
  const diagnostic = connectionDiagnostic(error);
  if (diagnostic) {
    // Expired authorization and unreadable credentials need the student to sign
    // in again; a plain network failure does not. Both are retried on the next
    // call, which re-reads the saved credential first.
    const requiresLogin = ['STUDY_AUTH', 'STUDY_TOKEN_DECRYPT_FAILED'].includes(diagnostic.connection_error);
    return new SourceGatewayError(diagnostic.connection_error, {
      kind: requiresLogin ? 'auth' : 'network',
      retryable: true,
      payload: {...diagnostic, ...(requiresLogin ? {login_required: true} : {}), retryable: true},
    });
  }
  // The read-only client only throws fixed, token-free messages for API-level
  // failures; keep the sanitized Moodle error code for diagnosis but never the
  // raw message or response body.
  const match = /^Moodle API failed: ([a-z_]{1,80})\b/i.exec(String(error?.message ?? ''));
  return new SourceGatewayError('STUDY_REMOTE_FAILED', {
    kind: 'remote',
    payload: {
      error: 'STUDY_REMOTE_FAILED',
      ...(match ? {reason: match[1].toLowerCase()} : {}),
      message: REMOTE_MESSAGE,
      next_step: REMOTE_NEXT_STEP,
    },
  });
}

function loadCredentialBytes(loader) {
  let value;
  try { value = loader(); }
  catch (error) {
    if (error instanceof SourceGatewayError) throw error;
    throw loginRequired();
  }
  if (!value || typeof value.length !== 'number' || value.length === 0) throw loginRequired();
  return typeof value === 'string' ? Buffer.from(value, 'utf8') : Buffer.from(value);
}

// Client facade for the MCP server. `root` is the resolved source data root
// (STUDY_DATA_ROOT when set); decryption and the connected-client factory are
// injectable so recovery behavior can be tested without the real DPAPI store.
export function createSourceGateway({
  root, decrypt = decryptWindowsToken, connectClient = defaultConnectClient, readCredential = null,
} = {}) {
  const workspace = resolve(root);
  const loadCredential = readCredential ?? (() => readSourceCredential(workspace));
  let client = null;
  let generation = null;
  let connecting = null;
  let connectingGeneration = null;

  function drop() {
    client = null;
    generation = null;
  }

  function snapshot() {
    const config = readSourceConfig(workspace);
    const credential = loadCredentialBytes(loadCredential);
    const expected = createHash('sha256')
      .update(JSON.stringify(config)).update('\0').update(credential).digest('hex');
    return {config, credential, expected};
  }

  async function performConnect(config, credential, expected) {
    try {
      const token = decrypt(credential);
      if (typeof token !== 'string' || !token) {
        const error = new Error('protected credential could not be decrypted');
        error.code = 'STUDY_TOKEN_DECRYPT_FAILED';
        throw error;
      }
      const connected = await connectClient(config, token);
      if (!connected || typeof connected.call !== 'function') {
        throw new SourceGatewayError('STUDY_REMOTE_FAILED', {
          kind: 'remote', payload: {
            error: 'STUDY_REMOTE_FAILED', message: REMOTE_MESSAGE, next_step: REMOTE_NEXT_STEP,
          },
        });
      }
      client = connected;
      generation = expected;
      if (typeof config.timezone === 'string' && config.timezone) {
        try { process.env.TZ = config.timezone; } catch { /* display-only setting */ }
      }
      return connected;
    } catch (error) {
      drop();
      throw toSourceError(error);
    }
  }

  function startConnect(config, credential, expected) {
    const run = performConnect(config, credential, expected);
    connecting = run;
    connectingGeneration = expected;
    const clear = () => {
      if (connecting !== run) return;
      connecting = null;
      connectingGeneration = null;
    };
    run.then(clear, clear);
    return run;
  }

  async function connect({force = false} = {}) {
    let attempts = 0;
    for (;;) {
      // A connection that was opened for an older saved credential must never be
      // handed to a request that started after a new credential was saved. Wait
      // for the in-flight attempt, then compare the generation again: a request
      // that still sees its own generation joins that single attempt, while a
      // request that sees a newer one opens a new session with the new files.
      while (connecting) {
        const attemptGeneration = connectingGeneration;
        let failure = null;
        try { await connecting; }
        catch (error) { failure = error; }
        if (!failure) continue;
        // The failed attempt belongs to one saved generation. Report the same
        // failure while that generation is still current instead of opening a
        // second session per waiting caller; a changed generation is retried.
        let current;
        try { current = snapshot(); }
        catch { throw failure; }
        if (current.expected === attemptGeneration) throw failure;
      }
      const current = (() => {
        try { return snapshot(); }
        catch (error) { drop(); throw error; }
      })();
      if (!force && client && generation === current.expected) return client;
      // Bounded rechecks: credential churn during connection is rare and must
      // not loop forever, but one recheck after an in-flight credential change
      // is the required recovery path.
      if (attempts >= 3) return startConnect(current.config, current.credential, current.expected);
      attempts += 1;
      try {
        return await startConnect(current.config, current.credential, current.expected);
      } catch (error) {
        // A failure belongs to the credential that was saved when the attempt
        // started. Only a generation change makes it meaningful to try once more
        // with the newly saved credential.
        let next;
        try { next = snapshot(); }
        catch { throw error; }
        if (next.expected === current.expected) throw error;
      }
    }
  }

  return {
    async call(method, params = {}) {
      const active = await connect();
      const usedGeneration = generation;
      try { return await active.call(method, currentAccountParams(params, active)); }
      catch (error) {
        const normalized = toSourceError(error);
        // Re-login can complete while this request already holds the previous
        // connection. Retry this read-only call once with the newly saved
        // credential; when the saved generation did not change, keep the
        // existing no-automatic-retry contract.
        let changed = false;
        if (usedGeneration !== null) {
          try { changed = snapshot().expected !== usedGeneration; }
          catch { changed = false; }
        }
        if (changed) {
          drop();
          const retried = await connect();
          try { return await retried.call(method, currentAccountParams(params, retried)); }
          catch (retryError) {
            const retriedNormalized = toSourceError(retryError);
            if (RETRY_WITH_FRESH_CONNECTION.has(retriedNormalized.code)) drop();
            throw retriedNormalized;
          }
        }
        if (RETRY_WITH_FRESH_CONNECTION.has(normalized.code)) drop();
        throw normalized;
      }
    },
    connect,
    supports(wsfunction) {
      if (!client) return true; // optimistic before the first lazy connection
      // The cached client may answer for a config/credential that has already
      // been replaced (e.g. a fresh browser login). Never trust its API list
      // across generations: drop it and report "optimistic" so the tool call
      // reconnects with the current files. Unreadable files behave the same way
      // instead of throwing from a capability probe.
      let current;
      try { current = snapshot(); }
      catch { drop(); return true; }
      if (generation !== current.expected) { drop(); return true; }
      try { return typeof client.supports === 'function' ? Boolean(client.supports(wsfunction)) : true; }
      catch { drop(); return true; }
    },
    get baseUrl() {
      try { return readSourceConfig(workspace).moodle_url; }
      catch { return null; }
    },
    get userId() {
      return client && Number.isSafeInteger(client.userId) && client.userId > 0 ? client.userId : null;
    },
    get siteName() { return client?.siteName ?? null; },
    get connected() { return Boolean(client); },
    invalidate() { drop(); },
  };
}

// study_check_chapter stays registered before login. Its workspace context is
// built on first use from the then-current config/credential and pins that
// generation, matching the existing STUDY_MCP_RESTART_REQUIRED semantics for
// later changes. Before login/initialization it reports the same structured
// setup/login state instead of failing the whole MCP server.
export function createSourceChapterContext({root, gateway} = {}) {
  const workspace = resolve(root);
  let pinned = null;

  function loadState() {
    try { return JSON.parse(readFileSync(sourceStatePath(workspace), 'utf8').replace(/^\uFEFF/, '')); }
    catch { throw setupRequired(); }
  }

  function build() {
    if (pinned) return pinned;
    const config = readSourceConfig(workspace);
    const credential = readSourceCredential(workspace);
    // Resolving a chapter needs the learning profile; report a setup hint rather
    // than a generic chapter failure while the profile is still missing.
    if (!existsSync(sourceStatePath(workspace))) throw setupRequired();
    pinned = createMcpChapterContext({
      root: workspace, loadState, client: gateway, config, credential,
    });
    return pinned;
  }

  return {
    root: workspace,
    loadState,
    beforeCheck: () => build().beforeCheck(),
    connect: async () => build().connect(),
  };
}
