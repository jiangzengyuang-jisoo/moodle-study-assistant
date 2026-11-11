// Regression tests for the source-install MCP server startup.
//
// The installer registers `node tools/moodle/run.mjs mcp` before the student has
// completed the school browser login. Startup must therefore finish the MCP
// handshake without config, credential or state, keep all read-only tools
// registered, and return one structured login/setup tool error instead of
// crashing. Remote tools then connect lazily: a login written in the same chat
// becomes usable without a restart, and a network failure is retried by the next
// call. All data roots are synthetic and live under the repository `tmp/`
// directory (the OS temp directory is not writable in the restricted DSH
// runtime); decryption and the connected-client factory are injected so no real
// DPAPI credential is ever read or decrypted and no school is contacted.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
import {InMemoryTransport} from '@modelcontextprotocol/sdk/inMemory.js';
import {serve} from '../tools/moodle/server.mjs';
import {connectionError} from '../tools/moodle/connection-errors.mjs';
import {createSourceChapterContext, createSourceGateway} from '../tools/moodle/source-gateway.mjs';

const ROOT = fileURLToPath(new URL('../', import.meta.url)).replace(/[\\/]+$/, '');
const TMP = join(ROOT, 'tmp');
mkdirSync(TMP, {recursive: true});
const WORK = mkdtempSync(join(TMP, 'mcp-startup-'));
after(() => {
  try { rmSync(WORK, {recursive: true, force: true, maxRetries: 3}); } catch { /* best effort */ }
});

const SCHOOL = 'https://moodle.invalid';
const SCHOOL_TWO = 'https://moodle-two.invalid';
const SECRET_CIPHERTEXT = 'SYNTHETIC-ENCRYPTED-AUTHORIZATION-NEVER-REAL';
const CHAT = 'session-abcdef12-abcd-4321-abcd-123456789012';

// The exact 15 pre-existing read tools plus the always-registered chapter tool.
const EXPECTED_TOOLS = [
  'moodle_get_assignment', 'moodle_get_calendar_events', 'moodle_get_course',
  'moodle_get_forum_discussions', 'moodle_get_grades', 'moodle_get_notifications',
  'moodle_get_quiz_attempts', 'moodle_get_site_info', 'moodle_list_assignments',
  'moodle_list_courses', 'moodle_list_forums', 'moodle_list_quizzes', 'moodle_list_resources',
  'study_all_discussions', 'study_check_chapter', 'study_discussion_posts',
];

// Child process wrapper: report the server process exit code/signal to a file so
// graceful shutdown after the MCP client closes can be asserted directly.
const WRAPPER = join(WORK, 'stdio-wrapper.mjs');
writeFileSync(WRAPPER, `import {spawn} from 'node:child_process';
import {writeFileSync} from 'node:fs';
const [script, exitFile] = process.argv.slice(2);
const child = spawn(process.execPath, [script, 'mcp'], {stdio: 'inherit', windowsHide: true, env: process.env});
child.on('close', (code, signal) => { writeFileSync(exitFile, JSON.stringify({code, signal})); process.exit(code ?? 0); });
`);

let counter = 0;
function dataRoot(label) {
  const dir = join(WORK, `data-${label}-${counter++}`);
  mkdirSync(dir, {recursive: true});
  return dir;
}

function writeConfig(root, moodleUrl = SCHOOL) {
  writeFileSync(join(root, 'config.local.json'),
    JSON.stringify({moodle_url: moodleUrl, timezone: 'Asia/Hong_Kong'}, null, 2));
}

function writeState(root) {
  mkdirSync(join(root, 'Study/_System'), {recursive: true});
  writeFileSync(join(root, 'Study/_System/state.json'),
    JSON.stringify({schema_version: 1, revision: 0, timezone: 'Asia/Hong_Kong', courses: [], settings: {}}));
}

