import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {chatCourseOptions, courseCatalog, selectCoursesInChat} from '../tools/moodle/chat-course-selection.mjs';
import {authorizationReport, selectedPages, syncCourseLibrary} from '../tools/moodle/course-library.mjs';
import {initialize} from '../tools/moodle/core.mjs';
import {saveState} from '../tools/moodle/state-bridge.mjs';

const account = {site: 'https://school.example', user_id: 17};
function fixture() {
  let state = {revision: 2, settings: {keep: true}, courses: [
    {id: 'old', moodle_course_ids: [8, 9], teaching_cursor: {user_answer: 'original'}}], mistakes: []};
  let writes = 0;
  const client = {baseUrl: account.site, userId: account.user_id,
    call: async method => {assert.equal(method, 'core_enrol_get_users_courses'); return [
      {id: 8, fullname: 'Synthetic common', shortname: 'SYN'},
      {id: 9, fullname: 'Synthetic section', shortname: 'SYN1'},
      {id: 10, fullname: 'New course', shortname: 'NEW'}];}};
  const deps = {client, readState: () => structuredClone(state), writeState: (expected, next) => {
    assert.deepEqual(expected, state); state = next; writes++;
  }};
  return {deps, state: () => state, writes: () => writes};
}

test('listing does not change records; full selection atomically saves deployed library grants', async () => {
  const f = fixture();
  const catalog = await courseCatalog(f.deps);
  assert.equal(f.writes(), 0);
  assert.deepEqual(catalog.modes, ['full', 'info_only']);
  const result = await selectCoursesInChat({...f.deps, selection: {...catalog, course_ids: [10], mode: 'full'}});
  assert.equal(result.status, 'saved');
  assert.equal(result.reviewed, false);
  assert.equal(f.writes(), 1);
  assert.equal(f.state().settings.course_content_sync, undefined);
  assert.equal(f.state().settings.course_library_authorization.courses['moodle-10'].mode, 'full');
  assert.equal(authorizationReport(f.state(), selectedPages(f.state()), account).authorized.length, 1);
  assert.deepEqual(f.state().courses[0].teaching_cursor, {user_answer: 'original'});
  assert.deepEqual(f.state().courses[0].moodle_course_ids, []);
});

test('merged common and class pages retain authorization for every selected page', async () => {
  const f = fixture();
  await selectCoursesInChat({...f.deps, selection: {...await courseCatalog(f.deps), course_ids: [8, 9], mode: 'full'}});
  assert.deepEqual(f.state().settings.course_library_authorization.courses.old.moodle_courseids, [8, 9]);
  assert.equal(authorizationReport(f.state(), selectedPages(f.state()), account).authorized.length, 2);
});

test('switching to info_only stops existing full grants without deleting learning history', async () => {
  const f = fixture();
  for (const mode of ['full', 'info_only']) await selectCoursesInChat({...f.deps,
    selection: {...await courseCatalog(f.deps), course_ids: [8, 9], mode}});
  assert.equal(authorizationReport(f.state(), selectedPages(f.state()), account).authorized.length, 0);
  assert.equal(f.state().settings.course_library_authorization.courses.old.mode, 'info_only');
  assert.deepEqual(f.state().courses[0].teaching_cursor, {user_answer: 'original'});
});

test('wrong account, stale choice, unknown pages, missing mode and cancel cannot write', async () => {
  for (const kind of ['account', 'site', 'state', 'hidden', 'mode', 'empty', 'string']) {
    const f = fixture();
    const selection = {...await courseCatalog(f.deps), course_ids: [8], mode: 'full'};
    if (kind === 'account') f.deps.client.userId = 18;
    if (kind === 'site') selection.account_scope.site = 'https://other.example';
    if (kind === 'state') f.state().revision++;
    if (kind === 'hidden') selection.course_ids = [99];
    if (kind === 'mode') delete selection.mode;
    if (kind === 'empty') selection.course_ids = [];
    if (kind === 'string') selection.course_ids = ['8'];
    await assert.rejects(selectCoursesInChat({...f.deps, selection}), undefined, kind);
    assert.equal(f.writes(), 0, kind);
  }
});

test('new grants cannot be consumed by another school or account', async () => {
  const f = fixture();
  await selectCoursesInChat({...f.deps, selection: {...await courseCatalog(f.deps), course_ids: [8], mode: 'full'}});
  for (const bad of [null, {...account, user_id: 18}, {...account, site: 'https://other.example'}]) {
    const report = authorizationReport(f.state(), selectedPages(f.state()), bad);
    assert.equal(report.authorized.length, 0);
    assert.equal(report.blocked[0].reason, 'authorization_account_changed');
  }
  await assert.rejects(syncCourseLibrary({root: tmpdir(), state: f.state(),
    client: {...f.deps.client, userId: 18, call: () => {throw new Error('must not enumerate');}}}), /authorization_account_changed/);
});

test('headless incomplete commands fail instead of waiting for terminal stdin', () => {
  for (const args of [[], ['--select-file'], ['--list', '--select-file', 'x'], ['--select', '8'],
    ['--select', '--mode', 'full', '--yes']]) assert.throws(() => chatCourseOptions(args, false));
  assert.equal(chatCourseOptions(['--list'], false).action, 'list');
  assert.equal(chatCourseOptions(['--select-file', '中文 路径.json'], false).file, '中文 路径.json');
  assert.equal(chatCourseOptions(['--select', '8', '--mode', 'full', '--yes'], false), null);
});

test('real shared writer saves selection and authorization together and preserves original backup', async () => {
  const root = mkdtempSync(join(tmpdir(), 'DeepSeek chat 中文 '));
  try {
    const original = initialize(root);
    const path = join(root, 'Study/_System/state.json');
    const {deps} = fixture();
    deps.readState = () => JSON.parse(readFileSync(path));
    deps.writeState = (expected, state) => saveState(root, 'replace', {expected, state});
    const selection = {...await courseCatalog(deps), course_ids: [8, 9], mode: 'full'};
    await selectCoursesInChat({...deps, selection});
    const saved = deps.readState();
    assert.equal(saved.revision, original.revision + 1);
    assert.equal(authorizationReport(saved, selectedPages(saved), account).authorized.length, 2);
    assert.deepEqual(JSON.parse(readFileSync(join(root, 'Study/_System/state.previous.json'))), original);
    await assert.rejects(selectCoursesInChat({...deps, selection}), /状态已变化/);
    assert.deepEqual(deps.readState(), saved);
  } finally { rmSync(root, {recursive: true, force: true}); }
});
