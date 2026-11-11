// Course downloads require a persistent, account-bound opt-in. Legacy course
// selection is not consent. This module performs no network effects and writes
// no files; the profile-identity helpers only read local account evidence.
//
// One Study profile keeps one school account. Numeric Moodle course/module IDs
// are only meaningful inside the account that created them: a different school
// (or another student account at the same school) can reuse the same numbers for
// unrelated courses. Everything that maps or downloads against those numbers
// therefore has to fail closed when the profile is already bound elsewhere,
// instead of silently re-binding old selection, grants, ledger or baselines.
import {existsSync, readFileSync} from 'node:fs';
import {join, resolve} from 'node:path';

export function normalizeSite(site) {
  const value = new URL(site);
  if (value.protocol !== 'https:' || value.username || value.password || value.search || value.hash) {
    throw new Error('学校地址必须是没有凭据、查询参数或片段的 HTTPS 地址');
  }
  return value.href.replace(/\/+$/, '');
}

const validId = value => Number.isSafeInteger(value) && value > 0;
const validStamp = value => typeof value === 'string' && Number.isFinite(Date.parse(value));

export function selectedCoursePages(state) {
  const result = new Map();
  for (const course of state?.courses ?? []) {
    if (typeof course?.id !== 'string' || !course.id) continue;
    const ids = Array.isArray(course.moodle_course_ids) ? course.moodle_course_ids
      : (course.moodle_progress?.pages ?? []).map(page => page?.course_id);
    for (const id of ids) if (validId(id)) {
      // Ambiguous ownership cannot safely choose an archive destination.
      if (result.has(id) && result.get(id) !== course.id) result.set(id, null);
      else if (!result.has(id)) result.set(id, course.id);
    }
  }
  return new Map([...result].filter(([, owner]) => owner !== null));
}

function matchesScope(grant, site, userId) {
  if (!grant || grant.user_id !== userId) return false;
  try { return normalizeSite(grant.site) === site; } catch { return false; }
}

export function authorizedCoursePages(state, {site, userId}) {
  const result = new Map();
  if (!validId(userId)) return result;
  let normalized;
  try { normalized = normalizeSite(site); } catch { return result; }
  const policy = state?.settings?.course_content_sync;
  if (policy?.version !== 1 || !Array.isArray(policy.grants)) return result;
  for (const [id, owner] of selectedCoursePages(state)) {
    const grants = policy.grants.filter(grant => matchesScope(grant, normalized, userId)
      && grant.moodle_course_id === id);
    if (grants.length === 1 && grants[0].mode === 'full' && validStamp(grants[0].authorized_at)) result.set(id, owner);
  }
  return result;
}

export function grantCourseContent(state, {site, userId, courseIds, mode, authorizedAt = new Date().toISOString()}) {
  const normalized = normalizeSite(site);
  const selected = selectedCoursePages(state);
  if (!validId(userId) || !Array.isArray(courseIds) || !courseIds.length
      || courseIds.some(id => !validId(id) || !selected.has(id))
      || !['full', 'metadata'].includes(mode) || !validStamp(authorizedAt)) {
    throw new Error('课程授权无效：请明确选择当前账户已跟踪的课程及同步方式');
  }
  const next = structuredClone(state);
  const previous = next.settings?.course_content_sync;
  if (previous && (previous.version !== 1 || !Array.isArray(previous.grants))) {
    throw new Error('已有课程同步授权格式无法识别；原记录保留，请先核对');
  }
  const ids = [...new Set(courseIds)];
  const retained = (previous?.grants ?? []).filter(grant => !(matchesScope(grant, normalized, userId)
    && ids.includes(grant.moodle_course_id)));
  next.settings ??= {};
  next.settings.course_content_sync = {version: 1, grants: [...retained, ...ids.map(id => ({
    site: normalized, user_id: userId, moodle_course_id: id, mode, authorized_at: authorizedAt,
  }))]};
  return next;
}

export function contentModeFromAnswer(answer) {
  const value = String(answer).trim();
  if (!value) return null;
  if (value === '1' || value === 'full') return 'full';
  if (value === '2' || value === 'metadata') return 'metadata';
  throw new Error('同步方式无效；输入 1 完整初始化、2 仅同步信息，或回车取消');
}

// ---------------------------------------------------------------------------
// One profile, one school account.
// ---------------------------------------------------------------------------

export const PROFILE_ACCOUNT_SCHEMA = 1;

// Normalize a school/account pair; null when it is not usable evidence.
export function accountScope(site, userId) {
  if (!validId(userId)) return null;
  try { return {site: normalizeSite(site), user_id: userId}; } catch { return null; }
}

function recordedAccount(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return accountScope(value.site, value.user_id);
}

