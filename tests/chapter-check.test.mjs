import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {
  chapterNumber, checkChapter, classifyItem, collectSectionItems, materialSources, MODES,
  receiptPath, resolveBaseline, resolveCoursePages, selectSections, summarize, verdictOf, writeReceipt,
} from '../tools/moodle/chapter-check.mjs';
import {webserviceFileURL} from '../tools/moodle/course-files.mjs';

const BASE = 'https://school.example/moodle';
const PDF = Buffer.from('%PDF-1.4\nfictional chapter 8 slides');
const OTHER_PDF = Buffer.from('%PDF-1.4\nfictional chapter 8 slides, revised');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

function stateFixture(overrides = {}) {
  return {
    revision: 1,
    lessons: [],
    settings: {profile_account_scope: {schema: 1, site: BASE, user_id: 7, bound_at: '2026-11-01T00:00:00Z'},
      course_library_authorization: {schema: 1, scope_version: 1, scope: 'test', courses: {
        'moodle-1': {moodle_courseid: 101, mode: 'full', scope_version: 1,
          account_scope: {site: BASE, user_id: 7}},
      }}},
    courses: [{id: 'moodle-1', name: 'ACCT1101 Accounting', shortname: 'ACCT1101', moodle_course_ids: [101]}],
    materials: [],
    ...overrides,
  };
}

function resourceModule(id, name, filename, extra = {}) {
  const fileurl = `${BASE}/webservice/pluginfile.php/1/mod_resource/content/1/${encodeURIComponent(filename)}`;
  return {
    id, name, modname: 'resource', instance: id, url: `${BASE}/mod/resource/view.php?id=${id}`,
    visible: 1, uservisible: true,
    contents: [{type: 'file', filename, filepath: '/', filesize: PDF.length, timemodified: 1000, fileurl}],
    ...extra,
  };
}

function moodleSections() {
  return [
    {id: 1, section: 0, name: 'General', summary: '', uservisible: true, modules: []},
    {id: 2, section: 8, name: 'Chapter 8: Inventories', summary: '', uservisible: true,
      modules: [resourceModule(501, 'Chapter 8 slides', 'Chapter 8.pdf'),
        resourceModule(502, 'Chapter 8 handout', 'Chapter 8 handout.pdf')]},
    {id: 3, section: 18, name: 'Chapter 18: Review', summary: '', uservisible: true,
      modules: [resourceModule(503, 'Chapter 18 slides', 'Chapter 18.pdf')]},
  ];
}

function fakeClient({sections = moodleSections(), bytes = PDF, failContents = false, failFor = null} = {}) {
  const calls = [];
  const client = {
    baseUrl: BASE, userId: 7, token: 'secret-token',
    calls,
    async call(method, args) {
      calls.push({method, args});
      if (method === 'core_course_get_contents') {
        if (failContents || (failFor && failFor.includes(args.courseid))) throw new Error('network down');
        return sections;
      }
      throw new Error(`unexpected call ${method}`);
    },
    async transport() {
      throw new Error('quick verification must not download files');
    },
  };
  client.download = async (clientArg, file) => ({bytes, source_url: 'clean'});
  return client;
}

function cleanUrl(filename) {
  return webserviceFileURL(BASE, `${BASE}/webservice/pluginfile.php/1/mod_resource/content/1/${encodeURIComponent(filename)}`)
    .toString();
}

function ledgerFixture({handoutTime = 2000} = {}) {
  return {schema: 1, entries: {
    'moodle-1|501|/|Chapter 8.pdf': {
      status: 'installed', material_id: 'm1', sha256: sha(PDF),
      remote: {filesize: PDF.length, timemodified: 1000, url: cleanUrl('Chapter 8.pdf')},
    },
    'moodle-1|502|/|Chapter 8 handout.pdf': {
      status: 'installed', material_id: 'm2', sha256: sha(PDF),
      remote: {filesize: PDF.length, timemodified: handoutTime, url: cleanUrl('Chapter 8 handout.pdf')},
    },
  }};
}

