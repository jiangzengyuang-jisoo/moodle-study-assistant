import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {
  CHAPTER_CHECK_SCHEMA, chapterIntent, chapterTokens, checkChapter, classifyItem, collectSectionItems,
  connectionGeneration, materialSources, MODES, receiptPath, resolveBaseline, resolveCatalogScope,
  resolveChapterLocal, resolveCoursePages, runChapterCheck, selectSections, summarize,
  validateReceiptReuse, verdictOf, writeReceipt,
} from '../tools/moodle/chapter-check.mjs';
import {webserviceFileURL} from '../tools/moodle/course-files.mjs';

const BASE = 'https://school.example/moodle';
const TOPIC5 = Buffer.from('%PDF-1.4\nfictional Topic 5 slides');
const TOPIC5B = Buffer.from('%PDF-1.4\nfictional Topic 5 handout');
const TOPIC5_REVISED = Buffer.from('%PDF-1.4\nfictional Topic 5 slides, revised');
const PDF = Buffer.from('%PDF-1.4\nfictional Chapter 8 slides');
const OTHER_PDF = Buffer.from('%PDF-1.4\nfictional Chapter 8 slides, revised');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function stateFixture({materials = [], courses = null} = {}) {
  return {
    revision: 1,
    lessons: [],
    settings: {
      profile_account_scope: {schema: 1, site: BASE, user_id: 7, bound_at: '2026-11-01T00:00:00Z'},
      course_library_authorization: {schema: 1, scope_version: 1, scope: 'test', courses: {
        'moodle-1': {moodle_courseid: 101, mode: 'full', scope_version: 1,
          account_scope: {site: BASE, user_id: 7}},
      }},
    },
    courses: courses ?? [{id: 'moodle-1', name: 'ECON1220 Economics', shortname: 'ECON1220',
      moodle_course_ids: [101]}],
    materials,
  };
}

function resourceModule(id, name, filename, {filesize = TOPIC5.length, timemodified = 1000,
  modname = 'resource', contents = null} = {}) {
  const fileurl = `${BASE}/webservice/pluginfile.php/1/mod_resource/content/1/${encodeURIComponent(filename)}`;
  return {
    id, name, modname, instance: id, url: `${BASE}/mod/${modname}/view.php?id=${id}`,
    visible: 1, uservisible: true,
    contents: contents ?? [{type: 'file', filename, filepath: '/', filesize, timemodified, fileurl}],
  };
}

function econSections({extraLecture = []} = {}) {
  return [
    {id: 1, section: 0, name: 'General', summary: '', uservisible: true, modules: []},
    {id: 2, section: 5, name: 'Lecture Slides & Notes', summary: '', uservisible: true, modules: [
      resourceModule(4243574, 'Topic1a', 'Topic1a.pdf'),
      resourceModule(4309760, 'Topic5', 'Topic5.pdf'),
      resourceModule(4330274, 'Topic6', 'Topic6.pdf'),
      ...extraLecture,
    ]},
    {id: 3, section: 11, name: 'Tutorial 3 (Oct 5 - Oct 9)', summary: '', uservisible: true, modules: [
      resourceModule(4304158, 'ECON1220ABC tutorial 4', 'ECON1220ABC tutorial 4 .pdf'),
    ]},
    {id: 4, section: 14, name: 'Tutorial 5 (Nov 9 - Nov 13)', summary: '', uservisible: true, modules: [
      resourceModule(4348785, 'ECON1220ABC tutorial 6', 'ECON1220ABC tutorial 6.pdf'),
    ]},
  ];
}

function accountingSections() {
  return [
    {id: 1, section: 0, name: 'General', summary: '', uservisible: true, modules: []},
    {id: 2, section: 8, name: 'Chapter 8: Inventories', summary: '', uservisible: true, modules: [
      resourceModule(501, 'Chapter 8 slides', 'Chapter 8.pdf', {filesize: PDF.length}),
      resourceModule(502, 'Chapter 8 handout', 'Chapter 8 handout.pdf', {filesize: PDF.length}),
    ]},
    {id: 3, section: 18, name: 'Chapter 18: Review', summary: '', uservisible: true, modules: [
      resourceModule(503, 'Chapter 18 slides', 'Chapter 18.pdf', {filesize: PDF.length}),
    ]},
  ];
}

function cloneEnumerate(template) {
  return async (client, page) => {
    const sections = template.map((section, index) => ({...section,
      index: Number.isInteger(section.index) ? section.index : index,
      modules: (section.modules ?? []).map(module => ({...module, section: null}))}));
    for (const section of sections) for (const module of section.modules) module.section = section;
    return {page, sections, modules: sections.flatMap(section => section.modules)};
  };
}

function fakeNetwork({sections = econSections(), bytes = TOPIC5, failContents = false,
  failFor = null} = {}) {
  const fetchCalls = [];
  const contentsCalls = [];
  const enumerate = async (client, page) => {
    contentsCalls.push(page.moodleCourseid);
    if (failContents || (failFor && failFor.includes(page.moodleCourseid))) throw new Error('network down');
    return cloneEnumerate(sections)(client, page);
  };
  const fetchFile = async (client, file) => {
    fetchCalls.push(file.filename);
    return {bytes};
  };
  return {enumerate, fetchFile, fetchCalls, contentsCalls};
}

