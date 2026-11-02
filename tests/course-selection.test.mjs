import test from 'node:test';
import assert from 'node:assert/strict';
import {chooseCourses} from '../tools/moodle/course-selection.mjs';
import {authorizedCoursePages} from '../tools/moodle/content-policy.mjs';

async function run(answers) {
  const events = [];
  const original = {settings: {}, courses: [{id: 'old', moodle_course_ids: [8], teaching_cursor: {answer: 'keep'}}]};
  let stored = structuredClone(original);
  const client = {baseUrl: 'https://school.example', userId: 17,
    call: async () => [{id: 9, fullname: 'Math', shortname: 'M'}]};
  const result = await chooseCourses({client, readState: () => structuredClone(stored),
    writeState: (expected, state) => {assert.deepEqual(expected, stored); events.push('saved'); stored = state;},
    ask: async () => answers.shift(), output: () => {}, initialize: async () => {
      events.push('initialized');
      assert.ok(stored.settings.course_content_sync);
    }});
  return {result, stored, original, events};
}

test('full selection saves scoped authorization before automatically initializing', async () => {
  const {result, stored, original, events} = await run(['9', '1']);
  assert.equal(result.mode, 'full');
  assert.deepEqual(events, ['saved', 'initialized']);
  assert.deepEqual([...authorizedCoursePages(stored, {site: 'https://school.example', userId: 17})], [[9, 'moodle-9']]);
  assert.deepEqual(stored.courses[0].teaching_cursor, original.courses[0].teaching_cursor);
  assert.deepEqual(stored.courses[0].moodle_course_ids, []);
});

test('metadata selection still synchronizes information without download authorization', async () => {
  const {stored, events} = await run(['9', '2']);
  assert.equal(authorizedCoursePages(stored, {site: 'https://school.example', userId: 17}).size, 0);
  assert.deepEqual(events, ['saved', 'initialized']);
});

test('cancelled selection or authorization leaves both state and network initialization untouched', async () => {
  for (const answers of [[''], ['9', '']]) {
    const {result, stored, original, events} = await run(answers);
    assert.equal(result.status, 'cancelled');
    assert.deepEqual(stored, original);
    assert.deepEqual(events, []);
  }
});

test('invalid selection cannot grant authority', async () => {
  await assert.rejects(run(['10', '1']), /当前账户/);
  await assert.rejects(run(['9', 'yes']), /同步方式/);
});