function writeCredential(root, text = SECRET_CIPHERTEXT) {
  mkdirSync(join(root, '.private'), {recursive: true});
  writeFileSync(join(root, '.private/moodle-token.dpapi'), Buffer.from(text, 'utf8'));
}

function fakeClient({site = SCHOOL, userId = 42, marker = 'connection-1'} = {}) {
  const calls = [];
  return {
    baseUrl: site,
    userId,
    siteName: 'Synthetic school',
    calls,
    supports: () => true,
    async connect() { return this; },
    async call(method, params = {}) {
      calls.push({method, params: {...params}});
      if (method === 'core_enrol_get_users_courses')
        return [{id: 101, fullname: `Demo course ${marker}`, shortname: 'DEMO'}];
      return {marker};
    },
  };
}

function instrumentedGateway(root, overrides = {}) {
  const connections = [];
  const gateway = createSourceGateway({
    root,
    decrypt: bytes => `decrypted:${bytes.toString('utf8')}`,
    connectClient: async (config, token) => {
      const client = fakeClient({site: config.moodle_url, marker: `connection-${connections.length + 1}`});
      connections.push({site: config.moodle_url, token, client});
      return client;
    },
    ...overrides,
  });
  return {gateway, connections};
}

const toolText = result => result.content?.[0]?.text ?? '';
const toolPayload = result => JSON.parse(toolText(result));

async function startStdioServer(label, root) {
  const exitFile = join(WORK, `exit-${label}.json`);
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [WRAPPER, join(ROOT, 'tools/moodle/run.mjs'), exitFile],
    env: {TMP: WORK, TEMP: WORK, STUDY_DATA_ROOT: root},
    stderr: 'pipe',
  });
  const stderr = [];
  transport.stderr?.on('data', chunk => stderr.push(String(chunk)));
  const client = new Client({name: `mcp-startup-${label}`, version: '1.0.0'});
  await client.connect(transport);
  return {
    client,
    stderr: () => stderr.join(''),
    async finish() {
      const started = Date.now();
      await client.close();
      const elapsed = Date.now() - started;
      await new Promise(resolve => setTimeout(resolve, 50));
      return {elapsed, ...JSON.parse(readFileSync(exitFile, 'utf8'))};
    },
  };
}

function assertNoLeak(text, root, secret = SECRET_CIPHERTEXT) {
  const normalized = String(text);
  assert(!normalized.includes(root), 'tool/stderr output must not expose the data root');
  assert(!normalized.includes(root.replaceAll('\\', '/')), 'tool/stderr output must not expose the data root');
  assert(!normalized.includes(secret), 'tool/stderr output must not expose credential bytes');
}

// ---------------------------------------------------------------------------
// Real stdio subprocess: startup before login/initialization must succeed.
// ---------------------------------------------------------------------------

test('source MCP server starts uninitialized, exposes all tools and returns structured setup errors', async () => {
  const root = dataRoot('uninitialized'); // completely empty: no config, token or state
  const server = await startStdioServer('uninitialized', root);
  try {
    const tools = (await server.client.listTools()).tools.map(tool => tool.name).sort();
    assert.deepEqual(tools, EXPECTED_TOOLS);
    assert.equal(tools.length, 16);

    const courses = await server.client.callTool({name: 'moodle_list_courses', arguments: {}});
    assert.equal(courses.isError, true);
    const courseError = toolPayload(courses);
    assert.equal(courseError.error, 'STUDY_SETUP_REQUIRED');
    assert.match(courseError.message, /本地资料/);
    assert.equal(courseError.login_required, undefined);
    assertNoLeak(toolText(courses), root);

    const chapter = await server.client.callTool({name: 'study_check_chapter',
      arguments: {course_id: 'moodle-1', section: 'Topic 5', chat_id: CHAT}});
    assert.equal(chapter.isError, true);
    const chapterError = toolPayload(chapter);
    assert.equal(chapterError.error, 'STUDY_SETUP_REQUIRED');
    assertNoLeak(toolText(chapter), root);

    // The tool errors above must not take the server down or drop the client.
    await server.client.ping();
    assert.equal((await server.client.listTools()).tools.length, 16);
    assertNoLeak(server.stderr(), root);
  } finally {
    const exit = await server.finish();
    assert.equal(exit.signal, null);
    assert.equal(exit.code, 0);
    assert(exit.elapsed < 2000, `graceful shutdown expected, took ${exit.elapsed}ms`);
  }
});

