import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync, mkdtempSync, readFileSync, writeFileSync, rmSync, realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, dirname, relative, isAbsolute} from 'node:path';
import {runChapterCheck} from '../tools/moodle/chapter-check.mjs';
import {syncCourseLibrary} from '../tools/moodle/course-library.mjs';
const read = path => JSON.parse(readFileSync(path, 'utf8'));
function fixture(t, {mergedPage = false, extraPage = false} = {}) {
  const base = realpathSync(tmpdir()), root = mkdtempSync(join(base, 'chapter-boundary-'));
  t.after(() => {const child = relative(base, realpathSync(root));
    assert.ok(child && !child.startsWith('..') && !isAbsolute(child)); rmSync(root, {recursive: true});});
  const save = (name, value) => {const path = join(root, name); mkdirSync(dirname(path), {recursive: true}); writeFileSync(path, JSON.stringify(value));};
  const site = 'https://school.example/moodle', statePath = join(root, 'Study/_System/state.json');
  const courses = [{id: 'moodle-1', name: 'Demo', moodle_course_ids: extraPage ? [101, 102] : [101]}];
  const grants = {'moodle-1': {moodle_courseid: 101, mode: 'full', scope_version: 1, account_scope: {site, user_id: 7}}};
  if (mergedPage) {
    courses.push({id: 'moodle-2', name: 'Tutorial page', moodle_course_ids: [102], merged_into: 'moodle-1'});
    grants['moodle-2'] = {moodle_courseid: 102, mode: 'info_only', scope_version: 1, account_scope: {site, user_id: 7}};
  }
  save('Study/_System/state.json', {revision: 1, courses, settings: {
    profile_account_scope: {schema: 1, site, user_id: 7},
    course_library_authorization: {schema: 1, scope_version: 1, courses: grants}}, materials: []});
  save('config.local.json', {moodle_url: site}); save('.private/moodle-token.dpapi', 'synthetic');
  const module = (id, name) => ({id, name, modname: 'resource', uservisible: true,
    url: `${site}/mod/resource/view.php?id=${id}`, contents: [{type: 'file', filename: name, filepath: '/',
      fileurl: `${site}/webservice/pluginfile.php/1/mod_resource/content/${id}/${name}`, filesize: null, timemodified: 1}]});
  const modules = [module(501, 'Topic5-A.txt')], requests = [], downloads = [];
  const client = {baseUrl: site, userId: 7, token: 'SYNTHETIC_SECRET',
    async call(method, args) {assert.equal(method, 'core_course_get_contents'); requests.push(args.courseid);
      assert.equal(args.courseid, 101, 'must not access an unrelated unauthorized page');
      return [{id: 1, name: 'Lectures', section: 0, modules}];},
    async transport(url) {const name = decodeURIComponent(new URL(url).pathname.split('/').at(-1)); downloads.push(name);
      return new Response('Contents ' + name, {headers: {'content-type': 'text/plain'}});}};
  const options = {root, loadState: () => read(statePath), course: 'moodle-1', section: 'Topic 5',
    chatKey: 'first-chat', mode: 'strict', acquireMissing: true, connect: async () => client};
  return {root, statePath, options, client, modules, module, requests, downloads};
}

test('new ambiguous chapter candidates prevent a complete result despite an existing local match', async t => {
  const f = fixture(t);
  assert.equal((await runChapterCheck(f.options)).verified.complete, true);
  f.modules.push(f.module(503, 'Topic5-Topic6.txt'));
  const downloaded = f.downloads.length;
  const result = await runChapterCheck({...f.options, chatKey: 'new-chat'});
  assert.equal(result.verified.complete, false);
  assert.equal(result.ambiguous, true);
  assert.ok(result.resolution.candidates.some(row => row.module_id === 503));
  assert.equal(f.downloads.length, downloaded, 'ambiguous scope must not download files');
  assert.equal(read(f.statePath).materials.length, 1);
});

for (const [label, config] of [['merged info-only page', {mergedPage: true}], ['ungranted page under the same course ID', {extraPage: true}]]) {
  test(`chapter acquisition and receipt reuse ignore an unrelated ${label}`, async t => {
    const f = fixture(t, config);
    const result = await runChapterCheck(f.options);
    assert.equal(result.acquisition?.status, 'complete', JSON.stringify(result));
    assert.equal(result.verified.complete, true);
    assert.deepEqual(result.scope.pages_in_scope, [101]);
    assert.equal(read(f.statePath).materials[0].filename, 'Topic5-A.txt');
    const calls = [f.requests.length, f.downloads.length];
    const reused = await runChapterCheck(f.options);
    assert.equal(reused.reused, true);
    assert.equal(reused.verified.complete, true);
    assert.deepEqual([f.requests.length, f.downloads.length], calls);
    await assert.rejects(syncCourseLibrary({client: f.client, root: f.root, state: read(f.statePath),
      courseIds: config.mergedPage ? ['moodle-2'] : ['moodle-1'], pageIds: [102], moduleIds: [602]}), /full authorization/);
    assert.deepEqual([f.requests.length, f.downloads.length], calls, 'scoped pages still require their own authorization');
  });
}
