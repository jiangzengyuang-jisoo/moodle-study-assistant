// Acquire only the requested lesson's originals under existing course grants.
import {createHash, randomUUID} from 'node:crypto';
import {existsSync, mkdirSync, readFileSync, realpathSync, renameSync, writeFileSync, unlinkSync} from 'node:fs';
import {join, resolve, sep} from 'node:path';
import {authorizedCoursePages, normalizeSite} from './content-policy.mjs';
import {enumerateCourseContent, stageDownload, cleanOwnedStaging} from './course-content-sync.mjs';
import {acquireChapterLock} from './chapter-lock.mjs';
import {installVersion} from './version-files.mjs';
import {chapterPattern, sources, verifyChapter, readCachedChapterCheck} from './chapter-verify.mjs';

const read = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
const current = m => m && !m.superseded_by && !['archived', 'deleted'].includes(m.version_status);
const visible = m => m && m.visible !== 0 && m.uservisible !== false;
function setup(input) {
  if (![input.courseId, input.chapter, input.sessionId].every(v => typeof v === 'string' && v.trim())
      || !Number.isSafeInteger(input.userId) || input.userId <= 0) throw new Error('invalid_lesson_scope');
  const root = realpathSync(input.studyRoot), site = normalizeSite(input.site);
  const key = createHash('sha256').update(JSON.stringify([site, input.userId, input.sessionId, input.courseId,
    input.chapter, [...(input.materialIds ?? [])].sort(), [...(input.moduleRefs ?? [])].sort()])).digest('hex');
  const folder = join(root, '_System/CourseContent/chapter-acquisitions');
  mkdirSync(folder, {recursive: true});
  if (realpathSync(folder) !== resolve(folder)) throw new Error('unsafe_acquisition_folder');
  const path = join(folder, key + '.json');
  if (existsSync(path) && realpathSync(path) !== resolve(path)) throw new Error('unsafe_acquisition_receipt');
  return {...input, root, site, path};
}
function save(options, receipt) {
  const temp = options.path + '.' + randomUUID() + '.tmp';
  try {writeFileSync(temp, JSON.stringify(receipt, null, 2), {flag: 'wx'}); renameSync(temp, options.path);}
  finally {if (existsSync(temp)) unlinkSync(temp);}
}
const failure = (options, reason, acquired = []) => ({operation: 'ensure_chapter', course_id: options.courseId,
  chapter: options.chapter, status: 'needs_review', reason, ready_for_teaching: false, full_refresh: false,
  reviewed: false, mastery_updated: false, reused: false, acquired});

export async function readCachedChapterEnsure(input) {
  if (input.force) return null;
  const options = setup(input);
  if (!existsSync(options.path)) return null;
  const receipt = read(options.path);
  const grants = authorizedCoursePages(read(options.statePath), options);
  if ((receipt.pages ?? []).some(page => grants.get(page) !== options.courseId))
    return {...failure(options, 'authorization_changed'), reused: true};
  if (!receipt.result.ready_for_teaching) return {...receipt.result, reused: true};
  // A deleted original can be restored under the same grant. Edited originals
  // still go through the verifier's invalidation path without being overwritten.
  const state = read(options.statePath);
  if (receipt.material_ids.some(id => {
    const material = state.materials?.find(m => m.id === id && current(m));
    if (typeof material?.relative_path !== 'string') return false;
    const path = resolve(options.root, material.relative_path);
    return path.startsWith(options.root + sep) && !existsSync(path);
  })) return null;
  const checked = await readCachedChapterCheck({...options, materialIds: receipt.material_ids});
  return checked ? {...checked, operation: 'ensure_chapter', acquired: receipt.result.acquired} : null;
}

