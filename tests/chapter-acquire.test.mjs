import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync, mkdtempSync, readFileSync, writeFileSync, unlinkSync, rmSync, realpathSync} from 'node:fs';
import {join, dirname, relative, isAbsolute} from 'node:path';
import {runChapterCheck} from '../tools/moodle/chapter-check.mjs';
const read = path => JSON.parse(readFileSync(path, 'utf8'));
test('teaching acquires only authorized chapter files through the real installer and index', async t => {
  const root = mkdtempSync(join(import.meta.dirname, '.acquire-'));
  t.after(() => {const child = relative(realpathSync(import.meta.dirname), realpathSync(root));
    assert.ok(child && !child.startsWith('..') && !isAbsolute(child)); rmSync(root, {recursive: true});});
  const save = (name, value) => {const path = join(root, name); mkdirSync(dirname(path), {recursive: true}); writeFileSync(path, value);};
  const site = 'https://school.example/moodle', statePath = join(root, 'Study/_System/state.json');
  const state = {revision: 1, courses: [{id: 'moodle-1', name: 'Demo', moodle_course_ids: [101]}],
    settings: {profile_account_scope: {schema: 1, site, user_id: 7}, course_library_authorization: {
      schema: 1, scope_version: 1, courses: {'moodle-1': {moodle_courseid: 101, mode: 'full', scope_version: 1,
      account_scope: {site, user_id: 7}}}}}, materials: [], mistakes: [{id: 'keep', answer: 'own answer'}]};
  save('Study/_System/state.json', JSON.stringify(state));
  save('config.local.json', JSON.stringify({moodle_url: site})); save('.private/moodle-token.dpapi', 'synthetic');
  const names = ['Topic5-A.txt', 'Topic6.txt'];
  const modules = names.map((name, i) => ({id: 501+i, name, modname: 'resource', uservisible: true,
    url: `${site}/mod/resource/view.php?id=${501+i}`, contents: [{type: 'file', filename: name, filepath: '/',
    fileurl: `${site}/webservice/pluginfile.php/1/mod_resource/content/${501+i}/${name}`, filesize: null, timemodified: 1}]}));
  let connections = 0, calls = 0, failed = false, contentPrefix = 'Contents ';
  const downloads = [];
  const client = {baseUrl: site, userId: 7, token: 'SYNTHETIC_SECRET',
    async call(method, args) {calls++; assert.equal(method, 'core_course_get_contents'); assert.equal(args.courseid, 101);
      return [{id: 1, name: 'Lectures', section: 0, modules}];},
    async transport(url) {const name = decodeURIComponent(new URL(url).pathname.split('/').at(-1)); downloads.push(name);
      if (failed) throw new Error('SYNTHETIC_SECRET'); return new Response(contentPrefix + (name === 'Topic5-copy.txt' ? 'Topic5-A.txt' : name), {headers: {'content-type': 'text/plain'}});}};
  const options = {root, loadState: () => read(statePath), course: 'moodle-1', section: 'Topic 5',
    chatKey: 'first-chat', mode: 'strict', acquireMissing: true, connect: async () => {connections++; return client;}};
  let result = await runChapterCheck(options);
  assert.equal(result.acquisition?.status, 'complete', JSON.stringify(result));
  assert.equal(result.verified.complete, true);
  assert.deepEqual([...new Set(downloads)], ['Topic5-A.txt']);
  const material = read(statePath).materials[0];
  assert.equal(readFileSync(join(root, 'Study', material.relative_path), 'utf8'), 'Contents Topic5-A.txt');
  assert.deepEqual(read(statePath).mistakes, state.mistakes);
  assert.ok(read(join(root, 'Study/_System/Library/catalog.json')).materials.length);
  const count = [connections, calls, downloads.length];
  result = await runChapterCheck(options);
  assert.equal(result.reused, true); assert.deepEqual([connections, calls, downloads.length], count);
  unlinkSync(join(root, 'Study', material.relative_path));
  result = await runChapterCheck({...options, chatKey: 'restore'});
  assert.equal(result.verified.complete, true, JSON.stringify(result));
  assert.equal(readFileSync(join(root, 'Study', material.relative_path), 'utf8'), 'Contents Topic5-A.txt');
  // A newly uploaded part in the same chapter must be found even with an existing local mapping.
  modules.push({...modules[0], id: 503, name: 'Topic5-B.txt', url: `${site}/mod/resource/view.php?id=503`,
    contents: [{...modules[0].contents[0], filename: 'Topic5-B.txt', fileurl: `${site}/webservice/pluginfile.php/1/mod_resource/content/503/Topic5-B.txt`}]});
  result = await runChapterCheck({...options, chatKey: 'new-part'});
  assert.equal(result.verified.complete, true, JSON.stringify(result));
  assert.ok(read(statePath).materials.some(m => m.filename === 'Topic5-B.txt'));
  modules.push({...modules[0], id: 504, name: 'Topic5-copy.txt', url: `${site}/mod/resource/view.php?id=504`,
    contents: [{...modules[0].contents[0], filename: 'Topic5-copy.txt', fileurl: `${site}/webservice/pluginfile.php/1/mod_resource/content/504/Topic5-copy.txt`}]});
  result = await runChapterCheck({...options, chatKey: 'duplicate-part'});
  assert.equal(result.verified.complete, true, JSON.stringify(result));
  assert.ok(result.files.some(row => row.module_id === 504));
  writeFileSync(join(root, 'Study', material.relative_path), 'MY LOCAL NOTES');
  contentPrefix = 'Updated ';
  result = await runChapterCheck({...options, chatKey: 'edited-original'});
  assert.equal(result.verified.complete, false, 'failed acquisition must not report verification success');
  assert.equal(readFileSync(join(root, 'Study', material.relative_path), 'utf8'), 'MY LOCAL NOTES');
  failed = true;
  result = await runChapterCheck({...options, section: 'Topic 6'});
  assert.equal(result.acquisition.status, 'incomplete');
  const failedCount = downloads.length;
  await runChapterCheck({...options, section: 'Topic 6'});
  assert.equal(downloads.length, failedCount);
  failed = false;
  const denied = read(statePath); denied.settings.course_library_authorization.courses['moodle-1'].mode = 'info_only';
  save('Study/_System/state.json', JSON.stringify(denied));
  await assert.rejects(runChapterCheck({...options, chatKey: 'denied'}), /完整资料授权/);
  assert.equal(downloads.length, failedCount);
});
