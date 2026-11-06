// Chapter-scoped, read-only verification of files the student already has.
//
// A full collection answers "what changed anywhere in my courses"; it also reads
// every assignment submission state, discussion thread and calendar event. This
// module answers one narrower question: "is the file for this chapter still the
// file I already have?" It never downloads into the library, never installs,
// archives or indexes, and never promotes a review receipt. It therefore cannot
// replace a full collection for announcements, assignment requirements,
// discussions, deadlines or exam preparation.
import {createHash, randomUUID} from 'node:crypto';
import {existsSync, mkdirSync, readFileSync, realpathSync, renameSync, statSync, unlinkSync, writeFileSync} from 'node:fs';
import {join, resolve, sep} from 'node:path';
import {MAX_BYTES, fetchCourseFile} from './course-files.mjs';
import {accountScope, assertProfileAccount} from './content-policy.mjs';
import {
  authorizationReport, emptyTextSources, enumerateCourse, nowISO, planPage, readJSON,
  selectedPages,
} from './course-library.mjs';

export const CHAPTER_CHECK_SCHEMA = 1;
export const MODES = Object.freeze({quick: 'quick', strict: 'strict'});
export const STATUS = Object.freeze({
  unchanged: 'unchanged', changed: 'changed', no_baseline: 'no_baseline', error: 'error', skipped: 'skipped',
});
// A ledger row is a usable baseline only after a verified installation.
const BASELINE_LEDGER_STATUSES = new Set(['installed', 'identical', 'duplicate', 'already_current']);
const SCOPE_NOTE = '本次只核对本章文件与本地版本的对应关系；公告、作业要求、论坛、日历和其他章节未检查，不能据此声称所有教学信息都是最新。';

export function normalizeText(value) {
  return String(value ?? '').normalize('NFKC').trim().toLowerCase();
}

// "8", "Chapter 8", "第8章", "Topic 8 - Inventories" all yield 8.
export function chapterNumber(value) {
  const match = normalizeText(value).match(/(?:^|\D)(\d{1,3})(?=\D|$)/);
  return match ? Number(match[1]) : null;
}

function standaloneNumber(name, number) {
  return new RegExp(`(?:^|\\D)${number}(?=\\D|$)`).test(name);
}

// Match by section name first, then by chapter number. A bare number must match a
// standalone number, so "8" never selects "Chapter 18".
export function selectSections(sections, query) {
  const wanted = normalizeText(query);
  if (!wanted) throw new Error('请提供要核对的章节名称或编号');
  const rows = sections.map(section => ({section, name: normalizeText(section.name)}));
  if (/^\d{1,3}$/.test(wanted)) {
    const number = Number(wanted);
    const numeric = rows.filter(row => row.section.number === number || standaloneNumber(row.name, number));
    return {sections: numeric.map(row => row.section), basis: 'number'};
  }
  const exact = rows.filter(row => row.name === wanted);
  if (exact.length) return {sections: exact.map(row => row.section), basis: 'name'};
  const substring = rows.filter(row => row.name.includes(wanted));
  if (substring.length) return {sections: substring.map(row => row.section), basis: 'name_substring'};
  const number = chapterNumber(wanted);
  if (number !== null) {
    const numeric = rows.filter(row => row.section.number === number || standaloneNumber(row.name, number));
    if (numeric.length) return {sections: numeric.map(row => row.section), basis: 'number'};
  }
  return {sections: [], basis: 'none'};
}

