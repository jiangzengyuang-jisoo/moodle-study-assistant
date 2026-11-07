// Chapter-scoped, read-only verification of files the student already has.
//
// A full collection answers "what changed anywhere in my courses"; it also reads
// every assignment submission state, discussion thread and calendar event. This
// module answers one narrower question: "is the file for this chapter still the
// file I already have?" It never downloads into the library, never installs,
// archives or indexes, and never promotes a review receipt. It therefore cannot
// replace a full collection for announcements, assignment requirements,
// discussions, deadlines or exam preparation.
//
// Chapter scope resolution is deliberately local-first and number-safe:
// - the local material index and its source mapping decide which Moodle modules
//   belong to the chapter being taught;
// - a Moodle "section number" (section.section) is navigation metadata, never a
//   chapter number; dates such as "Oct 5" and tutorial/lecture numbering are
//   parsed as explicit prefixes and never inferred from bare digits;
// - when several interpretations remain, the tool returns the candidates and
//   the reasons instead of silently selecting every section sharing a digit.
//
// Same-chat reuse is bound to the original chat digest, the school account, the
// course, the resolved material scope, the verification strength and the local
// file content hashes. A verified strict receipt is reused without any network
// call while those inputs are unchanged. Newly added local chapter material is
// verified incrementally; a changed local file, a new chat, a changed account or
// an explicit recheck invalidates reuse. Failed, missing-baseline or partially
// completed checks are never reused as success and are not retried automatically.
import {createHash, randomUUID} from 'node:crypto';
import {existsSync, mkdirSync, readFileSync, realpathSync, renameSync, statSync, unlinkSync, writeFileSync} from 'node:fs';
import {join, resolve, sep} from 'node:path';
import {MAX_BYTES, fetchCourseFile} from './course-files.mjs';
import {accountScope, assertProfileAccount, normalizeSite, profileAccountStatus} from './content-policy.mjs';
import {
  authorizationReport, emptyTextSources, enumerateCourse, nowISO, planPage, readJSON, selectedPages,
} from './course-library.mjs';

export const CHAPTER_CHECK_SCHEMA = 2;
export const MODES = Object.freeze({quick: 'quick', strict: 'strict'});
export const STATUS = Object.freeze({
  unchanged: 'unchanged', changed: 'changed', no_baseline: 'no_baseline', error: 'error', skipped: 'skipped',
});
// Ledger rows are usable baselines only after a verified installation.
const BASELINE_LEDGER_STATUSES = new Set(['installed', 'identical', 'duplicate', 'already_current']);
const SCOPE_NOTE = '本次只核对本章已定位到的本地资料与学校文件；公告、作业要求、论坛、日历和其他章节未检查，不能据此声称所有教学信息都是最新。';
const AMBIGUOUS_SCOPE_NOTE = '本章匹配不唯一，工具没有扩大核对范围；请根据候选改用更明确的章节名，或用 --modules 指定模块编号后重新核对。';
const MAX_CANDIDATES = 20;
const SAMPLE_FILES_LIMIT = 8;

export function normalizeText(value) {
  return String(value ?? '').normalize('NFKC').trim().toLowerCase().replace(/\s+/g, ' ');
}