test('source MCP server starts before login: tools stay registered and report STRUCTURED login state', async () => {
  const root = dataRoot('logged-out');
  writeConfig(root);   // setup.cmd wrote config...
  writeState(root);    // ...and initialized the profile...
  // ...but connect.cmd has not saved a school credential yet: no .private token.
  const server = await startStdioServer('logged-out', root);
  try {
    const tools = (await server.client.listTools()).tools.map(tool => tool.name).sort();
    assert.deepEqual(tools, EXPECTED_TOOLS);

    const courses = await server.client.callTool({name: 'moodle_list_courses', arguments: {}});
    assert.equal(courses.isError, true);
    const courseError = toolPayload(courses);
    assert.equal(courseError.error, 'STUDY_LOGIN_REQUIRED');
    assert.equal(courseError.login_required, true);
    assert.match(courseError.message, /已下载的本地资料/);
    assert.match(courseError.next_step, /浏览器登录/);
    assertNoLeak(toolText(courses), root);

    const chapter = await server.client.callTool({name: 'study_check_chapter',
      arguments: {course_id: 'moodle-1', section: 'Topic 5', chat_id: CHAT}});
    assert.equal(chapter.isError, true);
    const chapterError = toolPayload(chapter);
    assert.equal(chapterError.error, 'STUDY_LOGIN_REQUIRED');
    assert.equal(chapterError.login_required, true);
    assertNoLeak(toolText(chapter), root);

    await server.client.ping();
    assertNoLeak(server.stderr(), root);
  } finally {
    const exit = await server.finish();
    assert.equal(exit.signal, null);
    assert.equal(exit.code, 0);
  }
});

// ---------------------------------------------------------------------------
// Protocol-level recovery inside one running server (in-memory transport).
// ---------------------------------------------------------------------------

