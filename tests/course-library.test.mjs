import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {
  assignArchiveLayouts, authorizationReport, categoryFor, enumerateCourse, externalReason,
  gapMarkdown, htmlToText, planPage, resolveSubject, selectedPages, syncCourseLibrary,
} from '../tools/moodle/course-library.mjs';
import {validateFileBytes} from '../tools/moodle/course-files.mjs';

const BASE = 'https://school.example/moodle';
const PDF = Buffer.from('%PDF-1.4\nfictional course file');
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);

function stateFixture() {
  return {
    revision: 3,
    timezone: 'Asia/Hong_Kong',
    settings: {course_library_authorization: {
      schema: 1, scope_version: 1, scope: 'test scope',
      courses: {
        'moodle-1': {moodle_courseid: 101, mode: 'full', authorized_at: '2026-01-01T00:00:00Z', scope_version: 1},
        'moodle-2': {moodle_courseid: 202, mode: 'full', authorized_at: '2026-01-01T00:00:00Z', scope_version: 1},
      },
    }},
    courses: [
      {id: 'moodle-1', name: 'DEMO1013 Common', shortname: 'DEMO1013', moodle_course_ids: [101], page_role: 'primary', linked_page_ids: [202]},
      {id: 'moodle-2', name: 'DEMO1013 Section', shortname: 'DEMO1013_1C', moodle_course_ids: [202], merged_into: 'moodle-1', page_role: 'secondary'},
    ],
    materials: [],
    tasks: [{id: 'T', course_id: 'moodle-1'}],
  };
}

function pageFixture(courseId, moodleCourseid) {
  const state = stateFixture();
  const course = state.courses.find(row => row.id === courseId);
  return {courseId, moodleCourseid, course, subject: resolveSubject(state, courseId), baseUrl: BASE};
}

function module(id, modname, name, extra = {}) {
  return {id, modname, name, instance: extra.instance ?? id, url: `${BASE}/mod/${modname}/view.php?id=${id}`,
    visible: 1, uservisible: true, contents: extra.contents ?? [], ...extra};
}

function section(name, modules, index = 0) {
  return {name, index, summary: '', uservisible: true, modules};
}

function snapshotFixture() {
  return {
    started_at: '2026-11-02T09:00:00Z', finished_at: '2026-11-02T09:05:00Z',
    collection_complete: true,
    collection_binding: {run_id: 'a'.repeat(32)},
    pages: [
      {course_id: 'moodle-1', courseid: 101, sections: []},
      {course_id: 'moodle-2', courseid: 202, sections: []},
    ],
    assignments: {courses: [{id: 101, assignments: [
      {id: 900, cmid: 503, name: 'Assignment 3', duedate: 1000, intro: '<p>Submit <b>PDF</b></p>',
        introattachments: [{filename: 'Assignment 3.pdf', fileurl: `${BASE}/webservice/pluginfile.php/1/mod_assign/introattachment/0/Assignment%203.pdf`, filesize: PDF.length, timemodified: 11}]},
    ]}]},
    quizzes: {quizzes: [{id: 700, course: 101, coursemodule: 504, name: 'Quiz 5', timeclose: 2000}]},
    forums: [{course_id: 'moodle-2', courseid: 202, module: {id: 401, instance: 800}, discussions: []}],
    threads: [{course_id: 'moodle-2', forumid: 800, forum_url: `${BASE}/mod/forum/view.php?id=401`,
      discussion: {id: 9001, discussion: 9001, name: 'Announcement', userfullname: 'Teacher',
        created: 100, modified: 100, message: '<p>Read chapter 3</p>'},
      posts: [{id: 9101, subject: 'Re: Announcement', author: {fullname: 'Teacher'}, timecreated: 200,
        message: '<p>Hint: see <img src="' + BASE + '/pluginfile.php/1/mod_forum/post/9101/example.png"> the figure</p>',
        attachments: [{filename: 'notes.pdf', fileurl: `${BASE}/webservice/pluginfile.php/1/mod_forum/attachment/9101/notes.pdf`, filesize: PDF.length, timemodified: 21}]}]}],
  };
}