function fakeClient({userId = 7, baseUrl = BASE} = {}) {
  const calls = [];
  return {
    baseUrl, userId, token: 'secret-token', calls,
    async call(method, args) {
      calls.push({method, args});
      throw new Error(`unexpected call ${method}`);
    },
  };
}

function tempRoot() {
  const root = mkdtempSync(join(tmpdir(), 'chapter-check-'));
  // Every temporary profile gets stable synthetic connection files. Receipt
  // reuse is bound to the saved config+credential generation, so tests that do
  // not intend to change the login must keep these bytes unchanged.
  mkdirSync(join(root, '.private'), {recursive: true});
  writeFileSync(join(root, 'config.local.json'),
    JSON.stringify({moodle_url: BASE, timezone: 'Asia/Hong_Kong'}, null, 2));
  writeFileSync(join(root, '.private/moodle-token.dpapi'), Buffer.from('SYNTHETIC-ENCRYPTED-AUTHORIZATION'));
  return {root, cleanup: () => rmSync(root, {recursive: true, force: true})};
}

function writeStudyFile(root, relativePath, bytes) {
  const target = join(root, 'Study', relativePath);
  mkdirSync(dirname(target), {recursive: true});
  writeFileSync(target, bytes);
  return target;
}

function materialFixture({moduleId, filename, title = filename, bytes, sha256 = sha(bytes),
  section = 'Lecture Slides & Notes', sectionIndex = 5, moduleName = title,
  relativePath = null, category = 'Lectures'}) {
  const path = relativePath ?? `moodle-1/${category}/${filename}`;
  return {
    id: `moodle-1-SOURCE-${moduleId}-fixture`,
    course_id: 'moodle-1', filename, title, category, kind: 'course file',
    relative_path: path, sha256, bytes: bytes.length, version_status: 'current', superseded_by: null,
    moodle_file_sources: [{course_id: 'moodle-1', module_id: moduleId, filepath: '/',
      remote_filename: filename, url: `${BASE}/webservice/pluginfile.php/1/mod_resource/content/1/${encodeURIComponent(filename)}?forcedownload=1`}],
    source_pages: [{course_id: 'moodle-1', page_course_id: 'moodle-1', moodle_courseid: 101,
      page_url: `${BASE}/course/view.php?id=101`, page_name: 'ECON1220',
      section, section_index: sectionIndex, module_id: moduleId, module_name: moduleName,
      module_type: 'resource'}],
  };
}

function topic5Material({bytes = TOPIC5} = {}) {
  return materialFixture({moduleId: 4309760, filename: 'Topic5.pdf', title: 'Topic5',
    moduleName: 'Topic5', bytes});
}

function topic5bMaterial({bytes = TOPIC5B} = {}) {
  return materialFixture({moduleId: 4309761, filename: 'Topic5b.pdf', title: 'Topic5b',
    moduleName: 'Topic5b', bytes});
}

function tutorial5Material() {
  return materialFixture({moduleId: 4335109, filename: 'ECON1220ABC tutorial 5 .pdf',
    title: 'ECON1220ABC tutorial 5', moduleName: 'ECON1220ABC tutorial 5',
    section: 'Tutorial 4 (Nov 2 - Nov 6)', sectionIndex: 13, category: 'Tutorials', bytes: PDF});
}

