'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const web=path.join(__dirname,'../web');
const context={atob:s=>Buffer.from(s,'base64').toString('binary')};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(web,'joint_scores.js'),'utf8')+'\nthis.Grid=JointScoreGrid;',context);
const rawPacket={max_bearing_residual:.1,joint:{raw_validation:{available:true,passed:true,raw_residual_ratio:.01,latest_residual_ratio:.02}}};
assert.equal(context.jointRawQualityPassed(rawPacket),true);
rawPacket.joint.raw_validation.latest_residual_ratio=.2;
assert.equal(context.jointRawQualityPassed(rawPacket),false);
for(const name of ['gated','quality','fallback']){
  const p=JSON.parse(fs.readFileSync(path.join(__dirname,`fixtures/cov4/packet_${name}.json`),'utf8'));
  assert.equal(context.jointRawQualityPassed(p),name!=='fallback');
}
vm.runInContext(fs.readFileSync(path.join(web,'joint_heatmap.js'),'utf8')+'\nthis.History=JointHeatHistory;',context);
for(const k of [1,2,3]){
  const n=5,size=k===1?n:k===2?10:10;
  const grid=new context.Grid({path_count:k,profile:{angles_deg:[-2,-1,0,1,2]},score_grid:{score_u8:Buffer.from(Array.from({length:size},(_,i)=>i)).toString('base64')}});
  let ix=0;
  for(let i=0;i<n;i++)for(let j=k===1?0:i+1;j<(k===1?1:n);j++)for(let l=k<3?0:j+1;l<(k<3?1:n);l++){
    assert.equal(grid.index(i,j,l),ix);assert.equal(grid.score(i,j,l),ix++/254);
  }
  assert.equal(ix,size);assert.equal(grid.score(-1),null);
  if(k>1)assert.equal(grid.score(1,1),null);
}
const history=new context.History();
for(const [i,k] of [3,2,1,3].entries())history.update({processing_mode:'angle6',session:'same',run_id:String(i),received_at:100+i,rf_hz:445e6,phase_sign:1,antennas:[[0,0]],complete:true,joint:{path_count:k,has_best:true,selection:{method:'single_dual_triple_cascade'},profile:{angles_deg:[-1,0,1],relative_db:[-2,0,-1]}}});
assert.equal(history.rows.length,4);assert.equal(history.rows.map(r=>r.modelPaths).join(','),'3,2,1,3');
assert.equal(history.view().cells.length,4);
history.update({processing_mode:'position'});assert.equal(history.rows.length,0);
console.log('PASS joint visual models: ordered 1D/2D/3D grids and mixed-order history');
