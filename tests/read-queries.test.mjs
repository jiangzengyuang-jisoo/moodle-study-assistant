import test from 'node:test';
import assert from 'node:assert/strict';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {InMemoryTransport} from '@modelcontextprotocol/sdk/inMemory.js';
import {readCalendar} from '../tools/moodle/read-queries.mjs';
import {ReadOnlyClient} from '../tools/moodle/core.mjs';
import {serve} from '../tools/moodle/server.mjs';

test('calendar fallback filters after pagination, retaining later target-course events', async () => {
  const requests = [];
  const client = {supports: api => api.endsWith('by_timesort'), call: async (api, args) => {
    requests.push({...args});
    assert.equal(api, 'core_calendar_get_action_events_by_timesort');
    if (!args.aftereventid) return {events: [{id: 8, course: {id: 2}}, {id: 3, course: {id: 2}}], lastid: 3};
    assert.equal(args.aftereventid, 3); // event IDs need not increase in time order.
    return {events: [{id: 4, course: {id: 7}}], lastid: 4};
  }};
  const result = await readCalendar(client, {courseId: 7, timeFrom: 10, timeTo: 50, pageSize: 2});
  assert.equal(result.complete, true);
  assert.equal(result.scanned, 3);
  assert.deepEqual(result.events.map(e => e.id), [4]);
  assert(requests.every(r => r.timesortfrom === 10 && r.timesortto === 50));
});

test('calendar uses scoped API when available and does not silently accept stalled or bounded pages', async () => {
  let calls = 0;
  const client = {supports: () => true, call: async (api, args) => {
    calls++;
    assert.equal(api, 'core_calendar_get_action_events_by_course');
    assert.equal(args.courseid, 7);
    return {events: [{id: 5, courseid: 7}], lastid: 5};
  }};
  const stalled = await readCalendar(client, {courseId: 7, pageSize: 1});
  assert.equal(calls, 2);
  assert.equal(stalled.complete, false);
  assert.equal(stalled.reason, 'pagination_not_advancing');
  assert.equal(stalled.events.length, 1);
  const capped = await readCalendar(client, {courseId: 7, pageSize: 1, maxPages: 1});
  assert.equal(capped.complete, false);
  assert.equal(capped.reason, 'page_limit');
});

test('calendar reports unsupported API, warnings and malformed responses honestly', async () => {
  for (const pageSize of [0, 51, 100, 1.5])
    await assert.rejects(readCalendar({supports: () => assert.fail('invalid size reached API')}, {pageSize}));
  const unsupported = await readCalendar({supports: () => false, call: () => assert.fail('no API call')});
  assert.equal(unsupported.reason, 'api_not_enabled');
  assert.equal(unsupported.complete, false);
  await assert.rejects(readCalendar({supports: () => true, call: async () => ({events: [], warnings: [{message: 'denied'}]})}), /warnings/);
  await assert.rejects(readCalendar({supports: () => true, call: async () => ({})}), /missing events/);
  await assert.rejects(readCalendar({supports: () => true}, {timeFrom: 20, timeTo: 10}), /timeTo/);
});

test('new MCP queries run through the read-only client and never mark notifications or download', async () => {
  const calls = [];
  const api = new ReadOnlyClient('https://school.example', 'fake-token', async (_url, options) => {
    assert.equal(options.redirect, 'error');
    const args = Object.fromEntries(options.body);
    const method = args.wsfunction;
    calls.push({method, args});
    let data;
    if (method === 'gradereport_user_get_grade_items') {
      assert.equal(args.userid, '7');
      data = {usergrades: [{userid: 7, gradeitems: [{feedback: 'complete feedback'}]}]};
    } else if (method === 'message_popup_get_popup_notifications') {
      assert.equal(args.useridto, '7');
      assert.equal(args.newestfirst, '1');
      assert.equal(args.limit, '3');
      data = {notifications: args.offset === '0' ? [{id: 9}, {id: 8}, {id: 7}] : [{id: 7}], unreadcount: 3};
    } else if (method === 'core_course_get_contents') {
      data = [{id: 1, name: 'Week 1', modules: [{id: 2, modname: 'resource', name: 'Slides',
        contents: [{filename: 'slides.pdf', fileurl: 'https://school.example/file?wstoken=fake-token'}]},
        {id: 3, modname: 'resource', uservisible: false, name: 'Hidden'}]}];
    } else if (method === 'core_calendar_get_action_events_by_course') {
      data = {events: [{id: 10, course: {id: 12}}], lastid: 10};
    } else if (method === 'core_webservice_get_site_info') {
      data = {sitename: 'School', userid: 7, userprivateaccesskey: 'private-do-not-return', functions: []};
    } else assert.fail(`Unexpected API: ${method}`);
    return {ok: true, json: async () => data};
  });
  api.userId = 7;
  api.supportedFunctions = new Set(['gradereport_user_get_grade_items', 'message_popup_get_popup_notifications',
    'core_calendar_get_action_events_by_course']);
  const [a, b] = InMemoryTransport.createLinkedPair();
  const server = await serve(api, a);
  const client = new Client({name: 'read-query-test', version: '1.0.0'});
  try {
    await client.connect(b);
    const call = async (name, args = {}) => {
      const response = await client.callTool({name, arguments: args});
      assert(!response.isError, response.content?.[0]?.text);
      return JSON.parse(response.content[0].text);
    };
    const grades = await call('moodle_get_grades', {courseId: 12});
    assert.equal(grades.report.usergrades[0].gradeitems[0].feedback, 'complete feedback');
    const first = await call('moodle_get_notifications', {limit: 2});
    assert.equal(first.has_more, true);
    assert.equal(first.complete, false);
    assert.equal(first.next_offset, 2);
    const second = await call('moodle_get_notifications', {limit: 2, offset: first.next_offset});
    assert.equal(second.has_more, false);
    assert.equal(second.complete, false); // this is a later page, not a full account history.
    const resources = await call('moodle_list_resources', {courseId: 12});
    assert.equal(resources.sections[0].modules.length, 1);
    assert(!JSON.stringify(resources).includes('fake-token'));
    assert.equal((await call('moodle_get_calendar_events', {courseId: 12})).complete, true);
    assert(!JSON.stringify(await call('moodle_get_site_info')).includes('private-do-not-return'));
    const before = calls.length;
    for (const method of ['core_message_mark_notification_read', 'mod_assign_save_submission'])
      await assert.rejects(api.call(method), /read-only/);
    await assert.rejects(api.downloadFile('https://school.example/file'), /disabled/);
    await assert.rejects(api.call('gradereport_user_get_grade_items', {wstoken: 'override'}), /Reserved/);
    assert.equal(calls.length, before);
  } finally {await client.close(); await server.close();}
});
