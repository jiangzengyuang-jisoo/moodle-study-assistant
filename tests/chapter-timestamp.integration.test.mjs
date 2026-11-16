import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync, mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync, realpathSync} from 'node:fs';
import {join, resolve, relative, isAbsolute} from 'node:path';
import {spawnSync} from 'node:child_process';
import {runChapterCommand} from '../tools/moodle/chapter-entry.mjs';
import {courseCatalog, selectCoursesInChat} from '../tools/moodle/course-selection.mjs';
import {python as choosePython} from '../tools/moodle/state-bridge.mjs';

function fixture(t) {
  const root = mkdtempSync(join(import.meta.dirname, '.timestamps-'));
  t.after(() => {const child = relative(realpathSync(import.meta.dirname), realpathSync(root));
    assert.ok(child && !child.startsWith('..') && !isAbsolute(child)); rmSync(root, {recursive:true});});
  const studyRoot = join(root, 'Study'), statePath = join(studyRoot, '_System/state.json');
  mkdirSync(join(studyRoot, '_System'), {recursive:true});
  const read = () => JSON.parse(readFileSync(statePath, 'utf8'));
  const save = x => writeFileSync(statePath, JSON.stringify(x));
  save({revision:1,courses:[],materials:[],settings:{},mistakes:[{id:'M',user_answer:'historical answer'}]});
  let site='https://school.example', userId=7, calls=0, downloads=0, response=null;
  const modules=[{id:100,name:'Chapter 8',modname:'resource',contents:[{type:'file',filename:'Chapter 8.txt',
    filepath:'/',timemodified:100,fileurl:site+'/pluginfile.php/77/mod_resource/content/1/Chapter%208.txt'}]}];
  let body='Original lesson';
  const client=()=>({baseUrl:site,userId,token:'synthetic',async call(method){calls++;
    if(method==='core_enrol_get_users_courses')return [{id:10,fullname:'Demo',shortname:'D'}];
    return [{id:1,name:'Lectures',modules}];},async transport(){downloads++;
      return response ? response() : new Response(body,{headers:{'content-type':'text/plain'}});}});
  const paths={studyRoot,statePath,studyToolsDir:resolve(import.meta.dirname,'../tools/study'),python:choosePython(resolve(import.meta.dirname,'..'))};
  const runPython=(script,args=[])=>{const r=spawnSync(paths.python,['-X','utf8','-B','-c',
    'import sys,json;sys.path.insert(0,sys.argv[1]);'+script,paths.studyToolsDir,statePath,...args],{encoding:'utf8',windowsHide:true});
    assert.equal(r.status,0,r.stderr);return JSON.parse(r.stdout);};
  return {read,save,paths,modules,runPython,counts:()=>({calls,downloads}),
    setBody:x=>body=x,setResponse:x=>response=x,switchAccount:()=>{site='https://other.example';userId=8;},
    async select(){const deps={client:client(),readState:read,writeState:(_old,next)=>save(next)};
      return selectCoursesInChat({...deps,selection:{...await courseCatalog(deps),course_ids:[10],mode:'full'}});},
    async run(chat='one',extra=[]){let output;const exit=await runChapterCommand(['--ensure','--chat',chat,'--course','moodle-10','--chapter','8',...extra],
      {paths,environment:{},connectionIdentity:()=>({site,credentialKey:site}),createClient:async()=>client(),output:x=>output=x});
      return {exit,...output};}};
}

