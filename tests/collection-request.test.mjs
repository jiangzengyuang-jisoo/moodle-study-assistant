import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {loadCollectionRequest, bindCollection, sha256} from '../tools/moodle/collection-request.mjs';
import {collectRefresh} from '../tools/moodle/refresh.mjs';
import {decryptWindowsToken} from '../tools/moodle/windows-token.mjs';

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'request 学习 '));
  const run = 'a'.repeat(32), folder = join(root, 'Study/_System/Runtime/runs', run);
  mkdirSync(folder, {recursive: true}); mkdirSync(join(root, '.private'));
  const credential = Buffer.from('synthetic encrypted bytes, never a real token');
  writeFileSync(join(root, '.private/moodle-token.dpapi'), credential);
  writeFileSync(join(root, 'config.local.json'), JSON.stringify({moodle_url:'https://school.example', timezone:'UTC'}));
  const request = {protocol:1, run_id:run, collect_state:{tasks:[{source:{url:'https://school.example/mod/quiz/view.php?id=21'}}]}, scope:{site:'https://school.example',
    credential_generation:sha256(credential), course_ids:[1], courses:[{id:'C', moodle_course_ids:[1]}]}};
  const bytes = Buffer.from(JSON.stringify(request));
  const input = join(folder, 'request.json'), output = join(folder, 'snapshot.json');
  writeFileSync(input, bytes); writeFileSync(join(folder,'manifest.json'), JSON.stringify({run_id:run,request_sha256:sha256(bytes)}));
  return {root, input, output, credential, request, cleanup:()=>rmSync(root,{recursive:true,force:true})};
}

test('collection uses fixed course input even if live selection changes, and binds the exact request', () => {
  const f = fixture();
  try {
    writeFileSync(join(f.root,'Study/_System/state.json'), JSON.stringify({courses:[{id:'C',moodle_course_ids:[2]}]}));
    const bundle = loadCollectionRequest(f.root, f.input, f.output);
    assert.deepEqual(bundle.state.courses, [{id:'C',moodle_course_ids:[1]}]);
    writeFileSync(join(f.root,'Study/_System/state.json'), JSON.stringify({courses:[{id:'C',moodle_course_ids:[1]}]}));
    assert.deepEqual(bundle.state.courses, [{id:'C',moodle_course_ids:[1]}]);
    const report = bindCollection(bundle, {collection_complete:true}, 23);
    assert.equal(report.collection_binding.request_sha256, bundle.requestSha);
    assert.equal(report.collection_binding.run_id, f.request.run_id);
    assert.equal(report.account_scope.user_id, 23);
    assert.deepEqual(bundle.credential, f.credential);
    assert.equal(JSON.stringify(report).includes('synthetic encrypted bytes'), false);
  } finally { f.cleanup(); }
});

test('changed encrypted credential or school site refuses the request before networking', () => {
  const f = fixture();
  try {
    writeFileSync(join(f.root,'.private/moodle-token.dpapi'), 'different synthetic bytes');
    assert.throws(()=>loadCollectionRequest(f.root,f.input,f.output), /账户/);
    writeFileSync(join(f.root,'.private/moodle-token.dpapi'), f.credential);
    writeFileSync(join(f.root,'config.local.json'), JSON.stringify({moodle_url:'https://different.example'}));
    assert.throws(()=>loadCollectionRequest(f.root,f.input,f.output), /学校/);
  } finally { f.cleanup(); }
});

test('changed request, foreign path, or existing snapshot cannot be overwritten', () => {
  const f = fixture();
  try {
    const original = JSON.stringify(f.request);
    f.request.scope.course_ids=[2]; writeFileSync(f.input, JSON.stringify(f.request));
    assert.throws(()=>loadCollectionRequest(f.root,f.input,f.output), /请求已变化/);
    writeFileSync(f.input, original);
    assert.throws(()=>loadCollectionRequest(f.root,f.input,join(f.root,'outside.json')));
    writeFileSync(f.output, '{}');
    assert.throws(()=>loadCollectionRequest(f.root,f.input,f.output), /独立目录/);
  } finally { f.cleanup(); }
});

test('loaded request runs the real collector and preserves tracked quizzes without deadlines', async () => {
  const f = fixture();
  try {
    const bundle = loadCollectionRequest(f.root, f.input, f.output);
    // Later live edits must not change this collection's fixed task input.
    writeFileSync(join(f.root,'Study/_System/state.json'), JSON.stringify({courses:[{id:'C',moodle_course_ids:[2]}],
      tasks:[{source:{url:'https://school.example/mod/quiz/view.php?id=22'}}]}));
    const attempts = [], calls = [];
    const client = {baseUrl:'https://school.example',userId:23,call:async (method,args)=>{
      calls.push(method);
      if (method === 'mod_assign_get_assignments') return {courses:[],warnings:[]};
      if (method === 'mod_quiz_get_quizzes_by_courses') {
        assert.equal(args['courseids[0]'],1);
        return {quizzes:[{id:20,course:1,coursemodule:21,timeclose:0},
          {id:21,course:1,coursemodule:22,timeclose:0}, {id:22,course:1,coursemodule:23,timeclose:1}]};
      }
      if (method === 'mod_quiz_get_user_attempts') {attempts.push(args.quizid);return {attempts:[]};}
      if (method === 'core_course_get_contents') {assert.equal(args.courseid,1);return [];}
      if (method === 'core_calendar_get_action_events_by_timesort') return {events:[]};
      throw new Error('Unexpected API call');
    }};
    const result = bindCollection(bundle, await collectRefresh(client,bundle.state), client.userId);
    assert.equal(result.collection_complete,true);
    assert.deepEqual(attempts.sort((a,b)=>a-b),[20,22]);
    assert.deepEqual(result.pages.map(p=>p.courseid),[1]);
    assert.ok(calls.every(name=>name.includes('_get_')));
    assert.equal(result.collection_binding.request_sha256,bundle.requestSha);
  } finally { f.cleanup(); }
});

test('Windows token reader decrypts captured bytes without rereading a credential path',
  {skip: process.platform !== 'win32'}, () => {
    const shell = join(process.env.SystemRoot ?? 'C:/Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe');
    const protectedValue = spawnSync(shell, ['-NoProfile', '-NonInteractive', '-Command',
      "Add-Type -AssemblyName System.Security; [Console]::Out.Write([Convert]::ToBase64String(" +
      "[Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes('synthetic456')," +
      "$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)))"], {encoding:'utf8',windowsHide:true});
    assert.equal(protectedValue.status, 0, 'synthetic encryption must work in the current Windows account');
    assert.equal(decryptWindowsToken(Buffer.from(protectedValue.stdout, 'base64')), 'synthetic456');
    assert.throws(() => decryptWindowsToken(Buffer.from('invalid encrypted bytes')), /connect.cmd/);
  });