test('login written while the MCP server is running is picked up without a restart', async t => {
  const root = dataRoot('recover');
  writeConfig(root);
  writeState(root);
  const {gateway, connections} = instrumentedGateway(root);
  const chapterContext = createSourceChapterContext({root, gateway});
  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  const server = await serve(gateway, serverTransport, chapterContext);
  const client = new Client({name: 'mcp-startup-recovery', version: '1.0.0'});
  t.after(async () => { await client.close(); await server.close(); });
  await client.connect(clientTransport);
  const call = (name, args = {}) => client.callTool({name, arguments: args});

  assert.equal((await client.listTools()).tools.length, 16);

  // Before login: remote tools must not crash or disconnect the server.
  const anonymous = toolPayload(await call('moodle_list_courses'));
  assert.equal(anonymous.error, 'STUDY_LOGIN_REQUIRED');
  const anonymousChapter = toolPayload(await call('study_check_chapter',
    {course_id: 'moodle-1', section: 'Topic 5', chat_id: CHAT}));
  assert.equal(anonymousChapter.error, 'STUDY_LOGIN_REQUIRED');
  assert.equal(connections.length, 0);

  // A browser login in the same chat writes the synthetic credential.
  writeCredential(root, 'SYNTHETIC-TOKEN-ONE');
  const loggedIn = await call('moodle_list_courses');
  assert.equal(loggedIn.isError, undefined);
  assert.match(toolText(loggedIn), /Demo course connection-1/);
  assert.equal(connections.length, 1);
  assert.equal(connections[0].token, 'decrypted:SYNTHETIC-TOKEN-ONE');
  // The lazy client substituted the authenticated account for the pre-connect
  // null identity, so read scoping matches the previous eager implementation.
  assert.equal(connections[0].client.calls[0].params.userid, 42);

  // Cached: the saved credential is reused, no second school session is opened.
  await call('moodle_list_courses');
  assert.equal(connections.length, 1);

  // The chapter tool builds its pinned context on first use after login; with no
  // selected course it reports a chapter scope error (i.e. it is usable now).
  const chapter = toolPayload(await call('study_check_chapter',
    {course_id: 'moodle-1', section: 'Topic 5', chat_id: CHAT}));
  assert.equal(chapter.error, 'CHAPTER_COURSE_NOT_SELECTED');

  // Re-login / credential replacement: same process reconnects with the new
  // credential instead of reusing the old client.
  writeCredential(root, 'SYNTHETIC-TOKEN-TWO');
  const reconnected = await call('moodle_list_courses');
  assert.match(toolText(reconnected), /Demo course connection-2/);
  assert.equal(connections.length, 2);
  assert.equal(connections[1].token, 'decrypted:SYNTHETIC-TOKEN-TWO');

  // Config change is picked up the same way for remote tools...
  writeConfig(root, SCHOOL_TWO);
  const moved = await call('moodle_list_courses');
  assert.match(toolText(moved), /Demo course connection-3/);
  assert.equal(connections.length, 3);
  assert.equal(connections[2].site, SCHOOL_TWO);

  // ...while the chapter context keeps the existing pinned-generation contract.
  const stale = toolPayload(await call('study_check_chapter',
    {course_id: 'moodle-1', section: 'Topic 5', chat_id: CHAT}));
  assert.equal(stale.error, 'STUDY_MCP_RESTART_REQUIRED');
  assertNoLeak(JSON.stringify(stale), root);
});

// ---------------------------------------------------------------------------
// Re-login while a connection is already in flight / an API-support answer is
// cached: the next request must use the newly saved credential, not the old
// session's identity or API list. All connections are synthetic and offline.
// ---------------------------------------------------------------------------

test('a credential saved while the first lazy connection is in flight is used by the next request', async () => {
  const root = dataRoot('race');
  writeConfig(root);
  writeCredential(root, 'SYNTHETIC-TOKEN-OLD');
  const connections = [];
  let connectCalls = 0;
  let releaseFirst;
  const firstGate = new Promise(resolve => { releaseFirst = resolve; });
  let signalFirstStarted;
  const firstStarted = new Promise(resolve => { signalFirstStarted = resolve; });
  const gateway = createSourceGateway({
    root,
    decrypt: bytes => `decrypted:${bytes.toString('utf8')}`,
    connectClient: async (config, token) => {
      const index = ++connectCalls;
      if (index === 1) {
        // Stall the old-credential connection until the test saves a new login.
        signalFirstStarted();
        await firstGate;
      }
      const client = fakeClient({site: config.moodle_url, userId: index === 1 ? 7 : 88,
        marker: index === 1 ? 'old-connection' : 'new-connection'});
      connections.push({token, userId: client.userId, client});
      return client;
    },
  });

  const firstRequest = gateway.call('core_enrol_get_users_courses', {});
  await firstStarted;
  assert.equal(connections.length, 0, 'the first connection must still be in flight');
  // The browser login completes while the old connection is still connecting.
  writeCredential(root, 'SYNTHETIC-TOKEN-NEW');
  const secondRequest = gateway.call('core_enrol_get_users_courses', {});
  releaseFirst();
  const first = await firstRequest;
  const second = await secondRequest;

  assert.equal(JSON.stringify(first).includes('old-connection'), true);
  assert.equal(JSON.stringify(second).includes('new-connection'), true);
  assert.equal(connections.length, 2);
  assert.equal(connections[0].token, 'decrypted:SYNTHETIC-TOKEN-OLD');
  assert.equal(connections[0].userId, 7);
  assert.equal(connections[1].token, 'decrypted:SYNTHETIC-TOKEN-NEW');
  assert.equal(connections[1].userId, 88);
  assert.equal(gateway.userId, 88);
});

