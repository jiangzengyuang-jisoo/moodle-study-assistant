// Program root / data root separation: a separate STUDY_DATA_ROOT holds all
// personal data while the installed package stays read-only and clean.
// Everything here runs against a throwaway program copy and synthetic roots.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {clientTarget} from '../tools/moodle/core.mjs';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
mkdirSync(join(ROOT, 'tmp'), {recursive: true});
const tempRoot = mkdtempSync(join(ROOT, 'tmp', 'data-root-node-'));
after(() => {
  // Lock files are closed once the child processes exit; tolerate a Windows retry race.
  try { rmSync(tempRoot, {recursive: true, force: true, maxRetries: 3}); } catch { /* best effort */ }
});

const venvPython = [join(ROOT, '.venv/Scripts/python.exe'), join(ROOT, '.venv/bin/python')]
  .find(candidate => existsSync(candidate)) || 'python';

let programPath = null;

function programCopy() {
  if (programPath) return programPath;
  const program = join(tempRoot, 'program');
  cpSync(join(ROOT, 'tools'), join(program, 'tools'), {recursive: true,
    filter: source => !source.split(/[\\/]/).includes('__pycache__') && !source.endsWith('.pyc')});
  cpSync(join(ROOT, 'node_modules'), join(program, 'node_modules'), {recursive: true});
  cpSync(join(ROOT, 'client-target.json'), join(program, 'client-target.json'));
  cpSync(join(ROOT, 'package.json'), join(program, 'package.json'));
  programPath = program;
  return program;
}

function engineEnvironment(dataRoot) {
  const env = {...process.env};
  delete env.STUDY_DATA_ROOT;
  delete env.STUDY_CHAT_ID;
  if (dataRoot) env.STUDY_DATA_ROOT = dataRoot;
  env.STUDY_PYTHON = venvPython;
  env.TMP = tempRoot;
  env.TEMP = tempRoot;
  return env;
}

function initializeWith(program, dataRoot) {
  return spawnSync(process.execPath, [join(program, 'tools/moodle/run.mjs'), 'init'],
    {cwd: program, env: engineEnvironment(dataRoot), encoding: 'utf8', windowsHide: true});
}

function assertProgramClean(program) {
  for (const name of ['Study', 'config.local.json', '.private', '.codex'])
    assert.equal(existsSync(join(program, name)), false, `program root must stay clean: ${name}`);
}

test('run.mjs init writes only STUDY_DATA_ROOT and repeats idempotently', () => {
  const program = programCopy();
  const data = join(tempRoot, 'data-env');
  const first = initializeWith(program, data);
  assert.equal(first.status, 0, first.stderr);
  assert.ok(existsSync(join(data, 'Study/_System/state.json')));
  assert.ok(existsSync(join(data, 'config.local.json')));
  assert.ok(existsSync(join(data, '.private')));
  assertProgramClean(program);
  // The release marker is a program asset: this branch is deepseek, so a data
  // root must never receive the legacy Codex configuration.
  assert.equal(clientTarget(program), 'deepseek');
  assert.equal(existsSync(join(data, '.codex/config.toml')), false);
  const state = readFileSync(join(data, 'Study/_System/state.json'));
  const repeated = initializeWith(program, data);
  assert.equal(repeated.status, 0, repeated.stderr);
  assert.deepEqual(readFileSync(join(data, 'Study/_System/state.json')), state);
  assertProgramClean(program);
});

test('the release marker is read from the program directory, not the data root', () => {
  const program = programCopy();
  writeFileSync(join(program, 'client-target.json'), JSON.stringify({version: 1, client: 'codex'}));
  assert.equal(clientTarget(program), 'codex');
  // A conflicting marker inside personal data must not override the package.
  const data = join(tempRoot, 'data-codex');
  mkdirSync(data, {recursive: true});
  writeFileSync(join(data, 'client-target.json'), JSON.stringify({version: 1, client: 'deepseek'}));
  const result = initializeWith(program, data);
  assert.equal(result.status, 0, result.stderr);
  const configuration = join(data, '.codex/config.toml');
  assert.ok(existsSync(configuration));
  const text = readFileSync(configuration, 'utf8');
  assert.ok(text.includes(join(program, 'tools/moodle/run.mjs').replaceAll('\\', '/')));
  assertProgramClean(program);
});

test('without STUDY_DATA_ROOT init keeps writing the program root, as before', () => {
  const program = programCopy();
  const withoutRoot = engineEnvironment(null);
  assert.equal('STUDY_DATA_ROOT' in withoutRoot, false);
  assertProgramClean(program);
  const result = spawnSync(process.execPath, [join(program, 'tools/moodle/run.mjs'), 'init'],
    {cwd: program, env: withoutRoot, encoding: 'utf8', windowsHide: true});
  assert.equal(result.status, 0, result.stderr);
  assert.ok(existsSync(join(program, 'Study/_System/state.json')));
  assert.ok(existsSync(join(program, 'config.local.json')));
  assert.ok(existsSync(join(program, '.private')));
});
