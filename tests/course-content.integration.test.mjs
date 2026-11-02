import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync} from 'node:fs';
import {join, resolve, sep} from 'node:path';
import {syncCourseContent} from '../tools/moodle/course-content-sync.mjs';
import {grantCourseContent} from '../tools/moodle/content-policy.mjs';

const project = resolve(import.meta.dirname, '..');
const studyToolsDir = join(project, 'tools/study');
const python = process.env.STUDY_PYTHON || join(project, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const base = join(project, 'tmp/course-content-integration');
mkdirSync(base, {recursive: true});
const read = path => JSON.parse(readFileSync(path, 'utf8'));
const write = (path, value) => writeFileSync(path, JSON.stringify(value));
function pythonJSON(args) {
  const child = spawnSync(python, ['-B', ...args], {encoding: 'utf8', windowsHide: true,
    env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'}});
  assert.equal(child.status, 0, child.stderr || String(child.error));
  return JSON.parse(child.stdout);
}

test('actual Python installer and library CLI complete, reuse, version, retry and preserve evidence offline', async () => {
  const root = mkdtempSync(join(base, '实际 桥接-'));
  assert.ok(root.startsWith(base + sep));
  try {
    const studyRoot = join(root, 'Study'), statePath = join(studyRoot, '_System/state.json');
    mkdirSync(join(studyRoot, '_System'), {recursive: true});
    const initial = grantCourseContent({revision: 1, timezone: 'Asia/Hong_Kong', settings: {},
      courses: [{id: 'SYN', moodle_course_ids: [101], teaching_cursor: {answer: 'KEEP_LEARNING_ANSWER'}}],
      materials: [], mistakes: [], learning_feedback: []},
    {site: 'https://school.example', userId: 17, courseIds: [101], mode: 'full'});
    write(statePath, initial);
    const pdfPath = join(root, 'synthetic.pdf');
    const made = spawnSync(python, ['-B', '-c',
      'import sys; sys.path.insert(0,sys.argv[1]); from library_test_fixtures import write_pdf; write_pdf(sys.argv[2], ["PDF_SEARCHABLE_SENTINEL"])',
      studyToolsDir, pdfPath], {encoding: 'utf8', windowsHide: true});
    assert.equal(made.status, 0, made.stderr || String(made.error));
    const bytes = new Map([['lesson.txt', Buffer.from('ORIGINAL_SEARCHABLE_SENTINEL')], ['diagram.pdf', readFileSync(pdfPath)]]);
    const files = [...bytes].map(([filename, content]) => ({type: 'file', filename,
      fileurl: `https://school.example/pluginfile.php/77/mod_folder/content/1/${filename}`,
      filepath: '/', filesize: content.length, timemodified: 1}));
    const sections = [{id: 1, name: 'Lecture 1', summary: '<p>SECTION_SEARCHABLE_SENTINEL</p>',
      modules: [{id: 501, name: 'Lecture notes', modname: 'folder', uservisible: true,
        url: 'https://school.example/mod/folder/view.php?id=501', contents: files}]}];
    let downloads = 0, broken = null;
    const client = {baseUrl: 'https://school.example', userId: 17, token: 'fictional-secret-never-persisted',
      async call(method, args) {
        assert.equal(method, 'core_course_get_contents');
        assert.equal(args.courseid, 101);
        return structuredClone(sections);
      },
      async transport(url, options) {
        downloads++;
        const parsed = new URL(url);
        assert.equal(parsed.origin, 'https://school.example');
        assert.ok(parsed.pathname.startsWith('/webservice/pluginfile.php/'));
        assert.equal(options.redirect, 'error');
        const filename = decodeURIComponent(parsed.pathname.split('/').at(-1));
        if (broken === filename) throw new Error(url);
        assert.ok(bytes.has(filename));
        return new Response(bytes.get(filename), {headers: {'content-type': filename.endsWith('.pdf') ? 'application/pdf' : 'text/plain'}});
      }};
    const options = {studyRoot, statePath, studyToolsDir, python,
      snapshot: {collection_complete: true, collection_binding: {protocol: 1, run_id: 'synthetic-run', request_sha256: 'synthetic'}}};
    const sync = () => syncCourseContent(client, read(statePath), options);
    const first = await sync();
    assert.equal(first.status, 'complete', JSON.stringify(first.errors));
    assert.equal(downloads, 2);
    assert.equal(first.counts.saved_documents, 1);
    assert.equal(first.index[0].materials.length, 3);
    assert.ok(first.index[0].materials.every(row => row.status === 'indexed' && row.text_units > 0));
    let state = read(statePath);
    assert.equal(state.materials.length, 3);
    assert.equal(state.settings.material_version_policy, undefined, 'Downloading must not enable archive deletion');
    assert.equal(state.courses[0].teaching_cursor.answer, 'KEEP_LEARNING_ANSWER');
    const old = state.materials.find(row => row.filename === 'lesson.txt');
    assert.ok(old.relative_path.startsWith('SYN/Lectures/source-'));
    assert.equal(readFileSync(join(studyRoot, old.relative_path), 'utf8'), 'ORIGINAL_SEARCHABLE_SENTINEL');
    state.mistakes.push({id: 'actual-answer', course_id: 'SYN', material_id: old.id, answer: 'PRESERVE_ORIGINAL_ANSWER', page: 1});
    write(statePath, state);
    const second = await sync();
    assert.equal(second.status, 'complete');
    assert.equal(downloads, 2, 'Unchanged remote metadata and verified files must skip transfer');
    assert.equal(second.counts.reused, 2);
    const search = query => pythonJSON([join(studyToolsDir, 'library.py'), '--state', statePath, 'search', query, '--course', 'SYN']);
    assert.ok(search('PDF_SEARCHABLE_SENTINEL').results.some(row => row.material_id === state.materials.find(m => m.filename === 'diagram.pdf').id));

    const pdfMaterial = state.materials.find(row => row.filename === 'diagram.pdf');
    const installedPdf = resolve(studyRoot, pdfMaterial.relative_path);
    assert.ok(installedPdf.startsWith(resolve(studyRoot) + sep));
    unlinkSync(installedPdf);
    const restored = await sync();
    assert.equal(restored.status, 'complete', JSON.stringify(restored.errors));
    assert.equal(downloads, 3, 'A missing registered file must be fetched and restored through the real installer');
    const restoredPdf = read(statePath).materials.find(row => row.id === pdfMaterial.id);
    assert.equal(restoredPdf.relative_path, pdfMaterial.relative_path);
    assert.equal(restoredPdf.sha256, pdfMaterial.sha256);
    assert.deepEqual(readFileSync(installedPdf), bytes.get('diagram.pdf'));
    assert.ok(search('PDF_SEARCHABLE_SENTINEL').results.some(row => row.material_id === pdfMaterial.id));

    bytes.set('lesson.txt', Buffer.from('UPDATED_SEARCHABLE_SENTINEL'));
    files[0].filesize = bytes.get('lesson.txt').length;
    files[0].timemodified = 2;
    bytes.set('new.txt', Buffer.from('NEW_FILE_SEARCHABLE_SENTINEL'));
    files.push({type: 'file', filename: 'new.txt', fileurl: 'https://school.example/pluginfile.php/77/mod_folder/content/1/new.txt',
      filepath: '/', filesize: bytes.get('new.txt').length, timemodified: 2});
    broken = 'new.txt';
    const partial = await sync();
    assert.equal(partial.status, 'partial');
    assert.equal(partial.counts.failed, 1);
    assert.ok(!JSON.stringify(partial).includes(client.token));
    state = read(statePath);
    const historical = state.materials.find(row => row.id === old.id);
    assert.equal(historical.version_status, 'archived');
    assert.ok(historical.relative_path.includes('/_Archive/'));
    assert.equal(readFileSync(join(studyRoot, historical.relative_path), 'utf8'), 'ORIGINAL_SEARCHABLE_SENTINEL');
    assert.deepEqual(state.mistakes[0], {id: 'actual-answer', course_id: 'SYN', material_id: old.id, answer: 'PRESERVE_ORIGINAL_ANSWER', page: 1});
    assert.ok(search('ORIGINAL_SEARCHABLE_SENTINEL').results.every(row => row.material_id !== old.id));
    const newest = state.materials.find(row => row.filename === 'lesson.txt' && !row.superseded_by);
    assert.ok(search('UPDATED_SEARCHABLE_SENTINEL').results.some(row => row.material_id === newest.id));
    const beforeRetry = downloads;
    broken = null;
    const retried = await sync();
    assert.equal(retried.status, 'complete', JSON.stringify(retried.errors));
    assert.equal(downloads, beforeRetry + 1, 'Retry must fetch the failed new file only');
    assert.ok(search('NEW_FILE_SEARCHABLE_SENTINEL').results.length > 0);
    const report = read(join(studyRoot, '_System/CourseContent/latest.json'));
    assert.equal(report.status, 'complete');
    assert.equal(report.reviewed, false);
    assert.equal(report.mastery_updated, false);
    assert.ok(existsSync(join(studyRoot, '_System/Library/catalog.json')));
    assert.ok(!readFileSync(statePath, 'utf8').includes(client.token));

    const staleState = read(statePath);
    const revoked = structuredClone(staleState);
    revoked.settings.course_content_sync.grants[0].mode = 'metadata';
    write(statePath, revoked);
    const beforeRevocationCheck = downloads;
    await syncCourseContent(client, staleState, options);
    assert.equal(downloads, beforeRevocationCheck, 'Live revocation overrides the caller snapshot');
    assert.deepEqual(read(statePath).materials, revoked.materials);
  } finally {
    // root is created directly under the fixed, repository-local test folder.
    assert.ok(resolve(root).startsWith(resolve(base) + sep));
    rmSync(root, {recursive: true, force: true});
  }
});
