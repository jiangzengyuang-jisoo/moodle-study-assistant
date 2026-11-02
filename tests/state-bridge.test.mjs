import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {initialize} from '../tools/moodle/core.mjs';
import {saveState} from '../tools/moodle/state-bridge.mjs';
import {courseCatalog, selectCoursesInChat} from '../tools/moodle/course-selection.mjs';

test('Node course-style save uses shared Python commit and rejects a stale draft', () => {
  const root = mkdtempSync(join(tmpdir(), 'state 学习 '));
  try {
    const original = initialize(root);
    const next = structuredClone(original);
    next.courses.push({id: 'DEMO', moodle_course_ids: [12]});
    saveState(root, 'replace', {expected: original, state: next});
    const path = join(root, 'Study/_System/state.json');
    const saved = JSON.parse(readFileSync(path));
    assert.equal(saved.revision, original.revision + 1);
    assert.deepEqual(saved.mistakes, original.mistakes);
    assert.throws(() => saveState(root, 'replace', {expected: original, state: original}));
    assert.deepEqual(JSON.parse(readFileSync(path)), saved);
    assert.deepEqual(JSON.parse(readFileSync(join(root, 'Study/_System/state.previous.json'))), original);
  } finally { rmSync(root, {recursive: true, force: true}); }
});

test('chat selection commits through the real state bridge and rejects replayed stale choices', async () => {
  const root = mkdtempSync(join(tmpdir(), 'chat courses 中文 '));
  try {
    const original = initialize(root);
    const path = join(root, 'Study/_System/state.json');
    const deps = {client: {userId: 17, baseUrl: 'https://school.example',
      call: async () => [{id: 9, fullname: 'Synthetic math', shortname: 'SYN'}]},
      readState: () => JSON.parse(readFileSync(path)),
      writeState: (expected, state) => saveState(root, 'replace', {expected, state})};
    const selection = {...await courseCatalog(deps), mode: 'full', course_ids: [9]};
    await selectCoursesInChat({...deps, selection});
    const saved = deps.readState();
    assert.equal(saved.revision, original.revision + 1);
    assert.deepEqual(saved.courses[0].moodle_course_ids, [9]);
    assert.equal(saved.settings.course_content_sync.grants[0].mode, 'full');
    assert.deepEqual(saved.mistakes, original.mistakes);
    assert.deepEqual(JSON.parse(readFileSync(join(root, 'Study/_System/state.previous.json'))), original);
    await assert.rejects(selectCoursesInChat({...deps, selection}), /状态已变化/);
    assert.deepEqual(deps.readState(), saved);
  } finally { rmSync(root, {recursive: true, force: true}); }
});