function tutorial3Material() {
  return materialFixture({moduleId: 4304158, filename: 'ECON1220ABC tutorial 4 .pdf',
    title: 'ECON1220ABC tutorial 4', moduleName: 'ECON1220ABC tutorial 4',
    section: 'Tutorial 3 (Oct 5 - Oct 9)', sectionIndex: 11, category: 'Tutorials', bytes: PDF});
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

function cleanUrl(filename) {
  return webserviceFileURL(BASE, `${BASE}/webservice/pluginfile.php/1/mod_resource/content/1/${encodeURIComponent(filename)}`)
    .toString();
}

// A tiny harness around the unified command: counts connections and file fetches.
function harness({root, state, sections = econSections(), bytes = TOPIC5, userId = 7,
  failContents = false, failFor = null}) {
  const network = fakeNetwork({sections, bytes, failContents, failFor});
  const client = fakeClient({userId});
  let connections = 0;
  return {
    network, client,
    connections: () => connections,
    run(overrides = {}) {
      return runChapterCheck({
        root, loadState: () => state, course: 'moodle-1', section: overrides.section ?? 'Topic 5',
        modules: overrides.modules ?? null, mode: overrides.mode ?? 'strict',
        chatKey: overrides.chatKey === undefined ? 'chat-a' : overrides.chatKey,
        force: overrides.force ?? false,
        persist: overrides.persist ?? true,
        connect: async () => { connections += 1; return client; },
        enumerate: network.enumerate,
        fetchFile: network.fetchFile,
        progress: overrides.progress ?? (() => {}),
        now: overrides.now ?? (() => '2026-11-08T00:00:00.000Z'),
        ledgerPath: overrides.ledgerPath ?? join(root, 'missing-ledger.json'),
      });
    },
  };
}

// ---------------------------------------------------------------------------
// Chapter label parsing: explicit prefixes only.
// ---------------------------------------------------------------------------

test('chapter labels require explicit prefixes; dates and section positions are ignored', () => {
  assert.deepEqual(chapterTokens('Topic 5'), [{family: 'topic', number: 5, raw: 'topic 5', index: 0}]);
  assert.deepEqual(chapterTokens('Topic15.pdf').map(token => token.number), [15]);
  assert.deepEqual(chapterTokens('第8章 存货').map(token => [token.family, token.number]), [['chapter', 8]]);
  // A date inside a tutorial section is not a chapter number.
  assert.deepEqual(chapterTokens('Tutorial 3 (Oct 5 - Oct 9)').map(token => [token.family, token.number]),
    [['tutorial', 3]]);
  assert.deepEqual(chapterTokens('Lecture Slides & Notes'), []);
  assert.deepEqual(chapterTokens('2026-10-05'), []);
});

test('chapterIntent distinguishes topic, tutorial, bare numbers and dates', () => {
  assert.deepEqual([chapterIntent('Topic 5').kind, chapterIntent('Topic 5').number], ['topic', 5]);
  assert.deepEqual([chapterIntent('topic5').kind, chapterIntent('topic5').number], ['topic', 5]);
  assert.deepEqual([chapterIntent('第8章').kind, chapterIntent('第8章').number], ['chapter', 8]);
  assert.deepEqual([chapterIntent('Tutorial 3').kind, chapterIntent('Tutorial 3').number], ['tutorial', 3]);
  assert.equal(chapterIntent('5').kind, 'bare');
  assert.equal(chapterIntent('Oct 5').kind, 'name');
  assert.equal(chapterIntent('Oct 5').date_like, true);
  assert.equal(chapterIntent('Inventories').kind, 'name');
  assert.equal(chapterIntent('Chapter 8: Inventories').kind, 'chapter');
});

test('selectSections matches names only and never uses the Moodle section number', () => {
  const sections = [
    {name: 'Lecture Slides & Notes', index: 5, number: 5},
    {name: 'Chapter 8: Inventories', index: 1, number: 8},
    {name: 'Tutorial 3 (Oct 5 - Oct 9)', index: 11, number: 11},
    {name: 'Tutorial 5 (Nov 9 - Nov 13)', index: 14, number: 14},
  ];
  assert.deepEqual(selectSections(sections, 'Chapter 8: Inventories').sections.map(row => row.name),
    ['Chapter 8: Inventories']);
  assert.deepEqual(selectSections(sections, '8').sections, []);
  assert.deepEqual(selectSections(sections, 'Inventories').sections.map(row => row.name),
    ['Chapter 8: Inventories']);
  const ambiguous = selectSections(sections, 'Tutorial');
  assert.equal(ambiguous.basis, 'name_substring_ambiguous');
  assert.equal(ambiguous.sections.length, 2);
});

// ---------------------------------------------------------------------------
// Local scope resolution: the material index decides the chapter modules.
// ---------------------------------------------------------------------------

test('local index resolves Topic 5 to its own module only', () => {
  const {root, cleanup} = tempRoot();
  try {
    const topic5 = topic5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    const state = stateFixture({materials: [topic5, tutorial3Material(), tutorial5Material()]});
    const plan = resolveChapterLocal({state, root, course: 'moodle-1', section: 'Topic 5'});
    assert.equal(plan.status, 'resolved');
    assert.equal(plan.basis, 'local_materials');
    assert.deepEqual(plan.moduleIds, [4309760]);
    assert.deepEqual(plan.files.map(file => file.filename), ['Topic5.pdf']);
    assert.equal(plan.files[0].sha256, sha(TOPIC5));
    // "Tutorial 3 (Oct 5 - Oct 9)" and the whole lecture block are not selected.
    assert(!plan.moduleIds.includes(4304158));
    assert(!plan.moduleIds.includes(4335109));
    assert.deepEqual(plan.sections.map(section => section.name), ['Lecture Slides & Notes']);
  } finally {
    cleanup();
  }
});

test('a bare number returns candidates instead of selecting a whole block', () => {
  const {root, cleanup} = tempRoot();
  try {
    const topic5 = topic5Material();
    const tutorial5 = tutorial5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    writeStudyFile(root, tutorial5.relative_path, PDF);
    const state = stateFixture({materials: [topic5, tutorial3Material(), tutorial5]});
    const plan = resolveChapterLocal({state, root, course: 'moodle-1', section: '5'});
    assert.equal(plan.status, 'ambiguous');
    assert.deepEqual(plan.candidates.map(candidate => candidate.module_id).sort(), [4309760, 4335109]);
    assert(plan.candidates.some(candidate => candidate.reasons.some(reason => /topic/i.test(reason))));
    assert(plan.candidates.some(candidate => candidate.reasons.some(reason => /tutorial/i.test(reason))));
    assert.deepEqual(plan.moduleIds ?? [], []);
  } finally {
    cleanup();
  }
});

test('a mismatched tutorial filename and section returns candidates with reasons', () => {
  const {root, cleanup} = tempRoot();
  try {
    const tutorial3 = tutorial3Material();
    writeStudyFile(root, tutorial3.relative_path, PDF);
    const state = stateFixture({materials: [tutorial3]});
    const plan = resolveChapterLocal({state, root, course: 'moodle-1', section: 'Tutorial 3'});
    assert.equal(plan.status, 'ambiguous');
    assert.match(plan.reason, /同族编号|不一致/);
    const candidate = plan.candidates.find(row => row.module_id === 4304158);
    assert(candidate);
    assert(candidate.reasons.some(reason => /tutorial 4/.test(reason)));
  } finally {
    cleanup();
  }
});

test('a date-like request is refused instead of selecting a section', () => {
  const {root, cleanup} = tempRoot();
  try {
    const tutorial3 = tutorial3Material();
    writeStudyFile(root, tutorial3.relative_path, PDF);
    const state = stateFixture({materials: [tutorial3]});
    const plan = resolveChapterLocal({state, root, course: 'moodle-1', section: 'Oct 5'});
    assert.equal(plan.status, 'none');
    assert.equal(plan.date_like, true);
  } finally {
    cleanup();
  }
});

test('name resolution does not silently select sections through local evidence', () => {
  const {root, cleanup} = tempRoot();
  try {
    const topic5 = topic5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    const state = stateFixture({materials: [topic5]});
    const plan = resolveChapterLocal({state, root, course: 'moodle-1', section: 'Lecture Slides'});
    assert.equal(plan.status, 'none');
  } finally {
    cleanup();
  }
});

// ---------------------------------------------------------------------------
// Course-catalog fallback: matches explicit labels and valid section names.
// ---------------------------------------------------------------------------

test('catalog fallback matches the module label without selecting the whole lecture block', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const state = stateFixture();
    const network = fakeNetwork({sections: econSections()});
    const receipt = await checkChapter({
      client: fakeClient(), root, state, course: 'moodle-1', section: 'Topic 5', mode: 'quick',
      enumerate: network.enumerate, fetchFile: network.fetchFile, persist: false,
    });
    assert.deepEqual(receipt.files.map(file => file.filename), ['Topic5.pdf']);
    // Only the course catalog was read; quick mode downloads nothing.
    assert.equal(network.fetchCalls.length, 0);
    assert.deepEqual(receipt.scope.module_ids, [4309760]);
    assert.equal(receipt.request.sections_matched.length, 0);
    assert.deepEqual(receipt.request.modules_matched, [4309760]);
  } finally {
    cleanup();
  }
});

