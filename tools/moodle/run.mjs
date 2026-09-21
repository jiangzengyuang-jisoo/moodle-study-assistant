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

function tokenFromWindows() {
  const path = join(root, '.private/moodle-token.dpapi').replaceAll("'", "''");
  const shell = join(process.env.SystemRoot ?? 'C:/Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe');
  const result = spawnSync(shell, ['-NoProfile', '-NonInteractive', '-Command',
    "Add-Type -AssemblyName System.Security; [Console]::Out.Write([Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes('" + path + "'),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)))"
  ], {encoding: 'utf8', windowsHide: true});
  if (result.status !== 0 || !/^[a-zA-Z0-9]+$/.test(result.stdout ?? ''))
    throw new Error('请先运行 connect.cmd；Token 只能由保存它的 Windows 账户使用');
  return result.stdout;
}

async function main() {
  const mode = process.argv[2] ?? 'check';
  if (mode === 'init') { initialize(root); console.log('空白档案和本地 MCP 配置已准备好；已有档案保留。'); return; }
  if (!existsSync(statePath)) throw new Error('请先运行 setup.cmd');
  const config = json(join(root, 'config.local.json'));
  process.env.TZ = config.timezone;
  const client = await new ReadOnlyClient(config.moodle_url, tokenFromWindows()).connect();
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
    if (readFileSync(statePath, 'utf8') !== original) throw new Error('学习状态已被其他任务更新，请重试');
    atomicJSON(join(root, 'Study/_System/state.previous.json'), JSON.parse(original));
    state.revision++;
    state.updated_at = new Date().toISOString();
    atomicJSON(statePath, state);
    console.log('课程选择已保存；原有学习记录保留。');
    return;
  }
  if (mode === 'refresh') {
    const result = await collectRefresh(client, json(statePath));
    atomicJSON(join(root, 'Study/_System/refresh-latest.json'), result);
    if (result.collection_complete) atomicJSON(join(root, 'Study/_System/refresh-last-complete.json'), result);
    console.log(JSON.stringify({collection_complete: result.collection_complete, reviewed: false,
      courses: result.pages.length, discussions: result.threads.length, errors: result.errors,
      next: '请让 AI 阅读本次采集，与 state.json 核对后更新学习计划。'}));
    if (!result.collection_complete) process.exitCode = 2;
    return;
  }
  if (mode === 'mcp') { const { serve } = await import('./server.mjs'); await serve(client); return; }
  throw new Error('用法：run.mjs init | check | courses | refresh | mcp');
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
