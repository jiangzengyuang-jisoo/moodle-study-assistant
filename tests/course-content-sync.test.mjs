import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync, unlinkSync, readdirSync, realpathSync} from 'node:fs';
import {join, relative, isAbsolute} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {enumerateCourseContent, syncCourseContent} from '../tools/moodle/course-content-sync.mjs';
import {grantCourseContent} from '../tools/moodle/content-policy.mjs';
import {collectRefresh} from '../tools/moodle/refresh.mjs';
import {ReadOnlyClient} from '../tools/moodle/core.mjs';

const site = 'https://moodle.example.edu';
const file = (id=10, name='chapter.txt', modified=1) => ({type:'file', filename:name, filepath:'/',
  fileurl:`${site}/pluginfile.php/${id}/mod_resource/content/0/${name}?token=SECRET`, filesize:5, timemodified:modified});
const module = (id=10, contents=[file(id)]) => ({id,instance:id,modname:'resource',name:'Lecture notes',url:`${site}/mod/resource/view.php?id=${id}`,contents});
function fixture(t, {full=true, sections=[{id:1,modules:[module()]}]}={}) {
  const temporaryRoot = fileURLToPath(new URL('../tmp/', import.meta.url));
  mkdirSync(temporaryRoot, {recursive:true});
  const root=mkdtempSync(join(temporaryRoot,'content-sync-')); t.after(()=>{
    const child=relative(realpathSync(temporaryRoot),realpathSync(root));
    assert.ok(child && !child.startsWith('..') && !isAbsolute(child));
    rmSync(root,{recursive:true,force:true});
  });
  mkdirSync(join(root,'_System'),{recursive:true});
  const statePath=join(root,'_System/state.json');
  let state={courses:[{id:'SYN',moodle_course_ids:[1]}],settings:{},materials:[]};
  if(full) state=grantCourseContent(state,{site,userId:7,courseIds:[1],mode:'full'});
  const write=s=>writeFileSync(statePath,JSON.stringify(s)); write(state);
  let requests=0,installed=0,indexed=0;
  const client={baseUrl:site,userId:7,token:'SECRET',call:async(method,args)=>{
    assert.equal(method,'core_course_get_contents');assert.equal(args.courseid,1);return sections;
  },transport:async(url)=>{requests++;assert.equal(new URL(url).searchParams.get('token'),'SECRET');return new Response('hello');}};
  const options={studyRoot:root,statePath,studyToolsDir:root,python:'unused',snapshot:{collection_complete:true},
    install:async({source,stagedPath})=>{
      installed++; const bytes=readFileSync(stagedPath),sha256=createHash('sha256').update(bytes).digest('hex');
      const relative_path=`SYN/${source.module_id}-${source.filename}`;
      mkdirSync(join(root,'SYN'),{recursive:true});writeFileSync(join(root,relative_path),bytes);
      const s=JSON.parse(readFileSync(statePath));const id=`m-${source.module_id}-${source.filename}`;
      s.materials=s.materials.filter(m=>m.id!==id);s.materials.push({id,course_id:'SYN',relative_path,sha256});write(s);
      return {status:'installed',material_id:id,relative_path,sha256,bytes:bytes.length};
    },indexCourse:async()=>{indexed++;return {materials:JSON.parse(readFileSync(statePath)).materials.map(m=>({...m,status:'indexed',text_units:1}))};}};
  return {root,statePath,state,write,client,options,stats:()=>({requests,installed,indexed})};
}
test('legacy selection never implies bulk download consent',async t=>{
  const f=fixture(t,{full:false}); const r=await syncCourseContent(f.client,f.state,f.options);
  assert.equal(r.status,'not_authorized');assert.equal(f.stats().requests,0);assert.equal(existsSync(join(f.root,'_System/CourseContent')),false);
});
test('full initialization installs, indexes, checks local hashes and reuses unchanged files',async t=>{
  const f=fixture(t);const a=await syncCourseContent(f.client,f.state,f.options);
  assert.equal(a.status,'complete');assert.equal(a.counts.downloaded,1);assert.equal(a.reviewed,false);
  const b=await syncCourseContent(f.client,f.state,f.options);
  assert.equal(b.files[0].status,'unchanged');assert.deepEqual(f.stats(),{requests:1,installed:1,indexed:2});
  assert.doesNotMatch(readFileSync(join(f.root,'_System/CourseContent/latest.json'),'utf8'),/SECRET/);
});
test('changed remote metadata downloads again; unavailable stamps cannot hide replacements',async t=>{
  const sections=[{id:1,modules:[module()]}];const f=fixture(t,{sections});
  await syncCourseContent(f.client,f.state,f.options);sections[0].modules[0].contents[0].timemodified=2;
  await syncCourseContent(f.client,f.state,f.options);assert.equal(f.stats().requests,2);
  delete sections[0].modules[0].contents[0].timemodified;
  await syncCourseContent(f.client,f.state,f.options);await syncCourseContent(f.client,f.state,f.options);
  assert.equal(f.stats().requests,4);
});
test('modified local source is not mistaken for verified cache',async t=>{
  const f=fixture(t);await syncCourseContent(f.client,f.state,f.options);
  writeFileSync(join(f.root,'SYN/10-chapter.txt'),'local change');
  // Real installer preserves conflicts; fake deliberately refuses the changed source.
  f.options.install=async()=>{throw new Error('local conflict');};
  const r=await syncCourseContent(f.client,f.state,f.options);
  assert.equal(r.status,'partial');assert.equal(readFileSync(join(f.root,'SYN/10-chapter.txt'),'utf8'),'local change');
});
test('download failures are resumable and do not expose credential-bearing errors',async t=>{
  const f=fixture(t);const transport=f.client.transport;
  f.client.transport=async()=>{throw new Error('https://example/?token=SECRET');};
  const r=await syncCourseContent(f.client,f.state,f.options);assert.equal(r.status,'partial');assert.doesNotMatch(JSON.stringify(r),/SECRET/);
  f.client.transport=transport;assert.equal((await syncCourseContent(f.client,f.state,f.options)).status,'complete');
});
test('revocation during enumeration stops all file transfers',async t=>{
  const f=fixture(t);f.client.call=async()=>{f.write(grantCourseContent(f.state,{site,userId:7,courseIds:[1],mode:'metadata'}));return [{id:1,modules:[module()]}];};
  const r=await syncCourseContent(f.client,f.state,f.options);assert.equal(r.status,'partial');assert.equal(f.stats().requests,0);
});
test('different live account cannot use caller identity or old authorization',async t=>{
  const f=fixture(t);f.client.userId=8;
  assert.equal((await syncCourseContent(f.client,f.state,f.options)).status,'not_authorized');
  await assert.rejects(syncCourseContent(f.client,f.state,{...f.options,userId:7}),/account_scope/);
});
test('full scope has no legacy twenty-module limit',async t=>{
  const f=fixture(t,{sections:[{id:1,modules:Array.from({length:25},(_,i)=>module(i+1))}]});
  const r=await syncCourseContent(f.client,f.state,f.options);assert.equal(r.counts.downloaded,25);
});
test('source enumeration includes folder files, body images, assignment files and all forum authors',()=>{
  const forum={id:30,instance:40,modname:'forum',name:'Discussion',url:`${site}/mod/forum/view.php?id=30`};
  const assignment={id:20,modname:'assign',name:'HW',url:`${site}/mod/assign/view.php?id=20`};
  const result=enumerateCourseContent([{id:1,summary:'Overview',modules:[module(),assignment,forum]}],{pageId:1,courseId:'SYN',site,
    snapshot:{assignments:{courses:[{id:1,assignments:[{cmid:20,intro:'Homework',introattachments:[file(20,'work.pdf')]}]}]},
      forums:[{courseid:1,module:forum,discussions:[{discussion:44}]}],threads:[{forumid:40,discussion:{discussion:44},posts:[{id:99,author:{fullname:'Student'},message:`<img src="${file(40,'diagram.png').fileurl}">question`,attachments:[file(40,'question.txt')]}]}]}});
  assert.equal(result.files.length,4);assert.equal(result.documents.length,3);assert.equal(result.files.find(f=>f.filename==='diagram.png').post_id,99);
  assert.match(result.documents.find(d=>d.post_id===99).module_url,/#p99$/);
});
test('external links and unexposed pages remain explicit gaps, never spidered',async t=>{
  const f=fixture(t,{sections:[{id:1,modules:[module(10,[{...file(),fileurl:'https://external.example/chapter.txt'}]),{id:20,modname:'page',name:'Page'}]}]});
  const r=await syncCourseContent(f.client,f.state,f.options);assert.equal(r.status,'partial');assert.equal(f.stats().requests,0);assert.equal(r.skipped.length,2);
});
test('not indexed or index failure does not undo downloaded files',async t=>{
  const f=fixture(t);f.options.indexCourse=async()=>({materials:[{id:'m',status:'not_text_indexed',text_units:0}]});
  let r=await syncCourseContent(f.client,f.state,f.options);assert.equal(r.status,'partial');assert.equal(r.files[0].status,'downloaded');
  f.options.indexCourse=async()=>{throw new Error('parser unavailable');};r=await syncCourseContent(f.client,f.state,f.options);
  assert.equal(r.status,'partial');assert.equal(r.files[0].status,'unchanged');assert.equal(f.stats().requests,1);
});
test('size mismatch and login HTML never enter material registry',async t=>{
  const f=fixture(t);f.client.transport=async()=>new Response('login',{headers:{'content-type':'text/html'}});
  // .txt may legitimately be HTML; explicit login form is still blocked.
  f.client.transport=async()=>new Response('<form id="loginform">',{headers:{'content-type':'text/html'}});
  const r=await syncCourseContent(f.client,f.state,f.options);assert.equal(r.status,'partial');assert.equal(f.stats().installed,0);
});

test('actual refresh snapshot archives the first post and replies with attachments from every author', async t => {
  const forum={id:30,instance:40,modname:'forum',name:'Discussion',url:`${site}/mod/forum/view.php?id=30`,uservisible:true};
  const sections=[{id:1,modules:[forum]}], f=fixture(t,{sections});
  const calls=[];
  f.client.call=async(method,args)=>{
    calls.push(method);
    if(method==='core_course_get_contents') return sections;
    if(method==='mod_assign_get_assignments') return {courses:[],warnings:[]};
    if(method==='mod_quiz_get_quizzes_by_courses') return {quizzes:[],warnings:[]};
    if(method==='core_calendar_get_action_events_by_timesort') return {events:[],warnings:[]};
    if(method==='mod_forum_get_forum_discussions') return {discussions:[{discussion:44,numreplies:1}],warnings:[]};
    if(method==='mod_forum_get_discussion_posts') return {posts:[
      {id:99,author:{fullname:'Teacher'},message:'Teacher body',attachments:[file(40,'teacher.txt')]},
      {id:100,author:{fullname:'Student'},message:'Student reply',attachments:[file(40,'student.txt')]},
    ],warnings:[]};
    assert.fail(`Unexpected read/write method ${method}`);
  };
  const snapshot=await collectRefresh(f.client,{...f.state,tasks:[]});
  assert.ok(Array.isArray(snapshot.threads[0].posts));
  const result=await syncCourseContent(f.client,f.state,{...f.options,snapshot});
  assert.equal(result.status,'complete');
  assert.deepEqual(result.files.map(row=>row.filename),['teacher.txt','student.txt']);
  assert.deepEqual(result.documents.map(row=>row.post_id),[99,100]);
  assert.ok(calls.every(name=>name.includes('_get_')));
});

test('a visible forum with missing snapshot or missing thread cannot be reported complete', async t => {
  const forum={id:30,instance:40,modname:'forum',name:'Discussion'};
  const f=fixture(t,{sections:[{id:1,modules:[forum]}]});
  let result=await syncCourseContent(f.client,f.state,f.options);
  assert.equal(result.status,'partial');
  assert.ok(result.skipped.some(row=>row.reason==='forum_snapshot_unavailable'));
  result=await syncCourseContent(f.client,f.state,{...f.options,snapshot:{collection_complete:true,
    forums:[{courseid:1,module:forum,discussions:[{discussion:44}]}],threads:[]}});
  assert.equal(result.status,'partial');
  assert.ok(result.skipped.some(row=>row.reason==='forum_thread_missing'));
});

test('newly hidden forum and quiz never use a previous visible snapshot to download their content', () => {
  const forum={id:30,instance:40,modname:'forum',name:'Discussion',uservisible:false};
  const quiz={id:31,modname:'quiz',name:'Quiz',uservisible:false};
  const result=enumerateCourseContent([{id:1,modules:[forum,quiz]}],{pageId:1,courseId:'SYN',site,
    snapshot:{forums:[{courseid:1,module:{...forum,uservisible:true},discussions:[{discussion:44}]}],
      threads:[{forumid:40,discussion:{discussion:44},posts:[{id:99,message:'Secret body',attachments:[file()]}]}],
      quizzes:{quizzes:[{course:1,coursemodule:31,intro:'Hidden quiz'}]}}});
  assert.equal(result.files.length,0);assert.equal(result.documents.length,0);
  assert.equal(result.gaps.filter(row=>row.reason==='not_accessible').length,2);
});

test('missing previously installed file is fetched again and restored', async t => {
  const f=fixture(t);await syncCourseContent(f.client,f.state,f.options);
  unlinkSync(join(f.root,'SYN/10-chapter.txt'));
  const result=await syncCourseContent(f.client,f.state,f.options);
  assert.equal(result.status,'complete');assert.equal(result.files[0].status,'downloaded');
  assert.equal(readFileSync(join(f.root,'SYN/10-chapter.txt'),'utf8'),'hello');
  assert.equal(f.stats().requests,2);
});

test('native page body identity is stable when unrelated descriptions are inserted', () => {
  const page={id:10,modname:'page',name:'Page',contents:[{type:'content',filename:'index.html',content:'body'}]};
  const options={pageId:1,courseId:'SYN',site};
  const first=enumerateCourseContent([{id:1,modules:[page]}],options).documents[0];
  const later=enumerateCourseContent([{id:1,summary:'new summary',modules:[{id:9,name:'New',description:'new'},page]}],options).documents.find(row=>row.module_id===10);
  assert.equal(first.filename,later.filename);assert.equal(first.filepath,later.filepath);
});

test('page read API supplies native content and files, scoped to the authorized course', async t => {
  const page={id:20,instance:22,modname:'page',name:'Lesson page',uservisible:true};
  const sections=[{id:1,modules:[page]}],f=fixture(t,{sections}),calls=[];
  f.client.call=async(method,args)=>{
    calls.push(method);
    if(method==='core_course_get_contents') return sections;
    assert.equal(method,'mod_page_get_pages_by_courses');assert.deepEqual(args,{'courseids[0]':1});
    return {pages:[{course:1,coursemodule:20,content:'Full native page',contentfiles:[file(20,'page-image.txt')]},
      {course:99,coursemodule:20,content:'Other course must not be included',contentfiles:[file(99,'other.txt')]}],warnings:[]};
  };
  const result=await syncCourseContent(f.client,f.state,f.options);
  assert.equal(result.status,'complete');assert.equal(result.documents.length,1);
  assert.deepEqual(result.files.map(row=>row.filename),['page-image.txt']);
  assert.deepEqual(calls,['core_course_get_contents','mod_page_get_pages_by_courses']);
});

test('unavailable page read API remains a gap and never calls a view endpoint', async t => {
  const page={id:20,modname:'page',name:'Lesson page'},f=fixture(t,{sections:[{id:1,modules:[page]}]});
  f.client.supports=method=>{assert.equal(method,'mod_page_get_pages_by_courses');return false;};
  const result=await syncCourseContent(f.client,f.state,f.options);
  assert.equal(result.status,'partial');assert.equal(f.stats().requests,0);
  assert.ok(result.skipped.some(row=>row.reason==='native_content_not_exposed_by_api'));
});

test('identical installation removes only hash-verified staging duplicates', async t => {
  const f=fixture(t);let savedPath;
  f.options.install=async({stagedPath})=>{
    savedPath=stagedPath;
    return {status:'identical',sha256:createHash('sha256').update(readFileSync(stagedPath)).digest('hex')};
  };
  await syncCourseContent(f.client,f.state,f.options);
  assert.equal(existsSync(savedPath),false);
  assert.deepEqual(readdirSync(join(f.root,'_Inbox/.course-content')),[]);
  f.options.install=async({stagedPath})=>{savedPath=stagedPath;return {status:'identical',sha256:'unverified'};};
  await syncCourseContent(f.client,f.state,f.options);
  assert.equal(existsSync(savedPath),true);
});

test('download failure removes its empty staging folder while keeping previous materials', async t => {
  const f=fixture(t);f.client.transport=async()=>{throw new Error('network unavailable');};
  const result=await syncCourseContent(f.client,f.state,f.options);
  assert.equal(result.status,'partial');
  assert.deepEqual(readdirSync(join(f.root,'_Inbox/.course-content')),[]);
});

test('read-only client permits page content retrieval and still rejects page view writes', async () => {
  const calls=[];
  const client=new ReadOnlyClient(site,'SECRET',async(_url,options)=>{
    calls.push(options.body.get('wsfunction'));
    assert.equal(options.body.get('courseids[0]'),'1');
    return {ok:true,json:async()=>({pages:[],warnings:[]})};
  });
  await client.call('mod_page_get_pages_by_courses',{'courseids[0]':1});
  await assert.rejects(client.call('mod_page_view_page',{pageid:2}),/not allowed/);
  assert.deepEqual(calls,['mod_page_get_pages_by_courses']);
});
