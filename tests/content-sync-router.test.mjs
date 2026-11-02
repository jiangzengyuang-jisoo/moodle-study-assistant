import test from 'node:test';
import assert from 'node:assert/strict';
import {syncAuthorizedContent} from '../tools/moodle/content-sync-router.mjs';

function args(policy) {
  const settings = {course_content_sync: {version: 1, grants: [{mode: 'full'}]}};
  if (policy !== undefined) settings.course_library_authorization = policy;
  return {client: {}, root: process.cwd(), snapshot: {pages: [{courseid: 9}]},
    snapshotPath: 'current-managed-snapshot.json',
    state: {settings, courses: [{id: 'C', moodle_course_ids: [9]}]}};
}
const policy = mode => ({schema: 1, courses: {C: {mode, moodle_courseid: 9}}});
const forbidden = () => { throw new Error('must not call this downloader'); };

test('new full authorization uses library once with the validated snapshot', async () => {
  let calls = 0;
  const result = await syncAuthorizedContent(args(policy('full')), {legacy: forbidden,
    library: async input => { calls++; assert.equal(input.snapshotPath, 'current-managed-snapshot.json');
      return {ok: true, totals: {installed: 3}}; }});
  assert.equal(calls, 1); assert.equal(result.status, 'complete');
  assert.equal(result.counts.installed, 3); assert.match(result.report_path, /LibrarySync/);
});

test('info-only, malformed and changed-scope consent never revive old full grants', async () => {
  for (const value of [policy('info_only'), null, {}, {...policy('full'), schema: 2},
    {schema: 1, courses: {C: {mode: 'full', moodle_courseid: 10}}}]) {
    const result = await syncAuthorizedContent(args(value), {library: forbidden, legacy: forbidden});
    assert.equal(result.status, 'not_authorized');
  }
});

test('legacy accounts still use the published bound-consent downloader', async () => {
  let calls = 0;
  const result = await syncAuthorizedContent(args(), {library: forbidden,
    legacy: async (_client, _state, options) => { calls++;
      assert.deepEqual(options.snapshot.pages, [{courseid: 9}]);
      return {status: 'complete', counts: {downloaded: 2}}; }});
  assert.equal(calls, 1); assert.match(result.report_path, /CourseContent/);
});

test('a failed text capture is not reported as complete', async () => {
  const result = await syncAuthorizedContent(args(policy('full')), {legacy: forbidden,
    library: async () => ({ok: true, totals: {capture_failed: 1}})});
  assert.equal(result.status, 'partial');
});
