import test from 'node:test';
import assert from 'node:assert/strict';
import { listAssignments } from 'moodle-mcp/dist/tools/assignments.js';
import { listForums, getForumDiscussions } from 'moodle-mcp/dist/tools/forums.js';

test('Moodle cmid links an assignment to its published deadline', async () => {
  const client = {
    supports: () => true,
    async call(method) {
      if (method === 'core_course_get_contents') return [{ name: 'Assignments', modules: [
        { id: 110, instance: 111, modname: 'assign', name: 'Assignment 1' },
      ] }];
      if (method === 'mod_assign_get_assignments') return { courses: [{ assignments: [
        { id: 111, cmid: 110, name: 'Assignment 1', duedate: 1790351940, grade: 100 },
      ] }] };
      throw new Error('Unexpected API method: ' + method);
    },
  };
  const result = await listAssignments(client, 101);
  assert.match(result, /Due:/);
  assert.match(result, /111/);
  assert.doesNotMatch(result, /details unavailable/);
});

test('forum list returns forum instance ID while preserving browser URL', async () => {
  const client = { async call() { return [{ name: 'General', modules: [
    { id: 210, instance: 211, modname: 'forum', name: 'Discussion forum',
      url: 'https://school.example/mod/forum/view.php?id=210' },
  ] }]; } };
  const result = await listForums(client, 101);
  assert.match(result, /ID: `211`/);
  assert.match(result, /view\.php\?id=210/);
});

test('discussion query uses parameters accepted by the Moodle discussion API', async () => {
  const client = { supports: () => true, async call(method, params) {
    const allowed = ['forumid', 'sortorder', 'page', 'perpage', 'groupid'];
    if (Object.keys(params).some(key => !allowed.includes(key))) throw new Error('Invalid discussion parameter');
    return { discussions: [{ name: 'Limits', userfullname: 'Tutor', numreplies: 1, timemodified: 1790000000 }] };
  } };
  const result = await getForumDiscussions(client, 211);
  assert.match(result, /Limits/);
  assert.match(result, /By Tutor/);
});
