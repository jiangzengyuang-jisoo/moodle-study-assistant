import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {McpServer} from '@modelcontextprotocol/sdk/server/mcp.js';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {InMemoryTransport} from '@modelcontextprotocol/sdk/inMemory.js';
import {registerChapterCheckTool, chapterChatKey} from '../tools/moodle/chapter-check-tool.mjs';
import {createMcpChapterContext} from '../tools/moodle/mcp-workspace.mjs';
import {connectionError} from '../tools/moodle/connection-errors.mjs';

const BASE = 'https://school.example/moodle';
const PDF = Buffer.from('%PDF-1.4\nsynthetic chapter data');
const CONNECTION_AUTHORIZATION = Buffer.from('SYNTHETIC-ENCRYPTED-AUTHORIZATION');
const hash = value => createHash('sha256').update(value).digest('hex');
const CHAT = 'session-12345678-abcd-4321-abcd-123456789012';
const input = (extra = {}) => ({course_id: 'moodle-1', section: 'Topic 5', chat_id: CHAT, ...extra});
const parse = result => JSON.parse(result.content[0].text);

function file(root, name, bytes) {
  const path = join(root, 'Study', name);
  mkdirSync(dirname(path), {recursive: true});
  writeFileSync(path, bytes);
}

async function fixture(t, {root: existingRoot = null, schoolUserId = 7} = {}) {
  const root = existingRoot ?? mkdtempSync(join(tmpdir(), 'chapter-mcp-'));
  // Receipt reuse is bound to the saved config+credential generation. Write
  // stable synthetic connection files once per root so a restarted server can
  // distinguish "same saved login" from "a new login was saved".
  mkdirSync(join(root, '.private'), {recursive: true});
  if (!existsSync(join(root, 'config.local.json'))) {
    writeFileSync(join(root, 'config.local.json'),
      JSON.stringify({moodle_url: BASE, timezone: 'Asia/Hong_Kong'}, null, 2));
  }
  if (!existsSync(join(root, '.private/moodle-token.dpapi'))) {
    writeFileSync(join(root, '.private/moodle-token.dpapi'), CONNECTION_AUTHORIZATION);
  }
  const sources = [501, 502].map((id, i) => {
    const name = `Topic${5 + i}.pdf`;
    return {id, name, bytes: PDF, relative: `moodle-1/Lectures/${name}`,
      url: `${BASE}/webservice/pluginfile.php/1/mod_resource/content/${id}/${name}`};
  });
  const state = {revision: 1, courses: [{id: 'moodle-1', name: 'Demo course', moodle_course_ids: [101]}],
    settings: {profile_account_scope: {schema: 1, site: BASE, user_id: 7},
      course_library_authorization: {schema: 1, scope_version: 1, courses: {
        'moodle-1': {moodle_courseid: 101, mode: 'full', scope_version: 1, account_scope: {site: BASE, user_id: 7}},
      }}},
    materials: sources.map(source => ({id: `material-${source.id}`, course_id: 'moodle-1',
      filename: source.name, title: source.name, category: 'Lectures', kind: 'course file',
      relative_path: source.relative, sha256: hash(PDF), bytes: PDF.length, version_status: 'current',
      moodle_file_sources: [{course_id: 'moodle-1', module_id: source.id, filepath: '/', remote_filename: source.name, url: source.url}],
      source_pages: [{course_id: 'moodle-1', page_course_id: 'moodle-1', moodle_courseid: 101,
        section: 'Lecture Slides', module_id: source.id, module_name: source.name, module_type: 'resource'}],
    })),
  };
  for (const source of sources) file(root, source.relative, source.bytes);
  const statePath = join(root, 'Study/_System/state.json');
  const save = () => file(root, '_System/state.json', JSON.stringify(state));
  save();
  const calls = {connect: 0, catalog: [], fetch: [], before: 0};
  const control = {connectionError: null, catalogError: null, fileError: null, guardError: null};
  const school = {baseUrl: BASE, userId: schoolUserId, token: 'PRIVATE_TOKEN',
    call: async (method, args) => {
      assert.equal(method, 'core_course_get_contents');
      assert.equal(args.courseid, 101);
      calls.catalog.push(args.courseid);
      if (control.catalogError) throw control.catalogError;
      return [{id: 1, name: 'Lecture Slides', section: 0, modules: sources.map(source => ({
        id: source.id, name: source.name, modname: 'resource', instance: source.id, visible: 1, uservisible: true,
        url: `${BASE}/mod/resource/view.php?id=${source.id}`,
        contents: [{type: 'file', filename: source.name, filepath: '/', filesize: PDF.length, timemodified: 1000, fileurl: source.url}],
      }))}];
    },
    transport: async (rawURL, options) => {
      const url = new URL(rawURL);
      assert.equal(url.origin, new URL(BASE).origin);
      assert.equal(url.searchParams.get('token'), school.token);
      assert.equal(options.method, 'GET');
      assert.equal(options.redirect, 'error');
      calls.fetch.push(url.pathname.split('/').at(-1));
      if (control.fileError) throw control.fileError;
      return new Response(PDF, {headers: {'content-type': 'application/pdf', 'content-length': String(PDF.length)}});
    },
  };
  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  const server = new McpServer({name: 'chapter-test', version: '1.0.0'});
  registerChapterCheckTool(server, {root, loadState: () => JSON.parse(readFileSync(statePath, 'utf8')),
    beforeCheck: () => { calls.before++; if (control.guardError) throw control.guardError; },
    connect: async () => { calls.connect++; if (control.connectionError) throw control.connectionError; return school; },
  });
  const client = new Client({name: 'chapter-test-client', version: '1.0.0'});
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  t.after(async () => { await client.close(); await server.close(); rmSync(root, {recursive: true, force: true}); });
  const call = args => client.callTool({name: 'study_check_chapter', arguments: args});
  return {root, state, statePath, save, sources, calls, control, school, client, call};
}

