import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {downloadModules} from '../tools/moodle/download.mjs';

const state = {courses:[{id:'DEMO',moodle_course_ids:[101]}]};
function fixture(overrides = {}) {
  const data = Buffer.from('%PDF-1.4\nfictional test bytes');
  const file = {type:'file',filename:'Chapter One.pdf',fileurl:'https://school.example/moodle/pluginfile.php/1/mod_resource/content/1/lesson.pdf',filesize:data.length,...overrides};
  const client = {baseUrl:'https://school.example/moodle',token:'fictional-api-token',calls:[],
    async call(method,args) {this.calls.push({method,args});return [{modules:[{id:501,modname:'resource',uservisible:true,contents:[file]}]}];},
    async transport(url, options) {
      this.calls.push({download:true});
      const source = new URL(url);
      assert.equal(source.origin,'https://school.example');
      assert(source.pathname.startsWith('/moodle/webservice/pluginfile.php/'));
      assert.equal(source.searchParams.get('token'),'fictional-api-token');
      assert.equal(options.redirect,'error');
      return new Response(data,{headers:{'content-type':'application/pdf'}});
    }};
  return {client,file,data};
}
function temporary() {const root=mkdtempSync(join(tmpdir(),'课件 download '));mkdirSync(join(root,'Study','_System'),{recursive:true});return root;}

test('downloads require explicit authorized module selection before network access',async()=>{
  const {client}=fixture();
  for(const options of [{moduleIds:[501]},{moduleIds:[],authorized:true},{moduleIds:[501,501],authorized:true}])
    await assert.rejects(downloadModules(client,state,'.',options));
  assert.equal(client.calls.length,0);
});

test('authorized original is saved without credentials, duplicates reused and differing originals preserved',async()=>{
  const root=temporary();const {client,file,data}=fixture();
  try {
    const first=await downloadModules(client,state,root,{moduleIds:[501],authorized:true});
    assert.equal(first.errors.length,0);assert.equal(first.files.length,1);
    const target=join(root,'Study',first.files[0].relative_path);
    assert.deepEqual(readFileSync(target),data);
    assert.equal(first.files[0].filename,file.filename);
    assert(!JSON.stringify(first).includes(client.token));assert(!JSON.stringify(first).includes('?token='));
    const again=await downloadModules(client,state,root,{moduleIds:[501],authorized:true});
    assert.equal(again.files[0].status,'already_present_identical');
    const changed=Buffer.from('%PDF-1.4\nnew fictional content');file.filesize=changed.length;
    client.transport=async()=>new Response(changed,{headers:{'content-type':'application/pdf'}});
    const next=await downloadModules(client,state,root,{moduleIds:[501],authorized:true});
    assert.equal(next.errors.length,0);assert.notEqual(next.files[0].relative_path,first.files[0].relative_path);
    assert.deepEqual(readFileSync(target),data);
    assert.deepEqual(readFileSync(join(root,'Study',next.files[0].relative_path)),changed);
    assert.deepEqual(state.courses,[{id:'DEMO',moodle_course_ids:[101]}]);
  } finally {rmSync(root,{recursive:true,force:true});}
});

test('unselected module and unsafe file sources never receive credentialed file requests',async()=>{
  const root=temporary();
  try {
    const {client}=fixture();await assert.rejects(downloadModules(client,state,root,{moduleIds:[999],authorized:true}));
    assert(!client.calls.some(c=>c.download));
    for(const change of [{filename:'../outside.pdf'},{filename:'CON.pdf'},{filename:'malware.exe'},
      {fileurl:'https://other.example/moodle/pluginfile.php/file.pdf'},
      {fileurl:'https://school.example/not-moodle/pluginfile.php/file.pdf'},
      {fileurl:'https://school.example/moodle/admin/tool.php'}]){
      const {client:other}=fixture(change);
      const result=await downloadModules(other,state,root,{moduleIds:[501],authorized:true});
      assert.equal(result.files.length,0);assert.equal(result.errors.length,1);
      assert(!other.calls.some(c=>c.download));
    }
  } finally {rmSync(root,{recursive:true,force:true});}
});

test('file integrity failures and fetch errors keep authenticated URLs out of reports',async()=>{
  const root=temporary();
  try {
    for(const mode of ['mismatch','large','html','network']) {
      const {client,file}=fixture();
      if(mode==='mismatch')file.filesize++;
      if(mode==='html')client.transport=async()=>new Response('<html>login</html>',{headers:{'content-type':'text/html'}});
      if(mode==='network')client.transport=async url=>{throw new Error(url);};
      const result=await downloadModules(client,state,root,{moduleIds:[501],authorized:true,maxBytes:mode==='large'?2:1024});
      assert.equal(result.files.length,0);assert.equal(result.errors.length,1);
      assert(!JSON.stringify(result).includes(client.token));
    }
  } finally {rmSync(root,{recursive:true,force:true});}
});
