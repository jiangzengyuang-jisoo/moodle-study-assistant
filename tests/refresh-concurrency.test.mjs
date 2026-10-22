import test from 'node:test';
import assert from 'node:assert/strict';
import { setImmediate as nextTurn } from 'node:timers/promises';
import { collectRefresh } from '../tools/moodle/refresh.mjs';

// All inputs are synthetic; every method records its live overlap across branches.
function fixture({ fail = false, repeatedPage = false, badPosts = false, fullCalendar = false } = {}) {
  const state = {
    courses: [{ id: 'ALPHA', moodle_course_ids: [20] }, { id: 'BETA', moodle_course_ids: [10] }],
    tasks: [{ source: { url: 'https://school.example/mod/quiz/view.php?id=503' } }],
  };
  let active = 0, peak = 0;
  const calls = [], pendingForums = new Set(), completedForumPages = new Map();
  const client = {
    baseUrl: 'https://school.example', userId: 91,
    async call(method, args) {
      assert.equal(this, client, 'client methods must retain their receiver');
      calls.push({ method, args });
      active++; peak = Math.max(peak, active);
      if (method === 'mod_forum_get_forum_discussions') {
        assert.equal(pendingForums.has(args.forumid), false, 'one forum cannot have overlapping page requests');
        assert.equal(args.page, completedForumPages.get(args.forumid) ?? 0);
        pendingForums.add(args.forumid);
      }
      try {
        await nextTurn();
        if (method === 'mod_assign_get_assignments') {
          assert.deepEqual(Object.values(args).sort((a, b) => a - b), [10, 20]);
          return { courses: [{ id: 20, assignments: [{ id: 42, cmid: 142 }, { id: 41, cmid: 141 }] },
                             { id: 10, assignments: [{ id: 31, cmid: 131 }] }],
                   warnings: [{ item: 'module', itemid: 999, warningcode: '1' }] };
        }
        if (method === 'mod_quiz_get_quizzes_by_courses') return { quizzes: [
          { id: 51, course: 20, coursemodule: 501, timeclose: 1 },
          { id: 52, course: 10, coursemodule: 502, timeclose: 0 }, // unassigned question bank
          { id: 53, course: 10, coursemodule: 503, timeclose: 0 }, // explicitly tracked
        ] };
        if (method === 'core_course_get_contents') return [{ modules: [
          { id: args.courseid === 20 ? 142 : 131, modname: 'assign', uservisible: true },
          { id: args.courseid + 100, instance: args.courseid + 200, modname: 'forum', url: 'https://school.example/forum' },
        ] }];
        if (method === 'mod_forum_get_forum_discussions') {
          if (fail && args.forumid === 210 && args.page === 1) throw new Error('synthetic page failure');
          const base = args.forumid * 1000;
          const count = args.page === 0 ? 100 : 1;
          return { discussions: Array.from({ length: count }, (_, i) => ({
            discussion: base + (repeatedPage && args.page === 1 ? 0 : args.page * 100) + i,
            numreplies: 0,
          })) };
        }
        if (method === 'mod_forum_get_discussion_posts') {
          if (badPosts && args.discussionid === 220000) return { posts: [] };
          return { posts: [{ id: args.discussionid * 10, message: 'synthetic source' }] };
        }
        if (method === 'mod_assign_get_submission_status') {
          if (fail && args.assignid === 42) throw new Error('synthetic status failure');
          return { lastattempt: { submission: { status: 'submitted' } } };
        }
        if (method === 'mod_quiz_get_user_attempts') {
          assert.equal(args.userid, 91);
          if (fail && args.quizid === 53) return { warnings: [{ warningcode: 'denied' }] };
          return { attempts: [] };
        }
        if (method === 'core_calendar_get_action_events_by_timesort') {
          assert.equal(args.limitnum, 50);
          return { events: fullCalendar ? Array.from({ length: 50 }, (_, id) => ({ id })) : [] };
        }
        throw new Error('Unexpected synthetic API method: ' + method);
      } finally {
        active--;
        if (method === 'mod_forum_get_forum_discussions') {
          pendingForums.delete(args.forumid);
          completedForumPages.set(args.forumid, args.page + 1);
        }
      }
    },
  };
  return { state, client, calls, peak: () => peak, active: () => active };
}