test('normal selection, acquisition, full reading, teaching save, same/new chat, and timestamp replacement',async t=>{
  const f=fixture(t);
  assert.match((await f.select()).next,/study.cmd refresh/);
  const first=await f.run();assert.equal(first.exit,0,JSON.stringify(first));assert.equal(f.counts().downloads,1);
  const old=f.read().materials[0];
  const read=f.runPython('from library import Library;print(json.dumps(Library(sys.argv[2]).read(sys.argv[3])))',[old.id]);
  assert.equal(read.pages[0].text,'Original lesson');
  const event={id:'explanation-1',expected_revision:f.read().revision,occurred_at:'2026-01-01T00:00:00Z',
    event_type:'explanation',affects_current:true,source:{kind:'chat',thread_id:'one',turn_ids:['turn-1']},
    position:{topic:'Chapter 8',chapter:'8',stage:'awaiting_independent_attempt',next_step:'Try a new question',material_id:old.id,pages:[1]}};
  const saved=f.runPython('from teaching_progress import save_teaching;print(json.dumps(save_teaching(sys.argv[2],"moodle-10",json.loads(sys.argv[3]))))',[JSON.stringify(event)]);
  assert.equal(saved.readback_verified,true);assert.equal(saved.mastery_updated,false);
  const before=f.counts();assert.equal((await f.run()).reused,true);assert.deepEqual(f.counts(),before);
  assert.equal((await f.run('two')).exit,0);assert.equal(f.counts().downloads,1,'new chat checks cloud directory, not identical-time bytes');
  f.setBody('Different body without timestamp change');
  assert.equal((await f.run('three')).exit,0);assert.equal(f.counts().downloads,1,'same timestamp intentionally never redownloads');
  f.modules[0].contents[0].timemodified=101;
  const updated=await f.run('four');assert.equal(updated.exit,0,JSON.stringify(updated));assert.equal(f.counts().downloads,2);
  const state=f.read(),historical=state.materials.find(m=>m.id===old.id),current=state.materials.find(m=>m.id===historical.superseded_by);
  assert.equal(historical.version_status,'deleted');assert.equal(existsSync(join(f.paths.studyRoot,historical.relative_path)),false);
  assert.equal(readFileSync(join(f.paths.studyRoot,current.relative_path),'utf8'),'Different body without timestamp change');
  assert.deepEqual(state.mistakes,[{id:'M',user_answer:'historical answer'}]);
  assert.equal(state.courses[0].teaching_events[0].position.material_id,old.id);
  assert.equal((await f.run('four')).reused,true);
});

test('unknown timestamp, empty matching resource, auth JSON and failed indexing pause without destroying originals',async t=>{
  const f=fixture(t);await f.select();assert.equal((await f.run()).exit,0);
  const old=f.read().materials[0];
  f.modules[0].contents[0].timemodified=null;
  assert.equal((await f.run('unknown')).reason,'remote_timestamp_unknown');assert.equal(f.counts().downloads,1);
  f.modules[0].contents[0].timecreated=101;
  f.modules.push({id:101,name:'Chapter 8 Part B',modname:'resource',contents:[]});
  assert.equal((await f.run('gap')).reason,'chapter_source_gaps');assert.equal(f.counts().downloads,1);
  f.modules.pop();f.setResponse(()=>new Response(JSON.stringify({errorcode:'invalidtoken',error:'Invalid token'}),{headers:{'content-type':'application/json'}}));
  const failed=await f.run('auth');assert.equal(failed.ready_for_teaching,false);assert.equal(failed.teaching_paused,true);
  assert.equal(readFileSync(join(f.paths.studyRoot,old.relative_path),'utf8'),'Original lesson');
  const count=f.counts();await f.run('auth');assert.deepEqual(f.counts(),count);
  f.setResponse(()=>new Response('   ',{headers:{'content-type':'text/plain'}}));
  const empty=await f.run('unreadable');assert.equal(empty.ready_for_teaching,false);
  const previous=f.read().materials.find(m=>m.id===old.id);
  assert.equal(existsSync(join(f.paths.studyRoot,previous.relative_path)),true,'unreadable replacement must preserve old bytes');
});

