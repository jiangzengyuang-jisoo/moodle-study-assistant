import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, writeFileSync} from 'node:fs';
import {join, resolve, sep} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {acquireChapterLock} from '../tools/moodle/chapter-lock.mjs';
import {verifyChapter} from '../tools/moodle/chapter-verify.mjs';
import {grantCourseContent} from '../tools/moodle/content-policy.mjs';
import {python as choosePython} from '../tools/moodle/state-bridge.mjs';

const parent = realpathSync(import.meta.dirname);
const python = choosePython(resolve(parent, '..'));
const site = 'https://school.example';
const sha = value => createHash('sha256').update(value).digest('hex');
function fixture(t) {
  const root = mkdtempSync(join(parent, '.lock-test-'));
  t.after(() => {assert.ok(resolve(root).startsWith(parent + sep)); rmSync(root, {recursive: true, force: true});});
  mkdirSync(join(root, '_System'), {recursive: true});
  mkdirSync(join(root, 'TEST/Lectures'), {recursive: true});
  const statePath = join(root, '_System/state.json');
  const state = grantCourseContent({courses: [{id: 'TEST', moodle_course_ids: [10]}], settings: {},
    materials: [{id: 'M1', course_id: 'TEST', filename: 'lesson.txt', category: 'Lectures',
      relative_path: 'TEST/Lectures/lesson.txt', sha256: sha('ORIGINAL'),
      moodle_file_sources: [{module_id: 500, url: site + '/pluginfile.php/lesson.txt'}]}]},
  {site, userId: 7, courseIds: [10], mode: 'full'});
  writeFileSync(statePath, JSON.stringify(state));
  writeFileSync(join(root, 'TEST/Lectures/lesson.txt'), 'ORIGINAL');
  const options = {studyRoot: root, statePath, python, studyToolsDir: resolve(parent, '../tools/study'),
    courseId: 'TEST', chapter: '1', sessionId: 'same-chat', materialIds: ['M1'], force: true};
  const folder = join(root, '_System/CourseContent/chapter-checks');
  mkdirSync(folder, {recursive: true});
  const lockPath = join(folder, sha(JSON.stringify([site, 7, 'same-chat', 'TEST', '1'])) + '.json.lock');
  return {root, statePath, options, lockPath};
}

test('OS lock excludes simultaneous contenders and remains reusable without deleting the marker', async t => {
  const f = fixture(t);
  const leases = await Promise.all([acquireChapterLock(f.lockPath, python), acquireChapterLock(f.lockPath, python)]);
  assert.deepEqual(leases.map(l => l.status).sort(), ['acquired', 'busy']);
  const held = leases.find(l => l.status === 'acquired');
  try {
    held.assertHeld();
    assert.equal((await acquireChapterLock(f.lockPath, python)).status, 'busy');
  } finally {await held.release();}
  assert.ok(statSync(f.lockPath).isDirectory(), 'Atomic directory marker distinguishes the OS-lock protocol');
  assert.deepEqual(readdirSync(f.lockPath), [], 'Ownership must not depend on stored process numbers');
  for (let i = 0; i < 2; i++) {
    const retry = await acquireChapterLock(f.lockPath, python);
    assert.equal(retry.status, 'acquired'); await retry.release();
    assert.ok(statSync(f.lockPath).isDirectory());
  }
});

test('force never removes a live chapter lock or starts a second network check', async t => {
  const f = fixture(t);
  let unblock, entered;
  const started = new Promise(resolve => {entered = resolve;});
  const wait = new Promise(resolve => {unblock = resolve;});
  const first = verifyChapter({baseUrl: site, userId: 7, async call() {entered(); await wait; return [];}}, f.options);
  await started;
  try {
    let calls = 0;
    const second = await verifyChapter({baseUrl: site, userId: 7, async call() {calls++; return [];}}, f.options);
    assert.equal(second.materials[0].reason, 'chapter_check_already_running');
    assert.equal(calls, 0);
  } finally {unblock(); await first;}
});

test('legacy empty or unrecognized lock is preserved even with force, without guessing its owner', async t => {
  const f = fixture(t);
  for (const marker of ['', '{"pid":1234,"created_at":"2000-01-01"}']) {
    writeFileSync(f.lockPath, marker);
    let calls = 0;
    const result = await verifyChapter({baseUrl: site, userId: 7, async call() {calls++; return [];}}, f.options);
    assert.equal(result.materials[0].reason, 'chapter_check_legacy_lock_needs_review');
    assert.equal(readFileSync(f.lockPath, 'utf8'), marker);
    assert.equal(calls, 0);
  }
});

