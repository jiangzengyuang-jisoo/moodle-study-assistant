import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync,mkdtempSync,readFileSync,writeFileSync,unlinkSync,existsSync,rmSync,realpathSync} from 'node:fs';
import {join,dirname,relative,isAbsolute} from 'node:path';
import {tmpdir} from 'node:os';
import {registerChapterCheckTool} from '../tools/moodle/chapter-check-tool.mjs';
const read=p=>JSON.parse(readFileSync(p,'utf8'));
function fixture(t){
 const base=realpathSync(tmpdir()),root=mkdtempSync(join(base,'timestamp-'));
 t.after(()=>{const rel=relative(base,realpathSync(root));assert.ok(rel&&!rel.startsWith('..')&&!isAbsolute(rel));rmSync(root,{recursive:true});});
 const statePath=join(root,'Study/_System/state.json'),site='https://school.example/moodle';
 const save=(name,value)=>{const p=join(root,name);mkdirSync(dirname(p),{recursive:true});writeFileSync(p,JSON.stringify(value));};
 save('Study/_System/state.json',{revision:1,courses:[{id:'moodle-1',moodle_course_ids:[101]}],materials:[],
 settings:{profile_account_scope:{schema:1,site,user_id:7},course_library_authorization:{schema:1,scope_version:1,courses:{'moodle-1':{moodle_courseid:101,mode:'full',scope_version:1,account_scope:{site,user_id:7}}}}},
 mistakes:[{id:'original-answer',answer:'Original user reasoning'}]});
 save('config.local.json',{moodle_url:site});save('.private/moodle-token.dpapi','SYNTHETIC');
 const modules=[],bytes=new Map(),downloads=[],calls=[];let fail=false;
 const add=(id,name,body)=>{const file={type:'file',filename:name,filepath:'/',timemodified:10,
 fileurl:`${site}/webservice/pluginfile.php/1/mod_resource/content/${id}/${name}`};
 const module={id,name,modname:'resource',contents:[file],uservisible:true};modules.push(module);bytes.set(name,body);return file;};
 const file=add(501,'Topic5-A.txt','Readable original body');
 const client={baseUrl:site,userId:7,token:'SYNTHETIC',async call(method,args){calls.push(args.courseid);assert.equal(method,'core_course_get_contents');return [{id:1,name:'Lectures',section:0,modules}];},
 async transport(url){const name=decodeURIComponent(new URL(url).pathname.split('/').at(-1));downloads.push(name);if(fail)throw Error('offline');return new Response(bytes.get(name),{headers:{'content-type':'text/plain'}});}};
 let handle;registerChapterCheckTool({registerTool(_name,_schema,h){handle=h;}},{root,loadState:()=>read(statePath),connect:async()=>client,beforeCheck:async()=>{}});
 const run=async(chat='main',extra={})=>JSON.parse((await handle({course_id:'moodle-1',section:'Topic 5',chat_id:chat,force:false,...extra})).content[0].text);
 const current=()=>read(statePath).materials.filter(m=>!m.superseded_by&&!['archived','deleted'].includes(m.version_status));
 return {root,statePath,site,file,modules,bytes,downloads,calls,add,run,current,path:m=>join(root,'Study',m.relative_path),fail:value=>fail=value};
}
test('default MCP timestamps: first acquisition, offline reuse, new chat catalog only, changed readable replacement retires only its old bytes',async t=>{
 const f=fixture(t);let result=await f.run();assert.equal(result.mode,'timestamp');assert.equal(result.verified.complete,true);assert.equal(f.downloads.length,1);
 const old=f.current()[0],saved=read(f.statePath).mistakes;const counts=[f.calls.length,f.downloads.length];
 assert.equal((await f.run()).reused,true);assert.deepEqual([f.calls.length,f.downloads.length],counts);
 assert.equal((await f.run('new-chat')).verified.complete,true);assert.equal(f.calls.length,counts[0]+1);assert.equal(f.downloads.length,counts[1]);
 f.file.timemodified=11;f.bytes.set(f.file.filename,'Readable revised body');
 result=await f.run('changed');assert.equal(result.verified.complete,true,JSON.stringify(result));assert.equal(f.downloads.length,2);
 const history=read(f.statePath).materials.find(m=>m.id===old.id);assert.equal(history.version_status,'deleted');assert.ok(history.deleted_at&&history.archived_at);assert.equal(existsSync(f.path(history)),false);
 assert.equal(history.sha256,old.sha256);assert.equal(history.superseded_by,f.current()[0].id);assert.deepEqual(read(f.statePath).mistakes,saved);
 assert.equal((await f.run('changed')).verified.complete,true);
});
test('creation-time fallback and missing baseline establish real timestamps; unknown remote times pause without downloading',async t=>{
 const f=fixture(t);delete f.file.timemodified;f.file.timecreated=5;
 assert.equal((await f.run()).verified.complete,true);const n=f.downloads.length;
 assert.equal((await f.run('creation-unchanged')).verified.complete,true);assert.equal(f.downloads.length,n);
 const ledger=join(f.root,'Study/_System/LibrarySync/ledger.json'),data=read(ledger);
 for(const e of Object.values(data.entries)){delete e.remote?.timemodified;delete e.remote?.timecreated;}writeFileSync(ledger,JSON.stringify(data));
 assert.equal((await f.run('establish')).verified.complete,true);assert.equal(f.downloads.length,n+1);
 delete f.file.timecreated;const before=f.downloads.length;assert.equal((await f.run('unknown')).verified.complete,false);assert.equal(f.downloads.length,before);
});
test('local edits, failed transfers, unreadable replacements and empty resource gaps never authorize teaching or delete originals',async t=>{
 const f=fixture(t);await f.run();const original=f.current()[0];
 const existing=read(f.statePath),historical={...original,id:'pre-policy-history',relative_path:'moodle-1/_Archive/pre-policy/Topic5-A.txt',version_status:'archived',superseded_by:original.id};
 delete historical.chapter_timestamp_update;existing.materials.find(m=>m.id===original.id).supersedes=historical.id;existing.materials.push(historical);
 mkdirSync(dirname(f.path(historical)),{recursive:true});writeFileSync(f.path(historical),'Readable original body');writeFileSync(f.statePath,JSON.stringify(existing));
 f.file.timemodified=12;f.fail(true);assert.equal((await f.run('transfer-failed')).verified.complete,false);assert.equal(existsSync(f.path(original)),true);
 f.fail(false);f.bytes.set(f.file.filename,'   ');const unreadable=await f.run('unreadable');assert.equal(unreadable.verified.complete,false);
 const old=read(f.statePath).materials.find(m=>m.id===original.id);assert.equal(old.version_status,'archived');assert.equal(readFileSync(f.path(old),'utf8'),'Readable original body');
 const broken=f.current()[0];
 const count=f.downloads.length;assert.equal((await f.run('unreadable')).verified.complete,false);assert.equal(f.downloads.length,count);
 // A repaired server body with the same time must be fetched on explicit retry.
 f.bytes.set(f.file.filename,'Repaired readable body without a timestamp bump');
 const recovered=await f.run('unreadable',{force:true});assert.equal(recovered.verified.complete,true,JSON.stringify(recovered));
 assert.equal(f.downloads.length,count+1);assert.equal(readFileSync(f.path(f.current()[0]),'utf8'),'Repaired readable body without a timestamp bump');
 for(const prior of [old,broken]){const row=read(f.statePath).materials.find(m=>m.id===prior.id);assert.equal(row.version_status,'deleted');assert.equal(existsSync(f.path(row)),false);assert.equal(row.deleted_for_material_id,f.current()[0].id);}
 assert.equal(read(f.statePath).materials.find(m=>m.id===historical.id).version_status,'archived');assert.equal(existsSync(f.path(historical)),true);
 const g=fixture(t);await g.run();const edited=g.current()[0];writeFileSync(g.path(edited),'My annotated original');g.file.timemodified=20;
 assert.equal((await g.run('edited')).verified.complete,false);assert.equal(readFileSync(g.path(edited),'utf8'),'My annotated original');
 const h=fixture(t);h.modules.push({id:502,name:'Topic5-B.txt',modname:'resource',contents:[],uservisible:true});
 const gap=await h.run();assert.equal(gap.verified.complete,false);assert.ok(gap.skipped.some(s=>s.module_id===502));
});
test('lost text index repairs locally, and unchanged sibling in a changed folder is not downloaded',async t=>{
 const f=fixture(t);const b=f.add(501,'Topic5-B.txt','Readable B');f.modules[0].contents.push(b);f.modules.pop();await f.run();
 const n=f.downloads.length;unlinkSync(join(f.root,'Study/_System/Library/catalog.json'));
 const cached=await f.run();assert.equal(cached.verified.complete,true);assert.equal(f.downloads.length,n);assert.ok(read(join(f.root,'Study/_System/Library/catalog.json')).materials.length);
 b.timemodified=11;f.bytes.set(b.filename,'Revised B');assert.equal((await f.run('sibling')).verified.complete,true);
 assert.deepEqual(f.downloads.slice(n),['Topic5-B.txt']);
});
