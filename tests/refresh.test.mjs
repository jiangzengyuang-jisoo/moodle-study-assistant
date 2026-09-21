import test from 'node:test';
import assert from 'node:assert/strict';

test('forum pagination collects every page, without unsupported arguments', async () => {
  const { readDiscussions } = await import('../tools/moodle/refresh.mjs');
  const pages = [[{ discussion: 11 }, { discussion: 12 }], [{ discussion: 13 }]];
  const client = { call: async (name, args) => {
    assert.equal(name, 'mod_forum_get_forum_discussions');
    assert.equal(args.sortby, undefined);
    return { discussions: pages[args.page], warnings: [] };
  } };
  assert.deepEqual((await readDiscussions(client, 1, 2)).map(d => d.discussion), [11, 12, 13]);
});

test('a repeated page or API warning cannot be treated as complete', async () => {
  const { readDiscussions } = await import('../tools/moodle/refresh.mjs');
  await assert.rejects(readDiscussions({ call: async () => ({ discussions: [{ discussion: 11 }] }) }, 1, 1), /repeat/i);
  await assert.rejects(readDiscussions({ call: async () => ({ discussions: [], warnings: [{ warningcode: 'denied' }] }) }, 1), /warning/i);
});

test('post count gaps fail verification even when the request succeeded', async () => {
  const { verifyPosts } = await import('../tools/moodle/refresh.mjs');
  assert.throws(() => verifyPosts({ numreplies: 2 }, { posts: [{ id: 1 }, { id: 2 }] }), /count/i);
  assert.throws(() => verifyPosts({ numreplies: 1 }, { posts: [{ id: 1 }, { id: 1 }] }), /duplicate/i);
  assert.equal(verifyPosts({ numreplies: 1 }, { posts: [{ id: 1 }, { id: 2 }], warnings: [] }).length, 2);
});

test('evidence removes credentials in URLs and meeting instructions', async () => {
  const { redactEvidence } = await import('../tools/moodle/refresh.mjs');
  const out = redactEvidence({ message: 'Password: <b>123456</b>', url: 'https://example.test/file?token=other-secret', raw: 'local-secret' }, 'local-secret');
  assert.equal(JSON.stringify(out).includes('123456'), false);
  assert.equal(JSON.stringify(out).includes('other-secret'), false);
  assert.equal(JSON.stringify(out).includes('local-secret'), false);
});

test('full collection preserves accessible work and records hidden future modules separately', async () => {
  const { collectRefresh } = await import('../tools/moodle/refresh.mjs');
  const calls = [];
  const client = { userId: 5, baseUrl: 'https://example.test', call: async (method, args) => {
    calls.push(method);
    if (method === 'mod_assign_get_assignments') return { courses: [{ id: 1, assignments: [{ id: 10, cmid: 11 }] }], warnings: [{ item: 'module', itemid: 99, warningcode: '1' }] };
    if (method === 'mod_quiz_get_quizzes_by_courses') return { quizzes: [{ id: 20, course: 1, coursemodule: 21, timeclose: 1 }] };
    if (method === 'core_course_get_contents') return [{ modules: args.courseid === 1 ? [{ id: 11, modname: 'assign', uservisible: true }] : [] }];
    if (method === 'mod_assign_get_submission_status') return { lastattempt: { submission: { status: 'submitted' } } };
    if (method === 'mod_quiz_get_user_attempts') return { attempts: [] };
    if (method === 'core_calendar_get_action_events_by_timesort') { assert.equal(args.limitnum, 50); return { events: [] }; }
    throw new Error('Unexpected method');
  } };
  const state = { courses: [{ id: 'TEST', moodle_course_ids: [1, 2] }], tasks: [] };
  const before = JSON.stringify(state);
  const report = await collectRefresh(client, state);
  assert.equal(report.collection_complete, true);
  assert.equal(report.reviewed, false);
  assert.equal(report.scope_limits.length, 1);
  assert.equal(report.submissions.length, 1);
  assert.equal(JSON.stringify(state), before);
  assert.ok(calls.every(m => m.includes('_get_')));
});
