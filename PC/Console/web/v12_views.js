'use strict';
// Display transforms only: every angle, ML score and location comes from PS.
function v12Result(aoa){return aoa?.v12?.version==='1.2'?aoa.v12:null;}
// Internal scan6 packets share the measurement page; controls retain wire modes.
function analysisPage(mode){return mode==='scan6'?'angle6':mode;}
function v12FullArrayVisible(aoa,mode=aoa?.processing_mode){
  return !!v12Result(aoa)&&['angle6','scan6'].includes(mode);
}
function v12SelectedCandidate(aoa){
  const loc=v12Result(aoa)?.localization;
  return loc?.candidates?.find(c=>c.id===loc.selected_id&&c.valid)??null;
}
function v12LocalizationMethod(loc){
  if(loc?.selection_method==='triad_ml_energy')return '两组三天线 ML · 路径能量选点';
  if(loc?.selection_method!=='direction_residual_angle')return '联合角关联';
  const value=(n,d)=>Number.isFinite(n)?n.toFixed(d):'—';
  return `方向回代残差＋联合角一致性 · λ ${value(loc.residual_weight,2)} · σθ ${value(loc.angle_sigma_deg,0)}°`;
}
function v12CandidateScoreText(loc,c){
  if(loc?.selection_method==='triad_ml_energy')return `ML Energy ${Number.isFinite(c.energy_share)?`${(c.energy_share*100).toFixed(2)}%`:'—'}`;
  if(loc?.selection_method!=='direction_residual_angle')return '';
  const value=(n,d)=>Number.isFinite(n)?n.toFixed(d):'—';
  const residual=Number.isFinite(c.direction_residual_ratio)?`${(c.direction_residual_ratio*100).toFixed(2)}%`:'—';
  return `R ${residual} · Cost ${value(c.selection_cost,4)} · Δθ ${value(c.angle_error_deg,2)}°`;
}
function v12ScanDomain(scan,search){
  const angles=scan?.angles_deg;
  if(Array.isArray(angles)&&angles.length>=2)return [angles[0],angles.at(-1)];
  return [search.start,search.start+(search.count-1)*search.step];
}
function v12ScanPoints(scan,cx,cy,radius,floor=-40){
  const angles=scan?.angles_deg,db=scan?.relative_db;
  if(!Array.isArray(angles)||!Array.isArray(db)||angles.length!==db.length)return [];
  // Fixed dB radial scale, one vertex per received sample; no interpolation.
  return angles.map((angle,i)=>{
    const r=Math.max(0,Math.min(1,(db[i]-floor)/-floor))*radius,theta=angle*Math.PI/180;
    return [cx+r*Math.sin(theta),cy-r*Math.cos(theta)];
  });
}
function v12AppliedBeamInputs(aoa,now=Date.now()/1000){
  const beam=aoa?.beam,antennas=aoa?.antennas,phases=beam?.phase_rad;
  const age=now-aoa?.received_at,limit=beam?.acquisition?.applied_mode==='identify'?30:10;
  if(!beam||beam.available!==true||beam.registers_verified!==true||beam.applied_run_id!==aoa.run_id||
     !['applied','tracking','holding','paused'].includes(beam.status)||
     !Number.isFinite(age)||age<0||age>limit||aoa.complete!==true||
     !Array.isArray(antennas)||antennas.length!==6||
     !antennas.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite))||
     !Array.isArray(phases)||phases.length!==6||!phases.every(Number.isFinite)||
     !Number.isFinite(aoa.rf_hz)||aoa.rf_hz<=0||![undefined,1,-1].includes(aoa.phase_sign))return null;
  return {beam,antennas,phases};
}
function v12PhaseBeamPattern(aoa,phases,floor=-40){
  const antennas=aoa.antennas;
  // Same far-field array-factor display as the existing phase pattern. The
  // packet supplies phases, not amplitude weights or a measured PL response.
  const ref=antennas.reduce((s,p)=>[s[0]+p[0]/6,s[1]+p[1]/6],[0,0]);
  const k=(aoa.phase_sign||1)*2*Math.PI*aoa.rf_hz/299792458;
  const values=Array.from({length:721},(_,i)=>{
    const angle=-90+i*.25,theta=angle*Math.PI/180;
    let re=0,im=0;
    antennas.forEach(([x,y],ch)=>{
      const phase=k*((x-ref[0])*Math.sin(theta)+(y-ref[1])*Math.cos(theta))+phases[ch];
      re+=Math.cos(phase);im+=Math.sin(phase);
    });
    return {angle,magnitude:Math.hypot(re,im)};
  });
  const peak=Math.max(...values.map(p=>p.magnitude));
  if(!(peak>0))return [];
  return values.map(p=>({angle:p.angle,db:Math.max(floor,20*Math.log10(Math.max(1e-12,p.magnitude/peak)))}));
}
function v12AppliedBeamPattern(aoa,now=Date.now()/1000){
  const input=v12AppliedBeamInputs(aoa,now);
  const basis=input&&v12PlanarBeamBasis(aoa,input);
  if(!basis)return null;
  const {beam,phases}=input;
  const points=v12PhaseBeamPattern(aoa,phases.map((phase,ch)=>phase-basis.calibrationPhase[ch]));
  if(!points.length)return null;
  return {equalAmplitude:true,appliedRunId:beam.applied_run_id,syntheticPreview:beam.synthetic_preview===true,
    appliedAngleDeg:beam.angle_deg,responseBasis:'ideal_phase_calibrated',points};
}
function v12PositionBeamPattern(aoa,floor=-30,now=Date.now()/1000){
  const input=v12AppliedBeamInputs(aoa,now);
  if(!input)return null;
  const {beam,antennas}=input,target=beam.target;
  if(!['auto','follow'].includes(beam.mode)||target?.kind==='angle'||
     !Number.isFinite(beam.angle_deg)||!Number.isFinite(target?.x)||!Number.isFinite(target?.y))return null;
  const origin=antennas.reduce((s,p)=>[s[0]+p[0]/6,s[1]+p[1]/6],[0,0]);
  const rangeM=Math.hypot(target.x-origin[0],target.y-origin[1]);
  const distances=antennas.map(([x,y])=>Math.hypot(target.x-x,target.y-y));
  if(rangeM<.001||distances.some(r=>r<.001))return null;
  const k=(aoa.phase_sign||1)*2*Math.PI*aoa.rf_hz/299792458;
  // Target-matched, equal-amplitude ideal response at the applied focal range.
  // The ACK identifies that target. This does not independently validate the
  // receiver calibration or measure the PL response; raw register phases also
  // contain receiver-chain compensation and cannot be used as ideal weights.
  const points=Array.from({length:721},(_,i)=>{
    const angle=-90+i*.25,theta=angle*Math.PI/180;
    const x=origin[0]+rangeM*Math.sin(theta),y=origin[1]+rangeM*Math.cos(theta);
    const probe=antennas.map(([ax,ay])=>Math.hypot(x-ax,y-ay));
    let re=0,im=0;
    distances.forEach((distance,ch)=>{
      const phase=k*((distance-distances[0])-(probe[ch]-probe[0]));
      re+=Math.cos(phase);im+=Math.sin(phase);
    });
    return {angle,db:Math.max(floor,Math.min(0,20*Math.log10(Math.max(1e-12,Math.hypot(re,im)/6))))};
  });
  return {points,origin,rangeM,appliedAngleDeg:beam.angle_deg,appliedRunId:beam.applied_run_id,
    target:{x:target.x,y:target.y,source_run_id:target.source_run_id},responseBasis:'ideal_phase_calibrated_nearfield'};
}
function v12PlanarBeamBasis(aoa,input){
  if(!Number.isFinite(input.beam.angle_deg))return null;
  const {beam,antennas,phases}=input,ref=antennas[0];
  const angleTarget=['auto','follow'].includes(beam.mode)&&beam.target?.kind==='angle'&&
    Number.isFinite(beam.target.angle_deg)&&Math.abs(beam.target.angle_deg-beam.angle_deg)<1e-6;
  if(beam.mode!=='manual'&&!angleTarget)return null;
  const k=(aoa.phase_sign||1)*2*Math.PI*aoa.rf_hz/299792458;
  const old=beam.angle_deg*Math.PI/180;
  // PS manual contract: geometric steering + arg(c_ch)-arg(c_0).
  // ANGLE auto/follow also call beam_weights(manual), identified by their
  // explicit angle target. Equal and spherical POSITION weights do not.
  const calibrationPhase=antennas.map(([x,y],ch)=>
    phases[ch]-phases[0]+k*((x-ref[0])*Math.sin(old)+(y-ref[1])*Math.cos(old)));
  return {calibrationPhase};
}
function v12ManualBeamPreview(aoa,angle,floor=-40,now=Date.now()/1000){
  const input=v12AppliedBeamInputs(aoa,now);
  const basis=input&&v12PlanarBeamBasis(aoa,input);
  if(!basis||!Number.isFinite(angle))return null;
  const {antennas}=input,ref=antennas[0],draft=angle*Math.PI/180;
  const k=(aoa.phase_sign||1)*2*Math.PI*aoa.rf_hz/299792458;
  const expected=antennas.map(([x,y],ch)=>{
    const phase=basis.calibrationPhase[ch]-k*((x-ref[0])*Math.sin(draft)+(y-ref[1])*Math.cos(draft));
    return Math.atan2(Math.sin(phase),Math.cos(phase));
  });
  // Cancel the modeled receiver-chain phase only for the theoretical response.
  // Keep phaseRad as the original predicted PS register phases, not this model.
  const points=v12PhaseBeamPattern(aoa,expected.map((phase,ch)=>phase-basis.calibrationPhase[ch]),floor);
  return points.length?{angle,phaseRad:expected,sourceRunId:input.beam.applied_run_id,points}:null;
}
const v12StatusLabels={ok:'有效交点',candidate:'有效交点',missing_bearing:'缺少测向',ambiguous_bearing:'测向多解',ambiguous_fit:'拟合多解',weak_path:'路径贡献不足',unknown_power:'缺少路径能量',poor_fit:'拟合残差偏高',poor_bearing_fit:'拟合残差偏高',nonfinite:'无有限交点',nonfinite_geometry:'无有限交点',coincident_centres:'天线组中心重合',near_parallel:'方向接近平行',behind_station:'交点位于射线后方',no_valid_intersection:'无有效交点',no_scored_intersection:'无可评分交点',no_joint_reference:'无联合方向参考'};
