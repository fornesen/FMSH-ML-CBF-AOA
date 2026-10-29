'use strict';
// Display-only contract: PS POSITION weights focus on a spatial target, not
// a far-field direction. No hardware, clock, or archived capture is required.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const context={};vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/v12_views.js'),'utf8'),context);
assert.equal(typeof context.v12PositionBeamPattern,'function');
const now=1793264000,rad=Math.PI/180,lightSpeed=299792458;
const antennas=[[-1.282361334873,-.012],[-.936180667436,.004],[-.590000000000,.011],
  [.604869064002,-.006],[.936180667436,.002],[1.282361334873,.001]];
const correction=[0,-.618,.786,-2.541,-2.486,-2.517];
const clone=value=>JSON.parse(JSON.stringify(value));
const wrap=value=>Math.atan2(Math.sin(value),Math.cos(value));
const centre=points=>points.reduce((sum,p)=>[sum[0]+p[0]/points.length,sum[1]+p[1]/points.length],[0,0]);
const distances=(points,target)=>points.map(([x,y])=>Math.hypot(target.x-x,target.y-y));
function packet(angle=9.158535137,range=3.35,sign=1,mode='auto'){
  const origin=centre(antennas),target={x:origin[0]+range*Math.sin(angle*rad),y:origin[1]+range*Math.cos(angle*rad),source_run_id:'source-previous'};
  const d=distances(antennas,target),k=sign*2*Math.PI*445e6/lightSpeed;
  return {complete:true,received_at:now,run_id:'applied-current',processing_mode:'position',rf_hz:445e6,phase_sign:sign,
    antennas:clone(antennas),beam:{available:true,registers_verified:true,applied_run_id:'applied-current',status:'tracking',mode,
      angle_deg:angle,acquisition:{applied_mode:'locate'},target,
      // PS beam_weights: spherical geometric delay + relative channel correction.
      phase_rad:d.map((distance,ch)=>wrap(k*(distance-d[0])+correction[ch]-correction[0]))},
    v12:{version:'1.2',localization:{selected_id:'C4',candidates:[{id:'C4',valid:true,x:-8,y:2}]}}};
}
function draw(a,floor=-30){return context.v12PositionBeamPattern(a,floor,now);}
function response(a,angle){
  const origin=centre(a.antennas),target=a.beam.target,R=Math.hypot(target.x-origin[0],target.y-origin[1]);
  const q={x:origin[0]+R*Math.sin(angle*rad),y:origin[1]+R*Math.cos(angle*rad)};
  const dt=distances(a.antennas,target),dq=distances(a.antennas,q),k=(a.phase_sign||1)*2*Math.PI*a.rf_hz/lightSpeed;
  let real=0,imaginary=0;
  for(let ch=0;ch<a.antennas.length;ch++){
    const phase=k*((dt[ch]-dt[0])-(dq[ch]-dq[0]));
    real+=Math.cos(phase);imaginary+=Math.sin(phase);
  }
  return Math.hypot(real,imaginary)/a.antennas.length;
}
function check(a,floor=-30){
  const before=JSON.stringify(a),view=draw(a,floor);
  assert.ok(view,'valid same-batch spatial ACK has a theoretical response');
  assert.equal(JSON.stringify(a),before,'drawing cannot change the packet or beam target');
  assert.equal(view.responseBasis,'ideal_phase_calibrated_nearfield');
  assert.equal(view.appliedRunId,a.beam.applied_run_id);
  assert.equal(view.appliedAngleDeg,a.beam.angle_deg);
  assert.equal(view.target.source_run_id,a.beam.target.source_run_id);
  assert.equal(view.target.x,a.beam.target.x);assert.equal(view.target.y,a.beam.target.y);
  const origin=centre(a.antennas);
  assert.ok(Math.hypot(view.origin[0]-origin[0],view.origin[1]-origin[1])<1e-12);
  assert.ok(Math.abs(view.rangeM-Math.hypot(a.beam.target.x-origin[0],a.beam.target.y-origin[1]))<1e-10);
  assert.ok(view.points.length>=181);
  let previous=-Infinity;
  for(const point of view.points){
    assert.ok(Number.isFinite(point.angle)&&point.angle>previous,'the display keeps ordered angular samples');previous=point.angle;
    assert.ok(Number.isFinite(point.db)&&point.db>=floor&&point.db<=1e-9);
    const expected=Math.max(floor,20*Math.log10(Math.max(1e-12,response(a,point.angle))));
    assert.ok(Math.abs(point.db-expected)<1e-6,`independent spherical-wave sum at ${point.angle} degrees`);
  }
  const peak=view.points.reduce((a,b)=>a.db>b.db?a:b);
  assert.ok(Math.abs(peak.angle-a.beam.angle_deg)<=.251,'near-field focus peaks at the applied target bearing');
  assert.ok(peak.db>-.02,'the target attains coherent gain without shifting the curve');
  return view;
}
for(const sign of [1,-1])for(const mode of ['auto','follow'])for(const [angle,range] of [[-57.3,2.6],[9.158535137,3.35],[43.7,18]])check(packet(angle,range,sign,mode));
check(packet(),-45);
const a=packet(),baseline=check(a);
const uniformPhase=clone(a);uniformPhase.beam.phase_rad=uniformPhase.beam.phase_rad.map(phase=>wrap(phase+2.21));
assert.equal(JSON.stringify(draw(uniformPhase).points),JSON.stringify(baseline.points),'a common phase cannot alter power response');
const otherCorrection=clone(a);otherCorrection.beam.phase_rad=otherCorrection.beam.phase_rad.map((phase,ch)=>wrap(phase+.27*ch));
assert.equal(JSON.stringify(draw(otherCorrection).points),JSON.stringify(baseline.points),'ideal calibrated theory does not mistake receiver correction phases for geometric steering');
const otherCandidate=clone(a);otherCandidate.v12.localization.candidates[0]={id:'C4',valid:true,x:12,y:1};
assert.equal(JSON.stringify(draw(otherCandidate)),JSON.stringify(baseline),'current candidate cannot replace the target used by the verified ACK');
const opposite=check(packet(a.beam.angle_deg,3.35,-1));
assert.equal(JSON.stringify(opposite.points),JSON.stringify(baseline.points),'reversing the phase convention conjugates the ideal sum, leaving power unchanged');

