import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {
  assertSnapshotAccount, requireSnapshotAccount, snapshotAccountStatus, validatedSnapshotPath,
} from '../tools/moodle/snapshot-account.mjs';

const account = {site: 'https://school.example', user_id: 17};

function root(t) {
  const folder = mkdtempSync(join(tmpdir(), 'DeepSeek snapshot account '));
  t.after(() => rmSync(folder, {recursive: true, force: true}));
  return folder;
}

function snapshotFile(t, value) {
  const path = join(root(t), 'snapshot.json');
  writeFileSync(path, JSON.stringify(value), 'utf8');
  return path;
}

test('snapshot account status distinguishes match, another school and another account', () => {
  assert.equal(snapshotAccountStatus({account_scope: {site: 'https://school.example/', user_id: 17}}, account).status, 'match');
  const otherSchool = snapshotAccountStatus({account_scope: {...account, site: 'https://other.example'}}, account);
  assert.deepEqual([otherSchool.status, otherSchool.reason], ['mismatch', 'school']);
  const otherUser = snapshotAccountStatus({account_scope: {...account, user_id: 18}}, account);
  assert.deepEqual([otherUser.status, otherUser.reason], ['mismatch', 'account']);
  assert.equal(snapshotAccountStatus({moodle_url: account.site}, account).status, 'unverifiable');
  assert.equal(snapshotAccountStatus({}, account).status, 'unverifiable');
  assert.equal(snapshotAccountStatus(null, account).status, 'unverifiable');
});

test('a cached snapshot is reused only for the profile account; stale caches fall back', t => {
  const matching = snapshotFile(t, {account_scope: {...account}});
  assert.equal(validatedSnapshotPath(matching, account, '课程信息同步'), matching);
  // Old snapshots without an account stay unverifiable: fall back to a live read.
  assert.equal(validatedSnapshotPath(snapshotFile(t, {moodle_url: account.site}), account, '课程信息同步'), null);
  // A snapshot from another school/account is a mixed workspace: fail closed.
  assert.throws(() => validatedSnapshotPath(snapshotFile(t, {account_scope: {...account, site: 'https://other.example'}}),
    account, '课程信息同步'), /另一所学校/);
  assert.throws(() => validatedSnapshotPath(snapshotFile(t, {account_scope: {...account, user_id: 18}}),
    account, '课程信息同步'), /另一个账号/);
  assert.equal(validatedSnapshotPath(join(root(t), 'missing.json'), account, '课程信息同步'), null);
  const broken = join(root(t), 'broken.json');
  writeFileSync(broken, '{not json', 'utf8');
  assert.equal(validatedSnapshotPath(broken, account, '课程信息同步'), null);
});

test('an explicitly supplied snapshot must carry a verifiable account', t => {
  const matching = snapshotFile(t, {account_scope: {...account}});
  assert.equal(requireSnapshotAccount(matching, account, '课程资料同步'), matching);
  assert.throws(() => requireSnapshotAccount(snapshotFile(t, {moodle_url: account.site}), account,
    '课程资料同步'), /没有可核对的学校\/账号信息/);
  assert.throws(() => requireSnapshotAccount(snapshotFile(t, {account_scope: {...account, user_id: 18}}),
    account, '课程资料同步'), /另一个账号/);
  assert.equal(requireSnapshotAccount(null, account, '课程资料同步'), null);
  assert.throws(() => assertSnapshotAccount({account_scope: {...account, site: 'https://other.example'}},
    account, '完整初始化'), /另一所学校/);
});
