// Course-level authorization and one-shot material library sync.
//
// It enumerates every selected Moodle course page, downloads files the current
// student account can access directly, saves announcements/assignment
// requirements/discussion text with precise provenance, records every skipped or
// failed item with a reason, and then builds the local page index.
//
// Nothing here starts a quiz attempt, submits an assignment, posts, or changes a
// school state. A successful download or parse is never verification or mastery.
import {createHash, randomUUID} from 'node:crypto';
import {existsSync, mkdirSync, readFileSync, realpathSync, renameSync, writeFileSync, unlinkSync, rmSync} from 'node:fs';
import {dirname, join, relative, resolve, sep} from 'node:path';
import {spawnSync} from 'node:child_process';
import {python} from './state-bridge.mjs';
import {
  MAX_BYTES, digest, fetchCourseFile, isDownloadableName, isImageName, remoteName,
  safeName, safeSubdir, webserviceFileURL, extensionOf,
} from './course-files.mjs';
import {checkWarnings, readDiscussions, verifyPosts} from './refresh.mjs';

export const SCOPE_VERSION = 1;
export const SCOPE_TEXT = '所选课程当前账号可访问、可直接下载的全部课程资料（课件、作业题目附件、阅读材料、练习文件、论坛附件、正文图片、可直接下载的音视频），以及公告、作业要求、讨论文字及准确出处；不开始测验尝试、不提交作业、不发帖、不修改学校状态；流媒体、外部网站、额外登录或权限受限内容只保留入口和原因。';
export const SNAPSHOT_MAX_AGE_MINUTES = 360;
const LEDGER_SCHEMA = 1;
const REPORT_SCHEMA = 1;

export const SKIP_REASONS = Object.freeze({
  quiz_questions_require_attempt_not_started: '测验题目需在测验尝试中查看；按授权约定不自动开始测验尝试，保留入口。',
  external_site_not_downloaded: '外部网站或云盘内容不能通过学校文件接口直接获取，保留入口。',
  external_streaming_not_downloaded: '流媒体/录像需要站点内登录或播放器，不能作为文件直接下载，保留入口。',
  restricted_by_course_permission: '当前账号没有该模块的访问权限，保留入口。',
  activity_without_downloadable_content: '该活动类型没有可直接下载的文件或正文，保留入口。',
  label_body_not_exposed_by_api: '标签正文未通过课程接口返回，只保留模块入口。',
  folder_contents_not_exposed: '文件夹没有通过接口返回任何文件，可能需站点内权限或外部链接。',
  no_files_exposed_by_api: '该资源模块没有通过接口返回可下载文件。',
  unsupported_activity_type: '该活动类型不在可自动获取范围内，保留入口。',
  unsupported_file_type: '文件类型不在可安全归档的白名单内。',
  unsafe_filename: '文件名不安全，不能写入本地目录。',
  file_source_not_direct: '文件地址不是学校文件接口，不能直接获取。',
});
export const STATUS = Object.freeze({
  installed: 'installed', identical: 'identical', duplicate: 'duplicate',
  already_current: 'already_current', failed: 'failed', skipped: 'skipped', blocked: 'blocked',
});

export function nowISO() { return new Date().toISOString(); }
export function readJSON(path) { return JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, '')); }

export function atomicJSON(path, value) {
  const content = JSON.stringify(value, null, 2) + '\n';
  JSON.parse(content);
  const temp = `${path}.${process.pid}.${randomUUID().slice(0, 8)}.tmp`;
  writeFileSync(temp, content, {encoding: 'utf8', flag: 'wx'});
  renameSync(temp, path);
}