export async function ensureChapter(client, input) {
  const options = setup({...input, site: client.baseUrl, userId: client.userId});
  const cached = await readCachedChapterEnsure(options);
  if (cached) return cached;
  const lock = await acquireChapterLock(options.path + '.lock', options.python);
  if (lock.status !== 'acquired') return failure(options, 'chapter_acquisition_' + lock.status);
  try {
    const latest = () => read(options.statePath);
    const permitted = page => (lock.assertHeld(), authorizedCoursePages(latest(), options).get(page) === options.courseId);
    const pages = [...authorizedCoursePages(latest(), options)].filter(([, owner]) => owner === options.courseId).map(([id]) => id);
    const receipt = {pages, material_ids: [], result: failure(options, 'acquisition_interrupted')};
    save(options, receipt);
    const finish = result => {lock.assertHeld(); receipt.result = result; save(options, receipt); return result;};
    if (!pages.length) return finish(failure(options, 'course_content_not_authorized'));
    const refs = new Set(options.moduleRefs ?? []), foundRefs = new Set(), files = new Map();
    if ([...refs].some(ref => !pages.includes(Number(ref.split(':')[0]))))
      return finish(failure(options, 'module_outside_authorized_course'));
    const state = latest(), requested = [];
    for (const id of options.materialIds ?? []) {
      let row = state.materials?.find(m => m.id === id), seen = new Set();
      while (row?.superseded_by && !seen.has(row.id)) {seen.add(row.id); row = state.materials.find(m => m.id === row.superseded_by);}
      if (!current(row) || row.course_id !== options.courseId) return finish(failure(options, 'unknown_course_material'));
      requested.push(row);
    }
    const matches = (material, file) => sources(material, options.site).some(s => s.moduleId === Number(file.module_id)
      && (!s.pageId || s.pageId === file.page_id) && s.filename === file.filename
      && (s.filepath === undefined || s.filepath === file.filepath));
    const pattern = chapterPattern(options.chapter);
    for (const pageId of pages) {
      if (!permitted(pageId)) return finish(failure(options, 'authorization_changed'));
      let sections;
      try {sections = await client.call('core_course_get_contents', {courseid: pageId}); if (!Array.isArray(sections)) throw new Error();}
      catch {return finish(failure(options, 'course_listing_unavailable'));}
      for (const section of sections.filter(visible)) for (const module of (section.modules ?? []).filter(visible)) {
        if (!['resource', 'folder'].includes(module.modname)) continue;
        const ref = `${pageId}:${module.id}`, explicit = refs.has(ref);
        const listing = enumerateCourseContent([{...section, summary: '', modules: [{...module, description: ''}]}],
          {pageId, courseId: options.courseId, site: options.site, token: client.token});
        for (const file of listing.files) {
          const named = /\.(?:pdf|pptx?|docx?|txt)$/i.test(file.filename)
            && pattern?.test(`${module.name ?? ''} ${file.filename}`);
          if (!explicit && !named && !requested.some(m => matches(m, file))) continue;
          files.set(JSON.stringify([pageId, module.id, file.filepath, file.filename]), file);
          if (explicit) foundRefs.add(ref);
        }
      }
    }
    if ([...refs].some(ref => !foundRefs.has(ref))) return finish(failure(options, 'selected_module_unavailable'));
    const ids = new Set(requested.map(m => m.id)), acquired = [];
    for (const file of files.values()) {
      if (!permitted(file.page_id)) return finish(failure(options, 'authorization_changed', acquired));
      const known = (latest().materials ?? []).filter(m => current(m) && m.course_id === options.courseId && matches(m, file));
      if (known.length > 1) return finish(failure(options, 'ambiguous_registered_source', acquired));
      if (known.length) {
        const path = resolve(options.root, known[0].relative_path);
        if (!path.startsWith(options.root + sep)) return finish(failure(options, 'unsafe_material_path', acquired));
        ids.add(known[0].id);
        // Existing local edits remain protected by the strict verifier and installer.
        if (existsSync(path)) continue;
      }
      try {
        const stagedPath = await stageDownload(client, file, {studyRoot: options.root, site: options.site,
          timeoutMs: options.timeoutMs ?? 30000, maxBytes: options.maxBytes ?? Infinity});
        if (!permitted(file.page_id)) return finish(failure(options, 'authorization_changed', acquired));
        const installed = await (options.install ?? installVersion)({study: options.root, statePath: options.statePath,
          script: join(options.studyToolsDir, 'material_versions.py'), python: options.python,
          source: {...file, contains_solutions: known[0]?.contains_solutions,
            scope: {site: options.site, user_id: options.userId, page_id: file.page_id}},
          stagedPath, deferIndex: true, sourceSubdirectory: true, retainUnparsed: true});
        await cleanOwnedStaging(options.root, stagedPath, installed);
        ids.add(installed.material_id);
        acquired.push({material_id: installed.material_id, status: installed.status, filename: file.filename});
      } catch {return finish(failure(options, 'acquisition_failed_existing_materials_preserved', acquired));}
    }
    receipt.material_ids = [...ids];
    if (!ids.size) return finish(failure(options, 'no_matching_materials_select_module'));
    // The strict verifier checks all sources and indexes only this lesson's originals.
    const checked = await verifyChapter(client, {...options, materialIds: [...ids]});
    return finish({...checked, operation: 'ensure_chapter', acquired});
  } finally {await lock.release();}
}
