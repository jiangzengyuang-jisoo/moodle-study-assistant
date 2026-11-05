import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync, mkdirSync, readFileSync, writeFileSync, renameSync, rmSync, realpathSync} from 'node:fs';
import {join, resolve, sep} from 'node:path';
import {verifyChapter, readCachedChapterCheck} from '../tools/moodle/chapter-verify.mjs';
import {grantCourseContent} from '../tools/moodle/content-policy.mjs';

const site = 'https://school.example';
const sha = value => createHash('sha256').update(value).digest('hex');
const read = path => JSON.parse(readFileSync(path, 'utf8'));
const write = (path, data) => writeFileSync(path, JSON.stringify(data));
function fixture(t) {
  const parent = realpathSync(import.meta.dirname), root = mkdtempSync(join(parent, '.chapter-test-'));
  t.after(() => {assert.ok(resolve(root).startsWith(parent + sep)); rmSync(root, {recursive: true, force: true});});
  mkdirSync(join(root, '_System'), {recursive: true}); mkdirSync(join(root, 'TEST/Lectures'), {recursive: true});
  const statePath = join(root, '_System/state.json');
  const bytes = new Map([['Chapter 8 A.txt', Buffer.from('AAAA')], ['Chapter 8 B.txt', Buffer.from('BBBB')], ['Chapter 9.txt', Buffer.from('9999')]]);
  const modules = [...bytes].map(([filename, data], index) => ({id: 500 + index, name: filename, modname: 'resource', visible: 1,
    url: site + '/mod/resource/view.php?id=' + (500 + index), contents: [{type: 'file', filename, filepath: '/',
      fileurl: site + '/pluginfile.php/77/mod_resource/content/1/' + encodeURIComponent(filename), filesize: data.length, timemodified: 11}]}));
  const state = grantCourseContent({revision: 1, courses: [{id: 'TEST', moodle_course_ids: [10]}, {id: 'OTHER', moodle_course_ids: [20]}],
    settings: {}, materials: [...bytes].map(([filename, data], index) => ({id: 'M' + index, course_id: 'TEST', filename,
      category: 'Lectures', sha256: sha(data), relative_path: 'TEST/Lectures/' + filename,
      moodle_file_sources: [{module_id: 500 + index, url: modules[index].contents[0].fileurl, activity_url: modules[index].url}]})),
    mistakes: [{answer: 'REAL_ANSWER'}], teaching_cursor: {chapter: 1}},
  {site, userId: 7, courseIds: [10, 20], mode: 'full'});
  state.materials.forEach(row => writeFileSync(join(root, row.relative_path), bytes.get(row.filename)));
  write(statePath, state);
  const calls = [], downloads = [], indexes = [], installs = [];
  const client = {baseUrl: site, userId: 7, token: 'NEVER_PERSIST_THIS_TOKEN',
    async call(method, args) {calls.push([method, args]); assert.equal(method, 'core_course_get_contents'); assert.equal(args.courseid, 10);
      return [{id: 1, name: 'Lectures', modules: structuredClone(modules)}];},
    async transport(url) {const name = decodeURIComponent(new URL(url).pathname.split('/').at(-1)); downloads.push(name);
      return new Response(bytes.get(name), {headers: {'content-type': 'text/plain'}});},
  };
  const options = {studyRoot: root, statePath, studyToolsDir: root, courseId: 'TEST', chapter: '8', materialIds: ['M0'], sessionId: 'chat-a',
    async install(args) {
      installs.push(args.source.filename);
      const data = readFileSync(args.stagedPath), state = read(statePath);
      const before = state.materials.find(row => !row.superseded_by && row.moodle_file_sources[0].module_id === args.source.module_id);
      const id = before.id + '-updated';
      const archive = 'TEST/Lectures/' + before.id + '-archive.txt';
      renameSync(join(root, before.relative_path), join(root, archive)); before.relative_path = archive;
      before.superseded_by = id; before.version_status = 'archived';
      const after = {...before, id, relative_path: 'TEST/Lectures/' + args.source.filename, sha256: sha(data), version_status: 'current'};
      delete after.superseded_by; state.materials.push(after); write(statePath, state);
      renameSync(args.stagedPath, join(root, after.relative_path));
      return {status: 'promoted', material_id: id, sha256: sha(data), relative_path: after.relative_path};
    },
    async indexMaterials(course, ids) {indexes.push(ids); assert.equal(course, 'TEST'); return {materials: ids.map(id => ({id, status: 'indexed', text_units: 1}))};},
  };
  return {root, statePath, bytes, modules, calls, downloads, indexes, installs, options, client};
}

