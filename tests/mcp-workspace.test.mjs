import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createMcpChapterContext} from '../tools/moodle/mcp-workspace.mjs';
import {serve} from '../tools/moodle/server.mjs';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {InMemoryTransport} from '@modelcontextprotocol/sdk/inMemory.js';

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'mcp-workspace-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  mkdirSync(join(root, '.private'));
  const config = {moodle_url: 'https://school.example', timezone: 'Asia/Hong_Kong'};
  const credential = Buffer.from('synthetic-encrypted-authorization');
  writeFileSync(join(root, 'config.local.json'), JSON.stringify(config));
  writeFileSync(join(root, '.private/moodle-token.dpapi'), credential);
  let calls = 0;
  const client = {baseUrl: config.moodle_url, userId: 7, async connect() { calls++; return this; }};
  const state = {settings: {profile_account_scope: {schema: 1, site: config.moodle_url, user_id: 7}}};
  const context = createMcpChapterContext({root, loadState: () => state, client, config, credential});
  return {root, config, client, state, context, calls: () => calls};
}

test('MCP workspace checks generations locally; only connect revalidates identity', async t => {
  const f = fixture(t);
  f.context.beforeCheck(); f.context.beforeCheck();
  assert.equal(f.calls(), 0);
  assert.equal(await f.context.connect(), f.client);
  assert.equal(f.calls(), 1);
});

for (const changed of ['config', 'credential', 'missing', 'malformed']) {
  test(`MCP workspace rejects ${changed} before using a stale service`, async t => {
    const f = fixture(t);
    if (changed === 'config') writeFileSync(join(f.root, 'config.local.json'), JSON.stringify({...f.config, moodle_url: 'https://other.example'}));
    if (changed === 'credential') writeFileSync(join(f.root, '.private/moodle-token.dpapi'), 'SECRET-NEW-CREDENTIAL');
    if (changed === 'missing') rmSync(join(f.root, '.private/moodle-token.dpapi'));
    if (changed === 'malformed') writeFileSync(join(f.root, 'config.local.json'), 'SECRET-BROKEN-CONFIG');
    const safe = error => error.code === 'STUDY_MCP_RESTART_REQUIRED' && !error.message.includes('SECRET') && !error.message.includes(f.root);
    assert.throws(f.context.beforeCheck, safe);
    await assert.rejects(f.context.connect(), safe);
    assert.equal(f.calls(), 0);
  });
}

test('MCP workspace rejects authorization change while identity request is in flight', async t => {
  const f = fixture(t);
  f.client.connect = async () => { writeFileSync(join(f.root, '.private/moodle-token.dpapi'), 'new'); return f.client; };
  await assert.rejects(f.context.connect(), {code: 'STUDY_MCP_RESTART_REQUIRED'});
});

test('MCP workspace rejects a changed profile even when configuration and authorization are unchanged', async t => {
  const f = fixture(t);
  f.state.settings.profile_account_scope.user_id = 8;
  assert.throws(f.context.beforeCheck, {code: 'PROFILE_ACCOUNT_GUARD'});
  await assert.rejects(f.context.connect(), {code: 'PROFILE_ACCOUNT_GUARD'});
  assert.equal(f.calls(), 0);
});

test('production-style MCP context exposes the chapter tool alongside existing read tools', async t => {
  const f = fixture(t);
  const [st, ct] = InMemoryTransport.createLinkedPair();
  const server = await serve(f.client, st, f.context);
  const client = new Client({name: 'mcp-integration-test', version: '1'});
  try {
    await client.connect(ct);
    const {tools} = await client.listTools();
    assert.equal(tools.length, 16);
    assert(tools.some(tool => tool.name === 'study_check_chapter'));
    assert(tools.some(tool => tool.name === 'moodle_list_courses'));
    assert.equal(f.calls(), 0);
  } finally { await client.close(); await server.close(); }
});