function tempRoot() {
  const root = mkdtempSync(join(tmpdir(), 'chapter-check-'));
  return {root, cleanup: () => rmSync(root, {recursive: true, force: true})};
}

test('chapterNumber reads the chapter from common section titles', () => {
  assert.equal(chapterNumber('8'), 8);
  assert.equal(chapterNumber('Chapter 8'), 8);
  assert.equal(chapterNumber('第8章 存货'), 8);
  assert.equal(chapterNumber('Topic 12 - Ratios'), 12);
  assert.equal(chapterNumber('General'), null);
});

test('selectSections never turns 8 into Chapter 18', () => {
  const sections = [
    {name: 'Chapter 8: Inventories', index: 1, number: 8},
    {name: 'Chapter 18: Review', index: 2, number: 18},
    {name: 'General', index: 0, number: 0},
  ];
  const bare = selectSections(sections, '8');
  assert.deepEqual(bare.sections.map(row => row.name), ['Chapter 8: Inventories']);
  assert.equal(bare.basis, 'number');
  const named = selectSections(sections, 'chapter 8');
  assert.deepEqual(named.sections.map(row => row.name), ['Chapter 8: Inventories']);
  assert.equal(named.basis, 'name_substring');
  const chinese = selectSections(sections, '第8章');
  assert.deepEqual(chinese.sections.map(row => row.name), ['Chapter 8: Inventories']);
  assert.deepEqual(selectSections(sections, 'Chapter 99').sections, []);
});

test('quick classification compares ledger metadata and never claims content equality', () => {
  const item = {filename: 'Chapter 8.pdf', filesize: PDF.length, timemodified: 1000, cleanUrl: cleanUrl('Chapter 8.pdf')};
  const entry = ledgerFixture().entries['moodle-1|501|/|Chapter 8.pdf'];
  const same = classifyItem({item, entry, material: null, mode: MODES.quick});
  assert.equal(same.status, 'unchanged');
  assert.equal(same.evidence, 'metadata');
  const later = classifyItem({item: {...item, timemodified: 2000}, entry, material: null, mode: MODES.quick});
  assert.equal(later.status, 'changed');
  assert.deepEqual(later.differences, ['timemodified']);
});

test('size-only and missing baselines stay visible instead of pretending success', () => {
  const item = {filename: 'slides.pdf', filesize: 10, timemodified: null, cleanUrl: null};
  const material = {id: 'm1', course_id: 'moodle-1', bytes: 10, sha256: sha(PDF), relative_path: 'Study/moodle-1/Lectures/slides.pdf',
    moodle_file_sources: [{module_id: 9, remote_filename: 'slides.pdf', filepath: '/'}]};
  const sized = classifyItem({item, entry: null, material, mode: MODES.quick});
  assert.equal(sized.status, 'unchanged');
  assert.equal(sized.evidence, 'size');
  const missing = classifyItem({item: {...item, filesize: 11}, entry: null, material, mode: MODES.quick});
  assert.equal(missing.status, 'changed');
  assert.equal(classifyItem({item, entry: null, material: null, mode: MODES.quick}).status, 'no_baseline');
});

test('strict classification compares the content hash, not the filename', () => {
  const item = {filename: 'Chapter 8.pdf', filesize: PDF.length, timemodified: 9999, cleanUrl: null};
  const entry = ledgerFixture().entries['moodle-1|501|/|Chapter 8.pdf'];
  assert.equal(classifyItem({item, entry, material: null, mode: MODES.strict, sha256: sha(PDF)}).status, 'unchanged');
  const revised = classifyItem({item, entry, material: null, mode: MODES.strict, sha256: sha(OTHER_PDF)});
  assert.equal(revised.status, 'changed');
  assert.deepEqual(revised.differences, ['sha256']);
  assert.equal(classifyItem({item, entry, material: null, mode: MODES.strict, sha256: null}).evidence, 'content');
});

