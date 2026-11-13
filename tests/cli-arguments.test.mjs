// CLI preflight must finish before touching any real profile or authorization.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {chatCourseOptions} from '../tools/moodle/chat-course-selection.mjs';

const script = fileURLToPath(new URL('../tools/moodle/run.mjs', import.meta.url));
const source = readFileSync(script, 'utf8');
// Evaluate the actual preflight/main functions with tripwires instead of importing
// the executable entry, which would otherwise start an account connection.
const start = source.indexOf('function validateArguments(mode, args) {');
const end = source.indexOf('main().catch(error =>');
assert.ok(start > 0 && end > start);
const entry = source.slice(start, end);

async function isolated(args, {isTTY = false} = {}) {
  const touched = [];
  const stop = name => () => { touched.push(name); throw new Error('unexpected side effect'); };
  const context = {
    process: {argv: ['node', script, ...args], env: {}, stdin: {isTTY}},
    statePath: 'SYNTHETIC-STATE', root: 'SYNTHETIC-ROOT', chatCourseOptions,
    existsSync: stop('profile'), json: stop('config'), tokenFromWindows: stop('credential'),
    initialize: stop('initialize'),
    ReadOnlyClient: class { constructor() { touched.push('network'); throw new Error('unexpected side effect'); } },
  };
  let error;
  try { await vm.runInNewContext(entry + '\nmain()', context); }
  catch (caught) { error = caught; }
  return {error, touched};
}

const invalid = [
  ['unknown'], ['collect-course', 'C'], ['__proto__'],
  ['init', '--extra'], ['check', '--extra'], ['mcp', '--extra'],
  ['refresh', '--chat'], ['refresh', '--chat', 'C', '--extra'],
  ['collect'], ['collect', '--request', 'request.json'],
  ['collect', '--request', '--output', 'out.json'],
  ['collect', '--request', 'request.json', '--output', 'out.json', 'extra'],
  ['courses', '--list', '--extra'], ['courses', '--select-file'],
  ['courses', '--select', 'ECON1210', '--mode', 'full', '--yes'],
  ['courses', '--select', '101', '--mode', 'other', '--yes'],
  ['courses', '--select', '0', '--mode', 'full', '--yes'],
  ['courses', '--select', '101', '--mode', 'full', '--yes', '--yes'],
  ['library-sync', '--course'], ['library-sync', '--snapshot', '--dry-run'],
  ['library-sync', '--max-bytes', 'NaN'], ['library-sync', '--batch-size', '0'],
  ['library-sync', '--batch-size', '9007199254740992'],
  ['chapter-check', '--section', 'Topic 1'], ['chapter-check', '--course', 'C'],
  ['chapter-check', '--course', 'C', '--module', '1,invalid'],
  ['chapter-check', '--course', 'C', '--section', 'Topic 1', '--mode', 'other'],
  ['content-sync'], ['content-sync', '--snapshot', 'one.json', '--snapshot', 'two.json'],
  ['download', '101'], ['download', '--authorized'], ['download', '0', '--authorized'],
  ['download', '101', '101', '--authorized'], ['download', 'NaN', '--authorized'],
  ['download', '101', '--authorized', '--extra'],
  ['download', ...Array.from({length: 21}, (_, index) => String(index + 1)), '--authorized'],
];

for (const [index, args] of invalid.entries()) {
  test(`invalid argument case ${index + 1} stops before profile/credential/network`, async () => {
    const {error, touched} = await isolated(args);
    assert.match(error?.message ?? '', /^参数错误：/);
    assert.match(error.message, /示例：node tools\/moodle\/run\.mjs /);
    assert.deepEqual(touched, []);
  });
}

test('valid current syntax is retained without changing course or download authorization', async () => {
  const valid = [
    [], ['check'], ['refresh'], ['refresh', '--chat', 'session-original'],
    ['collect', '--request', 'request.json', '--output', 'out.json'],
    ['courses', '--list'], ['courses', '--select-file', 'choice.json'],
    ['courses', '--select', '101, 202', '--mode', 'full', '--yes', '--no-sync', '--chat', 'original'],
    ['courses', '--select', '101，202,', '--mode', 'info_only', '--yes', '--refresh'],
    ['library-sync'], ['library-sync', '--course', 'C', '--course', 'Other', '--snapshot', 'current.json', '--dry-run'],
    ['library-sync', '--no-snapshot', '--max-bytes', '65536', '--batch-size', '10'],
    ['chapter-check', '--course', 'C', '--section', 'Topic 1', '--chat-key', 'original', '--mode', 'strict'],
    ['chapter-check', '--course', '101', '--module', '11, 12', '--mode', 'quick', '--force', '--no-persist'],
    ['chapter-check', '--course', '唯一课程名', '--section', 'Topic 1'],
    ['content-sync', '--snapshot', 'current.json'],
    ['download', '11', '12', '--authorized'], ['download', '--authorized', '11'],
  ];
  for (const args of valid) {
    const {error, touched} = await isolated(args);
    assert.equal(error?.message, 'unexpected side effect', JSON.stringify(args));
    assert.deepEqual(touched, ['profile'], JSON.stringify(args));
  }
  const interactive = await isolated(['courses'], {isTTY: true});
  assert.deepEqual(interactive.touched, ['profile']);
});

test('course ID guidance distinguishes page selection from chapter references', async () => {
  const select = await isolated(['courses', '--select', 'ECON1210', '--mode', 'full', '--yes']);
  assert.match(select.error.message, /Moodle 正整数页面 ID.*不是本地课程编号/);
  const chapter = await isolated(['chapter-check', '--section', 'Topic 1']);
  assert.match(chapter.error.message, /本地课程 ID（按原大小写）、Moodle 数字页面 ID 或唯一课程名/);
});

test('real process exits nonzero with safe usage before any profile is available', () => {
  // A never-created data root proves the error is preflight, not setup/auth.
  const dataRoot = join(tmpdir(), 'study-cli-not-created-' + randomUUID());
  for (const args of [['unknown'], ['check', '--extra'], ['courses', '--list', '--extra'],
                      ['download', '11'], ['https://invalid/?token=SYNTHETIC-SECRET']]) {
    const result = spawnSync(process.execPath, [script, ...args], {
      encoding: 'utf8', timeout: 15000,
      env: {...process.env, STUDY_DATA_ROOT: dataRoot, ELECTRON_RUN_AS_NODE: '1'},
    });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, '');
    assert.match(result.stderr, /^参数错误：/);
    assert.match(result.stderr, /示例：node tools\/moodle\/run\.mjs /);
    assert.doesNotMatch(result.stderr, /setup\.cmd|SYNTHETIC-SECRET|https:\/\/invalid|\n\s+at\s|Error:/);
  }
});