test('killing the owning Node process releases its pipe and real chapter force retries recover', async t => {
  const f = fixture(t), moduleUrl = new URL('../tools/moodle/chapter-verify.mjs', import.meta.url).href;
  const script = `import {verifyChapter} from ${JSON.stringify(moduleUrl)};
    const timer = setInterval(() => {}, 1000);
    await verifyChapter({baseUrl:${JSON.stringify(site)},userId:7,
      async call(){console.log('NETWORK_STARTED');return new Promise(() => {});}}, JSON.parse(process.argv[1]));
    clearInterval(timer);`;
  const child = spawn(process.execPath, ['--input-type=module', '--eval', script, JSON.stringify(f.options)],
    {windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
  const exited = new Promise(resolve => child.once('exit', resolve));
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Owner never started its mock network call')), 15000);
      let stderr = '';
      child.stderr.on('data', bytes => {stderr += bytes;});
      child.stdout.on('data', bytes => {
        if (bytes.toString().includes('NETWORK_STARTED')) {clearTimeout(timer); resolve();}
      });
      child.once('error', error => {clearTimeout(timer); reject(error);});
      exited.then(() => {clearTimeout(timer); reject(new Error('Owner exited early: ' + stderr));});
    });
  } finally {child.kill('SIGKILL'); await exited;}
  let calls = 0;
  const client = {baseUrl: site, userId: 7, async call() {calls++; return [];}};
  let result;
  for (let i = 0; i < 30; i++) {
    result = await verifyChapter(client, f.options);
    if (result.materials[0].reason !== 'chapter_check_already_running') break;
    await delay(25);
  }
  assert.equal(result.materials[0].reason, 'source_missing_or_renamed');
  assert.equal(calls, 1, 'Retry reached the real check instead of sticking on a residual file');
  await verifyChapter(client, f.options);
  assert.equal(calls, 2);
  assert.equal(readFileSync(join(f.root, 'TEST/Lectures/lesson.txt'), 'utf8'), 'ORIGINAL');
});

test('loss of the lock helper is detected, and another owner can safely acquire the OS lock', async t => {
  const f = fixture(t), lease = await acquireChapterLock(f.lockPath, python);
  assert.equal(lease.status, 'acquired');
  process.kill(lease.helperPid, 'SIGKILL');
  for (let i = 0; i < 100; i++) {
    try {lease.assertHeld();} catch {break;}
    await delay(10);
  }
  assert.throws(() => lease.assertHeld(), error => error.code === 'CHAPTER_LOCK_LOST');
  await lease.release();
  const next = await acquireChapterLock(f.lockPath, python);
  assert.equal(next.status, 'acquired'); await next.release();
});

test('helper loss during a chapter check stops further receipt, installation and index writes', async t => {
  const f = fixture(t), beforeState = readFileSync(f.statePath);
  let lease, beforeReceipt, downloads = 0, installs = 0, indexes = 0;
  const receiptPath = f.lockPath.slice(0, -'.lock'.length);
  const options = {...f.options,
    acquireLock: async (...args) => {lease = await acquireChapterLock(...args); return lease;},
    install: async () => {installs++; throw new Error('Must not install after lock loss');},
    indexMaterials: async () => {indexes++; throw new Error('Must not index after lock loss');},
  };
  const client = {baseUrl: site, userId: 7, async call() {
    beforeReceipt = readFileSync(receiptPath);
    process.kill(lease.helperPid, 'SIGKILL');
    for (let i = 0; i < 100; i++) {
      try {lease.assertHeld();} catch {break;}
      await delay(10);
    }
    return [{id: 1, name: 'Lectures', modules: [{id: 500, modname: 'resource', name: 'lesson',
      contents: [{type: 'file', filename: 'lesson.txt', filepath: '/', fileurl: site + '/pluginfile.php/lesson.txt'}]}]}];
  }, async transport() {downloads++; return new Response('NEW');}};
  await assert.rejects(verifyChapter(client, options), error => error.code === 'CHAPTER_LOCK_LOST');
  assert.deepEqual({downloads, installs, indexes}, {downloads: 0, installs: 0, indexes: 0});
  assert.deepEqual(readFileSync(f.statePath), beforeState);
  assert.deepEqual(readFileSync(receiptPath), beforeReceipt);
  const next = await acquireChapterLock(f.lockPath, python);
  assert.equal(next.status, 'acquired'); await next.release();
});
