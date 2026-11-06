import {assertProfileAccount, contentModeFromAnswer, grantCourseContent, withProfileAccount} from './content-policy.mjs';

// The UI and persistence are injected so acceptance tests exercise the real
// selection/authorization/initialization ordering without a school account.
export async function chooseCourses({client, readState, writeState, ask, output, initialize, root = null}) {
  // One Study profile keeps one school account: a different school or student
  // account can reuse the same numeric course IDs for unrelated courses.
  const original = readState();
  const {account} = assertProfileAccount({state: original, root,
    site: client.baseUrl, userId: client.userId, action: '保存选课和资料授权'});
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
  let state = withProfileAccount(original, {site: account.site, userId: account.user_id});
  for (const course of state.courses) course.moodle_course_ids = (course.moodle_course_ids ?? []).filter(id => ids.includes(id));
  for (const id of ids) {
    if (state.courses.some(c => c.moodle_course_ids?.includes(id))) continue;
    const source = available.find(c => c.id === id);
    const previous = state.courses.find(c => c.id === `moodle-${id}`);
    if (previous) previous.moodle_course_ids = [id];
    else state.courses.push({id: `moodle-${id}`, name: source.fullname, shortname: source.shortname,
      moodle_course_ids: [id], assignment_focus: null, current_topic: null, teaching_cursor: null, orientation: null});
  }
  state = grantCourseContent(state, {site: account.site, userId: account.user_id, courseIds: ids, mode});
  writeState(original, state);
  output('课程选择和同步方式已保存，正在首次同步；原有学习记录保留。');
  await initialize();
  return {status: 'saved', mode, course_ids: ids};
}
