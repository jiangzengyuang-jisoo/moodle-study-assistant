// Complete, course-authorized local acquisition. Downloading is not content review.
import {createHash, randomUUID} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {existsSync, mkdirSync, readFileSync, realpathSync, renameSync, writeFileSync, createReadStream, unlinkSync, rmdirSync} from 'node:fs';
import {open, unlink} from 'node:fs/promises';
import {basename, extname, join, relative, resolve, sep} from 'node:path';
import {authorizedCoursePages, normalizeSite} from './content-policy.mjs';
import {installVersion} from './version-files.mjs';

const digest = value => createHash('sha256').update(value).digest('hex');
const readJSON = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
const stamp = () => new Date().toISOString();
const current = row => !row.superseded_by && !['archived', 'deleted'].includes(row.version_status);
const visible = module => module && module.uservisible !== false && module.visible !== 0;
function safeName(value) {
  return typeof value === 'string' && value && basename(value) === value
    && !/[<>:"/\\|?*\x00-\x1f]/.test(value) && !/[. ]$/.test(value) && !['.', '..'].includes(value)
    && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(value);
}
function contained(root, path) {
  const absolute = resolve(path);
  if (!absolute.startsWith(resolve(root) + sep) || realpathSync(absolute) !== absolute) throw new Error('unsafe_local_path');
  return absolute;
}
function atomic(path, value) {
  mkdirSync(resolve(path, '..'), {recursive: true});
  if (realpathSync(resolve(path, '..')) !== resolve(path, '..')) throw new Error('redirected_report_folder');
  if (existsSync(path) && realpathSync(path) !== resolve(path)) throw new Error('redirected_report');
  const temp = path + '.' + randomUUID() + '.tmp';
  let owned = false;
  try {
    writeFileSync(temp, JSON.stringify(value, null, 2), {flag: 'wx'}); owned = true;
    readJSON(temp); renameSync(temp, path);
  } finally {if (owned && existsSync(temp)) unlinkSync(temp);}
}
function cleanURL(value, site) {
  try {
    const url = new URL(String(value).replaceAll('&amp;', '&'), site + '/');
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null;
    for (const key of [...url.searchParams.keys()]) if (/token|sesskey|password|secret|signature|auth|key/i.test(key)) url.searchParams.delete(key);
    if (!/^#[a-z0-9_-]*$/i.test(url.hash)) url.hash = ''; return url.href;
  } catch {return null;}
}
function sanitize(text, token) {
  let output = String(text ?? '');
  if (token) output = output.split(token).join('[REDACTED]');
  return output.replace(/([?&](?:amp;)?(?:wstoken|token|sesskey|password|secret|signature|auth|key)=)[^\s"'<>\]&)]+/gi, '$1[REDACTED]');
}
function category(text) {
  if (/answer|solution|past.?paper|sample.?test|practice|练习|答案/i.test(text)) return 'Practice';
  if (/assignment|homework|\bhw\s*\d|作业/i.test(text)) return 'Assignments';
  if (/tutorial|worksheet|classwork|习题/i.test(text)) return 'Tutorials';
  if (/lecture|slide|notes|chapter|topic|subsection|课件|讲义/i.test(text)) return 'Lectures';
  return 'Other';
}
function fileEndpoint(site, value) {
  const url = new URL(value), base = new URL(site), prefix = base.pathname.replace(/\/$/, '');
  if (url.protocol !== 'https:' || url.origin !== base.origin || url.username || url.password) throw new Error('external_or_unsupported_download');
  if (url.pathname.startsWith(prefix + '/pluginfile.php/')) url.pathname = prefix + '/webservice' + url.pathname.slice(prefix.length);
  if (!url.pathname.startsWith(prefix + '/webservice/pluginfile.php/')) throw new Error('external_or_unsupported_download');
  url.search = ''; url.searchParams.set('forcedownload', '1'); return url;
}
export async function hashFile(path) {
  const hash = createHash('sha256'); for await (const bytes of createReadStream(path)) hash.update(bytes); return hash.digest('hex');
}

// Only this run's UUID staging folder may be cleaned; never recursively delete.
export async function cleanOwnedStaging(root, stagedPath, installed) {
  try {
    const directory = resolve(stagedPath, '..');
    if (resolve(directory, '..') !== join(root, '_Inbox', '.course-content')
        || !/^[a-f0-9-]{36}$/.test(basename(directory)) || realpathSync(directory) !== directory) return;
    if (existsSync(stagedPath)) {
      if (installed?.status !== 'identical' || !installed.sha256 || realpathSync(stagedPath) !== resolve(stagedPath)
          || await hashFile(stagedPath) !== installed.sha256) return;
      unlinkSync(stagedPath);
    }
    rmdirSync(directory); // Refuses a non-empty folder, preserving unexpected content.
  } catch { /* A retained staging file is safer than deleting unverified content. */ }
}

// Pure enumeration is independently testable; HTML is kept as inert text with provenance.
export function enumerateCourseContent(sections, {pageId, courseId, site, snapshot = {}, token, pageContents = []} = {}) {
  const files = new Map(), documents = [], gaps = [];
  const scope = {course_id: courseId, page_id: pageId};
  const source = (module, section = '') => ({...scope, module_id: module.id, module_url: cleanURL(module.url, site),
    title: module.name || section || String(module.id), category: category(`${section} ${module.name ?? ''}`)});
  function addFile(file, parent) {
    const fileurl = cleanURL(file.fileurl || file.url, site);
    if (!fileurl) {gaps.push({...parent, reason: 'file_without_download_url'}); return;}
    let filename = file.filename;
    if (!filename) {try {filename = decodeURIComponent(new URL(fileurl).pathname.split('/').at(-1));} catch {}}
    if (!safeName(filename)) {gaps.push({...parent, filename, source_url: fileurl, reason: 'unsafe_filename'}); return;}
    try {fileEndpoint(site, fileurl);} catch {gaps.push({...parent, filename, source_url: fileurl, reason: 'external_or_unsupported_download'}); return;}
    const item = {...parent, filename, fileurl, filepath: file.filepath || new URL(fileurl).pathname.slice(0, -encodeURIComponent(filename).length),
      filesize: Number.isFinite(file.filesize) ? file.filesize : null, timemodified: file.timemodified ?? file.timecreated ?? null};
    const key = `${parent.module_id}|${parent.post_id ?? ''}|${fileurl}`;
    if (!files.has(key) || item.filesize !== null) files.set(key, item);
  }
  function html(text, parent, label) {
    if (!text || !String(text).trim()) return;
    const raw = sanitize(text, token);
    const links = [...raw.matchAll(/(?:src|href)\s*=\s*["']([^"']+)["']/gi)].map(m => m[1]);
    for (const link of links) {
      if (/@@PLUGINFILE@@/i.test(link)) {gaps.push({...parent, reason: 'unresolved_embedded_file_url'}); continue;}
      const url = cleanURL(link, site);
      if (url && /\/(?:webservice\/)?pluginfile\.php\//.test(new URL(url).pathname)) addFile({fileurl: url}, parent);
      else if (url && /https?:/i.test(link) && /\.(?:pdf|pptx?|docx?|xlsx?|zip|mp4|mp3|png|jpe?g)(?:$|\?)/i.test(url))
        gaps.push({...parent, source_url: url, reason: 'external_link_only'});
    }
    const body = `Title: ${parent.title}\nMoodle course: ${pageId}\nModule: ${parent.module_id}\nPost: ${parent.post_id ?? ''}\nSource: ${parent.module_url ?? site}\n\n${raw}\n`;
    documents.push({...parent, filename: `${label}.html.txt`, filepath: `/local-page/${label}/`,
      fileurl: parent.module_url ?? `${site}/course/view.php?id=${pageId}`, text: body});
  }
  const modules = new Map();
  for (const section of sections) {
    if (!visible(section)) {gaps.push({...scope, module_id: `section-${section.id}`, reason: 'not_accessible'}); continue;}
    html(section.summary, {...scope, module_id: `section-${section.id}`, title: section.name || 'Section',
      category: category(section.name || ''), module_url: `${site}/course/view.php?id=${pageId}#section-${section.section ?? section.id}`}, 'section');
    for (const module of section.modules ?? []) {
      modules.set(Number(module.id), module);
      const parent = source(module, section.name);
      if (!visible(module)) {gaps.push({...parent, reason: 'not_accessible'}); continue;}
      html(module.description, parent, 'description');
      let nativeContent = false;
      for (const [contentIndex, file] of (module.contents ?? []).entries()) {
        if (file.type === 'file' || file.fileurl && file.type !== 'url') {addFile(file, parent); nativeContent = true;}
        else if (typeof file.content === 'string') {
          const identity = file.filename ? `${file.filepath ?? '/'}${file.filename}` : String(contentIndex);
          html(file.content, parent, `content-${digest(identity).slice(0, 12)}`); nativeContent = true;
        }
        else if (file.fileurl && /\/(?:webservice\/)?pluginfile\.php\//.test(file.fileurl)) {addFile(file, parent); nativeContent = true;}
        else if (file.fileurl) gaps.push({...parent, source_url: cleanURL(file.fileurl, site), reason: 'external_link_only'});
      }
      if (module.modname === 'page' && !nativeContent) {
        const page = pageContents.find(row => Number(row.course) === pageId && Number(row.coursemodule) === Number(module.id));
        if (page && typeof page.content === 'string') {
          html(page.content, parent, 'page-content');
          for (const file of [...(page.contentfiles ?? []), ...(page.introfiles ?? [])]) addFile(file, parent);
          nativeContent = true;
        }
      }
      if (['page', 'book'].includes(module.modname) && !nativeContent) gaps.push({...parent, reason: 'native_content_not_exposed_by_api'});
      if (['resource', 'folder'].includes(module.modname) && !(module.contents ?? []).length)
        gaps.push({...parent, reason: 'empty_or_unavailable_file_listing'});
      if (['url', 'lti', 'scorm', 'h5pactivity'].includes(module.modname) && !(module.contents ?? []).length)
        gaps.push({...parent, reason: 'external_or_interactive_resource_link_only'});
    }
  }
  for (const group of snapshot.assignments?.courses ?? []) if (Number(group.id) === pageId) {
    for (const assignment of group.assignments ?? []) {
      const module = modules.get(Number(assignment.cmid)); if (!visible(module)) continue;
      const parent = {...source(module), category: 'Assignments'};
      html(assignment.intro, parent, 'assignment');
      for (const file of [...(assignment.introattachments ?? []), ...(assignment.introfiles ?? [])]) addFile(file, parent);
    }
  }
  for (const quiz of snapshot.quizzes?.quizzes ?? []) if (Number(quiz.course) === pageId) {
    const module = modules.get(Number(quiz.coursemodule)); if (visible(module)) html(quiz.intro, source(module), 'quiz-intro');
  }
  const forums = new Map();
  for (const module of modules.values()) if (module.modname === 'forum' && visible(module)) {
    const collected = (snapshot.forums ?? []).find(f => Number(f.courseid) === pageId && Number(f.module?.id) === Number(module.id));
    if (!collected || !Array.isArray(collected.discussions) || !Array.isArray(snapshot.threads)) {
      gaps.push({...source(module), reason: 'forum_snapshot_unavailable'});
    }
    if (collected) {
      forums.set(Number(module.instance), module);
      for (const discussion of collected.discussions ?? []) {
        if (!(snapshot.threads ?? []).some(thread => Number(thread.forumid) === Number(module.instance)
            && Number(thread.discussion?.discussion) === Number(discussion.discussion)))
          gaps.push({...source(module), discussion_id: discussion.discussion, reason: 'forum_thread_missing'});
      }
    }
  }
  for (const thread of snapshot.threads ?? []) {
    const module = forums.get(Number(thread.forumid)); if (!module) continue;
    const posts = Array.isArray(thread.posts) ? thread.posts : thread.posts?.posts;
    if (!Array.isArray(posts)) {gaps.push({...source(module), reason: 'forum_posts_unavailable'}); continue;}
    if (Number.isFinite(Number(thread.discussion?.numreplies)) && posts.length !== Number(thread.discussion.numreplies) + 1)
      gaps.push({...source(module), reason: 'forum_post_count_mismatch'});
    for (const post of posts) {
      const parent = {...source(module), post_id: post.id, title: post.subject || module.name,
        module_url: cleanURL(post.urls?.view || `${site}/mod/forum/discuss.php?d=${thread.discussion.discussion}#p${post.id}`, site)};
      html(post.message, parent, `post-${post.id}`);
      for (const file of [...(post.attachments ?? []), ...(post.messageinlinefiles ?? [])]) addFile(file, parent);
    }
  }
  return {files: [...files.values()], documents, gaps};
}

export async function stageDownload(client, item, {studyRoot, site, timeoutMs, maxBytes}) {
  const url = fileEndpoint(site, item.fileurl); url.searchParams.set('token', client.token);
  const directory = join(studyRoot, '_Inbox', '.course-content', randomUUID());
  mkdirSync(directory, {recursive: true}); contained(studyRoot, directory);
  const target = join(directory, item.filename), temporary = target + '.part';
  let handle, size = 0, first = Buffer.alloc(0);
  try {
    const response = await (client.transport ?? globalThis.fetch)(url.href, {method: 'GET', redirect: 'error', signal: AbortSignal.timeout(timeoutMs)});
    const mime = response.headers.get('content-type') ?? '';
    if (!response.ok || !response.body || Number(response.headers.get('content-length')) > maxBytes) throw new Error('response');
    if (/text\/html|application\/json/i.test(mime) && !/\.(html?|json|txt)$/i.test(item.filename)) throw new Error('unexpected_auth_response');
    handle = await open(temporary, 'wx');
    for await (const chunk of response.body) {
      const bytes = Buffer.from(chunk); size += bytes.length;
      if (size > maxBytes) throw new Error('size_limit');
      if (first.length < 1024) first = Buffer.concat([first, bytes.subarray(0, 1024 - first.length)]);
      await handle.writeFile(bytes);
    }
    await handle.sync(); await handle.close(); handle = null;
    if (!size || (item.filesize > 0 && item.filesize !== size)) throw new Error('size_mismatch');
    if (extname(item.filename).toLowerCase() === '.pdf' && !first.includes(Buffer.from('%PDF-'))) throw new Error('invalid_pdf');
    if (/\.(docx|pptx|xlsx|odt)$/i.test(item.filename) && first.subarray(0, 2).toString() !== 'PK') throw new Error('invalid_office_file');
    if (/text\/html/i.test(mime) && /name=["'](?:username|password)|loginform|login\/index\.php/i.test(first.toString())) throw new Error('login_page');
    renameSync(temporary, target); return target;
  } catch {
    if (handle) await handle.close().catch(() => {});
    await unlink(temporary).catch(() => {});
    await cleanOwnedStaging(studyRoot, target);
    throw new Error('download_failed_network_access_or_integrity'); // Never leak authenticated URLs from fetch errors.
  }
}

export async function syncCourseContent(client, state, options = {}) {
  const {studyRoot, statePath, studyToolsDir, python, snapshot = {}, reportPath = join(studyRoot, '_System/CourseContent/latest.json'),
    timeoutMs = 300000, maxBytes = Infinity, install = installVersion, indexCourse} = options;
  const site = normalizeSite(options.site ?? client.baseUrl), userId = options.userId ?? client.userId;
  // The live connection identity takes precedence over a caller or stale snapshot.
  if (normalizeSite(client.baseUrl) !== site || client.userId !== userId) throw new Error('account_scope_mismatch');
  const granted = authorizedCoursePages(state, {site, userId});
  const result = {started_at: stamp(), status: 'not_authorized', granted_course_ids: [...granted.keys()], course_ids: [...granted.keys()],
    account_scope: {site, user_id: userId}, collection_binding: snapshot.collection_binding,
    files: [], documents: [],
    skipped: [], errors: [], index: [], counts: {}, reviewed: false, mastery_updated: false};
  if (!granted.size) return result;
  const root = realpathSync(studyRoot), manifestPath = join(root, '_System/CourseContent/manifest.json');
  const manifest = existsSync(manifestPath) ? readJSON(contained(root, manifestPath)) : {version: 1, files: {}};
  if (manifest.version !== 1 || !manifest.files || Array.isArray(manifest.files)) throw new Error('invalid_content_manifest');
  const latest = () => readJSON(contained(root, statePath));
  const permitted = (pageId, courseId) => authorizedCoursePages(latest(), {site, userId}).get(pageId) === courseId;
  const items = [], touched = new Set();
  if (snapshot.collection_complete !== true) result.errors.push({reason: 'metadata_snapshot_incomplete'});
  for (const limit of snapshot.scope_limits ?? []) result.skipped.push({reason: 'source_permission_limit', scope: limit.scope});
  for (const [pageId, courseId] of granted) {
    if (!permitted(pageId, courseId)) {result.skipped.push({page_id: pageId, reason: 'authorization_revoked'}); continue;}
    try {
      const sections = await client.call('core_course_get_contents', {courseid: pageId});
      if (!Array.isArray(sections)) throw new Error('invalid_sections');
      let pageContents = [];
      const missingPages = sections.flatMap(section => section.modules ?? []).some(module => visible(module) && module.modname === 'page'
        && !(module.contents ?? []).some(file => file.type === 'file' || typeof file.content === 'string' || file.fileurl && file.type !== 'url'));
      if (missingPages && client.supports?.('mod_page_get_pages_by_courses') !== false && permitted(pageId, courseId)) {
        try {
          // Official read API exposes page HTML and attached files without view/completion writes.
          const pages = await client.call('mod_page_get_pages_by_courses', {'courseids[0]': pageId});
          if (!Array.isArray(pages.pages)) throw new Error('invalid_page_content');
          pageContents = pages.pages;
          if (pages.warnings?.length) result.skipped.push({page_id: pageId, reason: 'page_content_api_warning'});
        } catch {result.errors.push({page_id: pageId, reason: 'page_content_api_unavailable'});}
      }
      const found = enumerateCourseContent(sections, {pageId, courseId, site, snapshot, token: client.token, pageContents});
      items.push(...found.files, ...found.documents); result.skipped.push(...found.gaps);
    } catch {result.errors.push({page_id: pageId, reason: 'course_enumeration_failed'});}
  }
  for (const item of items) {
    const row = {...item}; delete row.text;
    const destination = item.text === undefined ? result.files : result.documents;
    if (!permitted(item.page_id, item.course_id)) {result.skipped.push({...row, reason: 'authorization_revoked'}); continue;}
    touched.add(item.course_id);
    const key = digest(JSON.stringify([site, userId, item.course_id, item.page_id, item.module_id, item.post_id ?? null, item.filepath, item.filename, item.fileurl]));
    const fingerprint = digest(JSON.stringify([item.filesize, item.timemodified, item.text]));
    const previous = manifest.files[key];
    try {
      // No modification stamp => fetch again to detect same-size replacements.
      if (previous?.fingerprint === fingerprint && (item.timemodified !== null && item.timemodified !== undefined || item.text !== undefined)) {
        const material = (latest().materials ?? []).find(m => m.id === previous.material_id && current(m));
        let verified = false;
        if (material?.sha256 === previous.sha256 && material.relative_path === previous.relative_path) {
          try {verified = await hashFile(contained(root, join(root, material.relative_path))) === previous.sha256;}
          catch (error) {if (error.code !== 'ENOENT') throw error;}
        }
        if (verified) {
          destination.push({...row, ...previous, status: 'unchanged'}); continue;
        }
      }
      let stagedPath;
      if (item.text !== undefined) {
        const directory = join(root, '_Inbox', '.course-content', randomUUID());
        mkdirSync(directory, {recursive: true}); contained(root, directory);
        stagedPath = join(directory, item.filename); writeFileSync(stagedPath, item.text, {flag: 'wx'});
      } else stagedPath = await stageDownload(client, item, {studyRoot: root, site, timeoutMs, maxBytes});
      if (!permitted(item.page_id, item.course_id)) throw new Error('authorization_revoked');
      const installed = await install({study: root, statePath, script: join(studyToolsDir, 'material_versions.py'), python,
        source: {...row, scope: {site, user_id: userId, page_id: item.page_id}}, stagedPath,
        deferIndex: true, sourceSubdirectory: true, retainUnparsed: true});
      manifest.files[key] = {...installed, fingerprint, checked_at: stamp()}; atomic(manifestPath, manifest);
      await cleanOwnedStaging(root, stagedPath, installed);
      destination.push({...row, ...installed, status: installed.status === 'identical' ? 'identical' : 'downloaded'});
    } catch {destination.push({...row, status: 'failed'}); result.errors.push({...row, reason: 'download_or_install_failed_existing_materials_preserved'});}
    atomic(reportPath, {...result, status: 'running', finished_at: null});
  }
  for (const courseId of touched) {
    try {
      let indexed;
      if (indexCourse) indexed = await indexCourse(courseId);
      else {
        const child = spawnSync(python, [join(studyToolsDir, 'course_content.py'), '--state', statePath, 'index', '--course', courseId],
          {encoding: 'utf8', windowsHide: true, timeout: 900000, maxBuffer: 16 * 1024 * 1024,
            env: {...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1'}});
        if (child.error || child.status !== 0) throw new Error('index_failed');
        indexed = JSON.parse(child.stdout);
      }
      result.index.push({course_id: courseId, ...indexed});
      for (const material of indexed.materials ?? []) if (material.status !== 'indexed' || material.text_units === 0)
        result.skipped.push({course_id: courseId, material_id: material.id, reason: material.status === 'indexed' ? 'no_searchable_text' : material.status});
    } catch {result.errors.push({course_id: courseId, reason: 'index_failed_downloads_retained'});}
  }
  result.counts = {discovered_files: result.files.length, saved_documents: result.documents.filter(f => f.status !== 'failed').length,
    downloaded: result.files.filter(f => f.status === 'downloaded').length,
    reused: result.files.filter(f => ['unchanged', 'identical'].includes(f.status)).length,
    failed: result.errors.length, gaps: result.skipped.length};
  result.status = result.errors.length || result.skipped.length ? 'partial' : 'complete';
  result.finished_at = stamp(); atomic(reportPath, result); return result;
}