test('chapter tool advertises fixed inputs and its local receipt side effect', async t => {
  const f = await fixture(t);
  const {tools} = await f.client.listTools();
  assert.equal(tools.length, 1);
  const tool = tools[0];
  assert.equal(tool.name, 'study_check_chapter');
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(tool.inputSchema.required.sort(), ['chat_id', 'course_id']);
  assert.equal(tool.annotations.readOnlyHint, false);
  assert.equal(tool.annotations.destructiveHint, false);
  for (const key of ['root', 'command', 'url', 'path', 'mode', 'chat_key', 'persist'])
    assert(!Object.hasOwn(tool.inputSchema.properties, key));
});

test('strict MCP check fetches only the chapter, preserves state, and reuses the CLI-compatible chat digest', async t => {
  const f = await fixture(t);
  const before = readFileSync(f.statePath);
  const first = parse(await f.call(input()));
  assert.equal(first.mode, 'strict');
  assert.equal(first.verdict, 'unchanged');
  assert.equal(first.verified.complete, true);
  assert.equal(first.network_started, true);
  assert.equal(first.chat.key, hash(JSON.stringify(CHAT)));
  assert.equal(first.chat.key, chapterChatKey(CHAT));
  assert(!JSON.stringify(first).includes(CHAT));
  assert(!JSON.stringify(first).includes('PRIVATE_TOKEN'));
  assert.deepEqual(f.calls.fetch, ['Topic5.pdf']);
  assert.equal(first.files[0].evidence, 'content');
  assert.equal(first.files[0].content_sha256, hash(PDF));
  const second = parse(await f.call(input()));
  assert.equal(second.reused, true);
  assert.equal(second.network_started, false);
  assert.equal(f.calls.connect, 1);
  assert.deepEqual(f.calls.catalog, [101]);
  assert.deepEqual(readFileSync(f.statePath), before);
});

