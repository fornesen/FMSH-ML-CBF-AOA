'use strict';
// [六天线显示新增] 仅缓存板端 ML 剖面，不在浏览器重新计算测角。
class JointHeatHistory {
  constructor(){this.key='';this.rows=[];this.maxAge=64;this.maxRows=256;}
  update(a){
    if(!a||(a.processing_mode!=='angle6'&&a.v12?.version!=='1.2')){this.key='';this.rows=[];return;}
    const j=a.joint,p=j?.profile,angles=p?.angles_deg;
    if(!Array.isArray(angles)||angles.length<2||!Number.isFinite(a.received_at))return;
    // Cascade changes model order per frame, not the underlying antenna/search geometry.
    const key=JSON.stringify([a.session,a.rf_hz,a.phase_sign,a.antennas,j.selection?.method||j.path_count,angles]);
    if(key!==this.key){this.key=key;this.rows=[];}
    const last=this.rows.at(-1);
    if(last?.id===a.run_id||last&&a.received_at<=last.time)return;
    const valid=a.complete&&j.has_best&&Array.isArray(p.relative_db)&&
      p.relative_db.length===angles.length&&p.relative_db.every(Number.isFinite);
    this.rows.push({id:a.run_id,time:a.received_at,modelPaths:j.path_count,angles:angles.slice(),
      values:valid?p.relative_db.slice():null});
    this.rows=this.rows.filter(r=>r.time>=a.received_at-this.maxAge).slice(-this.maxRows);
  }
  view(){
    const latest=this.rows.at(-1);
    if(!latest)return null;
    const span=Math.min(this.maxAge,Math.max(5,Math.ceil(latest.time-this.rows[0].time)));
    // 按实际 PC 接收时间排布。每帧最多延伸 1 秒；断流/识别等待留灰，不插值补峰。
    const cells=this.rows.map((row,i)=>{
      const before=this.rows[i-1],after=this.rows[i+1],age=latest.time-row.time;
      return {...row,age,top:Math.max(0,age-Math.min(1,after?(after.time-row.time)/2:.5)),
        bottom:Math.min(span,age+Math.min(1,before?(row.time-before.time)/2:.5))};
    });
    // [显示对比度修正] 整个时间窗共用色阶；保留原始dB，避免逐帧拉伸产生假变化。
    let minimum=0;
    for(const row of cells)for(const value of row.values||[])minimum=Math.min(minimum,value);
    const levels=[1,2,3,5,10,20,40,80];
    const colorFloor=-(levels.find(v=>v>=-minimum-1e-9)||80);
    return {latest,span,cells,colorFloor};
  }
}
