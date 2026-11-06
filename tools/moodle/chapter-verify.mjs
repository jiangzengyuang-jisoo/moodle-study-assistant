// A teaching check is scoped to explicitly selected originals, never a full refresh.
import {createHash, randomUUID} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {existsSync, mkdirSync, readFileSync, realpathSync, renameSync, writeFileSync, unlinkSync} from 'node:fs';
import {join, resolve, sep} from 'node:path';
import {authorizedCoursePages, normalizeSite} from './content-policy.mjs';
import {enumerateCourseContent, stageDownload, hashFile, cleanOwnedStaging} from './course-content-sync.mjs';
import {installVersion} from './version-files.mjs';
import {acquireChapterLock} from './chapter-lock.mjs';

const digest = value => createHash('sha256').update(value).digest('hex');
const read = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
const current = row => row && !row.superseded_by && !['archived', 'deleted'].includes(row.version_status);
const visible = row => row && row.uservisible !== false && row.visible !== 0;
const successful = row => ['unchanged', 'updated'].includes(row?.status);
const usage = () => Object.assign(new Error('请指定课程、章节、真实对话 ID 和本次使用的资料 ID。'), {code: 'CHAPTER_VERIFY_USAGE'});

function setup(options) {
  const {courseId, chapter, sessionId, materialIds, studyRoot, statePath, site, userId} = options;
  if (![courseId, chapter, sessionId].every(value => typeof value === 'string' && value.trim())
      || !Array.isArray(materialIds) || !materialIds.length || materialIds.some(id => typeof id !== 'string' || !id.trim())
      || !Number.isSafeInteger(userId) || userId <= 0) throw usage();
  const root = realpathSync(studyRoot), normalizedSite = normalizeSite(site);
  const key = digest(JSON.stringify([normalizedSite, userId, sessionId, courseId, chapter.trim()]));
  const directory = join(root, '_System/CourseContent/chapter-checks');
  mkdirSync(directory, {recursive: true});
  if (realpathSync(directory) !== resolve(directory)) throw new Error('unsafe_chapter_receipt_folder');
  const path = join(directory, key + '.json');
  if (existsSync(path) && realpathSync(path) !== resolve(path)) throw new Error('unsafe_chapter_receipt');
  return {...options, root, site: normalizedSite, path, statePath, materialIds: [...new Set(materialIds)]};
}

function localPath(root, material) {
  if (typeof material?.relative_path !== 'string') throw new Error('missing_local_path');
  const path = resolve(root, material.relative_path);
  if (!path.startsWith(root + sep) || realpathSync(path) !== path) throw new Error('unsafe_local_path');
  return path;
}

// Material IDs are immutable. Both old and new IDs resolve to the same lineage key.
function resolveMaterials(state, options) {
  const materials = new Map((state.materials ?? []).map(row => [row.id, row]));
  return options.materialIds.map(requestedId => {
    let row = materials.get(requestedId), seen = new Set();
    while (row?.superseded_by && !seen.has(row.id)) {seen.add(row.id); row = materials.get(row.superseded_by);}
    if (!current(row) || row.course_id !== options.courseId) return {key: requestedId, requestedId, error: 'unknown_or_noncurrent_course_material'};
    let ancestor = row;
    seen = new Set([row.id]);
    for (;;) {
      const parents = [...materials.values()].filter(value => value.course_id === options.courseId && value.superseded_by === ancestor.id);
      if (parents.length !== 1 || seen.has(parents[0].id)) break;
      ancestor = parents[0]; seen.add(ancestor.id);
    }
    return {key: ancestor.id, requestedId, material: row};
  });
}

function loadReceipt(options) {
  if (!existsSync(options.path)) return {version: 1, materials: {}};
  const receipt = read(options.path);
  if (receipt.version !== 1 || !receipt.materials || typeof receipt.materials !== 'object' || Array.isArray(receipt.materials))
    throw new Error('invalid_chapter_receipt');
  return receipt;
}

