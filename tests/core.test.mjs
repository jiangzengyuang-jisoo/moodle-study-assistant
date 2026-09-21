import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { initialize, normalizeSite, ReadOnlyClient } from '../tools/moodle/core.mjs';
import { collectRefresh, redactEvidence } from '../tools/moodle/refresh.mjs';

test('initialization is blank, portable and preserves existing state', () => {
  const root = mkdtempSync(join(tmpdir(), '学习 project '));
  try {
    initialize(root);
    const path = join(root, 'Study/_System/state.json');
    const state = JSON.parse(readFileSync(path));
    assert.deepEqual(state.courses, []);
    assert.deepEqual(state.mistakes, []);
    state.tasks.push({id: 'KEEP', status: 'Completed'});
    writeFileSync(path, JSON.stringify(state));
    initialize(root);
    assert.equal(JSON.parse(readFileSync(path)).tasks[0].id, 'KEEP');
  } finally { rmSync(root, {recursive: true, force: true}); }
});

test('site URL rejects secrets, unsafe transports and ambiguous URLs', () => {
  assert.equal(normalizeSite('https://school.example/moodle/'), 'https://school.example/moodle');
  for (const url of ['http://school.example', 'https://user:password@school.example', 'https://school.example/?token=secret', 'https://school.example/#x'])
    assert.throws(() => normalizeSite(url));
});

test('client rejects writes and downloads before network, redacts returned credentials', async () => {
  let calls = 0;
  const client = new ReadOnlyClient('https://school.example', 'test-secret', async (_url, options) => {
    calls++;
    assert.equal(options.redirect, 'error');
    assert.equal(options.method, 'POST');
    assert.equal(options.body.get('wstoken'), 'test-secret');
    return {ok: true, json: async () => ({text: 'test-secret', link: 'https://school.example/?wstoken=test-secret'})};
  });
  await assert.rejects(client.call('mod_assign_save_submission'));
  await assert.rejects(client.call('mod_forum_view_forum'));
  await assert.rejects(client.downloadFile('https://school.example/pluginfile.php/a'));
  assert.equal(calls, 0);
  const response = await client.call('core_webservice_get_site_info');
  assert(!JSON.stringify(response).includes('test-secret'));
});

test('empty redaction token does not corrupt evidence', () => {
  assert.equal(redactEvidence('lesson text', ''), 'lesson text');
});

test('collection supports one configured course and flags partial failures', async () => {
  const client = {baseUrl: 'https://school.example', userId: 1, call: async (method) => {
    if (method === 'core_course_get_contents') return [{modules: []}];
    if (method === 'mod_assign_get_assignments') return {courses: [], warnings: []};
    if (method === 'mod_quiz_get_quizzes_by_courses') throw new Error('permission denied');
    if (method === 'core_calendar_get_action_events_by_timesort') return {events: []};
    throw new Error('unexpected API');
  }};
  const result = await collectRefresh(client, {courses: [{id: 'DEMO', moodle_course_ids: [101]}], tasks: []});
  assert.equal(result.pages.length, 1);
  assert.equal(result.collection_complete, false);
  assert.equal(result.reviewed, false);
  assert.equal(result.errors[0].scope, 'quiz_catalog');
  await assert.rejects(collectRefresh(client, {courses: [], tasks: []}), /course/i);
});
