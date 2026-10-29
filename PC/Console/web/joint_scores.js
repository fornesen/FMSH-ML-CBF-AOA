'use strict';
// Quality decisions come from PS; this only applies the displayed control gates.
function jointRawQualityPassed(aoa) {
  if(aoa?.v12?.version==='1.2')return aoa.v12.joint.valid===true;
  const j=aoa?.joint,q=j?.raw_validation,limit=aoa?.max_bearing_residual??.1;
  if(q&&(!q.available||q.passed!==true||![q.raw_residual_ratio,q.latest_residual_ratio].every(v=>Number.isFinite(v)&&v>=0&&v<=limit)))return false;
  const s=j?.selection;
  if(s?.method==='quality_cascade_energy_fallback'){
    const m=s.models?.[j.path_count-1];
    if(!s.accepted||s.fallback||!m?.passed||![m.raw_residual_ratio,m.latest_residual_ratio].every(v=>Number.isFinite(v)&&v>=0&&v<=limit))return false;
  }
  return true;
}
// [联合评分显示新增] 解码 PS 已计算的组合评分；此模块没有测角或波束估计算法。
class JointScoreGrid {
  constructor(joint) {
    this.k=joint.path_count;this.angles=joint.profile.angles_deg;this.n=this.angles.length;
    this.values=Uint8Array.from(atob(joint.score_grid.score_u8),c=>c.charCodeAt(0));
  }
  index(i,j=0,l=0) {
    const n=this.n;
    if(this.k===1)return i;
    if(this.k===2)return i*(2*n-i-1)/2+j-i-1;
    const choose3=x=>x<3?0:x*(x-1)*(x-2)/6;
    return choose3(n)-choose3(n-i)+(j-i-1)*(2*n-i-j-2)/2+l-j-1;
  }
  score(i,j,l) {
    if(i<0||i>=this.n||this.k>=2&&!(i<j&&j<this.n)||this.k===3&&!(j<l&&l<this.n))return null;
    const value=this.values[this.index(i,j,l)];
    return value===255?null:value/254;
  }
  // 仅三维点云抽样以保证旋转流畅；下方切片保留全部原始角度网格点。
  cloud(threshold,limit=3000) {
    const cutoff=Math.ceil(threshold*254);
    let count=0;
    for(const v of this.values)if(v!==255&&v>=cutoff)count++;
    const stride=Math.max(1,Math.ceil(count/limit)),points=[];
    let index=0,seen=0;
    for(let i=0;i<this.n;i++)for(let j=i+1;j<this.n;j++)for(let l=j+1;l<this.n;l++){
      const v=this.values[index++];
      if(v!==255&&v>=cutoff&&seen++%stride===0)points.push([i,j,l,v/254]);
    }
    return {points,count};
  }
}

