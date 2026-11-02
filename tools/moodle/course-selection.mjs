import {contentModeFromAnswer, grantCourseContent, normalizeSite} from './content-policy.mjs';
import {createHash} from 'node:crypto';

// Bind a chat choice to the account AND the exact selection/settings shown.
// Canonical key ordering makes the digest stable across JSON serialization.
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
const fingerprint = state => createHash('sha256').update(JSON.stringify(canonical(state))).digest('hex');

export function parseCourseOptions(args, isTTY) {
  if (args.length === 1 && args[0] === '--list') return {action: 'list'};
  if (args.length === 2 && args[0] === '--select-file' && args[1] && !args[1].startsWith('--')) {
    return {action: 'select', file: args[1]};
  }
  if (args.length && !(args.length === 2 && args[0] === '--chat' && args[1] && !args[1].startsWith('--'))) {
    throw new Error('使用 courses --list 或 courses --select-file <JSON路径>');
  }
  if (!isTTY) throw new Error('聊天中不要运行交互菜单；使用 courses --list / --select-file');
  return {action: 'interactive', chat: args[1] ?? ''};
}

export async function courseCatalog({client, readState}) {
  const available = await client.call('core_enrol_get_users_courses', {userid: client.userId});
  const state = readState();
  return {version: 1, account_scope: {site: normalizeSite(client.baseUrl), user_id: client.userId},
    state_fingerprint: fingerprint(state),
    courses: available.map(c => ({id: c.id, name: c.fullname, shortname: c.shortname,
      selected: state.courses.some(row => row.moodle_course_ids?.includes(c.id))})),
    modes: ['full', 'metadata'], selection_semantics: 'replace_selected_pages_preserve_history',
    instruction: '在聊天中让本人确认学校、账号、完整课程名单与同步方式；取消则不调用保存。'};
}

export async function selectCoursesInChat({client, readState, writeState, selection}) {
  if (selection?.version !== 1 || !Array.isArray(selection.course_ids) || !selection.course_ids.length
      || selection.course_ids.some(id => !Number.isSafeInteger(id) || id <= 0)
      || !['full', 'metadata'].includes(selection.mode)) {
    throw new Error('请先在聊天中明确选择课程和同步方式；缺失参数不会转入终端菜单');
  }
  if (selection.account_scope?.site !== normalizeSite(client.baseUrl)
      || selection.account_scope?.user_id !== client.userId) {
    throw new Error('学校或账号已变化，请重新列出课程并由本人确认');
  }
  const available = await client.call('core_enrol_get_users_courses', {userid: client.userId});
  const ids = [...new Set(selection.course_ids)];
  if (ids.some(id => !available.some(c => c.id === id))) throw new Error('课程 ID 不在当前账户的列表中');
  const original = readState();
  if (selection.state_fingerprint !== fingerprint(original)) {
    throw new Error('学习状态已变化，请重新列出课程并核对选择；没有覆盖任何记录');
  }
  saveSelection({client, original, available, ids, mode: selection.mode, writeState});
  // The assistant continues in its ORIGINAL chat using the normal session
  // protocol; selection never invents a chat, refreshes, or claims review.
  return {status: 'saved', mode: selection.mode, course_ids: ids, reviewed: false,
    next: '使用当前原始聊天 ID 执行 study.cmd continue；已有采集后改变选课用 refresh。核读返回的独立快照后主动讲解系统用法。'};
}

function saveSelection({client, original, available, ids, mode, writeState}) {
  let state = structuredClone(original);
  for (const course of state.courses) course.moodle_course_ids = (course.moodle_course_ids ?? []).filter(id => ids.includes(id));
  for (const id of ids) {
    if (state.courses.some(c => c.moodle_course_ids?.includes(id))) continue;
    const source = available.find(c => c.id === id);
    const previous = state.courses.find(c => c.id === `moodle-${id}`);
    if (previous) previous.moodle_course_ids = [id];
    else state.courses.push({id: `moodle-${id}`, name: source.fullname, shortname: source.shortname,
      moodle_course_ids: [id], assignment_focus: null, current_topic: null, teaching_cursor: null, orientation: null});
  }
  state = grantCourseContent(state, {site: client.baseUrl, userId: client.userId, courseIds: ids, mode});
  writeState(original, state);
}

// The UI and persistence are injected so acceptance tests exercise the real
// selection/authorization/initialization ordering without a school account.
export async function chooseCourses({client, readState, writeState, ask, output, initialize}) {
  const available = await client.call('core_enrol_get_users_courses', {userid: client.userId});
  for (const course of available) output(`${course.id}  ${course.fullname} (${course.shortname})`);
  const answer = await ask('输入本学期需要跟踪的课程 ID，用逗号分隔（直接回车取消）：');
  if (!answer.trim()) return {status: 'cancelled'};
  const ids = [...new Set(answer.trim().split(/[,，\s]+/).map(Number))];
  if (ids.some(id => !Number.isSafeInteger(id) || !available.some(c => c.id === id))) {
    throw new Error('课程 ID 不在当前账户的列表中');
  }
  output('1 完整初始化：授权保存所选课程全部可获取的正文和直接下载文件、建立资料索引；以后正常同步自动增量补齐。');
  output('2 仅同步信息：采集课程、公告、作业等信息，不下载课程文件。无后台下载；不会答题、提交或修改学校状态。');
  const mode = contentModeFromAnswer(await ask('请选择 1 或 2（直接回车取消，本次不改选课或授权）：'));
  if (mode === null) return {status: 'cancelled'};
  const original = readState();
  saveSelection({client, original, available, ids, mode, writeState});
  output('课程选择和同步方式已保存，正在首次同步；原有学习记录保留。');
  await initialize();
  return {status: 'saved', mode, course_ids: ids};
}
