// Evidence collection only. An assistant reviews changes before promoting a baseline.
export function redactEvidence(value, token) {
  if (typeof value === 'string') return (token ? value.replaceAll(token, '[REDACTED]') : value)
    .replace(/([?&](?:amp;)?(?:token|wstoken)=)[^"'\s<>]+/gi, '$1[REDACTED]')
    .replace(/((?:Password|Passcode)\s*:?\s*(?:<[^>]*>\s*)*)[\w-]+/gi, '$1[meeting access code omitted]');
  if (Array.isArray(value)) return value.map(v => redactEvidence(v, token));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, redactEvidence(v, token)]));
  return value;
}

export function checkWarnings(data) {
  if (data?.warnings?.length) throw new Error('API warnings: ' + JSON.stringify(data.warnings));
  return data;
}

export async function readDiscussions(client, forumid, perpage = 100) {
  const result = [], seen = new Set();
  for (let page = 0; page < 1000; page++) {
    const data = checkWarnings(await client.call('mod_forum_get_forum_discussions', { forumid, page, perpage }));
    if (!Array.isArray(data.discussions)) throw new Error('Missing discussion list');
    for (const d of data.discussions) {
      if (!d.discussion || seen.has(d.discussion)) throw new Error('Repeated or missing discussion ID');
      seen.add(d.discussion); result.push(d);
    }
    if (data.discussions.length < perpage) return result;
  }
  throw new Error('Pagination limit reached');
}

export function verifyPosts(discussion, data) {
  checkWarnings(data);
  if (!Array.isArray(data.posts)) throw new Error('Missing post list');
  if (new Set(data.posts.map(p => p.id)).size !== data.posts.length) throw new Error('Duplicate post ID');
  if (data.posts.length !== Number(discussion.numreplies) + 1) throw new Error('Post count differs from discussion listing');
  return data.posts;
}

export async function collectRefresh(client, state) {
  const report = { started_at: new Date().toISOString(), pages: [], forums: [], threads: [],
    submissions: [], attempts: [], errors: [], scope_limits: [], files_downloaded: 0, reviewed: false };
  const safe = async (scope, work) => {
    try { return await work(); } catch (e) { report.errors.push({ scope, error: e.message }); return null; }
  };
  const pageMap = new Map(state.courses.flatMap(c => (c.moodle_course_ids ?? []).map(id => [id, c.id])));
  if (!pageMap.size || [...pageMap.keys()].some(id => !Number.isInteger(id) || id <= 0))
    throw new Error('Choose valid course IDs first');
  const courseArgs = Object.fromEntries([...pageMap.keys()].map((id, i) => [`courseids[${i}]`, id]));
  report.assignments = await safe('assignment_catalog', () => client.call('mod_assign_get_assignments', courseArgs));
  report.quizzes = await safe('quiz_catalog', async () => checkWarnings(await client.call('mod_quiz_get_quizzes_by_courses', courseArgs)));
  for (const [courseid, course_id] of pageMap) {
    const sections = await safe(`course:${courseid}`, () => client.call('core_course_get_contents', {
      courseid, 'options[0][name]': 'excludecontents', 'options[0][value]': 1,
    }));
    if (!sections) continue;
    report.pages.push({ course_id, courseid, sections });
    for (const module of sections.flatMap(s => s.modules).filter(m => m.modname === 'forum')) {
      const discussions = await safe(`forum:${module.instance}`, () => readDiscussions(client, module.instance));
      report.forums.push({ course_id, courseid, module, discussions });
    }
  }
  const visibleModules = new Set(report.pages.flatMap(p => p.sections.flatMap(s => s.modules))
    .filter(m => m.uservisible !== false).map(m => m.id));
  for (const w of report.assignments?.warnings ?? []) {
    if (w.item === 'module' && w.warningcode === '1' && !visibleModules.has(Number(w.itemid))) {
      report.scope_limits.push({ scope: `module:${w.itemid}`, reason: 'not_accessible_in_current_student_course_view', api_warning: w });
    } else report.errors.push({ scope: 'assignment_catalog', error: JSON.stringify(w) });
  }
  const threads = report.forums.flatMap(f => (f.discussions ?? []).map(d => ({ course_id: f.course_id,
    forumid: f.module.instance, forum_url: f.module.url, discussion: d })));
  let cursor = 0;
  await Promise.all(Array.from({ length: 3 }, async () => {
    while (cursor < threads.length) {
      const item = threads[cursor++];
      const posts = await safe(`discussion:${item.discussion.discussion}`, async () => verifyPosts(item.discussion,
        await client.call('mod_forum_get_discussion_posts', { discussionid: item.discussion.discussion, sortby: 'created', sortdirection: 'ASC' })));
      report.threads.push({ ...item, posts });
    }
  }));
  for (const c of report.assignments?.courses ?? []) {
    for (const a of c.assignments) {
      const status = await safe(`assignment:${a.id}`, async () => checkWarnings(await client.call('mod_assign_get_submission_status', { assignid: a.id })));
      report.submissions.push({ assignment_id: a.id, courseid: c.id, cmid: a.cmid, status });
    }
  }
  // Required quizzes or existing tracked tasks; question banks are not automatically homework.
  const urls = new Set(state.tasks.map(t => t.source?.url).filter(Boolean));
  for (const q of report.quizzes?.quizzes ?? []) {
    if (!q.timeclose && !urls.has(`${client.baseUrl ?? process.env.MOODLE_URL}/mod/quiz/view.php?id=${q.coursemodule}`)) continue;
    const data = await safe(`quiz:${q.id}`, async () => checkWarnings(await client.call('mod_quiz_get_user_attempts', {
      quizid: q.id, userid: client.userId, status: 'all', includepreviews: 0,
    })));
    report.attempts.push({ quiz_id: q.id, courseid: q.course, cmid: q.coursemodule, data });
  }
  const now = Math.floor(Date.now() / 1000);
  report.calendar = await safe('calendar', async () => {
    const data = checkWarnings(await client.call('core_calendar_get_action_events_by_timesort', {
      timesortfrom: now, timesortto: now + 31 * 86400, limitnum: 50,
    }));
    if (data.events?.length >= 50) throw new Error('Calendar may be truncated; pagination required');
    return data;
  });
  report.finished_at = new Date().toISOString();
  report.collection_complete = report.errors.length === 0;
  return report;
}
