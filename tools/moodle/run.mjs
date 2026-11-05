import {decryptWindowsToken} from './windows-token.mjs';
import {connectionDiagnostic} from './connection-errors.mjs';
import {loadCollectionRequest, bindCollection} from './collection-request.mjs';
import {resolveChatId} from './chat-identity.mjs';
import { saveState, python } from './state-bridge.mjs';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { createInterface } from 'node:readline/promises';
import { initialize, atomicJSON, ReadOnlyClient } from './core.mjs';
import { collectRefresh } from './refresh.mjs';
import {chatCourseOptions, courseCatalog, selectCoursesInChat} from './chat-course-selection.mjs';
import {normalizeSite} from './content-policy.mjs';
import {
  SCOPE_TEXT, SCOPE_VERSION, authorizeCourses, authorizationReport, newestSnapshotPath,
  readJSON, selectedPages, syncCourseLibrary,
} from './course-library.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const statePath = join(root, 'Study/_System/state.json');
const json = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
const loadState = () => json(statePath);

function tokenFromWindows(ciphertext = null) {
  return decryptWindowsToken(ciphertext ?? readFileSync(join(root, '.private/moodle-token.dpapi')));
}

function parseIds(value) {
  return [...new Set(String(value ?? '').trim().split(/[,，\s]+/).filter(Boolean).map(Number))];
}

function ensureIds(ids, available) {
  if (!ids.length) throw new Error('请至少选择一个课程页面');
  if (ids.some(id => !Number.isInteger(id) || !available.some(course => course.id === id)))
    throw new Error('课程 ID 不在当前账户的列表中');
  return ids;
}

function applyCourseSelection(state, available, ids) {
  for (const course of state.courses) {
    course.moodle_course_ids = (course.moodle_course_ids ?? []).filter(id => ids.includes(id));
  }
  const added = [];
  for (const id of ids) {
    if (state.courses.some(course => course.moodle_course_ids?.includes(id))) continue;
    const source = available.find(course => course.id === id);
    const previous = state.courses.find(course => course.id === `moodle-${id}`);
    if (previous) previous.moodle_course_ids = [id];
    else state.courses.push({id: `moodle-${id}`, name: source.fullname, shortname: source.shortname,
      moodle_course_ids: [id], assignment_focus: null, current_topic: null, teaching_cursor: null, orientation: null});
    added.push(id);
  }
  return added;
}

function saveSelection(ids, available) {
  const original = readFileSync(statePath, 'utf8');
  const state = JSON.parse(original);
  const added = applyCourseSelection(state, available, ids);
  saveState(root, 'replace', {expected: JSON.parse(original), state});
  return {state: loadState(), added};
}

function authorizeSelected(state, mode) {
  const pages = selectedPages(state);
  const courses = pages.map(page => ({
    course_id: page.courseId, moodle_courseid: page.moodleCourseid,
  }));
  return authorizeCourses({root, statePath, mode, scope: SCOPE_TEXT, scopeVersion: SCOPE_VERSION, courses});
}

function refreshForCourseSync(chat, explicit) {
  const args = [join(root, 'tools/moodle/run.mjs'), 'refresh'];
  if (chat) args.push('--chat', chat);
  const result = spawnSync(process.execPath, args, {
    cwd: root, stdio: 'inherit', windowsHide: true,
    // The chooser runs its library stage immediately after this metadata refresh.
    env: {...process.env, STUDY_PYTHON: python(root), STUDY_DEFER_CONTENT_SYNC: '1'},
  });
  return result.status ?? 1;
}

function snapshotSummary(snapshotPath) {
  if (!snapshotPath || !existsSync(snapshotPath)) return null;
  try {
    const snapshot = json(snapshotPath);
    return {path: snapshotPath.replaceAll('\\', '/'),
      run_id: snapshot.collection_binding?.run_id ?? null,
      collection_complete: snapshot.collection_complete === true,
      collected_at: snapshot.finished_at ?? snapshot.started_at ?? null,
      pages: snapshot.pages?.length ?? 0, threads: snapshot.threads?.length ?? 0};
  } catch { return null; }
}

function courseSyncReuse(rootPath) {
  const candidate = newestSnapshotPath(rootPath, 360);
  if (!candidate) return null;
  const info = snapshotSummary(candidate);
  return info && info.collection_complete ? info : null;
}

