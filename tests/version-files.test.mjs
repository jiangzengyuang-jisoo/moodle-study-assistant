import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {resolve, join, sep} from 'node:path';
import {installVersion} from '../tools/moodle/version-files.mjs';

const project = resolve(import.meta.dirname, '..');
const python = join(project, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const script = join(project, 'tools/study/material_versions.py');
const base = join(project, 'tmp/version-tests');
mkdirSync(base, {recursive: true});
const digest = value => createHash('sha256').update(value).digest('hex');

test('real Python bridge installs new text, preserves old evidence and skips duplicate', () => {
  const root = mkdtempSync(join(base, 'bridge-'));
  try {
    const study = join(root, 'Study'), statePath = join(study, '_System/state.json');
    mkdirSync(join(study, 'DEMO/Lectures'), {recursive: true});
    mkdirSync(join(study, '_System'), {recursive: true});
    writeFileSync(join(study, 'DEMO/Lectures/lesson.txt'), 'OLD SENTINEL');
    writeFileSync(statePath, JSON.stringify({revision: 1, courses: [{id: 'DEMO'}],
      materials: [{id: 'old', course_id: 'DEMO', filename: 'lesson.txt', category: 'Lectures',
        relative_path: 'DEMO/Lectures/lesson.txt', sha256: digest('OLD SENTINEL'),
        moodle_file_sources: [{module_id: 501, remote_filename: 'lesson.txt'}]}],
      mistakes: [{id: 'q1', material_id: 'old', answer: 'my unchanged answer'}]}));
    const args = {study, statePath, script, python,
      source: {course_id: 'DEMO', module_id: 501, filename: 'lesson.txt', category: 'Lectures'},
      bytes: Buffer.from('NEW SENTINEL')};
    const first = installVersion(args);
    assert.equal(first.status, 'promoted');
    const state = JSON.parse(readFileSync(statePath, 'utf8'));
    assert.equal(readFileSync(join(study, first.relative_path), 'utf8'), 'NEW SENTINEL');
    const old = state.materials.find(m => m.id === 'old');
    assert.equal(readFileSync(join(study, old.relative_path), 'utf8'), 'OLD SENTINEL');
    assert.equal(state.mistakes[0].material_id, 'old');
    assert.equal(state.mistakes[0].answer, 'my unchanged answer');
    const before = readFileSync(statePath);
    assert.equal(installVersion(args).status, 'identical');
    assert.deepEqual(readFileSync(statePath), before);
    assert.throws(() => installVersion({...args, source: {...args.source, filename: '../escape.txt'}}));
    assert(!existsSync(join(study, '_Inbox/escape.txt')));
  } finally {
    assert(resolve(root).startsWith(resolve(base) + sep));
    rmSync(root, {recursive: true, force: true});
  }
});

test('bulk bridge promotes a streamed original, retains filename and defers indexing', () => {
  const root = mkdtempSync(join(base, 'bulk-'));
  try {
    const study = join(root, 'Study'), statePath = join(study, '_System/state.json');
    mkdirSync(join(study, '_System'), {recursive: true});
    const stagedPath = join(study, '_Inbox/.course-content/unique/original.zip');
    mkdirSync(resolve(stagedPath, '..'), {recursive: true});
    writeFileSync(stagedPath, Buffer.alloc(4 * 1024 * 1024, 42));
    const expected = digest(readFileSync(stagedPath));
    writeFileSync(statePath, JSON.stringify({revision: 1, courses: [{id: 'DEMO', moodle_course_ids: [100]}],
      materials: [], settings: {course_content_sync: {version: 1, grants: [{site: 'https://school.example',
        user_id: 77, moodle_course_id: 100, mode: 'full', authorized_at: '2026-01-01T00:00:00Z'}]}}}));
    const result = installVersion({study, statePath, script, python, stagedPath,
      deferIndex: true, sourceSubdirectory: true, retainUnparsed: true,
      source: {course_id: 'DEMO', module_id: 501, filename: 'original.zip', category: 'Lectures',
        scope: {site: 'https://school.example', user_id: 77, page_id: 100}}});
    assert.equal(result.status, 'promoted');
    assert.equal(result.sha256, expected);
    assert.equal(result.bytes, 4 * 1024 * 1024);
    assert.match(result.relative_path, /^DEMO\/Lectures\/source-[a-f0-9]{24}\/original.zip$/);
    assert.equal(digest(readFileSync(join(study, result.relative_path))), expected);
    assert(!existsSync(stagedPath));
    assert(!existsSync(join(study, '_System/Library/catalog.json')));
    const material = JSON.parse(readFileSync(statePath, 'utf8')).materials[0];
    assert.equal(material.moodle_file_sources[0].page_id, 100);
    assert.equal(material.content_reviewed, false);
  } finally {
    assert(resolve(root).startsWith(resolve(base) + sep));
    rmSync(root, {recursive: true, force: true});
  }
});

test('staged bridge refuses files outside Inbox and ambiguous byte sources', () => {
  const root = mkdtempSync(join(base, 'reject-'));
  try {
    const study = join(root, 'Study');
    mkdirSync(join(study, 'DEMO'), {recursive: true});
    const stagedPath = join(study, 'DEMO/original.txt');
    writeFileSync(stagedPath, 'Original notes');
    const args = {study, script, python, stagedPath, source: {filename: 'original.txt'}};
    assert.throws(() => installVersion(args), /inside _Inbox/);
    assert.throws(() => installVersion({...args, bytes: Buffer.from('new')}), /bytes or a staged path/);
    assert.equal(readFileSync(stagedPath, 'utf8'), 'Original notes');
  } finally {
    assert(resolve(root).startsWith(resolve(base) + sep));
    rmSync(root, {recursive: true, force: true});
  }
});
