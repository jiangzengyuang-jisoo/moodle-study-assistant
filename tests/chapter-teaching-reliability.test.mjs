import {mkdirSync,mkdtempSync,readFileSync,writeFileSync,unlinkSync,existsSync,rmSync,realpathSync} from 'node:fs';
import {join,dirname,relative,isAbsolute} from 'node:path';
import {createHash} from 'node:crypto';
import {registerChapterCheckTool} from '../tools/moodle/chapter-check-tool.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {tmpdir} from 'node:os';
import {spawn,spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
const parse = p => JSON.parse(readFileSync(p,'utf8'));
const hash = x=>createHash('sha256').update(x).digest('hex');

function fixture(t,label,{twoPages=false}={}){
 const base=realpathSync(tmpdir()),root=mkdtempSync(join(base,label+'-'));
 t.after(()=>{const child=relative(base,realpathSync(root));assert.ok(child&&!child.startsWith('..')&&!isAbsolute(child));rmSync(root,{recursive:true});});
 const statePath=join(root,'Study/_System/state.json');
 const save=(p,x)=>{p=join(root,p);mkdirSync(dirname(p),{recursive:true});writeFileSync(p,JSON.stringify(x));};
 const site='https://school.example/moodle';
 const courses=[{id:'moodle-1',name:'Demo',moodle_course_ids:[101]}];
 const grants={'moodle-1':{moodle_courseid:101,mode:'full',scope_version:1,account_scope:{site,user_id:7}}};
 if(twoPages){courses.push({id:'moodle-2',name:'Tutorials',moodle_course_ids:[102],merged_into:'moodle-1'});grants['moodle-2']={moodle_courseid:102,mode:'full',scope_version:1,account_scope:{site,user_id:7}};}
 save('Study/_System/state.json',{revision:1,courses,settings:{profile_account_scope:{schema:1,site,user_id:7},course_library_authorization:{schema:1,scope_version:1,courses:grants}},materials:[],mistakes:[{id:'keep',answer:'original independent answer'}],teaching_events:[{id:'historic',course_id:'moodle-1',position:{chapter:'Topic 4'},occurred_at:'2026-11-15T12:00:00+08:00'}]});
 save('config.local.json',{moodle_url:site});save('.private/moodle-token.dpapi','SYNTHETIC');
 const pages=new Map([[101,[]],[102,[]]]),payload=new Map(),failed=new Set(),calls=[],downloads=[];
 const add=(id,name,content,page=101)=>{const module={id,name,modname:'resource',uservisible:true,url:`${site}/mod/resource/view.php?id=${id}`,contents:[{type:'file',filename:name,filepath:'/',fileurl:`${site}/webservice/pluginfile.php/1/mod_resource/content/${id}/${name}`,filesize:null,timemodified:1}]};pages.get(page).push(module);payload.set(id,content);return module;};
 add(501,'Topic5-A.txt','Original A');
 const client={baseUrl:site,userId:7,token:'SYNTHETIC',async call(method,args){calls.push(args.courseid);if(method!=='core_course_get_contents')throw Error(method);return [{id:1,name:'Lectures',section:0,modules:pages.get(args.courseid)}];},async transport(url){const id=Number(new URL(url).pathname.split('/').at(-2));downloads.push(id);if(failed.has(id))throw Error('SYNTHETIC failure');return new Response(payload.get(id),{headers:{'content-type':'text/plain'}});}};
 let handle;registerChapterCheckTool({registerTool(_n,_s,h){handle=h;}},{root,loadState:()=>parse(statePath),connect:async()=>client,beforeCheck:async()=>{}});
 const run=async(extra={})=>{const r=await handle({course_id:'moodle-1',section:'Topic 5',chat_id:'review-main-chat',force:false,...extra});return {error:r.isError??false,...JSON.parse(r.content[0].text)};};
 const current=()=>parse(statePath).materials.filter(m=>!m.superseded_by&&!['archived','deleted'].includes(m.version_status));
 const path=m=>join(root,'Study',m.relative_path);
 const editState=fn=>{const s=parse(statePath);fn(s);writeFileSync(statePath,JSON.stringify(s));};
 return {root,statePath,run,pages,payload,failed,calls,downloads,add,current,path,editState,client};
}
test('same-length edited local originals never become successful strict receipts',async t=>{
 const f=fixture(t,'edited');assert.equal((await f.run()).verified.complete,true);
 const m=f.current()[0],before=readFileSync(f.statePath);writeFileSync(f.path(m),'CORRUPTED!');
 const checked=await f.run();assert.equal(checked.verified.complete,false);
 assert.equal(readFileSync(f.path(m),'utf8'),'CORRUPTED!');assert.deepEqual(readFileSync(f.statePath),before);
 const calls=[f.calls.length,f.downloads.length];const followup=await f.run();
 assert.equal(followup.verified.complete,false);assert.deepEqual([f.calls.length,f.downloads.length],calls);
 // A fresh chat and a changed server file still cannot overwrite the annotation.
 f.payload.set(501,'Remote revised');const changed=await f.run({chat_id:'new-chat'});
 assert.equal(changed.verified.complete,false);assert.equal(readFileSync(f.path(m),'utf8'),'CORRUPTED!');
 assert.deepEqual(readFileSync(f.statePath),before);
});
test('a labeled module subset cannot satisfy a later whole-chapter request',async t=>{
 const f=fixture(t,'subset');f.add(503,'Topic5-B.txt','Original B');
 const part=await f.run({module_ids:[501]});assert.equal(part.verified.complete,true);
 assert.deepEqual(part.files.map(x=>x.module_id),[501]);
 const whole=await f.run();assert.equal(whole.verified.complete,true);
 assert.deepEqual(whole.files.map(x=>x.module_id).sort(),[501,503]);
 const calls=[f.calls.length,f.downloads.length];assert.equal((await f.run()).reused,true);
 assert.deepEqual([f.calls.length,f.downloads.length],calls);
});
test('missing originals plus newer or reverted remote bytes preserve old evidence and use new paths',async t=>{
 const f=fixture(t,'missing');await f.run();const first=f.current()[0];
 f.editState(s=>{s.mistakes[0].material_id=first.id;for(const k of ["site","user_id","page_id"])delete s.materials[0].moodle_file_sources[0][k];});
 const note=join(f.root,'Study','note.md'),noteText=`[old lesson](${first.relative_path})\nMy original explanation`;
 writeFileSync(note,noteText);const oldRows=[];
 for(const [i,content] of ['Remote version two','Remote version three','Original A'].entries()){
  const old=f.current()[0];oldRows.push({...old});unlinkSync(f.path(old));f.payload.set(501,content);
  const checked=await f.run({chat_id:'missing-round-'+i});assert.equal(checked.verified.complete,true,JSON.stringify(checked));
  const current=f.current()[0];assert.equal(readFileSync(f.path(current),'utf8'),content);
  for(const original of oldRows){const saved=parse(f.statePath).materials.find(x=>x.id===original.id);
   assert.equal(saved.sha256,original.sha256);assert.equal(saved.relative_path,original.relative_path);
   assert.equal(saved.version_status,'missing');assert.equal(existsSync(f.path(saved)),false);
   assert.notEqual(current.id,original.id);assert.notEqual(current.relative_path,original.relative_path);
  }
  assert.equal(parse(f.statePath).mistakes[0].material_id,first.id);assert.equal(parse(f.statePath).mistakes[0].answer,'original independent answer');
  assert.equal(parse(f.statePath).teaching_events[0].id,'historic');assert.equal(readFileSync(note,'utf8'),noteText);
 }
});
test('partial acquisition failure is not retried by ordinary follow-up but force and new chats can recover',async t=>{
 const f=fixture(t,'partial');f.add(503,'Topic5-B.txt','Original B');f.failed.add(503);
 const first=await f.run();assert.equal(first.verified.complete,false);assert.equal(f.current().length,1);
 const calls=[f.calls.length,f.downloads.length];const second=await f.run();
 assert.equal(second.verified.complete,false);assert.deepEqual([f.calls.length,f.downloads.length],calls);
 f.failed.clear();assert.equal((await f.run({force:true})).verified.complete,true);
 assert.equal(f.current().length,2);assert.equal((await f.run({chat_id:'fresh-chat'})).verified.complete,true);
 assert.equal(parse(f.statePath).mistakes[0].answer,'original independent answer');
});

test('lost extraction index is detected before teaching and local sync restores the actual chapter text',async t=>{
 const f=fixture(t,'index-loss');await f.run();const stateBefore=readFileSync(f.statePath);
 const library=fileURLToPath(new URL('../tools/study/library.py',import.meta.url));
 const invoke=(...args)=>{const p=spawnSync(process.env.STUDY_PYTHON,[library,'--state',f.statePath,...args],{encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'},windowsHide:true});assert.equal(p.status,0,p.stderr);return JSON.parse(p.stdout);};
 unlinkSync(join(f.root,'Study/_System/Library/catalog.json'));
 const before=[f.calls.length,f.downloads.length];assert.equal((await f.run()).verified.complete,true);
 const missing=invoke('chapter-prep','--course','moodle-1','--section','Topic 5','--render','none');
 assert.equal(missing.status,'ready');
 assert.ok(JSON.stringify(missing).includes('Original A'));
 invoke('sync','--course','moodle-1');
 const prepared=invoke('chapter-prep','--course','moodle-1','--section','Topic 5','--render','none');
 assert.equal(prepared.status,'ready');assert.ok(JSON.stringify(prepared).includes('Original A'));
 assert.deepEqual([f.calls.length,f.downloads.length],before);assert.deepEqual(readFileSync(f.statePath),stateBefore);
});
test('a real process interrupted after a committed batch recovers without losing prior learning evidence',async t=>{
 const f=fixture(t,'interrupt');for(let id=502;id<=509;id++)f.add(id,`Topic5-part${id}.txt`,`Original ${id}`);
 const data=join(f.root,'input.json');writeFileSync(data,JSON.stringify({root:f.root,statePath:f.statePath,pages:[...f.pages],payload:[...f.payload]}));
 const childFile=join(f.root,'child.mjs');
 const tool=new URL('../tools/moodle/chapter-check-tool.mjs',import.meta.url).href;
 writeFileSync(childFile,`
 import {readFileSync} from 'node:fs';
 import {registerChapterCheckTool} from ${JSON.stringify(tool)};
 const f=JSON.parse(readFileSync(${JSON.stringify(data)},'utf8')),pages=new Map(f.pages),payload=new Map(f.payload);
 let handle;const client={baseUrl:'https://school.example/moodle',userId:7,token:'SYNTHETIC',
 async call(_m,args){return [{id:1,name:'Lectures',section:0,modules:pages.get(args.courseid)}];},
 async transport(url){const id=Number(new URL(url).pathname.split('/').at(-2));if(id===509){process.stdout.write('BATCH_COMMITTED\\n');await new Promise(()=>setInterval(()=>{},1000));}return new Response(payload.get(id),{headers:{'content-type':'text/plain'}});}};
 registerChapterCheckTool({registerTool(_n,_s,h){handle=h;}},{root:f.root,loadState:()=>JSON.parse(readFileSync(f.statePath,'utf8')),connect:async()=>client,beforeCheck:async()=>{}});
 await handle({course_id:'moodle-1',section:'Topic 5',chat_id:'review-main-chat',force:false});
 `);
 const child=spawn(process.execPath,[childFile],{stdio:['ignore','pipe','pipe'],windowsHide:true,env:process.env});
 const exit=once(child,'exit');t.after(()=>{if(child.exitCode===null)child.kill();});
 await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('child did not reach committed batch')),15000);let output='';child.stdout.on('data',b=>{output+=b;if(output.includes('BATCH_COMMITTED')){clearTimeout(timer);resolve();}});child.on('error',reject);child.on('exit',code=>{clearTimeout(timer);reject(new Error('child exited early '+code));});});
 assert.equal(f.current().length,8);child.kill();await exit;
 const recovered=await f.run({force:true});assert.equal(recovered.verified.complete,true,JSON.stringify(recovered));assert.equal(f.current().length,9);
 const state=parse(f.statePath);assert.equal(state.mistakes[0].answer,'original independent answer');assert.equal(state.teaching_events[0].id,'historic');
 assert.ok(f.current().every(m=>readFileSync(f.path(m),'utf8').startsWith('Original')));
});