function accountKey(account) {
  return account.user_id === null ? account.site : `${account.site}|${account.user_id}`;
}

function uniqueAccounts(accounts) {
  const seen = new Set();
  const result = [];
  for (const account of accounts) {
    const key = accountKey(account);
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(account);
  }
  return result;
}

// Account evidence stored inside the learning state. Grants written by the
// deployed chat/library flow carry `account_scope`; very old CLI grants may have
// no account information at all, and are deliberately not treated as evidence.
function recordedStateAccounts(state) {
  const accounts = [];
  const invalidEvidence = [];
  const add = (value, source) => {
    if (value === undefined || value === null) return;
    const account = recordedAccount(value);
    if (account) accounts.push({...account, source});
    else invalidEvidence.push(source);
  };
  add(state?.settings?.profile_account_scope, 'profile_account_scope');
  const grants = state?.settings?.course_library_authorization?.courses;
  if (grants && typeof grants === 'object' && !Array.isArray(grants)) {
    for (const grant of Object.values(grants)) {
      if (grant && typeof grant === 'object' && !Array.isArray(grant) && Object.hasOwn(grant, 'account_scope')) {
        add(grant.account_scope, 'course_library_authorization');
      }
    }
  }
  const policy = state?.settings?.course_content_sync;
  if (policy && policy.version === 1 && Array.isArray(policy.grants)) {
    for (const grant of policy.grants) {
      if (grant && typeof grant === 'object' && !Array.isArray(grant)
          && (Object.hasOwn(grant, 'site') || Object.hasOwn(grant, 'user_id'))) {
        add({site: grant.site, user_id: grant.user_id}, 'course_content_sync');
      }
    }
  }
  return {accounts, invalidEvidence};
}

function readJSONFile(path) {
  try {
    if (!existsSync(path)) return null;
    return JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
  } catch { return null; }
}

function recordedSite(value) {
  try { return normalizeSite(value); } catch { return null; }
}

// A course-page URL from an older sync report tells the school it came from.
function siteFromPageURL(value) {
  if (typeof value !== 'string' || !value) return null;
  try {
    const url = new URL(value);
    const marker = '/course/view.php';
    const index = url.pathname.indexOf(marker);
    if (index < 0) return null;
    const base = new URL(url.href);
    base.pathname = url.pathname.slice(0, index) || '/';
    base.search = '';
    base.hash = '';
    return recordedSite(base.href);
  } catch { return null; }
}

// Read-only local evidence left by previous managed collections or library
// reports. Older snapshots may record the school without a user ID.
export function recordedDiskAccounts(root) {
  const accounts = [];
  if (!root) return accounts;
  const system = resolve(root, 'Study', '_System');
  for (const name of ['refresh-latest.json', 'refresh-last-complete.json']) {
    const snapshot = readJSONFile(join(system, name));
    if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)) continue;
    const account = recordedAccount(snapshot.account_scope);
    if (account) { accounts.push({...account, source: name}); continue; }
    const site = recordedSite(snapshot.moodle_url);
    if (site) accounts.push({site, user_id: null, source: name});
  }
  const report = readJSONFile(join(system, 'LibrarySync', 'latest.json'));
  if (report && typeof report === 'object' && !Array.isArray(report)) {
    // Newer reports record the school explicitly. Prefer that over deriving an
    // identity from a displayed page URL, which can lose a deployment prefix.
    const explicit = recordedAccount(report.school);
    if (explicit) accounts.push({...explicit, source: 'LibrarySync/latest.json'});
    else {
      for (const course of report.courses ?? []) {
        const site = siteFromPageURL(course?.page_url);
        if (site) accounts.push({site, user_id: null, source: 'LibrarySync/latest.json'});
      }
    }
  }
  return accounts;
}

// Whether the profile already holds anything tied to school numeric IDs.
export function hasBoundSchoolData(state) {
  if (!state || typeof state !== 'object' || Array.isArray(state)) return false;
  const settings = state.settings && typeof state.settings === 'object' ? state.settings : {};
  if (settings.profile_account_scope) return true;
  const library = settings.course_library_authorization;
  if (library && typeof library === 'object' && library.courses
      && typeof library.courses === 'object' && Object.keys(library.courses).length) return true;
  const policy = settings.course_content_sync;
  if (policy && typeof policy === 'object' && Array.isArray(policy.grants) && policy.grants.length) return true;
  if ((state.courses ?? []).some(course => (Array.isArray(course?.moodle_course_ids) ? course.moodle_course_ids : []).some(validId)
      || (course?.moodle_progress?.pages ?? []).length)) return true;
  if ((state.materials ?? []).some(material => (material?.moodle_file_sources ?? []).length)) return true;
  if (state.refresh_receipts && typeof state.refresh_receipts === 'object'
      && Object.keys(state.refresh_receipts).length) return true;
  if ((state.tasks ?? []).some(task => typeof task?.source?.url === 'string' && task.source.url)) return true;
  return false;
}