// ---------------------------------------------------------------------------
// Chapter labels: explicit prefixes only.
//
// "Topic 5", "Chapter 8", "第8章", "Week 3", "Tutorial 4" are chapter labels.
// Bare dates ("Oct 5"), Moodle section positions (section.section === 5) and
// arbitrary numbers are not: they only become meaningful together with a word
// that names the kind of chapter. See chapterIntent().
// ---------------------------------------------------------------------------
const PREFIX_ALIASES = new Map([
  ['topic', 'topic'], ['topics', 'topic'], ['专题', 'topic'], ['專題', 'topic'], ['主题', 'topic'], ['主題', 'topic'],
  ['chapter', 'chapter'], ['chapters', 'chapter'], ['章', 'chapter'],
  ['lecture', 'lecture'], ['lectures', 'lecture'], ['讲', 'lecture'], ['講', 'lecture'],
  ['week', 'week'], ['weeks', 'week'], ['周', 'week'],
  ['lesson', 'lesson'], ['lessons', 'lesson'], ['课', 'lesson'], ['課', 'lesson'],
  ['tutorial', 'tutorial'], ['tutorials', 'tutorial'], ['tut', 'tutorial'], ['导修', 'tutorial'], ['導修', 'tutorial'],
  ['lab', 'lab'], ['labs', 'lab'], ['laboratory', 'lab'],
  ['seminar', 'seminar'], ['seminars', 'seminar'], ['workshop', 'workshop'], ['workshops', 'workshop'],
  ['unit', 'unit'], ['units', 'unit'], ['module', 'module'], ['modules', 'module'],
  ['part', 'part'], ['parts', 'part'], ['section', 'section'], ['sections', 'section'],
  ['block', 'section'], ['blocks', 'section'],
]);
const CN_FAMILY = new Map([
  ['专题', 'topic'], ['專題', 'topic'], ['主题', 'topic'], ['主題', 'topic'], ['单元', 'unit'], ['單元', 'unit'],
  ['章', 'chapter'], ['讲', 'lecture'], ['講', 'lecture'], ['课', 'lesson'], ['課', 'lesson'],
  ['周', 'week'], ['节', 'section'], ['節', 'section'],
]);
const CN_CHAPTER_RE = /第\s*(\d{1,3})\s*(专题|專題|主题|主題|单元|單元|章|讲|講|课|課|周|节|節)/gu;
const CN_TOPIC_RE = /(专题|專題|主题|主題)\s*(\d{1,3})(?=$|[^\d])/gu;
const EN_CHAPTER_RE = /(?:^|[^\p{L}\p{N}])([a-z]+)[\s._:#-]*(\d{1,3})(?=$|[^\d])/gu;
// "Oct 5" / "5 October" / "2026-10-05" are dates, not chapter names; they must
// never fall through to a section-name substring match.
const DATE_LIKE_RE = /^(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*\d{1,2}(?:st|nd|rd|th)?|\d{1,2}\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?)$/u;

function familyOf(word) {
  return PREFIX_ALIASES.get(word) ?? null;
}

// Return every explicit chapter-like token in a piece of evidence text.
// Examples: "Topic5.pdf" -> topic 5; "Tutorial 3 (Oct 5 - Oct 9)" -> tutorial 3
// only (the date is not a chapter); "Lecture Slides & Notes" -> none.
export function chapterTokens(value) {
  const text = normalizeText(value);
  const found = [];
  let match;
  CN_CHAPTER_RE.lastIndex = 0;
  while ((match = CN_CHAPTER_RE.exec(text)) !== null) {
    const family = CN_FAMILY.get(match[2]);
    if (family) found.push({family, number: Number(match[1]), raw: match[0], index: match.index});
  }
  CN_TOPIC_RE.lastIndex = 0;
  while ((match = CN_TOPIC_RE.exec(text)) !== null) {
    found.push({family: 'topic', number: Number(match[2]), raw: match[0], index: match.index});
  }
  EN_CHAPTER_RE.lastIndex = 0;
  while ((match = EN_CHAPTER_RE.exec(text)) !== null) {
    const family = familyOf(match[1]);
    if (family) found.push({family, number: Number(match[2]), raw: match[0].trim(), index: match.index});
  }
  found.sort((first, second) => first.index - second.index);
  return found;
}

// Parse what the student asked for. Numbered chapters keep their explicit family
// so "Tutorial 5" never quietly becomes "Topic 5".
export function chapterIntent(query) {
  const text = normalizeText(query);
  if (!text) throw new Error('请提供要核对的章节名称或编号');
  const chinese = text.match(/^第\s*(\d{1,3})\s*(专题|專題|主题|主題|单元|單元|章|讲|講|课|課|周|节|節)(?=$|[^\p{L}\p{N}])/u);
  if (chinese) {
    return {kind: CN_FAMILY.get(chinese[2]), family: CN_FAMILY.get(chinese[2]),
      number: Number(chinese[1]), text, basis: 'number', explicit: true};
  }
  const topicPrefix = text.match(/^(专题|專題|主题|主題)\s*(\d{1,3})(?=$|[^\d])/u);
  if (topicPrefix) {
    return {kind: 'topic', family: 'topic', number: Number(topicPrefix[2]), text, basis: 'number', explicit: true};
  }
  if (/^\d{1,3}$/.test(text)) {
    return {kind: 'bare', family: null, number: Number(text), text, basis: 'bare', explicit: false};
  }
  const english = text.match(/^([a-z]+)\s*[-_:#.]?\s*(\d{1,3})(?=$|[^\d])/u);
  if (english && familyOf(english[1])) {
    const family = familyOf(english[1]);
    return {kind: family, family, number: Number(english[2]), text, basis: 'number', explicit: true};
  }
  return {kind: 'name', family: null, number: null, text, basis: 'name', explicit: false,
    date_like: DATE_LIKE_RE.test(text)};
}

function dedupeHits(hits) {
  const seen = new Set();
  return hits.filter(hit => {
    const key = `${hit.kind ?? ''}|${hit.family ?? ''}|${hit.number ?? ''}|${hit.text ?? ''}|${hit.raw ?? ''}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function evidenceMatches(evidence, intent) {
  const tokens = [];
  for (const entry of evidence) {
    for (const token of chapterTokens(entry.text)) tokens.push({...token, kind: entry.kind, text: entry.text});
  }
  if (intent.kind === 'name') {
    const wanted = normalizeText(intent.text);
    const hits = [];
    for (const entry of evidence) {
      const text = normalizeText(entry.text);
      if (!text) continue;
      if (text === wanted || text.includes(wanted)) {
        hits.push({kind: entry.kind, text: entry.text, exact: text === wanted});
      }
    }
    return {matched: hits.length > 0, hits, conflicts: [], families: new Set(), exact: hits.some(hit => hit.exact)};
  }
  if (intent.kind === 'bare') {
    const hits = dedupeHits(tokens.filter(token => token.number === intent.number));
    return {matched: hits.length > 0, hits, conflicts: [], families: new Set(hits.map(hit => hit.family))};
  }
  const family = intent.family ?? intent.kind;
  const sameFamily = dedupeHits(tokens.filter(token => token.family === family));
  const hits = sameFamily.filter(token => token.number === intent.number);
  const conflicts = sameFamily.filter(token => token.number !== intent.number);
  return {matched: hits.length > 0, hits, conflicts, families: new Set(hits.map(hit => hit.family))};
}

// Section-name selection never looks at section.number: Moodle section numbers
// are navigation positions, not chapter numbers. Name matches are exact first,
// then unique substring; the caller decides whether several matches are
// ambiguous instead of treating them as one chapter.
export function selectSections(sections, query) {
  const wanted = normalizeText(query);
  if (!wanted) throw new Error('请提供要核对的章节名称或编号');
  // A bare digit or a date is never a section-name substring.
  if (/^\d{1,3}$/.test(wanted) || DATE_LIKE_RE.test(wanted)) return {sections: [], basis: 'none'};
  const rows = sections.map(section => ({section, name: normalizeText(section.name)}));
  const exact = rows.filter(row => row.name === wanted);
  if (exact.length) return {sections: exact.map(row => row.section), basis: 'name'};
  const substring = rows.filter(row => row.name.includes(wanted));
  if (substring.length) {
    return {sections: substring.map(row => row.section),
      basis: substring.length === 1 ? 'name_substring' : 'name_substring_ambiguous'};
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

function plannedPageItems({page, enumerated}) {
  return planPage({page, enumerated, texts: emptyTextSources(), state: {}});
}

// Build every file item and explained skip for one course page. Chapter scope is
// applied afterwards so the same item/ledger source keys are used for matching,
// baseline lookup and hash comparison.
export function collectPageItems({page, enumerated}) {
  const planned = plannedPageItems({page, enumerated});
  return {files: planned.items.filter(item => item.kind === 'file'), skips: planned.skips};
}

// Kept for compatibility with callers that already hold a section subset.
export function collectSectionItems({page, sections}) {
  const enumerated = {page, sections, modules: sections.flatMap(section => section.modules ?? [])};
  return collectPageItems({page, enumerated});
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

function localFileFacts(root, relativePath) {
  if (!root || !relativePath) return null;
  const study = resolve(root, 'Study');
  const target = resolve(study, relativePath);
  if (!target.startsWith(study + sep)) return {status: 'unsafe_path', relative_path: relativePath, sha256: null};
  if (!existsSync(target)) return {status: 'missing', relative_path: relativePath, sha256: null};
  try {
    const bytes = readFileSync(target);
    return {status: 'present', relative_path: relativePath, bytes: bytes.length,
      sha256: createHash('sha256').update(bytes).digest('hex')};
  } catch {
    return {status: 'unreadable', relative_path: relativePath, sha256: null};
  }
}

function safeSegment(value) {
  const text = String(value ?? '').normalize('NFKC').trim().toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  const slug = text || 'section';
  return /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(slug) ? `_${slug}` : slug;
}

export function requestLabel(section, modules) {
  if (section) return String(section);
  return 'modules-' + [...new Set((modules ?? []).map(Number).filter(Number.isInteger))].sort((a, b) => a - b).join('-');
}

export function receiptPath(root, courseId, label, suffix = null) {
  const folder = join(resolve(root, 'Study'), '_System', 'ChapterChecks', safeSegment(courseId));
  const name = safeSegment(label) + (suffix ? '.' + safeSegment(suffix) : '');
  return {folder, path: join(folder, `${name}.json`)};
}

export function writeReceipt(root, receipt, suffix = null) {
  const study = resolve(root, 'Study');
  const {folder, path} = receiptPath(root, receipt.course.course_id, receipt.request.label, suffix);
  if (!folder.startsWith(study + sep)) throw new Error('核对结果必须保存在 Study 目录内');
  receipt.receipt_path = path.replaceAll('\\', '/');
  mkdirSync(folder, {recursive: true});
  if (realpathSync(folder) !== folder) throw new Error('核对结果目录不能是指向别处的链接');
  const temp = `${path}.${process.pid}.${String(receipt.run_id ?? 'run').slice(0, 8)}.tmp`;
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

function readReceiptFile(path) {
  if (!existsSync(path)) return null;
  try {
    const value = readJSON(path);
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
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
    source_key: item.key,
    section: item.section,
    section_index: item.sectionIndex ?? null,
    category: item.category,
    remote: {filesize: item.filesize ?? null, timemodified: item.timemodified ?? null},
    baseline: {source: classification.baseline.source, filesize: classification.baseline.filesize,
      timemodified: classification.baseline.timemodified},
    status: status ?? classification.status,
    evidence: classification.evidence,
    differences: differences ?? classification.differences,
    content_sha256: sha256,
    local,
    from_cache: false,
  };
}

function skipRow(skip) {
  return {
    filename: skip.filename ?? '（无可下载文件）',
    module_id: skip.moduleId, module_name: skip.moduleName, module_type: skip.moduleType,
    source_key: null,
    section: skip.section, section_index: skip.source?.section_index ?? null,
    category: null, remote: null, baseline: null,
    status: STATUS.skipped, evidence: null, differences: [], content_sha256: null, local: null,
    reason: skip.reason, detail: skip.detail ?? null,
  };
}

// ---------------------------------------------------------------------------
// Local chapter scope: the material index and its source mapping come first.
// ---------------------------------------------------------------------------

function materialLayout(material, source) {
  const pages = (material.source_pages ?? []).filter(page => page
    && (page.module_id == null || Number(page.module_id) === Number(source.module_id)));
  const layout = pages.length ? pages : (material.source_pages ?? []);
  const sections = [];
  for (const page of layout) {
    const name = page?.section;
    if (!name) continue;
    const key = `${page.moodle_courseid ?? ''}|${page.section_index ?? ''}|${name}`;
    if (!sections.some(row => row.key === key)) {
      sections.push({key, name, index: page.section_index ?? null,
        moodle_courseid: Number.isInteger(page.moodle_courseid) ? page.moodle_courseid : null});
    }
  }
  const moduleName = layout.find(page => page?.module_name)?.module_name ?? null;
  const moodleCourseids = [...new Set(layout.map(page => page?.moodle_courseid).filter(Number.isInteger))];
  const evidence = [
    {kind: 'filename', text: source.remote_filename},
    {kind: 'filename', text: material.filename},
    {kind: 'title', text: material.title},
    {kind: 'module', text: moduleName},
    ...sections.map(section => ({kind: 'section', text: section.name})),
  ].filter(entry => entry.text);
  return {evidence, sections, moduleName, moodleCourseids};
}

function describeHit(hit, label) {
  return `${label}含「${hit.raw ?? hit.text}」`;
}

function candidateSummary(candidate) {
  const reasons = [];
  for (const hit of candidate.match.hits) reasons.push(describeHit(hit, hit.kind === 'section' ? '所在区块' : '文件名/标题/模块名'));
  for (const hit of candidate.match.conflicts) reasons.push(`同一族编号不一致：${hit.kind === 'section' ? '所在区块' : '文件名/标题/模块名'}含「${hit.raw}」`);
  return {
    filename: candidate.filename,
    module_id: candidate.module_id,
    module_name: candidate.module_name,
    relative_path: candidate.relative_path,
    sections: candidate.sections.map(section => section.name),
    moodle_courseids: candidate.moodle_courseids,
    reasons: [...new Set(reasons)],
    conflict: candidate.match.conflicts.length > 0,
    families: [...(candidate.match.families ?? [])],
    local: candidate.local ? {status: candidate.local.status, relative_path: candidate.local.relative_path,
      sha256: candidate.local.sha256 ?? null, bytes: candidate.local.bytes ?? null} : null,
  };
}

function uniqueSections(candidates) {
  const rows = [];
  for (const candidate of candidates) {
    for (const section of candidate.sections ?? []) {
      const key = `${section.moodle_courseid ?? ''}|${section.index ?? ''}|${section.name}`;
      if (!rows.some(row => row.key === key)) rows.push({key, ...section});
    }
  }
  return rows.map(({key, ...section}) => section);
}

function scopeSignature(moduleIds, files) {
  const payload = {
    modules: [...new Set((moduleIds ?? []).map(Number))].sort((first, second) => first - second),
    files: (files ?? []).map(file => [Number(file.module_id), String(file.relative_path ?? ''), String(file.sha256 ?? '')])
      .sort((first, second) => first[0] - second[0] || first[1].localeCompare(second[1])),
  };
  return createHash('sha256').update(JSON.stringify(payload)).digest('hex');
}

function ambiguousScope({subject, pages, course, intent, reason, candidates, basis}) {
  const summaries = candidates.slice(0, MAX_CANDIDATES).map(candidateSummary);
  return {status: 'ambiguous', subject, pages, course, intent, basis, reason,
    candidates: summaries, candidate_count: candidates.length,
    truncated: candidates.length > MAX_CANDIDATES};
}

function finishScopeResolution({candidates, intent, subject, pages, course, basis}) {
  let chosen = candidates;
  if (intent.kind === 'name') {
    const exact = candidates.filter(candidate => candidate.match.exact);
    chosen = exact.length ? exact : candidates;
    if (new Set(chosen.map(candidate => candidate.module_id)).size > 1) {
      return ambiguousScope({subject, pages, course, intent, basis,
        reason: `名称“${intent.text}”匹配到多个模块，无法确定本章文件`, candidates: chosen});
    }
  }
  const conflicting = chosen.filter(candidate => candidate.match.conflicts.length > 0);
  if (conflicting.length) {
    return ambiguousScope({subject, pages, course, intent, basis,
      reason: '部分候选同时出现本章编号和其他同族编号（或区块编号不一致），无法安全确定整章文件',
      candidates: chosen});
  }
  if (intent.kind === 'bare') {
    const families = new Set();
    for (const candidate of chosen) for (const hit of candidate.match.hits) families.add(hit.family);
    if (families.size > 1) {
      return ambiguousScope({subject, pages, course, intent, basis,
        reason: `纯数字“${intent.number}”同时对应 ${[...families].join('、')} 等不同资料类型；请改用明确的章节名或 --modules`,
        candidates: chosen});
    }
  }
  const moduleIds = [...new Set(chosen.map(candidate => candidate.module_id))].sort((first, second) => first - second);
  const files = chosen.map(candidate => ({
    module_id: candidate.module_id,
    filename: candidate.filename,
    filepath: candidate.filepath ?? '/',
    relative_path: candidate.relative_path ?? null,
    sha256: candidate.local?.sha256 ?? null,
    bytes: candidate.local?.bytes ?? null,
    local_status: candidate.local?.status ?? (candidate.relative_path ? 'missing' : 'unavailable'),
    baseline_sha256: candidate.baseline_sha256 ?? null,
  }));
  const pageIds = [...new Set(chosen.flatMap(candidate => candidate.moodle_courseids))];
  return {
    status: 'resolved', basis, subject, course, pages, intent, moduleIds, files,
    sections: uniqueSections(chosen),
    pages_in_scope: pageIds.length ? pageIds : pages.map(page => page.moodleCourseid),
    localSignature: scopeSignature(moduleIds, files),
    candidates: chosen.slice(0, MAX_CANDIDATES).map(candidateSummary),
    candidate_count: chosen.length,
  };
}

// Resolve the chapter against the local material index only (no network).
// Returns status 'resolved', 'ambiguous' or 'none'. An explicit --modules scope
// is accepted as-is; the caller still verifies it strictly.
export function resolveChapterLocal({state, root = null, course, section = null, modules = null, coursePages = null}) {
  if (!course) throw new Error('请提供课程 ID；章节核对只查这一门课');
  if (!section && !modules) throw new Error('请提供章节名称/编号，或用 --module 指定模块');
  const resolved = resolveCoursePages(state, course);
  const pages = coursePages ?? resolved.pages;
  const subject = resolved.subject;
  const wantedModules = modules ? new Set(modules.map(Number).filter(Number.isInteger)) : null;
  if (wantedModules && !wantedModules.size) throw new Error('--module 需要至少一个有效的模块编号');
  const intent = wantedModules
    ? {kind: 'modules', family: null, number: null, text: null, basis: 'modules', explicit: true}
    : chapterIntent(section);
  if (!wantedModules && intent.kind === 'name' && intent.date_like) {
    return {status: 'none', basis: 'local_materials', subject, pages, course: resolved.course, intent,
      candidates: [], date_like: true};
  }
  const candidates = [];
  const seen = new Set();
  for (const material of state.materials ?? []) {
    if (!material || material.course_id !== subject || material.superseded_by
        || ['archived', 'deleted'].includes(material.version_status)) continue;
    for (const source of material.moodle_file_sources ?? []) {
      if (!source?.remote_filename) continue;
      const moduleId = Number(source.module_id);
      if (!Number.isInteger(moduleId)) continue;
      if (wantedModules && !wantedModules.has(moduleId)) continue;
      const layout = materialLayout(material, source);
      const key = `${moduleId}|${material.relative_path ?? ''}|${source.remote_filename}`;
      if (seen.has(key)) continue;
      const evidence = intent.kind === 'name'
        ? layout.evidence.filter(entry => entry.kind !== 'section')
        : layout.evidence;
      const match = wantedModules
        ? {matched: true, hits: [], conflicts: [], families: new Set(), exact: true}
        : evidenceMatches(evidence, intent);
      if (!match.matched) continue;
      seen.add(key);
      candidates.push({
        module_id: moduleId,
        filename: source.remote_filename,
        filepath: source.filepath ?? '/',
        relative_path: material.relative_path ?? null,
        material_id: material.id,
        module_name: layout.moduleName,
        sections: layout.sections,
        moodle_courseids: layout.moodleCourseids,
        evidence: layout.evidence,
        match,
        local: root ? localFileFacts(root, material.relative_path) : null,
        baseline_sha256: material.sha256 ?? null,
      });
    }
  }
  if (!candidates.length) {
    if (wantedModules) {
      const moduleIds = [...wantedModules].sort((first, second) => first - second);
      return {
        status: 'resolved', basis: 'explicit_modules', subject, pages, course: resolved.course, intent,
        moduleIds, files: [], sections: [], pages_in_scope: pages.map(page => page.moodleCourseid),
        localSignature: scopeSignature(moduleIds, []), candidates: [], candidate_count: 0,
        section_selection: null,
      };
    }
    return {status: 'none', basis: 'local_materials',
      subject, pages, course: resolved.course, intent, candidates: []};
  }
  if (wantedModules) {
    const moduleIds = [...wantedModules].sort((first, second) => first - second);
    const files = candidates.map(candidate => ({
      module_id: candidate.module_id,
      filename: candidate.filename,
      filepath: candidate.filepath ?? '/',
      relative_path: candidate.relative_path ?? null,
      sha256: candidate.local?.sha256 ?? null,
      bytes: candidate.local?.bytes ?? null,
      local_status: candidate.local?.status ?? (candidate.relative_path ? 'missing' : 'unavailable'),
      baseline_sha256: candidate.baseline_sha256 ?? null,
    }));
    return {
      status: 'resolved', basis: 'explicit_modules', subject, pages, course: resolved.course, intent,
      moduleIds, files,
      sections: uniqueSections(candidates),
      pages_in_scope: [...new Set(candidates.flatMap(candidate => candidate.moodle_courseids))],
      localSignature: scopeSignature(moduleIds, files),
      candidates: candidates.slice(0, MAX_CANDIDATES).map(candidateSummary),
      candidate_count: candidates.length,
      section_selection: null,
    };
  }
  return finishScopeResolution({
    candidates, intent, subject, pages, course: resolved.course,
    basis: 'local_materials',
  });
}

// ---------------------------------------------------------------------------
// Course-catalog fallback: used only when the local index cannot name the
// chapter. Section names are authoritative only when they contain an explicit
// chapter label; section.number is never used. Modules are matched through
// their own names and filenames.
// ---------------------------------------------------------------------------

function catalogRows(enumeratedPages) {
  const sectionRows = [];
  const moduleRows = new Map();
  for (const {page, enumerated} of enumeratedPages) {
    for (const section of enumerated.sections) {
      sectionRows.push({page, section});
      for (const module of section.modules ?? []) {
        const key = `${page.subject}|${module.id}`;
        if (!moduleRows.has(key)) {
          moduleRows.set(key, {page, module, section, filenames: []});
        }
      }
    }
    const {files} = collectPageItems({page, enumerated});
    for (const item of files) {
      const row = moduleRows.get(`${page.subject}|${item.moduleId}`);
      if (row) row.filenames.push(item.filename);
    }
  }
  return {sectionRows, moduleRows};
}

function sectionCandidateEntry({page, section, match}) {
  return {
    module_id: null,
    filename: null,
    module_name: null,
    relative_path: null,
    sections: [{name: section.name, index: section.index, moodle_courseid: page.moodleCourseid}],
    moodle_courseids: [page.moodleCourseid],
    match,
    local: null,
    section_name: section.name,
    section_index: section.index,
    page,
  };
}

function resolveNameCatalog({sectionRows, moduleRows, intent, subject, pages, course}) {
  if (intent.date_like) return null;
  const wanted = normalizeText(intent.text);
  const names = new Map();
  for (const row of sectionRows) {
    const name = normalizeText(row.section.name);
    if (!name) continue;
    if (!names.has(name)) names.set(name, []);
    names.get(name).push(row);
  }
  const exactName = [...names.keys()].find(name => name === wanted);
  if (exactName) return sectionScopeFromRows({rows: names.get(exactName), subject, pages, course, intent});
  const substringNames = [...names.keys()].filter(name => name.includes(wanted));
  if (substringNames.length > 1) {
    const candidates = substringNames.flatMap(name => names.get(name)).map(row =>
      sectionCandidateEntry({...row, match: {matched: true, hits: [{kind: 'section', text: row.section.name}],
        conflicts: [], families: new Set()}}));
    return ambiguousScope({subject, pages, course, intent, basis: 'section_name',
      reason: `名称“${intent.text}”匹配到多个区块，无法确定本章范围`, candidates});
  }
  if (substringNames.length === 1) {
    return sectionScopeFromRows({rows: names.get(substringNames[0]), subject, pages, course, intent});
  }
  // Module/filename exact name, then a unique substring.
  const moduleMatches = [];
  for (const row of moduleRows.values()) {
    const evidence = [{kind: 'filename', text: row.module.name},
      ...row.filenames.map(filename => ({kind: 'filename', text: filename}))];
    const match = evidenceMatches(evidence, intent);
    if (match.matched) moduleMatches.push({row, match});
  }
  if (!moduleMatches.length) return null;
  const exact = moduleMatches.filter(entry => entry.match.hits.some(hit => normalizeText(hit.text) === wanted));
  const chosen = exact.length ? exact : moduleMatches;
  if (new Set(chosen.map(entry => entry.row.module.id)).size > 1) {
    const candidates = chosen.map(entry => moduleCandidateEntry(entry.row, entry.match));
    return ambiguousScope({subject, pages, course, intent, basis: 'course_catalog',
      reason: `名称“${intent.text}”匹配到多个模块，无法确定本章文件`, candidates});
  }
  return moduleScopeFromRows({rows: chosen.map(entry => entry.row), matches: chosen.map(entry => entry.match),
    subject, pages, course, intent});
}

function matchedSectionRows({sectionRows, intent}) {
  const matched = [];
  for (const row of sectionRows) {
    const match = evidenceMatches([{kind: 'section', text: row.section.name}], intent);
    if (match.matched) matched.push({...row, match});
  }
  return matched;
}

function sectionScopeFromRows({rows, subject, pages, course, intent}) {
  const pageIds = [...new Set(rows.map(row => row.page.moodleCourseid))];
  const names = [...new Set(rows.map(row => normalizeText(row.section.name)))];
  const selected = [];
  for (const pageRow of pages) {
    if (!pageIds.includes(pageRow.moodleCourseid) || !pageRow.enumerated) continue;
    for (const section of pageRow.enumerated.sections) {
      if (names.includes(normalizeText(section.name))) selected.push({page: pageRow, section});
    }
  }
  const files = [];
  const fileCache = new Map();
  for (const entry of selected) {
    let pageFiles = fileCache.get(entry.page.moodleCourseid);
    if (!pageFiles) {
      pageFiles = collectPageItems({page: entry.page, enumerated: entry.page.enumerated}).files;
      fileCache.set(entry.page.moodleCourseid, pageFiles);
    }
    for (const item of pageFiles) {
      if ((item.sectionIndex ?? null) === (entry.section.index ?? null)) files.push(item);
    }
  }
  return buildItemScope({files, selectedSections: selected, subject, pages, course, intent, basis: 'section_name'});
}

function moduleCandidateEntry(row, match) {
  return {
    module_id: row.module.id,
    filename: row.filenames[0] ?? row.module.name,
    module_name: row.module.name,
    relative_path: null,
    sections: [{name: row.section?.name ?? null, index: row.section?.index ?? null,
      moodle_courseid: row.page.moodleCourseid}].filter(section => section.name),
    moodle_courseids: [row.page.moodleCourseid],
    match,
    local: null,
  };
}

function moduleScopeFromRows({rows, matches, subject, pages, course, intent}) {
  const candidateRows = rows.map((row, index) => moduleCandidateEntry(row, matches?.[index] ?? {
    matched: true, hits: [], conflicts: [], families: new Set()}));
  const moduleIds = [...new Set(rows.map(row => row.module.id))].sort((first, second) => first - second);
  const pageIds = [...new Set(rows.map(row => row.page.moodleCourseid))];
  return {
    status: 'resolved', basis: 'course_catalog', subject, course, pages, intent,
    moduleIds,
    files: [],
    sections: uniqueSections(candidateRows),
    pages_in_scope: pageIds,
    localSignature: scopeSignature(moduleIds, []),
    candidates: candidateRows.slice(0, MAX_CANDIDATES),
    candidate_count: candidateRows.length,
    section_selection: null,
  };
}

function buildItemScope({files, selectedSections, subject, pages, course, intent, basis}) {
  const moduleIds = [...new Set(files.map(item => item.moduleId).filter(Number.isInteger))].sort((first, second) => first - second);
  const pageIds = [...new Set(files.map(item => item.moodleCourseid).filter(Number.isInteger))];
  return {
    status: 'resolved', basis, subject, course, pages, intent,
    moduleIds,
    files: [],
    sections: uniqueSections(selectedSections.map(entry => ({
      sections: [{name: entry.section.name, index: entry.section.index, moodle_courseid: entry.page.moodleCourseid}],
    }))),
    pages_in_scope: pageIds,
    localSignature: scopeSignature(moduleIds, []),
    candidates: [],
    candidate_count: files.length,
    section_selection: selectedSections.map(entry => ({moodle_courseid: entry.page.moodleCourseid,
      index: entry.section.index ?? null, name: entry.section.name})),
  };
}

// Resolve scope against the freshly enumerated course catalog. This is the
// fallback when no local material names the chapter; it never guesses from bare
// section positions or dates.
export function resolveCatalogScope({enumeratedPages, section, modules = null}) {
  if (modules) throw new Error('模块范围应在本地解析时直接指定');
  if (!section) throw new Error('请提供章节名称/编号，或用 --module 指定模块');
  const pages = enumeratedPages.map(entry => ({...entry.page, enumerated: entry.enumerated}));
  const firstPage = enumeratedPages[0];
  if (!firstPage) throw new Error('课程目录为空，无法定位章节');
  const subject = firstPage.page.subject;
  const course = firstPage.page.course ?? null;
  const intent = chapterIntent(section);
  const {sectionRows, moduleRows} = catalogRows(enumeratedPages);
  if (intent.kind === 'name') {
    const named = resolveNameCatalog({sectionRows, moduleRows, intent, subject, pages, course});
    if (named) return named;
  } else {
    const sectionMatches = matchedSectionRows({sectionRows, intent});
    if (sectionMatches.length) {
      if (intent.kind === 'bare') {
        const families = new Set();
        for (const row of sectionMatches) for (const hit of row.match.hits) families.add(hit.family);
        if (families.size > 1) {
          const candidates = sectionMatches.map(row => sectionCandidateEntry({...row}));
          return ambiguousScope({subject, pages, course, intent, basis: 'section_name',
            reason: `纯数字“${intent.number}”同时对应 ${[...families].join('、')} 等不同区块；请改用明确的章节名或 --modules`,
            candidates});
        }
      }
      const names = new Set(sectionMatches.map(row => normalizeText(row.section.name)));
      if (names.size > 1) {
        const candidates = sectionMatches.map(row => sectionCandidateEntry({...row}));
        return ambiguousScope({subject, pages, course, intent, basis: 'section_name',
          reason: `章节“${section}”匹配到多个区块，无法确定本章范围`, candidates});
      }
      return sectionScopeFromRows({rows: sectionMatches, subject, pages, course, intent});
    }
    const moduleMatches = [];
    for (const row of moduleRows.values()) {
      const evidence = [{kind: 'module', text: row.module.name},
        ...row.filenames.map(filename => ({kind: 'filename', text: filename}))];
      const match = evidenceMatches(evidence, intent);
      if (match.matched) moduleMatches.push({row, match});
    }
    if (moduleMatches.length) {
      const conflicting = moduleMatches.filter(entry => entry.match.conflicts.length > 0);
      if (conflicting.length) {
        return ambiguousScope({subject, pages, course, intent, basis: 'course_catalog',
          reason: '部分候选同时出现本章编号和其他同族编号，无法安全确定整章文件',
          candidates: moduleMatches.map(entry => moduleCandidateEntry(entry.row, entry.match))});
      }
      if (intent.kind === 'bare') {
        const families = new Set();
        for (const entry of moduleMatches) for (const hit of entry.match.hits) families.add(hit.family);
        if (families.size > 1) {
          return ambiguousScope({subject, pages, course, intent, basis: 'course_catalog',
            reason: `纯数字“${intent.number}”同时对应 ${[...families].join('、')} 等不同模块；请改用明确的章节名或 --modules`,
            candidates: moduleMatches.map(entry => moduleCandidateEntry(entry.row, entry.match))});
        }
      }
      return moduleScopeFromRows({rows: moduleMatches.map(entry => entry.row),
        matches: moduleMatches.map(entry => entry.match), subject, pages, course, intent});
    }
  }
  return {status: 'none', basis: 'course_catalog', subject, pages, course, intent,
    available_sections: [...new Set(sectionRows.map(row => row.section.name).filter(Boolean))]};
}

// ---------------------------------------------------------------------------
// Reuse validation and local hashing (no network).
// ---------------------------------------------------------------------------

function hashScopeLocalFiles(root, localFiles) {
  const rows = [];
  for (const file of localFiles ?? []) {
    if (!file?.relative_path) continue;
    const facts = localFileFacts(root, file.relative_path);
    rows.push({relative_path: file.relative_path, module_id: file.module_id ?? null,
      expected_sha256: file.sha256 ?? null, actual_sha256: facts?.sha256 ?? null,
      present: facts?.status === 'present'});
  }
  return rows;
}

function sameModuleSet(first, second) {
  const left = [...new Set((first ?? []).map(Number))].sort((a, b) => a - b);
  const right = [...new Set((second ?? []).map(Number))].sort((a, b) => a - b);
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function accountEvidence(state, root) {
  const status = profileAccountStatus(state, {root});
  if (status.status !== 'bound' || !status.site) return {status, account: null};
  return {status, account: {site: status.site, user_id: status.user_id}};
}

// Decide whether the previous strict receipt can be echoed or extended.
// Returns {kind:'reuse'|'incremental'|'none', reason, message, newModuleIds}.
export function validateReceiptReuse({receipt, plan, state, root = null, chatKey = null, mode = 'strict',
  coursePages = null}) {
  const no = reason => ({kind: 'none', reason, message: reuseMessage(reason)});
  if (!chatKey) return no('no_chat_key');
  if (!receipt || receipt.schema !== CHAPTER_CHECK_SCHEMA) return no('no_receipt');
  if (receipt.mode !== mode) return no('mode_changed');
  if (mode !== MODES.strict) return no('mode_not_strict');
  if (receipt.chat?.key !== String(chatKey)) return no('chat_changed');
  if (!receipt.course || !receipt.account_scope) return no('no_receipt');
  const subject = plan.subject;
  if (receipt.course.course_id !== subject) return no('course_changed');
  const pages = coursePages ?? plan.pages;
  const currentPageIds = pages.map(page => page.moodleCourseid).sort((a, b) => a - b);
  if (!sameModuleSet(receipt.course.moodle_courseids, currentPageIds)) return no('course_pages_changed');
  const expectedRequest = plan.intent?.kind === 'modules'
    ? {section: null, modules: plan.moduleIds}
    : {section: plan.intent?.text ?? null};
  const receiptModules = receipt.request?.modules ?? null;
  if (plan.intent?.kind === 'modules') {
    if (!sameModuleSet(receiptModules, expectedRequest.modules)) return no('scope_changed');
  } else if (normalizeText(receipt.request?.section) !== normalizeText(expectedRequest.section)) {
    return no('scope_changed');
  }
  const evidence = accountEvidence(state, root);
  if (!evidence.account) return no('account_unverified');
  if (normalizeSite(receipt.account_scope.site) !== evidence.account.site) return no('account_changed');
  if (evidence.account.user_id === null) return no('account_unverified');
  if (Number(receipt.account_scope.user_id) !== Number(evidence.account.user_id)) return no('account_changed');
  const {blocked} = authorizationReport(state, pages, receipt.account_scope);
  if (blocked.length) return no('authorization_changed');
  const localRows = hashScopeLocalFiles(root, receipt.scope?.local_files);
  if (localRows.some(row => !row.present || row.expected_sha256 !== row.actual_sha256)) {
    return no('local_file_changed');
  }
  if (plan.status === 'ambiguous') return no('ambiguous_scope');
  if (plan.status === 'none') {
    // The catalog/section/module receipt did not depend on the local index; its
    // recorded local files (if any) were hash-checked above.
    if (receipt.scope?.basis === 'local_materials') return no('local_index_changed');
    const complete = receipt.verified?.complete === true;
    return {kind: 'reuse', reason: complete ? 'same_chat_verified' : 'incomplete_previous',
      message: complete
        ? '同聊天已严格核对且本地文件未变；复用上次回执，不联网'
        : '上次核对未完整成功且本地范围未变；复用上次结果（不算全部核验成功），明确重查需 --force'};
  }
  const cachedModules = receipt.scope?.module_ids ?? [];
  const planModules = plan.moduleIds ?? [];
  const complete = receipt.verified?.complete === true;
  if (receipt.scope?.local_signature && receipt.scope.local_signature === plan.localSignature) {
    return {kind: 'reuse', reason: complete ? 'same_chat_verified' : 'incomplete_previous',
      message: complete
        ? '同聊天已严格核对且本地文件未变；复用上次回执，不联网'
        : '上次核对未完整成功且本地文件未变；复用上次结果（不算全部核验成功），明确重查需 --force'};
  }
  const planHasAllCached = cachedModules.every(moduleId => planModules.includes(Number(moduleId)));
  const added = planModules.filter(moduleId => !cachedModules.includes(Number(moduleId)));
  if (complete && planHasAllCached && added.length) {
    return {kind: 'incremental', reason: 'new_local_materials', newModuleIds: added,
      message: `本地新增 ${added.length} 个本章模块；只核对新增部分`};
  }
  if (!complete && planHasAllCached && sameModuleSet(planModules, cachedModules)) {
    // The previous attempt could not verify everything, and the local scope did
    // not change. Do not retry automatically on an ordinary follow-up; echo the
    // incomplete result. An explicit --force rechecks.
    return {kind: 'reuse', reason: 'incomplete_previous',
      message: '上次核对未完整成功且本地范围未变；复用上次结果（不算全部核验成功），明确重查需 --force'};
  }
  return no('local_scope_changed');
}

function reuseMessage(reason) {
  const messages = {
    no_chat_key: '没有聊天身份，无法绑定同聊天复用；本次重新核对',
    no_receipt: '没有可复用的严格核对回执；需要重新核对',
    mode_changed: '核对强度变化，上次回执不能复用',
    mode_not_strict: '只有严格核对（strict）的回执可复用',
    chat_changed: '新聊天，不复用其他聊天的回执',
    course_changed: '课程变化，上次回执不能复用',
    course_pages_changed: '课程页面范围变化，上次回执不能复用',
    scope_changed: '资料范围变化，上次回执不能复用',
    account_unverified: '无法从本地档案确认学校账号；需要重新联网核对',
    account_changed: '学校账号变化，上次回执失效',
    authorization_changed: '课程资料授权变化，上次回执失效',
    local_file_changed: '本地文件内容变化，上次回执失效',
    local_index_changed: '本地资料索引变化，需要重新核对',
    ambiguous_scope: '本章匹配不唯一，不复用旧回执',
    local_scope_changed: '本章资料范围变化，需要重新核对',
  };
  return messages[reason] ?? '上次回执不能安全复用；需要重新核对';
}

// ---------------------------------------------------------------------------
// Receipt construction helpers.
// ---------------------------------------------------------------------------

export function verifyCompleteness({mode, files, errors}) {
  if (mode !== MODES.strict) return {complete: false, evidence: 'metadata', reason: 'mode_not_strict'};
  if (errors.length) return {complete: false, evidence: 'content', reason: 'errors'};
  if (!files.length) return {complete: false, evidence: 'content', reason: 'no_files'};
  if (files.some(file => file.status === STATUS.error)) return {complete: false, evidence: 'content', reason: 'error'};
  if (files.some(file => file.status === STATUS.no_baseline)) {
    return {complete: false, evidence: 'content', reason: 'no_baseline'};
  }
  if (files.some(file => file.status === STATUS.skipped)) return {complete: false, evidence: 'content', reason: 'skipped'};
  if (files.some(file => !file.local || file.local.status !== 'present')) {
    return {complete: false, evidence: 'content', reason: 'local_not_present'};
  }
  return {complete: true, evidence: 'content', reason: null};
}

function scopeForReceipt(scope, localFiles) {
  return {
    basis: scope.basis,
    module_ids: [...new Set((scope.moduleIds ?? []).map(Number))].sort((first, second) => first - second),
    local_signature: scope.localSignature ?? scopeSignature(scope.moduleIds, localFiles),
    local_files: localFiles,
    section_selection: scope.section_selection ?? null,
  };
}

function ambiguityReceipt({mode, resolved, request, account = null, persist = true, root, now = nowISO}) {
  const candidates = (resolved.candidates ?? []).slice(0, MAX_CANDIDATES);
  const receipt = {
    schema: CHAPTER_CHECK_SCHEMA,
    run_id: randomUUID().replaceAll('-', ''),
    checked_at: now(),
    mode,
    ambiguous: true,
    account_scope: account ? {site: account.site, user_id: account.user_id} : null,
    course: {
      course_id: resolved.subject,
      name: resolved.course?.name ?? null,
      shortname: resolved.course?.shortname ?? null,
      moodle_courseids: resolved.pages.map(page => page.moodleCourseid),
    },
    request,
    scope: {status: 'ambiguous', basis: resolved.basis, intent: resolved.intent,
      candidate_count: resolved.candidate_count ?? candidates.length,
      candidates, reason: resolved.reason},
    resolution: {status: 'ambiguous', reason: resolved.reason,
      candidates: candidates.map(candidate => ({filename: candidate.filename, module_id: candidate.module_id,
        module_name: candidate.module_name, section: candidate.sections?.[0] ?? null,
        sections: candidate.sections, relative_path: candidate.relative_path,
        reasons: candidate.reasons, conflict: candidate.conflict}))},
    sections: [],
    files: [],
    skipped: [],
    errors: [],
    summary: {files: 0, unchanged: 0, changed: 0, no_baseline: 0, error: 0, skipped: 0,
      candidates: resolved.candidate_count ?? candidates.length},
    verdict: 'ambiguous',
    verified: {complete: false, evidence: null, reason: 'ambiguous'},
    reuse: {eligible: false, reason: 'ambiguous'},
    scope_note: AMBIGUOUS_SCOPE_NOTE,
    limitations: [
      '工具没有扩大范围，也没有联网下载任何文件；候选仅用于确定本章范围。',
      '请改用更明确的章节名（如 Topic 5、Chapter 8、Tutorial 3）或 --modules 指定模块编号。',
      '本章文件是否变化尚未核对。',
    ],
    automatic_retry: false,
    no_school_writes: true,
  };
  if (persist) writeReceipt(root, receipt, 'ambiguous');
  return receipt;
}

// ---------------------------------------------------------------------------
// Network side: enumerate only, then hash the resolved chapter files.
// ---------------------------------------------------------------------------

export async function checkChapter({
  client, root, state, course, section = null, modules = null, mode = 'strict',
  scope = null, carry = null, chatKey = null, maxBytes = MAX_BYTES, persist = true,
  now = nowISO, fetchFile = fetchCourseFile, enumerate = enumerateCourse, ledgerPath = null,
  progress = () => {},
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
  const request = {section, modules: moduleFilter ? [...moduleFilter].sort((a, b) => a - b) : null,
    label: requestLabel(section, modules)};

  let chapterScope = scope;
  if (!chapterScope) {
    chapterScope = resolveChapterLocal({state, root, course: resolved.subject, section, modules,
      coursePages: resolved.pages});
    if (chapterScope.status === 'ambiguous') {
      return ambiguityReceipt({mode, resolved: chapterScope, request, account, persist, root, now});
    }
  }
  if (chapterScope.status === 'ambiguous') {
    return ambiguityReceipt({mode, resolved: chapterScope, request, account, persist, root, now});
  }

  const sections = [], rows = [], errors = [], available = [];
  const enumeratedPages = [];
  for (const page of usable) {
    progress(`读取课程目录：${page.course?.name ?? page.subject}（moodle ${page.moodleCourseid}）`);
    let enumerated;
    try {
      enumerated = await enumerate(client, page);
    } catch {
      errors.push({moodle_courseid: page.moodleCourseid, scope: 'course_contents',
        reason: '课程目录读取失败（网络、权限或接口错误）；本次未核对这个课程页面。'});
      continue;
    }
    enumeratedPages.push({page, enumerated});
    available.push(...enumerated.sections.map(row => row.name || `#${row.index}`).filter(Boolean));
  }
  if (!enumeratedPages.length) {
    throw new Error('课程目录读取失败：' + (errors[0]?.reason ?? '网络、权限或接口错误'));
  }
  if (chapterScope.status === 'none') {
    progress('本地资料索引没有给出本章模块；改为按课程目录的章节名/模块名严格匹配');
    chapterScope = resolveCatalogScope({enumeratedPages, section, modules: null});
    if (chapterScope.status === 'ambiguous') {
      return ambiguityReceipt({mode, resolved: chapterScope, request, account, persist, root, now});
    }
    if (chapterScope.status === 'none') {
      const detail = available.length ? `；可用的章节：${available.join('、')}` : '';
      const dateHint = chapterIntent(section).date_like
        ? '“' + section + '”看起来是日期，不是章节编号；请改用 Topic 5、Chapter 8 这类明确章节名，或用 --modules 指定模块编号。'
        : '不会因为数字相同就选择整个区块；请改用更明确的章节名，或用 --modules 指定模块编号。';
      throw new Error(`没有匹配到章节“${section ?? ''}”${detail}；${dateHint}`);
    }
  }

  // Scope page authorization: a strict check must not silently ignore chapter
  // files that live on a blocked course page.
  if (mode === MODES.strict) {
    for (const pageId of chapterScope.pages_in_scope ?? []) {
      if (blockedByPage.has(pageId)) {
        errors.push({moodle_courseid: pageId, scope: 'authorization',
          reason: '本章文件所在课程页面没有完整资料授权（或授权账号/范围已变化）；本次未核对该页面。'});
      }
    }
  }

  // Gather all page items once, then select the chapter scope. Selection uses
  // module ids or explicit section (page,index) pairs from resolution; the item
  // key stays identical to the ledger/material source key.
  const allFiles = [];
  const allSkips = [];
  for (const {page, enumerated} of enumeratedPages) {
    const {files, skips} = collectPageItems({page, enumerated});
    allFiles.push(...files);
    allSkips.push(...skips);
  }
  const moduleIds = new Set(chapterScope.moduleIds ?? []);
  const sectionSelection = chapterScope.section_selection ?? null;
  const matchesScope = item => {
    if (moduleIds.size) return moduleIds.has(Number(item.moduleId));
    if (sectionSelection) return sectionSelection.some(selection =>
      selection.moodle_courseid === item.moodleCourseid
      && (selection.index ?? null) === (item.sectionIndex ?? null));
    return false;
  };
  const matchesSkipScope = skip => {
    if (!sectionSelection) return moduleIds.has(Number(skip.moduleId));
    return sectionSelection.some(selection => selection.moodle_courseid === skip.moodleCourseid
      && (selection.index ?? null) === (skip.source?.section_index ?? null));
  };
  const scopedFiles = allFiles.filter(item => item.moduleId != null && matchesScope(item));
  const scopedSkips = allSkips.filter(matchesSkipScope);
  const presentModules = new Set();
  for (const {enumerated} of enumeratedPages) {
    for (const section of enumerated.sections) {
      for (const module of section.modules ?? []) presentModules.add(Number(module.id));
    }
  }
  for (const moduleId of moduleIds) {
    if (!presentModules.has(Number(moduleId))) {
      errors.push({scope: 'module', module_id: Number(moduleId),
        reason: '本地资料映射到的模块在课程目录中不存在（可能已删除、移动或不再可见）；旧基线不再完整。'});
    }
  }
  for (const row of chapterScope.sections ?? []) {
    sections.push({moodle_courseid: row.moodle_courseid ?? null, name: row.name, index: row.index ?? null,
      number: null, modules: null});
  }

  const carryKeys = new Set((carry?.rows ?? []).map(row => row.source_key).filter(Boolean));
  const filesToVerify = scopedFiles.filter(item => !carryKeys.has(item.key));
  progress(`开始核对本章文件：共 ${scopedFiles.length} 个${carryKeys.size ? `（复用上次 ${carryKeys.size} 个，本次核对 ${filesToVerify.length} 个）` : ''}`);
  let completed = 0;
  const verified = await mapLimit(filesToVerify, 3, async item => {
    const entry = ledger.entries[item.key] ?? null;
    const material = bySource.get(item.key) ?? null;
    const classification = classifyItem({item, entry, material, mode});
    const local = localState(root, material);
    try {
      let row;
      if (mode !== 'strict' || !classification.baseline.sha256) {
        row = rowFor({item, classification, sha256: null, local});
      } else {
        const sha256 = await sha256OfFile({client, item, maxBytes, fetchFile});
        const same = sha256 === classification.baseline.sha256;
        row = rowFor({item, classification, sha256, local,
          status: same ? STATUS.unchanged : STATUS.changed, differences: same ? [] : ['sha256']});
      }
      completed += 1;
      progress(`核对文件 ${completed}/${filesToVerify.length}：${item.filename}`);
      return row;
    } catch {
      // Fetch errors may carry an authenticated URL, so only a fixed reason is kept.
      completed += 1;
      progress(`核对文件 ${completed}/${filesToVerify.length}：${item.filename}（失败，保留为不完整）`);
      return rowFor({item, classification, sha256: null, local, status: STATUS.error});
    }
  });
  rows.push(...verified);
  if (carry) {
    const carriedRows = [];
    for (const row of carry.rows ?? []) {
      if (!carryKeys.has(row.source_key)) continue;
      if (!presentModules.has(Number(row.module_id))) continue;
      carriedRows.push({...row, from_cache: true});
    }
    rows.push(...carriedRows);
    for (const section of carry.sections ?? []) {
      if (!sections.some(row => row.moodle_courseid === section.moodle_courseid
          && row.name === section.name && row.index === section.index)) sections.push(section);
    }
  }
  const files = rows.filter(row => row.status !== STATUS.skipped);
  const counts = summarize(files);
  const completeness = verifyCompleteness({mode, files, errors});
  const localFiles = [];
  const localByKey = new Map((chapterScope.files ?? []).map(file => [`${file.module_id}|${file.relative_path}`, file]));
  for (const row of files) {
    if (!row.local?.relative_path || row.local.status !== 'present') continue;
    const fromScope = localByKey.get(`${row.module_id}|${row.local.relative_path}`);
    localFiles.push({
      module_id: row.module_id,
      filename: row.filename,
      relative_path: row.local.relative_path,
      sha256: fromScope?.sha256 ?? localFileFacts(root, row.local.relative_path)?.sha256 ?? null,
    });
  }
  const receiptScope = scopeForReceipt(chapterScope, localFiles);
  const incremental = carry ? {
    previous_run_id: carry.run_id ?? null,
    added_module_ids: filesToVerify.map(item => Number(item.moduleId)).filter((value, index, all) => all.indexOf(value) === index),
    carried_files: rows.filter(row => row.from_cache).length,
    checked_files: verified.length,
  } : null;
  const receipt = {
    schema: CHAPTER_CHECK_SCHEMA,
    run_id: randomUUID().replaceAll('-', ''),
    checked_at: now(),
    mode,
    chat: chatKey ? {key: String(chatKey)} : null,
    // The baseline is only meaningful for the account that recorded it.
    account_scope: {site: account.site, user_id: account.user_id},
    course: {
      course_id: resolved.subject,
      name: resolved.course?.name ?? null,
      shortname: resolved.course?.shortname ?? null,
      moodle_courseids: pages.map(page => page.moodleCourseid),
    },
    request: {...request, intent: chapterScope.intent ?? null,
      match_basis: chapterScope.basis,
      sections_matched: chapterScope.basis === 'section_name' ? sections.map(row => row.name) : [],
      modules_matched: [...moduleIds].sort((first, second) => first - second),
      pages_without_match: []},
    scope: receiptScope,
    sections,
    files,
    skipped: scopedSkips.map(skipRow),
    errors,
    summary: {...counts, skipped: scopedSkips.length,
      from_cache: rows.filter(row => row.from_cache).length},
    verdict: errors.length ? 'incomplete' : verdictOf(counts),
    verified: completeness,
    reuse: {eligible: completeness.complete && mode === MODES.strict,
      reason: completeness.complete ? 'complete' : completeness.reason},
    incremental,
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
      (chapterScope.basis === 'local_materials' || chapterScope.basis === 'explicit_modules')
        ? '本章文件范围来自本地资料索引与来源映射；没有在本地登记的同章文件不在本次核对范围内。'
        : '本章文件范围来自课程目录中明确的章节名/模块名匹配；不按 Moodle 区块序号或日期猜测。',
      '论坛附件、作业附件和正文内嵌图片不在本章核对范围内。',
      '测验题目、标签正文、外部链接和流媒体只保留入口与原因。',
      '严格回执可按聊天、账号、课程、范围和本地内容哈希复用；本地文件变化或明确重查时才重新联网。',
    ],
    automatic_retry: false,
    no_school_writes: true,
  };
  if (counts.error || errors.length) receipt.limitations.push('部分文件或课程页面未能核对，本次结果不完整。');
  if (persist) writeReceipt(root, receipt);
  return receipt;
}

// ---------------------------------------------------------------------------
// Unified command orchestration: local resolution and receipt reuse happen
// before any network connection is opened.
// ---------------------------------------------------------------------------

function cloneReuse(receipt, {reason, message, now}) {
  return {
    ...receipt,
    reused: true,
    reused_at: now(),
    network_started: false,
    reuse: {eligible: false, reason, message,
      previous_run_id: receipt.run_id ?? null,
      previous_checked_at: receipt.checked_at ?? null,
      verified: receipt.verified ?? null},
  };
}

export async function runChapterCheck({
  root, loadState, course, section = null, modules = null, mode = 'strict',
  chatKey = null, force = false, persist = true, connect = null,
  progress = () => {}, now = nowISO, fetchFile = fetchCourseFile, enumerate = enumerateCourse,
  ledgerPath = null,
}) {
  if (!MODES[mode]) throw new Error('核对强度只能是 quick 或 strict');
  if (!course) throw new Error('请提供课程 ID；章节核对只查这一门课');
  if (!section && !modules) throw new Error('请提供章节名称/编号，或用 --module 指定模块');
  const state = loadState();
  progress('解析本章范围（优先本地资料索引与来源映射）');
  const plan = resolveChapterLocal({state, root, course, section, modules});
  const label = requestLabel(section, modules);
  const {path: cachePath} = receiptPath(root, plan.subject, label);
  const previous = readReceiptFile(cachePath);
  if (!force && previous) {
    const decision = validateReceiptReuse({receipt: previous, plan, state, root, chatKey, mode,
      coursePages: plan.pages});
    if (decision.kind === 'reuse') {
      progress(decision.message);
      return cloneReuse(previous, {reason: decision.reason, message: decision.message, now});
    }
    if (decision.kind === 'incremental') {
      progress(decision.message);
      if (!connect) throw new Error('需要对新增资料联网核对，但当前没有学校连接');
      const client = await connect();
      const receipt = await checkChapter({client, root, state, course, section, modules, mode, scope: plan,
        carry: {receipt: previous, moduleIds: previous.scope.module_ids ?? [],
          rows: previous.files ?? [], sections: previous.sections ?? [], run_id: previous.run_id},
        chatKey, persist, now, fetchFile, enumerate, ledgerPath, progress});
      return {...receipt, network_started: true};
    }
    progress(decision.message);
  } else if (force) {
    progress('用户明确要求重查；忽略同聊天回执，重新严格核对');
  }
  if (plan.status === 'ambiguous') {
    progress('本章匹配不唯一；返回候选与原因，不扩大范围、不联网');
    return {...ambiguityReceipt({mode, resolved: plan, request: {section,
      modules: modules ? [...new Set(modules.map(Number).filter(Number.isInteger))].sort((a, b) => a - b) : null,
      label}, account: null, persist, root, now}), network_started: false};
  }
  if (!connect) throw new Error('章节核对需要已连接的 Moodle 只读客户端');
  progress('连接学校只读接口（不会启动完整采集，也不会写学校状态）');
  const client = await connect();
  const receipt = await checkChapter({client, root, state, course, section, modules, mode,
    scope: plan.status === 'resolved' ? plan : null, chatKey, persist, now, fetchFile, enumerate,
    ledgerPath, progress});
  return {...receipt, network_started: true};
}
