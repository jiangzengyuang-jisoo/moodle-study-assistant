// Chat choices use the deployed DeepSeek library policy, not Codex grants.
import {createHash} from 'node:crypto';
import {normalizeSite} from './core.mjs';
import {SCOPE_TEXT, SCOPE_VERSION} from './course-library.mjs';

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
const fingerprint = state => createHash('sha256').update(JSON.stringify(canonical(state))).digest('hex');

export function chatCourseOptions(args, isTTY) {
  if (args.length === 1 && args[0] === '--list') return {action: 'list'};
  if (args.length === 2 && args[0] === '--select-file' && args[1] && !args[1].startsWith('--')) {
    return {action: 'select', file: args[1]};
  }
  if (args.includes('--list') || args.includes('--select-file')) {
    throw new Error('使用 courses --list 或 courses --select-file <JSON路径>');
  }
  // Keep the existing fully parameterized legacy automation entry available.
  const value = flag => args[args.indexOf(flag) + 1];
  const complete = args.includes('--select') && value('--select') && !value('--select').startsWith('--')
    && args.includes('--mode') && ['full', 'info_only'].includes(value('--mode')) && args.includes('--yes');
  if (!isTTY && !complete) throw new Error('聊天中使用 courses --list / --select-file；不要等待终端菜单输入');
  return null;
}

export async function courseCatalog({client, readState}) {
  const courses = await client.call('core_enrol_get_users_courses', {userid: client.userId});
  const state = readState();
  return {version: 1, account_scope: {site: normalizeSite(client.baseUrl), user_id: client.userId},
    state_fingerprint: fingerprint(state), scope: SCOPE_TEXT, modes: ['full', 'info_only'],
    courses: courses.map(c => ({id: c.id, name: c.fullname, shortname: c.shortname,
      selected: state.courses.some(row => row.moodle_course_ids?.includes(c.id))})),
    instruction: '在当前聊天让本人确认课程及同步方式；列表是完整选择，不是仅追加。取消则不保存。'};
}

export async function selectCoursesInChat({client, readState, writeState, selection}) {
  if (selection?.version !== 1 || !Array.isArray(selection.course_ids) || !selection.course_ids.length
      || selection.course_ids.some(id => !Number.isSafeInteger(id) || id <= 0)
      || !['full', 'info_only'].includes(selection.mode)) {
    throw new Error('请在聊天中明确选择课程和 full / info_only；缺失参数不会进入终端菜单');
  }
  if (selection.account_scope?.site !== normalizeSite(client.baseUrl)
      || selection.account_scope?.user_id !== client.userId) {
    throw new Error('学校或账号已变化，请重新列出课程并由本人确认');
  }
  const available = await client.call('core_enrol_get_users_courses', {userid: client.userId});
  const ids = [...new Set(selection.course_ids)];
  if (ids.some(id => !available.some(c => c.id === id))) throw new Error('课程 ID 不在当前账户列表中');
  const original = readState();
  if (selection.state_fingerprint !== fingerprint(original)) {
    throw new Error('学习状态已变化，请重新列出课程并核对选择；未覆盖记录');
  }
  const state = structuredClone(original);
  state.settings ??= {};
  const previous = state.settings.course_library_authorization;
  if (previous && (previous.schema !== 1 || !previous.courses || typeof previous.courses !== 'object'
      || Array.isArray(previous.courses))) throw new Error('已有资料授权格式异常，请保留并核对');
  for (const row of state.courses) row.moodle_course_ids = (row.moodle_course_ids ?? []).filter(id => ids.includes(id));
  for (const id of ids) {
    if (state.courses.some(c => c.moodle_course_ids?.includes(id))) continue;
    const source = available.find(c => c.id === id);
    const old = state.courses.find(c => c.id === `moodle-${id}`);
    if (old) old.moodle_course_ids = [id];
    else state.courses.push({id: `moodle-${id}`, name: source.fullname, shortname: source.shortname,
      moodle_course_ids: [id], assignment_focus: null, current_topic: null, teaching_cursor: null, orientation: null});
  }
  const now = new Date().toISOString();
  const grants = {...previous?.courses};
  for (const course of state.courses) {
    if (!course.moodle_course_ids.length) continue;
    grants[course.id] = {moodle_courseid: course.moodle_course_ids[0],
      moodle_courseids: [...course.moodle_course_ids], mode: selection.mode, authorized_at: now,
      scope_version: SCOPE_VERSION, account_scope: structuredClone(selection.account_scope)};
  }
  state.settings.course_library_authorization = {...previous, schema: 1, scope_version: SCOPE_VERSION,
    scope: SCOPE_TEXT, updated_at: now, new_course_requires_authorization: true,
    per_file_approval_required: false, background_refresh: false, courses: grants};
  // One optimistic, locked save prevents selection and permission disagreeing.
  writeState(original, state);
  return {status: 'saved', mode: selection.mode, course_ids: ids, reviewed: false,
    next: '按当前原始聊天 ID 执行 study.cmd continue（已采集后改选课则 refresh），核读独立快照。full 模式核对本轮自动资料同步的 content_sync 与报告，已完成不重复；明确补齐时使用 library-sync --snapshot 该快照路径，info_only 不执行下载。完成后主动讲系统用法。'};
}