function enumerateFixture() {
  const page1 = pageFixture('moodle-1', 101);
  const page2 = pageFixture('moodle-2', 202);
  return [
    {page: page1, sections: [section('Class Materials', [
      module(501, 'resource', 'Lecture 01', {contents: [{type: 'file', filename: 'Lecture 01.pdf',
        filepath: '/', filesize: PDF.length, timemodified: 12,
        fileurl: `${BASE}/webservice/pluginfile.php/11/mod_resource/content/1/Lecture%2001.pdf`}]}),
      module(502, 'page', 'Course policy', {contents: [{type: 'file', filename: 'index.html',
        filepath: '/', filesize: 0, timemodified: 13,
        fileurl: `${BASE}/webservice/pluginfile.php/12/mod_page/content/index.html`}]}),
      module(505, 'folder', 'Recordings', {contents: []}),
      module(506, 'label', 'Assignment marker', {contents: []}),
    ]), section('Quizzes', [
      module(504, 'quiz', 'Quiz 5', {instance: 700}),
      module(507, 'url', 'Lecture videos', {contents: [{type: 'url', filename: 'Lecture videos',
        fileurl: 'https://www.youtube.com/watch?v=abc'}]}),
      module(508, 'resource', 'Restricted solutions', {uservisible: false}),
    ], 1), section('Assignments', [
      module(503, 'assign', 'Assignment 3', {instance: 900, contents: []}),
    ], 2)]},
    {page: page2, sections: [section('Lecture materials', [
      module(601, 'resource', 'Lecture 01', {contents: [{type: 'file', filename: 'Lecture 01.pdf',
        filepath: '/', filesize: PDF.length, timemodified: 14,
        fileurl: `${BASE}/webservice/pluginfile.php/21/mod_resource/content/1/Lecture%2001.pdf`}]}),
    ]), section('Announcements', [
      module(401, 'forum', 'Announcements', {instance: 800, contents: []}),
    ], 1)]},
  ];
}

function textsFor(snapshot) {
  // Mirror the snapshot adapter without touching the network.
  const sources = {assignments: new Map(), quizzes: new Map(), threads: new Map(), collectedForums: new Set(),
    capturedAt: snapshot.finished_at, source: 'snapshot', snapshotRunId: 'a'.repeat(32), errors: []};
  for (const course of snapshot.assignments.courses) sources.assignments.set(Number(course.id), course.assignments);
  for (const quiz of snapshot.quizzes.quizzes) {
    const list = sources.quizzes.get(Number(quiz.course)) ?? [];
    list.push(quiz); sources.quizzes.set(Number(quiz.course), list);
  }
  for (const thread of snapshot.threads) {
    const key = `${thread.course_id}|${thread.forumid}`;
    const list = sources.threads.get(key) ?? [];
    list.push(thread); sources.threads.set(key, list);
  }
  for (const forum of snapshot.forums) sources.collectedForums.add(`${forum.course_id}|${forum.module.instance}`);
  return sources;
}

test('helpers: subject resolution, authorization states, html text and categories', () => {
  const state = stateFixture();
  assert.equal(resolveSubject(state, 'moodle-2'), 'moodle-1');
  assert.equal(resolveSubject(state, 'moodle-1'), 'moodle-1');
  const pages = selectedPages(state);
  assert.equal(pages.length, 2);
  const report = authorizationReport(state, pages);
  assert.equal(report.authorized.length, 2);
  assert.equal(report.blocked.length, 0);
  state.settings.course_library_authorization.courses['moodle-2'].mode = 'info_only';
  assert.deepEqual(authorizationReport(state, pages).blocked.map(b => b.reason), ['authorization_info_only']);
  delete state.settings.course_library_authorization.courses['moodle-1'];
  assert.deepEqual(authorizationReport(state, pages).blocked.map(b => b.reason), ['course_not_authorized', 'authorization_info_only']);
  assert.match(htmlToText('<p>Hello&nbsp;<b>world</b></p><table><tr><td>A</td><td>B</td></tr></table>'), /Hello world[\s\S]*A\tB/);
  assert.equal(categoryFor({sectionName: 'Tutorials', moduleName: 'Week 3'}), 'Tutorials');
  assert.equal(categoryFor({moduleName: 'Assignment 3'}), 'Assignments');
  assert.equal(categoryFor({moduleName: 'Practice questions'}), 'Practice');
  assert.equal(categoryFor({moduleName: 'Lecture 01 notes'}), 'Lectures');
  assert.equal(categoryFor({moduleName: 'Misc'}), 'Other');
  assert.equal(externalReason('https://youtu.be/x'), 'external_streaming_not_downloaded');
  assert.equal(externalReason('https://drive.google.com/x'), 'external_site_not_downloaded');
  // Moodle occasionally serves a JPEG under a .png name; image validation follows content.
  const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46]);
  assert.deepEqual(validateFileBytes('mismatch.png', jpeg), jpeg);
  assert.throws(() => validateFileBytes('fake.png', Buffer.from('not an image')), /signature/);
  assert.throws(() => validateFileBytes('fake.pdf', Buffer.from('not a pdf')), /signature/);
});

