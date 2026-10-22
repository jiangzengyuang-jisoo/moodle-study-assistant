// Evidence collection only. An assistant reviews changes before promoting a baseline.
export function redactEvidence(value, token) {
  if (typeof value === 'string') return (token ? value.replaceAll(token, '[REDACTED]') : value)
    .replace(/([?&](?:amp;)?(?:token|wstoken|sesskey)=)[^&"'\s<>]+/gi, '$1[REDACTED]')
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

// Slots belong to individual API calls, not to whole pagination workflows.
// A forum awaiting its next page must not hold a slot needed by that request.
function boundedCalls(client, concurrency) {
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 4)
    throw new Error('Concurrency must be an integer from 1 to 4');
  const queue = [];
  let active = 0;
  const drain = () => {
    while (active < concurrency && queue.length) {
      const { method, args, resolve, reject } = queue.shift();
      active++;
      Promise.resolve().then(() => client.call(method, args)).then(resolve, reject).finally(() => {
        active--;
        drain();
      });
    }
  };
  return { call: (method, args) => new Promise((resolve, reject) => {
    queue.push({ method, args, resolve, reject });
    drain();
  }) };
}

export async function collectRefresh(client, state, { concurrency = 4 } = {}) {
  const report = { started_at: new Date().toISOString(), moodle_url: client.baseUrl, pages: [], forums: [], threads: [],
    submissions: [], attempts: [], errors: [], scope_limits: [], files_downloaded: 0, reviewed: false };
  const api = boundedCalls(client, concurrency);
  const safe = async (scope, work) => {
    try { return await work(); } catch (e) { report.errors.push({ scope, error: String(e?.message ?? e) }); return null; }
  };
  const pageMap = new Map(state.courses.flatMap(c => (c.moodle_course_ids ?? []).map(id => [id, c.id])));
  if (!pageMap.size || [...pageMap.keys()].some(id => !Number.isInteger(id) || id <= 0))
    throw new Error('Choose valid course IDs first');
  const courseArgs = Object.fromEntries([...pageMap.keys()].map((id, i) => [`courseids[${i}]`, id]));
  const assignmentWork = async () => {
    report.assignments = await safe('assignment_catalog', () => api.call('mod_assign_get_assignments', courseArgs));
    report.submissions = (await Promise.all((report.assignments?.courses ?? []).map(async c =>
      Promise.all(c.assignments.map(async a => {
        const status = await safe(`assignment:${a.id}`, async () => checkWarnings(await api.call('mod_assign_get_submission_status', { assignid: a.id })));
        return { assignment_id: a.id, courseid: c.id, cmid: a.cmid, status };
      }))))).flat();
  };
  const quizWork = async () => {
    report.quizzes = await safe('quiz_catalog', async () => checkWarnings(await api.call('mod_quiz_get_quizzes_by_courses', courseArgs)));
    // Required quizzes or existing tracked tasks; question banks are not automatically homework.
    const urls = new Set(state.tasks.map(t => t.source?.url).filter(Boolean));
    const required = (report.quizzes?.quizzes ?? []).filter(q => q.timeclose ||
      urls.has(`${client.baseUrl ?? process.env.MOODLE_URL}/mod/quiz/view.php?id=${q.coursemodule}`));
    report.attempts = await Promise.all(required.map(async q => {
      const data = await safe(`quiz:${q.id}`, async () => checkWarnings(await api.call('mod_quiz_get_user_attempts', {
        quizid: q.id, userid: client.userId, status: 'all', includepreviews: 0,
      })));
      return { quiz_id: q.id, courseid: q.course, cmid: q.coursemodule, data };
    }));
  };
  const pageWork = async () => {
    const pages = await Promise.all([...pageMap].map(async ([courseid, course_id]) => {
      const sections = await safe(`course:${courseid}`, () => api.call('core_course_get_contents', {
        courseid, 'options[0][name]': 'excludecontents', 'options[0][value]': 1,
      }));
      if (!sections) return null;
      const forums = await Promise.all(sections.flatMap(s => s.modules).filter(m => m.modname === 'forum').map(async module => {
        // Pagination remains ordered within a forum while other sources progress.
        const discussions = await safe(`forum:${module.instance}`, () => readDiscussions(api, module.instance));
        const threads = await Promise.all((discussions ?? []).map(async discussion => {
          const posts = await safe(`discussion:${discussion.discussion}`, async () => verifyPosts(discussion,
            await api.call('mod_forum_get_discussion_posts', { discussionid: discussion.discussion, sortby: 'created', sortdirection: 'ASC' })));
          return { course_id, forumid: module.instance, forum_url: module.url, discussion, posts };
        }));
        return { forum: { course_id, courseid, module, discussions }, threads };
      }));
      return { page: { course_id, courseid, sections }, forums };
    }));
    for (const result of pages.filter(Boolean)) {
      report.pages.push(result.page);
      for (const { forum, threads } of result.forums) {
        report.forums.push(forum);
        report.threads.push(...threads);
      }
    }
  };
  const calendarWork = async () => {
    const now = Math.floor(Date.now() / 1000);
    report.calendar = await safe('calendar', async () => {
      const data = checkWarnings(await api.call('core_calendar_get_action_events_by_timesort', {
        timesortfrom: now, timesortto: now + 31 * 86400, limitnum: 50,
      }));
      if (data.events?.length >= 50) throw new Error('Calendar may be truncated; pagination required');
      return data;
    });
  };
  await Promise.all([assignmentWork(), quizWork(), pageWork(), calendarWork()]);

  // Classify permission warnings after all current course views are available.
  const visibleModules = new Set(report.pages.flatMap(p => p.sections.flatMap(s => s.modules))
    .filter(m => m.uservisible !== false).map(m => m.id));
  for (const w of report.assignments?.warnings ?? []) {
    if (w.item === 'module' && w.warningcode === '1' && !visibleModules.has(Number(w.itemid))) {
      report.scope_limits.push({ scope: `module:${w.itemid}`, reason: 'not_accessible_in_current_student_course_view', api_warning: w });
    } else report.errors.push({ scope: 'assignment_catalog', error: JSON.stringify(w) });
  }
  // Completion timing must not change evidence order between identical runs.
  report.pages.sort((a, b) => a.courseid - b.courseid);
  report.forums.sort((a, b) => a.courseid - b.courseid || a.module.instance - b.module.instance);
  report.threads.sort((a, b) => a.forumid - b.forumid || a.discussion.discussion - b.discussion.discussion);
  report.submissions.sort((a, b) => a.courseid - b.courseid || a.assignment_id - b.assignment_id);
  report.attempts.sort((a, b) => a.courseid - b.courseid || a.quiz_id - b.quiz_id);
  report.errors.sort((a, b) => a.scope.localeCompare(b.scope) || a.error.localeCompare(b.error));
  report.scope_limits.sort((a, b) => a.scope.localeCompare(b.scope));
  report.finished_at = new Date().toISOString();
  report.collection_complete = report.errors.length === 0;
  return report;
}