function saveReceipt(options, receipt) {
  options.assertLockHeld?.();
  const temporary = options.path + '.' + randomUUID() + '.tmp';
  try {writeFileSync(temporary, JSON.stringify(receipt, null, 2), {flag: 'wx'}); renameSync(temporary, options.path);}
  finally {if (existsSync(temporary)) unlinkSync(temporary);}
}

function sources(material, site) {
  const input = [...(material.moodle_file_sources ?? [])];
  if (!input.length && material.moodle_url) {
    try {input.push({module_id: Number(new URL(material.moodle_url).searchParams.get('id')), activity_url: material.moodle_url});} catch {}
  }
  const result = [];
  for (const source of input) {
    if (!Number.isSafeInteger(Number(source.module_id)) || Number(source.module_id) <= 0 || source.post_id != null) continue;
    try {
      for (const address of [source.url, source.activity_url].filter(Boolean)) {
        const url = new URL(address);
        if (url.origin !== new URL(site).origin || url.username || url.password) throw new Error('foreign_source');
      }
      const filename = source.remote_filename || (source.url && decodeURIComponent(new URL(source.url).pathname.split('/').at(-1))) || material.filename;
      if (!filename) continue;
      result.push({moduleId: Number(source.module_id), pageId: source.page_id,
        filename, filepath: source.filepath});
    } catch {}
  }
  return result;
}

async function cachedEntry(entry, selected, options, state) {
  if (!entry) return null;
  const material = selected.material;
  if (selected.error) return {...entry, status: 'needs_review', reason: selected.error, reused: true};
  const grants = authorizedCoursePages(state, options);
  if ([...(entry.sources ?? []), ...(entry.source ? [entry.source] : [])].some(source => grants.get(source.page_id) !== options.courseId))
    return {...entry, status: 'needs_review', reason: 'authorization_changed', reused: true};
  if (!successful(entry)) return {...entry, reused: true};
  try {
    if (material.id !== entry.material_id || material.sha256 !== entry.sha256
        || await hashFile(localPath(options.root, material)) !== entry.sha256)
      throw new Error('local_changed');
  } catch {
    return {...entry, status: 'needs_review', reason: 'local_material_changed_since_verification', reused: true,
      verified_at: null, previous_verified_at: entry.verified_at};
  }
  return {...entry, reused: true};
}

function candidateKnown(candidate, state, options) {
  return (state.materials ?? []).filter(row => current(row) && row.course_id === options.courseId)
    .some(row => sources(row, options.site).some(source => source.moduleId === candidate.module_id
      && (!source.pageId || source.pageId === candidate.page_id) && source.filename === candidate.filename
      && (source.filepath === undefined || source.filepath === candidate.filepath)));
}

function chapterPattern(chapter) {
  const number = chapter.trim().match(/^(?:chapter\s*|ch\.?\s*|第\s*)?(\d+)(?:\s*章)?$/i)?.[1];
  if (!number) return null; // A label without a known chapter marker has no safe fuzzy matching rule.
  const normalized = String(Number(number));
  return new RegExp(`(?:\\b(?:chapter|ch)\\.?[\\s_-]*0*${normalized}(?![\\d.])|第\\s*0*${normalized}\\s*章)`, 'i');
}

function findCandidates(listings, state, options) {
  const pattern = chapterPattern(options.chapter), result = new Map();
  if (!pattern) return [];
  for (const [pageId, sections] of listings) for (const section of (sections ?? []).filter(visible)) {
    for (const module of (section.modules ?? []).filter(visible)) {
      if (!['resource', 'folder'].includes(module.modname)) continue;
      for (const file of module.contents ?? []) {
        if (file.type !== 'file' || !/\.(?:pdf|pptx?|docx?|txt)$/i.test(file.filename ?? '')
            || !pattern.test(`${module.name ?? ''} ${file.filename ?? ''}`)) continue;
        const candidate = {page_id: pageId, module_id: Number(module.id), filename: file.filename, filepath: file.filepath ?? '/',
          reason: 'unregistered_related_chapter_file'};
        if (!candidateKnown(candidate, state, options)) result.set(JSON.stringify(candidate), candidate);
      }
    }
  }
  return [...result.values()];
}