test('a different chat and explicit force each perform a real check', async t => {
  const f = await fixture(t);
  await f.call(input());
  const second = parse(await f.call(input({chat_id: 'another-agreed-chat'})));
  assert.equal(second.network_started, true);
  const third = parse(await f.call(input({chat_id: 'another-agreed-chat', force: true})));
  assert.equal(third.network_started, true);
  assert.equal(f.calls.connect, 3);
});

test('local content mutation prevents receipt reuse without modifying the source file', async t => {
  const f = await fixture(t);
  await f.call(input());
  const changed = Buffer.from('%PDF-1.4\nlocal student revision');
  file(f.root, f.sources[0].relative, changed);
  const result = parse(await f.call(input()));
  assert.equal(result.network_started, true);
  assert.equal(f.calls.connect, 2);
  assert.deepEqual(readFileSync(join(f.root, 'Study', f.sources[0].relative)), changed);
});

test('explicit module scope cannot fetch another chapter or an unlisted module', async t => {
  const f = await fixture(t);
  const result = parse(await f.call({course_id: 'moodle-1', module_ids: [502], chat_id: CHAT}));
  assert.equal(result.verified.complete, true);
  assert.deepEqual(f.calls.fetch, ['Topic6.pdf']);
  const missing = parse(await f.call({course_id: 'moodle-1', module_ids: [9999], chat_id: CHAT}));
  assert.equal(missing.verified.complete, false);
  assert.deepEqual(f.calls.fetch, ['Topic6.pdf']);
});

test('bad input and injected capabilities are rejected before any workspace or network work', async t => {
  const f = await fixture(t);
  const bad = [{}, input({chat_id: ''}), input({section: undefined}), input({module_ids: []}),
    input({module_ids: [-1]}), input({force: 'yes'}), input({chat_id: '12345678-abcd-4321-abcd-123456789012'}),
    input({chat_id: 'line\nbreak'}), input({section: 'x'.repeat(301)}),
    ...['root', 'path', 'command', 'url', 'mode', 'chatKey', 'persist'].map(key => input({[key]: 'forbidden'}))];
  for (const args of bad) assert.equal((await f.call(args)).isError, true, JSON.stringify(args));
  assert.equal(f.calls.before, 0);
  assert.equal(f.calls.connect, 0);
  assert.equal(existsSync(join(f.root, 'Study/_System/ChapterChecks')), false);
});

test('an unknown or deselected course is rejected before connecting', async t => {
  const f = await fixture(t);
  assert.equal(parse(await f.call(input({course_id: 'other'}))).error, 'CHAPTER_COURSE_NOT_SELECTED');
  f.state.courses[0].moodle_course_ids = [];
  f.save();
  assert.equal(parse(await f.call(input())).error, 'CHAPTER_COURSE_NOT_SELECTED');
  assert.equal(f.calls.connect, 0);
});

test('withdrawn full authorization invalidates an existing success without fetching files', async t => {
  const f = await fixture(t);
  await f.call(input());
  f.state.settings.course_library_authorization.courses['moodle-1'].mode = 'info_only';
  f.save();
  const result = await f.call(input());
  assert.equal(result.isError, true);
  assert.equal(parse(result).error, 'CHAPTER_AUTHORIZATION_REQUIRED');
  assert.deepEqual(f.calls.fetch, ['Topic5.pdf']);
});

test('account mismatch stops before course enumeration or receipt creation', async t => {
  const f = await fixture(t);
  f.school.userId = 88;
  const result = await f.call(input());
  assert.equal(result.isError, true);
  assert.equal(parse(result).error, 'PROFILE_ACCOUNT_GUARD');
  assert.equal(f.calls.catalog.length, 0);
  assert.equal(existsSync(join(f.root, 'Study/_System/ChapterChecks')), false);
});