// Equal bearings with different focal distances need different curves. The
// spherical response also approaches the plane-wave limit as R tends to infinity.
const near=check(packet(20,2.5)),farPacket=packet(20,1e6),far=check(farPacket);
assert.ok(near.points.some((p,i)=>Math.abs(p.db-far.points[i].db)>1),'focal distance affects the near-field pattern');
const origin=centre(farPacket.antennas),k=2*Math.PI*farPacket.rf_hz/lightSpeed;
let limitError=0;
for(const p of far.points){
  let re=0,im=0;
  for(const [x,y]of farPacket.antennas){
    const phase=k*((x-origin[0])*(Math.sin(p.angle*rad)-Math.sin(20*rad))+(y-origin[1])*(Math.cos(p.angle*rad)-Math.cos(20*rad)));
    re+=Math.cos(phase);im+=Math.sin(phase);
  }
  limitError=Math.max(limitError,Math.abs(response(farPacket,p.angle)-Math.hypot(re,im)/6));
}
assert.ok(limitError<2e-5,'large-distance spherical power agrees with the independent plane-wave limit');

for(const [label,change]of [
  ['unverified registers',p=>p.beam.registers_verified=false],
  ['old run ACK',p=>p.beam.applied_run_id='previous'],
  ['pending command',p=>p.beam.status='sent'],
  ['incomplete batch',p=>p.complete=false],
  ['expired locate packet',p=>p.received_at=now-10.01],
  ['future packet',p=>p.received_at=now+.01],
  ['missing target',p=>delete p.beam.target],
  ['nonfinite target',p=>p.beam.target.x=NaN],
  ['missing target coordinate',p=>delete p.beam.target.y],
  ['zero range',p=>{const c=centre(p.antennas);p.beam.target.x=c[0];p.beam.target.y=c[1]}],
  ['ANGLE target with leftover XY',p=>Object.assign(p.beam.target,{kind:'angle',angle_deg:p.beam.angle_deg})],
  ['manual weights',p=>p.beam.mode='manual'],
  ['equal weights',p=>p.beam.mode='equal'],
  ['missing phase ACK',p=>p.beam.phase_rad=[]],
  ['invalid phase convention',p=>p.phase_sign=0]
]){
  const invalid=clone(a);change(invalid);assert.equal(draw(invalid),null,label+' must not display an applied near-field pattern');
}
const identify=clone(a);identify.beam.acquisition.applied_mode='identify';identify.received_at=now-29.9;
assert.ok(draw(identify),'identify mode retains its existing 30-second freshness allowance');
identify.received_at=now-30.1;assert.equal(draw(identify),null);

// Reproduce the reported POSITION geometry numerically, without checking in or
// depending on the large ignored state.json capture and its raw payloads.
const captured=packet();
captured.antennas=[[-1.2823613348729794,0],[-.9361806674364898,0],[-.5900000000000001,0],
  [.6048690640017024,0],[.9361806674364898,0],[1.2823613348729794,0]];
captured.beam.target={x:.5353267179904184,y:3.3050597007404807,source_run_id:'captured-source'};
captured.beam.angle_deg=9.158535137351874;
captured.beam.phase_rad=[0,-1.77102098766437,-2.2287753701477815,-.6773480243904357,-.14580921210479936,-.374689612716983];
const result=check(captured);
assert.ok(Math.abs(result.rangeM-3.347737623043186)<1e-10);
assert.ok(response(captured,captured.beam.angle_deg)>1-1e-14);
console.log('POSITION near-field beam display contract passed');