// `--course` accepts the subject ID, a Moodle course ID or a unique course name.
export function resolveCoursePages(state, courseRef) {
  const wanted = String(courseRef ?? '').trim();
  if (!wanted) throw new Error('请提供要核对的课程（课程 ID、Moodle 课程 ID 或课程名）');
  const pages = selectedPages(state);
  if (!pages.length) throw new Error('还没有选择课程；请先完成选课和课程级资料授权');
  let matched = pages.filter(page => page.courseId === wanted || String(page.moodleCourseid) === wanted);
  if (!matched.length) {
    const text = normalizeText(wanted);
    matched = pages.filter(page => normalizeText(page.course?.name).includes(text)
      || normalizeText(page.course?.shortname).includes(text));
  }
  if (!matched.length) throw new Error('没有找到课程：' + wanted);
  const subjects = [...new Set(matched.map(page => page.subject))];
  if (subjects.length > 1) throw new Error('这个名称对应多个学科，请改用课程 ID：' + subjects.join('、'));
  const subject = subjects[0];
  const subjectPages = [];
  for (const page of pages) {
    if (page.subject !== subject) continue;
    if (subjectPages.some(row => row.moodleCourseid === page.moodleCourseid)) continue;
    subjectPages.push(page);
  }
  subjectPages.sort((first, second) => first.moodleCourseid - second.moodleCourseid);
  return {subject, pages: subjectPages, course: (state.courses ?? []).find(course => course.id === subject) ?? null};
}

// Reuse the real planning path so verification and installation agree on which
// files belong to the chapter and on the stable source key of each file.
export function collectSectionItems({page, sections}) {
  const enumerated = {page, sections, modules: sections.flatMap(section => section.modules ?? [])};
  const planned = planPage({page, enumerated, texts: emptyTextSources(), state: {}});
  return {files: planned.items.filter(item => item.kind === 'file'), skips: planned.skips};
}

function readLedger(path) {
  if (!existsSync(path)) return {entries: {}};
  try {
    const value = readJSON(path);
    return value && typeof value.entries === 'object' ? value : {entries: {}};
  } catch {
    return {entries: {}};
  }
}

// Materials registered before the ledger existed still carry a content baseline.
export function materialSources(state) {
  const byKey = new Map();
  for (const material of state.materials ?? []) {
    if (!material || material.superseded_by || ['archived', 'deleted'].includes(material.version_status)) continue;
    for (const source of material.moodle_file_sources ?? []) {
      if (!source?.remote_filename) continue;
      const key = `${material.course_id}|${source.module_id}|${source.filepath ?? '/'}|${source.remote_filename}`;
      if (!byKey.has(key)) byKey.set(key, material);
    }
  }
  return byKey;
}

export function resolveBaseline({entry, material}) {
  const usable = entry && BASELINE_LEDGER_STATUSES.has(entry.status) ? entry : null;
  if (usable?.remote) {
    return {
      source: 'ledger',
      filesize: usable.remote.filesize ?? null,
      timemodified: usable.remote.timemodified ?? null,
      url: usable.remote.url ?? null,
      sha256: usable.sha256 ?? material?.sha256 ?? null,
    };
  }
  if (material) {
    return {
      source: 'material',
      filesize: Number.isFinite(material.bytes) ? material.bytes : null,
      timemodified: null,
      url: null,
      sha256: material.sha256 ?? null,
    };
  }
  return {source: 'none', filesize: null, timemodified: null, url: null, sha256: null};
}

function differences(baseline, remote) {
  const changed = [];
  if (baseline.filesize !== null && remote.filesize !== null && baseline.filesize !== remote.filesize) changed.push('filesize');
  if (baseline.timemodified !== null && remote.timemodified !== null && baseline.timemodified !== remote.timemodified) changed.push('timemodified');
  if (baseline.url && remote.url && baseline.url !== remote.url) changed.push('source_url');
  return changed;
}