test('catalog fallback resolves an explicitly named chapter section', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const state = stateFixture();
    const network = fakeNetwork({sections: accountingSections(), bytes: PDF});
    const receipt = await checkChapter({
      client: fakeClient(), root, state, course: 'moodle-1', section: 'Chapter 8', mode: 'quick',
      ledgerPath: join(root, 'missing-ledger.json'), enumerate: network.enumerate,
      fetchFile: network.fetchFile, persist: false,
    });
    assert.deepEqual(receipt.files.map(file => file.filename).sort(),
      ['Chapter 8 handout.pdf', 'Chapter 8.pdf']);
    assert.deepEqual(receipt.request.sections_matched, ['Chapter 8: Inventories']);
    assert.equal(receipt.summary.files, 2);
    assert.equal(receipt.summary.no_baseline, 2);
    assert.equal(receipt.verdict, 'unknown');
    assert.equal(receipt.files[0].evidence, 'none');
  } finally {
    cleanup();
  }
});

test('catalog bare example number never selects Chapter 18 for 8', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const state = stateFixture();
    const network = fakeNetwork({sections: accountingSections(), bytes: PDF});
    const receipt = await checkChapter({
      client: fakeClient(), root, state, course: 'moodle-1', section: '8', mode: 'quick',
      enumerate: network.enumerate, fetchFile: network.fetchFile, persist: false,
    });
    assert.deepEqual(receipt.request.sections_matched, ['Chapter 8: Inventories']);
    assert(!receipt.files.some(file => file.filename.includes('18')));
  } finally {
    cleanup();
  }
});

test('catalog ambiguity returns candidates and does not silently widen', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const sections = [
      {id: 1, section: 0, name: 'General', summary: '', uservisible: true, modules: []},
      {id: 2, section: 5, name: 'Chapter 5: Markets', summary: '', uservisible: true, modules: [
        resourceModule(601, 'Chapter 5 slides', 'Chapter 5.pdf', {filesize: PDF.length})]},
      {id: 3, section: 6, name: 'Tutorial 5: Practice', summary: '', uservisible: true, modules: [
        resourceModule(602, 'Tutorial 5', 'Tutorial 5.pdf', {filesize: PDF.length})]},
    ];
    const network = fakeNetwork({sections, bytes: PDF});
    const receipt = await checkChapter({
      client: fakeClient(), root, state: stateFixture(), course: 'moodle-1', section: '5',
      mode: 'strict', enumerate: network.enumerate, fetchFile: network.fetchFile, persist: false,
    });
    assert.equal(receipt.verdict, 'ambiguous');
    assert.equal(receipt.verified.complete, false);
    assert.deepEqual(receipt.resolution.candidates.map(candidate => candidate.section).sort(),
      ['Chapter 5: Markets', 'Tutorial 5: Practice']);
    assert.equal(receipt.files.length, 0);
    assert.equal(network.fetchCalls.length, 0);
  } finally {
    cleanup();
  }
});

// ---------------------------------------------------------------------------
// Pure classification and receipt guards (kept from the narrow-check contract).
// ---------------------------------------------------------------------------

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

