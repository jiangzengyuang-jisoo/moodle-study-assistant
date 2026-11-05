import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { dirname, isAbsolute, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseChapterArgs, runChapterCommand } from '../tools/moodle/chapter-entry.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const args = ['--course', 'TEST', '--chapter', '8', '--material', 'A,B'];
function fixture(t) {
  const directory = mkdtempSync(join(here, '.chapter-entry-test-'));
  t.after(() => {
    const child = relative(realpathSync(here), realpathSync(directory));
    assert.ok(child && !child.startsWith('..') && !isAbsolute(child));
    rmSync(directory, { recursive: true });
  });
  const outputs = [], counts = { connections: 0, checks: 0 };
  const options = { paths: { studyRoot: directory }, environment: { STUDY_CHAT_ID: 'chat-one' },
    connectionIdentity: () => ({site: 'https://school.test', credentialKey: 'encrypted-file-fingerprint'}),
    createClient: async () => { counts.connections++; return { baseUrl: 'https://school.test', userId: 7 }; },
    verify: async () => { counts.checks++; return { ready_for_teaching: true, reused: false }; },
    cached: async () => ({ ready_for_teaching: true, reused: true }), output: value => outputs.push(value) };
  return { options, outputs, counts };
}

test('chapter arguments require explicit material scope and stable chat identity', () => {
  assert.deepEqual(parseChapterArgs([...args, '--material', 'B,C'], { STUDY_CHAT_ID: 'one' }),
    { materialIds: ['A', 'B', 'C'], force: false, courseId: 'TEST', chapter: '8', sessionId: 'one' });
  assert.equal(parseChapterArgs([...args, '--chat', 'explicit', '--force'], {}).sessionId, 'explicit');
  for (const invalid of [[], args, [...args, '--refresh'], [...args, '--chat', ''],
    [...args, '--chat', 'one', '--course', 'OTHER'], [...args, '--material', 'A,,B', '--chat', 'one']]) {
    assert.throws(() => parseChapterArgs(invalid, {}), error => error.code === 'CHAPTER_USAGE');
  }
});

test('second invocation reuses local receipt before creating a network client', async t => {
  const { options, outputs, counts } = fixture(t);
  assert.equal(await runChapterCommand(args, options), 0);
  assert.equal(await runChapterCommand(args, options), 0);
  assert.deepEqual(counts, { connections: 1, checks: 1 });
  assert.equal(outputs.at(-1).reused, true);
});

test('new chat, explicit force and changed credential each require a new check', async t => {
  const { options, counts } = fixture(t);
  await runChapterCommand(args, options);
  await runChapterCommand([...args, '--force'], options);
  await runChapterCommand(args, { ...options, environment: { STUDY_CHAT_ID: 'chat-two' } });
  await runChapterCommand(args, { ...options, connectionIdentity: () => ({site: 'https://school.test', credentialKey: 'changed-encrypted-file'}) });
  assert.deepEqual(counts, { connections: 4, checks: 4 });
});

test('startup failures are sanitized and not retried in the same chat', async t => {
  const { options, outputs } = fixture(t);
  let attempts = 0;
  options.createClient = async () => { attempts++; throw new Error('private-url?token=SECRET'); };
  options.verify = async () => assert.fail('No usable client');
  assert.equal(await runChapterCommand(args, options), 1);
  assert.equal(await runChapterCommand(args, options), 1);
  assert.equal(attempts, 1);
  assert.equal(outputs.at(-1).reused, true);
  assert.equal(JSON.stringify(outputs).includes('SECRET'), false);
  assert.equal(await runChapterCommand([...args, '--force'], options), 1);
  assert.equal(attempts, 2);
});

test('invalidated local receipt is returned without a surprise retry', async t => {
  const { options, counts, outputs } = fixture(t);
  await runChapterCommand(args, options);
  options.cached = async () => ({ status: 'needs_review', ready_for_teaching: false, reused: true });
  assert.equal(await runChapterCommand(args, options), 1);
  assert.equal(counts.connections, 1);
  assert.equal(outputs.at(-1).status, 'needs_review');
});

test('missing selection or chat ID never touches credentials or network', async t => {
  const { options } = fixture(t);
  options.environment = {};
  options.connectionIdentity = () => assert.fail('No credential reads');
  options.createClient = async () => assert.fail('No connection');
  assert.equal(await runChapterCommand(args, options), 1);
});

test('changing school with the same encrypted credential never reuses another site receipt', async t => {
  const {options, counts} = fixture(t);
  let site = 'https://school.test';
  options.connectionIdentity = () => ({site, credentialKey: 'same-encrypted-file'});
  options.createClient = async identity => {counts.connections++; return {baseUrl: identity.site, userId: 7};};
  assert.equal(await runChapterCommand(args, options), 0);
  site = 'https://another-school.test';
  assert.equal(await runChapterCommand(args, options), 0);
  assert.deepEqual(counts, {connections: 2, checks: 2});
});

test('credentials or site changing while connecting cannot start chapter downloads', async t => {
  const {options, outputs, counts} = fixture(t);
  let credentialKey = 'first';
  options.connectionIdentity = () => ({site: 'https://school.test', credentialKey});
  options.createClient = async () => {
    credentialKey = 'replaced-during-login';
    return {baseUrl: 'https://school.test', userId: 7};
  };
  assert.equal(await runChapterCommand(args, options), 1);
  assert.equal(counts.checks, 0);
  assert.equal(outputs.at(-1).ready_for_teaching, false);
});

test('DeepSeek main identity is supported but internal identity is never used as a new chat', () => {
  const id = '01234567-89ab-cdef-0123-456789abcdef';
  assert.equal(parseChapterArgs(args, {DSH_SESSION_ID: 'session-' + id}).sessionId, 'session-' + id);
  assert.throws(() => parseChapterArgs(args, {DSH_SESSION_ID: id}), error => error.code === 'CHAPTER_USAGE');
  assert.equal(parseChapterArgs([...args, '--chat', 'parent-chat'], {DSH_SESSION_ID: id}).sessionId, 'parent-chat');
});
