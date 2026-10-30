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