test('authorization revoked while downloading a replacement is rechecked before changing missing history',async t=>{
 const f=fixture(t,'revoked-during-download');await f.run();const old=f.current()[0];unlinkSync(f.path(old));f.payload.set(501,'Updated remote');
 const original=f.client.transport;let fetches=0;
 f.client.transport=async(...args)=>{const response=await original(...args);if(++fetches===1)f.editState(s=>s.settings.course_library_authorization.courses['moodle-1'].mode='info_only');return response;};
 const checked=await f.run({chat_id:'new-chat'});assert.equal(checked.verified.complete,false);
 assert.equal(parse(f.statePath).materials.length,1);assert.equal(f.current()[0].sha256,old.sha256);assert.equal(f.current()[0].relative_path,old.relative_path);
 assert.equal(existsSync(f.path(old)),false);assert.equal(parse(f.statePath).mistakes[0].answer,'original independent answer');
});
test('an existing file updated before a new file fails keeps the failed receipt until explicit retry',async t=>{
 const f=fixture(t,'updated-partial');assert.equal((await f.run()).verified.complete,true);
 const old=f.current()[0];f.editState(s=>s.mistakes[0].material_id=old.id);
 f.payload.set(501,'Updated original A');f.pages.get(101)[0].contents[0].timemodified=2;f.add(503,'Topic5-B.txt','Original B');f.failed.add(503);
 const first=await f.run({chat_id:'updated-partial-chat'});
 assert.equal(first.verified.complete,false);assert.equal(first.acquisition.status,'incomplete');
 const updated=f.current()[0];assert.notEqual(updated.id,old.id);
 assert.equal(readFileSync(f.path(updated),'utf8'),'Updated original A');
 const archived=parse(f.statePath).materials.find(m=>m.id===old.id);
 assert.equal(readFileSync(f.path(archived),'utf8'),'Original A');
 const calls=[f.calls.length,f.downloads.length],stateBefore=readFileSync(f.statePath);
 const second=await f.run({chat_id:'updated-partial-chat'});
 assert.equal(second.verified.complete,false);assert.equal(second.reused,true);
 assert.deepEqual([f.calls.length,f.downloads.length],calls);assert.deepEqual(readFileSync(f.statePath),stateBefore);
 f.failed.clear();assert.equal((await f.run({chat_id:'updated-partial-chat',force:true})).verified.complete,true);
 assert.equal(f.current().length,2);assert.ok(f.downloads.length>calls[1]);
 assert.equal(parse(f.statePath).mistakes[0].material_id,old.id);
 assert.equal(parse(f.statePath).mistakes[0].answer,'original independent answer');
 assert.equal(parse(f.statePath).teaching_events[0].id,'historic');
});

