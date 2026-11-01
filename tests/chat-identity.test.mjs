import { test } from 'node:test';
import assert from 'node:assert/strict';

import { describeSources, resolveChatId } from '../tools/moodle/chat-identity.mjs';

const main = 'session-43e6211a-9a48-4334-8b13-821896b1829c';
const internal = '37daf969-abb2-4b9e-923e-02ede86ed164';

test('explicit chat ID wins over environment', () => {
  const result = resolveChatId('my-thread', { STUDY_CHAT_ID: 'named', DSH_SESSION_ID: main });
  assert.deepEqual(result, { chat: 'my-thread', source: 'argument' });
});

test('agreed STUDY_CHAT_ID is the fallback for clients without a stable ID', () => {
  const result = resolveChatId('', { STUDY_CHAT_ID: 'moodle-autumn', DSH_SESSION_ID: main });
  assert.deepEqual(result, { chat: 'moodle-autumn', source: 'env:STUDY_CHAT_ID' });
});

test('DeepSeek Harness main chat session ID is used automatically', () => {
  const result = resolveChatId('', { DSH_SESSION_ID: main });
  assert.deepEqual(result, { chat: main, source: 'env:DSH_SESSION_ID' });
});

test('DeepSeek Harness internal session ID is refused instead of splitting the chat', () => {
  assert.throws(() => resolveChatId('', { DSH_SESSION_ID: internal }), /内部会话 ID/);
});

test('no identity returns null so manual commands keep their explicit entry', () => {
  assert.equal(resolveChatId('', {}), null);
});

test('describeSources classifies Harness session kinds without exposing values', () => {
  assert.equal(describeSources({ DSH_SESSION_ID: main }).dsh_session_kind, 'main');
  assert.equal(describeSources({ DSH_SESSION_ID: internal }).dsh_session_kind, 'internal');
  assert.equal(describeSources({ DSH_SESSION_ID: 'custom-name' }).dsh_session_kind, 'unknown');
  assert.equal(describeSources({}).dsh_session_kind, 'none');
  assert.equal(describeSources({ STUDY_CHAT_ID: 'named' }).auto_resolvable, true);
});
