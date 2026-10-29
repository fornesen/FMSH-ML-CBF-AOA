'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const web=path.join(__dirname,'../web'),source=fs.readFileSync(path.join(web,'app.js'),'utf8');
const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
const input=html.match(/<input\b[^>]*id="enable-archive"[^>]*>/)?.[0];
assert.ok(input&&/\bdisabled\b/.test(input)&&! /\bchecked\b/.test(input),'archive starts unchecked and disabled');
const elements={},context={Date,Boolean,JSON,Error,console,archiveRequest:null,archiveError:'',archiveSession:'',controlPosting:false,frozen:false,state:null,liveState:null};
context.$=id=>elements[id]||(elements[id]={checked:false,disabled:false,textContent:''});
context.text=(id,value)=>context.$(id).textContent=value;
context.poll=async()=>{};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function archiveControls('),source.indexOf('function signalAxis(')),context);
const state=()=>({mode:'live',sources:{aoa:{connected:true,last_seen:Date.now()/1000,connection:'board',session:'producer'}},
  beam:{session:'producer',archive:{available:true,enabled:false,command_status:'idle',error:'',pending_batches:0,pending_scope:'current_session'}},control:null});
const control=(status,error='')=>({mode:'archive',status,error,connection:'board',session:'producer'});
context.liveState=state();context.archiveControls();
assert.equal(elements['enable-archive'].checked,false);
assert.equal(elements['enable-archive'].disabled,false);
assert.equal(elements['archive-status'].textContent,'调试归档：关 · 正常测量 · 待确认 0 批');
context.liveState.beam.archive.pending_batches=3;context.archiveControls();
assert.equal(elements['enable-archive'].checked,false,'closing recording does not hide the pending queue');
assert.match(elements['archive-status'].textContent,/关 · 正常测量 · 待确认 3 批$/);
assert.ok(!elements['archive-status'].textContent.includes('已清空'));
context.liveState.beam.archive.pending_batches=null;context.archiveControls();
assert.match(elements['archive-status'].textContent,/待确认未知$/);
context.liveState.beam.archive.pending_batches=0;
context.liveState.beam.archive=undefined;context.archiveControls();
assert.equal(elements['enable-archive'].disabled,true,'legacy board cannot expose an active archive control');
assert.equal(elements['enable-archive'].checked,false);
context.liveState=state();context.liveState.mode='demo';context.archiveControls();
assert.equal(elements['enable-archive'].disabled,true,'demo cannot enable board recording');
context.liveState=state();context.archiveRequest={mode:'archive',archive_enabled:true};
context.liveState.control=control('sent');context.archiveControls();
assert.equal(elements['enable-archive'].checked,true,'pending intent is shown separately from applied state');
assert.equal(elements['enable-archive'].disabled,true);
assert.match(elements['archive-status'].textContent,/等待板端回执/);
assert.match(elements['archive-status'].textContent,/当前关/,'a pending enable does not claim board recording is already on');
context.liveState.control=control('applied');
context.liveState.beam.archive.enabled=true;context.liveState.beam.archive.command_status='applied';context.archiveControls();
assert.equal(elements['enable-archive'].checked,true);
assert.equal(elements['enable-archive'].disabled,false);
assert.match(elements['archive-status'].textContent,/开 · 回传到电脑/);
context.liveState=state();context.liveState.control=control('rejected','板端拒绝');context.archiveControls();
assert.equal(elements['enable-archive'].checked,false);
assert.equal(elements['archive-status'].textContent,'板端拒绝 · 待确认 0 批');

// Rendering disconnected or a restarted producer must not reuse an old enabled state.
context.liveState=state();context.liveState.beam.archive.enabled=true;
context.liveState.sources.aoa.connected=false;context.archiveControls();
assert.equal(elements['enable-archive'].checked,false);
assert.equal(elements['enable-archive'].disabled,true);
context.liveState.sources.aoa.connected=true;
context.liveState.sources.aoa.session='new-board';context.liveState.beam.session='old-board';
context.archiveRequest={mode:'archive',archive_enabled:true};context.archiveControls();
assert.equal(elements['enable-archive'].checked,false,'old producer archive status is not a new session ACK');
assert.equal(context.archiveRequest,null,'new sessions discard an old local enable intention');
context.liveState.beam.session='new-board';context.liveState.beam.archive.enabled=false;
context.archiveControls();
assert.equal(elements['enable-archive'].checked,false);
assert.equal(elements['enable-archive'].disabled,false);

// A former command cannot leak an error or pending enable across either identity change.
for(const changed of ['session','connection'])for(const status of ['unknown','rejected','error','sent']){
  context.liveState=state();context.archiveControls();
  context.liveState.control=control(status,'旧归档命令错误');
  context.archiveRequest={mode:'archive',archive_enabled:true};context.archiveError='旧发送错误';
  context.liveState.sources.aoa[changed]='new-'+changed;
  if(changed==='session')context.liveState.beam.session='new-session';
  context.archiveControls();
  assert.equal(elements['enable-archive'].checked,false);
  assert.equal(elements['enable-archive'].disabled,false);
  assert.equal(context.archiveRequest,null);
  assert.equal(context.archiveError,'');
  assert.equal(elements['archive-status'].textContent,'调试归档：关 · 正常测量 · 待确认 0 批');
}

(async()=>{
  context.liveState=state();context.archiveControls();elements['enable-archive'].checked=true;
  let sent=null;
  context.fetch=async(url,options)=>{sent={url,...options};return {ok:true,json:async()=>control('sent')};};
  await context.sendArchive();
  assert.equal(sent.url,'/api/beam');
  assert.deepEqual(JSON.parse(sent.body),{mode:'archive',archive_enabled:true},'archive has an independent request, without observation flags');
  assert.equal(context.liveState.beam.archive.enabled,false,'HTTP send alone cannot mark board recording applied');
  assert.match(elements['archive-status'].textContent,/等待板端回执/);
  context.liveState=state();context.archiveRequest=null;elements['enable-archive'].checked=true;
  context.fetch=async()=>({ok:false,json:async()=>({error:'发送失败'})});
  await context.sendArchive();
  assert.equal(elements['enable-archive'].checked,false,'failed sends restore the applied board state');
  assert.equal(elements['archive-status'].textContent,'发送失败 · 待确认 0 批');
  let viewRequests=0;context.fetch=async()=>{viewRequests++;throw Error('render must not send control');};
  context.liveState=state();context.archiveRequest=null;context.archiveError='';context.archiveControls();
  context.liveState.sources.aoa.connected=false;context.archiveControls();
  context.liveState=state();context.archiveControls();
  assert.equal(viewRequests,0,'viewing or reconnecting never sends an archive-enable command');
  console.log('PASS archive UI: default off, ACK, failure rollback, no cross-session errors or automatic enable');
})().catch(error=>{console.error(error);process.exitCode=1;});