// Pure classification: tests and the CLI use exactly the same evidence rule.
export function classifyItem({item, entry, material, mode, sha256 = null}) {
  const baseline = resolveBaseline({entry, material});
  const remote = {filesize: item.filesize ?? null, timemodified: item.timemodified ?? null, url: item.cleanUrl ?? null};
  if (mode === 'strict') {
    if (!baseline.sha256) return {status: STATUS.no_baseline, evidence: 'content', baseline, remote, differences: []};
    const same = Boolean(sha256) && sha256 === baseline.sha256;
    return {status: same ? STATUS.unchanged : STATUS.changed, evidence: 'content', baseline, remote,
      differences: same ? [] : ['sha256']};
  }
  if (baseline.timemodified !== null) {
    const changed = differences(baseline, remote);
    return {status: changed.length ? STATUS.changed : STATUS.unchanged,
      evidence: 'metadata', baseline, remote, differences: changed};
  }
  if (baseline.filesize !== null && remote.filesize !== null) {
    const same = baseline.filesize === remote.filesize;
    return {status: same ? STATUS.unchanged : STATUS.changed, evidence: 'size', baseline, remote,
      differences: same ? [] : ['filesize']};
  }
  return {status: STATUS.no_baseline, evidence: 'none', baseline, remote, differences: []};
}

export function localState(root, material) {
  if (!material?.relative_path) return null;
  const study = resolve(root, 'Study');
  const target = resolve(study, material.relative_path);
  if (!target.startsWith(study + sep)) return {status: 'unsafe_path', relative_path: material.relative_path};
  if (!existsSync(target)) return {status: 'missing', relative_path: material.relative_path};
  try {
    const stat = statSync(target);
    return {status: Number.isFinite(material.bytes) && stat.size !== material.bytes ? 'size_mismatch' : 'present',
      relative_path: material.relative_path, bytes: stat.size};
  } catch {
    return {status: 'unreadable', relative_path: material.relative_path};
  }
}

function safeSegment(value) {
  const text = String(value ?? '').normalize('NFKC').trim().toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  const slug = text || 'section';
  return /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(slug) ? `_${slug}` : slug;
}

export function receiptPath(root, courseId, label) {
  const folder = join(resolve(root, 'Study'), '_System', 'ChapterChecks', safeSegment(courseId));
  return {folder, path: join(folder, `${safeSegment(label)}.json`)};
}

export function writeReceipt(root, receipt) {
  const study = resolve(root, 'Study');
  const {folder, path} = receiptPath(root, receipt.course.course_id, receipt.request.label);
  if (!folder.startsWith(study + sep)) throw new Error('核对结果必须保存在 Study 目录内');
  receipt.receipt_path = path.replaceAll('\\', '/');
  mkdirSync(folder, {recursive: true});
  if (realpathSync(folder) !== folder) throw new Error('核对结果目录不能是指向别处的链接');
  const temp = `${path}.${process.pid}.${receipt.run_id.slice(0, 8)}.tmp`;
  try {
    writeFileSync(temp, JSON.stringify(receipt, null, 2) + '\n', {encoding: 'utf8', flag: 'wx'});
    renameSync(temp, path);
  } finally {
    if (existsSync(temp)) {
      try { unlinkSync(temp); } catch { /* a leftover temp file is harmless */ }
    }
  }
  return path;
}

export function summarize(files) {
  const counts = {files: files.length, unchanged: 0, changed: 0, no_baseline: 0, error: 0};
  for (const file of files) if (counts[file.status] !== undefined) counts[file.status] += 1;
  return counts;
}