test('strictly fetches only requested chapter originals, reuses in chat and fetches in a new chat', async t => {
  const f = fixture(t), before = readFileSync(f.statePath);
  const first = await verifyChapter(f.client, f.options);
  assert.equal(first.status, 'verified'); assert.equal(first.reused, false);
  assert.deepEqual(f.downloads, ['Chapter 8 A.txt']); assert.equal(f.calls.length, 1);
  assert.equal(first.full_refresh, false); assert.equal(first.reviewed, false);
  const second = await verifyChapter(f.client, f.options);
  assert.equal(second.reused, true); assert.equal(second.verified_at, first.verified_at);
  assert.equal(f.calls.length, 1); assert.equal(f.downloads.length, 1);
  const cached = await readCachedChapterCheck({...f.options, site, userId: 7});
  assert.equal(cached.reused, true);
  await verifyChapter(f.client, {...f.options, sessionId: 'chat-b'});
  assert.equal(f.downloads.length, 2);
  assert.deepEqual(readFileSync(f.statePath), before);
  assert.equal(readFileSync(first.receipt_path, 'utf8').includes(f.client.token), false);
});

test('same-size same-time byte replacement is installed, indexed, and old/new IDs both reuse', async t => {
  const f = fixture(t); f.bytes.set('Chapter 8 A.txt', Buffer.from('CCCC'));
  const result = await verifyChapter(f.client, f.options);
  assert.equal(result.status, 'verified'); assert.equal(result.materials[0].status, 'updated');
  assert.equal(result.materials[0].material_id, 'M0-updated');
  assert.deepEqual(f.installs, ['Chapter 8 A.txt']); assert.deepEqual(f.indexes, [['M0-updated']]);
  assert.equal(readFileSync(join(f.root, 'TEST/Lectures/M0-archive.txt'), 'utf8'), 'AAAA');
  assert.equal(readFileSync(join(f.root, 'TEST/Lectures/Chapter 8 A.txt'), 'utf8'), 'CCCC');
  const before = f.downloads.length;
  assert.equal((await verifyChapter(f.client, f.options)).reused, true);
  assert.equal((await verifyChapter(f.client, {...f.options, materialIds: ['M0-updated']})).reused, true);
  assert.equal(f.downloads.length, before);
  assert.deepEqual(read(f.statePath).mistakes, [{answer: 'REAL_ANSWER'}]);
});

test('adding another part of the same chapter fetches only the newly selected original', async t => {
  const f = fixture(t); await verifyChapter(f.client, f.options);
  const result = await verifyChapter(f.client, {...f.options, materialIds: ['M0', 'M1']});
  assert.equal(result.verified, true);
  assert.equal(result.materials[0].reused, true); assert.equal(result.materials[1].reused, false);
  assert.deepEqual(f.downloads, ['Chapter 8 A.txt', 'Chapter 8 B.txt']);
});

test('failed download is retained as a failed attempt, no follow-up retry; explicit force retries', async t => {
  const f = fixture(t), original = f.client.transport;
  f.client.transport = async url => {f.downloads.push('failed'); throw new Error(url);};
  const first = await verifyChapter(f.client, f.options);
  assert.equal(first.status, 'needs_review'); assert.equal(first.verified_at, null);
  assert.equal(JSON.stringify(first).includes(f.client.token), false);
  f.client.transport = original;
  assert.equal((await verifyChapter(f.client, f.options)).reused, true);
  assert.equal(f.downloads.length, 1);
  assert.equal((await verifyChapter(f.client, {...f.options, force: true})).status, 'verified');
  assert.equal(f.downloads.length, 2);
});

test('cache invalidated by local edits does not silently check or overwrite again', async t => {
  const f = fixture(t); await verifyChapter(f.client, f.options);
  writeFileSync(join(f.root, 'TEST/Lectures/Chapter 8 A.txt'), 'USER');
  const result = await verifyChapter(f.client, f.options);
  assert.equal(result.status, 'needs_review'); assert.equal(result.verified_at, null);
  assert.equal(result.materials[0].reason, 'local_material_changed_since_verification');
  assert.equal(f.downloads.length, 1); assert.equal(f.calls.length, 1);
  assert.equal((await verifyChapter(f.client, {...f.options, force: true})).status, 'needs_review');
  assert.equal(readFileSync(join(f.root, 'TEST/Lectures/Chapter 8 A.txt'), 'utf8'), 'USER');
});

