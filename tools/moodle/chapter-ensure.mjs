// Acquire only the requested lesson's originals under existing course grants.
import {createHash, randomUUID} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {existsSync, mkdirSync, readFileSync, realpathSync, renameSync, writeFileSync, unlinkSync} from 'node:fs';
import {join, resolve, sep} from 'node:path';
import {authorizedCoursePages, normalizeSite, assertProfileAccount} from './content-policy.mjs';
import {enumerateCourseContent, stageDownload, cleanOwnedStaging, hashFile} from './course-content-sync.mjs';
import {acquireChapterLock} from './chapter-lock.mjs';
import {installVersion} from './version-files.mjs';
import {chapterPattern, sources, ensureIndex} from './chapter-verify.mjs';

const read = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
const current = m => m && !m.superseded_by && !['archived', 'deleted'].includes(m.version_status);
const visible = m => m && m.visible !== 0 && m.uservisible !== false;
function setup(input) {
  if (![input.courseId, input.chapter, input.sessionId].every(v => typeof v === 'string' && v.trim())
      || !Number.isSafeInteger(input.userId) || input.userId <= 0) throw new Error('invalid_lesson_scope');
  const root = realpathSync(input.studyRoot), site = normalizeSite(input.site);
  assertProfileAccount(read(input.statePath), {site, userId: input.userId});
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
  reviewed: false, mastery_updated: false, reused: false, acquired,
  teaching_paused: true, next_action: '暂停教学；处理来源、适用权限或本人登录后，在原范围明确重试。不得改用通用知识或未同步旧版本。'});

export function remoteTime(source) {
  for (const field of ['timemodified', 'timecreated']) {
    const value = source?.[field];
    if (Number.isSafeInteger(value) && value > 0) return {field, value};
  }
  return null;
}
const sameTime = (a, b) => a && b && a.value === b.value;