test('strict classification compares content hashes and partial evidence stays visible', () => {
  const item = {filename: 'Chapter 8.pdf', filesize: PDF.length, timemodified: 9999, cleanUrl: null};
  const entry = ledgerFixture().entries['moodle-1|501|/|Chapter 8.pdf'];
  assert.equal(classifyItem({item, entry, material: null, mode: MODES.strict, sha256: sha(PDF)}).status, 'unchanged');
  const revised = classifyItem({item, entry, material: null, mode: MODES.strict, sha256: sha(OTHER_PDF)});
  assert.equal(revised.status, 'changed');
  assert.deepEqual(revised.differences, ['sha256']);
  const unhashed = classifyItem({item, entry, material: null, mode: MODES.strict, sha256: null});
  assert.equal(unhashed.evidence, 'content');
  assert.equal(unhashed.status, 'changed');
});

test('verdict summarises partial evidence without overclaiming', () => {
  assert.equal(verdictOf({files: 2, unchanged: 2, changed: 0, no_baseline: 0, error: 0}), 'unchanged');
  assert.equal(verdictOf({files: 2, unchanged: 1, changed: 1, no_baseline: 0, error: 0}), 'partly_changed');
  assert.equal(verdictOf({files: 1, unchanged: 0, changed: 1, no_baseline: 0, error: 0}), 'changed');
  assert.equal(verdictOf({files: 1, unchanged: 0, changed: 0, no_baseline: 1, error: 0}), 'unknown');
  assert.equal(verdictOf({files: 1, unchanged: 0, changed: 0, no_baseline: 0, error: 1}), 'incomplete');
  assert.equal(verdictOf({files: 0, unchanged: 0, changed: 0, no_baseline: 0, error: 0}), 'no_files');
  assert.equal(summarize([]).files, 0);
});

test('course resolution accepts subject id, Moodle id and a unique name', () => {
  const state = stateFixture();
  assert.equal(resolveCoursePages(state, 'moodle-1').subject, 'moodle-1');
  assert.equal(resolveCoursePages(state, '101').pages[0].moodleCourseid, 101);
  assert.equal(resolveCoursePages(state, 'econ1220').subject, 'moodle-1');
  assert.throws(() => resolveCoursePages(state, 'missing'), /没有找到课程/);
});

test('quick chapter check reports changed chapter files and writes a scan receipt', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const ledgerPath = join(root, 'ledger.json');
    writeFileSync(ledgerPath, JSON.stringify(ledgerFixture()), 'utf8');
    const network = fakeNetwork({sections: accountingSections(), bytes: PDF});
    const receipt = await checkChapter({
      client: fakeClient(), root, state: stateFixture(), course: 'moodle-1', section: 'Chapter 8',
      mode: 'quick', ledgerPath, enumerate: network.enumerate, fetchFile: network.fetchFile,
    });
    assert.equal(receipt.summary.changed, 1);
    assert.equal(receipt.summary.unchanged, 1);
    assert.equal(receipt.verdict, 'partly_changed');
    assert.deepEqual(receipt.files.map(row => row.filename).sort(),
      ['Chapter 8 handout.pdf', 'Chapter 8.pdf']);
    assert.deepEqual(receipt.request.sections_matched, ['Chapter 8: Inventories']);
    assert.match(receipt.scope_note, /公告、作业要求、论坛、日历和其他章节未检查/);
    assert.equal(receipt.schema, CHAPTER_CHECK_SCHEMA);
    assert.equal(receipt.verified.complete, false);
    assert.equal(receipt.verified.reason, 'mode_not_strict');
    assert(receipt.receipt_path && existsSync(receipt.receipt_path));
    const written = JSON.parse(readFileSync(receipt.receipt_path, 'utf8'));
    assert.equal(written.schema, 3);
    assert.match(written.connection_generation, /^[0-9a-f]{64}$/);
    assert.equal(written.mode, 'quick');
    assert.deepEqual(written.account_scope, {site: BASE, user_id: 7});
    assert.equal(written.chat, null);
    assert(!/secret-token/.test(readFileSync(receipt.receipt_path, 'utf8')));
  } finally {
    cleanup();
  }
});

test('another school with the same numeric IDs never reads baselines or writes a receipt', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const ledgerPath = join(root, 'ledger.json');
    writeFileSync(ledgerPath, JSON.stringify(ledgerFixture()), 'utf8');
    const ledgerBefore = readFileSync(ledgerPath);
    const other = fakeClient({baseUrl: 'https://other.example/moodle', userId: 18});
    await assert.rejects(() => checkChapter({
      client: other, root, state: stateFixture(), course: 'moodle-1', section: 'Chapter 8',
      mode: 'quick', ledgerPath, persist: false,
    }), /另一所学校/);
    assert.deepEqual(other.calls, []);
    assert.deepEqual(readFileSync(ledgerPath), ledgerBefore);
    assert.equal(existsSync(join(root, 'Study', '_System', 'ChapterChecks')), false);
  } finally {
    cleanup();
  }
});