test('enumerateCourse keeps module contents and access flags', async () => {
  const calls = [];
  const client = {baseUrl: BASE, async call(method, args) { calls.push({method, args});
    return [section('One', [module(501, 'resource', 'Lecture', {contents: [{type: 'file', filename: 'a.pdf', fileurl: `${BASE}/webservice/pluginfile.php/1/mod_resource/content/1/a.pdf`}]})])]; }};
  const result = await enumerateCourse(client, pageFixture('moodle-1', 101));
  assert.equal(calls[0].method, 'core_course_get_contents');
  assert.equal(calls[0].args.courseid, 101);
  assert.equal(result.modules.length, 1);
  assert.equal(result.modules[0].contents[0].filename, 'a.pdf');
});

test('planPage enumerates files, text captures and explained skips', () => {
  const state = stateFixture();
  const [page1, page2] = enumerateFixture();
  const snapshot = snapshotFixture();
  const texts = textsFor(snapshot);
  const first = planPage({page: page1.page, enumerated: page1, texts, state});
  const second = planPage({page: page2.page, enumerated: page2, texts, state});
  const items = [...first.items, ...second.items];
  const skips = [...first.skips, ...second.skips];
  const files = items.filter(item => item.kind === 'file');
  assert(files.some(item => item.filename === 'Lecture 01.pdf' && item.subject === 'moodle-1'));
  assert(files.some(item => item.filename === 'Lecture 01.pdf' && item.subject === 'moodle-1' && item.pageId === 'moodle-2'));
  assert(files.some(item => item.filename === 'Assignment 3.pdf' && item.filepath === '/assign-introattachment/'));
  assert(files.some(item => item.filename === 'notes.pdf' && item.filepath === `/forum/9101/`));
  assert(files.some(item => item.filename === 'example.png' && item.filepath === '/forum-inline/9101/'));
  assert(files.some(item => item.filename === 'index.html'));
  const captures = items.filter(item => item.kind === 'capture').map(item => item.capture.kind).sort();
  assert.deepEqual(captures, ['assignment', 'course-page', 'course-page', 'forum', 'page-module']);
  const assignmentCapture = items.find(item => item.capture?.kind === 'assignment');
  assert.match(assignmentCapture.capture.markdown, /Submit PDF/);
  const forumCapture = items.find(item => item.capture?.kind === 'forum');
  assert.match(forumCapture.capture.markdown, /Read chapter 3/);
  assert.match(forumCapture.capture.markdown, /discuss\.php\?d=9001/);
  const reasons = skips.map(skip => skip.reason).sort();
  assert(reasons.includes('quiz_questions_require_attempt_not_started'));
  assert(reasons.includes('external_streaming_not_downloaded'));
  assert(reasons.includes('restricted_by_course_permission'));
  assert(reasons.includes('folder_contents_not_exposed'));
  assert(reasons.includes('label_body_not_exposed_by_api'));
  const layouts = assignArchiveLayouts(items, state);
  const fileItems = layouts.filter(item => item.kind === 'file');
  const lecture = fileItems.filter(item => item.filename === 'Lecture 01.pdf');
  assert.equal(lecture.filter(item => item.archiveSubdir === null).length, 1);
  assert.equal(lecture.filter(item => item.archiveSubdir?.startsWith('module-')).length, 1);
});

function temporaryRoot() {
  const root = mkdtempSync(join(tmpdir(), 'course-library-'));
  mkdirSync(join(root, 'Study/_System'), {recursive: true});
  mkdirSync(join(root, 'Study/_Inbox'), {recursive: true});
  return root;
}