function finishSource(options, id, source) {
  const child = spawnSync(options.python, [join(options.studyToolsDir, 'chapter_source.py'),
    '--state', options.statePath, '--material', id], {input: JSON.stringify(source), encoding: 'utf8',
    windowsHide: true, timeout: 900000, maxBuffer: 1024 * 1024,
    env: {...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1'}});
  if (child.error || child.status !== 0) throw new Error('lesson_finish_failed');
  return JSON.parse(child.stdout);
}

export async function readCachedChapterEnsure(input) {
  if (input.force) return null;
  const options = setup(input);
  if (!existsSync(options.path)) return null;
  const receipt = read(options.path);
  if (receipt.version !== 2) return null; // Old byte-check receipts do not establish timestamps.
  const state = read(options.statePath), grants = authorizedCoursePages(state, options);
  if ((receipt.pages ?? []).some(page => grants.get(page) !== options.courseId))
    return {...failure(options, 'authorization_changed'), reused: true};
  if (!receipt.result.ready_for_teaching) return {...receipt.result, reused: true};
  for (const checked of receipt.result.materials) {
    const material = state.materials?.find(m => m.id === checked.material_id && current(m));
    if (!material) return {...failure(options, 'material_version_changed'), reused: true};
    if (checked.source_signature !== JSON.stringify(material.moodle_file_sources ?? []))
      return {...failure(options, 'source_mapping_changed'), reused: true};
    const path = resolve(options.root, material.relative_path);
    if (!path.startsWith(options.root + sep)) return failure(options, 'unsafe_material_path');
    if (!existsSync(path)) return null; // Restore an actually missing original under the same grant.
    if (realpathSync(path) !== path || material.sha256 !== checked.sha256 || await hashFile(path) !== checked.sha256)
      return {...failure(options, 'local_material_changed_since_verification'), reused: true};
    try {await ensureIndex(options, material.id, true);}
    catch {return {...failure(options, 'local_index_unavailable'), reused: true};}
  }
  return {...receipt.result, reused: true};
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
    const receipt = {version: 2, pages, material_ids: [], result: failure(options, 'acquisition_interrupted')};
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
    const pattern = chapterPattern(options.chapter), gaps = [];
    if (!refs.size) for (const material of state.materials ?? []) {
      if (current(material) && material.course_id === options.courseId && sources(material, options.site).length
          && pattern?.test(`${material.title ?? ''} ${material.filename ?? ''}`)
          && !requested.some(m => m.id === material.id)) requested.push(material);
    }
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
        const relevantModule = explicit || pattern?.test(module.name ?? '') || requested.some(m =>
          sources(m, options.site).some(source => source.moduleId === Number(module.id)
            && (!source.pageId || source.pageId === pageId)));
        const relatedFiles = [...listing.files, ...listing.gaps].some(file => pattern?.test(file.filename ?? ''));
        if (relevantModule || relatedFiles) gaps.push(...listing.gaps);
        for (const file of listing.files) {
          const named = /\.(?:pdf|pptx?|docx?|txt)$/i.test(file.filename)
            && pattern?.test(`${module.name ?? ''} ${file.filename}`);
          if (!explicit && !named && !requested.some(m => matches(m, file))) continue;
          files.set(JSON.stringify([pageId, module.id, file.filepath, file.filename]), file);
          if (explicit) foundRefs.add(ref);
        }
      }
    }
    if (gaps.length) return finish({...failure(options, 'chapter_source_gaps'), gaps});
    if ([...refs].some(ref => !foundRefs.has(ref))) return finish(failure(options, 'selected_module_unavailable'));
    if (requested.some(m => ![...files.values()].some(file => matches(m, file))))
      return finish(failure(options, 'source_missing_or_renamed'));
    const ids = new Set(), acquired = [], rows = [];
    for (const file of files.values()) {
      if (!permitted(file.page_id)) return finish(failure(options, 'authorization_changed', acquired));
      const time = remoteTime(file);
      if (!time) return finish(failure(options, 'remote_timestamp_unknown', acquired));
      const known = (latest().materials ?? []).filter(m => current(m) && m.course_id === options.courseId && matches(m, file));
      if (known.length > 1) return finish(failure(options, 'ambiguous_registered_source', acquired));
      const old = known[0];
      let present = false, savedTime = null;
      if (old) {
        const path = resolve(options.root, old.relative_path);
        if (!path.startsWith(options.root + sep)) return finish(failure(options, 'unsafe_material_path', acquired));
        present = existsSync(path);
        if (present && (realpathSync(path) !== path || await hashFile(path) !== old.sha256))
          return finish(failure(options, 'local_material_missing_or_modified', acquired));
        const source = old.moodle_file_sources?.find(s => Number(s.module_id) === file.module_id
          && (!s.page_id || s.page_id === file.page_id)
          && (s.remote_filename || old.filename) === file.filename && (s.filepath ?? '/') === file.filepath);
        savedTime = remoteTime(source);
      }
      try {
        const source = {...file, contains_solutions: old?.contains_solutions, chapter_timestamp_update: true,
          scope: {site: options.site, user_id: options.userId, page_id: file.page_id}};
        let id = old?.id, status = 'timestamp_unchanged';
        if (!present || !sameTime(time, savedTime)) {
          const stagedPath = await stageDownload(client, file, {studyRoot: options.root, site: options.site,
            timeoutMs: options.timeoutMs ?? 30000, maxBytes: options.maxBytes ?? Infinity});
          if (!permitted(file.page_id)) return finish(failure(options, 'authorization_changed', acquired));
          const installed = await (options.install ?? installVersion)({study: options.root, statePath: options.statePath,
            script: join(options.studyToolsDir, 'material_versions.py'), python: options.python,
            source, stagedPath, deferIndex: true, sourceSubdirectory: true, retainUnparsed: true});
          await cleanOwnedStaging(options.root, stagedPath, installed);
          id = installed.material_id;
          status = old ? 'updated_or_restored' : 'acquired';
          acquired.push({material_id: id, status: installed.status, filename: file.filename,
            ...(installed.missing_original_id ? {missing_original_id: installed.missing_original_id} : {})});
        }
        if (!permitted(file.page_id)) return finish(failure(options, 'authorization_changed', acquired));
        const reading = await ensureIndex(options, id, true);
        lock.assertHeld();
        finishSource(options, id, source);
        const material = latest().materials.find(m => m.id === id && current(m));
        if (!material) throw new Error('material_changed');
        ids.add(id);
        rows.push({material_id: id, status, reading_mode: reading.reading_mode, visual_units: reading.visual_units ?? [], visual_review_required: !!reading.visual_review_required, sha256: material.sha256, relative_path: material.relative_path,
          source_signature: JSON.stringify(material.moodle_file_sources ?? []),
          source_time: time, verified_at: new Date().toISOString()});
      } catch {return finish(failure(options, 'download_index_or_update_failed_existing_original_preserved', acquired));}
    }
    receipt.material_ids = [...ids];
    if (!ids.size) return finish(failure(options, 'no_matching_materials_select_module'));
    return finish({operation: 'ensure_chapter', course_id: options.courseId, chapter: options.chapter,
      policy: 'remote_timestamp', strict_binary_check: false, status: 'verified', verified: true,
      sync_complete: true, reading_required: true, visual_review_required: rows.some(r => r.visual_review_required),
      reading_instruction: "同步成功不等于核读完成；讲解前核读正文，visual_units 所列页必须查看原图。",
      ready_for_teaching: true, teaching_paused: false, reused: false, materials: rows, acquired,
      related_candidates: [], source_review_required: false, verified_at: rows[0].verified_at,
      full_refresh: false, reviewed: false, mastery_updated: false, receipt_path: options.path,
      limitation: '按来源时间确认同步状态；相同来源时间不会重新下载来检测服务器未更新时间的改写。'});
  } finally {await lock.release();}
}
