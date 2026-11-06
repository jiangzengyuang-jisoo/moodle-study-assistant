import {z} from 'zod';
import {checkWarnings} from './refresh.mjs';

const positive = z.number().int().positive();
const output = value => ({content: [{type: 'text', text: JSON.stringify(value)}]});
const unsupported = api => ({complete: false, reason: 'api_not_enabled', api});
const calendarParameters = {courseId: positive.optional(), daysAhead: positive.max(366).optional(),
  timeFrom: z.number().int().nonnegative().optional(), timeTo: z.number().int().nonnegative().optional(),
  pageSize: positive.max(50).optional(), maxPages: positive.max(100).optional()};

// Moodle's calendar API pages with aftereventid, not a numeric offset.
// Keep one time window for the entire request, including fallback course filtering.
export async function readCalendar(client, options = {}) {
  const {courseId, daysAhead = 30, timeFrom, timeTo, pageSize = 50, maxPages = 20} =
    z.object(calendarParameters).parse(options);
  const from = timeFrom ?? Math.floor(Date.now() / 1000);
  const to = timeTo ?? from + daysAhead * 86400;
  if (to < from) throw new Error('timeTo must be at or after timeFrom');
  const scoped = courseId && client.supports('core_calendar_get_action_events_by_course');
  const api = scoped ? 'core_calendar_get_action_events_by_course' : 'core_calendar_get_action_events_by_timesort';
  if (!client.supports(api)) return unsupported(api);
  const events = [], seen = new Set(), cursors = new Set([0]);
  const scope = {course_id: courseId ?? null, time_from: from, time_to: to, api};
  let aftereventid = 0, scanned = 0;
  for (let page = 0; page < maxPages; page++) {
    const data = checkWarnings(await client.call(api, {timesortfrom: from, timesortto: to,
      aftereventid, limitnum: pageSize, ...(scoped ? {courseid: courseId} : {})}));
    if (!Array.isArray(data.events)) throw new Error('Calendar response is missing events');
    for (const event of data.events) {
      if (!Number.isInteger(event.id) || event.id <= 0) throw new Error('Calendar event is missing its identity');
      if (seen.has(event.id)) continue;
      seen.add(event.id);
      scanned++;
      if (!courseId || Number(event.course?.id ?? event.courseid) === courseId) events.push(event);
    }
    if (data.events.length < pageSize) return {events, complete: true, scope, pages: page + 1, scanned};
    const cursor = data.lastid ?? data.events.at(-1)?.id;
    if (!Number.isInteger(cursor) || cursor <= 0 || cursors.has(cursor))
      return {events, complete: false, reason: 'pagination_not_advancing', scope, pages: page + 1, scanned};
    cursors.add(cursor);
    aftereventid = cursor;
  }
  return {events, complete: false, reason: 'page_limit', scope, pages: maxPages, scanned,
    guidance: 'Query a smaller timeFrom/timeTo range; do not treat this result as all upcoming events.'};
}

export function registerReadQueries(server, client) {
  server.tool('moodle_get_calendar_events',
    'Read upcoming calendar action events, optionally for one course. Paginates within a fixed time range; complete describes API coverage, not human review or all types of school events.',
    calendarParameters,
    async args => output(await readCalendar(client, args)));

  server.tool('moodle_get_grades', 'Read the current account grade report for one course, including feedback.',
    {courseId: positive}, async ({courseId}) => {
      const api = 'gradereport_user_get_grade_items';
      if (!client.supports(api)) return output(unsupported(api));
      const report = checkWarnings(await client.call(api, {courseid: courseId, userid: client.userId}));
      if (!Array.isArray(report.usergrades)) throw new Error('Grade response is missing usergrades');
      return output({complete: true, course_id: courseId, report});
    });

  server.tool('moodle_get_notifications', 'Read recent account notifications without marking them read. Use next_offset while has_more is true.',
    {limit: positive.max(100).optional(), offset: z.number().int().nonnegative().optional()},
    async ({limit = 20, offset = 0}) => {
      const api = 'message_popup_get_popup_notifications';
      if (!client.supports(api)) return output(unsupported(api));
      const data = checkWarnings(await client.call(api, {useridto: client.userId, newestfirst: 1,
        limit: limit + 1, offset}));
      if (!Array.isArray(data.notifications)) throw new Error('Notification response is missing notifications');
      const more = data.notifications.length > limit;
      return output({notifications: data.notifications.slice(0, limit), unread_count: data.unreadcount ?? null,
        offset, has_more: more, next_offset: more ? offset + limit : null,
        complete: offset === 0 && !more});
    });

  server.tool('moodle_list_resources', 'List accessible course files and links without downloading. Content acquisition uses the separate course authorization workflow.',
    {courseId: positive}, async ({courseId}) => {
      const sections = checkWarnings(await client.call('core_course_get_contents', {courseid: courseId}));
      if (!Array.isArray(sections)) throw new Error('Course response is missing sections');
      return output({course_id: courseId, scope: 'accessible_course_resource_modules', complete: true,
        sections: sections.map(section => ({id: section.id, name: section.name,
          modules: (section.modules ?? []).filter(module =>
            module.uservisible !== false && ['resource', 'folder', 'url'].includes(module.modname))
            .map(module => ({id: module.id, name: module.name, modname: module.modname,
              url: module.url, contents: module.contents ?? []}))})).filter(section => section.modules.length)});
    });

  server.tool('moodle_get_site_info', 'Read the current school, account identity and enabled API information.',
    {}, async () => {
      const info = checkWarnings(await client.call('core_webservice_get_site_info'));
      return output({sitename: info.sitename, siteurl: info.siteurl, userid: info.userid,
        fullname: info.fullname, username: info.username, release: info.release,
        functions: (info.functions ?? []).map(entry => entry.name)});
    });
}