function syncClient({failURL = null} = {}) {
  const fixture = enumerateFixture();
  const client = {
    baseUrl: BASE, token: 'fictional-token', userId: 1, fetches: [], installs: 0,
    async call(method, args) {
      if (method === 'core_course_get_contents') {
        const found = fixture.find(entry => entry.page.moodleCourseid === Number(args.courseid));
        return found.sections.map(section => ({...section, modules: section.modules.map(m => ({
          id: m.id, name: m.name, modname: m.modname, instance: m.instance, url: m.url,
          visible: m.visible, uservisible: m.uservisible, contents: m.contents,
        }))}));
      }
      throw new Error('unexpected API call ' + method);
    },
    async transport(url) {
      this.fetches.push(String(url));
      const parsed = new URL(url);
      const file = decodeURIComponent(parsed.pathname.split('/').at(-1));
      if (failURL && String(url).includes(failURL)) throw new Error('network down ' + url);
      if (file.endsWith('.html')) return new Response('<html><body>page <img src="' + BASE + '/pluginfile.php/1/mod_page/content/inline.png"></body></html>',
        {headers: {'content-type': 'text/html'}});
      if (file.endsWith('.png')) return new Response(PNG, {headers: {'content-type': 'image/png'}});
      return new Response(PDF, {headers: {'content-type': 'application/pdf'}});
    },
  };
  return client;
}

function stubInstaller(record) {
  return ({payload}) => {
    record.push(payload);
    return {items: payload.items.map(item => ({
      item_key: item.item_key, status: 'promoted', material_id: 'material-' + item.item_key.replace(/[^A-Za-z0-9]/g, ''),
      relative_path: item.relative_path.replace('_Inbox/.staging/', ''), archived_id: null,
    }))};
  };
}

test('syncCourseLibrary downloads, captures, records gaps and writes resumable ledger', async () => {
  const root = temporaryRoot();
  try {
    const snapshotPath = join(root, 'Study/_System/test-snapshot.json');
    writeFileSync(snapshotPath, JSON.stringify(snapshotFixture()), 'utf8');
    const state = stateFixture();
    const client = syncClient();
    const installRecords = [];
    const indexCalls = [];
    const report = await syncCourseLibrary({
      client, root, state, snapshotPath,
      installBatch: stubInstaller(installRecords),
      buildIndex: courses => { indexCalls.push(courses); return {courses: Object.fromEntries(courses.map(course => [course,
        {materials: 5, units: 9, text_units: 8, status_counts: {indexed: 5}, attention: []}]))}; },
      batchSize: 4,
    });
    assert.equal(report.ok, true, JSON.stringify(report.errors).slice(0, 2000));
    assert.equal(report.dry_run, false);
    assert(report.totals.file_candidates >= 6);
    assert(report.totals.installed >= report.totals.file_candidates);
    assert(report.totals.capture_installed >= 5);
    assert(report.totals.skipped >= 5);
    assert(indexCalls.length === 1);
    const serialized = JSON.stringify(report);
    assert(!serialized.includes('fictional-token'));
    assert(!serialized.includes('wstoken='));
    assert.equal(report.no_school_writes, true);
    assert.equal(report.no_background_task, true);

    const latest = JSON.parse(readFileSync(join(root, 'Study/_System/LibrarySync/latest.json'), 'utf8'));
    assert.equal(latest.run_id, report.run_id);
    assert(existsSync(join(root, 'Study/_System/LibrarySync/资料缺口.md')));
    const gaps = readFileSync(join(root, 'Study/_System/LibrarySync/资料缺口.md'), 'utf8');
    assert.match(gaps, /未获取/);
    assert.match(gaps, /下载成功或解析成功不等于已核读/);
    const ledger = JSON.parse(readFileSync(join(root, 'Study/_System/LibrarySync/ledger.json'), 'utf8'));
    const installedEntries = Object.values(ledger.entries).filter(entry => entry.status === 'installed');
    assert(installedEntries.length >= report.totals.installed);

    // A second run with the same state must not refetch files whose ledger
    // fingerprint and current material still match.
    const materials = report.items.filter(item => item.kind === 'file' && item.status === 'installed').map(item => ({
      id: item.material_id, course_id: item.subject, sha256: item.sha256, relative_path: item.relative_path,
      moodle_file_sources: [{course_id: item.subject, module_id: item.module_id, filepath: item.filepath,
        remote_filename: item.filename, url: item.url}],
    }));
    materials.push(...report.items.filter(item => item.kind === 'capture' && item.status === 'installed').map(item => ({
      id: item.material_id, course_id: item.subject, sha256: item.sha256, relative_path: item.relative_path, moodle_file_sources: [],
    })));
    const resumedState = {...stateFixture(), materials};
    const resumedClient = syncClient();
    const resumed = await syncCourseLibrary({
      client: resumedClient, root, state: resumedState, snapshotPath,
      installBatch: () => { throw new Error('no install expected'); },
      buildIndex: () => ({courses: {}}),
      batchSize: 4,
    });
    assert.equal(resumedClient.fetches.length, 0);
    assert.equal(resumed.totals.installed, 0);
    assert.equal(resumed.totals.failed, 0);
    assert(resumed.totals.already_current >= report.items.filter(item => item.status === 'installed' && item.kind === 'capture').length);
    assert.equal(resumed.ok, true);
  } finally { rmSync(root, {recursive: true, force: true}); }
});

