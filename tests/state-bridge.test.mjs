import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {initialize} from '../tools/moodle/core.mjs';
import {saveState} from '../tools/moodle/state-bridge.mjs';

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