function safeError(error) {
  // Errors from fetch may carry authenticated URLs; keep only local/stable text.
  const text = String(error?.message ?? error ?? 'unknown error');
  return text.replace(/[?&](?:token|wstoken|sesskey)=[^&\s"']+/gi, '$1[REDACTED]').slice(0, 400);
}

function sha256(value) { return createHash('sha256').update(value).digest('hex'); }
function sha256Bytes(bytes) { return createHash('sha256').update(bytes).digest('hex'); }

export function resolveSubject(state, courseId) {
  const courses = new Map((state.courses ?? []).map(c => [c.id, c]));
  let current = courseId, guard = 0;
  while (courses.get(current)?.merged_into && guard++ < 10) {
    const next = courses.get(current).merged_into;
    if (!courses.has(next)) break;
    current = next;
  }
  return current;
}

export function selectedPages(state, courseIds = null) {
  const wanted = courseIds ? new Set(courseIds) : null;
  const pages = [];
  for (const course of state.courses ?? []) {
    if (wanted && !wanted.has(course.id)) continue;
    const moodleIds = (course.moodle_course_ids ?? []).filter(Number.isInteger);
    for (const moodleCourseid of moodleIds) {
      pages.push({courseId: course.id, moodleCourseid, course, subject: resolveSubject(state, course.id)});
    }
  }
  if (wanted) {
    const known = new Set(pages.map(p => p.courseId));
    const missing = [...wanted].filter(id => !known.has(id));
    if (missing.length) throw new Error('Unknown or unselected course IDs: ' + missing.join(', '));
  }
  return pages;
}

export function authorizationReport(state, pages) {
  const authorization = state.settings?.course_library_authorization ?? null;
  const grants = authorization?.courses ?? {};
  const authorized = [], blocked = [];
  for (const page of pages) {
    const grant = grants[page.courseId];
    if (!grant) blocked.push({page, reason: 'course_not_authorized'});
    else if (grant.mode !== 'full') blocked.push({page, reason: 'authorization_info_only'});
    else if (Number(grant.moodle_courseid) !== Number(page.moodleCourseid)) blocked.push({page, reason: 'authorization_scope_changed'});
    else authorized.push(page);
  }
  return {authorization, authorized, blocked};
}

export function coursePageURL(baseUrl, moodleCourseid) {
  return new URL(`/course/view.php?id=${moodleCourseid}`, baseUrl).toString();
}

// Very small HTML-to-text conversion: enough to preserve wording, paragraph and
// table boundaries. It never claims to preserve layout or formulas.
export function htmlToText(html) {
  if (!html || typeof html !== 'string') return '';
  const decoded = html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(?:p|div|li|tr|h[1-6]|blockquote|section|article)>/gi, '\n')
    .replace(/<(?:td|th)[^>]*>/gi, '\t')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return decoded;
}

export function categoryFor({moduleName = '', sectionName = '', filename = '', modname = ''}) {
  const text = `${sectionName} ${moduleName} ${filename} ${modname}`.toLowerCase();
  if (/tutorial/.test(text)) return 'Tutorials';
  if (/assignment|homework|\bhw\b|submission|group project|project report|weekly assignment/.test(text)) return 'Assignments';
  if (/exercise|practice|question bank|past ?paper|mock|quiz|problem set/.test(text)) return 'Practice';
  if (/lecture|note|slide|classwork|chapter|record|video|reading|textbook|seminar|course information|welcome|policy/.test(text)) return 'Lectures';
  return 'Other';
}

export function externalReason(url) {
  let host = '';
  try { host = new URL(url).hostname.toLowerCase(); } catch { return 'external_site_not_downloaded'; }
  if (/(^|\.)(youtube\.com|youtu\.be|vimeo\.com|zoom\.us|panopto\.com|echo360\.org|kaltura\.com|mediasite\.com|cloud\.panopto)/.test(host)
      || /recording|video/.test(host)) return 'external_streaming_not_downloaded';
  if (/(^|\.)(drive\.google\.com|docs\.google\.com|dropbox\.com|sharepoint\.com|onedrive\.live\.com|box\.com|notion\.so|github\.com)/.test(host))
    return 'external_site_not_downloaded';
  return 'external_site_not_downloaded';
}

function moduleContext(page, module, section) {
  return {
    course_id: page.subject,
    page_course_id: page.courseId,
    moodle_courseid: page.moodleCourseid,
    page_url: coursePageURL(page.baseUrl, page.moodleCourseid),
    page_name: page.course?.name ?? null,
    section: section?.name ?? null,
    section_index: section?.index ?? null,
    module_id: module.id,
    module_name: module.name,
    module_type: module.modname,
  };
}

function makeSkip({page, module, section, reason, detail = null, url = null}) {
  return {
    kind: 'skip', status: STATUS.skipped, reason,
    detail: detail ?? SKIP_REASONS[reason] ?? null,
    courseId: page.subject, pageId: page.courseId, moodleCourseid: page.moodleCourseid,
    moduleId: module?.id ?? null, moduleType: module?.modname ?? null,
    moduleName: module?.name ?? null, section: section?.name ?? null,
    filename: null, url: url ?? module?.url ?? null,
    source: moduleContext(page, module ?? {}, section),
  };
}

function inlineImagesFromHTML(html, page) {
  const found = [];
  if (!html || typeof html !== 'string') return found;
  const pattern = /(?:https?:\/\/[^\s"'<>]*)?\/?(?:webservice\/)?pluginfile\.php[^\s"'<>]*/gi;
  for (const match of html.matchAll(pattern)) {
    let raw = match[0].replace(/&amp;/g, '&');
    if (!raw) continue;
    if (!/^https?:\/\//i.test(raw)) raw = new URL(raw.replace(/^\//, '/'), page.baseUrl).toString();
    let decoded;
    try {
      const url = new URL(raw);
      decoded = decodeURIComponent(url.pathname.split('/').at(-1) ?? '');
    } catch { continue; }
    if (!isImageName(decoded)) continue;
    if (found.some(entry => entry.url === raw)) continue;
    found.push({url: raw, filename: decoded});
  }
  return found;
}

async function mapLimit(items, limit, work) {
  const results = new Array(items.length);
  let next = 0;
  const workers = Array.from({length: Math.max(1, Math.min(limit, items.length))}, async () => {
    while (next < items.length) {
      const index = next++;
      results[index] = await work(items[index], index);
    }
  });
  await Promise.all(workers);
  return results;
}

function snapshotProvenance(snapshotPath, root) {
  const absolute = resolve(root, snapshotPath);
  const study = resolve(root, 'Study');
  if (!absolute.startsWith(study + sep)) throw new Error('Snapshot must be inside the Study folder');
  const bytes = readFileSync(absolute);
  const snapshot = JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, ''));
  if (!snapshot || !Array.isArray(snapshot.pages) || !Array.isArray(snapshot.threads) || !snapshot.assignments)
    throw new Error('Not a complete Moodle collection snapshot');
  const finished = snapshot.finished_at ?? snapshot.started_at ?? null;
  return {
    snapshot,
    provenance: {
      path: relative(root, absolute).replaceAll('\\', '/'),
      run_id: snapshot.collection_binding?.run_id ?? null,
      checked_at: finished,
      sha256: sha256Bytes(bytes),
      collection_complete: snapshot.collection_complete === true,
    },
  };
}

export function newestSnapshotPath(root, maxAgeMinutes = SNAPSHOT_MAX_AGE_MINUTES) {
  for (const name of ['refresh-latest.json', 'refresh-last-complete.json']) {
    const path = join(root, 'Study/_System', name);
    if (!existsSync(path)) continue;
    try {
      const snapshot = readJSON(path);
      const at = Date.parse(snapshot.finished_at ?? snapshot.started_at ?? '');
      if (!Number.isFinite(at) || Date.now() - at > maxAgeMinutes * 60000) continue;
      if (!Array.isArray(snapshot.pages) || !snapshot.pages.length) continue;
      return path;
    } catch { /* try the next candidate */ }
  }
  return null;
}

function emptyTextSources() {
  return {assignments: new Map(), quizzes: new Map(), threads: new Map(), collectedForums: new Set(),
    capturedAt: null, source: 'unavailable', snapshotRunId: null, errors: []};
}

function textSourcesFromSnapshot(snapshot, provenance, pages) {
  const sources = emptyTextSources();
  sources.source = 'snapshot';
  sources.capturedAt = provenance.checked_at;
  sources.snapshotRunId = provenance.run_id;
  const pageKeys = new Set(pages.map(p => p.courseId));
  for (const page of snapshot.pages) {
    if (!pageKeys.has(page.course_id)) continue;
    sources.capturedAt = sources.capturedAt ?? null;
  }
  for (const course of snapshot.assignments.courses ?? []) {
    sources.assignments.set(Number(course.id), course.assignments ?? []);
  }
  for (const quiz of snapshot.quizzes?.quizzes ?? []) {
    const list = sources.quizzes.get(Number(quiz.course)) ?? [];
    list.push(quiz); sources.quizzes.set(Number(quiz.course), list);
  }
  for (const thread of snapshot.threads ?? []) {
    const key = `${thread.course_id}|${thread.forumid}`;
    const list = sources.threads.get(key) ?? [];
    list.push(thread); sources.threads.set(key, list);
  }
  for (const forum of snapshot.forums ?? []) {
    if (forum?.module?.instance) sources.collectedForums.add(`${forum.course_id}|${forum.module.instance}`);
  }
  return sources;
}

async function liveTextSources(client, pages, enumerated) {
  const sources = emptyTextSources();
  sources.source = 'live';
  sources.capturedAt = nowISO();
  const courseArgs = Object.fromEntries(pages.map((page, index) => [`courseids[${index}]`, page.moodleCourseid]));
  try {
    const data = await client.call('mod_assign_get_assignments', courseArgs);
    for (const course of data?.courses ?? []) sources.assignments.set(Number(course.id), course.assignments ?? []);
  } catch (error) {
    sources.errors.push({scope: 'assignments', reason: safeError(error)});
  }
  try {
    const data = checkWarnings(await client.call('mod_quiz_get_quizzes_by_courses', courseArgs));
    for (const quiz of data?.quizzes ?? []) {
      const list = sources.quizzes.get(Number(quiz.course)) ?? [];
      list.push(quiz); sources.quizzes.set(Number(quiz.course), list);
    }
  } catch (error) {
    sources.errors.push({scope: 'quizzes', reason: safeError(error)});
  }
  const forumTasks = [];
  for (let index = 0; index < pages.length; index++) {
    for (const module of enumerated[index].modules) {
      if (module.modname !== 'forum' || module.uservisible === false || module.visible === 0) continue;
      forumTasks.push({page: pages[index], module});
    }
  }
  const results = await mapLimit(forumTasks, 4, async ({page, module}) => {
    const key = `${page.courseId}|${module.instance}`;
    try {
      const discussions = await readDiscussions(client, module.instance);
      const threads = await mapLimit(discussions, 4, async discussion => ({
        course_id: page.courseId, forumid: module.instance, forum_url: module.url,
        discussion,
        posts: verifyPosts(discussion, await client.call('mod_forum_get_discussion_posts',
          {discussionid: discussion.discussion, sortby: 'created', sortdirection: 'ASC'})),
      }));
      sources.threads.set(key, threads);
      sources.collectedForums.add(key);
    } catch (error) {
      sources.errors.push({scope: `forum:${module.instance}`, reason: safeError(error)});
    }
  });
  void results;
  return sources;
}

export async function enumerateCourse(client, page) {
  const sections = await client.call('core_course_get_contents', {courseid: page.moodleCourseid});
  if (!Array.isArray(sections)) throw new Error('Invalid course contents response');
  const normalized = [];
  const modules = [];
  sections.forEach((section, index) => {
    const entry = {
      name: section.name ?? '', index, summary: section.summary ?? '',
      uservisible: section.uservisible !== false,
      modules: [],
    };
    for (const module of section.modules ?? []) {
      const item = {
        id: module.id, name: module.name ?? '', modname: module.modname ?? 'unknown',
        instance: module.instance ?? null, url: module.url ?? null,
        visible: module.visible, uservisible: module.uservisible,
        section: entry, contents: Array.isArray(module.contents) ? module.contents : [],
      };
      entry.modules.push(item);
      modules.push(item);
    }
    normalized.push(entry);
  });
  return {page, sections: normalized, modules};
}

// Build every planned file item, text capture, and explained skip for one page.
export function planPage({page, enumerated, texts, state}) {
  const items = [], skips = [];
  const base = {courseId: page.subject, pageId: page.courseId, moodleCourseid: page.moodleCourseid};
  const seenURLs = new Map();
  const enumeratedModules = enumerated.modules ?? enumerated.sections.flatMap(section => section.modules);
  const enumeratedModuleCount = enumeratedModules.length;
  const pushFile = ({module, section, file, filepath, titlePrefix = null, categoryOverride = null, inline = false}) => {
    const rawURL = file.fileurl ?? file.url;
    const filename = file.filename ?? remoteName({url: rawURL});
    if (!isDownloadableName(filename)) {
      skips.push(makeSkip({page, module, section, reason: safeName(filename) ? 'unsupported_file_type' : 'unsafe_filename',
        detail: `文件：${String(filename).slice(0, 160)}`, url: module?.url}));
      return;
    }
    let cleanURL;
    try { cleanURL = webserviceFileURL(page.baseUrl, rawURL).toString(); }
    catch {
      skips.push(makeSkip({page, module, section, reason: 'file_source_not_direct',
        detail: `文件：${filename}`}));
      return;
    }
    if (seenURLs.has(cleanURL)) {
      // The same remote file can appear as a post attachment and an inline image.
      // It is still one source; not a gap, so it is counted instead of reported.
      return;
    }
    const filepathValue = filepath ?? file.filepath ?? '/';
    const item = {
      kind: 'file',
      key: `${page.subject}|${module?.id ?? 'page'}|${filepathValue}|${filename}`,
      ...base,
      subject: page.subject,
      moduleId: module?.id ?? null,
      moduleType: module?.modname ?? 'page',
      moduleName: module?.name ?? null,
      section: section?.name ?? null,
      sectionIndex: section?.index ?? null,
      filename, filepath: filepathValue,
      title: titlePrefix ? `${titlePrefix} / ${filename}` : (module?.name ?? filename),
      category: categoryOverride ?? categoryFor({
        moduleName: module?.name ?? '', sectionName: section?.name ?? '', filename,
        modname: module?.modname ?? ''}),
      url: rawURL, cleanUrl: cleanURL,
      activityUrl: module?.url ?? null,
      filesize: Number.isFinite(Number(file.filesize)) ? Number(file.filesize) : null,
      timemodified: Number.isFinite(Number(file.timemodified)) ? Number(file.timemodified) : null,
      inline,
      sourcePages: [moduleContext(page, module ?? {}, section)],
    };
    seenURLs.set(cleanURL, item.key);
    items.push(item);
    return item;
  };
  const pushCapture = ({module, section, filename, filepath, title, category, markdown, captureKind, extra = {}}) => {
    const content = markdown.endsWith('\n') ? markdown : markdown + '\n';
    const item = {
      kind: 'capture',
      key: `${page.subject}|${module?.id ?? page.moodleCourseid}|${filepath}|${filename}`,
      ...base,
      subject: page.subject,
      moduleId: module?.id ?? page.moodleCourseid,
      moduleType: module?.modname ?? 'course',
      moduleName: module?.name ?? page.course?.name ?? null,
      section: section?.name ?? null,
      sectionIndex: section?.index ?? null,
      filename, filepath,
      title,
      category,
      url: module?.url ?? coursePageURL(page.baseUrl, page.moodleCourseid),
      cleanUrl: null,
      activityUrl: module?.url ?? coursePageURL(page.baseUrl, page.moodleCourseid),
      filesize: Buffer.byteLength(content), timemodified: null,
      inline: false,
      capture: {kind: captureKind, markdown: content, ...extra},
      sourcePages: [moduleContext(page, module ?? {}, section)],
    };
    items.push(item);
    return item;
  };

  for (const module of enumeratedModules) {
    const section = module.section;
    if (module.uservisible === false || module.visible === 0) {
      skips.push(makeSkip({page, module, section, reason: 'restricted_by_course_permission',
        detail: module.visible === 0 ? '模块对学生隐藏' : '当前账号无访问权限'}));
      continue;
    }
    if (module.modname === 'forum') {
      const key = `${page.courseId}|${module.instance}`;
      const threads = texts.threads.get(key) ?? [];
      const collected = texts.collectedForums.has(key);
      const attachments = [];
      for (const thread of threads) {
        const discussion = thread.discussion ?? {};
        for (const post of thread.posts ?? []) {
          for (const file of post.attachments ?? []) {
            attachments.push({module, section, file,
              filepath: `/forum/${post.id}/`,
              titlePrefix: module.name, categoryOverride: 'Other'});
          }
          for (const image of inlineImagesFromHTML(post.message, page)) {
            attachments.push({module, section, file: image,
              filepath: `/forum-inline/${post.id}/`,
              titlePrefix: module.name, categoryOverride: 'Other'});
          }
        }
        for (const image of inlineImagesFromHTML(discussion.message, page)) {
          attachments.push({module, section, file: image,
            filepath: `/forum-inline/${discussion.id}/`,
            titlePrefix: module.name, categoryOverride: 'Other'});
        }
      }
      for (const attachment of attachments) pushFile(attachment);
      pushCapture({
        module, section, captureKind: 'forum',
        filename: captureName('forum', module, 'md'),
        filepath: '/moodle-text/',
        title: `${page.course?.shortname ?? page.subject} / ${module.name}（论坛文字）`,
        category: 'Other',
        markdown: forumCaptureMarkdown({page, module, threads, collected, attachments}),
        extra: {threads: threads.length, posts: threads.reduce((n, t) => n + (t.posts?.length ?? 0), 0)},
      });
      continue;
    }
    if (module.modname === 'assign') {
      const assignments = texts.assignments.get(Number(page.moodleCourseid)) ?? [];
      const assignment = assignments.find(a => a.cmid === module.id || a.id === module.instance);
      const attachments = [];
      for (const file of assignment?.introattachments ?? []) {
        attachments.push({module, section, file, filepath: '/assign-introattachment/',
          titlePrefix: module.name, categoryOverride: 'Assignments'});
      }
      for (const file of assignment?.introfiles ?? []) {
        attachments.push({module, section, file, filepath: '/assign-introfile/',
          titlePrefix: module.name, categoryOverride: 'Assignments'});
      }
      for (const image of inlineImagesFromHTML(assignment?.intro ?? '', page)) {
        attachments.push({module, section, file: image, filepath: '/assign-inline/',
          titlePrefix: module.name, categoryOverride: 'Assignments'});
      }
      for (const attachment of attachments) pushFile(attachment);
      pushCapture({
        module, section, captureKind: 'assignment',
        filename: captureName('assignment', module, 'md'),
        filepath: '/moodle-text/',
        title: `${page.course?.shortname ?? page.subject} / ${module.name}（作业要求）`,
        category: 'Assignments',
        markdown: assignmentCaptureMarkdown({page, module, assignment, attachments}),
        extra: {attachments: attachments.length},
      });
      continue;
    }
    if (module.modname === 'quiz') {
      const quizzes = texts.quizzes.get(Number(page.moodleCourseid)) ?? [];
      const quiz = quizzes.find(q => q.coursemodule === module.id || q.id === module.instance);
      skips.push(makeSkip({page, module, section, reason: 'quiz_questions_require_attempt_not_started',
        detail: quiz ? `测验：${quiz.name}${quiz.timeclose ? `，关闭时间戳 ${quiz.timeclose}` : ''}` : '测验' }));
      continue;
    }
    if (module.modname === 'label') {
      skips.push(makeSkip({page, module, section, reason: 'label_body_not_exposed_by_api'}));
      continue;
    }
    if (['questionnaire', 'scheduler', 'choicegroup', 'subsection', 'forum2', 'attendance', 'feedback']
      .includes(module.modname)) {
      skips.push(makeSkip({page, module, section, reason: 'activity_without_downloadable_content'}));
      continue;
    }
    if (['resource', 'folder', 'page'].includes(module.modname)) {
      const files = module.contents.filter(content => content.type === 'file');
      const external = module.contents.filter(content => content.type === 'url');
      for (const content of external) {
        const reason = externalReason(content.fileurl ?? content.url ?? '');
        skips.push(makeSkip({page, module, section, reason,
          detail: content.filename ?? content.url ?? null, url: content.fileurl ?? content.url ?? module.url}));
      }
      if (!files.length && !external.length) {
        skips.push(makeSkip({page, module, section,
          reason: module.modname === 'folder' ? 'folder_contents_not_exposed' : 'no_files_exposed_by_api'}));
      }
      for (const file of files) {
        pushFile({module, section, file});
      }
      if (module.modname === 'page') {
        pushCapture({
          module, section, captureKind: 'page-module',
          filename: captureName('page', module, 'md'),
          filepath: '/moodle-text/',
          title: `${page.course?.shortname ?? page.subject} / ${module.name}（页面说明）`,
          category: categoryFor({moduleName: module.name, sectionName: section?.name ?? '', modname: 'page'}),
          markdown: pageModuleCaptureMarkdown({page, module, section}),
        });
      }
      continue;
    }
    if (module.modname === 'url') {
      const target = module.contents.find(content => content.type === 'url');
      const url = target?.fileurl ?? target?.url ?? module.url;
      skips.push(makeSkip({page, module, section, reason: externalReason(url), url}));
      continue;
    }
    skips.push(makeSkip({page, module, section, reason: 'unsupported_activity_type',
      detail: `modname=${module.modname}`}));
  }

  pushCapture({
    module: null, section: null, captureKind: 'course-page',
    filename: `course-page-${page.moodleCourseid}.md`,
    filepath: '/moodle-page/',
    title: `${page.course?.shortname ?? page.subject} / 课程页面结构（${page.moodleCourseid}）`,
    category: 'Other',
    markdown: coursePageCaptureMarkdown({page, enumerated}),
    extra: {sections: enumerated.sections.length, modules: enumeratedModuleCount},
  });
  return {items, skips};
}

function captureName(prefix, module, extension) {
  const raw = String(module?.name ?? 'item');
  const slug = raw.replace(/[<>:"/\\|?*\x00-\x1f]/g, ' ').replace(/\s+/g, '-').replace(/^[-. ]+|[-. ]+$/g, '').slice(0, 60) || 'item';
  return `${prefix}-${module?.id ?? 'course'}-${slug}.${extension}`;
}

function captureHeader({page, module, captureKind}) {
  // Only stable provenance belongs in the hashed content. Capture time and the
  // source snapshot are stored as material metadata, so an unchanged course page
  // does not create a new text-capture version on every sync.
  const lines = [
    '<!-- course-library-capture',
    `capture_kind: ${captureKind}`,
    `course_page_id: ${page.courseId}`,
    `moodle_courseid: ${page.moodleCourseid}`,
    `course_name: ${JSON.stringify(page.course?.name ?? '')}`,
    `module_id: ${module?.id ?? 'course-page'}`,
    `module_type: ${module?.modname ?? 'course'}`,
    `module_name: ${JSON.stringify(module?.name ?? '')}`,
    `module_url: ${module?.url ?? coursePageURL(page.baseUrl, page.moodleCourseid)}`,
    `course_page_url: ${coursePageURL(page.baseUrl, page.moodleCourseid)}`,
    'note: 文本捕获只保存公告/作业要求/讨论文字及出处；下载或解析成功不等于已核读或已掌握。',
    '-->',
    '',
  ];
  return lines.join('\n');
}

function forumCaptureMarkdown({page, module, threads, collected, attachments}) {
  const lines = [captureHeader({page, module, captureKind: 'forum'})];
  lines.push(`# ${module.name}（论坛文字记录）`, '');
  lines.push(`- 课程页面：${coursePageURL(page.baseUrl, page.moodleCourseid)}`);
  lines.push(`- 论坛模块：${module.url ?? ''}`);
  lines.push(`- 论坛实例 ID：${module.instance}`);
  lines.push(`- 讨论数：${threads.length}${collected ? '' : '（本次未完整采集该论坛，缺失部分见缺口清单）'}`);
  lines.push(`- 附件/内联图候选：${attachments.length}`);
  lines.push('');
  for (const thread of threads) {
    const discussion = thread.discussion ?? {};
    lines.push(`## ${discussion.name ?? discussion.subject ?? `讨论 ${discussion.discussion}`}`);
    lines.push(`- 讨论 ID：${discussion.discussion}`);
    lines.push(`- 入口：${discussion.discussion ? `https://moodle.hku.hk/mod/forum/discuss.php?d=${discussion.discussion}` : (module.url ?? '')}`);
    lines.push(`- 发起人：${discussion.userfullname ?? discussion.userid ?? 'unknown'}`);
    lines.push(`- 创建：${discussion.created ?? 'unknown'}；最后修改：${discussion.modified ?? discussion.timemodified ?? 'unknown'}`);
    if (discussion.message) lines.push('', htmlToText(discussion.message));
    lines.push('');
    for (const post of thread.posts ?? []) {
      lines.push(`### 帖子 ${post.id} · ${post.author?.fullname ?? post.author?.id ?? 'unknown'}`);
      lines.push(`- 时间：${post.timecreated ?? 'unknown'}；修改：${post.timemodified ?? 'unknown'}`);
      lines.push(`- 直接链接：https://moodle.hku.hk/mod/forum/discuss.php?d=${discussion.discussion}#p${post.id}`);
      if (post.subject) lines.push(`- 主题：${post.subject}`);
      const body = htmlToText(post.message);
      if (body) lines.push('', body);
      for (const file of post.attachments ?? []) lines.push(`- 附件：${file.filename}（${file.filesize ?? '?'} bytes）`);
      for (const file of post.messageinlinefiles ?? []) lines.push(`- 内联文件：${file.filename}（${file.filesize ?? '?'} bytes）`);
      lines.push('');
    }
  }
  if (!threads.length) lines.push(collected ? '_该论坛没有讨论。_' : '_该论坛未采集到讨论文字。_', '');
  return lines.join('\n');
}

function assignmentCaptureMarkdown({page, module, assignment, attachments}) {
  const lines = [captureHeader({page, module, captureKind: 'assignment'})];
  lines.push(`# ${module.name}（作业要求）`, '');
  lines.push(`- 课程页面：${coursePageURL(page.baseUrl, page.moodleCourseid)}`);
  lines.push(`- 作业模块：${module.url ?? ''}`);
  lines.push(`- 作业 ID：${assignment?.id ?? module.instance}`);
  if (assignment?.duedate) lines.push(`- 截止（时间戳）：${assignment.duedate}`);
  if (assignment?.allowsubmissionsfromdate) lines.push(`- 开放（时间戳）：${assignment.allowsubmissionsfromdate}`);
  if (assignment?.cutoffdate) lines.push(`- 提交截止（时间戳）：${assignment.cutoffdate}`);
  lines.push(`- 附件：${attachments.length}`);
  lines.push('- 说明：此处只保存 Moodle 公开的作业说明与附件清单；不包含提交内容，也不代表已完成或已掌握。');
  lines.push('');
  const intro = htmlToText(assignment?.intro ?? '');
  lines.push('## 作业说明正文', '', intro || '_Moodle 未返回作业说明正文；需查看附件或作业页面。_', '');
  if (attachments.length) {
    lines.push('## 附件清单', '');
    for (const attachment of attachments) {
      const name = attachment.file?.filename ?? attachment.filename ?? 'attachment';
      lines.push(`- ${name}${attachment.file?.filesize ? `（${attachment.file.filesize} bytes）` : ''}`);
    }
    lines.push('');
  }
  return lines.join('\n');
}

function pageModuleCaptureMarkdown({page, module, section}) {
  const lines = [captureHeader({page, module, captureKind: 'page-module'})];
  lines.push(`# ${module.name}（页面说明入口）`, '');
  lines.push(`- 课程页面：${coursePageURL(page.baseUrl, page.moodleCourseid)}`);
  lines.push(`- 页面模块：${module.url ?? ''}`);
  lines.push(`- 章节：${section?.name ?? ''}`);
  lines.push('- 正文 HTML 文件已作为课程文件下载并登记，可用本地资料库检索。');
  lines.push('');
  return lines.join('\n');
}

function coursePageCaptureMarkdown({page, enumerated}) {
  const lines = [captureHeader({page, module: null, captureKind: 'course-page'})];
  const moduleCount = enumerated.modules?.length
    ?? enumerated.sections.reduce((total, section) => total + (section.modules?.length ?? 0), 0);
  lines.push(`# ${page.course?.name ?? page.subject}（课程页面结构）`, '');
  lines.push(`- 课程页面：${coursePageURL(page.baseUrl, page.moodleCourseid)}`);
  lines.push(`- 页面 ID：${page.courseId}；Moodle courseid：${page.moodleCourseid}；学科：${page.subject}`);
  lines.push(`- 章节数：${enumerated.sections.length}；模块数：${moduleCount}`);
  lines.push('');
  for (const section of enumerated.sections) {
    lines.push(`## [${section.index}] ${section.name || '(未命名章节)'}`, '');
    const summary = htmlToText(section.summary);
    if (summary) lines.push(summary, '');
    for (const module of section.modules) {
      const access = module.uservisible === false ? '（受限）' : module.visible === 0 ? '（隐藏）' : '';
      lines.push(`- \`${module.modname}\` ${module.name}${access} — ${module.url ?? ''}`);
    }
    lines.push('');
  }
  return lines.join('\n');
}

function parseCategorySubdir(relativePath, courseId, category, filename) {
  const parts = String(relativePath ?? '').split('/');
  if (parts.length === 4 && parts[0] === courseId && parts[1] === category && parts[3] === filename
      && safeSubdir(parts[2])) return parts[2];
  return null;
}

// Deterministic subdirectory for same-name, different-content files. Forum
// attachments use their post id so retries keep the same path even if another
// same-name file appears first in a later run.
function collisionSubdir(item) {
  const segments = String(item.filepath ?? '/').split('/').filter(Boolean);
  const last = segments.at(-1);
  const suffix = last && /^\d+$/.test(last) ? `-${last}` : `-${sha256(item.key).slice(0, 8)}`;
  return `module-${item.moduleId ?? 'x'}${suffix}`;
}

export function assignArchiveLayouts(items, state) {
  const current = (state.materials ?? []).filter(m => !m.superseded_by && !['archived', 'deleted'].includes(m.version_status));
  const existingByKey = new Map();
  const pathOwner = new Map();
  for (const material of current) {
    pathOwner.set(material.relative_path, material);
    for (const source of material.moodle_file_sources ?? []) {
      const key = `${source.course_id}|${source.module_id}|${source.filepath ?? '/'}|${source.remote_filename}`;
      existingByKey.set(key, material);
    }
  }
  const groups = new Map();
  for (const item of items) {
    if (item.kind !== 'file') continue;
    const groupKey = `${item.subject}|${item.category}|${item.filename}`;
    const list = groups.get(groupKey) ?? [];
    list.push(item); groups.set(groupKey, list);
  }
  const taken = new Map();
  for (const list of groups.values()) {
    list.sort((a, b) => a.key.localeCompare(b.key));
    list.forEach((item, index) => {
      const existing = existingByKey.get(item.key);
      let subdir = existing ? parseCategorySubdir(existing.relative_path, item.subject, item.category, item.filename) : null;
      if (!subdir && list.length > 1 && index > 0) subdir = collisionSubdir(item);
      if (!subdir) {
        const plain = `${item.subject}/${item.category}/${item.filename}`;
        const owner = pathOwner.get(plain);
        if (owner && owner !== existing) subdir = collisionSubdir(item);
      }
      if (subdir) {
        let candidate = subdir, suffix = 0;
        while (taken.has(`${item.subject}/${item.category}/${candidate}/${item.filename}`)) {
          suffix++;
          candidate = `${subdir}-${sha256(item.key).slice(0, 8)}${suffix > 1 ? `-${suffix}` : ''}`;
        }
        taken.set(`${item.subject}/${item.category}/${candidate}/${item.filename}`, item.key);
        item.archiveSubdir = candidate;
      } else {
        taken.set(`${item.subject}/${item.category}/${item.filename}`, item.key);
        item.archiveSubdir = null;
      }
    });
  }
  return items;
}

function fingerprintOf(item) {
  if (item.kind === 'capture') return {sha256: sha256(Buffer.from(item.capture.markdown, 'utf8'))};
  return {
    filesize: item.filesize ?? null,
    timemodified: item.timemodified ?? null,
    url: item.cleanUrl ?? null,
  };
}

function fingerprintMatches(entryRemote, remote) {
  if (!entryRemote || !remote) return false;
  return entryRemote.filesize === remote.filesize
    && entryRemote.timemodified === remote.timemodified
    && entryRemote.url === remote.url;
}

function materialCurrent(state, materialId, sha = null) {
  const material = (state.materials ?? []).find(m => m.id === materialId);
  if (!material) return false;
  if (material.superseded_by || ['archived', 'deleted'].includes(material.version_status)) return false;
  if (sha && material.sha256 !== sha) return false;
  return true;
}

function readLedger(path) {
  if (!existsSync(path)) return {schema: LEDGER_SCHEMA, updated_at: null, entries: {}};
  try {
    const value = readJSON(path);
    if (!value || typeof value.entries !== 'object') throw new Error('invalid ledger');
    return value;
  } catch { return {schema: LEDGER_SCHEMA, updated_at: null, entries: {}}; }
}

function pythonJSON(root, statePath, script, args, payload) {
  const child = spawnSync(python(root), [join(root, 'tools/study', script), '--state', statePath, ...args], {
    input: JSON.stringify(payload), encoding: 'utf8', windowsHide: true,
    env: {...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1'},
    maxBuffer: 64 * 1024 * 1024,
  });
  if (child.error) throw new Error('Python bridge failed: ' + safeError(child.error));
  if (child.status !== 0) throw new Error(safeError(child.stderr) || 'Python bridge failed');
  try { return JSON.parse(child.stdout); }
  catch { throw new Error('Python bridge returned invalid JSON'); }
}

export function authorizeCourses({root, statePath, mode, scope = SCOPE_TEXT, courses, scopeVersion = SCOPE_VERSION}) {
  return pythonJSON(root, statePath, 'course_library.py', ['authorize'],
    {mode, scope, scope_version: scopeVersion, courses});
}

function defaultInstallBatch({root, statePath, payload}) {
  return pythonJSON(root, statePath, 'course_library.py', ['install-batch'], payload);
}

function defaultBuildIndex({root, statePath, courses}) {
  return pythonJSON(root, statePath, 'course_library.py', ['build-index'], {courses});
}

function writeReport(root, report) {
  const folder = join(root, 'Study/_System/LibrarySync');
  mkdirSync(folder, {recursive: true});
  atomicJSON(join(folder, 'latest.json'), report);
  writeFileSync(join(folder, '资料缺口.md'), gapMarkdown(report), 'utf8');
}

export function gapMarkdown(report) {
  const lines = [];
  lines.push('# 课程资料缺口与状态（自动生成）', '');
  lines.push(`- 生成时间：${report.finished_at}`);
  lines.push(`- 同步编号：${report.run_id}`);
  lines.push(`- 授权范围版本：${report.authorization?.scope_version ?? '未授权'}`);
  lines.push(`- 文本来源：${report.text_source?.source ?? 'unknown'}${report.text_source?.snapshot_run_id ? `（快照 ${report.text_source.snapshot_run_id}，采集于 ${report.text_source.captured_at}）` : ''}`);
  lines.push('- 说明：下载成功或解析成功不等于已核读，更不等于已掌握。实际学习仍以核读原文和独立作答为准。');
  lines.push('');
  lines.push('## 总览', '');
  const total = report.totals ?? {};
  lines.push(`- 课程页面：${report.courses?.length ?? 0}，学科：${new Set((report.courses ?? []).map(c => c.subject)).size}`);
  lines.push(`- 枚举可见模块：${total.modules_enumerated ?? 0}；受限模块：${total.modules_restricted ?? 0}`);
  lines.push(`- 文件候选：${total.file_candidates ?? 0}；已安装/相同/重复/已最新：${total.installed ?? 0}/${total.identical ?? 0}/${total.duplicate ?? 0}/${total.already_current ?? 0}；失败：${total.failed ?? 0}`);
  lines.push(`- 文本捕获：${total.capture_installed ?? 0} 个已保存；失败：${total.capture_failed ?? 0}`);
  lines.push(`- 有说明的跳过入口：${total.skipped ?? 0}`);
  lines.push('');
  lines.push('## 逐课程', '');
  for (const course of report.courses ?? []) {
    lines.push(`### ${course.shortname ?? course.course_id}（${course.course_id}，Moodle ${course.moodle_courseid}）`, '');
    lines.push(`- 页面：${course.page_url}`);
    lines.push(`- 学科目录：${course.subject}；章节 ${course.sections_enumerated ?? '?'}；可见模块 ${course.modules_visible ?? '?'}；受限 ${course.modules_restricted ?? 0}`);
    lines.push(`- 文件：候选 ${course.file_candidates ?? 0}，新安装 ${course.installed ?? 0}，相同内容 ${course.identical ?? 0}，重复 ${course.duplicate ?? 0}，无需更新 ${course.already_current ?? 0}，失败 ${course.failed ?? 0}`);
    lines.push(`- 文本捕获：${course.captures ?? 0}，失败 ${course.capture_failed ?? 0}`);
    const gaps = (course.gaps ?? []);
    if (gaps.length) {
      lines.push('- 未获取 / 未解析清单（按原因汇总，完整逐项记录见 latest.json）：');
      const groups = new Map();
      for (const gap of gaps) {
        const key = `${gap.reason ?? gap.status ?? 'unknown'}`;
        const list = groups.get(key) ?? [];
        list.push(gap); groups.set(key, list);
      }
      for (const [reason, list] of groups) {
        lines.push(`  - [${reason}] 共 ${list.length} 项：${SKIP_REASONS[reason] ?? ''}`);
        for (const gap of list.slice(0, 6)) {
          const label = [gap.module_type, gap.module_name].filter(Boolean).join(' / ') || gap.filename || '未命名';
          lines.push(`    - ${label}${gap.detail ? ` — ${gap.detail}` : ''}${gap.url ? ` — ${gap.url}` : ''}`);
        }
        if (list.length > 6) lines.push(`    - …其余 ${list.length - 6} 项见 latest.json`);
      }
    } else lines.push('- 未获取 / 未解析清单：无');
    const attention = course.index_attention ?? [];
    if (attention.length) {
      lines.push('- 索引注意：');
      for (const row of attention.slice(0, 200)) lines.push(`  - ${row.id}：${row.status ?? ''} ${(row.warnings ?? []).join('; ')}`);
    }
    lines.push('');
  }
  lines.push('## 索引（全库 catalog，含当前版本与归档行）', '');
  const indexEntries = Object.values(report.index?.courses ?? {});
  if (indexEntries.length) {
    const value = indexEntries[indexEntries.length - 1];
    lines.push(`- catalog 全库：材料 ${value.materials ?? 0}，单元 ${value.units ?? 0}，有文字单元 ${value.text_units ?? 0}，状态 ${JSON.stringify(value.status_counts ?? {})}`);
  }
  lines.push('', '说明：catalog 为全库累计（含 _Archive 历史版本行），不是单门课材料数；单科当前材料见各课程小节。');
  lines.push('', '## 下一步', '');
  lines.push('- 缺口逐项处理：外部/流媒体入口需本人在站点内访问或提供文件；失败项可再次运行同步（已完成项不会重复下载）。');
  lines.push('- 需要检索时使用 `tools/study/library.cmd search/read/render`，并核对原页；解析文字不能替代原页视觉核对。');
  lines.push('');
  return lines.join('\n');
}

function itemReport(item) {
  return {
    key: item.key, kind: item.kind, status: item.status, reason: item.reason ?? null,
    detail: item.detail ?? null,
    course_id: item.courseId, page_id: item.pageId, moodle_courseid: item.moodleCourseid,
    subject: item.subject, module_id: item.moduleId, module_type: item.moduleType,
    module_name: item.moduleName, section: item.section,
    filename: item.filename, category: item.category, title: item.title,
    url: item.url, activity_url: item.activityUrl,
    archive_subdir: item.archiveSubdir ?? null,
    material_id: item.materialId ?? null, relative_path: item.relativePath ?? null,
    sha256: item.sha256 ?? null, bytes: item.bytes ?? null,
    duplicate_of: item.duplicateOf ?? null, retained_path: item.retainedPath ?? null,
    source_pages: item.sourcePages,
  };
}

export async function syncCourseLibrary({
  client, root, state, courseIds = null, snapshotPath = null, maxBytes = MAX_BYTES,
  batchSize = 8, dryRun = false, installBatch, buildIndex, now = nowISO,
}) {
  root = resolve(root);
  const study = resolve(root, 'Study');
  const statePath = join(study, '_System/state.json');
  if (!Number.isInteger(batchSize) || batchSize < 1 || batchSize > 20) throw new Error('batch size must be 1-20');
  const pages = selectedPages(state, courseIds);
  if (!pages.length) throw new Error('No selected course pages to sync');
  for (const page of pages) page.baseUrl = client.baseUrl;
  const {authorization, authorized, blocked} = authorizationReport(state, pages);
  if (blocked.length) {
    blocked.forEach(entry => { entry.page.status = STATUS.blocked; });
  }
  if (!authorized.length) {
    const detail = blocked.map(b => `${b.page.courseId}(${b.reason})`).join(', ');
    throw new Error('Courses need a saved course-level full authorization: ' + detail);
  }

  const startedAt = now();
  const enumerated = [];
  for (const page of authorized) enumerated.push(await enumerateCourse(client, page));
  if (snapshotPath) {
    const {snapshot, provenance} = snapshotProvenance(snapshotPath, root);
    const pagesPresent = authorized.every(page => snapshot.pages.some(row => row.course_id === page.courseId
      && Number(row.courseid) === Number(page.moodleCourseid)));
    if (pagesPresent) {
      state.__textSources = textSourcesFromSnapshot(snapshot, provenance, authorized);
      state.__snapshotProvenance = provenance;
    } else {
      state.__textSources = await liveTextSources(client, authorized, enumerated);
      state.__snapshotProvenance = null;
    }
  } else {
    state.__textSources = await liveTextSources(client, authorized, enumerated);
    state.__snapshotProvenance = null;
  }
  const texts = state.__textSources;

  const plannedItems = [];
  const skips = [];
  for (let index = 0; index < authorized.length; index++) {
    const page = authorized[index];
    const result = planPage({page, enumerated: enumerated[index], texts, state});
    plannedItems.push(...result.items);
    skips.push(...result.skips);
  }
  assignArchiveLayouts(plannedItems, state);

  const ledgerPath = join(study, '_System/LibrarySync/ledger.json');
  const ledger = readLedger(ledgerPath);
  const ledgerEntries = ledger.entries ?? {};
  const runId = randomUUID().replaceAll('-', '');
  const pending = [];
  for (const item of plannedItems) {
    if (item.kind === 'capture') {
      item.sha256 = sha256(Buffer.from(item.capture.markdown, 'utf8'));
      item.bytes = Buffer.byteLength(item.capture.markdown);
    }
    const entry = ledgerEntries[item.key];
    if (item.kind === 'file') {
      item.remote = fingerprintOf(item);
      if (entry && [STATUS.installed, STATUS.identical, STATUS.duplicate].includes(entry.status)
          && fingerprintMatches(entry.remote, item.remote)
          && materialCurrent(state, entry.status === STATUS.duplicate ? entry.duplicate_of : entry.material_id,
            entry.status === STATUS.duplicate ? null : entry.sha256)) {
        item.status = STATUS.already_current;
        item.materialId = entry.material_id;
        item.relativePath = entry.relative_path;
        item.sha256 = entry.sha256;
        continue;
      }
      pending.push(item);
    } else {
      if (entry && [STATUS.installed, STATUS.identical].includes(entry.status)
          && entry.sha256 === item.sha256 && materialCurrent(state, entry.material_id, item.sha256)) {
        item.status = STATUS.already_current;
        item.materialId = entry.material_id;
        item.relativePath = entry.relative_path;
        continue;
      }
      pending.push(item);
    }
  }

  const summary = {
    schema: REPORT_SCHEMA, run_id: runId, started_at: startedAt,
    authorization: authorization ? {scope_version: authorization.scope_version, mode: 'full'} : null,
    text_source: {source: texts.source, captured_at: texts.capturedAt, snapshot_run_id: texts.snapshotRunId,
      snapshot: state.__snapshotProvenance ?? null, errors: texts.errors},
    dry_run: dryRun, root,
  };
  const courseReports = authorized.map((page, index) => {
    const modules = enumerated[index].modules;
    return {
      course_id: page.courseId, subject: page.subject, moodle_courseid: page.moodleCourseid,
      shortname: page.course?.shortname ?? null, name: page.course?.name ?? null,
      page_url: coursePageURL(page.baseUrl, page.moodleCourseid),
      sections_enumerated: enumerated[index].sections.length,
      modules_visible: modules.filter(m => m.uservisible !== false && m.visible !== 0).length,
      modules_restricted: modules.filter(m => m.uservisible === false || m.visible === 0).length,
      file_candidates: 0, installed: 0, identical: 0, duplicate: 0, already_current: 0, failed: 0,
      captures: 0, capture_failed: 0, skipped: 0, gaps: [], index_attention: [],
    };
  });
  const courseReportByPage = new Map(authorized.map((page, index) => [page.courseId, courseReports[index]]));
  for (const item of plannedItems) {
    const report = courseReportByPage.get(item.pageId);
    if (item.kind === 'file') report.file_candidates++;
  }
  void pending;

  if (dryRun) {
    const fileItems = plannedItems.filter(item => item.kind === 'file');
    const captureItems = plannedItems.filter(item => item.kind === 'capture');
    const totals = {modules_enumerated: enumerated.reduce((n, e) => n + e.modules.length, 0),
      file_candidates: fileItems.length, capture_candidates: captureItems.length,
      pending_files: pending.filter(item => item.kind === 'file').length,
      pending_captures: pending.filter(item => item.kind === 'capture').length,
      skipped: skips.length, blocked_courses: blocked.length};
    return {ok: true, dry_run: true, run_id: runId, courses: courseReports, totals, skipped: skips.map(s => ({
      course_id: s.courseId, module_id: s.moduleId, module_name: s.moduleName, reason: s.reason, url: s.url}))};
  }

  mkdirSync(join(study, '_System/LibrarySync'), {recursive: true});
  const runFolder = join(study, '_Inbox/.staging', runId);
  mkdirSync(runFolder, {recursive: true});
  if (realpathSync(runFolder) !== runFolder) throw new Error('Redirected staging folder');
  const install = installBatch ?? defaultInstallBatch;
  const indexBuilder = buildIndex ?? ((courses) => defaultBuildIndex({root, statePath, courses}));
  const errors = [];
  let pendingIndex = pending.slice();
  const processedKeys = new Set();
  let sequence = 0;
  const retainedTarget = (item) => join(study, '_Inbox', item.subject, String(item.moduleId ?? 'page'), item.filename);

  while (pendingIndex.length) {
    const batch = pendingIndex.splice(0, batchSize);
    const payloadItems = [];
    for (const item of batch) {
      sequence++;
      try {
        if (item.kind === 'capture') {
          const target = join(runFolder, `${sequence}-${item.filename}`);
          writeFileSync(target, item.capture.markdown, {encoding: 'utf8', flag: 'wx'});
          item.stagedPath = target;
          item.sha256 = sha256(Buffer.from(item.capture.markdown, 'utf8'));
          item.bytes = Buffer.byteLength(item.capture.markdown);
        } else {
          const {bytes} = await fetchCourseFile(client, {url: item.url, filename: item.filename, filesize: item.filesize},
            {maxBytes});
          const target = join(runFolder, `${sequence}-${item.filename}`);
          writeFileSync(target, bytes, {flag: 'wx'});
          item.stagedPath = target;
          item.sha256 = sha256Bytes(bytes);
          item.bytes = bytes.length;
        }
        payloadItems.push({
          item_key: item.key,
          relative_path: relative(study, item.stagedPath).replaceAll('\\', '/'),
          sha256: item.sha256, bytes: item.bytes,
          archive_subdir: item.archiveSubdir ?? null,
          source: {
            course_id: item.subject, module_id: item.moduleId,
            filename: item.filename, filepath: item.filepath,
            url: item.kind === 'capture' ? item.activityUrl : item.cleanUrl,
            module_url: item.activityUrl, activity_url: item.activityUrl,
            title: item.title, category: item.category,
          },
          source_pages: item.sourcePages,
          extra: {
            kind: item.kind === 'capture' ? 'Moodle text capture' : 'Moodle course file',
            ...(item.capture ? {capture: {kind: item.capture.kind, item_count: item.capture.item_count ?? null,
              captured_at: texts.capturedAt, snapshot_run_id: texts.snapshotRunId}} : {}),
          },
        });
      } catch (error) {
        item.status = STATUS.failed;
        item.reason = 'download_failed';
        item.detail = safeError(error);
        errors.push({scope: item.key, error: item.detail});
        ledgerEntries[item.key] = {
          ...ledgerEntries[item.key], status: STATUS.failed, reason: item.reason, detail: item.detail,
          attempt_at: now(), remote: item.remote ?? null,
        };
        processedKeys.add(item.key);
        const report = courseReportByPage.get(item.pageId);
        if (item.kind === 'capture') report.capture_failed++; else report.failed++;
      }
    }
    if (payloadItems.length) {
      try {
        const result = install({root, statePath, payload: {run_id: runId, snapshot: texts.source === 'snapshot' ? state.__snapshotProvenance : null, items: payloadItems}});
        const byKey = new Map((result.items ?? []).map(row => [row.item_key, row]));
        for (const item of batch) {
          if (!item.stagedPath) continue; // failed before staging
          const row = byKey.get(item.key);
          const report = courseReportByPage.get(item.pageId);
          if (!row || row.status === 'error') {
            item.status = STATUS.failed;
            item.reason = 'install_failed';
            item.detail = safeError(row?.error ?? 'installer did not report this item');
            // Keep the verified bytes for manual review rather than deleting them.
            try {
              const target = retainedTarget(item);
              mkdirSync(dirname(target), {recursive: true});
              if (!existsSync(target)) { renameSync(item.stagedPath, target); item.retainedPath = relative(study, target).replaceAll('\\', '/'); }
            } catch { /* retain in staging instead */ }
            ledgerEntries[item.key] = {...ledgerEntries[item.key], status: STATUS.failed, reason: item.reason,
              detail: item.detail, attempt_at: now(), remote: item.remote ?? null, sha256: item.sha256, bytes: item.bytes};
            if (item.kind === 'capture') report.capture_failed++; else report.failed++;
            errors.push({scope: item.key, error: item.detail});
          } else {
            const status = row.status === 'promoted' ? STATUS.installed
              : row.status === 'identical' ? STATUS.identical : row.status === 'duplicate' ? STATUS.duplicate : STATUS.failed;
            item.status = status;
            item.materialId = row.material_id ?? null;
            item.relativePath = row.relative_path ?? null;
            item.duplicateOf = row.duplicate_of ?? null;
            if (item.kind === 'capture') {
              report.captures++;
            } else if (status === STATUS.installed) report.installed++;
            else if (status === STATUS.identical) report.identical++;
            else if (status === STATUS.duplicate) report.duplicate++;
            ledgerEntries[item.key] = {status, reason: null, detail: null, sha256: item.sha256, bytes: item.bytes,
              material_id: item.materialId, relative_path: item.relativePath, duplicate_of: item.duplicateOf,
              remote: item.remote ?? null, first_success_at: ledgerEntries[item.key]?.first_success_at ?? now(),
              attempt_at: now(), archive_subdir: item.archiveSubdir ?? null};
            processedKeys.add(item.key);
            try { if (item.stagedPath && existsSync(item.stagedPath)) unlinkSync(item.stagedPath); } catch { /* keep for review */ }
          }
        }
      } catch (error) {
        for (const item of batch) {
          if (!item.stagedPath) continue;
          const report = courseReportByPage.get(item.pageId);
          item.status = STATUS.failed;
          item.reason = 'install_batch_failed';
          item.detail = safeError(error);
          if (item.kind === 'capture') report.capture_failed++; else report.failed++;
          errors.push({scope: item.key, error: item.detail});
          ledgerEntries[item.key] = {...ledgerEntries[item.key], status: STATUS.failed, reason: item.reason,
            detail: item.detail, attempt_at: now(), remote: item.remote ?? null};
        }
      }
      ledger.updated_at = now();
      atomicJSON(ledgerPath, ledger);
    }
    // Page HTML can reference inline images; queue newly discovered images once.
    for (const item of batch) {
      if (item.kind !== 'file' || !item.stagedPath || !['.html', '.htm'].includes(extensionOf(item.filename))) continue;
      if (!existsSync(item.stagedPath)) continue;
      let html = '';
      try { html = readFileSync(item.stagedPath, 'utf8'); } catch { continue; }
      const page = authorized.find(p => p.courseId === item.pageId);
      if (!page) continue;
      for (const image of inlineImagesFromHTML(html, page)) {
        let cleanURL;
        try { cleanURL = webserviceFileURL(page.baseUrl, image.url).toString(); } catch { continue; }
        if (plannedItems.some(other => other.cleanUrl === cleanURL)) continue;
        const module = enumerated.find(e => e.page.courseId === page.courseId)?.modules.find(m => m.id === item.moduleId);
        const newItem = {
          kind: 'file', key: `${item.subject}|${item.moduleId}|${item.filepath}inline|${image.filename}`,
          courseId: item.courseId, pageId: item.pageId, moodleCourseid: item.moodleCourseid,
          subject: item.subject, moduleId: item.moduleId, moduleType: item.moduleType,
          moduleName: item.moduleName, section: item.section, sectionIndex: item.sectionIndex,
          filename: image.filename, filepath: `${item.filepath}inline`,
          title: `${item.title} / ${image.filename}`, category: item.category,
          url: image.url, cleanUrl: cleanURL, activityUrl: item.activityUrl,
          filesize: null, timemodified: null, inline: true, sourcePages: item.sourcePages,
          remote: {filesize: null, timemodified: null, url: cleanURL},
        };
        if (newItem.key in ledgerEntries || plannedItems.some(other => other.key === newItem.key)) continue;
        plannedItems.push(newItem);
        pendingIndex.push(newItem);
        courseReportByPage.get(item.pageId).file_candidates++;
        void module;
      }
    }
  }

  // One index build after all files are registered.
  const affectedCourses = [...new Set(plannedItems.map(item => item.subject))];
  let index = {courses: {}};
  try {
    index = indexBuilder(affectedCourses);
  } catch (error) {
    errors.push({scope: 'index', error: safeError(error)});
  }

  for (const item of plannedItems) {
    if (!item.status) item.status = STATUS.skipped;
    if (item.kind === 'capture' && !['installed', 'identical', 'already_current', 'failed'].includes(item.status)) {
      item.status = STATUS.failed;
      item.reason = 'install_not_reported';
    }
    if (item.kind === 'capture' && item.status === STATUS.already_current) {
      const report = courseReportByPage.get(item.pageId);
      if (report) report.captures++;
    }
    if (item.kind === 'file' && item.status === STATUS.already_current) {
      const report = courseReportByPage.get(item.pageId);
      if (report) report.already_current++;
    }
  }
  for (const skip of skips) {
    const report = courseReportByPage.get(skip.pageId);
    if (report) {
      report.skipped++;
      report.gaps.push({status: STATUS.skipped, reason: skip.reason, detail: skip.detail,
        module_type: skip.moduleType, module_name: skip.moduleName, filename: skip.filename, url: skip.url});
    }
    ledgerEntries[skip.key ?? `skip|${skip.pageId}|${skip.moduleId}|${skip.reason}|${skip.filename ?? ''}`] = {
      status: STATUS.skipped, reason: skip.reason, detail: skip.detail, url: skip.url, attempt_at: now(),
    };
  }
  for (const item of plannedItems) {
    if (item.status === STATUS.failed) {
      const report = courseReportByPage.get(item.pageId);
      report.gaps.push({status: item.status, reason: item.reason, detail: item.detail,
        module_type: item.moduleType, module_name: item.moduleName, filename: item.filename, url: item.url});
    }
  }
  for (const page of blocked) {
    courseReports.push({
      course_id: page.page.courseId, subject: page.page.subject, moodle_courseid: page.page.moodleCourseid,
      shortname: page.page.course?.shortname ?? null, name: page.page.course?.name ?? null,
      page_url: coursePageURL(client.baseUrl, page.page.moodleCourseid),
      sections_enumerated: 0, modules_visible: 0, modules_restricted: 0,
      file_candidates: 0, installed: 0, identical: 0, duplicate: 0, already_current: 0, failed: 0,
      captures: 0, capture_failed: 0, skipped: 0,
      gaps: [{status: STATUS.blocked, reason: page.reason, detail: '未同步：需重新授权该课程的完整初始化范围', url: null}],
      index_attention: [],
    });
  }
  // Index reports are cumulative; keep only attention rows that belong to the
  // course this report line describes, using the freshly built catalog labels.
  let catalogCourse = new Map();
  try {
    const catalog = readJSON(join(study, '_System/Library/catalog.json'));
    catalogCourse = new Map((catalog.materials ?? []).map(row => [row.id, row.course_id]));
  } catch { /* keep the per-course fallback */ }
  for (const [course, value] of Object.entries(index.courses ?? {})) {
    // Prefer the primary page row; a merged secondary page shares the subject index.
    const report = courseReports.find(row => row.course_id === course)
      ?? courseReports.find(row => row.subject === course);
    if (report) {
      report.index_attention = (value.attention ?? []).filter(row => {
        const owner = catalogCourse.get(row.id);
        return owner === undefined || owner === report.subject;
      });
    }
  }

  const installTotals = (key) => plannedItems.filter(item => item.status === key).length;
  const captures = plannedItems.filter(item => item.kind === 'capture');
  const totals = {
    modules_enumerated: enumerated.reduce((n, e) => n + e.modules.length, 0),
    modules_restricted: enumerated.reduce((n, e) => n + e.modules.filter(m => m.uservisible === false || m.visible === 0).length, 0),
    file_candidates: plannedItems.filter(item => item.kind === 'file').length,
    installed: installTotals(STATUS.installed), identical: installTotals(STATUS.identical),
    duplicate: installTotals(STATUS.duplicate), already_current: installTotals(STATUS.already_current),
    failed: plannedItems.filter(item => item.kind === 'file' && item.status === STATUS.failed).length,
    capture_installed: captures.filter(item => ['installed', 'identical', 'already_current'].includes(item.status)).length,
    capture_failed: captures.filter(item => item.status === STATUS.failed).length,
    skipped: skips.length,
    blocked_courses: blocked.length,
    downloaded_bytes: plannedItems.filter(item => item.kind === 'file' && [STATUS.installed, STATUS.identical, STATUS.duplicate].includes(item.status))
      .reduce((n, item) => n + (item.bytes ?? 0), 0),
  };
  const report = {
    ...summary, finished_at: now(),
    courses: courseReports,
    items: plannedItems.map(itemReport).concat(skips.map(skip => ({...itemReport(skip), key: skip.key ?? null}))),
    totals, index,
    errors,
    no_background_task: true,
    no_school_writes: true,
    note: 'Files and parsed text are local references only; download or parse success is not verification or mastery.',
  };
  writeReport(root, report);
  try { rmSync(runFolder, {recursive: true, force: true}); } catch { /* staging retained */ }
  const unauthorized = blocked.length;
  return {...report, ok: errors.length === 0 && totals.failed === 0 && unauthorized === 0,
    blocked: blocked.map(entry => ({course_id: entry.page.courseId, reason: entry.reason}))};
}