// Classify the profile's recorded identity without guessing or writing.
// - bound: one school (and possibly one known user) is recorded
// - conflict: the state or local evidence already mixes accounts/schools
// - unknown: school data exists but no account information can be verified
// - fresh: nothing recorded yet, safe for a first account
export function profileAccountStatus(state, {root = null} = {}) {
  const stateEvidence = recordedStateAccounts(state);
  const accounts = uniqueAccounts([...stateEvidence.accounts, ...recordedDiskAccounts(root)]);
  const sites = [...new Set(accounts.map(account => account.site))];
  const knownUsers = accounts.filter(account => account.user_id !== null);
  const identified = uniqueAccounts(knownUsers);
  const result = {
    accounts, sites, invalidEvidence: stateEvidence.invalidEvidence,
    site: sites.length === 1 ? sites[0] : null,
    user_id: identified.length === 1 ? identified[0].user_id : null,
  };
  if (sites.length > 1 || identified.length > 1) return {...result, status: 'conflict'};
  if (sites.length === 1) return {...result, status: 'bound'};
  if (hasBoundSchoolData(state)) return {...result, status: 'unknown'};
  return {...result, status: 'fresh'};
}

function describeSites(sites) {
  return sites.join('、');
}

// Guard every operation that maps or downloads against school numeric IDs.
// Fails closed before the caller writes state, downloads, or writes a receipt.
export function assertProfileAccount({state, root = null, site, userId = null, action = '此操作'}) {
  let normalized;
  try { normalized = normalizeSite(site); } catch { throw new Error('缺少有效的学校 HTTPS 地址，不能继续' + action); }
  const currentUser = validId(userId) ? userId : null;
  const status = profileAccountStatus(state, {root});
  if (status.status === 'conflict') {
    throw new Error(`学习档案中已存在来自不同学校或账号的记录（${describeSites(status.sites)}），无法安全确定数字课程/模块 ID 属于谁；已停止${action}，未写入任何记录。请恢复备份，或在新目录使用独立档案；不会自动重绑旧授权、资料账本或章节核验基线。`);
  }
  if (status.status === 'unknown') {
    throw new Error(`学习档案已有课程、资料或授权记录，但无法确认属于哪所学校、哪个账号；已停止${action}，未写入任何记录。为避免猜测或补签旧授权，请恢复能证明原学校/账号的备份后继续，或复制/新建独立项目目录并用独立档案重新 setup；不会猜测或补签旧授权。`);
  }
  if (status.status === 'bound') {
    if (status.site !== normalized) {
      throw new Error(`同一学习档案此前用于另一所学校（${status.site}），当前学校是 ${normalized}；已停止${action}，未写入任何记录。数字课程/模块 ID 在不同学校可能指向不同课程，继续会污染选课、旧授权、资料库账本和章节核验基线。换学校请复制/新建独立项目目录并使用独立学习档案，重新 setup 和登录；不要在同一 Study 档案来回切换。`);
    }
    if (currentUser !== null && status.user_id !== null && status.user_id !== currentUser) {
      throw new Error(`同一学习档案此前使用的是同一学校的另一个账号（${status.site}），当前登录账号不同；已停止${action}，未写入任何记录。不同账号的课程与资料范围可能不同，继续会把数字课程/模块 ID 关联到旧记录。请恢复原账号，或为当前账号使用独立目录和独立档案。`);
    }
  }
  return {status: status.status, account: {site: normalized, user_id: currentUser}};
}

// Record the confirmed identity together with an explicit selection/authorization.
export function withProfileAccount(state, {site, userId, boundAt = new Date().toISOString()}) {
  const account = accountScope(site, userId);
  if (!account) throw new Error('缺少有效学校或账号，不能绑定学习档案');
  const next = structuredClone(state);
  next.settings ??= {};
  const previous = recordedAccount(next.settings.profile_account_scope);
  if (previous && (previous.site !== account.site || previous.user_id !== account.user_id)) {
    throw new Error('学习档案已绑定其他学校或账号，拒绝覆盖；换学校或账号请使用独立目录和独立档案');
  }
  const stamp = previous && typeof next.settings.profile_account_scope?.bound_at === 'string'
    ? next.settings.profile_account_scope.bound_at : boundAt;
  next.settings.profile_account_scope = {schema: PROFILE_ACCOUNT_SCHEMA, ...account, bound_at: stamp};
  return next;
}