test('a credential replacement clears a cached "API not supported" answer so read queries reconnect', async () => {
  const root = dataRoot('supports');
  writeConfig(root);
  writeCredential(root, 'SYNTHETIC-TOKEN-OLD');
  const connections = [];
  const apiCalls = [];
  const gateway = createSourceGateway({
    root,
    decrypt: bytes => `decrypted:${bytes.toString('utf8')}`,
    connectClient: async (config, token) => {
      const index = connections.length;
      const client = {
        baseUrl: config.moodle_url,
        userId: index === 0 ? 7 : 88,
        siteName: 'Synthetic school',
        // The old session did not advertise these APIs; a real re-login into a
        // session that does must not stay blocked by that cached answer.
        supports: () => index !== 0,
        async call(method, params) {
          apiCalls.push({index, method, params: {...params}});
          if (method === 'gradereport_user_get_grade_items') return {usergrades: [{userid: 88, gradeitems: []}]};
          if (method === 'message_popup_get_popup_notifications') return {notifications: [{id: 21}], unreadcount: 1};
          if (method.startsWith('core_calendar_get_action_events_by_')) return {events: [], lastid: 0};
          return {};
        },
      };
      connections.push({token, userId: client.userId});
      return client;
    },
  });

  // Prime the first session together with its "API not supported" answers.
  await gateway.connect();
  assert.equal(gateway.supports('gradereport_user_get_grade_items'), false);
  assert.equal(gateway.connected, true);

  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  const server = await serve(gateway, serverTransport);
  const client = new Client({name: 'mcp-supports-recovery', version: '1.0.0'});
  try {
    await client.connect(clientTransport);
    // Before the re-login the cached answer is what the tools see.
    for (const [name, args] of [['moodle_get_grades', {courseId: 101}],
      ['moodle_get_notifications', {}], ['moodle_get_calendar_events', {courseId: 101}]]) {
      const cached = toolPayload(await client.callTool({name, arguments: args}));
      assert.equal(cached.complete, false);
      assert.equal(cached.reason, 'api_not_enabled');
    }
    assert.equal(connections.length, 1);

    writeCredential(root, 'SYNTHETIC-TOKEN-NEW');
    // The cached session is dropped; the probe is optimistic so the tool call
    // can reconnect instead of returning api_not_enabled forever.
    assert.equal(gateway.supports('gradereport_user_get_grade_items'), true);
    assert.equal(gateway.connected, false);

    const response = await client.callTool({name: 'moodle_get_grades', arguments: {courseId: 101}});
    assert.equal(response.isError, undefined, toolText(response));
    const grades = toolPayload(response);
    assert.equal(grades.complete, true);
    assert.deepEqual(grades.report.usergrades, [{userid: 88, gradeitems: []}]);
    assert.equal(connections.length, 2);
    assert.equal(connections[1].token, 'decrypted:SYNTHETIC-TOKEN-NEW');
    const gradeCall = apiCalls.find(entry => entry.method === 'gradereport_user_get_grade_items');
    assert.equal(gradeCall?.index, 1);
    assert.equal(gradeCall?.params.userid, 88);

    // Notifications and calendar now use the same fresh session.
    const notifications = toolPayload(await client.callTool({name: 'moodle_get_notifications', arguments: {}}));
    assert.equal(notifications.complete, true);
    assert.deepEqual(notifications.notifications, [{id: 21}]);
    const notificationCall = apiCalls.find(entry => entry.method === 'message_popup_get_popup_notifications');
    assert.equal(notificationCall?.index, 1);
    assert.equal(notificationCall?.params.useridto, 88);
    const calendar = toolPayload(await client.callTool({name: 'moodle_get_calendar_events',
      arguments: {courseId: 101, timeFrom: 0, timeTo: 100}}));
    assert.equal(calendar.complete, true);
    assert.equal(connections.length, 2);
  } finally {
    await client.close();
    await server.close();
  }
});