test('workspace identity guard runs before cached reuse and does not expose its original exception', async t => {
  const f = await fixture(t);
  await f.call(input());
  f.control.guardError = Object.assign(new Error('PRIVATE_TOKEN changed private-path'), {code: 'STUDY_MCP_RESTART_REQUIRED'});
  const result = await f.call(input());
  assert.equal(result.isError, true);
  assert.equal(parse(result).error, 'STUDY_MCP_RESTART_REQUIRED');
  assert.equal(f.calls.connect, 1);
  assert(!JSON.stringify(result).includes('PRIVATE_TOKEN'));
  assert(!JSON.stringify(result).includes('private-path'));
});

test('connection failures retain only known diagnostics and never auto-retry', async t => {
  const f = await fixture(t);
  f.control.connectionError = Object.assign(new Error('PRIVATE_TOKEN private-path'), {code: 'STUDY_TOKEN_DECRYPT_FAILED'});
  const result = await f.call(input());
  assert.equal(result.isError, true);
  assert.equal(parse(result).connection_error, 'STUDY_TOKEN_DECRYPT_FAILED');
  assert(!JSON.stringify(result).includes('PRIVATE_TOKEN'));
  assert.equal(f.calls.connect, 1);
  assert.equal(existsSync(join(f.root, 'Study/_System/ChapterChecks')), false);
  f.control.connectionError = new Error('PRIVATE_TOKEN private-path');
  const unknown = await f.call(input());
  assert.equal(parse(unknown).error, 'CHAPTER_CHECK_FAILED');
  assert(!JSON.stringify(unknown).includes('PRIVATE_TOKEN'));
});

test('failed file checks remain incomplete and follow-up reuses failure without retrying', async t => {
  const f = await fixture(t);
  f.control.fileError = new Error('authenticated URL with PRIVATE_TOKEN');
  const first = parse(await f.call(input()));
  assert.equal(first.verified.complete, false);
  assert(!JSON.stringify(first).includes('PRIVATE_TOKEN'));
  const second = parse(await f.call(input()));
  assert.equal(second.reused, true);
  assert.equal(second.network_started, false);
  assert.equal(second.verified.complete, false);
  assert.equal(f.calls.connect, 1);
  f.control.fileError = null;
  const retry = parse(await f.call(input({force: true})));
  assert.equal(retry.verified.complete, true);
  assert.equal(f.calls.connect, 2);
});

test('catalog errors do not disclose remote response bodies', async t => {
  const f = await fixture(t);
  f.control.catalogError = connectionError('STUDY_ACCESS');
  f.control.catalogError.message += ' PRIVATE_TOKEN';
  const result = await f.call(input());
  assert.equal(result.isError, true);
  assert.equal(parse(result).error, 'CHAPTER_CATALOG_UNAVAILABLE');
  assert(!JSON.stringify(result).includes('PRIVATE_TOKEN'));
});

// One restarted source-style service: a fresh MCP server + fresh pinned chapter
// workspace context (the config/credential are read at "startup"), backed by a
// gateway whose account identity only becomes available after connect.
async function restartedService(t, {root, statePath, credential, school}) {
  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  let connected = false;
  let connections = 0;
  const gateway = {
    get baseUrl() { return BASE; },
    get userId() { return connected ? school.userId : null; },
    async connect() { connections += 1; connected = true; return school; },
  };
  const context = createMcpChapterContext({
    root, loadState: () => JSON.parse(readFileSync(statePath, 'utf8')),
    client: gateway, config: {moodle_url: BASE, timezone: 'Asia/Hong_Kong'}, credential,
  });
  const server = new McpServer({name: 'chapter-restart', version: '1.0.0'});
  registerChapterCheckTool(server, context);
  const client = new Client({name: 'chapter-restart-client', version: '1.0.0'});
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  t.after(async () => { await client.close(); await server.close(); });
  return {
    call: args => client.callTool({name: 'study_check_chapter', arguments: args}),
    connections: () => connections,
  };
}

