import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { serve } from '../tools/moodle/server.mjs';

test('MCP handshake exposes working read tools and no download or write tool', async () => {
  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  const server = await serve({userId: 1, supports: () => true, call: async method => {
    assert.equal(method, 'core_enrol_get_users_courses');
    return [{id: 101, fullname: 'Demo course', shortname: 'DEMO'}];
  }}, serverTransport);
  const client = new Client({name: 'test', version: '1.0.0'});
  try {
    await client.connect(clientTransport);
    const {tools} = await client.listTools();
    assert.equal(tools.length, 10);
    assert(tools.some(tool => tool.name === 'study_discussion_posts'));
    assert(!tools.some(tool => /download|submit|save|send|mark/.test(tool.name)));
    const response = await client.callTool({name: 'moodle_list_courses', arguments: {}});
    assert.match(response.content[0].text, /Demo course/);
  } finally { await client.close(); await server.close(); }
});
