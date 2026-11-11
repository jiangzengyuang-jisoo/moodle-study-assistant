// Offline forwarding tests for the two school-connection gateways.
//
// These cover the recovery contract that tools rely on:
// - a connection opened for an older saved config/credential is never handed to
//   a request that started after a newer login was saved;
// - a cached "API not supported" answer is invalidated by a saved login change;
// - a read-only call is replayed at most once, and only when the saved
//   generation changed while the request was failing.
//
// Every credential, client and network path is synthetic. The plugin gateway's
// decrypt/connect seams are injected so no DPAPI store and no school is touched.
// Temporary directories live under the repository `tmp/` directory (the OS temp
// directory is not writable in the restricted DSH runtime).
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync, mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createSourceGateway} from '../tools/moodle/source-gateway.mjs';
import {connectionError} from '../tools/moodle/connection-errors.mjs';
import {serve} from '../tools/moodle/server.mjs';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {InMemoryTransport} from '@modelcontextprotocol/sdk/inMemory.js';

const ROOT = fileURLToPath(new URL('../', import.meta.url)).replace(/[\\/]+$/, '');
const TMP = join(ROOT, 'tmp');
mkdirSync(TMP, {recursive: true});
const WORK = mkdtempSync(join(TMP, 'gateway-recovery-'));
const SUPPORT = join(WORK, 'support');
mkdirSync(SUPPORT, {recursive: true});
// The plugin gateway resolves its settings directory from the environment; keep
// it inside the isolated fixture tree before the module reads it.
process.env.MOODLE_STUDY_SUPPORT_DIR = SUPPORT;
const {createRemoteGateway} = await import('../tools/moodle/plugin/remote.mjs');

after(() => {
  try { rmSync(WORK, {recursive: true, force: true, maxRetries: 3}); } catch { /* best effort */ }
});

const SCHOOL = 'https://moodle.invalid';
let counter = 0;

function sourceRoot({credential = 'CRED-A'} = {}) {
  const root = join(WORK, `source-${counter++}`);
  mkdirSync(join(root, '.private'), {recursive: true});
  writeFileSync(join(root, 'config.local.json'),
    JSON.stringify({moodle_url: SCHOOL, timezone: 'Asia/Hong_Kong'}, null, 2));
  writeFileSync(join(root, '.private/moodle-token.dpapi'), Buffer.from(credential));
  return root;
}

// The stored credential bytes are synthetic too, but assertions use the
// decrypted test token labels so no credential file content is echoed.
function fakeDecrypt(bytes) {
  const value = bytes.toString('utf8');
  return value === 'CRED-A' ? 'TOKEN-A' : 'TOKEN-B';
}

function dataDir({credential = 'CRED-A'} = {}) {
  const dir = join(WORK, `data-${counter++}`);
  mkdirSync(join(dir, '.private'), {recursive: true});
  writeFileSync(join(dir, 'config.local.json'),
    JSON.stringify({moodle_url: SCHOOL, timezone: 'Asia/Hong_Kong'}, null, 2));
  writeFileSync(join(dir, '.private/moodle-token.dpapi'), Buffer.from(credential));
  writeFileSync(join(SUPPORT, 'settings.json'), JSON.stringify({schemaVersion: 1, dataDir: dir}));
  return dir;
}

function setCredential(dir, credential) {
  writeFileSync(join(dir, '.private/moodle-token.dpapi'), Buffer.from(credential));
}

function fakeClient({userId = 7, supports = () => true, onCall = null} = {}) {
  const calls = [];
  return {
    baseUrl: SCHOOL,
    userId,
    siteName: 'Synthetic school',
    calls,
    supports,
    async call(method, params = {}) {
      calls.push({method, params: {...params}});
      if (onCall) return onCall({method, params, index: calls.length - 1});
      return {method, params: {...params}};
    },
  };
}

// ---------------------------------------------------------------------------
// Source-install gateway
// ---------------------------------------------------------------------------

test('source gateway joins concurrent connection attempts into one school session', async () => {
  const root = sourceRoot();
  let opens = 0;
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const gateway = createSourceGateway({
    root,
    decrypt: fakeDecrypt,
    connectClient: async config => {
      opens += 1;
      await gate;
      return fakeClient({userId: 7});
    },
  });
  const first = gateway.connect();
  const second = gateway.connect();
  release();
  const [a, b] = await Promise.all([first, second]);
  assert.equal(opens, 1);
  assert.equal(a, b);
  assert.equal(gateway.connected, true);
  assert.equal(gateway.userId, 7);
});

