import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { grantCourseContent } from '../tools/moodle/content-policy.mjs';
import { python as choosePython } from '../tools/moodle/state-bridge.mjs';
import { runChapterCommand } from '../tools/moodle/chapter-entry.mjs';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const read = path => JSON.parse(readFileSync(path, 'utf8'));
const write = (path, value) => writeFileSync(path, JSON.stringify(value));
const studyToolsDir = resolve(import.meta.dirname, '../tools/study');
const python = choosePython(resolve(import.meta.dirname, '..'));

test('chapter entry uses real installer and targeted index, preserves history and reuses new IDs offline', async t => {
  const directory = mkdtempSync(join(import.meta.dirname, '.chapter-integration-'));
  t.after(() => {
    const child = relative(realpathSync(import.meta.dirname), realpathSync(directory));
    assert.ok(child && !child.startsWith('..') && !isAbsolute(child));
    rmSync(directory, { recursive: true });
  });
  const studyRoot = join(directory, 'Study'), statePath = join(studyRoot, '_System/state.json');
  mkdirSync(join(studyRoot, '_System'), { recursive: true });
  mkdirSync(join(studyRoot, 'TEST/Lectures'), { recursive: true });
  const oldBytes = Buffer.from('OLD_CHAPTER_SENTINEL'), newBytes = Buffer.from('NEW_CHAPTER_SENTINEL');
  assert.equal(oldBytes.length, newBytes.length);
  const course = { id: 'TEST', moodle_course_ids: [10], teaching_cursor: { evidence: 'preserve' } };
  const material = { id: 'old-A', course_id: 'TEST', filename: 'Chapter 8-A.txt', category: 'Lectures',
    relative_path: 'TEST/Lectures/Chapter 8-A.txt', sha256: hash(oldBytes),
    moodle_file_sources: [{ module_id: 100, url: 'https://school.test/pluginfile.php/77/mod_resource/content/1/Chapter%208-A.txt',
      activity_url: 'https://school.test/mod/resource/view.php?id=100' }] };
  const initial = grantCourseContent({ revision: 1, courses: [course], settings: {}, materials: [material],
    mistakes: [{ id: 'original-answer', material_id: material.id, answer: 'keep independent answer' }] },
  { site: 'https://school.test', userId: 7, courseIds: [10], mode: 'full' });
  write(statePath, initial);
  writeFileSync(join(studyRoot, material.relative_path), oldBytes);
  const index = spawnSync(python, [join(studyToolsDir, 'course_content.py'), '--state', statePath, 'index', '--course', 'TEST'],
    { encoding: 'utf8', windowsHide: true });
  assert.equal(index.status, 0, index.stderr);
  let connections = 0, downloads = 0, calls = 0;
  const sections = [{ id: 1, name: 'Chapter 8', modules: [{ id: 100, modname: 'resource', name: 'Chapter 8-A',
    url: 'https://school.test/mod/resource/view.php?id=100', contents: [{ type: 'file', filename: material.filename,
      filepath: '/', fileurl: material.moodle_file_sources[0].url, filesize: newBytes.length, timemodified: 1 }] }] }];
  const client = { baseUrl: 'https://school.test', userId: 7, token: 'fictional-secret',
    call: async (method, args) => { calls++; assert.equal(method, 'core_course_get_contents'); assert.equal(args.courseid, 10); return sections; },
    transport: async () => { downloads++; return new Response(newBytes, { headers: { 'content-type': 'text/plain' } }); } };
  const output = [], options = { paths: { studyRoot, statePath, studyToolsDir, python },
    connectionIdentity: () => ({site: 'https://school.test', credentialKey: 'synthetic-binding'}), environment: { STUDY_CHAT_ID: 'same-chat' },
    createClient: async () => { connections++; return client; }, output: row => output.push(row) };
  const args = ['--course', 'TEST', '--chapter', '8', '--material', material.id];
  assert.equal(await runChapterCommand(args, options), 0, JSON.stringify(output.at(-1)));
  const receipt = output.at(-1);
  assert.equal(receipt.materials[0].status, 'updated');
  const state = read(statePath), old = state.materials.find(m => m.id === material.id);
  const newest = state.materials.find(m => m.id === old.superseded_by);
  assert.ok(newest);
  assert.equal(old.version_status, 'archived');
  assert.deepEqual(readFileSync(join(studyRoot, old.relative_path)), oldBytes);
  assert.deepEqual(readFileSync(join(studyRoot, newest.relative_path)), newBytes);
  assert.deepEqual(state.courses, initial.courses);
  assert.deepEqual(state.mistakes, initial.mistakes);
  const catalog = read(join(studyRoot, '_System/Library/catalog.json'));
  assert.equal(catalog.materials.find(m => m.id === newest.id).status, 'indexed');
  assert.ok(catalog.materials.find(m => m.id === old.id).relative_path.includes('/_Archive/'));
  assert.equal(await runChapterCommand(args, options), 0);
  assert.equal(await runChapterCommand([...args.slice(0, -1), newest.id], options), 0);
  assert.equal(output.at(-1).reused, true);
  assert.equal(downloads, 1);
  assert.equal(calls, 1);
  assert.equal(connections, 1);
  assert.equal(JSON.stringify(output).includes(client.token), false);
  assert.equal(await runChapterCommand(args, { ...options, environment: { STUDY_CHAT_ID: 'new-chat' } }), 0);
  assert.equal(downloads, 2, 'A new chat compares real bytes again despite unchanged timestamps');
});
