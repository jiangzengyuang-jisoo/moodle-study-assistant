import test from 'node:test';
import assert from 'node:assert/strict';
import {ReadOnlyClient} from '../tools/moodle/core.mjs';
import {classifyTransportError, connectionError, connectionDiagnostic} from '../tools/moodle/connection-errors.mjs';
import {collectRefresh} from '../tools/moodle/refresh.mjs';

const secret = 'synthetic-secret';
const method = 'core_webservice_get_site_info';
const response = data => ({ok: true, json: async () => data});
const thrown = code => async () => {
  throw new Error(`https://private.example/?wstoken=${secret}`, {cause: Object.assign(new Error(secret), {code})});
};

for (const [name, transport, expected] of [
  ['nested DNS', thrown('ENOTFOUND'), 'STUDY_DNS'],
  ['timeout', thrown('UND_ERR_CONNECT_TIMEOUT'), 'STUDY_TIMEOUT'],
  ['TLS', thrown('CERT_HAS_EXPIRED'), 'STUDY_TLS'],
  ['unknown transport', thrown('ECONNRESET'), 'STUDY_NETWORK'],
  ['HTTP forbidden', async () => ({ok: false, status: 403}), 'STUDY_ACCESS'],
  ['HTTP unavailable', async () => ({ok: false, status: 503}), 'STUDY_HTTP'],
  ['invalid JSON', async () => ({ok: true, json: async () => {throw new SyntaxError(secret);}}), 'STUDY_RESPONSE'],
  ['body timeout', async () => ({ok: true, json: thrown('UND_ERR_BODY_TIMEOUT')}), 'STUDY_TIMEOUT'],
  ['empty response', async () => response(null), 'STUDY_RESPONSE'],
  ['expired authorization', async () => response({exception: secret, errorcode: 'invalidtoken', message: secret}), 'STUDY_AUTH'],
  ['API permission', async () => response({exception: secret, errorcode: 'nopermissions'}), 'STUDY_ACCESS'],
]) {
  test(`read-only client classifies ${name} without disclosing transport details`, async () => {
    const client = new ReadOnlyClient('https://school.example', secret, transport);
    await assert.rejects(client.call(method), error => {
      assert.equal(error.code, expected);
      const output = JSON.stringify(connectionDiagnostic(error)) + error.stack;
      assert(!output.includes(secret));
      assert(!output.includes('private.example'));
      assert.equal(error.cause, undefined);
      return true;
    });
  });
}

test('aggregate and cyclic causes are bounded; diagnostics ignore arbitrary properties', () => {
  const error = new AggregateError([Object.assign(new Error(secret), {code: 'EAI_AGAIN'})], secret);
  error.cause = error;
  assert.equal(classifyTransportError(error).code, 'STUDY_DNS');
  const timeout = Object.assign(new Error(secret), {name: 'TimeoutError'});
  assert.equal(classifyTransportError(timeout).code, 'STUDY_TIMEOUT');
  assert.equal(connectionDiagnostic({code: 'unrecognized', message: secret}), null);
  assert(!JSON.stringify(connectionDiagnostic({code: 'STUDY_DNS', message: secret, next_step: secret})).includes(secret));
});

test('partial collection retains safe recovery guidance and remains incomplete', async () => {
  const client = {baseUrl: 'https://school.example', userId: 1, call: async method => {
    if (method === 'core_course_get_contents') return [];
    if (method === 'mod_assign_get_assignments') return {courses: []};
    if (method === 'core_calendar_get_action_events_by_timesort') return {events: []};
    throw connectionError('STUDY_TIMEOUT');
  }};
  const snapshot = await collectRefresh(client, {courses: [{id: 'DEMO', moodle_course_ids: [101]}], tasks: []});
  assert.equal(snapshot.collection_complete, false);
  assert.equal(snapshot.reviewed, false);
  assert(snapshot.errors.some(error => error.connection_error === 'STUDY_TIMEOUT' && error.next_step));
});