test('source gateway uses a credential saved while the previous connection is in flight', async () => {
  const root = sourceRoot({credential: 'CRED-A'});
  const opened = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let started;
  const firstStarted = new Promise(resolve => { started = resolve; });
  const gateway = createSourceGateway({
    root,
    decrypt: fakeDecrypt,
    connectClient: async (config, token) => {
      const index = opened.length;
      opened.push(token);
      if (index === 0) {
        started();
        await gate;
      }
      return fakeClient({userId: index === 0 ? 7 : 88});
    },
  });
  const first = gateway.call('core_enrol_get_users_courses', {});
  await firstStarted;
  assert.deepEqual(opened, ['TOKEN-A']);
  setCredential(root, 'CRED-B');
  const second = gateway.call('core_enrol_get_users_courses', {});
  release();
  await Promise.all([first, second]);
  assert.deepEqual(opened, ['TOKEN-A', 'TOKEN-B']);
  assert.equal(gateway.userId, 88);
});

test('source gateway: requests queued behind a failing old connection recover with the newly saved login', async () => {
  const root = sourceRoot({credential: 'CRED-A'});
  const opened = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let started;
  const firstStarted = new Promise(resolve => { started = resolve; });
  const gateway = createSourceGateway({
    root,
    decrypt: fakeDecrypt,
    connectClient: async (config, token) => {
      const index = opened.length;
      opened.push(token);
      if (index === 0) {
        started();
        await gate;
        throw connectionError('STUDY_AUTH');
      }
      return fakeClient({userId: 88});
    },
  });
  const firstRequest = gateway.call('core_enrol_get_users_courses', {});
  await firstStarted;
  // The browser login completes while the old-generation attempt is failing.
  setCredential(root, 'CRED-B');
  const secondRequest = gateway.call('core_enrol_get_users_courses', {});
  release();
  const [first, second] = await Promise.all([firstRequest, secondRequest]);
  // Neither request surfaces the stale authentication failure: both recover on
  // one shared new-generation connection instead of each opening a session.
  assert.equal(first.method, 'core_enrol_get_users_courses');
  assert.equal(second.method, 'core_enrol_get_users_courses');
  assert.deepEqual(opened, ['TOKEN-A', 'TOKEN-B']);
  assert.equal(gateway.userId, 88);
});

test('source gateway replays a read-only call once when a re-login completed during the failure', async () => {
  const root = sourceRoot({credential: 'CRED-A'});
  const opened = [];
  let callCount = 0;
  const gateway = createSourceGateway({
    root,
    decrypt: fakeDecrypt,
    connectClient: async (config, token) => {
      const index = opened.length;
      opened.push(token);
      return fakeClient({userId: index === 0 ? 7 : 88, onCall: () => {
        callCount += 1;
        if (index === 0) {
          // The browser login completes while the old session is failing.
          setCredential(root, 'CRED-B');
          throw connectionError('STUDY_AUTH');
        }
        return {recovered: true};
      }});
    },
  });
  const result = await gateway.call('core_enrol_get_users_courses', {});
  assert.deepEqual(result, {recovered: true});
  assert.deepEqual(opened, ['TOKEN-A', 'TOKEN-B']);
  assert.equal(callCount, 2);
  assert.equal(gateway.userId, 88);
});

test('source gateway keeps the no-automatic-retry contract when the saved login did not change', async () => {
  const root = sourceRoot({credential: 'CRED-A'});
  const opened = [];
  let callCount = 0;
  const gateway = createSourceGateway({
    root,
    decrypt: fakeDecrypt,
    connectClient: async (config, token) => {
      opened.push(token);
      return fakeClient({onCall: () => {
        callCount += 1;
        throw connectionError('STUDY_AUTH');
      }});
    },
  });
  await assert.rejects(gateway.call('core_enrol_get_users_courses', {}), error => {
    assert.equal(JSON.parse(error.message).connection_error, 'STUDY_AUTH');
    return true;
  });
  assert.deepEqual(opened, ['TOKEN-A']);
  assert.equal(callCount, 1);
});

test('source gateway capability probe never answers for an older saved login or unreadable files', async () => {
  const root = sourceRoot();
  let opens = 0;
  const gateway = createSourceGateway({
    root,
    decrypt: fakeDecrypt,
    connectClient: async () => {
      opens += 1;
      return fakeClient({userId: 7, supports: () => false});
    },
  });
  await gateway.connect();
  assert.equal(gateway.supports('gradereport_user_get_grade_items'), false);
  assert.equal(gateway.connected, true);

  setCredential(root, 'CRED-B');
  assert.equal(gateway.supports('gradereport_user_get_grade_items'), true);
  assert.equal(gateway.connected, false);
  await gateway.connect();
  assert.equal(opens, 2);
  assert.equal(gateway.supports('gradereport_user_get_grade_items'), false);

  // Unreadable saved files must never throw from a capability probe.
  rmSync(join(root, 'config.local.json'));
  assert.equal(gateway.supports('gradereport_user_get_grade_items'), true);
  assert.equal(gateway.connected, false);
  await assert.rejects(gateway.call('core_enrol_get_users_courses', {}), error => {
    assert.equal(JSON.parse(error.message).error, 'STUDY_SETUP_REQUIRED');
    return true;
  });
});