async function runCourses(client, options) {
  const available = await client.call('core_enrol_get_users_courses', {userid: client.userId});
  for (const course of available) console.log(`${course.id}  ${course.fullname} (${course.shortname})`);
  const prompt = createInterface({input: process.stdin, output: process.stdout});
  try {
    let ids;
    if (options.select) ids = ensureIds(parseIds(options.select), available);
    else {
      const answer = await prompt.question('输入本学期需要跟踪的课程 ID，用逗号分隔（直接回车取消）：');
      if (!answer.trim()) {
        console.log('未选择课程，未保存授权。');
        return;
      }
      ids = ensureIds(parseIds(answer), available);
    }
    let mode = options.mode;
    if (mode && !['full', 'info_only'].includes(mode)) throw new Error('--mode 只能是 full 或 info_only');
    if (!mode) {
      console.log('');
      console.log('课程级资料授权范围（一次授权，适用于上面选中的课程页面）：');
      console.log(SCOPE_TEXT);
      console.log('说明：授权后，正常课程同步会沿用该授权自动补齐新增或更新的资料，不再逐份询问；新增课程需要单独授权。');
      console.log('不开始测验尝试、不提交作业、不发帖、不修改学校状态；流媒体/外部网站/额外登录/权限受限内容只保留入口和原因。');
      console.log('1 完整初始化（同步课程信息 + 下载/归档/建立索引）');
      console.log('2 只同步课程信息（不下载资料）');
      console.log('0 取消（不保存）');
      const choice = (await prompt.question('请选择：')).trim();
      if (choice === '0' || choice === '') { console.log('已取消，未保存授权。'); return; }
      if (choice !== '1' && choice !== '2') throw new Error('请输入 1、2 或 0');
      mode = choice === '1' ? 'full' : 'info_only';
    }
    if (!options.yes) {
      const confirm = (await prompt.question(`确认保存课程级授权（${mode === 'full' ? '完整初始化' : '只同步信息'}）并继续？输入 Y 确认，其他返回：`)).trim().toLowerCase();
      if (confirm !== 'y') { console.log('未确认，未保存授权。'); return; }
    }
    const selectedIds = ids;
    saveSelection(selectedIds, available);
    const authorization = authorizeSelected(loadState(), mode);
    console.log(`课程级授权已保存：${authorization.authorized_courses.length} 个课程页面，模式 ${authorization.mode}。`);
    if (options.noSync) { console.log('已按 --no-sync 停止；未运行课程同步。'); return; }
    let snapshotPath = null;
    let refreshStatus = null;
    if (mode === 'info_only') {
      if (options.refresh || !courseSyncReuse(root)) {
        console.log('正在同步课程信息（只读采集，不下载资料）…');
        refreshStatus = refreshForCourseSync(options.chat, true);
      } else {
        const reuse = courseSyncReuse(root);
        snapshotPath = reuse.path;
        console.log(`课程信息同步复用本机最近一次完整快照：${reuse.run_id ?? 'unknown'}，采集于 ${reuse.collected_at ?? 'unknown'}。`);
      }
      if (snapshotPath) {
        console.log('已保存“只同步信息”授权；没有下载任何资料。');
      } else if (refreshStatus === 0) {
        const latest = snapshotSummary(join(root, 'Study/_System/refresh-latest.json'));
        console.log(`课程信息已采集：${latest?.pages ?? 0} 个页面、${latest?.threads ?? 0} 个讨论；资料未下载。请让 AI 核读本次快照。`);
      } else {
        console.log('课程信息采集未完整成功；旧记录保留。请让 AI 检查原因后明确重试，未下载资料。');
        process.exitCode = 2;
      }
      return;
    }
    let reuse = options.refresh ? null : courseSyncReuse(root);
    if (reuse) {
      snapshotPath = reuse.path;
      console.log(`课程信息同步复用本机最近一次完整快照：${reuse.run_id ?? 'unknown'}，采集于 ${reuse.collected_at ?? 'unknown'}。`);
    } else {
      console.log('正在同步课程信息（只读采集）…');
      refreshStatus = refreshForCourseSync(options.chat, true);
      const latest = snapshotSummary(join(root, 'Study/_System/refresh-latest.json'));
      if (latest?.collection_complete) snapshotPath = latest.path;
      else if (refreshStatus !== 0) console.log('课程信息采集未完整成功；继续尝试资料枚举与下载，缺口会逐项记录。');
    }
    console.log('正在枚举、下载、归档并建立资料索引（可中断，再次同步会续跑）…');
    const state = loadState();
    const report = await syncCourseLibrary({client, root, state, snapshotPath});
    printLibrarySummary(report);
    if (!report.ok) process.exitCode = 2;
    const latestSnapshot = snapshotPath ? snapshotSummary(snapshotPath) : null;
    if (latestSnapshot && !latestSnapshot.collection_complete) process.exitCode = 2;
    console.log('下一步：让 AI 核读本轮学校快照中的作业要求、公告与讨论，再开始学习；下载/解析成功不等于已核读或已掌握。');
  } finally { prompt.close(); }
}

