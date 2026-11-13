// Offline forwarding tests for the source-install school-connection gateway.
//
// These cover the recovery contract that tools rely on:
// - a connection opened for an older saved config/credential is never handed to
//   a request that started after a newer login was saved;
// - a cached "API not supported" answer is invalidated by a saved login change;
// - a read-only call is replayed at most once, and only when the saved
//   generation changed while the request was failing.
//
// Every credential, client and network path is synthetic. The source gateway's
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
