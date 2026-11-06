import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {chatCourseOptions, courseCatalog, selectCoursesInChat} from '../tools/moodle/chat-course-selection.mjs';
import {assertProfileAccount} from '../tools/moodle/content-policy.mjs';
import {authorizationReport, selectedPages, syncCourseLibrary} from '../tools/moodle/course-library.mjs';
import {initialize} from '../tools/moodle/core.mjs';
import {saveState} from '../tools/moodle/state-bridge.mjs';

const account = {site: 'https://school.example', user_id: 17};
const otherAccount = {site: 'https://other.example', user_id: 18};

function accountBinding(scope = account) {
  return {schema: 1, site: scope.site, user_id: scope.user_id, bound_at: '2026-11-01T00:00:00Z'};
}

function fixture({settings = {keep: true, profile_account_scope: accountBinding()}, courses} = {}) {
  let state = {revision: 2, settings, courses: courses ?? [
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

function snapshotRoot(site) {
  const root = mkdtempSync(join(tmpdir(), 'DeepSeek cross school '));
  mkdirSync(join(root, 'Study', '_System'), {recursive: true});
  writeFileSync(join(root, 'Study', '_System', 'refresh-latest.json'), JSON.stringify({
    started_at: '2026-11-01T00:00:00Z', collection_complete: true,
    moodle_url: site, account_scope: {site, user_id: 17},
  }), 'utf8');
  return root;
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
  assert.deepEqual(f.state().settings.profile_account_scope, accountBinding());
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

test('another school cannot attach its same-numbered course IDs to old course records', async () => {
  const f = fixture();
  const catalog = await courseCatalog(f.deps);
  const before = structuredClone(f.state());
  f.deps.client.baseUrl = otherAccount.site;
  f.deps.client.userId = otherAccount.user_id;
  // The catalog itself fails closed, so stale "selected" flags are never shown.
  await assert.rejects(courseCatalog(f.deps), /另一所学校/);
  await assert.rejects(selectCoursesInChat({...f.deps,
    selection: {...catalog, account_scope: {...otherAccount}, course_ids: [8], mode: 'full'}}), /另一所学校/);
  assert.equal(f.writes(), 0);
  assert.deepEqual(f.state(), before);
  assert.deepEqual(f.state().courses[0].moodle_course_ids, [8, 9]);
  assert.deepEqual(f.state().courses[0].teaching_cursor, {user_answer: 'original'});
});

test('legacy authorization without account info is not re-signed on a blocked switch', async () => {
  const root = snapshotRoot(account.site);
  try {
    const legacy = {schema: 1, scope_version: 1, scope: 'legacy scope', courses: {
      old: {moodle_courseid: 8, mode: 'full', scope_version: 1}}};
    const f = fixture({settings: {keep: true, course_library_authorization: legacy}});
    const before = structuredClone(f.state());
    f.deps.client.baseUrl = otherAccount.site;
    f.deps.client.userId = otherAccount.user_id;
    await assert.rejects(selectCoursesInChat({...f.deps, root,
      selection: {version: 1, account_scope: {...otherAccount}, state_fingerprint: 'stale',
        course_ids: [8], mode: 'full'}}), /另一所学校/);
    assert.equal(f.writes(), 0);
    assert.deepEqual(f.state(), before);
    // No account information is invented or back-filled onto the old grant.
    assert.equal(f.state().settings.course_library_authorization.courses.old.account_scope, undefined);
    assert.equal(f.state().settings.profile_account_scope, undefined);
  } finally { rmSync(root, {recursive: true, force: true}); }
});

test('older library reports also identify the recorded school', () => {
  const root = mkdtempSync(join(tmpdir(), 'DeepSeek library report '));
  try {
    mkdirSync(join(root, 'Study', '_System', 'LibrarySync'), {recursive: true});
    writeFileSync(join(root, 'Study', '_System', 'LibrarySync', 'latest.json'), JSON.stringify({
      courses: [{course_id: 'moodle-8', page_url: `${account.site}/course/view.php?id=8`}],
    }), 'utf8');
    const state = {settings: {}, courses: [{id: 'moodle-8', moodle_course_ids: [8]}]};
    assert.equal(assertProfileAccount({state, root, site: account.site, userId: account.user_id,
      action: '同步课程资料'}).status, 'bound');
    assert.throws(() => assertProfileAccount({state, root, site: otherAccount.site,
      userId: otherAccount.user_id, action: '同步课程资料'}), /另一所学校/);
  } finally { rmSync(root, {recursive: true, force: true}); }
});

test('an existing profile with no verifiable account fails closed instead of guessing', async () => {
  const f = fixture({settings: {keep: true}});
  await assert.rejects(courseCatalog(f.deps), /无法确认|补签/);
  await assert.rejects(selectCoursesInChat({...f.deps,
    selection: {version: 1, account_scope: {...account}, state_fingerprint: 'stale',
      course_ids: [8], mode: 'full'}}), /无法确认|补签/);
  assert.equal(f.writes(), 0);
});

test('profile account guard rejects cross-school local sync before any download', () => {
  const root = snapshotRoot(account.site);
  try {
    const state = {settings: {}, courses: [{id: 'moodle-8', moodle_course_ids: [8]}],
      materials: [{id: 'm', moodle_file_sources: [{module_id: 501}]}], mistakes: []};
    assert.equal(assertProfileAccount({state, root, site: account.site, userId: account.user_id,
      action: '同步课程资料'}).status, 'bound');
    assert.throws(() => assertProfileAccount({state, root, site: otherAccount.site,
      userId: otherAccount.user_id, action: '同步课程资料'}), /另一所学校/);
    assert.throws(() => assertProfileAccount({state, root, site: account.site,
      userId: 18, action: '同步课程资料'}), /另一个账号/);
    assert.equal(assertProfileAccount({state: {courses: []}, root: null, site: account.site,
      userId: account.user_id, action: '保存选课'}).status, 'fresh');
    // Pre-fix chat grants already carry account_scope: they still bind the
    // profile to that account without needing a new explicit field.
    const grantsOnly = {settings: {course_library_authorization: {schema: 1, courses: {
      old: {moodle_courseid: 8, mode: 'full', account_scope: {...account}}}}},
    courses: [{id: 'old', moodle_course_ids: [8]}]};
    assert.equal(assertProfileAccount({state: grantsOnly, root: null, site: account.site,
      userId: account.user_id, action: '同步课程资料'}).status, 'bound');
    assert.throws(() => assertProfileAccount({state: grantsOnly, root: null,
      site: otherAccount.site, userId: otherAccount.user_id, action: '同步课程资料'}), /另一所学校/);
    // Contradictory evidence is never guessed into one account.
    const conflict = structuredClone(grantsOnly);
    conflict.settings.profile_account_scope = {schema: 1, ...otherAccount};
    assert.throws(() => assertProfileAccount({state: conflict, root: null, site: account.site,
      userId: account.user_id, action: '同步课程资料'}), /不同学校或账号/);
  } finally { rmSync(root, {recursive: true, force: true}); }
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

test('a fresh profile lists, selects all pages and can re-select in the same school', async () => {
  const root = mkdtempSync(join(tmpdir(), 'DeepSeek fresh profile '));
  try {
    const original = initialize(root);
    const path = join(root, 'Study/_System/state.json');
    const list = [{id: 8, fullname: 'Common', shortname: 'C'},
      {id: 9, fullname: 'Section', shortname: 'S'}, {id: 10, fullname: 'New', shortname: 'N'}];
    const deps = {client: {baseUrl: account.site, userId: account.user_id,
      call: async () => list},
    root,
    readState: () => JSON.parse(readFileSync(path, 'utf8')),
    writeState: (expected, state) => saveState(root, 'replace', {expected, state})};
    const first = await courseCatalog(deps);
    assert.deepEqual(first.courses.map(course => course.selected), [false, false, false]);
    const saved = await selectCoursesInChat({...deps,
      selection: {...first, course_ids: [8, 9, 10], mode: 'full'}});
    assert.equal(saved.status, 'saved');
    const state = deps.readState();
    assert.equal(state.revision, original.revision + 1);
    assert.equal(state.settings.profile_account_scope.site, account.site);
    assert.equal(state.settings.profile_account_scope.user_id, account.user_id);
    assert.deepEqual(state.courses.map(course => course.moodle_course_ids), [[8], [9], [10]]);
    assert.equal(authorizationReport(state, selectedPages(state), account).authorized.length, 3);
    // Same school: a later catalog sees the selection, and changing the mode is allowed.
    const second = await courseCatalog(deps);
    assert.deepEqual(second.courses.map(course => course.selected), [true, true, true]);
    const again = await selectCoursesInChat({...deps,
      selection: {...second, course_ids: [8, 9, 10], mode: 'info_only'}});
    assert.equal(again.mode, 'info_only');
    const revised = deps.readState();
    assert.equal(revised.revision, state.revision + 1);
    assert.equal(authorizationReport(revised, selectedPages(revised), account).authorized.length, 0);
    assert.equal(revised.settings.profile_account_scope.site, account.site);
    assert.equal(revised.settings.profile_account_scope.user_id, account.user_id);
  } finally { rmSync(root, {recursive: true, force: true}); }
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
    await selectCoursesInChat({...deps, root, selection});
    const saved = deps.readState();
    assert.equal(saved.revision, original.revision + 1);
    assert.equal(saved.settings.profile_account_scope.site, account.site);
    assert.equal(saved.settings.profile_account_scope.user_id, account.user_id);
    assert.equal(saved.settings.profile_account_scope.schema, 1);
    assert.equal(authorizationReport(saved, selectedPages(saved), account).authorized.length, 2);
    assert.deepEqual(JSON.parse(readFileSync(join(root, 'Study/_System/state.previous.json'))), original);
    await assert.rejects(selectCoursesInChat({...deps, root, selection}), /状态已变化/);
    assert.deepEqual(deps.readState(), saved);
  } finally { rmSync(root, {recursive: true, force: true}); }
});

test('a blocked cross-school selection leaves the real state file byte-identical', async () => {
  const root = mkdtempSync(join(tmpdir(), 'DeepSeek blocked switch '));
  try {
    const initial = initialize(root);
    const path = join(root, 'Study/_System/state.json');
    const bound = structuredClone(initial);
    bound.settings.profile_account_scope = accountBinding();
    bound.courses = [{id: 'moodle-8', name: 'Old school course', shortname: 'OLD',
      moodle_course_ids: [8], teaching_cursor: {answer: 'keep'}}];
    bound.materials = [{id: 'm-8', course_id: 'moodle-8', moodle_file_sources: [{module_id: 501}]}];
    saveState(root, 'replace', {expected: initial, state: bound});
    const before = readFileSync(path);
    const previousBefore = readFileSync(join(root, 'Study/_System/state.previous.json'));
    const client = {baseUrl: otherAccount.site, userId: otherAccount.user_id,
      call: async () => {throw new Error('must not list another school');}};
    await assert.rejects(selectCoursesInChat({
      client, root,
      readState: () => JSON.parse(readFileSync(path)),
      writeState: (expected, state) => saveState(root, 'replace', {expected, state}),
      selection: {version: 1, account_scope: {...otherAccount}, state_fingerprint: 'stale',
        course_ids: [8], mode: 'full'},
    }), /另一所学校/);
    assert.deepEqual(readFileSync(path), before);
    assert.deepEqual(readFileSync(join(root, 'Study/_System/state.previous.json')), previousBefore);
    assert.deepEqual(JSON.parse(readFileSync(path)).courses[0].moodle_course_ids, [8]);
  } finally { rmSync(root, {recursive: true, force: true}); }
});