class JointScoreView {
  constructor() {
    this.grid=null;this.key='';this.slice=null;this.yaw=-.65;this.pitch=.35;this.cloud=null;
    const get=id=>document.getElementById(id);
    this.canvas=get('joint-score');this.section=get('score-panel');this.palette=get('score-palette');
    this.sliceCanvas=get('joint-score-slice');this.slider=get('score-slice');
    this.threshold=get('score-threshold');this.sliceAuto=get('score-slice-auto');
    this.slider.oninput=()=>{this.sliceAuto.checked=false;this.slice=Number(this.slider.value);this.draw();};
    this.sliceAuto.onchange=()=>{if(this.sliceAuto.checked)this.followBest();this.draw();};
    this.threshold.oninput=()=>{this.cloud=null;this.draw();};
    this.palette.onchange=()=>this.draw();
    get('score-reset').onclick=()=>{this.yaw=-.65;this.pitch=.35;this.draw();};
    let drag=null;
    this.canvas.onpointerdown=e=>{
      if(this.grid?.k!==3)return;
      drag=[e.clientX,e.clientY];this.canvas.setPointerCapture(e.pointerId);
    };
    this.canvas.onpointermove=e=>{
      if(!drag)return;
      this.yaw+=(e.clientX-drag[0])*.009;
      this.pitch=Math.max(-1.2,Math.min(1.2,this.pitch+(e.clientY-drag[1])*.009));
      drag=[e.clientX,e.clientY];this.drawVolume();
    };
    this.canvas.onpointerup=this.canvas.onpointercancel=()=>{drag=null;};
  }
  update(aoa,color) {
    this.color=color;this.aoa=aoa;
    const j=aoa?.joint;
    const key=JSON.stringify([aoa?.session,aoa?.run_id,j?.path_count,!!j?.score_grid]);
    if(key!==this.key) {
      const previous=this.grid;
      this.key=key;this.grid=j?.score_grid&&j.has_best&&aoa.complete?new JointScoreGrid(j):null;
      this.cloud=null;
      if(!previous||!this.grid||previous.k!==this.grid.k||
        previous.angles.join()!==this.grid.angles.join())this.sliceAuto.checked=true;
      if(this.sliceAuto.checked)this.followBest();
    }
    this.draw();
  }
  followBest() {
    if(this.grid?.k!==3)return;
    const {angles,n}=this.grid,angle=this.aoa.joint.paths[2].angle_deg;
    this.slice=Math.max(2,Math.min(n-1,Math.round((angle-angles[0])/(angles[1]-angles[0]))));
  }
  context(canvas) {
    const w=Math.max(240,canvas.clientWidth),h=canvas.clientHeight,scale=window.devicePixelRatio||1;
    canvas.width=Math.round(w*scale);canvas.height=Math.round(h*scale);
    const ctx=canvas.getContext('2d');ctx.scale(scale,scale);
    ctx.font='11px Silkscreen, "AOA Chinese", "Microsoft YaHei UI", sans-serif';
    ctx.fillStyle='#454b50';ctx.lineWidth=1;
    return {ctx,w,h};
  }
  label(ctx,text,x,y,align='left') {
    ctx.fillStyle='#454b50';ctx.textAlign=align;ctx.fillText(text,x,y);
  }
  mark(ctx,x,y) {
    if(this.aoa?.joint?.selection?.accepted===false&&!this.aoa.joint.selection.has_candidate)return;
    ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2);ctx.fillStyle='#e3c45c';ctx.fill();
    ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();ctx.lineWidth=1;
    ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.strokeStyle='#d77b45';ctx.stroke();
  }
  draw() {
    const get=id=>document.getElementById(id),g=this.grid,k=g?.k||this.aoa?.joint?.path_count||3;
    const selection=this.aoa?.joint?.selection,v12=this.aoa?.v12?.version==='1.2'?this.aoa.v12:null;
    const experimental=selection?.method==='quality_cascade_energy_fallback';
    get('score-title').textContent=`联合 ML 评分 · ${k} 径`+(this.aoa?.direction_frames===4?' · 四帧':'');
    get('score-reference').hidden=!selection||!!v12;
    get('score-title').title='与联合 ML 历史图来自同一板端 ML 搜索；此图展示当前批次角度组合，历史图展示跨批次角度剖面。同源评分、不同视角，Score 非概率。';
    if(selection&&!v12){
      get('score-single-range').textContent=selection.status==='no_reference'?'—':selection.range_deg.map(v=>`${v.toFixed(1)}°`).join(' ～ ');
      get('score-single-range').title=`单径 ${selection.single_angle_deg.toFixed(1)}° ± ${selection.half_width_deg}°`+(experimental?'；实验方案仅作参考，不作为硬约束':'');
      const peak=Math.max(0,...selection.dual_amplitudes);
      get('score-dual-reference').textContent=selection.dual_angles_deg.map((v,i)=>
        `${v.toFixed(1)}°${selection.dual_amplitudes[i]>0&&selection.dual_amplitudes[i]>=selection.amplitude_ratio*peak?'':'（弱）'}`).join(' / ')||'—';
      get('score-dual-reference').title=experimental?'双径模型参考；仅兜底时按 5° 匹配候选':k===3?`按有效幅度筛选后，角距 ≤ ${selection.dual_tolerance_deg}°`:'高阶参考；本批未采用三径匹配';
      const chosen=selection.accepted||selection.has_candidate?this.aoa.joint.paths[selection.path_index]:null;
      const qualified=chosen&&chosen.active&&this.aoa.complete&&this.aoa.status==='ok'&&
        !this.aoa.joint.ambiguous&&Number.isFinite(this.aoa.joint.residual_ratio)&&
        this.aoa.joint.residual_ratio<=(this.aoa.max_bearing_residual??.1)&&selection.accepted&&jointRawQualityPassed(this.aoa);
      get('score-selected-angle').textContent=qualified?`${chosen.angle_deg.toFixed(1)}°`:
        chosen&&selection.fallback?`${chosen.angle_deg.toFixed(1)}° · 兜底候选，未通过质量`:chosen?'质量未通过 · 无输出':'冲突 · 无输出';
      get('score-selected-angle').style.color='#454b50';
    }
    get('score-dimension').textContent=k===1?'θ₁':k===2?'θ₁ × θ₂':'θ₁ × θ₂ × θ₃';
    get('score-volume-tools').hidden=!g||k!==3;get('score-slice-tools').hidden=!g||k!==3;
    this.sliceCanvas.hidden=!g||k!==3;
    this.canvas.classList.toggle('is-volume',!!g&&k===3);
    this.canvas.setAttribute('aria-label',k===1?'单路径角度评分曲线':k===2?'双路径角度组合热力图':'三路径角度组合三维评分图，拖动旋转');
    const bar=get('score-colorbar');
    this.palette.hidden=k===1;bar.parentElement.hidden=k===1;
    bar.style.background=`linear-gradient(90deg,${Array.from({length:16},(_,i)=>`rgb(${this.color(i/15,this.palette.value).join(',')})`).join(',')})`;
    if(!g){
      get('score-best').textContent='';
      const {ctx,w,h}=this.context(this.canvas);
      this.label(ctx,this.aoa?.joint?.has_best?'等待板端组合评分':'等待有效联合解',w/2,h/2,'center');
      get('score-note').textContent='当前批次 · Score 1−J/E，非概率';return;
    }
    const best=this.aoa.joint.paths.map(p=>`${p.angle_deg.toFixed(1)}°`).join(' / ');
    get('score-best').textContent=v12?`板端 ${k} 径组合 ${best} · 主方向 ${Number.isFinite(v12.joint.angle_deg)?v12.joint.angle_deg.toFixed(2)+'°':'—'}`:`${selection?(selection.accepted?(k===1?'单径退化':'选中组合'):'未通过的候选组合'):'最佳组合'} ${best}`;
    const reasons={no_reference:'无有效参考',no_constrained_candidate:'范围未命中',inactive_candidate:'有效幅度未通过',residual_gap:'残差增量超限'};
    get('score-best').title=selection?.stages?.slice(0,-1).map((reason,i)=>
      `${3-i} 径：${reasons[reason]||reason}`).join('；')||'';
    if(selection?.fallback)get('score-best').textContent+= ' · 兜底候选，不可自动跟踪';
    else if(selection?.stages?.length>1)get('score-best').textContent+=` · ${k===2?'三径未命中':'三／双径未命中'}`;
    get('score-note').textContent=k===3?'角度按 θ₁ < θ₂ < θ₃ 排序 · 灰色为无效域 · 评分非概率':
      k===2?'纵轴 θ₂ 向上增大 · 对称组合等价 · 灰色为退化组合 · 评分非概率':'横轴为角度 · 纵轴为评分 1−J/E，非概率';
    const raw=this.aoa?.joint?.raw_validation;
    if(raw)get('score-note').textContent+=` · 原帧 J/E ${raw.raw_residual_ratio.toFixed(4)} · 末帧 ${raw.latest_residual_ratio.toFixed(4)}`;
    if(k===1)this.drawCurve();
    if(k===2)this.drawMap(this.canvas);
    if(k===3){
      this.slider.min=2;this.slider.max=g.n-1;this.slider.value=this.slice;
      get('score-slice-value').textContent=`${g.angles[this.slice].toFixed(1)}°`;
      get('score-threshold-value').textContent=Number(this.threshold.value).toFixed(2);
      this.drawVolume();this.drawMap(this.sliceCanvas,this.slice);
    }
  }
  drawCurve() {
    const {ctx,w,h}=this.context(this.canvas),g=this.grid,L=45,R=w-20,T=30,B=h-40;
    const xAt=i=>g.n===1?(L+R)/2:L+i/(g.n-1)*(R-L);
    for(const v of [0,.5,1]){
      const y=B-v*(B-T);ctx.strokeStyle='#e6eaea';ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(R,y);ctx.stroke();
      this.label(ctx,String(v),L-7,y+4,'right');
    }
    ctx.strokeStyle='#d77b45';ctx.lineWidth=1.6;ctx.beginPath();
    let connected=false;
    for(let i=0;i<g.n;i++){
      const v=g.score(i);if(v===null){connected=false;continue;}
      const x=xAt(i),y=B-v*(B-T);
      if(connected)ctx.lineTo(x,y);else ctx.moveTo(x,y);connected=true;
    }
    ctx.stroke();ctx.lineWidth=1;
    for(const i of (g.n===1?[0]:[0,Math.floor((g.n-1)/2),g.n-1]))
      this.label(ctx,`${g.angles[i].toFixed(0)}°`,xAt(i),B+21,'center');
    const i=g.n===1?0:Math.round((this.aoa.joint.paths[0].angle_deg-g.angles[0])/(g.angles[1]-g.angles[0]));
    if(g.score(i)!==null)this.mark(ctx,xAt(i),B-g.score(i)*(B-T));
    this.label(ctx,'Score',L,T-10);this.label(ctx,'θ₁',R,B+36,'right');
  }
  drawMap(canvas,slice=null) {
    const {ctx,w,h}=this.context(canvas),g=this.grid,n=g.n,L=47,R=w-22,T=20,B=h-43;
    const image=document.createElement('canvas');image.width=n;image.height=n;
    const pixels=image.getContext('2d'),data=pixels.createImageData(n,n);
    for(let y=0;y<n;y++)for(let x=0;x<n;x++){
      const j=n-1-y;
      const v=slice===null?g.score(Math.min(x,j),Math.max(x,j)):g.score(x,j,slice);
      data.data.set([...(v===null?[235,238,238]:this.color(v,this.palette.value)),255],(y*n+x)*4);
    }
    pixels.putImageData(data,0,0);ctx.imageSmoothingEnabled=false;
    ctx.drawImage(image,L,T,R-L,B-T);
    const px=i=>L+(i+.5)/n*(R-L),py=i=>B-(i+.5)/n*(B-T);
    for(const i of [0,Math.floor((n-1)/2),n-1]){
      this.label(ctx,`${g.angles[i].toFixed(0)}°`,px(i),B+19,'center');
      this.label(ctx,`${g.angles[i].toFixed(0)}°`,L-7,py(i)+4,'right');
    }
    this.label(ctx,'θ₂',L-6,12,'right');this.label(ctx,'θ₁',R,B+36,'right');
    const indices=this.aoa.joint.paths.map(p=>Math.round((p.angle_deg-g.angles[0])/(g.angles[1]-g.angles[0])));
    if(slice===null||slice===indices[2])this.mark(ctx,px(indices[0]),py(indices[1]));
  }
  drawVolume() {
    if(this.grid?.k!==3)return;
    const {ctx,w,h}=this.context(this.canvas),g=this.grid,n=g.n;
    if(!this.cloud)this.cloud=g.cloud(Number(this.threshold.value));
    const size=Math.min(w*.28,(h-90)*.36),cy=h*.48,cx=w*.5;
    const project=v=>{
      const [x,y,z]=v.map(a=>2*a/(n-1)-1),c=Math.cos(this.yaw),s=Math.sin(this.yaw);
      const xx=x*c-y*s,yy=x*s+y*c,cp=Math.cos(this.pitch),sp=Math.sin(this.pitch);
      return [cx+size*xx,cy-size*(z*cp-yy*sp),yy*cp+z*sp];
    };
    const line=(a,b,stroke)=>{
      const p=project(a),q=project(b);ctx.strokeStyle=stroke;ctx.beginPath();ctx.moveTo(p[0],p[1]);ctx.lineTo(q[0],q[1]);ctx.stroke();
    };
    const max=n-1;
    for(let axis=0;axis<3;axis++)for(let a of [0,max])for(let b of [0,max]){
      const p=[0,0,0],q=[0,0,0],other=[0,1,2].filter(i=>i!==axis);
      p[axis]=0;q[axis]=max;p[other[0]]=q[other[0]]=a;p[other[1]]=q[other[1]]=b;
      line(p,q,'#dce3e5');
    }
    const plane=[[0,0,this.slice],[this.slice,this.slice,this.slice],[0,this.slice,this.slice]].map(project);
    ctx.beginPath();plane.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();
    ctx.fillStyle='rgba(108,143,167,.08)';ctx.fill();ctx.strokeStyle='#9db2be';ctx.stroke();
    const points=this.cloud.points.map(p=>({p:project(p.slice(0,3)),v:p[3]})).sort((a,b)=>b.p[2]-a.p[2]);
    ctx.globalAlpha=.28;
    for(const {p,v} of points){ctx.fillStyle=`rgb(${this.color(v,this.palette.value).join(',')})`;ctx.fillRect(p[0]-1.3,p[1]-1.3,2.6,2.6);}
    ctx.globalAlpha=1;
    const best=this.aoa.joint.paths.map(p=>(p.angle_deg-g.angles[0])/(g.angles[1]-g.angles[0])),b=project(best);
    this.mark(ctx,b[0],b[1]);
    for(let axis=0;axis<3;axis++){
      const end=[0,0,0];end[axis]=max;
      line([0,0,0],end,'#8899a2');
      const p=project(end),offset=axis===0?[-8,18]:axis===1?[8,18]:[0,-13];
      this.label(ctx,`θ${['₁','₂','₃'][axis]} ${g.angles.at(-1)}°`,p[0]+offset[0],p[1]+offset[1],'center');
      const middle=[0,0,0];middle[axis]=max/2;
      const mid=project(middle);
      this.label(ctx,`${((g.angles[0]+g.angles.at(-1))/2).toFixed(0)}°`,mid[0]-5,mid[1]+12,'right');
    }
    const origin=project([0,0,0]);
    this.label(ctx,`${g.angles[0]}°`,origin[0],origin[1]+15,'center');
    this.label(ctx,`${g.angles[0]}° … ${g.angles.at(-1)}°`,16,20);
    this.label(ctx,'拖动旋转',w-16,20,'right');
    this.label(ctx,`显示 ${points.length} / ${this.cloud.count} 个网格点`,w/2,h-12,'center');
  }
}