function printLibrarySummary(report) {
  const totals = report.totals ?? {};
  console.log(JSON.stringify({
    run_id: report.run_id, ok: report.ok === true, text_source: report.text_source,
    totals, blocked: report.blocked ?? [],
    index: Object.fromEntries(Object.entries(report.index?.courses ?? {}).map(([course, value]) =>
      [course, {materials: value.materials, units: value.units, text_units: value.text_units, status_counts: value.status_counts}])),
    report_paths: ['Study/_System/LibrarySync/latest.json', 'Study/_System/LibrarySync/资料缺口.md'],
    no_school_writes: true,
  }, null, 2));
}

async function runLibrarySync(client, options) {
  const state = loadState();
  const args = process.argv.slice(3);
  const courseIds = [];
  for (let index = 0; index < args.length; index++) {
    if (args[index] === '--course' && args[index + 1]) courseIds.push(args[index + 1]);
  }
  const snapshotIndex = args.indexOf('--snapshot');
  let snapshotPath = snapshotIndex >= 0 ? args[snapshotIndex + 1] : null;
  if (snapshotPath && !existsSync(snapshotPath)) throw new Error('快照文件不存在');
  if (!snapshotPath && !args.includes('--no-snapshot')) {
    const recent = newestSnapshotPath(root, 30);
    if (recent) {
      try {
        const snapshot = json(recent);
        // Only a complete collection may replace a live read for announcements,
        // assignment requirements and discussions; partial snapshots fall back.
        if (snapshot.collection_complete === true) {
          snapshotPath = recent;
          console.log('使用最近 30 分钟内的完整快照作为公告/作业/讨论文字来源。');
        } else {
          console.log('最近快照采集不完整，本次改用只读 API 现场读取公告/作业/讨论。');
        }
      } catch {
        console.log('最近快照无法解析，本次改用只读 API 现场读取公告/作业/讨论。');
      }
    }
  }
  const maxIndex = args.indexOf('--max-bytes');
  const batchIndex = args.indexOf('--batch-size');
  const report = await syncCourseLibrary({
    client, root, state,
    courseIds: courseIds.length ? courseIds : null,
    snapshotPath,
    maxBytes: maxIndex >= 0 ? Number(args[maxIndex + 1]) : undefined,
    batchSize: batchIndex >= 0 ? Number(args[batchIndex + 1]) : undefined,
    dryRun: args.includes('--dry-run'),
  });
  printLibrarySummary(report);
  if (!report.ok) process.exitCode = 2;
}