function resultFor(options, rows, relatedCandidates = []) {
  const verified = rows.length > 0 && rows.every(successful) && !relatedCandidates.length;
  return {operation: 'verify_chapter', scope: 'selected_chapter_materials', course_id: options.courseId,
    chapter: options.chapter, strict_binary_check: true, status: verified ? 'verified' : 'needs_review',
    verified, ready_for_teaching: verified, reused: rows.length > 0 && rows.every(row => row.reused),
    verified_at: verified ? rows.map(row => row.verified_at).sort()[0] : null,
    materials: rows, related_candidates: relatedCandidates, source_review_required: relatedCandidates.length > 0,
    discovery_scope: 'same_course_explicit_chapter_number_only_not_all_notices_or_unnamed_replacements',
    full_refresh: false, reviewed: false, mastery_updated: false,
    receipt_path: options.path};
}

// May be called before constructing a network client. Never substitutes another chat's receipt.
export async function readCachedChapterCheck(input) {
  if (input.force) return null;
  const options = setup(input), state = read(options.statePath), receipt = loadReceipt(options);
  const selections = resolveMaterials(state, options), rows = [];
  for (const selected of selections) {
    const row = await cachedEntry(receipt.materials[selected.key], selected, options, state);
    if (!row) return null;
    rows.push({...row, requested_material_id: selected.requestedId});
  }
  return resultFor(options, rows, (receipt.related_candidates ?? []).filter(candidate => !candidateKnown(candidate, state, options)));
}