test('missing or renamed source never falls back to a similarly titled chapter', async t => {
  const f = fixture(t); f.modules[0].contents[0].filename = 'Chapter 8 A revised.txt';
  const result = await verifyChapter(f.client, f.options);
  assert.equal(result.status, 'needs_review'); assert.equal(result.materials[0].reason, 'source_missing_or_renamed');
  assert.equal(f.downloads.length, 0);
  assert.equal((await verifyChapter(f.client, f.options)).reused, true);
  assert.equal(f.calls.length, 1);
});

test('no authorization, wrong course, and revoked authorization never download', async t => {
  const f = fixture(t), state = read(f.statePath);
  state.settings.course_content_sync.grants[0].mode = 'metadata'; write(f.statePath, state);
  const denied = await verifyChapter(f.client, f.options);
  assert.equal(denied.materials[0].reason, 'course_content_not_authorized');
  assert.equal(f.calls.length, 0);
  const wrong = await verifyChapter(f.client, {...f.options, courseId: 'OTHER'});
  assert.equal(wrong.materials[0].reason, 'unknown_or_noncurrent_course_material');
  state.settings.course_content_sync.grants[0].mode = 'full'; write(f.statePath, state);
  await verifyChapter(f.client, {...f.options, force: true});
  state.settings.course_content_sync.grants[0].mode = 'metadata'; write(f.statePath, state);
  const revoked = await verifyChapter(f.client, f.options);
  assert.equal(revoked.materials[0].reason, 'authorization_changed'); assert.equal(revoked.verified, false);
  assert.equal(f.downloads.length, 1);
});

test('one part can succeed while another fails without making the chapter verified', async t => {
  const f = fixture(t), original = f.client.transport;
  f.client.transport = async url => {
    if (decodeURIComponent(url).includes('Chapter 8 B.txt')) throw new Error('offline');
    return original(url);
  };
  const result = await verifyChapter(f.client, {...f.options, materialIds: ['M0', 'M1']});
  assert.equal(result.status, 'needs_review'); assert.equal(result.materials[0].status, 'unchanged');
  assert.equal(result.materials[1].status, 'needs_review'); assert.equal(result.verified_at, null);
  assert.equal((await verifyChapter(f.client, {...f.options, materialIds: ['M0', 'M1']})).reused, true);
});

test('index failure retains downloaded version and reports no successful teaching verification', async t => {
  const f = fixture(t); f.bytes.set('Chapter 8 A.txt', Buffer.from('CCCC'));
  const result = await verifyChapter(f.client, {...f.options, indexMaterials: async () => {throw new Error('failed');}});
  assert.equal(result.verified, false); assert.equal(result.materials[0].reason, 'index_failed_download_retained');
  assert.equal(result.materials[0].material_id, 'M0-updated');
  assert.equal(readFileSync(join(f.root, 'TEST/Lectures/Chapter 8 A.txt'), 'utf8'), 'CCCC');
  assert.equal((await verifyChapter(f.client, {...f.options, materialIds: ['M0-updated']})).reused, true);
  const retry = await verifyChapter(f.client, {...f.options, force: true});
  assert.equal(retry.verified, true); assert.deepEqual(f.indexes, [['M0-updated']]);
});

test('missing session ID is rejected, account changes never reuse another account receipt', async t => {
  const f = fixture(t);
  await assert.rejects(verifyChapter(f.client, {...f.options, sessionId: ''}), error => error.code === 'CHAPTER_VERIFY_USAGE');
  await verifyChapter(f.client, f.options);
  assert.equal(await readCachedChapterCheck({...f.options, site, userId: 8}), null);
  const result = await verifyChapter({...f.client, userId: 8}, f.options);
  assert.equal(result.verified, false); assert.equal(f.downloads.length, 1);
});

test('new same-chapter resources require source review without downloading or broadening scope', async t => {
  const f = fixture(t);
  const candidate = structuredClone(f.modules[0]);
  candidate.id = 900; candidate.name = 'Chapter 8 revised'; candidate.contents[0].filename = 'Chapter 8 revised.txt';
  f.modules.push(candidate);
  const result = await verifyChapter(f.client, f.options);
  assert.equal(result.verified, false); assert.equal(result.ready_for_teaching, false);
  assert.equal(result.materials[0].status, 'unchanged');
  assert.deepEqual(result.related_candidates.map(row => row.module_id), [900]);
  assert.equal(result.source_review_required, true);
  assert.deepEqual(f.downloads, ['Chapter 8 A.txt']);
  const cached = await verifyChapter(f.client, f.options);
  assert.equal(cached.reused, true); assert.equal(cached.source_review_required, true);
  assert.equal(f.calls.length, 1);
});