test('failed download is recorded and retried alone on the next run', async () => {
  const root = temporaryRoot();
  try {
    const snapshotPath = join(root, 'Study/_System/test-snapshot.json');
    writeFileSync(snapshotPath, JSON.stringify(snapshotFixture()), 'utf8');
    const state = stateFixture();
    const firstClient = syncClient({failURL: 'Lecture%2001.pdf'});
    const first = await syncCourseLibrary({
      client: firstClient, root, state, snapshotPath,
      installBatch: stubInstaller([]), buildIndex: () => ({courses: {}}), batchSize: 3,
    });
    assert(first.totals.failed >= 1);
    assert.equal(first.ok, false);
    assert(first.errors.some(error => String(error.error).includes('network') || String(error.error).includes('download')));
    const failedItems = first.items.filter(item => item.kind === 'file' && item.status === 'failed');
    assert(failedItems.length >= 1);

    const secondClient = syncClient();
    const second = await syncCourseLibrary({
      client: secondClient, root, state, snapshotPath,
      installBatch: stubInstaller([]), buildIndex: () => ({courses: {}}), batchSize: 3,
    });
    // Successful items are skipped by ledger; the failed ones are fetched again.
    assert(secondClient.fetches.length >= failedItems.length);
    for (const item of failedItems) {
      assert(secondClient.fetches.some(url => String(url).includes(encodeURIComponent(item.filename).replaceAll('%2F', '/'))));
    }
    assert.equal(second.ok, true, JSON.stringify(second.errors).slice(0, 2000));
  } finally { rmSync(root, {recursive: true, force: true}); }
});

test('unauthorized or info-only courses are blocked before any download', async () => {
  const root = temporaryRoot();
  try {
    const client = syncClient();
    const state = stateFixture();
    state.settings.course_library_authorization.courses['moodle-1'].mode = 'info_only';
    state.settings.course_library_authorization.courses['moodle-2'].mode = 'info_only';
    await assert.rejects(syncCourseLibrary({client, root, state, installBatch: () => { throw new Error('no install'); },
      buildIndex: () => ({courses: {}})}), /course-level full authorization/i);
    const noAuth = stateFixture();
    delete noAuth.settings.course_library_authorization;
    await assert.rejects(syncCourseLibrary({client, root, state: noAuth, installBatch: () => { throw new Error('no install'); },
      buildIndex: () => ({courses: {}})}), /course-level full authorization/i);
    assert.equal(client.fetches.length, 0);
  } finally { rmSync(root, {recursive: true, force: true}); }
});

test('gap markdown is generated from a completed report', async () => {
  const root = temporaryRoot();
  try {
    const snapshotPath = join(root, 'Study/_System/test-snapshot.json');
    writeFileSync(snapshotPath, JSON.stringify(snapshotFixture()), 'utf8');
    const report = await syncCourseLibrary({
      client: syncClient(), root, state: stateFixture(), snapshotPath,
      installBatch: stubInstaller([]), buildIndex: () => ({courses: {}}),
    });
    const markdown = gapMarkdown(report);
    assert.match(markdown, /课程资料缺口/);
    assert.match(markdown, /MATH|DEMO|moodle-1/);
    assert.match(markdown, /索引/);
  } finally { rmSync(root, {recursive: true, force: true}); }
});