// ---------------------------------------------------------------------------
// Plugin gateway (injected decrypt/connect seams; no settings, DPAPI or network)
// ---------------------------------------------------------------------------

test('plugin gateway joins concurrent connection attempts into one school session', async () => {
  dataDir({credential: 'CRED-A'});
  let opens = 0;
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const gateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async () => {
      opens += 1;
      await gate;
      return fakeClient({userId: 7});
    },
  });
  const first = gateway.connect();
  const second = gateway.connect();
  release();
  const [a, b] = await Promise.all([first, second]);
  assert.equal(opens, 1);
  assert.equal(a, b);
  assert.equal(gateway.connected, true);
});

test('plugin gateway uses a credential saved while the previous connection is in flight', async () => {
  const dir = dataDir({credential: 'CRED-A'});
  const opened = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let started;
  const firstStarted = new Promise(resolve => { started = resolve; });
  const gateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async (config, token) => {
      const index = opened.length;
      opened.push(token);
      if (index === 0) {
        started();
        await gate;
      }
      return fakeClient({userId: index === 0 ? 7 : 88});
    },
  });
  const first = gateway.connect();
  await firstStarted;
  setCredential(dir, 'CRED-B');
  const second = gateway.connect();
  release();
  const [a, b] = await Promise.all([first, second]);
  assert.deepEqual(opened, ['TOKEN-A', 'TOKEN-B']);
  assert.equal(a.userId, 7);
  assert.equal(b.userId, 88);
  assert.equal(gateway.userId, 88);
});

test('plugin gateway never answers API support from a session connected with an older saved login', async () => {
  const dir = dataDir({credential: 'CRED-A'});
  const opened = [];
  const gateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async (config, token) => {
      const index = opened.length;
      opened.push(token);
      return fakeClient({userId: index === 0 ? 7 : 88, supports: () => index !== 0});
    },
  });
  await gateway.connect();
  assert.equal(gateway.supports('gradereport_user_get_grade_items'), false);
  setCredential(dir, 'CRED-B');
  assert.equal(gateway.supports('gradereport_user_get_grade_items'), true);
  assert.equal(gateway.connected, false);
  await gateway.connect();
  assert.equal(gateway.supports('gradereport_user_get_grade_items'), true);
  assert.deepEqual(opened, ['TOKEN-A', 'TOKEN-B']);

  // A throwing probe is treated like an unreadable session: drop and let the
  // next tool call reconnect instead of surfacing a capability error.
  dataDir({credential: 'CRED-A'});
  const throwingGateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async () => fakeClient({supports: () => { throw new Error('probe failure'); }}),
  });
  await throwingGateway.connect();
  assert.equal(throwingGateway.supports('gradereport_user_get_grade_items'), true);
  assert.equal(throwingGateway.connected, false);
});

test('plugin gateway replays a read-only call once when a re-login completed during the failure', async () => {
  const dir = dataDir({credential: 'CRED-A'});
  const opened = [];
  const forwarded = [];
  let calls = 0;
  const gateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async (config, token) => {
      const index = opened.length;
      opened.push(token);
      return fakeClient({userId: index === 0 ? 7 : 88, onCall: ({params}) => {
        calls += 1;
        forwarded.push({...params});
        if (index === 0) {
          setCredential(dir, 'CRED-B');
          throw connectionError('STUDY_AUTH');
        }
        return {recovered: true};
      }});
    },
  });
  const result = await gateway.call('gradereport_user_get_grade_items', {userid: 7, courseid: 101});
  assert.deepEqual(result, {recovered: true});
  assert.deepEqual(opened, ['TOKEN-A', 'TOKEN-B']);
  assert.equal(calls, 2);
  // The replay on the reconnected (account 88) session must carry the new
  // authenticated account id, not the stale one from the original request.
  assert.deepEqual(forwarded[1], {userid: 88, courseid: 101});
});