test('chapter markers match whole numbers and do not confuse Chapter 8 with 80 or 8.1', async t => {
  const f = fixture(t);
  for (const [id, name] of [[900, 'Chapter 80.txt'], [901, 'Chapter 8.1.txt'], [902, '第8章 修订.txt']]) {
    const module = structuredClone(f.modules[0]); module.id = id; module.name = name; module.contents[0].filename = name;
    f.modules.push(module);
  }
  const result = await verifyChapter(f.client, f.options);
  assert.deepEqual(result.related_candidates.map(row => row.module_id), [902]);
});

test('failed listing is cached and never interpreted as unchanged materials', async t => {
  const f = fixture(t);
  f.client.call = async () => {f.calls.push('failed'); throw new Error('https://school.example/?token=' + f.client.token);};
  const result = await verifyChapter(f.client, f.options);
  assert.equal(result.materials[0].reason, 'course_listing_unavailable');
  assert.equal(result.verified, false); assert.equal(f.downloads.length, 0);
  assert.equal(JSON.stringify(result).includes(f.client.token), false);
  assert.equal((await verifyChapter(f.client, f.options)).reused, true);
  assert.equal(f.calls.length, 1);
});

test('new chat repairs a retained updated original whose earlier indexing failed', async t => {
  const f = fixture(t); f.bytes.set('Chapter 8 A.txt', Buffer.from('CCCC'));
  const failed = await verifyChapter(f.client, {...f.options, indexMaterials: async () => {throw new Error('failed');}});
  assert.equal(failed.ready_for_teaching, false);
  const repaired = await verifyChapter(f.client, {...f.options, sessionId: 'new-chat'});
  assert.equal(repaired.ready_for_teaching, true);
  assert.equal(repaired.materials[0].status, 'unchanged');
  assert.deepEqual(f.indexes, [['M0-updated']]);
  assert.equal(f.downloads.length, 2);
});

function addMirror(f, sameEndpoint = false) {
  const module = structuredClone(f.modules[0]); module.id = 900; module.name = 'Chapter 8 mirror';
  const file = module.contents[0];
  if (!sameEndpoint) {
    file.filename = 'mirror.txt'; file.fileurl = site + '/pluginfile.php/77/mod_resource/content/1/mirror.txt';
    f.bytes.set('mirror.txt', Buffer.from('AAAA'));
  }
  f.modules.push(module);
  const state = read(f.statePath);
  state.materials[0].moodle_file_sources.push({module_id: module.id, url: file.fileurl, remote_filename: file.filename, filepath: '/'});
  write(f.statePath, state);
}

test('registered multiple sources are checked individually and an unchanged original remains usable', async t => {
  const f = fixture(t); addMirror(f);
  const result = await verifyChapter(f.client, f.options);
  assert.equal(result.ready_for_teaching, true);
  assert.equal(result.materials[0].sources.length, 2);
  assert.deepEqual(f.downloads, ['Chapter 8 A.txt', 'mirror.txt']);
  assert.equal((await verifyChapter(f.client, f.options)).reused, true);
  assert.equal(f.downloads.length, 2);
});

test('registered aliases of one exact endpoint share a transfer without losing provenance', async t => {
  const f = fixture(t); addMirror(f, true);
  const result = await verifyChapter(f.client, f.options);
  assert.equal(result.ready_for_teaching, true); assert.equal(result.materials[0].sources.length, 2);
  assert.deepEqual(f.downloads, ['Chapter 8 A.txt']);
});

test('disagreeing registered sources preserve originals and do not guess which is authoritative', async t => {
  const f = fixture(t); addMirror(f); f.bytes.set('mirror.txt', Buffer.from('ZZZZ'));
  const result = await verifyChapter(f.client, f.options);
  assert.equal(result.ready_for_teaching, false); assert.equal(result.materials[0].reason, 'registered_sources_disagree');
  assert.equal(f.installs.length, 0); assert.equal(readFileSync(join(f.root, 'TEST/Lectures/Chapter 8 A.txt'), 'utf8'), 'AAAA');
});

test('identical updates to several registered sources require version review rather than partial replacement', async t => {
  const f = fixture(t); addMirror(f);
  f.bytes.set('Chapter 8 A.txt', Buffer.from('CCCC')); f.bytes.set('mirror.txt', Buffer.from('CCCC'));
  const result = await verifyChapter(f.client, f.options);
  assert.equal(result.ready_for_teaching, false); assert.equal(result.materials[0].reason, 'known_multisource_update_requires_review');
  assert.equal(f.installs.length, 0); assert.equal(readFileSync(join(f.root, 'TEST/Lectures/Chapter 8 A.txt'), 'utf8'), 'AAAA');
});