test('a failed update receipt cannot hide later local edits or revoked page authorization',async t=>{
 const f=fixture(t,'updated-partial-guards');await f.run();
 f.payload.set(501,'Updated original A');f.add(503,'Topic5-B.txt','Original B');f.failed.add(503);
 const first=await f.run({chat_id:'updated-partial-guards'});
 assert.equal(first.verified.complete,false);assert.equal(first.acquisition.status,'incomplete');
 const current=f.current()[0],stateBefore=readFileSync(f.statePath);
 writeFileSync(f.path(current),'My annotated copy!');
 const edited=await f.run({chat_id:'updated-partial-guards'});
 assert.equal(edited.verified.complete,false);assert.notEqual(edited.reused,true);
 assert.equal(edited.files.find(row=>row.module_id===501).local.status,'modified');
 assert.equal(readFileSync(f.path(current),'utf8'),'My annotated copy!');
 assert.deepEqual(readFileSync(f.statePath),stateBefore);
 const forced=await f.run({chat_id:'updated-partial-guards',force:true});
 assert.equal(forced.verified.complete,false);assert.equal(readFileSync(f.path(current),'utf8'),'My annotated copy!');
 f.editState(s=>s.settings.course_library_authorization.courses['moodle-1'].mode='info_only');
 const downloads=f.downloads.length,revoked=await f.run({chat_id:'updated-partial-guards'});
 assert.notEqual(revoked.verified?.complete,true);assert.equal(revoked.error,'CHAPTER_AUTHORIZATION_REQUIRED');assert.notEqual(revoked.reused,true);
 assert.equal(f.downloads.length,downloads);assert.equal(readFileSync(f.path(current),'utf8'),'My annotated copy!');
 assert.equal(parse(f.statePath).mistakes[0].answer,'original independent answer');
});