test('plugin gateway forwards read-query account ids from the authenticated session', async () => {
  dataDir({credential: 'CRED-A'});
  const forwarded = [];
  let connections = 0;
  const gateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async () => {
      connections += 1;
      return {
        baseUrl: SCHOOL, userId: 88, supports: () => true,
        async call(method, params) { forwarded.push({method, params}); return {ok: true}; },
      };
    },
  });
  // The first call starts before any connection: connect happens first, then the
  // stale request ids are replaced by the authenticated account.
  const grades = await gateway.call('gradereport_user_get_grade_items',
    {userid: 7, useridto: 7, courseid: 101});
  assert.deepEqual(grades, {ok: true});
  assert.equal(connections, 1);
  assert.deepEqual(forwarded[0], {method: 'gradereport_user_get_grade_items',
    params: {userid: 88, useridto: 88, courseid: 101}});

  // A cached session keeps the same contract without opening a new connection.
  await gateway.call('message_popup_get_popup_notifications', {useridto: 7, newestfirst: 1});
  assert.equal(connections, 1);
  assert.deepEqual(forwarded[1].params, {useridto: 88, newestfirst: 1});

  // Non-object parameters are forwarded untouched (not cloned or replaced).
  const raw = ['opaque'];
  await gateway.call('core_webservice_get_site_info', raw);
  assert.equal(forwarded[2].params, raw);

  // No authenticated id yet (anonymous client): parameters stay as provided.
  dataDir({credential: 'CRED-B'});
  const anonymous = [];
  const anonymousGateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async () => ({
      baseUrl: SCHOOL, userId: null, supports: () => true,
      async call(method, params) { anonymous.push(params); return {ok: true}; },
    }),
  });
  await anonymousGateway.call('message_popup_get_popup_notifications', {useridto: 7});
  assert.deepEqual(anonymous[0], {useridto: 7});
});

test('plugin MCP read tools send the authenticated account, not the saved-profile id', async () => {
  const dir = dataDir({credential: 'CRED-A'});
  // The local profile still records the previous account; before the lazy
  // connection the tool builds its request from that fallback id.
  mkdirSync(join(dir, 'Study/_System'), {recursive: true});
  writeFileSync(join(dir, 'Study/_System/state.json'),
    JSON.stringify({settings: {profile_account_scope: {site: SCHOOL, user_id: 7}}}));
  const forwarded = [];
  const gateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async () => ({
      baseUrl: SCHOOL, userId: 88, supports: () => true,
      async call(method, params) {
        forwarded.push({method, params: {...params}});
        if (method === 'gradereport_user_get_grade_items') return {usergrades: [{userid: 88, gradeitems: []}]};
        return {};
      },
    }),
  });
  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  const server = await serve(gateway, serverTransport);
  const client = new Client({name: 'plugin-account-forward', version: '1.0.0'});
  try {
    await client.connect(clientTransport);
    const response = await client.callTool({name: 'moodle_get_grades', arguments: {courseId: 101}});
    assert.equal(response.isError, undefined, response.content?.[0]?.text);
    const grades = JSON.parse(response.content[0].text);
    assert.equal(grades.complete, true);
    assert.deepEqual(grades.report.usergrades, [{userid: 88, gradeitems: []}]);
    assert.equal(forwarded[0].method, 'gradereport_user_get_grade_items');
    assert.equal(forwarded[0].params.userid, 88);
    assert.equal(forwarded[0].params.courseid, 101);
  } finally {
    await client.close();
    await server.close();
  }
});

test('plugin gateway keeps the no-automatic-retry contract when the saved login did not change', async () => {
  dataDir({credential: 'CRED-A'});
  const opened = [];
  let calls = 0;
  const gateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async () => {
      opened.push('one-session');
      return fakeClient({onCall: () => {
        calls += 1;
        throw connectionError('STUDY_AUTH');
      }});
    },
  });
  await assert.rejects(gateway.call('core_enrol_get_users_courses', {}), error => {
    assert.equal(error?.code, 'STUDY_AUTH');
    return true;
  });
  assert.equal(opened.length, 1);
  assert.equal(calls, 1);
});

test('plugin gateway fails closed with the existing structured state when login files are missing', async () => {
  dataDir({credential: 'CRED-A'});
  const gateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async () => fakeClient(),
  });
  rmSync(join(WORK, 'support', 'settings.json'));
  await assert.rejects(gateway.connect(), error => error?.code === 'LOCATION_REQUIRED');
});

test('plugin gateway capability probe stays optimistic when saved files become unreadable', async () => {
  const dir = dataDir({credential: 'CRED-A'});
  const gateway = createRemoteGateway({
    decrypt: fakeDecrypt,
    connectClient: async () => fakeClient({supports: () => false}),
  });
  await gateway.connect();
  assert.equal(gateway.supports('gradereport_user_get_grade_items'), false);
  rmSync(join(dir, 'config.local.json'));
  assert.equal(gateway.supports('gradereport_user_get_grade_items'), true);
  assert.equal(gateway.connected, false);
});