test('verdict summarises partial evidence without overclaiming', () => {
  assert.equal(verdictOf({files: 2, unchanged: 2, changed: 0, no_baseline: 0, error: 0}), 'unchanged');
  assert.equal(verdictOf({files: 2, unchanged: 1, changed: 1, no_baseline: 0, error: 0}), 'partly_changed');
  assert.equal(verdictOf({files: 1, unchanged: 0, changed: 1, no_baseline: 0, error: 0}), 'changed');
  assert.equal(verdictOf({files: 1, unchanged: 0, changed: 0, no_baseline: 1, error: 0}), 'unknown');
  assert.equal(verdictOf({files: 1, unchanged: 0, changed: 0, no_baseline: 0, error: 1}), 'incomplete');
  assert.equal(verdictOf({files: 0, unchanged: 0, changed: 0, no_baseline: 0, error: 0}), 'no_files');
});

test('course resolution accepts subject id, Moodle id and a unique name', () => {
  const state = stateFixture();
  assert.equal(resolveCoursePages(state, 'moodle-1').subject, 'moodle-1');
  assert.equal(resolveCoursePages(state, '101').pages[0].moodleCourseid, 101);
  assert.equal(resolveCoursePages(state, 'acct1101').subject, 'moodle-1');
  assert.throws(() => resolveCoursePages(state, 'missing'), /没有找到课程/);
});

test('quick check reports changed chapter files and writes a receipt', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const state = stateFixture();
    const client = fakeClient();
    const ledgerPath = join(root, 'ledger.json');
    const withoutBaseline = await checkChapter({
      client, root, state, course: 'moodle-1', section: '8', mode: 'quick',
      ledgerPath: join(root, 'missing-ledger.json'), persist: false,
    });
    // Without a ledger or registered material there is no baseline to compare.
    assert.equal(withoutBaseline.summary.files, 2);
    assert.equal(withoutBaseline.summary.no_baseline, 2);
    assert.equal(withoutBaseline.verdict, 'unknown');

    writeFileSync(ledgerPath, JSON.stringify(ledgerFixture()), 'utf8');
    const receipt = await checkChapter({
      client, root, state, course: 'moodle-1', section: '8', mode: 'quick', ledgerPath,
    });
    assert.equal(receipt.summary.changed, 1);
    assert.equal(receipt.summary.unchanged, 1);
    assert.equal(receipt.verdict, 'partly_changed');
    assert.deepEqual(receipt.files.map(row => row.filename).sort(),
      ['Chapter 8 handout.pdf', 'Chapter 8.pdf']);
    assert.equal(receipt.request.sections_matched.length, 1);
    assert.match(receipt.scope_note, /公告、作业要求、论坛、日历和其他章节未检查/);
    assert(receipt.receipt_path && existsSync(receipt.receipt_path));
    const written = JSON.parse(readFileSync(receipt.receipt_path, 'utf8'));
    assert.equal(written.schema, 1);
    assert.equal(written.mode, 'quick');
    assert.deepEqual(written.account_scope, {site: BASE, user_id: 7});
    assert(!/secret-token/.test(readFileSync(receipt.receipt_path, 'utf8')));
  } finally {
    cleanup();
  }
});

test('repeated same-school chapter checks rewrite the account-bound receipt', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const state = stateFixture();
    const ledgerPath = join(root, 'ledger.json');
    writeFileSync(ledgerPath, JSON.stringify(ledgerFixture()), 'utf8');
    const first = await checkChapter({client: fakeClient(), root, state, course: 'moodle-1',
      section: '8', mode: 'quick', ledgerPath});
    const second = await checkChapter({client: fakeClient(), root, state, course: 'moodle-1',
      section: '8', mode: 'quick', ledgerPath});
    assert.equal(first.receipt_path, second.receipt_path);
    assert.notEqual(first.run_id, second.run_id);
    const written = JSON.parse(readFileSync(second.receipt_path, 'utf8'));
    assert.deepEqual(written.account_scope, {site: BASE, user_id: 7});
    assert.equal(written.run_id, second.run_id);
    assert.equal(written.summary.unchanged, 1);
  } finally {
    cleanup();
  }
});

