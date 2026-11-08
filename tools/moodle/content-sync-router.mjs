// The managed snapshot/account gate in run.mjs runs before either implementation.
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {python} from './state-bridge.mjs';
import {normalizeSite} from './content-policy.mjs';
import {authorizationReport, selectedPages, syncCourseLibrary} from './course-library.mjs';
import {syncCourseContent} from './course-content-sync.mjs';

const programRoot = fileURLToPath(new URL('../../', import.meta.url));

export async function syncAuthorizedContent({client, state, root, snapshot, snapshotPath},
  {library = syncCourseLibrary, legacy = syncCourseContent} = {}) {
  // An explicit new policy (including info-only or invalid) supersedes old grants.
  // Never fall back to old full consent after the user has changed their choice.
  if (Object.hasOwn(state.settings ?? {}, 'course_library_authorization')) {
    const policy = state.settings.course_library_authorization;
    const reportPath = join(root, 'Study/_System/LibrarySync/latest.json');
    if (policy?.schema !== 1 || !authorizationReport(state, selectedPages(state), {site: normalizeSite(client.baseUrl), user_id: client.userId}).authorized.length)
      return {status: 'not_authorized', report_path: reportPath};
    const result = await library({client, root, state, snapshotPath});
    return {status: result.ok && !result.totals?.capture_failed ? 'complete' : 'partial',
      counts: result.totals, report_path: reportPath};
  }
  const result = await legacy(client, state, {studyRoot: join(root, 'Study'),
    statePath: join(root, 'Study/_System/state.json'), studyToolsDir: join(programRoot, 'tools/study'),
    python: python(root), snapshot});
  return {status: result.status, counts: result.counts,
    report_path: join(root, 'Study/_System/CourseContent/latest.json')};
}
