import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {chooseCourses} from '../tools/moodle/course-selection.mjs';
import {authorizedCoursePages} from '../tools/moodle/content-policy.mjs';

const account = {site: 'https://school.example', userId: 17};
const otherAccount = {site: 'https://other.example', userId: 18};
const binding = {schema: 1, site: account.site, user_id: account.userId, bound_at: '2026-11-01T00:00:00Z'};

function fixture(settings = {profile_account_scope: binding}, courses = [
  {id: 'old', moodle_course_ids: [8], teaching_cursor: {answer: 'keep'}}]) {
  return {settings, courses, materials: [], mistakes: [{id: 'm1', course_id: 'old', answer: 'keep'}]};
}

function snapshotRoot(site) {
  const root = mkdtempSync(join(tmpdir(), 'DeepSeek choose courses '));
  mkdirSync(join(root, 'Study', '_System'), {recursive: true});
  writeFileSync(join(root, 'Study', '_System', 'refresh-latest.json'), JSON.stringify({
    collection_complete: true, moodle_url: site, account_scope: {site, user_id: 17},
  }), 'utf8');
  return root;
}

async function run(answers, {client = {baseUrl: account.site, userId: account.userId,
  call: async () => [{id: 9, fullname: 'Math', shortname: 'M'}]}, root = null, original = fixture()} = {}) {
  const events = [];
  let stored = structuredClone(original);
  const result = await chooseCourses({client, root, readState: () => structuredClone(stored),
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
  assert.deepEqual([...authorizedCoursePages(stored, {site: account.site, userId: account.userId})], [[9, 'moodle-9']]);
  assert.deepEqual(stored.courses[0].teaching_cursor, original.courses[0].teaching_cursor);
  assert.deepEqual(stored.courses[0].moodle_course_ids, []);
  assert.deepEqual(stored.settings.profile_account_scope, binding);
});

test('metadata selection still synchronizes information without download authorization', async () => {
  const {stored, events} = await run(['9', '2']);
  assert.equal(authorizedCoursePages(stored, {site: account.site, userId: account.userId}).size, 0);
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

test('a different school cannot reuse numeric course IDs and leaves records untouched', async () => {
  const original = fixture();
  await assert.rejects(run(['9', '1'], {client: {baseUrl: otherAccount.site,
    userId: otherAccount.userId, call: async () => [{id: 9, fullname: 'Other Math', shortname: 'OM'}]},
  original}), /另一所学校/);
  // Also checked at the guard level: even without a stored binding, a previous
  // school snapshot blocks the switch and no account is guessed.
  const root = snapshotRoot(account.site);
  try {
    await assert.rejects(run(['9', '1'], {root, client: {baseUrl: otherAccount.site,
      userId: otherAccount.userId, call: async () => [{id: 9, fullname: 'Other Math', shortname: 'OM'}]},
    original: fixture({})}), /另一所学校/);
  } finally { rmSync(root, {recursive: true, force: true}); }
});

test('an unverifiable profile fails closed instead of binding the account', async () => {
  await assert.rejects(run(['9', '1'], {original: fixture({})}), /无法确认|补签/);
});

test('same-school selection keeps the recorded account binding', async () => {
  const {stored} = await run(['9', '1']);
  assert.equal(stored.settings.course_content_sync.grants[0].site, account.site);
  assert.equal(stored.settings.course_content_sync.grants[0].user_id, account.userId);
  assert.deepEqual(stored.mistakes, [{id: 'm1', course_id: 'old', answer: 'keep'}]);
});
