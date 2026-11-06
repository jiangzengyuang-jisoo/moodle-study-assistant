// Account checks for reused school snapshots: a snapshot supplies announcement,
// assignment and discussion text, so it must belong to this profile's school
// account. Numeric course/module IDs are only meaningful inside that account.
import {readFileSync} from 'node:fs';
import {normalizeSite} from './content-policy.mjs';

const readJSON = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));

export function snapshotAccountStatus(snapshot, account) {
  const scope = snapshot && typeof snapshot === 'object' && !Array.isArray(snapshot)
    ? snapshot.account_scope : null;
  let site = null;
  try {
    site = normalizeSite(typeof scope?.site === 'string' ? scope.site : snapshot?.moodle_url);
  } catch { site = null; }
  const userId = Number.isSafeInteger(scope?.user_id) && scope.user_id > 0 ? scope.user_id : null;
  if (!site) return {status: 'unverifiable', recorded: null, reason: 'school'};
  const recorded = {site, user_id: userId};
  if (site !== account.site) return {status: 'mismatch', recorded, reason: 'school'};
  if (userId === null) return {status: 'unverifiable', recorded, reason: 'account'};
  if (userId !== account.user_id) return {status: 'mismatch', recorded, reason: 'account'};
  return {status: 'match', recorded, reason: null};
}

export function assertSnapshotAccount(snapshot, account, action) {
  const status = snapshotAccountStatus(snapshot, account);
  if (status.status === 'match') return status;
  if (status.status === 'unverifiable') {
    throw new Error(`${action}使用的快照没有可核对的学校/账号信息（旧快照或被修改），已停止，未写入任何资料、账本或归档。请明确重新采集后使用本轮快照，不要用旧或备份快照代替本轮来源。`);
  }
  if (status.reason === 'school') {
    throw new Error(`${action}使用的快照来自另一所学校（${status.recorded.site}），当前学习档案是 ${account.site}；已停止，未写入任何资料、账本或归档。换学校请使用独立目录和独立档案。`);
  }
  throw new Error(`${action}使用的快照来自同一学校的另一个账号，当前账号不同；已停止，未写入任何资料、账本或归档。请恢复原账号，或使用独立目录和独立档案。`);
}

// Auto-selected cached snapshots: an unverifiable snapshot is skipped in favour
// of a live read; a school/account mismatch is a mixed workspace and fails closed.
export function validatedSnapshotPath(snapshotPath, account, action) {
  if (!snapshotPath) return null;
  let snapshot;
  try { snapshot = readJSON(snapshotPath); } catch { return null; }
  const status = snapshotAccountStatus(snapshot, account);
  if (status.status === 'match') return snapshotPath;
  if (status.status === 'unverifiable') {
    console.log(`${action}：暂存的完整快照缺少可核对的学校/账号信息，本次不使用它作为文字来源；改用只读 API 现场读取。`);
    return null;
  }
  return assertSnapshotAccount(snapshot, account, action) && snapshotPath;
}

// An explicitly supplied snapshot is never reused unless its source account is
// verifiable; the caller asked for this exact file, so skip-and-fallback is not
// an option here.
export function requireSnapshotAccount(snapshotPath, account, action) {
  if (!snapshotPath) return null;
  assertSnapshotAccount(readJSON(snapshotPath), account, action);
  return snapshotPath;
}