function evidence(report) {
  const { started_at, finished_at, ...rest } = report;
  return rest;
}

test('all collection branches share four slots and preserve selected-course evidence', async () => {
  const f = fixture();
  const original = JSON.stringify(f.state);
  const report = await collectRefresh(f.client, f.state);
  assert.equal(f.peak(), 4);
  assert.equal(f.active(), 0);
  assert.equal(report.collection_complete, true);
  assert.equal(report.moodle_url, f.client.baseUrl);
  assert.equal(report.reviewed, false);
  assert.equal(report.files_downloaded, 0);
  assert.equal(report.pages.length, 2);
  assert.equal(report.forums.length, 2);
  assert.equal(report.threads.length, 202);
  assert.equal(report.submissions.length, 3);
  assert.deepEqual(report.attempts.map(q => q.quiz_id), [53, 51]);
  assert.equal(report.scope_limits[0].scope, 'module:999');
  assert.equal(JSON.stringify(f.state), original);
  assert.ok(f.calls.every(c => c.method.includes('_get_')));
  assert.deepEqual(f.calls.filter(c => c.method === 'core_course_get_contents').map(c => c.args.courseid).sort((a, b) => a - b), [10, 20]);
});

test('serial and concurrent scheduling produce identical evidence and drain failures', async () => {
  const serial = fixture({ fail: true }), parallel = fixture({ fail: true });
  const expected = await collectRefresh(serial.client, serial.state, { concurrency: 1 });
  const actual = await collectRefresh(parallel.client, parallel.state);
  assert.equal(serial.peak(), 1);
  assert.equal(parallel.peak(), 4);
  assert.equal(parallel.active(), 0);
  assert.deepEqual(evidence(actual), evidence(expected));
  assert.equal(actual.collection_complete, false);
  assert.deepEqual(actual.errors.map(e => e.scope), ['assignment:42', 'forum:210', 'quiz:53']);
  assert.equal(actual.submissions.find(s => s.assignment_id === 42).status, null);
  assert.equal(actual.attempts.find(q => q.quiz_id === 53).data, null);
  assert.equal(actual.threads.length, 101, 'an inaccessible forum must not prevent other sources completing');
});

test('pagination loops, missing posts and truncated calendars still block completeness', async () => {
  for (const options of [{ repeatedPage: true }, { badPosts: true }, { fullCalendar: true }]) {
    const f = fixture(options);
    const report = await collectRefresh(f.client, f.state);
    assert.equal(report.collection_complete, false);
    assert.ok(report.errors.length > 0);
    assert.ok(f.peak() <= 4);
    assert.equal(f.active(), 0);
  }
});

test('invalid concurrency or unselected courses cannot issue API requests', async () => {
  for (const concurrency of [0, -1, 1.5, 5]) {
    const f = fixture();
    await assert.rejects(collectRefresh(f.client, f.state, { concurrency }), /Concurrency/);
    assert.equal(f.calls.length, 0);
  }
  const f = fixture();
  await assert.rejects(collectRefresh(f.client, { courses: [], tasks: [] }), /course IDs/);
  assert.equal(f.calls.length, 0);
});

test('visible-module warnings remain errors after slower course reads finish', async () => {
  const f = fixture();
  const originalCall = f.client.call;
  f.client.call = async function (method, args) {
    const data = await originalCall.call(this, method, args);
    if (method === 'mod_assign_get_assignments') data.warnings.push({ item: 'module', itemid: 142, warningcode: '1' });
    return data;
  };
  const report = await collectRefresh(f.client, f.state);
  assert.equal(report.collection_complete, false);
  assert.equal(report.scope_limits.length, 1);
  assert.equal(report.errors[0].scope, 'assignment_catalog');
});