test('strict check hashes only chapter files and honours authorization', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const topic5 = topic5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    const state = stateFixture({materials: [topic5]});
    const network = fakeNetwork({sections: econSections(), bytes: TOPIC5});
    const same = await checkChapter({
      client: fakeClient(), root, state, course: 'moodle-1', section: 'Topic 5', mode: 'strict',
      enumerate: network.enumerate, fetchFile: network.fetchFile, persist: false,
    });
    assert.equal(same.verdict, 'unchanged');
    assert.equal(same.verified.complete, true);
    assert.deepEqual(network.fetchCalls, ['Topic5.pdf']);
    assert.equal(same.files[0].evidence, 'content');
    assert.equal(same.files[0].content_sha256, sha(TOPIC5));

    const revisedNetwork = fakeNetwork({sections: econSections(), bytes: TOPIC5_REVISED});
    const revised = await checkChapter({
      client: fakeClient(), root, state, course: 'moodle-1', section: 'Topic 5', mode: 'strict',
      enumerate: revisedNetwork.enumerate, fetchFile: revisedNetwork.fetchFile, persist: false,
    });
    assert.equal(revised.verdict, 'changed');
    assert.match(revised.files[0].differences[0], /sha256/);

    const infoOnly = stateFixture({materials: [topic5]});
    infoOnly.settings.course_library_authorization.courses['moodle-1'].mode = 'info_only';
    await assert.rejects(() => checkChapter({
      client: fakeClient(), root, state: infoOnly, course: 'moodle-1', section: 'Topic 5', mode: 'strict',
      enumerate: network.enumerate, fetchFile: network.fetchFile, persist: false,
    }), /完整资料授权/);
  } finally {
    cleanup();
  }
});

test('chapter files that exist only in the catalog are not claimed as locally present', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const network = fakeNetwork({sections: accountingSections(), bytes: PDF});
    const receipt = await checkChapter({
      client: fakeClient(), root, state: stateFixture(), course: 'moodle-1', section: 'Chapter 8',
      mode: 'strict', enumerate: network.enumerate, fetchFile: network.fetchFile, persist: false,
    });
    assert.equal(receipt.summary.no_baseline, 2);
    assert.equal(receipt.verified.complete, false);
    assert.equal(receipt.verified.reason, 'no_baseline');
    // No baseline means no download and no false "unchanged".
    assert.equal(network.fetchCalls.length, 0);
  } finally {
    cleanup();
  }
});

test('a missing section lists what the course actually has and a date is refused', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const network = fakeNetwork({sections: accountingSections(), bytes: PDF});
    await assert.rejects(() => checkChapter({
      client: fakeClient(), root, state: stateFixture(), course: 'moodle-1', section: 'Chapter 99',
      mode: 'quick', enumerate: network.enumerate, fetchFile: network.fetchFile, persist: false,
    }), /可用的章节：General、Chapter 8: Inventories、Chapter 18: Review/);
    const dateNetwork = fakeNetwork({sections: econSections(), bytes: TOPIC5});
    await assert.rejects(() => checkChapter({
      client: fakeClient(), root, state: stateFixture(), course: 'moodle-1', section: 'Oct 5',
      mode: 'quick', enumerate: dateNetwork.enumerate, fetchFile: dateNetwork.fetchFile, persist: false,
    }), /看起来是日期/);
  } finally {
    cleanup();
  }
});

test('module filter checks one file without needing the section name', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const network = fakeNetwork({sections: accountingSections(), bytes: PDF});
    const receipt = await checkChapter({
      client: fakeClient(), root, state: stateFixture(), course: '101', modules: [501], mode: 'quick',
      enumerate: network.enumerate, fetchFile: network.fetchFile, persist: false,
    });
    assert.equal(receipt.summary.files, 1);
    assert.equal(receipt.files[0].module_id, 501);
    assert.equal(receipt.scope.basis, 'explicit_modules');
  } finally {
    cleanup();
  }
});

test('an unreadable course page never yields an unchanged verdict', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const state = stateFixture({courses: [{id: 'moodle-1', name: 'ECON1220 Economics', shortname: 'ECON1220',
      moodle_course_ids: [101, 202]}]});
    const network = fakeNetwork({sections: accountingSections(), bytes: PDF, failFor: [202]});
    const receipt = await checkChapter({
      client: fakeClient(), root, state, course: 'moodle-1', section: 'Chapter 8', mode: 'quick',
      enumerate: network.enumerate, fetchFile: network.fetchFile, persist: false,
    });
    assert.equal(receipt.errors.length, 1);
    assert.equal(receipt.errors[0].moodle_courseid, 202);
    assert.equal(receipt.verdict, 'incomplete');
    assert.match(receipt.limitations.at(-1), /未能核对/);
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

test('material sources are keyed exactly like planned file items', () => {
  const state = stateFixture({materials: [materialFixture({moduleId: 501,
    filename: 'Chapter 8.pdf', title: 'Chapter 8 slides', bytes: PDF})]});
  const page = {courseId: 'moodle-1', moodleCourseid: 101, subject: 'moodle-1', baseUrl: BASE,
    course: state.courses[0]};
  const section = {name: 'Chapter 8: Inventories', index: 1, number: 8, summary: '', uservisible: true,
    modules: [resourceModule(501, 'Chapter 8 slides', 'Chapter 8.pdf', {filesize: PDF.length})]};
  section.modules[0].section = section;
  const {files} = collectSectionItems({page, sections: [section]});
  assert.equal(files.length, 1);
  assert.equal(materialSources(state).get(files[0].key)?.id, state.materials[0].id);
  assert.equal(resolveBaseline({entry: null, material: state.materials[0]}).source, 'material');
});

// ---------------------------------------------------------------------------
// Same-chat reuse and incremental verification (no network on the fast path).
// ---------------------------------------------------------------------------

test('same chat strict receipt is reused without any network call', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const topic5 = topic5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    const state = stateFixture({materials: [topic5]});
    const kit = harness({root, state});
    const first = await kit.run();
    assert.equal(first.network_started, true);
    assert.equal(first.reused, undefined);
    assert.equal(first.verified.complete, true);
    assert.equal(kit.connections(), 1);
    assert.deepEqual(kit.network.fetchCalls, ['Topic5.pdf']);

    const second = await kit.run();
    assert.equal(second.reused, true);
    assert.equal(second.network_started, false);
    assert.equal(second.run_id, first.run_id);
    assert.equal(second.verified.complete, true);
    assert.equal(second.reuse.reason, 'same_chat_verified');
    // No new connection and no new file fetch.
    assert.equal(kit.connections(), 1);
    assert.deepEqual(kit.network.fetchCalls, ['Topic5.pdf']);
    assert.equal(JSON.parse(readFileSync(first.receipt_path, 'utf8')).run_id, first.run_id);
  } finally {
    cleanup();
  }
});