async function main() {
  const mode = process.argv[2] ?? 'check';
  if (mode === 'init') { initialize(root); console.log('空白档案和本地 MCP 配置已准备好；已有档案保留。'); return; }
  const chatOptions = mode === 'courses' ? chatCourseOptions(process.argv.slice(3), process.stdin.isTTY) : null;
  if (!existsSync(statePath)) throw new Error('请先运行 setup.cmd');
  if (mode === 'refresh') {
    const args = process.argv.slice(3);
    const index = args.indexOf('--chat');
    let resolved;
    try {
      // Explicit --chat keeps the client's original thread ID; DSH main chats
      // supply it through DSH_SESSION_ID. Manual refresh without either keeps
      // the historical manual entry so a human command still has a stable key.
      resolved = resolveChatId(index >= 0 ? args[index + 1] : '', process.env);
    } catch (error) {
      console.error(error.message);
      process.exitCode = 1;
      return;
    }
    const chat = resolved ? resolved.chat : 'manual-refresh-entry';
    const result = spawnSync(python(root), [join(root, 'tools/study/session.py'), '--chat', chat, 'refresh'],
      {stdio: 'inherit', windowsHide: true, env: {...process.env, STUDY_NODE: process.execPath}});
    process.exitCode = result.status ?? 1;
    return;
  }
  let output, bundle;
  if (mode === 'collect') {
    const index = process.argv.indexOf('--output');
    output = index >= 0 ? process.argv[index + 1] : null;
    const requestIndex = process.argv.indexOf('--request');
    const requestPath = requestIndex >= 0 ? process.argv[requestIndex + 1] : null;
    bundle = loadCollectionRequest(root, requestPath, output);
  }
  const config = bundle?.config ?? json(join(root, 'config.local.json'));
  process.env.TZ = config.timezone;
  const client = await new ReadOnlyClient(config.moodle_url, tokenFromWindows(bundle?.credential)).connect();
  if (mode === 'check') { console.log('Moodle API 已连接。'); return; }
  if (mode === 'courses') {
    if (chatOptions) {
      const context = {client, readState: loadState,
        writeState: (expected, state) => saveState(root, 'replace', {expected, state})};
      const result = chatOptions.action === 'list' ? await courseCatalog(context)
        : await selectCoursesInChat({...context, selection: json(chatOptions.file)});
      console.log(JSON.stringify(result));
      return;
    }
    const args = process.argv.slice(3);
    const selectIndex = args.indexOf('--select');
    const modeIndex = args.indexOf('--mode');
    const chatIndex = args.indexOf('--chat');
    await runCourses(client, {
      select: selectIndex >= 0 ? args[selectIndex + 1] : null,
      mode: modeIndex >= 0 ? args[modeIndex + 1] : null,
      chat: chatIndex >= 0 ? args[chatIndex + 1] : null,
      yes: args.includes('--yes'),
      noSync: args.includes('--no-sync'),
      refresh: args.includes('--refresh'),
    });
    return;
  }
  if (mode === 'library-sync') {
    await runLibrarySync(client);
    return;
  }
  if (mode === 'chapter-check') {
    // Narrow verification: one course, one chapter. It never installs files or
    // promotes a review receipt, so it cannot stand in for a full collection.
    const {checkChapter} = await import('./chapter-check.mjs');
    const args = process.argv.slice(3);
    const option = flag => {
      const index = args.indexOf(flag);
      return index >= 0 ? args[index + 1] : null;
    };
    const moduleArg = option('--module');
    const receipt = await checkChapter({
      client, root, state: loadState(),
      course: option('--course'), section: option('--section'),
      modules: moduleArg ? moduleArg.split(',').map(value => Number(value.trim())) : null,
      mode: option('--mode') ?? 'quick',
      persist: !args.includes('--no-persist'),
    });
    console.log(JSON.stringify(receipt, null, 2));
    return;
  }
  if (mode === 'collect') {
    const result = bindCollection(bundle, await collectRefresh(client, bundle.state), client.userId);
    atomicJSON(output, result);
    console.log(JSON.stringify({collection_complete: result.collection_complete, reviewed: false,
      courses: result.pages.length, discussions: result.threads.length, errors: result.errors,
      next: '请让 AI 阅读本次采集，与 state.json 核对后更新学习计划。'}));
    if (!result.collection_complete) process.exitCode = 2;
    return;
  }
  if (mode === 'content-sync') {
    const index = process.argv.indexOf('--snapshot');
    const path = index >= 0 ? process.argv[index + 1] : null;
    if (!path) throw new Error('课程内容同步需要本轮已保存的快照');
    // Reuse the state writer's current-run gate; an old/modified snapshot or a
    // changed school/course scope must not start downloads.
    const checked = spawnSync(python(root), ['-B', '-c',
      'import sys; sys.path.insert(0, sys.argv[1]); from snapshot_guard import validate_current_snapshot; from state_io import read; validate_current_snapshot(sys.argv[2], read(sys.argv[3]), allow_legacy=False)',
      join(root, 'tools/study'), statePath, path], {encoding: 'utf8', windowsHide: true});
    if (checked.status !== 0) throw new Error('本轮快照或课程范围已变化，未启动资料下载；请明确重新同步');
    const snapshot = json(path);
    if (snapshot.account_scope?.user_id !== client.userId
        || normalizeSite(snapshot.account_scope?.site) !== normalizeSite(client.baseUrl)) {
      throw new Error('本轮快照与当前学校账户不一致，未启动资料下载');
    }
    const state = json(statePath);
    const captured = new Set(snapshot.pages.map(page => page.courseid));
    state.courses = state.courses.map(course => ({...course,
      moodle_course_ids: (course.moodle_course_ids ?? []).filter(id => captured.has(id))}));
    const {syncAuthorizedContent} = await import('./content-sync-router.mjs');
    const result = await syncAuthorizedContent({client, state, root, snapshot, snapshotPath: path});
    console.log(JSON.stringify({...result, reviewed: false}));
    if (result.status === 'partial') process.exitCode = 2;
    return;
  }
  if (mode === 'download') {
    const args = process.argv.slice(3);
    const authorized = args.includes('--authorized');
    const ids = args.filter(arg => arg !== '--authorized').map(Number);
    const { downloadModules } = await import('./download.mjs');
    const result = await downloadModules(client, json(statePath), root, {moduleIds: ids, authorized});
    atomicJSON(join(root, 'Study/_System/authorized-download-latest.json'), result);
    console.log(JSON.stringify(result));
    if (result.errors.length) process.exitCode = 2;
    return;
  }
  if (mode === 'mcp') { const { serve } = await import('./server.mjs'); await serve(client); return; }
  throw new Error('用法：run.mjs init | check | courses [--select IDS --mode full|info_only --yes] | library-sync [--course ID] [--snapshot PATH] [--dry-run] | chapter-check --course ID --section 章节 [--mode quick|strict] [--module IDS] [--no-persist] | content-sync --snapshot PATH | refresh | mcp | download <module-id> [...] --authorized');
}
main().catch(error => {
  // Stable, allowlisted code for diagnostics; never expose arbitrary properties.
  const diagnostic = connectionDiagnostic(error);
  console.error(diagnostic ? JSON.stringify(diagnostic) : error.message);
  process.exitCode = 1;
});
