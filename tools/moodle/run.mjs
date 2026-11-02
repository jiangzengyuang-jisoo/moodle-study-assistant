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
import {chooseCourses} from './course-selection.mjs';
import {normalizeSite} from './content-policy.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const statePath = join(root, 'Study/_System/state.json');
const json = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));

function tokenFromWindows(ciphertext = null) {
  return decryptWindowsToken(ciphertext ?? readFileSync(join(root, '.private/moodle-token.dpapi')));
}

async function main() {
  const mode = process.argv[2] ?? 'check';
  if (mode === 'init') { initialize(root); console.log('空白档案和本地 MCP 配置已准备好；已有档案保留。'); return; }
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
    const chatIndex = process.argv.indexOf('--chat');
    const identity = resolveChatId(chatIndex >= 0 ? process.argv[chatIndex + 1] : '', process.env);
    // A standalone setup window has no chat. Its explicit initialization run
    // uses a separate setup key; it is never substituted for a learning chat.
    const chat = identity?.chat ?? 'course-initialization';
    const prompt = createInterface({input: process.stdin, output: process.stdout});
    try {
      await chooseCourses({client, readState: () => json(statePath),
        writeState: (expected, state) => saveState(root, 'replace', {expected, state}),
        ask: question => prompt.question(question), output: message => console.log(message),
        initialize: async () => {
          prompt.close();
          const result = spawnSync(python(root), [join(root, 'tools/study/session.py'), '--chat', chat, 'refresh'],
            {stdio: 'inherit', windowsHide: true, env: {...process.env, STUDY_NODE: process.execPath}});
          process.exitCode = result.status ?? 1;
        }});
    } finally { prompt.close(); }
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
    const {syncCourseContent} = await import('./course-content-sync.mjs');
    const result = await syncCourseContent(client, state, {studyRoot: join(root, 'Study'), statePath,
      studyToolsDir: join(root, 'tools/study'), python: python(root), snapshot});
    console.log(JSON.stringify({status: result.status, counts: result.counts,
      report_path: join(root, 'Study/_System/CourseContent/latest.json'), reviewed: false}));
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
  throw new Error('用法：run.mjs init | check | courses | refresh | content-sync --snapshot <path> | mcp | download <module-id> [...] --authorized');
}
main().catch(error => {
  // Stable, allowlisted code for diagnostics; never expose arbitrary properties.
  const diagnostic = connectionDiagnostic(error);
  console.error(diagnostic ? JSON.stringify(diagnostic) : error.message);
  process.exitCode = 1;
});
