'use strict';
// Execute the actual form handlers, renderer and HTTP request path with board ACKs.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const web=path.join(__dirname,'../web'),source=fs.readFileSync(path.join(web,'app.js'),'utf8');
const elements={};
function node(){return {value:'',checked:false,disabled:false,hidden:false,style:{},attributes:{},children:[],classList:{toggle(){}},
  setAttribute(k,v){this.attributes[k]=String(v)},append(...items){this.children.push(...items)},replaceChildren(){this.children=[]}};}
const $=id=>elements[id]||(elements[id]=node());
const options=Object.fromEntries(['auto','follow','manual','equal'].map(mode=>[mode,node()]));
$('beam-mode').querySelector=selector=>options[selector.match(/value="(.*?)"/)[1]];
$('beam').querySelector=()=>node();$('beam').value='current';
$('beam-angle').value='0';$('beam-angle-slider').value='0';$('angle-layer-beam').checked=true;
const a=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/v12/packet_angle6.json'),'utf8'));
const now=Date.now()/1000,k=2*Math.PI*a.rf_hz/299792458;
a.received_at=now;a.search={start:-80,step:1,count:161};a.complete=true;
a.v12.joint.angle_deg=-26;
const calibrationPhase=[0,-.618,.786,-2.541,-2.486,-2.517];
function acknowledgement(angle){return {available:true,mode:'manual',status:'applied',tracking:false,
  registers_verified:true,applied_run_id:a.run_id,angle_deg:angle,
  phase_rad:a.antennas.map(([x,y],ch)=>{
    const phase=-k*((x-a.antennas[0][0])*Math.sin(angle*Math.PI/180)+(y-a.antennas[0][1])*Math.cos(angle*Math.PI/180))+calibrationPhase[ch];
    return Math.atan2(Math.sin(phase),Math.cos(phase));
  }),
  paths:[0,0],pl_version:1,required_pl_version:1,auto_supported:true,received_at:now};}
a.beam=acknowledgement(12);
const s={mode:'live',aoa:a,beam:{...a.beam},sources:{aoa:{session:a.session,connected:true,last_seen:now}},candidates:[{id:'C1',valid:true}]};
const context={state:s,liveState:s,processingMode:'angle6',beamFormInitialized:false,controlPosting:false,controlError:'',frozen:false,selected:0,
  rad:Math.PI/180,color:{blue:'#6c8fa7',orange:'#d77b45'},Date,Number,Math,console,$,
  text:(id,value)=>$(id).textContent=value,fmt:(v,d=2)=>Number.isFinite(v)?v.toFixed(d):'—',
  archiveControls(){},syncProcessingState(){},kv(){},jointManual:false,scanManual:false,
  svgSize(svg){svg.replaceChildren();return [800,460]},
  add(parent,tag,attributes={},content){const child=node();child.tag=tag;child.attributes={...attributes};child.textContent=content;parent.append(child);return child},
  poll:async()=>{}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(web,'v12_views.js'),'utf8'),context);
vm.runInContext(source.slice(source.indexOf('function jointBeamPattern('),source.indexOf('function renderAngle6Plot(')),context);
vm.runInContext(source.slice(source.indexOf('function polarFrame('),source.indexOf('function renderV12ScanPolar(')),context);
vm.runInContext(source.slice(source.indexOf('function renderBeamControls('),source.indexOf("$('observe-waveform').onchange")),context);
function paint(){context.renderBeamControls();context.renderV12BeamPolar(context.v12AppliedBeamPattern(a),true);}
function curve(role){return $('angle6-plot').children.find(n=>n.attributes['data-role']===role);}
function matchingCurves(){
  const numbers=role=>curve(role).attributes.d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
  const applied=numbers('v12-applied-beam-pattern'),preview=numbers('manual-beam-preview');
  assert.equal(applied.length,preview.length);
  assert.ok(Math.max(...applied.map((value,i)=>Math.abs(value-preview[i])))<1e-8,
    'same-angle preview and real ACK use the same calibrated phases and dB normalization');
}
let sent=[];
context.fetch=async(url,options)=>{sent.push({url,request:JSON.parse(options.body)});return {ok:true,json:async()=>({mode:'manual',status:'sent'})}};
paint();
assert.equal($('beam-mode').value,'manual');assert.equal($('beam-angle').value,'12');
assert.equal(options.follow.hidden,true);assert.equal(options.follow.disabled,true);
assert.equal(curve('v12-applied-beam-pattern').attributes['stroke-dasharray'],undefined,'ACK curve is solid');
const axis=curve('v12-applied-beam-axis').attributes;
assert.equal(axis.stroke,context.color.blue);
assert.ok(Math.abs(Math.atan2(axis.x2-axis.x1,axis.y1-axis.y2)*180/Math.PI-12)<1e-10,
  'upper primary axis follows applied 12°, not the current ML -26°');
const mlAxis=curve('v12-beam-ml-reference').attributes;
assert.equal(mlAxis.stroke,context.color.orange);
assert.ok(Math.abs(Math.atan2(mlAxis.x2-mlAxis.x1,mlAxis.y1-mlAxis.y2)*180/Math.PI+26)<1e-10,
  'orange reference follows this packet ML -26°, independently of applied 12°');
