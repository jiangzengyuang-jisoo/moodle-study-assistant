import {decryptWindowsToken} from './windows-token.mjs';
import {loadCollectionRequest, bindCollection} from './collection-request.mjs';
import { saveState, python } from './state-bridge.mjs';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { createInterface } from 'node:readline/promises';
import { initialize, atomicJSON, ReadOnlyClient } from './core.mjs';
import { collectRefresh } from './refresh.mjs';

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
    const chat = index >= 0 ? args[index + 1] : 'manual-refresh-entry';
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
    const available = await client.call('core_enrol_get_users_courses', {userid: client.userId});
    for (const course of available) console.log(`${course.id}  ${course.fullname} (${course.shortname})`);
    const prompt = createInterface({input: process.stdin, output: process.stdout});
    let answer;
    try { answer = await prompt.question('输入本学期需要跟踪的课程 ID，用逗号分隔（直接回车取消）：'); }
    finally { prompt.close(); }
    if (!answer.trim()) return;
    const ids = [...new Set(answer.trim().split(/[,，\s]+/).map(Number))];
    if (ids.some(id => !Number.isInteger(id) || !available.some(c => c.id === id))) throw new Error('课程 ID 不在当前账户的列表中');
    const original = readFileSync(statePath, 'utf8');
    const state = JSON.parse(original);
    // Keep learning history; selection only changes which pages are refreshed.
    for (const course of state.courses) course.moodle_course_ids = (course.moodle_course_ids ?? []).filter(id => ids.includes(id));
    for (const id of ids) {
      if (state.courses.some(c => c.moodle_course_ids?.includes(id))) continue;
      const source = available.find(c => c.id === id);
      const previous = state.courses.find(c => c.id === `moodle-${id}`);
      if (previous) previous.moodle_course_ids = [id];
      else state.courses.push({id: `moodle-${id}`, name: source.fullname, shortname: source.shortname,
        moodle_course_ids: [id], assignment_focus: null, current_topic: null, teaching_cursor: null, orientation: null});
    }
    saveState(root, 'replace', {expected: JSON.parse(original), state});
    console.log('课程选择已保存；原有学习记录保留。');
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
  throw new Error('用法：run.mjs init | check | courses | refresh | mcp | download <module-id> [...] --authorized');
}
main().catch(error => {
  // Stable, allowlisted code for diagnostics; never expose arbitrary properties.
  console.error(error.code === 'STUDY_TOKEN_DECRYPT_FAILED' ? `${error.code}: ${error.message}` : error.message);
  process.exitCode = 1;
});
