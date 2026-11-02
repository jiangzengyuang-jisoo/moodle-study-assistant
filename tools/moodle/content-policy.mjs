// Course downloads require a persistent, account-bound opt-in. Legacy course
// selection is not consent. This module has no network or filesystem effects.
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