assert.equal($('angle-ml-reference').hidden,false);
assert.equal($('angle-ml-axis').textContent,'ML 主方向 -26.0°');
assert.equal($('angle-applied-axis').textContent,'已应用方向 12.0°');
const joint=a.v12.joint;
for(const invalid of [{...joint,valid:false},{...joint,angle_deg:null},undefined]){
  a.v12.joint=invalid;paint();
  assert.equal(curve('v12-beam-ml-reference'),undefined,'invalid or missing current joint result cannot retain an old ML ray');
  assert.equal($('angle-ml-reference').hidden,true);
  assert.equal(curve('v12-applied-beam-axis').attributes.stroke,context.color.blue);
}
a.v12.joint=joint;
context.renderV12BeamPolar(context.v12AppliedBeamPattern(a),false);
assert.equal(curve('v12-applied-beam-pattern'),undefined);
assert.ok(curve('v12-beam-ml-reference'),'Beam visibility only controls the applied-beam layer');
paint();
matchingCurves();
assert.equal($('angle-draft-label').textContent,'预选 · 与已应用一致');
const initialCurve=curve('v12-applied-beam-pattern').attributes.d,unchangedBoard=JSON.stringify(a.beam);
for(const angle of [-80,-25.4,32,80]){
  $('beam-angle-slider').value=String(angle);$('beam-angle-slider').oninput();
  assert.equal($('beam-angle').value,String(angle));assert.equal(context.manualBeamAngle(),angle);
  assert.equal(curve('v12-applied-beam-pattern').attributes.d,initialCurve,'dragging cannot move applied weights');
  assert.equal(JSON.stringify(a.beam),unchangedBoard);assert.equal(sent.length,0);
}
assert.equal(curve('manual-beam-preview').attributes['fill-opacity'],.02);
assert.equal(curve('manual-beam-preview').attributes['stroke-opacity'],.55);
assert.equal(curve('manual-beam-preview-axis').attributes.opacity,.65);
const manualAck=a.beam;
a.beam={...manualAck,mode:'auto',target:{kind:'angle',angle_deg:12}};
$('beam-mode').value='manual';$('beam-angle').value='12';$('beam-mode').onchange();
matchingCurves();assert.equal(sent.length,0,'first manual preview from ANGLE auto is local only');
a.beam={...manualAck,mode:'equal'};paint();
assert.equal(curve('manual-beam-preview'),undefined,'equal weights cannot identify calibration phase');
assert.equal(curve('v12-applied-beam-pattern'),undefined,'equal ACK cannot stand in for a calibrated receiver response');
assert.equal(curve('v12-applied-beam-axis'),undefined);
assert.ok(curve('v12-beam-ml-reference'),'ML reference remains independent of an unavailable applied-beam response');
assert.match($('beam-manual-status').textContent,/预览等待有效指向相位回执/);
a.beam=manualAck;paint();
$('beam-angle').value='12.34';$('beam-angle').oninput();assert.equal($('beam-angle-slider').value,'12.3');
$('beam-angle').onchange();assert.equal($('beam-angle').value,'12.3');
for(const invalid of ['','81','80.01','-80.01','bad']){
  $('beam-angle').value=invalid;$('beam-angle').oninput();assert.equal($('beam-apply').disabled,true);
  assert.equal(curve('manual-beam-preview'),undefined);assert.equal(sent.length,0);
}
s.aoa.search={start:-45,step:.5,count:181};context.renderBeamControls();
assert.equal($('beam-angle-slider').min,-45);assert.equal($('beam-angle').max,45);
$('beam-angle').value='46';$('beam-angle').oninput();assert.equal($('beam-apply').disabled,true);
$('beam-angle').value='32';$('beam-angle').oninput();assert.equal($('beam-apply').disabled,false);

(async()=>{
  await context.sendBeam('manual');
  assert.deepEqual(sent[0],{url:'/api/beam',request:{mode:'manual',angle_deg:32}});
  paint();assert.equal(curve('v12-applied-beam-pattern').attributes.d,initialCurve,'HTTP sent is not a board ACK');
  assert.equal($('beam-apply').disabled,true);assert.match($('beam-manual-status').textContent,/已应用 12.0°.*预选 32.0°.*未应用/);
  s.control={mode:'manual',status:'rejected',error:'board rejected'};paint();
  assert.equal(curve('v12-applied-beam-pattern').attributes.d,initialCurve,'rejected command retains applied weights');
  a.beam=acknowledgement(32);s.beam={...a.beam};s.control={mode:'manual',status:'applied'};paint();
  assert.notEqual(curve('v12-applied-beam-pattern').attributes.d,initialCurve,'only real verified phase ACK changes applied curve');
  assert.match($('beam-manual-status').textContent,/已应用 32.0°.*与已应用一致/);
  matchingCurves();
  assert.equal($('angle-draft-label').textContent,'预选 · 与已应用一致','APPLY acknowledgement updates the draft legend');
  const verifiedCurve=curve('v12-applied-beam-pattern').attributes.d;
  a.beam.registers_verified=false;paint();assert.equal(curve('v12-applied-beam-pattern'),undefined);
  a.beam.registers_verified=true;a.beam.applied_run_id='other';paint();assert.equal(curve('v12-applied-beam-pattern'),undefined);
  a.beam.applied_run_id=a.run_id;paint();assert.equal(curve('v12-applied-beam-pattern').attributes.d,verifiedCurve);
  const count=sent.length;await context.sendBeam('follow');assert.equal(sent.length,count,'ANGLE cannot send follow through a stale/programmatic option');
  context.processingMode='position';$('beam-mode').value='follow';context.renderBeamControls();
  assert.equal(options.follow.hidden,false);assert.equal(options.follow.disabled,false);assert.equal($('beam-apply').disabled,false);
  await context.sendBeam('follow');
  assert.deepEqual(sent.at(-1).request,{mode:'follow',candidate:0,candidate_id:'C1',run_id:a.run_id},'POSITION retains its candidate-follow protocol');
  console.log('PASS manual beam: local draft, current search bounds, no drag requests, APPLY request, real ACK-only solid curve and POSITION follow retained');
})().catch(error=>{console.error(error);process.exitCode=1});