test('another school with the same numeric course/module IDs never reuses old baselines', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const state = stateFixture();
    const ledgerPath = join(root, 'ledger.json');
    writeFileSync(ledgerPath, JSON.stringify(ledgerFixture()), 'utf8');
    const ledgerBefore = readFileSync(ledgerPath);
    const other = fakeClient();
    other.baseUrl = 'https://other.example/moodle';
    other.userId = 18;
    await assert.rejects(() => checkChapter({
      client: other, root, state, course: 'moodle-1', section: '8', mode: 'quick', ledgerPath,
    }), /另一所学校/);
    // Fail before any catalog read, hash, or receipt write.
    assert.deepEqual(other.calls, []);
    assert.deepEqual(readFileSync(ledgerPath), ledgerBefore);
    assert.equal(existsSync(join(root, 'Study', '_System', 'ChapterChecks')), false);
    // The same school and account still checks normally.
    const allowed = await checkChapter({
      client: fakeClient(), root, state, course: 'moodle-1', section: '8', mode: 'quick',
      ledgerPath, persist: false,
    });
    assert.equal(allowed.summary.files, 2);
  } finally {
    cleanup();
  }
});


test('strict check hashes chapter bytes and rejects unlicensed downloads', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const state = stateFixture();
    const ledgerPath = join(root, 'ledger.json');
    writeFileSync(ledgerPath, JSON.stringify(ledgerFixture()), 'utf8');
    const same = await checkChapter({
      client: fakeClient({bytes: PDF}), root, state, course: 'moodle-1', section: '8', mode: 'strict',
      ledgerPath, fetchFile: (client, file) => ({bytes: PDF}), persist: false,
    });
    assert.equal(same.summary.unchanged, 2);
    assert.equal(same.verdict, 'unchanged');
    assert.equal(same.files[0].evidence, 'content');
    assert.equal(same.files[0].content_sha256, sha(PDF));
    assert.deepEqual(same.files[0].differences, []);

    const revised = await checkChapter({
      client: fakeClient(), root, state, course: 'moodle-1', section: '8', mode: 'strict',
      ledgerPath, fetchFile: () => ({bytes: OTHER_PDF}), persist: false,
    });
    assert.equal(revised.summary.changed, 2);
    assert.equal(revised.verdict, 'changed');

    const failing = await checkChapter({
      client: fakeClient(), root, state, course: 'moodle-1', section: '8', mode: 'strict',
      ledgerPath, fetchFile: () => { throw new Error('Course file download failed'); }, persist: false,
    });
    assert.equal(failing.summary.error, 2);
    assert.equal(failing.verdict, 'incomplete');

    const infoOnly = stateFixture({settings: {
      profile_account_scope: {schema: 1, site: BASE, user_id: 7},
      course_library_authorization: {schema: 1, courses: {
        'moodle-1': {moodle_courseid: 101, mode: 'info_only'},
      }}}});
    await assert.rejects(() => checkChapter({
      client: fakeClient(), root, state: infoOnly, course: 'moodle-1', section: '8', mode: 'strict',
      ledgerPath, fetchFile: (client, file) => ({bytes: PDF}), persist: false,
    }), /完整资料授权/);

    // Older archives have no ledger row; the registered material hash is the baseline.
    const legacyState = stateFixture({materials: [{id: 'm1', course_id: 'moodle-1', bytes: PDF.length,
      sha256: sha(PDF), relative_path: 'Study/moodle-1/Lectures/Chapter 8.pdf',
      moodle_file_sources: [{module_id: 501, remote_filename: 'Chapter 8.pdf', filepath: '/'}]}]});
    const legacy = await checkChapter({
      client: fakeClient(), root, state: legacyState, course: 'moodle-1', section: '8', mode: 'strict',
      ledgerPath: join(root, 'no-ledger.json'), fetchFile: () => ({bytes: PDF}), persist: false,
    });
    assert.equal(legacy.files.find(row => row.module_id === 501).status, 'unchanged');
    assert.equal(legacy.files.find(row => row.module_id === 502).status, 'no_baseline');
  } finally {
    cleanup();
  }
});

