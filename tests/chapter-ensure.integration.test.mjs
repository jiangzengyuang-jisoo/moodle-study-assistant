import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, unlinkSync, writeFileSync} from 'node:fs';
import {join, resolve, relative, isAbsolute} from 'node:path';
import {grantCourseContent} from '../tools/moodle/content-policy.mjs';
import {python as choosePython} from '../tools/moodle/state-bridge.mjs';
import {runChapterCommand, parseChapterArgs} from '../tools/moodle/chapter-entry.mjs';

const read = path => JSON.parse(readFileSync(path, 'utf8'));
test('lesson acquisition uses real installer/index, restores missing originals, protects scope and edits', async t => {
  const directory = mkdtempSync(join(import.meta.dirname, '.ensure-integration-'));
  t.after(() => {
    const child = relative(realpathSync(import.meta.dirname), realpathSync(directory));
    assert.ok(child && !child.startsWith('..') && !isAbsolute(child));
    rmSync(directory, {recursive: true});
  });
  const studyRoot = join(directory, 'Study'), statePath = join(studyRoot, '_System/state.json');
  mkdirSync(join(studyRoot, '_System'), {recursive: true});
  const initial = grantCourseContent({revision: 1, courses: [{id: 'TEST', moodle_course_ids: [10]}],
    settings: {}, materials: [], mistakes: [{answer: 'keep'}], teaching_cursor: {chapter: 3}},
  {site: 'https://school.test', userId: 7, courseIds: [10], mode: 'full'});
  writeFileSync(statePath, JSON.stringify(initial));
  const names = ['Chapter 8-A.txt', 'Chapter 8-B.txt', 'Chapter 9.txt', 'Extra lecture.txt'];
  const modules = names.map((filename, i) => ({id: 100 + i, name: filename, modname: 'resource',
    url: `https://school.test/mod/resource/view.php?id=${100 + i}`,
    contents: [{type: 'file', filename, filepath: '/', fileurl: `https://school.test/pluginfile.php/77/mod_resource/content/1/${encodeURIComponent(filename)}`}]}));
  let calls = 0, connections = 0, fail = false;
  const downloads = [], outputs = [];
  const client = {baseUrl: 'https://school.test', userId: 7, token: 'fictional-secret',
    async call(method, args) {calls++; assert.equal(method, 'core_course_get_contents'); assert.equal(args.courseid, 10);
      return [{id: 1, name: 'Lectures', modules}];},
    async transport(url) {const filename = decodeURIComponent(new URL(url).pathname.split('/').at(-1));
      downloads.push(filename); if (fail) throw new Error('private?token=fictional-secret');
      return new Response('Text of ' + filename, {headers: {'content-type': 'text/plain'}});}};
  const options = {paths: {studyRoot, statePath, studyToolsDir: resolve(import.meta.dirname, '../tools/study'), python: choosePython(resolve(import.meta.dirname, '..'))},
    connectionIdentity: () => ({site: 'https://school.test', credentialKey: 'synthetic'}), environment: {STUDY_CHAT_ID: 'one'},
    createClient: async () => {connections++; return client;}, output: result => outputs.push(result)};
  const args = ['--ensure', '--course', 'TEST', '--chapter', '8'];
  assert.equal(parseChapterArgs(args, options.environment).ensure, true);
  assert.equal(await runChapterCommand(args, options), 0, JSON.stringify(outputs.at(-1)));
  assert.equal(outputs.at(-1).acquired.length, 2);
  assert.deepEqual([...new Set(downloads)], names.slice(0, 2));
  const after = read(statePath), first = after.materials.find(m => m.filename === names[0]);
  assert.equal(after.materials.length, 2);
  assert.deepEqual(after.mistakes, initial.mistakes);
  assert.deepEqual(after.teaching_cursor, initial.teaching_cursor);
  const count = {calls, connections, downloads: downloads.length};
  assert.equal(await runChapterCommand(args, options), 0);
  assert.deepEqual({calls, connections, downloads: downloads.length}, count, 'same chat is completely offline');
  assert.equal(outputs.at(-1).reused, true);
  assert.equal(read(join(studyRoot, '_System/Library/catalog.json')).materials.every(m => m.status === 'indexed'), true);
  unlinkSync(join(studyRoot, first.relative_path));
  const nextChat = {...options, environment: {STUDY_CHAT_ID: 'two'}};
  assert.equal(await runChapterCommand(args, options), 0, JSON.stringify(outputs.at(-1)));
  assert.equal(outputs.at(-1).acquired[0].status, 'restored');
  assert.equal(readFileSync(join(studyRoot, first.relative_path), 'utf8'), 'Text of ' + names[0]);
  assert.equal(read(statePath).materials.length, 2);
  assert.equal(await runChapterCommand(args, nextChat), 0, JSON.stringify(outputs.at(-1)));
  writeFileSync(join(studyRoot, first.relative_path), 'MY NOTES');
  assert.equal(await runChapterCommand(args, {...options, environment: {STUDY_CHAT_ID: 'three'}}), 1);
  assert.equal(readFileSync(join(studyRoot, first.relative_path), 'utf8'), 'MY NOTES');
  const explicit = ['--ensure', '--course', 'TEST', '--chapter', 'Extra', '--module', '10:103'];
  assert.equal(await runChapterCommand(explicit, options), 0, JSON.stringify(outputs.at(-1)));
  assert.equal(outputs.at(-1).materials.length, 1);
  assert.equal(downloads.includes(names[2]), false);
  const beforeDenied = downloads.length;
  assert.equal(await runChapterCommand([...explicit.slice(0, -1), '20:103'], options), 1);
  assert.equal(downloads.length, beforeDenied);
  client.userId = 8;
  assert.equal(await runChapterCommand(args, {...options, environment: {STUDY_CHAT_ID: 'other-account'}}), 1);
  assert.equal(outputs.at(-1).reason, 'course_content_not_authorized');
  assert.equal(downloads.length, beforeDenied);
  client.userId = 7; fail = true;
  const failing = ['--ensure', '--course', 'TEST', '--chapter', '9'];
  assert.equal(await runChapterCommand(failing, options), 1);
  assert.equal(outputs.at(-1).reason, 'acquisition_failed_existing_materials_preserved');
  const failures = downloads.length;
  assert.equal(await runChapterCommand(failing, options), 1);
  assert.equal(downloads.length, failures, 'failed acquisition is not retried by a follow-up');
  fail = false;
  assert.equal(await runChapterCommand([...failing, '--force'], options), 0, JSON.stringify(outputs.at(-1)));
  assert.equal(JSON.stringify(outputs).includes(client.token), false);
  const unlabelled = ['--ensure', '--course', 'TEST', '--chapter', 'Unknown topic'];
  assert.equal(await runChapterCommand(unlabelled, options), 1);
  assert.equal(outputs.at(-1).reason, 'no_matching_materials_select_module');
});