test('a restarted source-style chapter service refuses to reuse the previous account receipt', async t => {
  const first = await fixture(t);
  // First service: the saved login authenticates as account 7 and the strict
  // check succeeds, writing a schema-3 receipt bound to this connection.
  const serviceA = await restartedService(t, {root: first.root, statePath: first.statePath,
    credential: CONNECTION_AUTHORIZATION, school: first.school});
  const successResult = await serviceA.call(input());
  const success = parse(successResult);
  assert.equal(successResult.isError, undefined);
  assert.equal(success.reused, undefined);
  assert.equal(success.verified.complete, true);
  assert.equal(serviceA.connections(), 1);
  assert.equal(first.calls.fetch.length, 1);
  const receiptPath = join(first.root, 'Study/_System/ChapterChecks/moodle-1/topic-5.json');
  assert(existsSync(receiptPath));
  const previousReceipt = JSON.parse(readFileSync(receiptPath, 'utf8'));
  assert.match(previousReceipt.connection_generation, /^[0-9a-f]{64}$/);
  const previousRun = previousReceipt.run_id;

  // The student signs in again: only the protected credential changes. A new
  // service process starts with the new credential and the same chat/chapter.
  const credentialB = Buffer.from('SYNTHETIC-ENCRYPTED-AUTHORIZATION-B');
  writeFileSync(join(first.root, '.private/moodle-token.dpapi'), credentialB);
  const accountBCalls = {catalog: 0, fetch: 0};
  const schoolB = {
    baseUrl: BASE, userId: 88, token: 'PRIVATE_TOKEN',
    async call() { accountBCalls.catalog += 1; throw new Error('must not enumerate another account'); },
    async transport() { accountBCalls.fetch += 1; throw new Error('must not download another account files'); },
  };
  const serviceB = await restartedService(t, {root: first.root, statePath: first.statePath,
    credential: credentialB, school: schoolB});
  const result = await serviceB.call(input());
  assert.equal(result.isError, true);
  assert.equal(parse(result).error, 'PROFILE_ACCOUNT_GUARD');
  assert.notEqual(parse(result).reused, true);
  // The account guard connects once and rejects before enumeration/downloads.
  assert.equal(serviceB.connections(), 1);
  assert.deepEqual(accountBCalls, {catalog: 0, fetch: 0});
  // The old success receipt is neither echoed nor replaced.
  const written = JSON.parse(readFileSync(receiptPath, 'utf8'));
  assert.equal(written.run_id, previousRun);
  assert(!JSON.stringify(result).includes('SYNTHETIC-ENCRYPTED-AUTHORIZATION-B'));
});

test('a restarted server with a newly saved credential does not reuse the previous account receipt', async t => {
  const first = await fixture(t);
  const success = parse(await first.call(input()));
  assert.equal(success.reused, undefined);
  assert.equal(success.verified.complete, true);
  assert.equal(first.calls.connect, 1);
  assert.equal(first.calls.fetch.length, 1);
  assert.match(success.connection_generation, /^[0-9a-f]{64}$/);
  assert(!JSON.stringify(success).includes(CONNECTION_AUTHORIZATION.toString('utf8')));

  // The student signs in again: only the protected credential changes. The
  // local learning state still records account 7, so the cached strict receipt
  // must not be echoed by the newly started service process.
  writeFileSync(join(first.root, '.private/moodle-token.dpapi'),
    Buffer.from('SYNTHETIC-ENCRYPTED-AUTHORIZATION-B'));
  const restarted = await fixture(t, {root: first.root, schoolUserId: 88});
  const result = await restarted.call(input());
  assert.equal(result.isError, true);
  assert.equal(parse(result).error, 'PROFILE_ACCOUNT_GUARD');
  assert.notEqual(parse(result).reused, true);
  assert.equal(restarted.calls.connect, 1);
  // The account guard rejects before course enumeration or chapter downloads.
  assert.deepEqual(restarted.calls.catalog, []);
  assert.deepEqual(restarted.calls.fetch, []);
  assert(!JSON.stringify(result).includes('PRIVATE_TOKEN'));
  assert(!JSON.stringify(result).includes('SYNTHETIC-ENCRYPTED-AUTHORIZATION'));
});