test('a changed saved credential invalidates reuse and receipts store only the connection hash', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const topic5 = topic5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    const state = stateFixture({materials: [topic5]});
    const kit = harness({root, state});
    const first = await kit.run();
    assert.equal(first.schema, 3);
    assert.equal(first.schema, CHAPTER_CHECK_SCHEMA);
    assert.match(first.connection_generation, /^[0-9a-f]{64}$/);
    const writtenText = readFileSync(first.receipt_path, 'utf8');
    const written = JSON.parse(writtenText);
    assert.equal(written.connection_generation, first.connection_generation);
    // Only the hash is stored; neither the config values nor the credential
    // bytes of the saved login may leak into the reusable receipt.
    assert(!writtenText.includes('SYNTHETIC-ENCRYPTED-AUTHORIZATION'));
    assert(!JSON.stringify(first).includes('SYNTHETIC-ENCRYPTED-AUTHORIZATION'));

    const cached = await kit.run();
    assert.equal(cached.reused, true);
    assert.equal(cached.connection_generation, first.connection_generation);
    assert.equal(kit.connections(), 1);

    // A fresh browser login replaces only the saved credential: the local
    // learning profile (still account 7) looks unchanged, so the connection
    // generation is what must invalidate the old success receipt.
    writeFileSync(join(root, '.private/moodle-token.dpapi'),
      Buffer.from('SYNTHETIC-ENCRYPTED-AUTHORIZATION-B'));
    assert.notEqual(connectionGeneration(root), first.connection_generation);
    const rechecked = await kit.run();
    assert.equal(rechecked.reused, undefined);
    assert.equal(rechecked.network_started, true);
    assert.equal(rechecked.verified.complete, true);
    assert.equal(kit.connections(), 2);
    assert.notEqual(rechecked.connection_generation, first.connection_generation);
  } finally {
    cleanup();
  }
});

test('receipt reuse requires a readable and identical connection generation', () => {
  const receipt = {
    schema: CHAPTER_CHECK_SCHEMA, mode: 'strict', chat: {key: 'chat-key'},
    connection_generation: 'a'.repeat(64), account_scope: {site: BASE, user_id: 7},
    course: {course_id: 'moodle-1', moodle_courseids: [101]}, request: {section: 'Topic 5'},
    scope: {module_ids: [], local_files: []}, verified: {complete: true},
  };
  const plan = {subject: 'moodle-1', pages: [], intent: {kind: 'topic', text: 'topic 5'}};
  const decide = overrides => validateReceiptReuse({receipt, plan, state: stateFixture(),
    chatKey: 'chat-key', mode: 'strict', ...overrides}).reason;
  assert.equal(decide({connectionGeneration: 'b'.repeat(64)}), 'connection_changed');
  assert.equal(decide({connectionGeneration: null}), 'account_unverified');
  assert.equal(decide({receipt: {...receipt, connection_generation: null},
    connectionGeneration: 'a'.repeat(64)}), 'account_unverified');
  assert.equal(decide({receipt: {...receipt, schema: 2},
    connectionGeneration: 'a'.repeat(64)}), 'no_receipt');
});

test('a new chat, a changed local file, a changed account and --force each invalidate reuse', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const topic5 = topic5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    const state = stateFixture({materials: [topic5]});
    const kit = harness({root, state});
    await kit.run();
    assert.equal(kit.connections(), 1);

    const newChat = await kit.run({chatKey: 'chat-b'});
    assert.equal(newChat.reused, undefined);
    assert.equal(kit.connections(), 2);
    assert.deepEqual(kit.network.fetchCalls, ['Topic5.pdf', 'Topic5.pdf']);

    writeStudyFile(root, topic5.relative_path, TOPIC5_REVISED);
    const changedLocal = await kit.run();
    assert.equal(changedLocal.reused, undefined);
    assert.equal(kit.connections(), 3);

    // Restore the local bytes so only the receipt/chat identity differs.
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    const forced = await kit.run({force: true});
    assert.equal(forced.reused, undefined);
    assert.equal(kit.connections(), 4);

    // Same chat again after the forced run: receipt is reusable again.
    const cachedAgain = await kit.run();
    assert.equal(cachedAgain.reused, true);
    assert.equal(kit.connections(), 4);

    // A different account cannot reuse this profile's receipt.
    const otherAccount = structuredClone(state);
    otherAccount.settings.profile_account_scope.user_id = 8;
    const otherKit = harness({root, state: otherAccount});
    await assert.rejects(() => otherKit.run(), /不同学校或账号|停止章节核对/);
    assert.equal(otherKit.connections(), 1);
  } finally {
    cleanup();
  }
});