export function verdictOf(counts) {
  if (counts.error) return 'incomplete';
  if (counts.changed && counts.unchanged === 0 && counts.no_baseline === 0) return 'changed';
  if (counts.changed) return 'partly_changed';
  if (counts.no_baseline && counts.unchanged === 0) return 'unknown';
  if (counts.no_baseline) return 'partly_unknown';
  if (!counts.files) return 'no_files';
  return 'unchanged';
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

function sectionRow(page, section) {
  return {moodle_courseid: page.moodleCourseid, name: section.name, index: section.index,
    number: section.number ?? null, modules: (section.modules ?? []).length};
}

async function sha256OfFile({client, item, maxBytes, fetchFile}) {
  const {bytes} = await fetchFile(client, {url: item.url, filename: item.filename, filesize: item.filesize},
    {maxBytes});
  return createHash('sha256').update(bytes).digest('hex');
}

function rowFor({item, classification, sha256, local, status = null, differences = null}) {
  return {
    filename: item.filename,
    module_id: item.moduleId,
    module_name: item.moduleName,
    module_type: item.moduleType,
    section: item.section,
    category: item.category,
    remote: {filesize: item.filesize ?? null, timemodified: item.timemodified ?? null},
    baseline: {source: classification.baseline.source, filesize: classification.baseline.filesize,
      timemodified: classification.baseline.timemodified},
    status: status ?? classification.status,
    evidence: classification.evidence,
    differences: differences ?? classification.differences,
    content_sha256: sha256,
    local,
  };
}

function skipRow(skip) {
  return {
    filename: skip.filename ?? '（无可下载文件）',
    module_id: skip.moduleId, module_name: skip.moduleName, module_type: skip.moduleType,
    section: skip.section, category: null, remote: null, baseline: null,
    status: STATUS.skipped, evidence: null, differences: [], content_sha256: null, local: null,
    reason: skip.reason, detail: skip.detail ?? null,
  };
}

export async function checkChapter({
  client, root, state, course, section = null, modules = null, mode = 'quick',
  maxBytes = MAX_BYTES, persist = true, now = nowISO,
  fetchFile = fetchCourseFile, enumerate = enumerateCourse, ledgerPath = null,
} = {}) {
  if (!client || typeof client.call !== 'function') throw new Error('需要已连接的 Moodle 只读客户端');
  if (!MODES[mode]) throw new Error('核对强度只能是 quick 或 strict');
  if (!section && !modules) throw new Error('请提供章节名称/编号，或用 --module 指定模块');
  // The baseline comes from this profile's library ledger/materials, which are
  // keyed by numeric course/module IDs. Another school or account may reuse the
  // same numbers for different files, so refuse before enumerating or hashing.
  const checked = accountScope(client.baseUrl, client.userId ?? null);
  if (!checked) throw new Error('章节核对需要已连接的学校账号');
  assertProfileAccount({state, root, site: checked.site, userId: checked.user_id, action: '章节核对'});
  const resolved = resolveCoursePages(state, course);
  const pages = resolved.pages.map(page => ({...page, baseUrl: client.baseUrl}));
  const account = checked;
  const {blocked} = authorizationReport(state, pages, account);
  const blockedByPage = new Map(blocked.map(entry => [entry.page.moodleCourseid, entry.reason]));
  // Strict verification downloads chapter files, so it keeps the course-level
  // download authorization. Quick verification only reads course metadata.
  const usable = pages.filter(page => mode !== 'strict' || !blockedByPage.has(page.moodleCourseid));
  if (!usable.length) {
    throw new Error('该课程没有可用的完整资料授权，不能做严格核对；请先确认课程级 full 授权，或改用 --mode quick');
  }
  const moduleFilter = modules ? new Set(modules.map(Number).filter(Number.isInteger)) : null;
  if (moduleFilter && !moduleFilter.size) throw new Error('--module 需要至少一个有效的模块编号');

  const ledger = readLedger(ledgerPath ?? join(resolve(root, 'Study'), '_System/LibrarySync/ledger.json'));
  const bySource = materialSources(state);
  const sections = [], rows = [], errors = [], unmatched = [], available = [];

  for (const page of usable) {
    let enumerated;
    try {
      enumerated = await enumerate(client, page);
    } catch {
      errors.push({moodle_courseid: page.moodleCourseid, scope: 'course_contents',
        reason: '课程目录读取失败（网络、权限或接口错误）；本次未核对这个课程页面。'});
      continue;
    }
    available.push(...enumerated.sections.map(row => row.name || `#${row.index}`).filter(Boolean));
    let selected;
    if (moduleFilter) {
      const touched = enumerated.sections.filter(row => (row.modules ?? []).some(module => moduleFilter.has(module.id)));
      if (section) {
        const named = selectSections(enumerated.sections, section).sections;
        selected = touched.filter(row => named.includes(row));
      } else selected = touched;
    } else {
      selected = selectSections(enumerated.sections, section).sections;
    }
    if (!selected.length) {
      unmatched.push(page.moodleCourseid);
      continue;
    }
    for (const row of selected) sections.push(sectionRow(page, row));
    const collected = collectSectionItems({page, sections: selected});
    const files = moduleFilter ? collected.files.filter(item => moduleFilter.has(item.moduleId)) : collected.files;
    const classified = await mapLimit(files, 3, async item => {
      const entry = ledger.entries[item.key] ?? null;
      const material = bySource.get(item.key) ?? null;
      const classification = classifyItem({item, entry, material, mode});
      const local = localState(root, material);
      if (mode !== 'strict' || !classification.baseline.sha256) {
        return rowFor({item, classification, sha256: null, local});
      }
      try {
        const sha256 = await sha256OfFile({client, item, maxBytes, fetchFile});
        const same = sha256 === classification.baseline.sha256;
        return rowFor({item, classification, sha256, local,
          status: same ? STATUS.unchanged : STATUS.changed, differences: same ? [] : ['sha256']});
      } catch {
        // Fetch errors may carry an authenticated URL, so only a fixed reason is kept.
        return rowFor({item, classification, sha256: null, local, status: STATUS.error});
      }
    });
    rows.push(...classified);
    if (!moduleFilter) rows.push(...collected.skips.map(skipRow));
  }

  if (!sections.length) {
    if (errors.length && !available.length) throw new Error('课程目录读取失败：' + errors[0].reason);
    const detail = available.length ? `；可用的章节：${available.join('、')}` : '';
    throw new Error(`没有匹配到章节“${section ?? ''}”${detail}`);
  }

  const files = rows.filter(row => row.status !== STATUS.skipped);
  const counts = summarize(files);
  const receipt = {
    schema: CHAPTER_CHECK_SCHEMA,
    run_id: randomUUID().replaceAll('-', ''),
    checked_at: now(),
    mode,
    // The baseline is only meaningful for the account that recorded it.
    account_scope: {site: account.site, user_id: account.user_id},
    course: {
      course_id: resolved.subject,
      name: resolved.course?.name ?? null,
      shortname: resolved.course?.shortname ?? null,
      moodle_courseids: pages.map(page => page.moodleCourseid),
    },
    request: {section, modules: moduleFilter ? [...moduleFilter] : null,
      label: section ?? (moduleFilter ? 'modules-' + [...moduleFilter].join('-') : 'section'),
      sections_matched: sections.map(row => row.name), pages_without_match: unmatched},
    sections,
    files,
    skipped: rows.filter(row => row.status === STATUS.skipped),
    errors,
    summary: {...counts, skipped: rows.length - files.length},
    verdict: errors.length ? 'incomplete' : verdictOf(counts),
    authorization: {
      full: pages.filter(page => !blockedByPage.has(page.moodleCourseid)).map(page => page.moodleCourseid),
      blocked: [...blockedByPage].map(([moodleCourseid, reason]) => ({moodle_courseid: moodleCourseid, reason})),
      note: mode === 'strict' ? '严格核对只允许在课程级 full 授权范围内下载本章文件' : '快速核对只读取课程元数据，不下载文件',
    },
    scope_note: SCOPE_NOTE,
    limitations: [
      '快速核对比较服务器记录的文件大小与修改时间；同大小的改写可能漏检，记录变化但内容相同时会显示 changed。',
      mode === 'strict'
        ? '严格核对只重新获取本章文件并比较内容哈希；不重建索引，也不更新本地资料。'
        : '本次没有重新下载文件；需要按内容确认时改用 --mode strict。',
      '论坛附件、作业附件和正文内嵌图片不在本章核对范围内。',
      '测验题目、标签正文、外部链接和流媒体只保留入口与原因。',
    ],
    automatic_retry: false,
    no_school_writes: true,
  };
  if (counts.error || errors.length) receipt.limitations.push('部分文件或课程页面未能核对，本次结果不完整。');
  if (persist) writeReceipt(root, receipt);
  return receipt;
}
