import test from 'node:test';
import assert from 'node:assert/strict';
import {chooseCourses, courseCatalog, selectCoursesInChat, parseCourseOptions} from '../tools/moodle/course-selection.mjs';
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

function chatFixture() {
  let state = {revision: 2, settings: {keep: true}, courses: [
    {id: 'old', moodle_course_ids: [8], teaching_cursor: {user_answer: 'keep'}}]};
  const client = {baseUrl: 'https://school.example/', userId: 17,
    call: async () => [{id: 8, fullname: 'Old', shortname: 'O'}, {id: 9, fullname: 'Math', shortname: 'M'}]};
  let writes = 0;
  const deps = {client, readState: () => structuredClone(state), writeState: (expected, next) => {
    assert.deepEqual(expected, state); writes++; state = next;
  }};
  return {deps, state: () => state, writes: () => writes};
}

test('chat catalog is read only and selection needs no stdin or extra initialization chat', async () => {
  const f = chatFixture();
  const catalog = await courseCatalog(f.deps);
  assert.equal(f.writes(), 0);
  assert.equal(catalog.courses[0].selected, true);
  const result = await selectCoursesInChat({...f.deps, selection: {...catalog, course_ids: [9], mode: 'full'}});
  assert.equal(result.status, 'saved');
  assert.equal(result.reviewed, false);
  assert.equal(f.writes(), 1);
  assert.deepEqual([...authorizedCoursePages(f.state(), {site: 'https://school.example', userId: 17})], [[9, 'moodle-9']]);
  assert.deepEqual(f.state().courses[0].teaching_cursor, {user_answer: 'keep'});
});

test('chat metadata selection does not authorize downloads', async () => {
  const f = chatFixture();
  const selection = {...await courseCatalog(f.deps), course_ids: [9], mode: 'metadata'};
  await selectCoursesInChat({...f.deps, selection});
  assert.equal(authorizedCoursePages(f.state(), {site: 'https://school.example', userId: 17}).size, 0);
});

test('missing consent, changed account, stale state and hidden courses never write', async () => {
  for (const change of ['mode', 'empty', 'string', 'site', 'account', 'state', 'hidden', 'version']) {
    const f = chatFixture();
    const selection = {...await courseCatalog(f.deps), course_ids: [9], mode: 'full'};
    if (change === 'mode') delete selection.mode;
    if (change === 'empty') selection.course_ids = [];
    if (change === 'string') selection.course_ids = ['9'];
    if (change === 'site') selection.account_scope.site = 'https://other.example';
    if (change === 'account') f.deps.client.userId = 18;
    if (change === 'state') f.state().settings.keep = false;
    if (change === 'hidden') selection.course_ids = [99];
    if (change === 'version') selection.version = 2;
    await assert.rejects(selectCoursesInChat({...f.deps, selection}), undefined, change);
    assert.equal(f.writes(), 0, change);
  }
});

test('state conflict propagates without initialization or a success result', async () => {
  const f = chatFixture();
  const selection = {...await courseCatalog(f.deps), course_ids: [9], mode: 'full'};
  await assert.rejects(selectCoursesInChat({...f.deps, selection,
    writeState: () => { throw new Error('state changed'); }}), /state changed/);
});

test('invalid or headless course CLI cannot fall back to a terminal question', () => {
  for (const args of [[], ['--select-file'], ['--select-file', '--list'], ['--list', '--select-file', 'x'],
    ['--json'], ['--chat', 'x', '--typo']]) assert.throws(() => parseCourseOptions(args, false));
  assert.deepEqual(parseCourseOptions(['--list'], false), {action: 'list'});
  assert.deepEqual(parseCourseOptions(['--select-file', '中文 路径.json'], false), {action: 'select', file: '中文 路径.json'});
  assert.equal(parseCourseOptions([], true).action, 'interactive');
});