test('fallback creation time, local annotation and account isolation preserve the selected source',async t=>{
  const f=fixture(t);delete f.modules[0].contents[0].timemodified;f.modules[0].contents[0].timecreated=100;
  await f.select();assert.equal((await f.run()).exit,0);const old=f.read().materials[0];
  assert.equal((await f.run('two')).exit,0);assert.equal(f.counts().downloads,1);
  writeFileSync(join(f.paths.studyRoot,old.relative_path),'MY ANNOTATION');f.modules[0].contents[0].timecreated=101;
  assert.equal((await f.run('three')).reason,'local_material_missing_or_modified');assert.equal(f.counts().downloads,1);
  const before=readFileSync(f.paths.statePath);f.switchAccount();await assert.rejects(f.select(),/学校或账号/);
  assert.deepEqual(readFileSync(f.paths.statePath),before);assert.equal(readFileSync(join(f.paths.studyRoot,old.relative_path),'utf8'),'MY ANNOTATION');
});

test('generic folders cannot hide related gaps and a removed registered chapter part blocks readiness',async t=>{
  const f=fixture(t);await f.select();
  f.modules[0].name='Lecture folder';f.modules[0].modname='folder';
  f.modules[0].contents.push({type:'file',filename:'Chapter 8: Part B.txt',filepath:'/',timemodified:100,
    fileurl:'https://school.example/pluginfile.php/77/mod_folder/content/1/b.txt'});
  assert.equal((await f.run('gap')).reason,'chapter_source_gaps');assert.equal(f.counts().downloads,0);
  f.modules[0].contents.pop();assert.equal((await f.run('first')).exit,0);
  const old=f.read().materials[0];f.modules.splice(0);
  assert.equal((await f.run('removed')).reason,'source_missing_or_renamed');
  assert.equal(existsSync(join(f.paths.studyRoot,old.relative_path)),true);
});

test('legacy missing local timestamp is established once without inventing equality',async t=>{
  const f=fixture(t);await f.select();assert.equal((await f.run()).exit,0);
  const state=f.read(),id=state.materials[0].id;
  delete state.materials[0].moodle_file_sources[0].timemodified;delete state.materials[0].moodle_file_sources[0].timecreated;f.save(state);
  assert.equal((await f.run('baseline')).exit,0);assert.equal(f.counts().downloads,2);
  assert.equal(f.read().materials[0].id,id);assert.equal((await f.run('later')).exit,0);assert.equal(f.counts().downloads,2);
});

test('an unreadable replacement can recover at the same remote timestamp after explicit retry',async t=>{
  const f=fixture(t);await f.select();assert.equal((await f.run()).exit,0);
  const original=f.read().materials[0];f.modules[0].contents[0].timemodified=101;f.setBody('   ');
  assert.equal((await f.run('update')).ready_for_teaching,false);
  const bad=f.read().materials.find(m=>!m.superseded_by);
  assert.equal(bad.moodle_file_sources[0].timemodified,undefined,'failed indexing cannot commit the new time baseline');
  f.setBody('Recovered readable original');
  const count=f.counts();assert.equal((await f.run('update')).ready_for_teaching,false);assert.deepEqual(f.counts(),count);
  assert.equal((await f.run('update',['--force'])).exit,0,'same remote time retries an uncommitted failed baseline');
  assert.equal(f.counts().downloads,count.downloads+1);
  const current=f.read().materials.find(m=>!m.superseded_by);
  assert.equal(current.moodle_file_sources[0].timemodified,101);
  assert.equal(readFileSync(join(f.paths.studyRoot,current.relative_path),'utf8'),'Recovered readable original');
  const old=f.read().materials.find(m=>m.id===original.id);
  assert.equal(old.version_status,'deleted');
  assert.equal(existsSync(join(f.paths.studyRoot,old.relative_path)),false,'readable recovery removes original A through failed B');
  const failed=f.read().materials.find(m=>m.id===bad.id);
  assert.equal(failed.version_status,'deleted');
  assert.equal(existsSync(join(f.paths.studyRoot,failed.relative_path)),false);
});