// ---------------------------------------------------------------------------
// Lazy gateway unit tests (injected credential/decrypt/connect: no DPAPI, no network).
// ---------------------------------------------------------------------------

test('lazy gateway reports missing setup config and missing login as distinct structured states', async () => {
  const root = dataRoot('states');
  const {gateway, connections} = instrumentedGateway(root);
  await assert.rejects(gateway.call('core_enrol_get_users_courses', {userid: null}), error => {
    const payload = JSON.parse(error.message);
    assert.equal(payload.error, 'STUDY_SETUP_REQUIRED');
    assertNoLeak(error.message, root);
    return true;
  });

  writeConfig(root);
  await assert.rejects(gateway.call('core_enrol_get_users_courses', {userid: null}), error => {
    const payload = JSON.parse(error.message);
    assert.equal(payload.error, 'STUDY_LOGIN_REQUIRED');
    assert.equal(payload.login_required, true);
    assert.match(payload.next_step, /浏览器登录/);
    assertNoLeak(error.message, root);
    return true;
  });
  assert.equal(connections.length, 0);
  assert.equal(gateway.connected, false);
});

test('lazy gateway connects on first call, caches the client and substitutes the account id', async () => {
  const root = dataRoot('connect');
  writeConfig(root);
  writeCredential(root, 'SYNTHETIC-TOKEN-ONE');
  const {gateway, connections} = instrumentedGateway(root);

  const courses = await gateway.call('core_enrol_get_users_courses', {userid: null});
  assert.equal(courses[0].fullname, 'Demo course connection-1');
  assert.equal(connections.length, 1);
  assert.equal(connections[0].site, SCHOOL);
  assert.equal(connections[0].token, 'decrypted:SYNTHETIC-TOKEN-ONE');
  assert.equal(connections[0].client.calls[0].params.userid, 42);
  assert.equal(gateway.connected, true);
  assert.equal(gateway.userId, 42);
  assert.equal(gateway.baseUrl, SCHOOL);

  await gateway.call('core_enrol_get_users_courses', {userid: null});
  assert.equal(connections.length, 1, 'a healthy connection must be reused');
});

test('lazy gateway retries the next call after a network failure and after a damaged credential', async () => {
  const root = dataRoot('retry');
  writeConfig(root);
  writeCredential(root, 'SYNTHETIC-TOKEN-ONE');

  let attempts = 0;
  const network = instrumentedGateway(root, {
    connectClient: async (config, token) => {
      attempts += 1;
      if (attempts === 1) throw connectionError('STUDY_NETWORK');
      return fakeClient({site: config.moodle_url, marker: `retry-${attempts}`});
    },
  });
  await assert.rejects(network.gateway.call('core_enrol_get_users_courses', {userid: null}), error => {
    const payload = JSON.parse(error.message);
    assert.equal(payload.connection_error, 'STUDY_NETWORK');
    assert.equal(payload.retryable, true);
    assertNoLeak(error.message, root);
    return true;
  });
  // The next call re-reads the same credential and retries; no restart needed.
  const retried = await network.gateway.call('core_enrol_get_users_courses', {userid: null});
  assert.equal(retried[0].fullname, 'Demo course retry-2');
  assert.equal(network.gateway.connected, true);

  // A decrypt failure keeps the existing security semantics and is not cached:
  // after the saved credential becomes usable again, the next call connects.
  const decryptErrors = [];
  const decrypt = bytes => {
    if (bytes.toString('utf8') === 'DAMAGED') {
      const error = new Error('private decrypt detail');
      error.code = 'STUDY_TOKEN_DECRYPT_FAILED';
      decryptErrors.push(error.code);
      throw error;
    }
    return `decrypted:${bytes.toString('utf8')}`;
  };
  const damaged = dataRoot('decrypt');
  writeConfig(damaged);
  writeCredential(damaged, 'DAMAGED');
  const {gateway} = instrumentedGateway(damaged, {decrypt});
  await assert.rejects(gateway.call('core_enrol_get_users_courses', {userid: null}), error => {
    const payload = JSON.parse(error.message);
    assert.equal(payload.connection_error, 'STUDY_TOKEN_DECRYPT_FAILED');
    assert(!error.message.includes('private decrypt detail'));
    assertNoLeak(error.message, damaged);
    return true;
  });
  assert.deepEqual(decryptErrors, ['STUDY_TOKEN_DECRYPT_FAILED']);
  writeCredential(damaged, 'SYNTHETIC-TOKEN-RESTORED');
  const recovered = await gateway.call('core_enrol_get_users_courses', {userid: null});
  assert.equal(recovered[0].fullname, 'Demo course connection-1');
  assert.equal(gateway.connected, true);
});