test('new local chapter material is verified incrementally', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const topic5 = topic5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    const state = stateFixture({materials: [topic5]});
    const firstKit = harness({root, state});
    const first = await firstKit.run();
    assert.equal(first.verified.complete, true);
    assert.equal(firstKit.network.fetchCalls.length, 1);

    const topic5b = topic5bMaterial();
    writeStudyFile(root, topic5b.relative_path, TOPIC5B);
    const extended = stateFixture({materials: [topic5, topic5b]});
    const kit = harness({root, state: extended,
      sections: econSections({extraLecture: [resourceModule(4309761, 'Topic5b', 'Topic5b.pdf',
        {filesize: TOPIC5B.length})]})});
    const second = await kit.run();
    assert.equal(second.network_started, true);
    assert.equal(second.reused, undefined);
    assert.equal(second.incremental.added_module_ids.length, 1);
    assert.equal(second.summary.files, 2);
    assert.equal(second.summary.from_cache, 1);
    assert.equal(second.verified.complete, true);
    // Only the new file was downloaded, not Topic5.pdf again.
    assert.deepEqual(kit.network.fetchCalls, ['Topic5b.pdf']);
    assert.equal(kit.connections(), 1);

    const thirdKit = harness({root, state: extended,
      sections: econSections({extraLecture: [resourceModule(4309761, 'Topic5b', 'Topic5b.pdf',
        {filesize: TOPIC5B.length})]})});
    const third = await thirdKit.run();
    assert.equal(third.reused, true);
    assert.equal(thirdKit.connections(), 0);
  } finally {
    cleanup();
  }
});

test('failed or missing-baseline results are not reused as success and are not auto-retried', async () => {
  const {root, cleanup} = tempRoot();
  try {
    // No ledger/material baseline: strict cannot verify content.
    const state = stateFixture();
    const kit = harness({root, state});
    const first = await kit.run();
    assert.equal(first.verified.complete, false);
    assert.equal(first.verdict, 'unknown');
    assert.equal(kit.connections(), 1);

    const second = await kit.run();
    assert.equal(second.reused, true);
    assert.equal(second.network_started, false);
    assert.equal(second.verified.complete, false);
    assert.equal(second.reuse.reason, 'incomplete_previous');
    assert.equal(kit.connections(), 1);

    const forced = await kit.run({force: true});
    assert.equal(forced.reused, undefined);
    assert.equal(kit.connections(), 2);

    // A network/file failure is also never reported as success or retried.
    const topic5 = topic5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    const failRoot = tempRoot();
    try {
      const failState = stateFixture({materials: [topic5]});
      const failing = harness({root: failRoot.root, state: failState});
      failing.network.fetchFile = async () => { throw new Error('Course file download failed'); };
      const failed = await failing.run();
      assert.equal(failed.verdict, 'incomplete');
      assert.equal(failed.verified.complete, false);
      assert.equal(failed.summary.error, 1);
      const reused = await failing.run();
      assert.equal(reused.reused, true);
      assert.equal(reused.verified.complete, false);
      assert.equal(failing.connections(), 1);
    } finally {
      failRoot.cleanup();
    }
  } finally {
    cleanup();
  }
});

test('an ambiguous local request returns candidates before any connection', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const topic5 = topic5Material();
    const tutorial5 = tutorial5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    writeStudyFile(root, tutorial5.relative_path, PDF);
    const state = stateFixture({materials: [topic5, tutorial3Material(), tutorial5]});
    const kit = harness({root, state});
    const result = await kit.run({section: '5'});
    assert.equal(result.verdict, 'ambiguous');
    assert.equal(result.network_started, false);
    assert.equal(kit.connections(), 0);
    assert.deepEqual(result.resolution.candidates.map(candidate => candidate.module_id).sort(),
      [4309760, 4335109]);
    assert(result.resolution.candidates.every(candidate => candidate.reasons.length > 0));
    assert.equal(result.verified.complete, false);
    // The ambiguity file must not replace the reusable chapter receipt cache path.
    const {path} = receiptPath(root, 'moodle-1', '5');
    assert.equal(existsSync(path), false);
    assert(existsSync(join(root, 'Study', '_System', 'ChapterChecks', 'moodle-1', '5.ambiguous.json')));
  } finally {
    cleanup();
  }
});

test('progress stages are emitted and the final result stays structured', async () => {
  const {root, cleanup} = tempRoot();
  try {
    const topic5 = topic5Material();
    writeStudyFile(root, topic5.relative_path, TOPIC5);
    const state = stateFixture({materials: [topic5]});
    const messages = [];
    const kit = harness({root, state});
    const first = await kit.run({progress: message => messages.push(message)});
    assert(messages.some(message => /解析本章范围/.test(message)));
    assert(messages.some(message => /读取课程目录/.test(message)));
    assert(messages.some(message => /核对文件 1\/1/.test(message)));
    assert(typeof first.verdict === 'string');
    const second = await kit.run({progress: message => messages.push(message)});
    assert(messages.some(message => /复用上次回执/.test(message)));
    assert.equal(second.reused, true);
  } finally {
    cleanup();
  }
});