async function indexMaterials(options, ids) {
  options.assertLockHeld?.();
  if (options.indexMaterials) return options.indexMaterials(options.courseId, ids);
  const child = spawnSync(options.python, [join(options.studyToolsDir, 'course_content.py'), '--state', options.statePath,
    'index', '--course', options.courseId, ...ids.flatMap(id => ['--material', id])],
  {encoding: 'utf8', windowsHide: true, timeout: 900000, maxBuffer: 16 * 1024 * 1024,
    env: {...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1'}});
  if (child.error || child.status !== 0) throw new Error('index_failed');
  return JSON.parse(child.stdout);
}

async function ensureIndex(options, materialId) {
  const indexed = await indexMaterials(options, [materialId]);
  const entry = indexed.materials?.find(m => m.id === materialId);
  if (!entry || entry.status !== 'indexed' || !(entry.text_units > 0)) throw new Error('index_failed');
}

export async function verifyChapter(client, input) {
  const options = setup({...input, site: client.baseUrl, userId: client.userId});
  const cached = await readCachedChapterCheck(options);
  if (cached) return cached;
  const lock = await (options.acquireLock ?? acquireChapterLock)(options.path + '.lock', options.python);
  if (lock.status !== 'acquired') return resultFor(options, [{status: 'needs_review',
    reason: lock.status === 'busy' ? 'chapter_check_already_running' : 'chapter_check_legacy_lock_needs_review', reused: false}]);
  try {
    lock.assertHeld();
    return await runCheck(client, {...options, assertLockHeld: lock.assertHeld});
  } finally {await lock.release();}
}

async function runCheck(client, options) {
  const now = options.now ?? (() => new Date().toISOString()), install = options.install ?? installVersion;
  const latest = () => read(options.statePath);
  const state = latest(), selections = resolveMaterials(state, options), receipt = loadReceipt(options);
  const rows = [], pending = [], selectedKeys = new Set();
  for (const selected of selections) {
    if (selectedKeys.has(selected.key)) continue;
    selectedKeys.add(selected.key);
    const cached = !options.force && await cachedEntry(receipt.materials[selected.key], selected, options, state);
    if (cached) {rows.push({...cached, requested_material_id: selected.requestedId}); continue;}
    const row = {requested_material_id: selected.requestedId, material_id: selected.material?.id ?? selected.requestedId,
      status: 'needs_review', checked_at: now(), verified_at: null, reused: false};
    rows.push(row);
    receipt.materials[selected.key] = row;
    if (selected.error) {row.reason = selected.error; continue;}
    const source = sources(selected.material, options.site);
    if (!source.length) {row.reason = 'no_verified_moodle_source'; continue;}
    const pages = [...authorizedCoursePages(state, options)].filter(([, owner]) => owner === options.courseId);
    if (!pages.length) {row.reason = 'course_content_not_authorized'; continue;}
    pending.push({selected, row, source, pages});
  }
  // Persist a failed/in-progress attempt too: ordinary follow-ups never silently retry it.
  for (const {row} of pending) row.reason = 'verification_interrupted';
  saveReceipt(options, receipt);
  const listings = new Map();
  const pageIds = new Set(pending.flatMap(item => item.pages.filter(([pageId]) => item.source.some(s => !s.pageId || s.pageId === pageId)).map(([id]) => id)));
  for (const pageId of pageIds) {
    if (authorizedCoursePages(latest(), options).get(pageId) !== options.courseId) {listings.set(pageId, null); continue;}
    try {
      const sections = await client.call('core_course_get_contents', {courseid: pageId});
      options.assertLockHeld?.();
      if (!Array.isArray(sections)) throw new Error('invalid_listing');
      listings.set(pageId, sections);
    } catch {listings.set(pageId, null);}
  }
  const previousCandidates = (receipt.related_candidates ?? []).filter(candidate => !listings.has(candidate.page_id));
  receipt.related_candidates = [...previousCandidates, ...findCandidates(listings, latest(), options)];
  for (const {selected, row, source, pages} of pending) {
    let staged;
    try {
      const candidates = new Map();
      const matches = new Map(source.map(expected => [expected, new Set()]));
      let listingFailed = false;
      for (const [pageId] of pages) {
        if (!source.some(s => !s.pageId || s.pageId === pageId)) continue;
        const sections = listings.get(pageId);
        if (!sections) {listingFailed = true; continue;}
        for (const section of sections.filter(visible)) for (const module of (section.modules ?? []).filter(visible)) {
          const expected = source.filter(s => s.moduleId === Number(module.id) && (!s.pageId || s.pageId === pageId));
          if (!expected.length) continue;
          const found = enumerateCourseContent([{...section, summary: '', modules: [{...module, description: ''}]}],
            {pageId, courseId: options.courseId, site: options.site, token: client.token});
          for (const file of found.files) for (const expectedSource of expected) {
            if (expectedSource.filename !== file.filename || expectedSource.filepath !== undefined && expectedSource.filepath !== file.filepath) continue;
            const key = JSON.stringify([pageId, module.id, file.filepath, file.filename]);
            candidates.set(key, file); matches.get(expectedSource).add(key);
          }
        }
      }
      if (listingFailed) {row.reason = 'course_listing_unavailable'; continue;}
      if ([...matches.values()].some(match => match.size !== 1)) {
        row.reason = [...matches.values()].some(match => match.size > 1) ? 'ambiguous_remote_source' : 'source_missing_or_renamed'; continue;
      }
      const items = [...candidates.values()], item = items[0];
      row.sources = items.map(file => ({page_id: file.page_id, module_id: file.module_id, filename: file.filename, filepath: file.filepath}));
      row.source = row.sources[0];
      const permitted = () => row.sources.every(source => authorizedCoursePages(latest(), options).get(source.page_id) === options.courseId);
      if (!permitted()) {row.reason = 'authorization_changed'; continue;}
      const before = resolveMaterials(latest(), {...options, materialIds: [selected.requestedId]})[0].material;
      if (!before || before.id !== selected.material.id) {row.reason = 'material_changed_during_verification'; continue;}
      // Protect handwritten/edited originals. Never overwrite a locally changed source.
      let localHash;
      try {localHash = await hashFile(localPath(options.root, before));}
      catch {row.reason = 'local_material_missing_or_modified'; continue;}
      if (localHash !== before.sha256) {row.reason = 'local_material_missing_or_modified'; continue;}
      const transfers = new Map();
      for (const file of items) {
        const url = new URL(file.fileurl); url.pathname = url.pathname.replace('/webservice/pluginfile.php/', '/pluginfile.php/'); url.search = '';
        if (transfers.has(url.href)) continue; // Registered aliases of the exact endpoint share one transfer.
        if (!permitted()) throw new Error('authorization_changed');
        options.assertLockHeld?.();
        const path = await stageDownload(client, file, {studyRoot: options.root, site: options.site,
          timeoutMs: options.timeoutMs ?? 30000, maxBytes: options.maxBytes ?? Infinity});
        options.assertLockHeld?.();
        transfers.set(url.href, {path, sha256: await hashFile(path)});
      }
      const downloaded = [...transfers.values()]; staged = downloaded[0].path;
      const remoteHash = downloaded[0].sha256;
      if (!permitted()) {row.reason = 'authorization_changed'; continue;}
      if (resolveMaterials(latest(), {...options, materialIds: [selected.requestedId]})[0].material?.id !== before.id)
        {row.reason = 'material_changed_during_verification'; continue;}
      if (await hashFile(localPath(options.root, before)) !== localHash) {row.reason = 'local_material_changed_during_verification'; continue;}
      if (downloaded.some(file => file.sha256 !== remoteHash)) {
        row.reason = 'registered_sources_disagree';
        for (const file of downloaded) if (file.sha256 === localHash)
          await cleanOwnedStaging(options.root, file.path, {status: 'identical', sha256: localHash});
        continue;
      }
      if (remoteHash === localHash) {
        Object.assign(row, {material_id: before.id, relative_path: before.relative_path, sha256: remoteHash,
          binary_verified_at: now(), reason: 'index_failed_download_retained'});
        for (const file of downloaded) await cleanOwnedStaging(options.root, file.path, {status: 'identical', sha256: remoteHash}); staged = null;
        // Targeted sync reuses valid caches and repairs only this original when missing/stale.
        await ensureIndex(options, before.id);
        Object.assign(row, {status: 'unchanged', verified_at: row.binary_verified_at}); delete row.reason;
      } else {
        if (items.length > 1) {row.reason = 'known_multisource_update_requires_review'; continue;}
        options.assertLockHeld?.();
        const installed = await install({study: options.root, statePath: options.statePath,
          script: join(options.studyToolsDir, 'material_versions.py'), python: options.python,
          source: {...item, contains_solutions: before.contains_solutions,
            scope: {site: options.site, user_id: options.userId, page_id: item.page_id}},
          stagedPath: staged, deferIndex: true, sourceSubdirectory: true, retainUnparsed: true});
        await cleanOwnedStaging(options.root, staged, installed); staged = null;
        const after = resolveMaterials(latest(), {...options, materialIds: [selected.requestedId]})[0].material;
        if (!after || after.id !== installed.material_id || after.sha256 !== remoteHash
            || await hashFile(localPath(options.root, after)) !== remoteHash) throw new Error('installed_version_not_current');
        Object.assign(row, {material_id: after.id, relative_path: after.relative_path, sha256: remoteHash,
          status: 'needs_review', reason: 'index_failed_download_retained', binary_verified_at: now()});
        await ensureIndex(options, after.id);
        Object.assign(row, {status: 'updated', verified_at: row.binary_verified_at}); delete row.reason;
      }
    } catch {
      row.status = 'needs_review';
      if (row.reason !== 'index_failed_download_retained') row.reason = 'download_or_install_failed_existing_materials_preserved';
      row.verified_at = null;
    } finally {saveReceipt(options, receipt);}
  }
  receipt.finished_at = now(); saveReceipt(options, receipt);
  return resultFor(options, rows, receipt.related_candidates.filter(candidate => !candidateKnown(candidate, latest(), options)));
}
