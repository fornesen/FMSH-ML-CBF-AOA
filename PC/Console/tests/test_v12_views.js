'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const web=path.join(__dirname,'../web'),context={atob:s=>Buffer.from(s,'base64').toString('binary')};
vm.createContext(context);
for(const file of ['v12_views.js','joint_heatmap.js','joint_scores.js'])vm.runInContext(fs.readFileSync(path.join(web,file),'utf8'),context);
const p=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/v12/packet_position.json'),'utf8'));
assert.equal(context.analysisPage('position'),'position');
assert.equal(context.analysisPage('angle6'),'angle6');
assert.equal(context.analysisPage('scan6'),'angle6','legacy scan6 packets select the measurement page');
const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
const analysisNav=html.match(/<div class="ao-navgroup" role="group" aria-label="分析模式">([\s\S]*?)<\/div>/)[1];
const visibleTabs=[...analysisNav.matchAll(/<button\b([^>]*)>([^<]*)<\/button>/g)].filter(m=>! /\bhidden(?:\s|$|=)/.test(m[1])).map(m=>m[2]);
assert.deepEqual(visibleTabs,['POSITION','ANGLE'],'only two analysis tabs are exposed');
for(const label of ['信号观测','波束控制','NPU 分类识别','采集参数','联合 ML 历史','联合 ML 评分','波束合成 IQ 频谱','波束合成 IQ 波形'])assert.ok(html.includes(label),`Chinese primary title is visible: ${label}`);
for(const label of ['Enable Spectrum','Enable Waveform','Apply','Pause'])assert.ok(html.includes(label),`English control is preserved: ${label}`);
for(const id of ['angle-layer-scan','angle-layer-ml','angle-layer-beam','angle-polar-key','angle-history-grid','scan-polar-plot'])assert.ok(html.includes(`id="${id}"`),'separate Beam and Scan plots retain layer controls and an external key');
assert.ok(html.includes('>CBF</label>')&&html.includes('常规波束形成空间谱（CBF）'),'CBF is the display term while scan IDs and wire fields stay unchanged');
assert.ok(html.includes('同一板端 ML 搜索'),'history and current-batch views explain their shared source in Chinese');
const uiSource=fs.readFileSync(path.join(web,'app.js'),'utf8');
assert.ok(uiSource.includes("$('angle-beam-info').hidden=true;")&&html.includes('class="ao-scan-polar-scale" hidden'),'decorative paragraphs stay hidden while their technical notes remain in hover help');
assert.ok(uiSource.includes("$('angle-layer-beam-label').title=")&&uiSource.includes("$('joint-panel-title').title="),'Beam and panel hover help retain technical qualifications');
const frameSource=uiSource.slice(uiSource.indexOf('function polarFrame('),uiSource.indexOf('function polarDirection('));
context.rad=Math.PI/180;context.add=()=>({});context.svgSize=()=>[800,460];
vm.runInContext(frameSource,context);
const originalFrame=context.polarFrame({});
assert.equal(originalFrame.cy,460-58);
assert.equal(originalFrame.radius,.9*Math.min((800-110)/2,460-98),'upper Beam uses the original V1.0 curve radius');
const css=fs.readFileSync(path.join(web,'app.css'),'utf8');
assert.ok(css.includes('grid-template-rows:minmax(0,1fr) auto')&&css.includes('.ao-scan-polar-body>.ao-polar-key{grid-row:2;align-self:end}'),'CBF key uses a bottom-aligned grid row while the plot centers independently');
assert.ok(html.includes('id="cbf-power-label"')&&uiSource.includes("if(!compact)add(svg,'text',{x:16,y:20},'Relative Power / dB');"),'CBF power unit is outside its centered SVG while the Beam label stays inside');
assert.ok(html.includes('id="cbf-reference-label"')&&css.includes('#cbf-reference-label{grid-column:1;grid-row:1;align-self:end;justify-self:end;'),'CBF reference text anchors to the drawing row above the key');
assert.ok(!/\.is-v12 #angle6-plot\{/.test(css),'upper Beam inherits the original 460px/400px responsive CSS without extra caps');
assert.ok(!uiSource.includes('#5c9694')&&!fs.readFileSync(path.join(web,'app.css'),'utf8').includes('#5c9694'),'auxiliary markers and cards reuse the original blue instead of a new teal role');
const beamView=uiSource.slice(uiSource.indexOf('function renderV12BeamPolar('),uiSource.indexOf('function renderV12ScanPolar('));
assert.ok(!beamView.includes('v12ScanPoints')&&!beamView.includes('v12-scan-polar'),'upper Beam plot cannot substitute the scan curve');
const scanView=uiSource.slice(uiSource.indexOf('function renderV12ScanPolar('),uiSource.indexOf('function renderV12Angle6('));
assert.ok(!scanView.includes("'v12-joint-direction'"),'scan view retains ML markers without an orange primary ray');
for(const explanation of ['理想校准·等幅理论响应；非实测','CBF 主峰','选中 ML 组','剩余 ML 路径','仅显示','等待本批有效指向与校准相位依据'])assert.ok(uiSource.includes(explanation),`Chinese explanation remains available: ${explanation}`);
for(const id of ['mode-locate','mode-identify','config-open'])assert.ok(html.includes(`id="${id}"`),'acquisition and setup controls are preserved');
assert.equal(context.v12FullArrayVisible(p),false,'POSITION must hide all six-channel plots');
for(const mode of ['angle6','scan6'])assert.equal(context.v12FullArrayVisible(p,mode),true,'ANGLE6/SCAN6 keep full-array scan and ML views');
assert.equal(context.v12SelectedCandidate(p).id,p.v12.localization.selected_id);
assert.equal(context.v12LocalizationMethod(p.v12.localization),'联合角关联','legacy board packets keep the previous explanation');
assert.equal(context.v12CandidateScoreText(p.v12.localization,p.v12.localization.candidates[0]),'','legacy packets do not invent scores');
const balanced=JSON.parse(JSON.stringify(p));
Object.assign(balanced.v12.localization,{selection_method:'direction_residual_angle',residual_weight:.5,angle_sigma_deg:30});
for(const c of balanced.v12.localization.candidates)Object.assign(c,{direction_residuals:[.1,.2],direction_residual_ratio:.15,angle_cost:.04,selection_cost:.095});
const balancedSource=JSON.stringify(balanced),selectedBefore=context.v12SelectedCandidate(balanced).id;
assert.equal(context.v12LocalizationMethod(balanced.v12.localization),'方向回代残差＋联合角一致性 · λ 0.50 · σθ 30°');
assert.match(context.v12CandidateScoreText(balanced.v12.localization,balanced.v12.localization.candidates[0]),/^R 15\.00% · Cost 0\.0950 · Δθ /);
assert.equal(context.v12CandidateScoreText(balanced.v12.localization,{direction_residual_ratio:null,selection_cost:null,angle_error_deg:null}),'R — · Cost — · Δθ —°');
assert.equal(context.v12SelectedCandidate(balanced).id,selectedBefore,'score displays cannot rerank the board selection');
assert.equal(JSON.stringify(balanced),balancedSource,'score formatting preserves source fields');
const energy=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/v12/packet_position_no_reference.json'),'utf8'));
energy.v12.joint.valid=false;energy.v12.joint.angle_deg=null;
Object.assign(energy.v12.localization,{selection_method:'triad_ml_energy',reference_angle_deg:null,selected_id:'C1'});
energy.v12.localization.candidates.forEach((c,i)=>Object.assign(c,{energy_share:[.42,.28,.18,.12][i],angle_error_deg:null}));
const energySource=JSON.stringify(energy),energyLoc=energy.v12.localization;
assert.equal(context.v12LocalizationMethod(energyLoc),'两组三天线 ML · 路径能量选点');
assert.equal(context.v12CandidateScoreText(energyLoc,energyLoc.candidates[0]),'ML Energy 42.00%');
assert.equal(context.v12CandidateScoreText(energyLoc,{energy_share:null}),'ML Energy —');
assert.equal(context.v12SelectedCandidate(energy).id,'C1','pure triad ML selection needs no full-array joint direction');
assert.equal(JSON.stringify(energy),energySource,'energy formatting preserves source values');
// Exercise the actual candidate-card renderer without a browser dependency.
const elements={},node=()=>({children:[],dataset:{},append(...children){this.children.push(...children);},replaceChildren(){this.children=[];}});
context.state={aoa:balanced};context.$=id=>elements[id]||(elements[id]=node());
context.text=(id,value)=>context.$(id).textContent=value;
context.fmt=(value,d)=>Number.isFinite(value)?value.toFixed(d):'—';
context.document={createElement:node};
vm.runInContext(uiSource.slice(uiSource.indexOf('function renderV12Localization('),uiSource.indexOf('// [测角方向图新增]')),context);
context.renderV12Localization(balanced.v12);
assert.match(elements['v12-localization-summary'].textContent,/方向回代残差＋联合角一致性/);
assert.equal(elements['v12-candidates'].children.length,4);
assert.ok(elements['v12-candidates'].children.every(card=>card.children.length===4),'new cards show a separate score line');
assert.equal(elements['v12-candidates'].children.filter(card=>card.dataset.boardSelected==='true').length,1);
context.state={aoa:p};context.renderV12Localization(p.v12);
assert.ok(elements['v12-candidates'].children.every(card=>card.children.length===3),'legacy cards remain compact');
context.state={aoa:energy};context.renderV12Localization(energy.v12);
assert.match(elements['v12-localization-summary'].textContent,/板端最终定位 C1.*两组三天线 ML · 路径能量选点/);
for(const card of elements['v12-candidates'].children){
  assert.equal(card.children.length,4);
  assert.match(card.children[3].textContent,/^ML Energy /);
  assert.match(card.children[3].title,/不是定位概率/);
  assert.ok(!card.children.some(child=>/Cost|Δθ|联合角/.test(child.textContent||'')),'POSITION cards omit old weighted-angle diagnostics');
}
assert.equal(elements['v12-candidates'].children.filter(card=>card.dataset.boardSelected==='true').length,1);
assert.equal(context.v12FullArrayVisible(energy),false,'POSITION keeps CBF and full-array joint plots hidden');
const polar=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/v12/packet_angle6.json'),'utf8'));
const kept=JSON.stringify(polar.v12);
assert.equal(context.v12ScanDomain(polar.v12.scan,polar.search).join(','),[polar.v12.scan.angles_deg[0],polar.v12.scan.angles_deg.at(-1)].join(','),'scan domain uses the actual received endpoints');
assert.equal(context.v12ScanDomain({angles_deg:[]},{start:-80,step:1,count:161}).join(','),'-80,80','missing curves use the reported search endpoints');
assert.equal(context.v12ScanDomain({angles_deg:[-60,0,60]},{start:-80,step:1,count:161}).join(','),'-60,60','sampled ±60 degrees cannot be relabeled as ±80 or ±90');
const points=context.v12ScanPoints(polar.v12.scan,200,300,150);
assert.equal(points.length,polar.v12.scan.angles_deg.length,'one display vertex per board scan sample');
assert.ok(points.length>=2,'measurement fixture retains its board-computed scan curve');
for(let i=0;i<points.length;i++){
  const angle=polar.v12.scan.angles_deg[i]*Math.PI/180;
  const radius=Math.max(0,Math.min(1,(polar.v12.scan.relative_db[i]+40)/40))*150;
  assert.ok(Math.abs(points[i][0]-(200+radius*Math.sin(angle)))<1e-9);
  assert.ok(Math.abs(points[i][1]-(300-radius*Math.cos(angle)))<1e-9);
}
assert.equal(JSON.stringify(polar.v12),kept,'rendering must preserve source values');
const weighted=JSON.parse(JSON.stringify(polar)),rf=445e6,k=2*Math.PI*rf/299792458,steer=20*Math.PI/180;
weighted.rf_hz=rf;weighted.phase_sign=1;weighted.received_at=100;
weighted.antennas=Array.from({length:6},(_,i)=>[(i-2.5)*299792458/rf/2,0]);
weighted.beam={available:true,status:'applied',registers_verified:true,applied_run_id:weighted.run_id,
  hardware_response_verified:false,mode:'manual',angle_deg:20,phase_rad:weighted.antennas.map(([x])=>-k*x*Math.sin(steer))};
const original=JSON.stringify(weighted),beam=context.v12AppliedBeamPattern(weighted,101);
assert.ok(beam);assert.equal(beam.equalAmplitude,true);assert.equal(beam.points.length,721);
assert.equal(Math.max(...beam.points.map(p=>p.db)),0,'beam is normalized to its own maximum');
assert.ok(Math.abs(beam.points.reduce((a,b)=>a.db>b.db?a:b).angle-20)<=.125,'verified planar phases use the corresponding ideal calibration model');
assert.equal(beam.appliedAngleDeg,20);
assert.equal(beam.responseBasis,'ideal_phase_calibrated');
assert.equal(JSON.stringify(weighted),original,'array factor cannot alter board data');
for(const change of [q=>delete q.beam,q=>q.beam.registers_verified=false,q=>q.beam.applied_run_id='other',q=>q.beam.status='error',q=>q.beam.phase_rad.pop(),q=>q.beam.phase_rad[0]=NaN]){
  const q=JSON.parse(original);change(q);assert.equal(context.v12AppliedBeamPattern(q,101),null,'unverified, incomplete or mismatched weights stay hidden');
}
assert.equal(context.v12AppliedBeamPattern(weighted,111),null,'stale applied weights stay hidden');
// Independent PS beam_weights.hpp manual contract, with the real calibration
// phase offsets and CH3 equivalent phase-center offset from the V2 fixture.
const calibrated=JSON.parse(original),gains=[[1,0],[.669128995,-.475768382],[.721640917,.722590988],[-.585179804,-.400752836],[-1.511980505,-1.162465207],[-1.762055037,-1.271231187]];
calibrated.antennas=[-1.282361,-.93618,-.59,.59+.042344420802395664,.93618,1.282361].map(x=>[x,0]);
function psManualPhases(packet,angle,correction=gains){
  const theta=angle*Math.PI/180,k=(packet.phase_sign||1)*2*Math.PI*packet.rf_hz/299792458,ref=packet.antennas[0];
  return packet.antennas.map(([x,y],ch)=>{
    const phase=-k*((x-ref[0])*Math.sin(theta)+(y-ref[1])*Math.cos(theta))+Math.atan2(correction[ch][1],correction[ch][0])-Math.atan2(correction[0][1],correction[0][0]);
    return Math.atan2(Math.sin(phase),Math.cos(phase));
  });
}
for(const sign of [-1,1]){
  calibrated.phase_sign=sign;
  calibrated.beam.mode='manual';calibrated.beam.angle_deg=-38.3;
  calibrated.beam.phase_rad=psManualPhases(calibrated,-38.3);
  const unchanged=JSON.stringify(calibrated);
  for(const angle of [-80,-38.3,0,22.7,80]){
    const preview=context.v12ManualBeamPreview(calibrated,angle,-40,101),expected=psManualPhases(calibrated,angle);
    assert.ok(preview);
    preview.phaseRad.forEach((phase,ch)=>assert.ok(Math.abs(Math.atan2(Math.sin(phase-expected[ch]),Math.cos(phase-expected[ch])))<1e-12,
      'draft phases match PS manual geometry plus calibration, modulo 2π'));
    const ack=JSON.parse(unchanged);ack.beam.phase_rad=expected;ack.beam.angle_deg=angle;
    const applied=context.v12AppliedBeamPattern(ack,101);
    assert.ok(Math.max(...preview.points.map((p,i)=>Math.abs(p.db-applied.points[i].db)))<1e-9,
      'predicted draft and subsequent independent ACK share the same own-peak scale');
    const peak=applied.points.reduce((a,b)=>a.db>b.db?a:b).angle;
    assert.ok(Math.abs(peak-angle)<=.125+1e-12,'off-grid applied direction is resolved to the nearest 0.25° display sample');
  }
  assert.equal(JSON.stringify(calibrated),unchanged,'draft reconstruction cannot modify board phases or corrected geometry');
}
const unity=Array.from({length:6},()=>[1,0]);
const rotated=gains.map(([re,im],ch)=>{
  const phase=Math.atan2(im,re)+ch*.71;
  return [Math.cos(phase),Math.sin(phase)];
});
calibrated.phase_sign=1;calibrated.beam.mode='manual';calibrated.beam.angle_deg=-25.07;
const ideal=context.v12PhaseBeamPattern(calibrated,psManualPhases(calibrated,-25.07,unity));
for(const correction of [unity,gains,rotated]){
  calibrated.beam.phase_rad=psManualPhases(calibrated,-25.07,correction);
  const response=context.v12AppliedBeamPattern(calibrated,101);
  assert.ok(Math.max(...response.points.map((p,i)=>Math.abs(p.db-ideal[i].db)))<1e-9,
    'fixed hardware compensation cannot move the ideal calibration-model response');
  assert.ok(Math.abs(response.points.reduce((a,b)=>a.db>b.db?a:b).angle+25.07)<=.125+1e-12);
}
calibrated.beam.angle_deg=-38.3;calibrated.beam.phase_rad=psManualPhases(calibrated,-38.3);
for(const mode of ['auto','follow']){
  calibrated.beam.mode=mode;calibrated.beam.target={kind:'angle',angle_deg:-38.3};
  assert.ok(context.v12ManualBeamPreview(calibrated,20,-40,101),'ANGLE auto/follow retains its manual steering contract');
}
for(const change of [q=>q.beam.mode='equal',q=>q.beam.target={x:1,y:3},
  q=>q.beam.target.angle_deg=40,q=>q.beam.registers_verified=false,
  q=>q.beam.applied_run_id='other',q=>q.received_at=0]){
  const unavailable=JSON.parse(JSON.stringify(calibrated));change(unavailable);
  assert.equal(context.v12ManualBeamPreview(unavailable,20,-40,101),null,
    'equal, spherical, stale or unproven weights cannot invent a calibration phase');
  assert.equal(context.v12AppliedBeamPattern(unavailable,101),null,
    'unidentified channel response is unavailable rather than mislabeled as calibrated');
}
p.v12.localization.selected_id=null;
assert.equal(context.v12SelectedCandidate(p),null,'no browser candidate fallback');
p.joint.residual_ratio=.8;p.joint.ambiguous=true;
assert.equal(context.jointRawQualityPassed(p),p.v12.joint.valid,'board V1.2 validity controls display');
vm.runInContext('this.History=JointHeatHistory;',context);
const history=new context.History();
for(const [i,mode] of ['position','angle6','scan6'].entries()){
  p.processing_mode=mode;p.received_at=100+i;p.run_id='preview-'+i;
  history.update(p);
}
assert.equal(history.rows.length,3,'source profiles survive analysis switches while POSITION keeps full-array views hidden');
delete p.v12;p.processing_mode='position';history.update(p);
assert.equal(history.rows.length,0,'old position mode retains its original heatmaps');
console.log('PASS V1.2 display: two analysis pages, legacy scan mapping, separate POSITION/full-array views, board-selected candidate, direct polar vertices');