test('receipts stay inside Study even with traversal-like labels', () => {
  const {root, cleanup} = tempRoot();
  try {
    const {folder, path} = receiptPath(root, '../..', '../../Chapter 8');
    assert(folder.startsWith(join(root, 'Study')));
    assert(path.endsWith('.json'));
    const receipt = {run_id: 'a'.repeat(32), course: {course_id: '../moodle-1'}, request: {label: '../../x'}};
    const written = writeReceipt(root, receipt);
    assert(written.startsWith(join(root, 'Study')), written);
    assert(JSON.parse(readFileSync(written, 'utf8')).run_id === receipt.run_id);
  } finally {
    cleanup();
  }
});

test('a missing section lists what the course actually has', async () => {
  const {root, cleanup} = tempRoot();
  try {
    await assert.rejects(() => checkChapter({
      client: fakeClient(), root, state: stateFixture(), course: 'moodle-1', section: 'Chapter 99',
      mode: 'quick', persist: false,
    }), /可用的章节：General、Chapter 8: Inventories、Chapter 18: Review/);
  } finally {
    cleanup();
  }
});

test('module filter checks one file without needing the section name', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const receipt = await checkChapter({
      client: fakeClient(), root, state: stateFixture(), course: '101', modules: [501], mode: 'quick',
      persist: false,
    });
    assert.equal(receipt.summary.files, 1);
    assert.equal(receipt.files[0].module_id, 501);
  } finally {
    cleanup();
  }
});

test('an unreadable course page never yields an unchanged verdict', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const state = stateFixture({courses: [{id: 'moodle-1', name: 'ACCT1101 Accounting', shortname: 'ACCT1101',
      moodle_course_ids: [101, 202]}]});
    const ledgerPath = join(root, 'ledger.json');
    writeFileSync(ledgerPath, JSON.stringify(ledgerFixture()), 'utf8');
    const receipt = await checkChapter({
      client: fakeClient({failFor: [202]}), root, state, course: 'moodle-1', section: '8', mode: 'quick',
      ledgerPath, persist: false,
    });
    assert.equal(receipt.errors.length, 1);
    assert.equal(receipt.errors[0].moodle_courseid, 202);
    assert.equal(receipt.verdict, 'incomplete');
    assert.match(receipt.limitations.at(-1), /未能核对/);
  } finally {
    cleanup();
  }
});

test('material sources are keyed exactly like planned file items', () => {
  const state = stateFixture({materials: [{id: 'm1', course_id: 'moodle-1', bytes: PDF.length, sha256: sha(PDF),
    relative_path: 'Study/moodle-1/Lectures/Chapter 8.pdf',
    moodle_file_sources: [{module_id: 501, remote_filename: 'Chapter 8.pdf', filepath: '/'}]}]});
  const page = {courseId: 'moodle-1', moodleCourseid: 101, subject: 'moodle-1', baseUrl: BASE,
    course: state.courses[0]};
  const enumeratedSections = [{name: 'Chapter 8: Inventories', index: 1, number: 8, summary: '',
    uservisible: true, modules: [{id: 501, name: 'Chapter 8 slides', modname: 'resource', instance: 501,
      url: `${BASE}/mod/resource/view.php?id=501`, visible: 1, uservisible: true,
      section: null,
      contents: [{type: 'file', filename: 'Chapter 8.pdf', filepath: '/', filesize: PDF.length, timemodified: 1000,
        fileurl: `${BASE}/webservice/pluginfile.php/1/mod_resource/content/1/${encodeURIComponent('Chapter 8.pdf')}`}]}]}];
  for (const section of enumeratedSections) for (const module of section.modules) module.section = section;
  const {files} = collectSectionItems({page, sections: enumeratedSections});
  assert.equal(files.length, 1);
  const material = materialSources(state).get(files[0].key);
  assert.equal(material?.id, 'm1');
  assert.equal(resolveBaseline({entry: null, material}).source, 'material');
  assert.equal(summarize([]).files, 0);
});