test('lazy gateway maps unsanitized school API failures to fixed structured errors', async () => {
  const root = dataRoot('api-error');
  writeConfig(root);
  writeCredential(root, 'SYNTHETIC-TOKEN-ONE');
  const {gateway} = instrumentedGateway(root, {
    connectClient: async () => ({
      baseUrl: SCHOOL, userId: 42, supports: () => true,
      async call() { throw new Error('Moodle API failed: invalidparameter PRIVATE-RESPONSE-BODY'); },
    }),
  });
  await assert.rejects(gateway.call('core_course_get_contents', {courseid: 101}), error => {
    const payload = JSON.parse(error.message);
    assert.equal(payload.error, 'STUDY_REMOTE_FAILED');
    assert.equal(payload.reason, 'invalidparameter');
    assert(!error.message.includes('PRIVATE-RESPONSE-BODY'));
    assertNoLeak(error.message, root);
    return true;
  });
});

test('lazy chapter context is built on first use and pins the credential generation', async () => {
  const root = dataRoot('chapter-context');
  writeConfig(root);
  writeState(root);
  const {gateway, connections} = instrumentedGateway(root);
  const context = createSourceChapterContext({root, gateway});

  // No token: the chapter tool stays registered but reports login, not a crash.
  assert.throws(() => context.beforeCheck(), error => error?.code === 'STUDY_LOGIN_REQUIRED');
  await assert.rejects(context.connect(), {code: 'STUDY_LOGIN_REQUIRED'});
  assert.equal(connections.length, 0);

  writeCredential(root, 'SYNTHETIC-TOKEN-ONE');
  context.beforeCheck(); // local generation check only: no network connection
  assert.equal(connections.length, 0);
  const connected = await context.connect();
  assert.equal(connections.length, 1);
  assert.equal(connected.userId, 42);

  // After the first successful build, later credential changes keep the existing
  // restart-required contract instead of silently using a different authorization.
  writeCredential(root, 'SYNTHETIC-TOKEN-TWO');
  let pinFailure = null;
  try { context.beforeCheck(); } catch (error) { pinFailure = error; }
  assert.equal(pinFailure?.code, 'STUDY_MCP_RESTART_REQUIRED');
  assert(!String(pinFailure.message).includes('SYNTHETIC-TOKEN-TWO'));
  assertNoLeak(pinFailure.message, root);
  await assert.rejects(context.connect(), {code: 'STUDY_MCP_RESTART_REQUIRED'});
});

test('lazy chapter context reports a missing learning profile as setup, not as an account conflict', async () => {
  const root = dataRoot('chapter-no-state');
  writeConfig(root);
  writeCredential(root, 'SYNTHETIC-TOKEN-ONE');
  const {gateway} = instrumentedGateway(root);
  const context = createSourceChapterContext({root, gateway});
  assert.throws(() => context.beforeCheck(), error => error?.code === 'STUDY_SETUP_REQUIRED');
  await assert.rejects(context.connect(), {code: 'STUDY_SETUP_REQUIRED'});
});