test('missing old bytes and a remote update install a separate version in the same chat', async t => {
  const directory = mkdtempSync(join(import.meta.dirname, '.missing-version-'));
  t.after(() => {
    const child = relative(realpathSync(import.meta.dirname), realpathSync(directory));
    assert.ok(child && !child.startsWith('..') && !isAbsolute(child));
    rmSync(directory, {recursive: true});
  });
  const studyRoot = join(directory, 'Study'), statePath = join(studyRoot, '_System/state.json');
  mkdirSync(join(studyRoot, '_System'), {recursive: true});
  writeFileSync(statePath, JSON.stringify(grantCourseContent({revision: 1,
    courses: [{id: 'TEST', moodle_course_ids: [10]}], settings: {}, materials: []},
  {site: 'https://school.test', userId: 7, courseIds: [10], mode: 'full'})));
  let bytes = 'Original lesson', connections = 0, transfers = 0;
  const client = {baseUrl: 'https://school.test', userId: 7, token: 'synthetic',
    async call() {return [{id: 1, modules: [{id: 100, name: 'Chapter 8', modname: 'resource',
      contents: [{type: 'file', filename: 'Chapter 8.txt', filepath: '/',
        fileurl: 'https://school.test/pluginfile.php/77/mod_resource/content/1/Chapter%208.txt'}]}]}];},
    async transport() {transfers++; return new Response(bytes, {headers: {'content-type': 'text/plain'}});}};
  const outputs = [], paths = {studyRoot, statePath, studyToolsDir: resolve(import.meta.dirname, '../tools/study'), python: choosePython(resolve(import.meta.dirname, '..'))};
  const options = {paths, connectionIdentity: () => ({site: client.baseUrl, credentialKey: 'synthetic'}),
    environment: {STUDY_CHAT_ID: 'same-chat', CODEX_THREAD_ID: 'same-chat'},
    createClient: async () => {connections++; return client;}, output: value => outputs.push(value)};
  const args = ['--ensure', '--course', 'TEST', '--chapter', '8'];
  assert.equal(await runChapterCommand(args, options), 0, JSON.stringify(outputs.at(-1)));
  const old = read(statePath).materials[0];
  unlinkSync(join(studyRoot, old.relative_path)); bytes = 'Updated lesson';
  assert.equal(await runChapterCommand(args, options), 0, JSON.stringify(outputs.at(-1)));
  const result = outputs.at(-1), state = read(statePath);
  const historical = state.materials.find(m => m.id === old.id), current = state.materials.find(m => !m.superseded_by);
  assert.equal(result.acquired[0].missing_original_id, old.id);
  assert.equal(historical.version_status, 'missing');
  assert.equal(historical.sha256, old.sha256);
  assert.equal(historical.relative_path, old.relative_path);
  assert.notEqual(current.relative_path, old.relative_path);
  assert.equal(readFileSync(join(studyRoot, current.relative_path), 'utf8'), bytes);
  const count = {connections, transfers};
  assert.equal(await runChapterCommand(args, options), 0);
  assert.deepEqual({connections, transfers}, count);
  // Both material IDs resolve to the newly checked lineage without reconnecting.
  for (const id of [old.id, current.id]) {
    assert.equal(await runChapterCommand(['--course', 'TEST', '--chapter', '8', '--material', id], options), 0);
    assert.equal(outputs.at(-1).materials[0].material_id, current.id);
  }
  assert.deepEqual({connections, transfers}, count);
});
