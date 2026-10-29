/* 上位机 UI：只渲染服务已校验的数据。候选点选择不向硬件发送控制命令。 */
'use strict';
const $=id=>document.getElementById(id), ns='http://www.w3.org/2000/svg';
const rad=Math.PI/180, color={orange:'#d77b45',blue:'#6c8fa7',yellow:'#e3c45c'};
const iqZeroOrange='#e88a4b';
const labels={candidate:'路径组合候选',weak_path:'包含弱路径',ambiguous_fit:'测向存在多解',poor_fit:'拟合残差偏高',near_parallel:'方向接近平行',behind_station:'交点位于射线后方',coincident_centres:'天线组中心重合',nonfinite:'无有限交点'};
const handoffs={sent:'等待同批 NPU 结果',busy:'NPU 忙，本批未交付',rate_limited:'定位优先，本批不触发识别',absent:'NPU 未启动，本批未交付',disabled:'本批禁用 NPU 交接',error:'NPU 交接错误',not_ready:'未形成四轮 NPU 输入',unsupported_sample_rate:'当前采样率不适用于 v6 模型'};
let state=null,liveState=null,selected=0,frozen=false,heatKey='',signalKey='',fetching=false,controlPosting=false,controlError='',beamFormInitialized=false;
// [六天线测角新增] Analysis Mode与旧定位/识别采集开关分离；仅使用板端 joint 数据。
let processingMode='position',jointPathCount=3,jointSelected=0,jointManual=false,jointKey='',processingAwait=false,processingError='';
// [快速扫描新增] 扫描峰选择与手动角度是纯显示状态；只有用户点“应用”才下发波束命令。
let scanSelected=0,scanManual=false,scanKey='';
let observationRequest=null,observationError='';
let archiveRequest=null,archiveError='',archiveSession='';
const jointHeatHistory=new JointHeatHistory();
const jointScoreView=new JointScoreView();
const beamSpectrumHistory=new BeamSpectrumHistory();
let waterfallPaintKey='',frozenWaterfallView=null;
let waveFrame=null,wavePart=0,waveStart=0,waveDrag=null,waveSession='';
const fmt=(v,d=2)=>Number.isFinite(v)?v.toFixed(d):'—';
const text=(id,value)=>{$(id).textContent=value;};
function el(tag,attrs={},content){const node=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))node.setAttribute(k,v);if(content!==undefined)node.textContent=content;return node;}
function add(parent,tag,attrs,content){const node=el(tag,attrs,content);parent.append(node);return node;}
function svgSize(svg){const w=Math.max(260,svg.clientWidth),h=svg.clientHeight||100;svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.replaceChildren();return [w,h];}
function blank(svg,message){const [w,h]=svgSize(svg);add(svg,'text',{x:w/2,y:h/2,'text-anchor':'middle',fill:'#626b72','font-size':12},message);}
function renderGeometry(a){
  const numbers=$('geometry').querySelectorAll('strong');
  if(a?.antennas?.length!==6){for(const node of numbers)node.textContent='—';return;}
  const distance=(i,k)=>Math.hypot(a.antennas[i][0]-a.antennas[k][0],a.antennas[i][1]-a.antennas[k][1]);
  numbers[0].textContent=`${fmt(a.rf_hz/1e6,3)} MHz`;
  numbers[1].textContent=`${fmt(distance(1,0),4)} / ${fmt(distance(4,3),4)} m`;
  numbers[2].textContent=`${fmt(distance(3,2),4)} m`;
}
function group(id){return state?.aoa?.groups.find(g=>g.id===id);}
const processingData=()=>(liveState||state)?.beam?.processing;
const scanData=()=>state?.aoa?.beam_scan;
function processingAvailable(){const p=processingData();return Boolean(p&&p.available===true);}
function processingPending(){const p=processingData(),c=(liveState||state)?.control;return Boolean(processingAwait||p?.command_status==='pending'||(c?.status==='sent'&&c?.mode==='processing'));}
// [快速扫描新增] 旧 PS 没有能力列表，不能凭猜测放出 scan6；只有新版显式声明才显示。
function supportedProcessingModes(){
  const p=processingData(),modes=p?.supported_modes;
  if(Array.isArray(modes)&&modes.length)return modes.filter(mode=>['position','angle6','scan6'].includes(mode));
  return ['position','angle6'];
}
function processingSupported(mode){return supportedProcessingModes().includes(mode);}
const processingLabels={position:'Position',angle6:'Angle',scan6:'Angle'};
function syncProcessingState(){
  if(processingAwait)return;
  const c=(liveState||state)?.control;
  if(c?.status==='sent'&&c?.mode==='processing')return;
  const p=processingData(),modes=supportedProcessingModes();
  if(!p||p.available!==true||!modes.length){processingMode='position';return;}
  if(p.command_status==='pending'){
    if(modes.includes(p.mode))processingMode=p.mode;
    const n=Number(p.joint_paths);if([1,2,3].includes(n))jointPathCount=n;
    return;
  }
  const applied=state?.aoa?.processing_mode||p.applied_mode||p.mode;
  processingMode=modes.includes(applied)?applied:(modes.includes('position')?'position':modes[0]);
  const n=Number(p.applied_joint_paths??p.joint_paths);
  if([1,2,3].includes(n))jointPathCount=n;
}
function layoutProcessing(){
  const angle=processingMode==='angle6',scan=processingMode==='scan6',v12=!!v12Result(state?.aoa);
  const fullArray=v12FullArrayVisible(state?.aoa,processingMode);
  $('aoa-console-preview').classList.toggle('is-v12',v12);
  $('location').parentElement.classList.toggle('ao-position',!angle&&!scan);
  $('location').hidden=angle||scan;$('angle6').hidden=!angle&&!fullArray;$('scan6').hidden=!scan||v12;
  $('heat-panel').hidden=angle||scan;
  $('score-panel').hidden=!angle&&!fullArray;
  $('v12-polar-legend').hidden=!fullArray;
  $('angle-beam-info').hidden=true;
  $('joint-note').hidden=v12;
  $('angle-polar-key').hidden=!fullArray;
  $('angle-scan-pane').hidden=!fullArray;
  $('angle-history-grid').classList.toggle('is-v12-history',fullArray);
  $('v12-localization').hidden=!v12;
  text('joint-panel-title',v12?'测角 · ML 与 CBF 空间谱':'测角');
  const option=$('selection-mode').querySelector('option[value="auto"]');
  if(option)option.textContent=v12?'Board Selection':'Auto: Highest LoS Score';
  const preview=$('beam').querySelector('option[value="preview"]');
  if(preview){preview.disabled=angle||scan;if((angle||scan)&&$('beam').value==='preview')$('beam').value='current';}
}
function joint(){return state?.aoa?.joint;}
function jointPathList(){return Array.isArray(joint()?.paths)?joint().paths:[];}
function bestJointPathIndex(){
  const v12=v12Result(state?.aoa);
  if(v12)return v12.joint.valid?v12.joint.path_index:-1;
  const selection=joint()?.selection;
  if(selection)return selection.accepted||selection.has_candidate?selection.path_index:-1;
  const paths=jointPathList();let best=-1,power=-Infinity;
  for(let i=0;i<paths.length;i++){
    const p=paths[i];
    if(!p||!Number.isFinite(p.angle_deg)||p.active===false)continue;
    const v=Number.isFinite(p.energy_share)?p.energy_share:(Number.isFinite(p.relative_power_db)?p.relative_power_db:-Infinity);
    if(v>power){power=v;best=i;}
  }
  if(best<0)for(let i=0;i<paths.length;i++)if(paths[i]&&Number.isFinite(paths[i].angle_deg)){best=i;break;}
  return best<0?0:best;
}
function selectedJointPath(){const paths=jointPathList();if(!paths.length)return null;if(jointSelected<0||jointSelected>=paths.length)jointSelected=bestJointPathIndex();return paths[jointSelected]||null;}
function choose(i){$('selection-mode').value='manual';selected=i;renderPlot(true);renderInfo();renderBeamControls();}
function autoSelect(){
  if($('selection-mode').value!=='auto')return;
  const cs=state?.candidates||[];
  let index=cs.findIndex(c=>c.id===state?.auto_candidate_id);
  if(v12Result(state?.aoa)){selected=index<0?0:index;return;}
  if(index<0){
    // [自动显示新增] 无合格定位时显示评分最高的组合及拒绝原因，不把它当成有效位置。
    const ranked=cs.map((c,i)=>({c,i})).filter(v=>Number.isFinite(v.c.energy_share));
    ranked.sort((a,b)=>b.c.energy_share-a.c.energy_share);index=ranked[0]?.i??0;
  }
  selected=index;
}
function renderPlot(animate=false){
  const svg=$('plot'),a=state?.aoa,cs=state?.candidates||[];
  if(!a){blank(svg,'等待板端测向数据');return;}
  const [w,h]=svgSize(svg),left=46,right=w-20,top=25,bottom=h-44;
  const trajectoryMode=$('plot-mode').value==='trajectory';
  const trail=trajectoryMode?(state.trajectory||[]).filter(p=>p.session===a.session):[];
  const v12=v12Result(a),valid=cs.filter(c=>Number.isFinite(c.x)&&Number.isFinite(c.y)),trailPoints=trail.filter(p=>p.valid),auto=$('range').value==='auto';
  const xs=[...a.antennas.map(p=>p[0]),...(auto?[...valid,...trailPoints].map(c=>c.x):[])];
  const centre=(Math.min(...xs)+Math.max(...xs))/2,origin=a.antennas.reduce((s,p)=>s+p[0]/6,0);
  const ymax=auto?Math.min(10000,Math.max(2,Math.hypot(a.centres[1][0]-a.centres[0][0],a.centres[1][1]-a.centres[0][1])*1.5,...[...valid,...trailPoints].map(c=>c.y*1.17))):Number($('range').value);
  const scale=Math.min((bottom-top)/ymax,(right-left)/(Math.max(...xs)-Math.min(...xs)+.7));
  const xcentre=auto?centre:origin,px=x=>(left+right)/2+(x-xcentre)*scale,py=y=>bottom-y*scale;
  const maxy=(bottom-top)/scale,xmin=xcentre-(right-left)/2/scale,xmax=xcentre+(right-left)/2/scale;
  const step0=maxy/5,base=10**Math.floor(Math.log10(step0)),step=[1,2,5,10].map(v=>v*base).find(v=>v>=step0);
  for(let y=0;y<=maxy;y+=step){add(svg,'line',{x1:left,y1:py(y),x2:right,y2:py(y),stroke:y===0?'#d1d6d7':'#edf0f0'});add(svg,'text',{x:left-8,y:py(y)+4,'text-anchor':'end'},fmt(y,step<1?1:0));}
  for(let x=Math.ceil(xmin/step)*step;x<=xmax;x+=step){add(svg,'line',{x1:px(x),y1:top,x2:px(x),y2:bottom,stroke:'#edf0f0','stroke-dasharray':Math.abs(x)<1e-8?'4 5':'none'});add(svg,'text',{x:px(x),y:bottom+32,'text-anchor':'middle'},fmt(x,step<1?1:0));}
  add(svg,'text',{x:left,y:13},'Y / m');add(svg,'text',{x:right,y:h-3,'text-anchor':'end'},'X / m');
  const defs=add(svg,'defs'),clip=add(defs,'clipPath',{id:'plot-clip'});add(clip,'rect',{x:left,y:top,width:right-left,height:bottom-top});
  const marks=add(svg,'g',{'clip-path':'url(#plot-clip)'}),candidate=v12?v12SelectedCandidate(a):cs[selected];
  const inside=p=>px(p.x)>=left&&px(p.x)<=right&&py(p.y)>=top&&py(p.y)<=bottom;
  let offscreen=0;
  // [轨迹新增] 使用服务端保留的真实估计；跨失效批次、会话断开、长时间空窗不连线。
  let previous=null;
  for(const [i,p] of trail.entries()){
    if(!p.valid){previous=null;continue;}
    // [轨迹排版修正] 视野外的估计保留数据和计数，但不画穿过画面的截断长线。
    if(!inside(p)){offscreen++;previous=null;continue;}
    // [轨迹淡出新增] 真实时间指数衰减（90 秒时间常数），不删除保存的数据。
    const opacity=.9*Math.exp(-Math.max(0,(state.server_time||Date.now()/1000)-p.time)/90);
    if(previous&&previous.session===p.session&&p.time-previous.time<120)
      add(marks,'line',{x1:px(previous.x),y1:py(previous.y),x2:px(p.x),y2:py(p.y),stroke:color.blue,'stroke-width':1.7,'stroke-opacity':opacity*.75,'data-role':'trajectory-segment'});
    const dot=add(marks,'circle',{cx:px(p.x),cy:py(p.y),r:2.4,fill:color.blue,opacity,'data-role':'trajectory-point'});
    add(dot,'title',{},`${p.candidate_id} · ${p.run_id} · ${fmt(p.x,3)}, ${fmt(p.y,3)} m${Number.isFinite(p.energy_share)?` · LoS score ${fmt(p.energy_share*100,1)}%`:''}`);
    previous=p;
  }
  const mode=$('beam').value;
  const applied=a.beam,known=applied?.registers_verified&&applied.applied_run_id===a.run_id;
  const appliedModel=mode==='current'&&v12?(v12PositionBeamPattern(a)||v12AppliedBeamPattern(a)):null;
  if(mode!=='off'&&(mode==='equal'||mode==='current'&&known&&(!v12||appliedModel)||mode==='preview'&&candidate?.x!==null&&candidate)){
    const target=mode==='preview'?Math.atan2(candidate.x-origin,candidate.y):0;
    const k=(a.phase_sign||1)*2*Math.PI*a.rf_hz/299792458;
    const phases=mode==='current'?applied.phase_rad:a.antennas.map(([x,y])=>
      -k*((x-origin)*Math.sin(target)+y*Math.cos(target)));
    const pattern=appliedModel?.points||jointBeamPattern(a,phases);
    const [ox,oy]=appliedModel?.origin||[origin,0],rmax=maxy*.32;
    const points=pattern.map(p=>{const r=Math.max(0,(p.db+30)/30)*rmax;return `${px(ox+r*Math.sin(p.angle*rad))},${py(oy+r*Math.cos(p.angle*rad))}`;});
    const shape=add(marks,'path',{d:`M ${px(ox)} ${py(oy)} L ${points.join(' L ')} Z`,fill:color.orange,'fill-opacity':.075,stroke:color.orange,'stroke-width':1.7,'stroke-dasharray':mode==='preview'?'3 3':'none','data-role':'beam-pattern'});
    if(appliedModel?.rangeM)add(shape,'title',{},`已应用目标的定距等幅理论响应，R ${fmt(appliedModel.rangeM,3)} m；不代表实测 PL 响应或独立校准验证。`);
    add(svg,'text',{x:right-6,y:top+12,'text-anchor':'end',fill:color.orange},mode==='current'?`已应用 ${fmt(applied.angle_deg,1)}° / ${appliedModel?.rangeM?`近场理论响应 · R ${fmt(appliedModel.rangeM,2)} m`:'理论方向图'}`:mode==='preview'?'指向预览':'等权参考');
    if(mode==='current'){
      const angle=applied.angle_deg*rad;
      add(marks,'line',{x1:px(ox),y1:py(oy),x2:px(ox+maxy*2*Math.sin(angle)),y2:py(oy+maxy*2*Math.cos(angle)),stroke:color.orange,'stroke-width':1.6,'stroke-dasharray':'8 5',opacity:.65,'data-role':'beam-axis'});
      const target=applied.target;
      if(target){
        const g=add(marks,'g',{'data-role':'applied-beam-target'});
        add(g,'rect',{x:px(target.x)-8,y:py(target.y)-8,width:16,height:16,fill:'none',stroke:color.orange,'stroke-dasharray':'3 2','stroke-width':1.4});
        add(g,'title',{},`已应用波束目标 (${fmt(target.x,3)}, ${fmt(target.y,3)}) m · 测向批次 ${target.source_run_id}`);
      }
    }
  }
  const search=a.search,coverage=[search.start,search.start+(search.count-1)*search.step];
  for(let g=0;g<2;g++){
    const [sx,sy]=a.centres[g];
    for(const angle of coverage)add(marks,'line',{x1:px(sx),y1:py(sy),x2:px(sx+maxy*3*Math.sin(angle*rad)),y2:py(sy+maxy*3*Math.cos(angle*rad)),stroke:'#dbe0e1','stroke-width':.7,'stroke-dasharray':'2 6'});
    for(const p of group(g)?.paths||[]){const active=candidate?.paths[g]===p.id;add(marks,'line',{x1:px(sx),y1:py(sy),x2:px(sx+maxy*5*Math.sin(p.angle_deg*rad)),y2:py(sy+maxy*5*Math.cos(p.angle_deg*rad)),stroke:g?color.blue:color.orange,'stroke-width':active?1.6:1,'stroke-opacity':trajectoryMode?(active ? .28 : .04):(active?1:.18),'stroke-dasharray':p.id?'6 5':'none'});}
    // [轨迹排版修正] 两组靠近时，完整通道列表会挤在同一处；轴下只标组号。
    add(svg,'text',{x:px(sx)+(g?9:-9),y:py(sy)+18,'text-anchor':g?'start':'end'},`G${g+1}`);
  }
  a.antennas.forEach(([x,y],i)=>add(svg,'rect',{x:px(x)-3,y:py(y)-3,width:6,height:6,rx:1,fill:group(0)?.channels.includes(i)?color.orange:color.blue}));
  const corner=(r,l)=>`M ${-r} ${-r+l} V ${-r} H ${-r+l} M ${r-l} ${-r} H ${r} V ${-r+l} M ${r} ${r-l} V ${r} H ${r-l} M ${-r+l} ${r} H ${-r} V ${r-l}`;
  cs.forEach((c,i)=>{
    if(!Number.isFinite(c.x)||!Number.isFinite(c.y))return;
    const active=v12?c.id===v12.localization.selected_id:i===selected,g=add(marks,'g',{transform:`translate(${px(c.x)} ${py(c.y)})`,role:'button',tabindex:0,'data-role':v12?'v12-localization-candidate':'localization-candidate','data-candidate-id':c.id,'data-board-selected':v12?String(active):'false','aria-label':`${c.id} ${v12StatusLabels[c.status]||labels[c.status]||c.status} X ${fmt(c.x,3)} Y ${fmt(c.y,3)}`});
    add(g,'title',{},`${c.id} · ${v12StatusLabels[c.status]||labels[c.status]||c.status}${v12&&c.id===v12.localization.selected_id?' · 板端最终定位':''}`);add(g,'rect',{x:-17,y:-17,width:34,height:34,fill:'transparent'});
    g.addEventListener('click',()=>choose(i));g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose(i);}});
    const symbol=add(g,'g',{opacity:active?1:.26,class:'ao-lock-brackets'});
    add(symbol,'path',{d:corner(active?15:5,active?6:3),fill:'none',stroke:active?color.orange:color.blue,'stroke-width':active?1.6:1});
    add(symbol,'rect',{x:-1.5,y:-1.5,width:3,height:3,fill:active?color.yellow:color.blue});
    if(!active)add(g,'text',{x:10,y:-9,class:'ao-candidate-label',opacity:v12?.75:.28},c.id);
    if(active&&animate&&!matchMedia('(prefers-reduced-motion: reduce)').matches)symbol.animate([{transform:'scale(1.65)'},{transform:'scale(1)'}],{duration:260,easing:'cubic-bezier(.2,.8,.2,1)'});
  });
  if(candidate?.x!==null&&candidate&&trajectoryMode){
    add(marks,'text',{x:px(candidate.x)+18,y:py(candidate.y)-14,class:'ao-lock-text ao-lock-power'},v12?`${candidate.id} · 板端选中`:`${candidate.id} · ${fmt(candidate.energy_share*100,1)}%`);
  }
  if(candidate?.x!==null&&candidate&&!trajectoryMode){
    const tx=px(candidate.x),ty=py(candidate.y);
    if(tx>=left&&tx<=right&&ty>=top&&ty<=bottom){
      const lw=Math.min(216,right-left-10),lh=81;
      const options=[{x:tx+27,y:ty-39},{x:tx-lw-27,y:ty-39},{x:tx+27,y:ty+20},{x:tx-lw-27,y:ty+20},{x:left+8,y:top+24}];
      const available=options.filter(o=>o.x>=left+4&&o.x+lw<=right-4&&o.y>=top+4&&o.y+lh<=bottom-4);
      const overlaps=o=>valid.filter(c=>Math.abs(px(c.x)-(o.x+lw/2))<lw/2+15&&Math.abs(py(c.y)-(o.y+lh/2))<lh/2+15).length;
      available.sort((a,b)=>overlaps(a)-overlaps(b));const label=available[0]||{x:left+5,y:top+25};
      const edge=label.x>tx?label.x:label.x+lw;
      add(marks,'path',{d:`M ${tx+(edge>tx?18:-18)} ${ty} L ${edge} ${Math.max(label.y+8,Math.min(label.y+lh-8,ty))}`,fill:'none',stroke:color.orange,'stroke-opacity':.5,'stroke-dasharray':'3 3','stroke-width':.8});
      const caption=add(marks,'g',{transform:`translate(${label.x} ${label.y})`,'pointer-events':'none'});
      // [界面调整] 信息背景透明，保留引线和统一像素字体。
      add(caption,'text',{x:0,y:11,class:'ao-lock-text ao-lock-title'},`${candidate.id} / ${v12?'BOARD SELECTED':$('selection-mode').value==='auto'?'MAX LOS SCORE':'SELECTED'}`);
      for(let g=0;g<2;g++){const channels=group(g)?.channels||[];add(caption,'text',{x:0,y:31+g*18,class:'ao-lock-text'},`G${g+1}/P${candidate.paths[g]+1} CH${channels.join('/')}`);add(caption,'text',{x:lw,y:31+g*18,'text-anchor':'end',class:'ao-lock-text ao-lock-power'},`${fmt(candidate.powers?.[g]??group(g)?.paths[candidate.paths[g]]?.relative_power_db,1)} dB`);}
      // [上位机新增] 能量归一化权重，与 NPU 分类置信度及几何有效性分开。
      add(caption,'text',{x:0,y:72,class:'ao-lock-energy'},v12?'Position Output':'LOS SCORE (ENERGY)');
      add(caption,'text',{x:lw,y:72,'text-anchor':'end',class:'ao-lock-text ao-lock-power'},v12?'板端选中':Number.isFinite(candidate.energy_share)?`${fmt(candidate.energy_share*100,1)}%`:'—');
    }
  }
  if(trajectoryMode&&offscreen)add(svg,'text',{x:left+6,y:top+15},`轨迹：${offscreen} 点位于视野外`);
  if(candidate?.x!==null&&candidate&&!inside(candidate))add(svg,'text',{x:left+6,y:top+31},`${candidate.id} 在视野外 (${fmt(candidate.x,1)}, ${fmt(candidate.y,1)}) m`);
}
function renderInfo(){
  const a=state?.aoa,cs=state?.candidates||[],c=cs[selected],v12=v12Result(a);
  const trajectoryMode=$('plot-mode').value==='trajectory';
  $('trajectory-lock').hidden=!trajectoryMode;
  kv($('trajectory-lock'),c?[['G1 路径 / 通道',`P${c.paths[0]+1} · CH${(group(0)?.channels||[]).join('/')}`],['G1 Relative Power',`${fmt(c.powers?.[0]??group(0)?.paths[c.paths[0]]?.relative_power_db,1)} dB`],['G2 路径 / 通道',`P${c.paths[1]+1} · CH${(group(1)?.channels||[]).join('/')}`],['G2 Relative Power',`${fmt(c.powers?.[1]??group(1)?.paths[c.paths[1]]?.relative_power_db,1)} dB`]]:[]);
  $('picker').replaceChildren();cs.forEach((v,i)=>{const b=document.createElement('button');b.textContent=v.id;b.setAttribute('aria-pressed',i===selected);b.title=v12StatusLabels[v.status]||labels[v.status]||v.status;b.onclick=()=>choose(i);$('picker').append(b);});
  text('candidate-name',c?`${c.id} · 候选交点`:'候选交点');text('candidate-pair',c?`G1/P${c.paths[0]+1} × G2/P${c.paths[1]+1}`:'等待两组有效测向');text('candidate-x',fmt(c?.x,3));text('candidate-y',fmt(c?.y,3));
  const auto=$('selection-mode').value==='auto';
  text('candidate-note',c?`${auto?(state.auto_candidate_id?'自动：最高评分有效候选 · ':'自动：无合格定位 · '):''}${labels[c.status]} · 交叉角 ${fmt(c.crossing_deg,1)}° · LoS score ${fmt(c.energy_share*100,1)}%（能量评分）`:'等待两组测向结果');
  const target=a?.beam?.target;
  text('beam-note',a?.beam?.registers_verified?`橙色虚线：已应用指向${target?`，目标 (${fmt(target.x,2)}, ${fmt(target.y,2)}) m，依据上一批测向`:''}；方向图 −30～0 dB，非实测场强。`:'本批无已校验相位；可查看等权参考或指向预览。');
  const trajectory=(state?.trajectory||[]).filter(p=>p.session===a?.session);
  text('trajectory-note',$('plot-mode').value==='trajectory'?`蓝线：每批最高评分的有效定位估计 · ${trajectory.filter(p=>p.valid).length} 点 · ${trajectory.filter(p=>!p.valid).length} 个失效批次断线 · 越早越淡`:'');
  if(v12){
    text('candidate-name',c?`${c.id} · ${c.id===v12.localization.selected_id?'板端选中':'查看候选'}`:'候选交点');
    text('candidate-note',c?`${v12StatusLabels[c.status]||c.status} · 交叉角 ${fmt(c.crossing_deg,1)}° · ${c.id===v12.localization.selected_id?'板端最终定位':'查看候选交点'}`:'等待板端候选交点');
    text('trajectory-note',trajectoryMode?`蓝线：各批板端选中位置 · ${trajectory.filter(p=>p.valid).length} 点 · ${trajectory.filter(p=>!p.valid).length} 个失效批次断线`:'');
    renderV12Localization(v12);
  }
  $('rays').replaceChildren();
  for(let g=0;g<2;g++)for(const p of group(g)?.paths||[]){const row=document.createElement('div');row.className='ao-ray'+(c?.paths[g]===p.id?' is-selected':'');const line=document.createElement('i');line.style.borderColor=g?color.blue:color.orange;line.style.borderTopStyle=p.id?'dashed':'solid';const power=document.createElement('em');power.textContent=`${fmt(p.relative_power_db,1)} dB`;row.append(line,`G${g+1} · P${p.id+1}　${fmt(p.angle_deg,1)}°`,power);$('rays').append(row);}
  if(!a)return;
  text('coverage',`正前方 0° · 搜索 ${a.search.start}°～${fmt(a.search.start+(a.search.count-1)*a.search.step,1)}°`);
}
function renderV12Localization(v12){
  const loc=v12.localization,chosen=v12SelectedCandidate(state?.aoa);
  text('v12-localization-summary',`${chosen?`板端最终定位 ${chosen.id} · X ${fmt(chosen.x,3)} / Y ${fmt(chosen.y,3)} m`:'无有效定位点'} · ${v12LocalizationMethod(loc)}`);
  const list=$('v12-candidates');list.replaceChildren();
  for(const c of loc.candidates){
    const card=document.createElement('div');card.className='ao-v12-candidate'+(c.id===loc.selected_id?' is-board-selected':'');
    card.dataset.candidateId=c.id;card.dataset.boardSelected=String(c.id===loc.selected_id);
    const name=document.createElement('b');name.textContent=`${c.id} · G1/P${c.paths[0]+1} × G2/P${c.paths[1]+1}${c.id===loc.selected_id?' · 板端选中':''}`;
    const point=document.createElement('span');point.textContent=`X ${fmt(c.x,3)} / Y ${fmt(c.y,3)} m · ${v12StatusLabels[c.status]||c.status}`;
    const angle=document.createElement('span');angle.textContent=`交叉角 ${fmt(c.crossing_deg,1)}° · ${c.id===loc.selected_id?'板端最终定位':'候选交点'}`;
    card.append(name,point,angle);
    const scoreText=v12CandidateScoreText(loc,c);
    if(scoreText){
      const score=document.createElement('span');score.textContent=scoreText;
      if(loc.selection_method==='triad_ml_energy')score.title='两组 ML 路径组内归一化能量的乘积；相对能量评分，不是定位概率。';
      card.append(score);
    }
    list.append(card);
  }
}
// [测角方向图新增] 仅以实际阵元位置和已校验相位计算阵列因子，不改变板端权值。
function jointBeamPattern(a,phases,floor=-30){
  const antennas=a.antennas;
  if(!Array.isArray(antennas)||antennas.length!==6||
     !antennas.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite))||
     !Array.isArray(phases)||phases.length!==6||!phases.every(Number.isFinite)||
     !Number.isFinite(a.rf_hz)||a.rf_hz<=0)return [];
  const ref=antennas.reduce((s,p)=>[s[0]+p[0]/6,s[1]+p[1]/6],[0,0]);
  const k=(a.phase_sign||1)*2*Math.PI*a.rf_hz/299792458;
  return Array.from({length:721},(_,i)=>{
    const angle=-90+i*.25,t=angle*rad;
    let re=0,im=0;
    antennas.forEach(([x,y],ch)=>{
      const phase=k*((x-ref[0])*Math.sin(t)+(y-ref[1])*Math.cos(t))+phases[ch];
      re+=Math.cos(phase);im+=Math.sin(phase);
    });
    const db=Math.max(floor,Math.min(0,20*Math.log10(Math.max(1e-12,Math.hypot(re,im)/6))));
    return {angle,db};
  });
}
function manualBeamRange(){
  const search=(liveState||state)?.aoa?.search;
  return search?[search.start,search.start+(search.count-1)*search.step]:[-80,80];
}
function manualBeamAngle(){
  const value=$('beam-angle').value,[lo,hi]=manualBeamRange(),raw=Number(value),angle=Math.round(raw*10)/10;
  return value.trim()!==''&&Number.isFinite(raw)&&raw>=lo&&raw<=hi&&angle>=lo&&angle<=hi?angle:null;
}
function drawManualBeam(frame,a,floor=-40){
  const angle=manualBeamAngle();
  if($('beam-mode').value!=='manual'||angle===null||!a)return;
  const preview=v12ManualBeamPreview(a,angle,floor);
  if(!preview)return;
  const points=preview.points.map(p=>frame.at(p.angle,(p.db-floor)/-floor*frame.radius).join(','));
  if(!points.length)return;
  const curve=add(frame.svg,'path',{d:`M ${frame.cx} ${frame.cy} L ${points.join(' L ')} Z`,fill:color.orange,'fill-opacity':.02,stroke:color.orange,'stroke-width':1.8,'stroke-opacity':.55,'stroke-dasharray':'6 5','data-role':'manual-beam-preview'});
  add(curve,'title',{},`预选 ${fmt(angle,1)}° · 理想校准·等幅理论响应；非实测`);
  polarDirection(frame,angle,color.orange,'4 5',1.2,.65,'manual-beam-preview-axis');
}
function manualBeamChanged(){
  beamFormInitialized=true;
  const angle=manualBeamAngle();
  if(angle!==null)$('beam-angle-slider').value=String(angle);
  renderBeamControls();
  const a=state?.aoa;
  if(processingMode!=='angle6'||!a)return;
  const v12=v12Result(a);
  if(v12)renderV12BeamPolar(v12AppliedBeamPattern(a),$('angle-layer-beam').checked);
  else renderAngle6Plot(a,joint(),jointPathList(),selectedJointPath());
}
function renderAngle6Plot(a,j,paths,selected){
  const svg=$('angle6-plot');
  if(!a||!j||!paths.length){blank(svg,'等待板端联合测角数据');return;}
  // [六天线视图调整] 参考中心下移，整个上半平面用于测角，避免只占画布中间一小块。
  const [w,h]=svgSize(svg),cx=w/2,cy=h-58,radius=Math.min((w-110)/2,h-98);
  const ref=Array.isArray(j.reference_xy)&&j.reference_xy.length===2&&j.reference_xy.every(Number.isFinite)
    ? j.reference_xy : a.antennas.reduce((s,p)=>[s[0]+p[0]/a.antennas.length,s[1]+p[1]/a.antennas.length],[0,0]);
  const point=(angle,r)=>[cx+r*Math.sin(angle*rad),cy-r*Math.cos(angle*rad)];
  // 极坐标角度网格：0° 指向 +Y，正角向 +X，与板端协议一致。
  for(const angle of [-60,-30,0,30,60]){
    const [ex,ey]=point(angle,radius);
    add(svg,'line',{x1:cx,y1:cy,x2:ex,y2:ey,stroke:'#e7ebeb','stroke-width':angle===0?1:.7,'stroke-dasharray':angle===0?'none':'3 6'});
    const [lx,ly]=point(angle,radius+21);
    add(svg,'text',{x:angle===0?lx-18:lx,y:ly+4,'text-anchor':angle===0?'end':'middle'},`${angle}°`);
  }
  add(svg,'text',{x:12,y:16},'角度 / °');add(svg,'text',{x:w-12,y:16,'text-anchor':'end'},'共同相位中心');
  const applied=a.beam?.registers_verified&&a.beam?.applied_run_id===a.run_id?a.beam:null;
  const pattern=applied?jointBeamPattern(a,applied.phase_rad):[];
  if(pattern.length){
    const beamRadius=radius*.9;
    for(const db of [-20,-10,0]){
      const r=(db+30)/30*beamRadius,[lx,ly]=point(-78,r);
      add(svg,'path',{d:`M ${cx-r} ${cy} A ${r} ${r} 0 0 1 ${cx+r} ${cy}`,fill:'none',stroke:'#e5e9e9','stroke-width':.8,'stroke-dasharray':'2 5'});
      add(svg,'text',{x:lx-7,y:ly+4,'text-anchor':'end','data-role':'joint-beam-scale'},`${db} dB`);
    }
    const points=pattern.map(p=>point(p.angle,(p.db+30)/30*beamRadius).join(','));
    const shape=add(svg,'path',{d:`M ${cx} ${cy} L ${points.join(' L ')} Z`,fill:color.blue,'fill-opacity':.025,stroke:color.blue,'stroke-width':1.7,'data-role':'joint-beam-pattern'});
    add(shape,'title',{},'由板端已校验的六路相位计算；0 dB 为六路理想相干叠加，非实测响应。');
  }
  add(svg,'text',{x:w-16,y:h-9,'text-anchor':'end','data-role':'joint-beam-note'},
      pattern.length?'方向图 −30～0 dB · 相位计算':'方向图：等待板端已校验相位');
  drawManualBeam({svg,w,h,cx,cy,radius:radius*.9,at:point},a,-30);
  // 六阵元示意：只画相对位置，不表示距离尺度。
  const xs=a.antennas.map(p=>p[0]-ref[0]),span=Math.max(1e-9,Math.max(...xs)-Math.min(...xs));
  const scale=Math.min(w*.24/span,70),baseY=cy+16;
  add(svg,'line',{x1:cx+Math.min(...xs)*scale,y1:baseY,x2:cx+Math.max(...xs)*scale,y2:baseY,stroke:'#d9e0e1','stroke-width':1});
  a.antennas.forEach((p,index)=>{
    add(svg,'circle',{cx:cx+(p[0]-ref[0])*scale,cy:baseY-(p[1]-ref[1])*scale,r:2.7,fill:index<3?color.orange:color.blue});
  });
  add(svg,'text',{x:cx,y:baseY+16,'text-anchor':'middle'},'CH0–CH5 · 六天线共同相位中心');
  // 每条路径一条真实角度射线；选中高亮，其余透明。
  const associated=a.beam?.path_tracking?.source_run_id===a.run_id?a.beam.path_tracking.tracks||[]:[];
  paths.forEach((p,index)=>{
    if(!Number.isFinite(p.angle_deg))return;
    const active=index===jointSelected,weak=p.active===false;
    const [ex,ey]=point(p.angle_deg,radius);
    add(svg,'line',{x1:cx,y1:cy,x2:ex,y2:ey,stroke:color.blue,'stroke-width':active?2.4:1.2,'stroke-opacity':active?1:(weak?.12:.26),'stroke-dasharray':active?'none':'4 5','data-role':'joint-path-ray'});
    const [lx,ly]=point(p.angle_deg,radius+12),nearCentre=Math.abs(p.angle_deg)<15;
    const anchor=lx<120?'start':lx>w-120?'end':nearCentre?'start':lx<cx?'end':'start';
    const track=associated.find(t=>t.observed&&t.path_index===(Number.isInteger(p.id)?p.id:index));
    add(svg,'text',{x:nearCentre?lx+12:lx,y:ly+4,'text-anchor':anchor,
      style:`fill:${active?color.blue:'#626b72'}`},`P${(Number.isInteger(p.id)?p.id:index)+1}${track?` / T${track.track_id}`:''} · ${fmt(p.angle_deg,1)}°`);
  });
  // 已校验波束只按 angle_deg/phase_rad 的板端回执绘制，不读取 target.x/y。
  if(applied&&Number.isFinite(applied.angle_deg)){
    const [ex,ey]=point(applied.angle_deg,radius*1.06);
    add(svg,'line',{x1:cx,y1:cy,x2:ex,y2:ey,stroke:color.orange,'stroke-width':1.4,'stroke-dasharray':'8 5','stroke-opacity':.62,'data-role':'joint-beam-axis'});
    // 波束读数固定放右上角，避免与正前方刻度和路径标签相叠。
    add(svg,'text',{x:w-16,y:38,'text-anchor':'end',style:`fill:${color.orange}`},`BEAM${applied.target?.track_id?` T${applied.target.track_id}`:''} ${fmt(applied.angle_deg,1)}°`);
  }
  const selectedPath=selected||null;
  if(selectedPath&&Number.isFinite(selectedPath.angle_deg)){
    const [ex,ey]=point(selectedPath.angle_deg,radius*.48);
    add(svg,'circle',{cx:ex,cy:ey,r:3.4,fill:color.yellow,stroke:color.blue,'stroke-width':1.2,'data-role':'joint-selected-path'});
  }
  if(!paths.some(p=>Number.isFinite(p.angle_deg)))add(svg,'text',{x:cx,y:h-16,'text-anchor':'middle'},'板端尚未给出有效路径角');
}
function renderJointHeatmaps(profile,selected){
  renderJointHeat(selected);
  jointScoreView.update(state?.aoa,palette);
  const view=jointHeatHistory.view();
  const models=[...new Set((view?.cells||[]).map(r=>r.modelPaths))].sort();
  text('joint-profile-def',`最近 64 s · ${view?.cells.length||0} 批 · ${models.length>1?`${models.join('/')} 径历史 · `:''}最新在上`);
  $('joint-profile-def').title='与联合 ML 评分图来自同一板端 ML 搜索；历史图展示跨批次角度剖面，组合图展示当前批次角度组合。各批采用自身阶数，灰色为无数据，Score 非概率。';
  $('joint-colorbar').title=(profile?.scale_definition||'板端相对 ML 评分 / dB')+'；最近64秒共用自适应色阶，原始数值不变';
}
function renderJointHeat(selected){
  const svg=$('joint-profile'),view=jointHeatHistory.view();
  if(!view){blank(svg,'等待板端 ML 数据');text('joint-scale-min','—');return;}
  const [w,h]=svgSize(svg),left=48,right=w-22,top=22,bottom=h-28;
  const angles=view.latest.angles,lo=angles[0],hi=angles.at(-1);
  const canvas=document.createElement('canvas');canvas.width=angles.length;canvas.height=Math.round(bottom-top);
  const ctx=canvas.getContext('2d');ctx.fillStyle='#edf0f0';ctx.fillRect(0,0,canvas.width,canvas.height);
  // 左侧保留角度随接收时间的变化，右侧单独展示当前帧组合评分。
  const floor=view.colorFloor,choice=$('joint-palette').value;
  text('joint-scale-min',`−${-floor} dB`);
  $('joint-scale-min').title='自动色阶：最近64秒有效数据共用同一映射，至少保留1 dB显示范围。';
  for(const row of view.cells){
    if(!row.values||row.bottom<=row.top)continue;
    const y0=row.top/view.span*canvas.height,y1=row.bottom/view.span*canvas.height;
    const cells=row.values.map((v,i)=>[i,v]);
    cells.forEach(([i,v])=>{
      if(i<0||i>=angles.length)return;
      const rgb=palette((v-floor)/-floor,choice);
      ctx.fillStyle=`rgb(${rgb.join(',')})`;ctx.fillRect(i,y0,1,y1-y0);
    });
  }
  add(svg,'image',{href:canvas.toDataURL(),x:left,y:top,width:right-left,height:bottom-top,
    preserveAspectRatio:'none','data-role':'joint-ml-heatmap'});
  for(const row of view.cells){
    if(row.bottom<=row.top)continue;
    const hit=add(svg,'rect',{x:left,y:top+row.top/view.span*(bottom-top),width:right-left,
      height:(row.bottom-row.top)/view.span*(bottom-top),fill:'transparent'});
    add(hit,'title',{},`${row.modelPaths} 径 · ${row.id}`);
  }
  for(const age of [0,view.span/2,view.span]){
    const y=top+age/view.span*(bottom-top);
    add(svg,'text',{x:left-8,y:y+4,'text-anchor':'end'},fmt(age,age%1?1:0));
  }
  const x=a=>left+(.5+(a-lo)/(hi-lo)*(angles.length-1))/angles.length*(right-left);
  for(const angle of [lo,(lo+hi)/2,hi])add(svg,'text',{x:x(angle),y:h-9,'text-anchor':'middle'},`${fmt(angle,0)}°`);
  add(svg,'text',{x:left,y:13},'距最新 / s');
  if(selected&&Number.isFinite(selected.angle_deg)){
    const sx=x(selected.angle_deg);
    add(svg,'path',{d:`M${sx-4},${top-8}L${sx+4},${top-8}L${sx},${top-2}Z`,fill:color.orange});
  }
  const bar=$('joint-colorbar');
  bar.setAttribute('aria-label',`${choice==='turbo'?'MATLAB Turbo':'暖橙'}色标，${floor}～0 dB`);
  bar.style.background=`linear-gradient(90deg,${Array.from({length:16},(_,i)=>`rgb(${palette(i/15,choice).join(',')})`).join(',')})`;
}
function renderAngle6(){
  if(v12Result(state?.aoa)){renderV12Angle6();return;}
  const a=state?.aoa,j=joint(),paths=jointPathList();
  const svg=$('angle6-plot');
  if(!a||!j||!paths.length){
    blank(svg,'等待板端联合测角数据');
    renderJointHeatmaps(j?.profile,null);
    $('joint-paths-list').replaceChildren();
    text('joint-meta','FAR-FIELD ML');text('joint-status','等待板端联合数据');$('joint-status').title='';text('joint-channels','CH0–CH5 · 共同相位中心');
    if(j?.selection){text('joint-status','无有效单径／双径／三径结果');blank(svg,'无合格联合选角');}
    text('joint-note','六天线联合测角结果尚未上报；不显示距离或 XY 交点。');
    return;
  }
  const key=`${a.session||''}|${j.path_count||paths.length}`;
  if(key!==jointKey){jointKey=key;jointManual=false;}
  if(!jointManual||jointSelected<0||jointSelected>=paths.length)jointSelected=bestJointPathIndex();
  const selected=paths[jointSelected]||null;
  const conditionDefinition=j.condition_definition||'';
  text('joint-meta',`${j.path_count||paths.length} 径模型${j.selection&&j.path_count<3?' · 退化选角':''} · ${j.channels?.length||6} CH`);
  text('joint-status',`${j.has_best?'有联合解':'无合格联合解'}${j.ambiguous?' · 多解':''} · J/E ${fmt(j.residual_ratio,4)} · Frobenius cond ${fmt(j.condition,2)}`);
  if(j.selection){
    const s=j.selection;
    const poorFit=!Number.isFinite(j.residual_ratio)||j.residual_ratio>(a.max_bearing_residual??.1);
    text('joint-status',`${s.accepted?'选角约束通过':'联合选角冲突'}${poorFit?' · 残差超限':''}${j.ambiguous?' · 多解':''} · J/E ${fmt(j.residual_ratio,4)} · ΔJ/E ${fmt(s.residual_gap_ratio*100,3)}%`);
    if(s.method==='quality_cascade_energy_fallback')text('joint-status',`${s.fallback?'兜底候选 · 未通过质量':s.accepted?'质量级联通过':'无可信候选'} · 融合 J/E ${fmt(j.residual_ratio,4)}`);
  }
  if(a.direction_frames===4)text('joint-meta',$('joint-meta').textContent+' · 四帧协方差');
  if(j.raw_validation){
    const q=j.raw_validation;
    text('joint-status',$('joint-status').textContent+` · 原帧 J/E ${fmt(q.raw_residual_ratio,4)} · 末帧 ${fmt(q.latest_residual_ratio,4)}${jointRawQualityPassed(a)?'':' · 原始质量未通过'}`);
  }
  $('joint-status').style.color='#454b50';
  $('joint-status').title=conditionDefinition?`condition_definition: ${conditionDefinition}`:'';
  text('joint-channels',`CH0–CH5 · 参考 ${Array.isArray(j.reference_xy)?j.reference_xy.map(v=>fmt(v,3)).join(' / '):'板端未上报'}`);
  text('joint-note',`正前方 0° · 搜索 ${a.search.start}°～${fmt(a.search.start+(a.search.count-1)*a.search.step,0)}°${j.ambiguous?' · 存在近等价解':''}`);
  $('joint-paths-list').replaceChildren();
  paths.forEach((p,index)=>{
    const button=document.createElement('button');button.type='button';button.className='ao-joint-path'+(index===jointSelected?' is-selected':'')+(p.active===false?' is-weak':'');
    button.setAttribute('aria-pressed',index===jointSelected?'true':'false');
    const head=document.createElement('b');head.textContent=`P${(Number.isInteger(p.id)?p.id:index)+1}`;
    const angle=document.createElement('span');angle.textContent=`${fmt(p.angle_deg,1)}°`;
    const rel=document.createElement('span');rel.textContent=`REL ${fmt(p.relative_power_db,1)} dB`;
    const share=document.createElement('em');share.textContent=Number.isFinite(p.energy_share)?`ENERGY SHARE ${fmt(p.energy_share*100,1)}%`:'ENERGY SHARE —';
    share.title='拟合分量的相对能量，不是直达径概率，也不决定联合选角';
    button.append(head,angle,rel,share);
    button.onclick=()=>{jointSelected=index;jointManual=true;renderAngle6();renderBeamControls();};
    $('joint-paths-list').append(button);
  });
  renderAngle6Plot(a,j,paths,selected);
  renderJointHeatmaps(j.profile,selected);
}
function polarFrame(svg,compact=false,bounds=[-90,90]){
  const [w,h]=svgSize(svg),[lo,hi]=bounds;
  // Upper Beam viewport and curve radius match the original V1.0 angle view.
  let cx=w/2,cy=h-58,radius=.9*Math.min((w-110)/2,h-98);
  if(compact){
    const left=Math.sin(lo*rad),right=Math.sin(hi*rad);
    radius=Math.max(50,Math.min((w-72)/Math.max(.01,right-left),h-70));
    cx=w/2-radius*(left+right)/2;cy=radius+34;
  }
  const at=(angle,r=radius)=>[cx+r*Math.sin(angle*rad),cy-r*Math.cos(angle*rad)];
  for(const db of [-30,-20,-10,0]){
    const r=(db+40)/40*radius,pts=[];
    const segments=Math.max(2,Math.ceil((hi-lo)/3));
    for(let i=0;i<=segments;i++)pts.push(at(lo+(hi-lo)*i/segments,r).join(','));
    add(svg,'polyline',{points:pts.join(' '),fill:'none',stroke:'#e5e9e9','stroke-width':1});
    add(svg,'text',{x:cx+6,y:cy-r+13},`${db} dB`);
  }
  const ticks=[...new Set([lo,...[-90,-60,-30,0,30,60,90].filter(v=>v>lo&&v<hi),hi])];
  for(const theta of ticks){
    const [x,y]=at(theta);add(svg,'line',{x1:cx,y1:cy,x2:x,y2:y,stroke:'#e5e9e9','stroke-width':1});
    const [tx,ty]=at(theta,radius+17);add(svg,'text',{x:tx,y:ty+4,'text-anchor':'middle'},`${theta}°`);
  }
  if(!compact)add(svg,'text',{x:16,y:20},'Relative Power / dB');
  if(!compact)add(svg,'text',{x:w-16,y:h-9,'text-anchor':'end'},'全阵列中心 · 正前方 0°');
  return {svg,w,h,cx,cy,radius,at};
}
function polarDirection(frame,angle,ink,dash,width,opacity,role){
  if(!Number.isFinite(angle))return;
  const [x,y]=frame.at(angle);
  add(frame.svg,'line',{x1:frame.cx,y1:frame.cy,x2:x,y2:y,stroke:ink,'stroke-width':width,'stroke-dasharray':dash,opacity,'data-role':role});
}
function polarMarker(frame,angle,ratio,shape,id,ink,opacity=1){
  if(!Number.isFinite(angle))return;
  const [x,y]=frame.at(angle,frame.radius*ratio),g=add(frame.svg,'g',{transform:`translate(${x} ${y})`,'data-role':'v12-angle-marker','data-marker-id':id});
  const attrs={fill:shape==='circle'?'none':'#fff',stroke:ink,'stroke-width':1.5,opacity};
  if(shape==='circle')add(g,'circle',{cx:0,cy:0,r:4.5,...attrs});
  else if(shape==='square')add(g,'rect',{x:-4,y:-4,width:8,height:8,...attrs});
  else add(g,'path',{d:shape==='diamond'?'M0 -5 L5 0 L0 5 L-5 0 Z':shape==='down'?'M-5 -4 L5 -4 L0 5 Z':'M0 -5 L5 4 L-5 4 Z',...attrs,fill:shape==='diamond'?ink:'#fff'});
  add(g,'text',{x:7,y:-7,style:'fill:#454b50;font-size:9px'},id);
  add(g,'title',{},`${id} · ${fmt(angle,2)}° · 方向标记，半径不表示功率`);
}
function renderV12BeamPolar(beam,visible){
  const f=polarFrame($('angle6-plot'));
  const joint=v12Result(state?.aoa)?.joint;
  const mlAngle=joint?.valid===true&&Number.isFinite(joint.angle_deg)?joint.angle_deg:null;
  add(f.svg,'title',{},`${beam?.syntheticPreview?'合成预览。':''}理想校准·等幅理论响应；非实测`);
  text('angle-applied-axis',beam?`已应用方向 ${fmt(beam.appliedAngleDeg,1)}°`:'已应用方向 —');
  $('angle-ml-reference').hidden=mlAngle===null;
  $('angle-ml-reference').title='本批 ML 主方向参照，非已应用波束方向';
  text('angle-ml-axis',`ML 主方向 ${fmt(mlAngle,1)}°`);
  if(visible&&beam){
    const points=beam.points.map(p=>f.at(p.angle,(p.db+40)/40*f.radius).join(','));
    const curve=add(f.svg,'path',{d:`M ${f.cx} ${f.cy} L ${points.join(' L ')} Z`,fill:color.blue,'fill-opacity':.015,stroke:color.blue,'stroke-width':1.6,'stroke-opacity':.7,'data-role':'v12-applied-beam-pattern'});
    add(curve,'title',{},'理想校准·等幅理论响应；非实测');
    polarDirection(f,beam.appliedAngleDeg,color.blue,'none',1.5,.65,'v12-applied-beam-axis');
  }else add(f.svg,'text',{x:f.cx,y:f.cy-f.radius*.6,'text-anchor':'middle'},beam?'Beam 图层已隐藏':'等待本批有效指向与校准相位依据');
  drawManualBeam(f,state?.aoa);
  if(mlAngle!==null){
    polarDirection(f,mlAngle,color.orange,'none',2,.95,'v12-beam-ml-reference');
  }
}
function renderV12ScanPolar(scan,joint,secondary,layers,search){
  const f=polarFrame($('scan-polar-plot'),true,v12ScanDomain(scan,search));
  add(f.svg,'title',{},'常规波束形成空间谱（CBF），以自身峰值为 0 dB。方向标记半径仅作区分，不代表功率；次解仅显示。');
  if(layers.scan){
    const points=v12ScanPoints(scan,f.cx,f.cy,f.radius);
    add(f.svg,'polyline',{points:points.map(p=>p.join(',')).join(' '),fill:'none',stroke:color.blue,'stroke-width':1.4,'stroke-linejoin':'round','data-role':'v12-scan-polar'});
    for(const i of [0,points.length-1]){
      const [x,y]=points[i];
      const edge=add(f.svg,'line',{x1:x,y1:y,x2:f.cx,y2:f.cy,stroke:color.blue,'stroke-width':.8,'stroke-opacity':.22,'data-role':'v12-scan-boundary','data-angle-deg':scan.angles_deg[i]});
      add(edge,'title',{},'边界封口仅显示：沿实际首末角径向连接至绘图底，不代表范围外功率数据。');
    }
    polarDirection(f,scan.angle_deg,color.blue,'none',1,.5,'v12-scan-direction');
    polarDirection(f,scan.secondary_angle_deg,color.blue,'3 6',.8,.3,'v12-secondary-direction');
    polarMarker(f,scan.angle_deg,.98,'up','S1',color.blue);
    polarMarker(f,scan.secondary_angle_deg,.92,'square','S2',color.blue,.5);
  }else if(scan.valid===false||scan.angles_deg.length<2)add(f.svg,'text',{x:f.cx,y:f.cy-f.radius*.6,'text-anchor':'middle'},'等待板端 CBF 空间谱');
  if(layers.ml){
    polarDirection(f,secondary?.ml_angle_deg,color.blue,'5 6',.9,.4,'v12-remaining-direction');
    joint.angles.forEach((angle,i)=>polarMarker(f,angle,.78,'circle',`M${i+1}`,color.orange,.75));
    polarMarker(f,joint.angle_deg,.9,'diamond','P',color.orange);
    polarMarker(f,secondary?.ml_angle_deg,.86,'down','R',color.blue,.65);
  }
}
function renderV12Angle6(){
  if(!v12FullArrayVisible(state?.aoa,processingMode))return;
  const a=state.aoa,v12=v12Result(a),s=v12.scan,j=v12.joint,secondary=j.secondary;
  const beam=v12AppliedBeamPattern(a),scanAvailable=s.valid!==false&&s.angles_deg.length>=2;
  const layers={scan:$('angle-layer-scan').checked&&scanAvailable,ml:$('angle-layer-ml').checked&&j.valid,beam:$('angle-layer-beam').checked&&!!beam};
  for(const [name,available] of [['scan',scanAvailable],['ml',j.valid],['beam',!!beam]]){
    const input=$('angle-layer-'+name);input.disabled=!available;input.parentElement.classList.toggle('is-unavailable',!available);
  }
  text('angle-beam-info',beam?`已应用 ${fmt(beam.appliedAngleDeg,1)}° · 理想校准·等幅理论响应；非实测`:'Beam 未显示：本批无可靠的平面指向校准依据');
  $('angle-beam-info').title=beam?`对应批次：${beam.appliedRunId}。理想校准·等幅理论响应；非实测。`:'仅使用同批已验证的 Manual/ANGLE 指向相位；等权或球面权值不代作校准响应。';
  $('angle-layer-beam-label').title=`${beam?.syntheticPreview?'合成预览。':''}${$('angle-beam-info').title}`;
  renderV12BeamPolar(beam,layers.beam);
  renderV12ScanPolar(s,j,secondary,layers,a.search);
  renderV12PolarKey(s,j,secondary);
  text('joint-meta',`V2.0 · ${j.model_paths||'—'} 径模型 · CH0–CH5`);
  text('joint-status',`${j.valid?'联合方向有效':'本批无有效联合方向'} · J/E ${fmt(j.residual_ratio,4)} · Match ${fmt(j.main_match,3)}`);
  $('joint-status').title='联合 ML 模型、路径与主方向均采用板端输出；页面不进行二次选角。';
  text('joint-channels',`CBF 主峰 ${fmt(s.angle_deg,2)}° · Coarse ${fmt(s.coarse_angle_deg,1)}°`);
  const list=$('joint-paths-list');list.replaceChildren();
  const cards=[['CBF 主峰',s.angle_deg,`板端 CBF · ${s.angles_deg.length} 个方向`,'ao-v12-scan-card'],['ML 主方向',j.valid?j.angle_deg:null,`${j.model_paths||'—'} 径模型 · P${Number.isInteger(j.path_index)?j.path_index+1:'—'} · Score ${fmt(j.score,4)}`,'ao-v12-main-card'],['CBF 次峰 · 仅显示',secondary?.angle_deg,`对应 ML ${fmt(secondary?.ml_angle_deg,2)}° · Error ${fmt(secondary?.error_deg,2)}°`,'ao-v12-secondary-card']];
  for(const [name,angle,note,kind] of cards){
    const card=document.createElement('div');card.className=`ao-joint-path ${kind}`;
    const head=document.createElement('b');head.textContent=`${name} · ${fmt(angle,2)}°`;
    const detail=document.createElement('span');detail.textContent=note;card.append(head,detail);list.append(card);
  }
  jointSelected=j.valid?j.path_index:-1;jointManual=false;
  renderJointHeatmaps(a.joint?.profile,selectedJointPath());
  text('joint-note','上图 Beam 为等幅理论阵列因子，并非 PL 实测；下图 CBF 为板端同批空间谱，两图分别以自身峰值为 0 dB。方向标记半径不表示功率；次解仅显示，不参与定位和控制。');
  $('joint-panel-title').title=$('joint-note').textContent;
}
function renderV12PolarKey(scan,joint,secondary){
  const panel=$('angle-polar-key');panel.replaceChildren();
  const entries=[['is-scan-line','同批 CBF 空间谱','板端功率采样'],['is-triangle','S1 · CBF 主峰',`${fmt(scan.angle_deg,2)}°`],['is-square','S2 · CBF 次峰',`${fmt(scan.secondary_angle_deg,2)}° · 仅显示`],['is-circle','M · 选中 ML 组',joint.angles.map((angle,i)=>`M${i+1} ${fmt(angle,2)}°`).join(' / ')||'—'],['is-diamond','P · ML 主方向',`${fmt(joint.angle_deg,2)}° · J/E ${fmt(joint.residual_ratio,4)} · Soft Match ${fmt(joint.main_match,3)}`],['is-down','R · 剩余 ML 路径',`${fmt(secondary?.ml_angle_deg,2)}° · Error ${fmt(secondary?.error_deg,2)}° · Secondary Match ${fmt(secondary?.score,4)}`]];
  for(const [kind,title,value] of entries){
    const row=document.createElement('div'),head=document.createElement('b'),key=document.createElement('i'),detail=document.createElement('span');
    row.className='ao-polar-key-row';key.className=kind;head.append(key,title);detail.textContent=value;row.append(head,detail);panel.append(row);
  }
}
function scanCandidates(){const s=scanData();return Array.isArray(s?.candidates)?s.candidates:[];}
function scanAutomaticLabel(s){
  const f=s?.fusion;
  if(!f?.enabled)return '自动：最高扫描峰';
  if(f.valid&&!f.ml_available)return '自动：扫描方向（ML不可用）';
  return f.valid?'自动：融合方向':'自动：融合方向（等待有效数据）';
}
function bestScanCandidateIndex(){
  let best=-1,power=-Infinity;
  scanCandidates().forEach((c,i)=>{if(Number.isFinite(c.normalized_power)&&c.normalized_power>power){power=c.normalized_power;best=i;}});
  return best;
}
function scanAngleIndex(angle){
  const angles=scanData()?.angles_deg;
  if(!Array.isArray(angles)||!Number.isFinite(angle))return -1;
  return angles.findIndex(a=>Number.isFinite(a)&&Math.abs(a-angle)<1e-8);
}
function scanVerificationSummary(s){
  const v=s?.verification;
  if(!v)return '未上报';
  const names={disabled:'未启用',pending:'等待',not_run:'未执行',offline_not_run:'离线未执行',checking:'核验中',matched:'匹配',mismatch:'不匹配',error:'错误',restore_failed:'恢复失败'};
  const checks=Array.isArray(v.checks)?v.checks:[];
  const passed=checks.filter(c=>c?.valid===true).length;
  const head=names[v.status]||v.status||'未知';
  if(!v.enabled)return `${head} · 未启用`;
  return `${head} · ${passed}/${checks.length} 通过 · 第 ${Number.isInteger(v.source_round)?v.source_round+1:'—'} 轮 · ${v.restore_verified?'恢复已验证':'恢复未验证'}`;
}
function selectScanCandidate(index){
  const candidate=scanCandidates()[index];
  if(!candidate||!Number.isFinite(candidate.angle_deg))return;
  scanSelected=index;scanManual=true;
  $('scan6-selection').value='manual';
  beamFormInitialized=true;
  $('beam-mode').value='manual';
  $('beam-angle').value=String(candidate.angle_deg);
  controlError='';
  renderScan6();renderBeamControls();
}
function renderScanCandidateList(){
  const list=$('scan6-candidates'),candidates=scanCandidates(),s=scanData();
  list.replaceChildren();
  if(!candidates.length){
    const empty=document.createElement('span');empty.className='ao-small';
    empty.textContent=s?.valid?'无扫描峰候选':'扫描无效，无候选峰';
    list.append(empty);return;
  }
  candidates.forEach((candidate,index)=>{
    const button=document.createElement('button');
    button.type='button';button.className='ao-scan6-candidate'+(index===scanSelected?' is-selected':'');
    button.setAttribute('aria-pressed',index===scanSelected?'true':'false');
    const head=document.createElement('b');head.textContent=`扫描峰 #${index+1} · ${fmt(candidate.angle_deg,1)}°`;
    const power=document.createElement('span');
    power.textContent=`归一化 ${fmt(candidate.normalized_power,3)} · 相对峰值 ${fmt(candidate.relative_db,2)} dB`;
    const hint=document.createElement('em');hint.textContent='点击填入手动角度';
    button.append(head,power,hint);
    button.title='功率曲面的局部峰，不代表物理来波数量。点击后切换为手动角度，仍需在“波束控制”中点击应用才下发。';
    button.onclick=()=>selectScanCandidate(index);
    list.append(button);
  });
}
function renderScanVerification(){
  const panel=$('scan6-verify'),v=scanData()?.verification;
  panel.replaceChildren();
  if(!v){panel.textContent='PL 复核：板端未上报';return;}
  const names={disabled:'未启用',pending:'等待',not_run:'未执行',offline_not_run:'离线未执行',checking:'核验中',matched:'匹配',mismatch:'不匹配',error:'错误',restore_failed:'恢复失败'};
  const head=document.createElement('div');head.className='ao-scan6-verify-head';
  head.textContent=`PL 复核：${names[v.status]||v.status||'未知'}${v.enabled?` · 第 ${Number.isInteger(v.source_round)?v.source_round+1:'—'} 轮 · ${v.restore_verified?'相位恢复已验证':'相位恢复未验证'} · ${fmt(v.elapsed_ms,1)} ms`:' · 未启用'}`;
  panel.append(head);
  const checks=Array.isArray(v.checks)?v.checks:[];
  if(!v.enabled)return;
  if(!checks.length){const empty=document.createElement('span');empty.className='ao-small';empty.textContent='没有实际 PL 核验点';panel.append(empty);return;}
  checks.forEach(check=>{
    const row=document.createElement('div');
    row.className='ao-scan6-verify-row'+(check.valid?' is-valid':' is-invalid');
    const name=document.createElement('b');
    name.textContent=`#${Number.isInteger(check.candidate_id)?check.candidate_id+1:'—'} · ${fmt(check.angle_deg,1)}°`;
    const detail=document.createElement('span');
    detail.textContent=`期望 ${fmt(check.expected_power,1)} / 观测 ${fmt(check.observed_power,1)} · 相对误差 ${Number.isFinite(check.relative_error)?check.relative_error.toExponential(2):'—'} · 输入形状 ${Number.isFinite(check.input_shape_error)?check.input_shape_error.toExponential(2):'—'}`;
    const flag=document.createElement('em');
    flag.textContent=check.valid?'有效':check.registers_verified?'未通过':'寄存器未校验';
    row.append(name,detail,flag);
    row.title=`${check.capture_id||'未命名采集'} · 寄存器${check.registers_verified?'已校验':'未校验'}${check.npu_eligible?' · 可交 NPU':' · 不交 NPU'}${check.fft_file?` · FFT ${check.fft_file}`:''}${check.pha_file?` · PHA ${check.pha_file}`:''}`;
    panel.append(row);
  });
}
function renderScanFusion(s){
  const panel=$('scan6-fusion'),legend=$('scan6-fusion-legend'),f=s?.fusion;
  const automatic=$('scan6-selection').querySelector('option[value="auto"]');
  if(automatic)automatic.textContent=scanAutomaticLabel(s);
  panel.replaceChildren();panel.hidden=!f?.enabled;legend.hidden=!f?.enabled;
  if(!f?.enabled)return;
  const values=[['原 ML',f.ml_available?f.ml_angle_deg:null,'is-ml'],
    ['扫描',f.scan_angle_deg,'is-scan'],['融合',f.valid?f.angle_deg:null,'is-fusion']];
  for(const [name,angle,kind] of values){
    const card=document.createElement('div');card.className=`ao-scan6-angle-card ${kind}`;
    const label=document.createElement('span');label.textContent=name;
    const value=document.createElement('b');value.textContent=Number.isFinite(angle)?`${fmt(angle,2)}°`:'—';
    card.append(label,value);panel.append(card);
  }
  const note=document.createElement('span');note.className='ao-scan6-fusion-note';
  if(!f.valid)note.textContent='当前批次无有效融合结果';
  else if(!f.ml_available)note.textContent='ML 无可用候选 · 当前仅使用扫描';
  else{
    const weight=f.effective_ml_weight;
    note.textContent=`sin 域 · 扫描 ${fmt((1-weight)*100,0)}% / ML ${fmt(weight*100,0)}% · ML ${f.ml_qualified?'质量通过':'质量未通过，保留原选角'} · ML ${fmt(f.ml_elapsed_ms,1)} ms`;
  }
  panel.append(note);
}
function renderScan6(){
  const a=state?.aoa,s=scanData(),svg=$('scan6-plot'),candidates=scanCandidates();
  renderScanFusion(s);
  if(!a||!s){
    blank(svg,'等待板端直接波束扫描');text('scan6-meta','PL FFT · 同批 4 帧');
    text('scan6-status','等待板端扫描数据');
    $('scan6-candidates').replaceChildren();$('scan6-verify').replaceChildren();
    text('scan6-note','直接波束扫描尚未上报；不下发 ML 二维/三维评分。');
    return;
  }
  const angles=Array.isArray(s.angles_deg)?s.angles_deg:[];
  const db=Array.isArray(s.relative_db)?s.relative_db:[];
  if(angles.length<2||db.length!==angles.length){
    blank(svg,'扫描数据不完整');text('scan6-meta','PL FFT · 数据不完整');
    text('scan6-status','等待完整扫描功率曲线');
    $('scan6-candidates').replaceChildren();$('scan6-verify').replaceChildren();
    text('scan6-note','扫描功率曲线长度与角度网格不一致。');
    return;
  }
  const key=`${a.session||''}|${s.model||''}`;
  if(key!==scanKey){scanKey=key;scanManual=false;}
  if(!scanManual||scanSelected<0||scanSelected>=candidates.length)scanSelected=bestScanCandidateIndex();
  $('scan6-selection').value=scanManual?'manual':'auto';
  const [w,h]=svgSize(svg),left=54,right=w-20,top=28,bottom=h-42;
  const lo=angles[0],hi=angles.at(-1),finiteDb=db.filter(Number.isFinite);
  const rawLow=Math.min(-10,Math.floor((finiteDb.length?Math.min(...finiteDb):-10)/5)*5);
  const ymin=Math.max(-80,rawLow),ymax=0;
  const px=angle=>hi===lo?(left+right)/2:left+(angle-lo)/(hi-lo)*(right-left);
  const py=value=>bottom-(value-ymin)/(ymax-ymin)*(bottom-top);
  const xstep=hi-lo>120?20:hi-lo>60?10:5;
  for(let value=0;value>=ymin;value-=10){
    add(svg,'line',{x1:left,y1:py(value),x2:right,y2:py(value),stroke:value===0?'#d1d6d7':'#edf0f0'});
    add(svg,'text',{x:left-8,y:py(value)+4,'text-anchor':'end'},`${value}`);
  }
  for(let angle=Math.ceil(lo/xstep)*xstep;angle<=hi+1e-9;angle+=xstep){
    add(svg,'line',{x1:px(angle),y1:top,x2:px(angle),y2:bottom,stroke:'#edf0f0','stroke-dasharray':Math.abs(angle)<1e-8?'4 5':'none'});
    add(svg,'text',{x:px(angle),y:bottom+30,'text-anchor':'middle'},`${fmt(angle,0)}°`);
  }
  add(svg,'text',{x:left,y:14},'相对峰值 / dB');
  add(svg,'text',{x:right,y:h-5,'text-anchor':'end'},'角度 / °');
  const defs=add(svg,'defs'),clip=add(defs,'clipPath',{id:'scan6-clip'});
  add(clip,'rect',{x:left,y:top,width:right-left,height:bottom-top});
  const marks=add(svg,'g',{'clip-path':'url(#scan6-clip)'});
  const points=angles.map((angle,index)=>`${px(angle)},${py(db[index])}`).join(' ');
  add(marks,'polyline',{points,fill:'none',stroke:color.blue,'stroke-width':1.8,'stroke-linejoin':'round','data-role':'scan-power-curve'});
  const fusion=s.fusion;
  if(fusion?.enabled){
    const directions=[['扫描',fusion.scan_angle_deg,color.blue,'2 5','scan-fusion-scan-angle'],
      ['原 ML',fusion.ml_available?fusion.ml_angle_deg:null,'#535d64','6 5','scan-fusion-ml-angle'],
      ['融合',fusion.valid?fusion.angle_deg:null,color.orange,'8 4','scan-fusion-angle']];
    for(const [name,angle,stroke,dash,role] of directions){
      if(!Number.isFinite(angle)||angle<lo||angle>hi)continue;
      const line=add(marks,'line',{x1:px(angle),y1:top,x2:px(angle),y2:bottom,stroke,
        'stroke-width':name==='融合'?1.8:1.2,'stroke-dasharray':dash,'data-role':role});
      add(line,'title',{},`本批 ${name} ${fmt(angle,2)}°`);
    }
  }
  const selectedCandidate=candidates[scanSelected];
  if(selectedCandidate&&Number.isFinite(selectedCandidate.angle_deg))
    add(marks,'line',{x1:px(selectedCandidate.angle_deg),y1:top,x2:px(selectedCandidate.angle_deg),y2:bottom,stroke:'#c8a23c','stroke-width':1,'stroke-dasharray':'4 4',opacity:.55,'data-role':'scan-selected-angle'});
  // 实际 PL 只核验少数方向；用橙色点区分，不把整条蓝色曲线说成 PL 逐点扫描。
  const checks=s.verification?.enabled&&Array.isArray(s.verification.checks)?s.verification.checks:[];
  checks.forEach(check=>{
    const index=scanAngleIndex(check.angle_deg);
    if(index<0)return;
    if(!(check.expected_power>0)||!(check.observed_power>0))return;
    const measuredDb=db[index]+10*Math.log10(check.observed_power/check.expected_power);
    const x=px(angles[index]),y=py(measuredDb),group=add(marks,'g',{'data-role':'scan-pl-verification'});
    add(group,'line',{x1:x,y1:top,x2:x,y2:bottom,stroke:color.orange,'stroke-width':.8,'stroke-dasharray':'3 5',opacity:.5});
    add(group,'circle',{cx:x,cy:y,r:4.2,fill:color.orange,stroke:'#fff','stroke-width':1.4});
    add(group,'title',{},`实际 PL 核验点 · 候选 #${check.candidate_id+1} · ${fmt(check.angle_deg,1)}° · 期望 ${fmt(check.expected_power,2)} / 观测 ${fmt(check.observed_power,2)} · 相对误差 ${Number.isFinite(check.relative_error)?check.relative_error.toExponential(2):'—'} · ${check.capture_id||'未命名采集'}`);
    if(checks.length<=3)add(group,'text',{x:x+7,y:y-8,style:`fill:${color.orange}`},'PL');
  });
  candidates.forEach((candidate,index)=>{
    if(!Number.isFinite(candidate.angle_deg))return;
    const grid=scanAngleIndex(candidate.angle_deg);
    if(grid<0)return;
    const x=px(candidate.angle_deg),y=py(db[grid]),active=index===scanSelected;
    const node=add(marks,'g',{transform:`translate(${x} ${y})`,role:'button',tabindex:0,'aria-label':`扫描峰 #${index+1} ${fmt(candidate.angle_deg,1)}度，归一化功率 ${fmt(candidate.normalized_power,3)}`});
    add(node,'title',{},`扫描峰 #${index+1} · ${fmt(candidate.angle_deg,1)}° · 归一化功率 ${fmt(candidate.normalized_power,3)} · 相对峰值 ${fmt(candidate.relative_db,2)} dB。候选峰不是物理来波数量。`);
    add(node,'rect',{x:-11,y:-11,width:22,height:22,fill:'transparent'});
    node.addEventListener('click',()=>selectScanCandidate(index));
    node.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectScanCandidate(index);}});
    add(node,'path',{d:'M 0 -7 L 6 0 L 0 7 L -6 0 Z',fill:active?color.yellow:'#fff',stroke:active?'#8d6d17':'#a99a62','stroke-width':active?1.8:1.2});
    if(active)add(node,'text',{x:9,y:-9,style:'fill:#535d64;font-weight:600'},`#${index+1} ${fmt(candidate.angle_deg,1)}°`);
  });
  if(Number.isFinite(s.peak_angle_deg))
    add(svg,'text',{x:w-16,y:top-9,'text-anchor':'end',style:`fill:${color.blue}`},`峰值 ${fmt(s.peak_angle_deg,1)}° · ${fmt(s.peak_normalized_power,3)}`);
  text('scan6-meta',s.valid?`PL FFT · 同批 ${s.frame_count} 帧平均`:'PL FFT · 扫描无效');
  text('scan6-status',`${s.valid?'有效':'无效'} · 输入功率 ${fmt(s.input_power,1)} · 耗时 ${fmt(s.elapsed_ms,1)} ms · 本地 SNR ${fmt(s.local_snr_db,1)} dB · 峰频 ${fmt(s.global_peak_frequency_hz/1e3,1)} kHz${s.ambiguous?' · 近等价峰':''} · PL 复核 ${scanVerificationSummary(s)}`);
  text('scan6-note',`蓝色：同批 PL FFT 等效扫描（${angles.length} 方向，非 161 方向 PL 逐扫）· 橙色：候选同帧 PL 核验（按同帧期望归一） · 候选峰为功率曲面局部峰，不代表物理来波数量。`);
  renderScanCandidateList();
  renderScanVerification();
}
function renderProcessing(){
  syncProcessingState();
  const source=(liveState||state)?.sources?.aoa;
  const p=processingData(),modes=supportedProcessingModes();
  const available=processingAvailable()&&source?.connected&&Date.now()/1000-source.last_seen<6,pending=processingPending();
  for(const [id,mode] of [['processing-position','position'],['processing-angle6','angle6'],['processing-scan6','scan6']]){
    const button=$(id),supported=modes.includes(mode);
    button.hidden=mode==='scan6'||!(supported||mode==='angle6'&&modes.includes('scan6'));
    button.disabled=!supported||!available||pending||frozen||controlPosting||(liveState||state)?.mode==='demo';
    button.setAttribute('aria-pressed',mode!=='scan6'&&analysisPage(processingMode)===mode?'true':'false');
  }
  let message='',bad=false;
  if(!p){
    const current=liveState||state;
    if(!current?.aoa){message=current?.board_link?.status==='connected'?'已连接 · 等待首个测量批次':'等待板端连接与测量数据';}
    else{message='板端尚未声明分析模式';bad=true;}
  }
  else if(!available){message=processingAvailable()?'板端未连接':'分析模式不可用';bad=true;}
  else if(pending){message='切换中 · 等待板端应用';}
  else if(p.command_status==='error'||p.command_status==='rejected'){message='切换失败 · '+(p.error||'命令被拒绝');bad=true;}
  else if(p.applied_mode){
    if(p.applied_mode==='angle6')message=`测角 · ${joint()?.selection?'单/双/三径 ML 与 CBF 空间谱':`${jointPathCount} 径 ML 与 CBF 空间谱`} · 已应用`;
    else if(p.applied_mode==='scan6')message=`测角 · CBF 空间谱 · 同批 ${scanData()?.frame_count??4} 帧 · 已应用`;
    else message='定位 · 已应用';
  }
  else if(processingMode==='scan6')message='测角 · CBF 空间谱 · 等待应用';
  else if(processingMode==='angle6')message='测角 · 单/双/三径 ML 与 CBF 空间谱 · 等待应用';
  else message='定位 · 等待应用';
  if(processingError){message+=' · '+processingError;bad=true;}
  text('processing-status',message);$('processing-status').classList.toggle('error',bad);
}
async function sendProcessing(mode){
  if(controlPosting||frozen)return;
  const p=processingData();
  if(!p||p.available!==true||!processingSupported(mode)){processingError='板端不支持该Analysis Mode';renderProcessing();return;}
  processingMode=mode;jointPathCount=3;processingAwait=true;processingError='';controlPosting=true;
  jointManual=false;jointKey='';scanManual=false;scanSelected=0;scanKey='';
  layoutProcessing();renderProcessing();renderBeamControls();
  try{
    const response=await fetch('/api/beam',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'processing',processing_mode:mode,joint_paths:jointPathCount})});
    const result=await response.json();if(!response.ok)throw Error(result.error||'命令未发送');
    if(liveState)liveState.control=result;
  }catch(e){processingError=e.message;}
  finally{
    controlPosting=false;await poll();processingAwait=false;
    const requested=mode;syncProcessingState();layoutProcessing();
    if(requested!==processingMode)render();else renderProcessing();
    renderBeamControls();
  }
}
function palette(t,choice=$('palette').value){
  // MATLAB 使用的原始 256 色 Turbo 表；暖橙为用户确认的界面彩色方案。
  if(choice==='turbo')return TURBO[Math.round(Math.max(0,Math.min(1,t))*255)].map(v=>Math.round(v*255));
  const stops=[[37,78,128],[102,160,198],[186,216,224],[245,224,136],[245,178,91],[228,126,69]];
  const x=Math.max(0,Math.min(1,t))*(stops.length-1),i=Math.min(stops.length-2,Math.floor(x)),f=x-i;
  return stops[i].map((v,k)=>Math.round(v*(1-f)+stops[i+1][k]*f));
}
const bytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
function heatmaps(){
  const a=state?.aoa,key=(a?.session||'')+(a?.run_id||'')+$('palette').value;
  if(key===heatKey)return;heatKey=key;
  $('colorbar').style.background=`linear-gradient(90deg,${Array.from({length:12},(_,i)=>`rgb(${palette(i/11).join(',')})`).join(',')})`;
  for(let g=0;g<2;g++){
    const canvas=$(`heat-${g}`),ctx=canvas.getContext('2d'),gr=group(g),grid=gr?.grid,n=grid?.angles_deg.length||192;
    canvas.width=canvas.height=n;ctx.fillStyle='#f2f4f4';ctx.fillRect(0,0,n,n);
    text(`angles-${g}`,gr?.paths.length?gr.paths.map(p=>fmt(p.angle_deg,1)+'°').join(' / '):'—');
    const axis=$(`heat-axis-${g}`);axis.replaceChildren();for(const t of [grid?`${grid.angles_deg[0]}°`:'—','θ₁ →',grid?`${grid.angles_deg.at(-1)}°`:'—']){const s=document.createElement('span');s.textContent=t;axis.append(s);}
    if(!grid){ctx.fillStyle='#626b72';ctx.font='12px sans-serif';ctx.textAlign='center';ctx.fillText('暂无联合网格',n/2,n/2);continue;}
    const score=bytes(grid.score_u8),mask=bytes(grid.invalid_u8),image=ctx.createImageData(n,n);
    // 线上 i*n+j 为 theta1_i,theta2_j；canvas 行向下，故翻转 theta2。
    for(let y=0;y<n;y++)for(let x=0;x<n;x++){const i=x*n+(n-1-y),out=(y*n+x)*4,c=mask[i]?[202,207,208]:palette(score[i]/255);image.data.set([...c,255],out);}
    ctx.putImageData(image,0,0);
    if(gr.paths.length===2){const lo=grid.angles_deg[0],hi=grid.angles_deg.at(-1),x=(gr.paths[0].angle_deg-lo)/(hi-lo)*(n-1),y=(hi-gr.paths[1].angle_deg)/(hi-lo)*(n-1);ctx.beginPath();ctx.arc(x,y,3.7,0,2*Math.PI);ctx.strokeStyle='white';ctx.lineWidth=1.6;ctx.stroke();}
  }
}
function plotSeries(svg,values,min,max,xlabels,yunit,visible=()=>true){
  const [w,h]=svgSize(svg),left=62,right=w-12,top=14,bottom=h-17;
  const tick=v=>{const step=(max-min)/2;if(v===0)return '0';return step<.001?v.toExponential(2):fmt(v,Math.max(0,Math.ceil(-Math.log10(step))+1));};
  for(let i=0;i<=4;i++){const value=min+(max-min)*i/4,y=bottom-(bottom-top)*i/4;add(svg,'line',{x1:left,y1:y,x2:right,y2:y,stroke:'#edf0f0'});if(i%2===0)add(svg,'text',{x:left-6,y:y+3,'text-anchor':'end',fill:'#626b72','font-size':10},tick(value));}
  const clipId=svg.id+'-trace-clip',defs=add(svg,'defs'),clip=add(defs,'clipPath',{id:clipId});
  add(clip,'rect',{x:left,y:top,width:right-left,height:bottom-top});
  const trace=add(svg,'g',{'clip-path':`url(#${clipId})`});
  let points=[];
  const flush=()=>{if(points.length)add(trace,'polyline',{points:points.join(' '),fill:'none',stroke:color.blue,'stroke-width':1.2});points=[];};
  values.forEach((v,i)=>{
    if(!visible(i)||!Number.isFinite(v)){flush();return;}
    points.push(`${left+i/(values.length-1)*(right-left)},${bottom-(v-min)/(max-min)*(bottom-top)}`);
  });
  flush();
  add(svg,'text',{x:right,y:top-3,'text-anchor':'end',fill:'#626b72','font-size':10},yunit);
  return {w,h,left,right,top,bottom};
}
function markSpectrumPeaks(svg,values,s,visible,frame){
  const peaks=spectrumPeaks(values,visible,s.db_min),{left,right,top,bottom}=frame;
  peaks.forEach((index,rank)=>{
    const x=left+index/(values.length-1)*(right-left),y=bottom-(values[index]-s.db_min)/(s.db_max-s.db_min)*(bottom-top),ink=rank?color.blue:color.orange;
    const width=Math.min(194,right-left),bx=Math.max(left,Math.min(right-width,x+14)),by=top+14+rank*70;
    const g=add(svg,'g',{'aria-label':`${rank?'次高峰':'最高峰'} ${(s.frequency_start_hz+index*s.frequency_step_hz)/1e6} MHz，${values[index]} dB Relative Power`});
    add(g,'polyline',{points:`${x},${y} ${x},${by+29} ${bx},${by+29}`,fill:'none',stroke:ink,'stroke-width':1,'stroke-dasharray':'4 4',opacity:.65});
    add(g,'path',{d:`M${x-5},${y-2}v-4h4 M${x+5},${y-2}v-4h-4 M${x-5},${y+2}v4h4 M${x+5},${y+2}v4h-4`,fill:'none',stroke:ink,'stroke-width':1.5});
    add(g,'circle',{cx:x,cy:y,r:2.5,fill:ink,stroke:'#fff','stroke-width':1});
    add(g,'rect',{x:bx,y:by,width,height:59,rx:3,fill:'#fff','fill-opacity':.8,stroke:'#e4e9eb'});
    add(g,'text',{x:bx+9,y:by+15,fill:ink,'font-size':11,'font-weight':600},`P${rank+1} / ${rank?'次高峰':'最高峰'}`);
    add(g,'text',{x:bx+9,y:by+32,fill:'#454b50','font-size':11},`${fmt((s.frequency_start_hz+index*s.frequency_step_hz)/1e6,6)} MHz`);
    add(g,'text',{x:bx+9,y:by+48,fill:'#454b50','font-size':11},`${fmt(values[index],2)} dB · Relative Power`);
  });
  return peaks;
}
function renderPeakSnr(values,s,peaks){
  const panel=$('spectrum-snr');panel.replaceChildren();
  const label=document.createElement('span');label.className='ao-small';label.textContent='峰值 SNR（单频点估算）';panel.append(label);
  const noisePeaks=spectrumPeaks(values,()=>true,s.db_min);
  peaks.forEach((index,rank)=>{
    const result=estimatePeakSnr(values,index,noisePeaks,s.frequency_step_hz,s.db_min);
    const item=document.createElement('span');item.className='ao-snr-item';
    const name=document.createElement('b');name.textContent=`P${rank+1}`;name.style.color=rank?color.blue:color.orange;
    const value=document.createElement('strong');value.textContent=result.snrDb===null?'暂不可估':`${fmt(result.snrDb,1)} dB`;
    item.append(name,value);
    const band=result.noiseBandBins?.map(i=>fmt((s.frequency_start_hz+i*s.frequency_step_hz)/1e6,3)+' MHz').join('～');
    item.title=result.snrDb===null?result.reason:`单频点功率扣噪后的功率比；噪底频带 ${band}，固定排除边界 5 点及谱峰各 ±6 点，取线性功率中位数 / ln(2)。Hann 等效噪声带宽约 ${fmt(result.rbwHz/1e3,3)} kHz。不是整带调制信号 SNR。${index===values.length/2?'0 Hz 分量可能含直流。':''}${result.reason}`;
    panel.append(item);
  });
}
function renderSpectrumWaterfall(force=false){
  const now=state?.server_time,history=frozen?frozenWaterfallView:beamSpectrumHistory.view(now),choice=$('spectrum-waterfall-palette').value,hide=$('spectrum-hide-boundary').checked,range=Number($('spectrum-waterfall-range').value);
  const canvas=$('spectrum-waterfall'),dpr=Math.min(2,window.devicePixelRatio||1),columns=Math.min(4096,Math.max(1,Math.round(canvas.clientWidth*dpr))),height=Math.round(240*dpr);
  const key=[frozen?'frozen':beamSpectrumHistory.version,Math.floor((now||0)*2),choice,hide,columns,range].join(':');
  if(!force&&key===waterfallPaintKey)return;waterfallPaintKey=key;
  const ctx=canvas.getContext('2d');
  canvas.onpointermove=null;canvas.width=columns;canvas.height=height;ctx.fillStyle='#edf0f0';ctx.fillRect(0,0,columns,height);
  $('spectrum-waterfall-colorbar').style.background=`linear-gradient(90deg,${Array.from({length:24},(_,i)=>`rgb(${palette(i/23,choice).join(',')})`).join(',')})`;
  text('spectrum-waterfall-min',`−${range} dB`);
  text('spectrum-waterfall-meta',`最近 64 s · ${history?.counts.valid||0} 批${history?.counts.missing?` · ${history.counts.missing} 批无频谱`:''}`);
  text('spectrum-waterfall-note',history?.counts.valid?'批次快照 · 每批归一化 · 最新在上':state?.aoa?.observation?.spectrum_enabled?'等待频谱':'频谱上报已关闭');
  canvas.title=`按批次先后等距排列，每行是一批真实快照，不代表持续时间；灰色标记空档或无频谱。悬停可查看接收时间。原始4096点FFT不变，色阶 −${range}～0 dB，各帧峰值归一化。`;
  if(!history){signalAxis('spectrum-waterfall-axis',[]);return;}
  const {bins,start,step}=history.axis;
  signalAxis('spectrum-waterfall-axis',[start,0,start+(bins-1)*step].map(v=>(v>0?'+':'')+fmt(v/1e6,3)+' MHz'));
  const strip=document.createElement('canvas');strip.width=columns;strip.height=1;
  const rowCtx=strip.getContext('2d'),image=rowCtx.createImageData(columns,1);
  const colors=Array.from({length:256},(_,i)=>palette(i/255,choice));ctx.imageSmoothingEnabled=false;
  const actual=history.cells.slice().reverse(),deltas=actual.slice(1).map((r,i)=>r.top-actual[i].top).filter(d=>d>0).sort((a,b)=>a-b);
  const gapLimit=Math.max(3,(deltas[Math.floor(deltas.length/2)]||1)*2.5),display=[];
  for(const row of actual){
    const prior=display.at(-1);
    if((!prior&&row.top>gapLimit)||(prior&&row.top-prior.top>gapLimit))display.push({values:null,gap:true,top:row.top});
    display.push(row);
  }
  const capacity=Math.max(64,display.length),rowHeight=height/capacity;
  canvas.onpointermove=e=>{
    const rect=canvas.getBoundingClientRect(),index=Math.floor((e.clientY-rect.top)/rect.height*capacity),row=display[index];
    canvas.title=!row?'尚无快照':row.gap?'接收空档':`${new Date(row.time*1000).toLocaleTimeString()} · 距当前 ${fmt(row.top,1)} s · ${row.values?'有效频谱':'本批无频谱'} · 每帧峰值归一化`;
  };
  for(const [rowIndex,row] of display.entries()){
    const top=Math.floor(rowIndex*rowHeight),bandHeight=Math.max(1,Math.ceil((rowIndex+1)*rowHeight)-top);
    if(!row.values){ctx.fillStyle='#cbd1d4';ctx.fillRect(0,top,columns,bandHeight);continue;}
    const raster=spectrumRasterRow(row.values,columns,hide);
    for(let i=0;i<columns;i++){
      const rgb=!Number.isFinite(raster[i])?[237,240,240]:colors[Math.round(Math.max(0,Math.min(1,(raster[i]+range)/range))*255)];
      image.data.set([...rgb,255],i*4);
    }
    rowCtx.putImageData(image,0,0);
    ctx.drawImage(strip,0,0,columns,1,0,top,columns,bandHeight);
  }
}
function observationControls(){
  const current=liveState||state,ob=current?.beam?.observation,control=current?.control,source=current?.sources?.aoa;
  const pending=controlPosting||(control?.mode==='observation'&&control.status==='sent');
  if(!pending)observationRequest=null;
  const available=Boolean(ob?.available&&source?.connected&&Date.now()/1000-source.last_seen<6);
  const desired=observationRequest||ob;
  $('observe-waveform').checked=Boolean(desired?.waveform_enabled);
  $('observe-spectrum').checked=Boolean(desired?.spectrum_enabled);
  for(const id of ['observe-waveform','observe-spectrum'])$(id).disabled=!available||frozen||controlPosting||current?.control?.status==='sent';
  const error=observationError||(control?.mode==='observation'&&['unknown','error','rejected'].includes(control.status)?control.error:'')||ob?.error;
  text('observation-status',error||!available?(error||'观测设备未连接'):pending?'观测设置已发送 · 等待回执':`波形上报：${desired?.waveform_enabled?'开':'关'} · 频谱上报：${desired?.spectrum_enabled?'开':'关'}`);
}
async function sendObservation(){
  if(controlPosting||frozen)return;
  const request={mode:'observation',waveform_enabled:$('observe-waveform').checked,spectrum_enabled:$('observe-spectrum').checked};
  observationRequest=request;observationError='';controlPosting=true;observationControls();
  try{
    const r=await fetch('/api/beam',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)}),result=await r.json();
    if(!r.ok)throw Error(result.error||'观测设置未发送');
    if(liveState)liveState.control=result;
  }catch(e){observationError=e.message;observationRequest=null;}
  finally{controlPosting=false;await poll();observationControls();}
}
function archiveControls(){
  const current=liveState||state,archive=current?.beam?.archive,source=current?.sources?.aoa;
  const session=source?.session||'',identity=`${source?.connection||''}/${session}`;
  if(identity!==archiveSession){archiveSession=identity;archiveRequest=null;archiveError='';}
  const control=current?.control?.session===session&&current?.control?.connection===source?.connection?current.control:null;
  const matchingSession=!session||current?.beam?.session===session;
  const available=Boolean(current?.mode==='live'&&archive?.available&&source?.connected&&matchingSession&&Date.now()/1000-source.last_seen<6);
  const pending=available&&(Boolean(archiveRequest&&controlPosting)||(control?.mode==='archive'&&control.status==='sent'));
  if(!pending)archiveRequest=null;
  $('enable-archive').checked=available&&Boolean(archiveRequest?.archive_enabled??archive?.enabled);
  $('enable-archive').disabled=!available||frozen||controlPosting||control?.status==='sent'||pending;
  const error=archiveError||(control?.mode==='archive'&&['unknown','error','rejected'].includes(control.status)?control.error:'')||archive?.error;
  const count=available&&Number.isInteger(archive?.pending_batches)&&archive.pending_batches>=0?archive.pending_batches:null;
  const queue=count===null?'待确认未知':`待确认 ${count} 批`;
  const status=error||!available?(error||'调试归档：设备未就绪'):pending?`调试归档：等待板端回执（当前${archive?.enabled?'开':'关'}）`:`调试归档：${archive?.enabled?'开 · 回传到电脑':'关 · 正常测量'}`;
  text('archive-status',`${status} · ${queue}`);
  $('archive-status').title='所有上位机共享板端开关状态；关闭后停止新批次原始归档，已生成数据继续等待电脑落盘确认。正常定位、测角结果仍实时显示。';
}
async function sendArchive(){
  if(controlPosting||frozen)return;
  const request={mode:'archive',archive_enabled:$('enable-archive').checked};
  archiveRequest=request;archiveError='';controlPosting=true;archiveControls();
  try{
    const response=await fetch('/api/beam',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)}),result=await response.json();
    if(!response.ok)throw Error(result.error||'归档设置未发送');
    if(liveState)liveState.control=result;
  }catch(error){archiveError=error.message;archiveRequest=null;}
  finally{controlPosting=false;await poll();archiveControls();}
}
function signalAxis(id,labels){
  $(id).replaceChildren();for(const label of labels){const span=document.createElement('span');span.textContent=label;$(id).append(span);}
}
function renderIQWave(){
  $('wave-preset').hidden=$('wave-scale').value!=='fixed';
  for(const [id,part] of [['wave-i',0],['wave-q',1]])$(id).setAttribute('aria-pressed',String(wavePart===part));
  if(!waveFrame){
    blank($('wave'),'等待本批完整 PL 合成 IQ');blank($('wave-overview'),'等待数据');
    $('wave').setAttribute('aria-label','PL合成IQ波形，等待本批数据');
    signalAxis('wave-axis',[]);$('wave-window').disabled=true;text('wave-window-label','等待数据');
    for(const id of ['wave-mean','wave-rms','wave-pp'])text(id,'—');
    return;
  }
  const full=waveFrame.parts[wavePart],window=iqWindow(full.length,Number($('wave-count').value),waveStart);
  waveStart=window.start;
  const values=full.slice(waveStart,waveStart+window.count),mode=$('wave-scale').value;
  const bounds=iqCodeBounds(values,mode,Number($('wave-preset').value)),stats=iqCodeStats(values);
  const frame=plotSeries($('wave'),values,bounds.min,bounds.max,null,'码值');
  // Zero in the signed PL component is code 2048 in the offset display.
  if(2048>=bounds.min&&2048<=bounds.max){
    const y=frame.bottom-(2048-bounds.min)/(bounds.max-bounds.min)*(frame.bottom-frame.top);
    add($('wave'),'line',{x1:frame.left,x2:frame.right,y1:y,y2:y,stroke:iqZeroOrange,'stroke-dasharray':'5 5','stroke-width':1.2,'data-role':'iq-zero'});
  }
  const first=64+waveStart,last=first+window.count-1,fs=waveFrame.sampleRate;
  $('wave').setAttribute('aria-label',`PL合成${wavePart?'Q':'I'}波形，样本${first}至${last}，${window.count}个连续点，纵轴${bounds.min}至${bounds.max}码值`);
  signalAxis('wave-axis',[first,(first+last)/2,last].map(v=>fmt(v/fs*1e6,2)+' μs'));
  text('wave-mean',fmt(stats.mean,1));text('wave-rms',fmt(stats.acRms,1));text('wave-pp',fmt(stats.peakToPeak,1));
  text('wave-window-label',`样本 ${first}–${last}`);
  const slider=$('wave-window');slider.max=window.maxStart;slider.value=waveStart;slider.disabled=!window.maxStart;
  slider.setAttribute('aria-valuetext',`样本 ${first} 至 ${last}，共 ${window.count} 点`);
  const outsideView=values.reduce((n,v)=>n+Number(v<bounds.min||v>bounds.max),0);
  text('wave-note',`${window.count} 点 · 2048 为零点${outsideView?` · ${outsideView} 点在视野外`:''}`);
  $('wave-note').title=`批次 ${waveFrame.runId} · 第 ${waveFrame.round+1} 轮 · 显示码值 = PL 有符号合成 IQ + 2048，非单路 ADC 采样或电压。选区为连续原始样本，未抽点或逐帧幅度归一化；原始 IQ 与完整 4096 点 FFT 不变。`;
  const overview=$('wave-overview'),allBounds=iqCodeBounds(full),ov=plotSeries(overview,full,allBounds.min,allBounds.max,null,'');
  if(2048>=allBounds.min&&2048<=allBounds.max){
    const y=ov.bottom-(2048-allBounds.min)/(allBounds.max-allBounds.min)*(ov.bottom-ov.top);
    add(overview,'line',{x1:ov.left,x2:ov.right,y1:y,y2:y,stroke:iqZeroOrange,'stroke-dasharray':'5 5','stroke-width':1.2,'data-role':'iq-zero'});
  }
  const x=ov.left+waveStart/(full.length-1)*(ov.right-ov.left),right=ov.left+(waveStart+window.count-1)/(full.length-1)*(ov.right-ov.left);
  const sel=add(overview,'g',{'data-role':'iq-window-selection'});
  const selectionInk=wavePart?color.orange:color.blue,handleInk=wavePart?'#b85b2b':'#395f78';
  add(sel,'rect',{x,y:ov.top,width:Math.max(2,right-x),height:ov.bottom-ov.top,rx:3,fill:selectionInk,'fill-opacity':.2,stroke:selectionInk,'stroke-width':1.2});
  for(const edge of [x,right])add(sel,'line',{x1:edge,x2:edge,y1:ov.top+9,y2:ov.bottom-9,stroke:handleInk,'stroke-width':3,'stroke-linecap':'round'});
  overview.setAttribute('aria-label',`全帧概览，${full.length}点，选中样本${first}至${last}；可拖动或使用下方窗口位置滑块`);
}
function iqPointerPosition(event){
  const svg=$('wave-overview'),rect=svg.getBoundingClientRect(),width=svg.viewBox.baseVal.width;
  const x=(event.clientX-rect.left)/rect.width*width,left=62,right=width-12;
  return {inside:x>=left&&x<=right,sample:(x-left)/(right-left)*(waveFrame.parts[0].length-1)};
}
function moveIQWindow(event){
  if(!waveDrag||event.pointerId!==waveDrag.id||!waveFrame)return;
  const {sample}=iqPointerPosition(event);
  waveStart=iqWindow(waveFrame.parts[0].length,Number($('wave-count').value),sample-waveDrag.offset).start;
  renderIQWave();
}
function startIQWindow(event){
  if(event.button!==0||!waveFrame)return;
  const total=waveFrame.parts[0].length,window=iqWindow(total,Number($('wave-count').value),waveStart);
  if(!window.maxStart)return;
  const {inside,sample}=iqPointerPosition(event);if(!inside)return;
  waveDrag={id:event.pointerId,offset:sample>=waveStart&&sample<waveStart+window.count?sample-waveStart:(window.count-1)/2};
  event.preventDefault();$('wave-overview').setPointerCapture(event.pointerId);moveIQWindow(event);
}
function beamSignals(force=false){
  observationControls();
  const a=state?.aoa,o=a?.observation;
  const hideBoundary=$('spectrum-hide-boundary').checked;
  const key='PLPHA'+(a?.session||'')+(a?.run_id||'')+(o?.round_index??0)+hideBoundary;
  if(!force&&key===signalKey)return;signalKey=key;
  text('wave-meta','PL PHA · 4032 Samples');text('spectrum-meta','PL PHA · Full 4096-point FFT');
  for(const id of ['wave-note','signal-note'])$(id).classList.remove('stale');
  const missing=o?.error||'等待本批完整 PL 合成 IQ';
  if(o?.valid&&o.wave_iq_s32le){
    const raw=bytes(o.wave_iq_s32le),view=new DataView(raw.buffer,raw.byteOffset,raw.byteLength);
    if(waveSession!==a.session){waveStart=0;waveSession=a.session;}
    waveFrame={parts:decodeIQCodes(view,o.sample_count),sampleRate:o.sample_rate_hz,session:a.session,runId:a.run_id,round:o.round_index};
    renderIQWave();
  }else{
    waveFrame=null;renderIQWave();blank($('wave'),o?.waveform_enabled?missing:'波形上报已关闭');
    text('wave-note',o?.waveform_enabled?missing:'开启后上报 PL 合成 IQ 的完整 4096 点。');
    $('wave-note').removeAttribute('title');
  }
  if(o?.valid&&o.spectrum){
    const s=o.spectrum,raw=bytes(s.db_f32le),view=new DataView(raw.buffer,raw.byteOffset,raw.byteLength);
    const values=Array.from({length:s.bins},(_,i)=>view.getFloat32(i*4,true));
    // Display only: omit the Nyquist tone and its two Hann neighbours on each
    // side of the circular FFT boundary. Keep all bins and their original dB.
    const visible=i=>!hideBoundary||Math.min(i,s.bins-i)>2;
    const hidden=values.reduce((n,_,i)=>n+Number(!visible(i)),0);
    const frame=plotSeries($('spectrum'),values,s.db_min,s.db_max,null,'相对全谱峰值 dB',visible);
    const peaks=markSpectrumPeaks($('spectrum'),values,s,visible,frame);
    renderPeakSnr(values,s,peaks);
    const start=s.frequency_start_hz,end=start+(s.bins-1)*s.frequency_step_hz,fs=o.sample_rate_hz;
    signalAxis('spectrum-axis',[start,-fs/4,0,fs/4,end].map(v=>(v>0?'+':'')+fmt(v/1e6,3)+' MHz'));
    const display=hidden?`显示 ${s.bins-hidden}/${s.bins} 频点 · 边界 ±${fmt(2*s.frequency_step_hz/1e3,2)} kHz 内 ${hidden} 点仅隐藏显示`:`全部 ${s.bins} 频点`;
    const startup=s.preprocessing==='zero64_hann4032'?'屏蔽前 64 点':'旧版：包含启动段';
    text('signal-note',`频率分辨率 ${fmt(s.frequency_step_hz/1e3,3)} kHz · ${startup} · Hann 窗 · ${hidden?`隐藏边界 ${hidden} 点`:'完整频谱'}`);
    $('signal-note').title=`批次 ${a.run_id} · PL 合成 IQ · ${s.preprocessing==='zero64_hann4032'?'前 64 点置零，后 4032 点加周期 Hann 窗；':'未上报启动段屏蔽契约；'}4096 点复 FFT · ${display} · 数值相对处理后全谱峰值，非 dBm；P1/P2 为局部谱峰，至少相隔 3 个频点。`;
  }else{
    blank($('spectrum'),o?.spectrum_enabled?missing:'频谱上报已关闭');signalAxis('spectrum-axis',[]);
    text('signal-note',o?.spectrum_enabled?missing:'开启后由复旦微 PS 对 PL 合成 IQ 计算完整频谱；不使用 ML 六路 FFT。');
    $('signal-note').removeAttribute('title');
    text('spectrum-snr','峰值 SNR · 等待频谱');
  }
}
function npu(){
  // [定位优先新增] 显示最近一次真实识别，并明确历史批次；不把旧类别贴到新位置。
  const n=state?.npu||state?.signal_npu,a=state?.aoa,c=n?.classification,show=Boolean(c&&n.status==='inferred');
  const matched=n?.run_id===a?.run_id,age=Math.max(0,(state?.server_time||Date.now()/1000)-(n?.received_at||0));
  const index=show?c.probabilities.indexOf(Math.max(...c.probabilities)):-1;
  text('class',show?c.classes[index]:'—');text('confidence',show?`${fmt(c.probabilities[index]*100,1)}%`:'—');
  const names={FM:'频率调制',AM:'幅度调制',CW:'连续波',noise:'噪声',OOK:'开关键控',PM:'相位调制'};
  text('class-name',show?names[c.classes[index]]:(n?.status==='error'?'本批推理失败':n?.status==='preprocessed'?'仅预处理':handoffs[a?.handoff]||'尚无匹配批次'));
  const paused=a?.beam?.acquisition?.applied_mode==='locate';
  text('npu-state',state?.mode==='demo'?'合成演示':paused?`NPU 已暂停${show?` · 上次识别于 ${fmt(age,1)} 秒前`:''}`:n?(n.npu_executed?matched?'NPU 实际推理':`最近识别 · ${fmt(age,1)} 秒前`:n.backend==='host'?'CPU 参考':'未执行 NPU'):'等待数据');
  $('probabilities').replaceChildren();(show?c.classes:['FM','AM','CW','noise','OOK','PM']).forEach((label,i)=>{const row=document.createElement('div');row.className='ao-prob'+(i===index?' active':'');const track=document.createElement('div');track.className='ao-prob-track';const bar=document.createElement('i');bar.style.width=`${show?c.probabilities[i]*100:0}%`;track.append(bar);row.append(label,track,show?`${fmt(c.probabilities[i]*100,1)}%`:'—');$('probabilities').append(row);});
  text('npu-note',n?.error||`批次：${n?.run_id||'—'}${show&&!matched?' · 历史识别，不对应当前定位点':''}${paused?' · 定位模式不提交识别任务':''}`);
}
function kv(parent,pairs){parent.replaceChildren();for(const [k,v]of pairs){const div=document.createElement('div'),span=document.createElement('span'),b=document.createElement('b');span.textContent=k;b.textContent=v;div.append(span,b);parent.append(div);}}
function render(){
  if(!state)return;
  jointHeatHistory.update(state.aoa);
  syncProcessingState();
  const angleMode=processingMode==='angle6',scanMode=processingMode==='scan6',v12=v12Result(state.aoa);
  const autoOption=$('beam-mode').querySelector('option[value="auto"]');
  if(autoOption)autoOption.textContent=v12?angleMode||scanMode?'Auto: Board ML + CBF':'Auto: Board Position':angleMode&&joint()?.selection?'Auto: Joint ML Selection':scanMode?scanAutomaticLabel(scanData()):'Auto: Highest LoS Score';
  layoutProcessing();
  const a=state.aoa,candidates=state.candidates||[];
  renderGeometry(a);
  if(!angleMode&&!scanMode){
    selected=Math.min(selected,Math.max(0,candidates.length-1));
    autoSelect();
    renderPlot();renderInfo();heatmaps();
  }else if(v12)renderV12Angle6();
  else if(angleMode)renderAngle6();
  else renderScan6();
  beamSignals();renderSpectrumWaterfall();npu();renderProcessing();
  const age=a?Math.max(0,Date.now()/1000-a.received_at):null;
  const stale=age>(a?.beam?.acquisition?.applied_mode==='identify'?30:10);
  const mode=state.mode==='demo'?'DEMO / 合成数据':a?.capture_mode==='offline_fft'?'离线 FFT 回放':'实时 / TCP';
  const connected=Object.entries(state.sources).filter(([,s])=>s.connected&&Date.now()/1000-s.last_seen<6).map(([name])=>name.toUpperCase());
  text('connection',`${mode} · ${connected.length?connected.join(' + '):'等待板端'}`);
  text('batch',a?`${a.run_id} · ${a.status} · 接收于 ${fmt(age,1)} 秒前${angleMode||scanMode?' · 测角':''}${frozen?' · 画面已冻结':''}${stale?' · 数据已过期':''}`:state.board_link?`等待复旦微推送 · ${state.board_link.status==='connected'?'已连接': '正在连接'} ${state.board_link.host}:${state.board_link.port}`:'等待板端数据 · 旧版 TCP 接收模式');
  $('batch').classList.toggle('stale',stale);
  text('origin',state.mode==='demo'?'合成演示':a?.capture_mode||'板端上报');
  const parameterRows=[['ADC Sample Rate',a?`${fmt(a.sample_rate_hz/1e6,1)} MS/s`:'—'],['Baseband Rate',a?`${fmt(a.baseband_sample_rate_hz/1e6,1)} MS/s`:'—'],['Array Baseline',a?`${fmt(Math.hypot(a.centres[1][0]-a.centres[0][0],a.centres[1][1]-a.centres[0][1]),4)} m`:'—'],['Angle / NPU Latency',`${fmt(a?.analysis_ms,0)} / ${fmt(state.npu?.total_ms,0)} ms`]];
  if(angleMode||scanMode)parameterRows.splice(3,0,['Analysis Mode','ANGLE']);
  kv($('parameters'),parameterRows);
  const counts=a?.counts||{},jd=angleMode||v12&&scanMode?joint():null;
  const qualityRows=[['IPC 已交付',counts.ipc_sent??'—'],['NPU 忙丢批',counts.ipc_busy??'—'],['无消费者丢批',counts.ipc_absent??'—'],['IPC 错误',counts.ipc_errors??'—'],['遥测丢包计数',Object.values(state.sources).reduce((v,s)=>v+(s.transport.dropped||0),0)],['拒收帧',state.rejected]];
  qualityRows.push(angleMode?['G1 / G2 残差 J/E','角度模式不适用']:scanMode?['G1 / G2 残差 J/E','扫描模式不适用']:['G1 / G2 残差 J/E',`${fmt(group(0)?.residual_ratio,4)} / ${fmt(group(1)?.residual_ratio,4)}`]);
  if(angleMode||v12&&scanMode)qualityRows.push(['联合残差 / Frobenius 条件数',`${fmt(jd?.residual_ratio,4)} / ${fmt(jd?.condition,2)}`]);
  else if(scanMode)qualityRows.push(['扫描峰候选',`${scanCandidates().length} 个（非物理来波数量）`],['PL 复核',scanVerificationSummary(scanData())]);
  const prep=a?.preprocessing;
  const backend={pl:'PL 加窗 / PS 归一化',ps:'PS 软件预处理',none:'未执行'}[prep?.ml_backend]||'旧版未上报处理端';
  qualityRows.push(['ML 预处理',prep?`${backend} · ${prep.ml_startup_samples} 点屏蔽 / ${prep.ml_retained_samples} 点有效 · 4096 点 FFT`:'板端未上报'],
    ['ML FFT 原始载荷',({'pl-hann64':'PL 已加窗',raw:'PL 未加窗',unknown:'未确定'})[prep?.fft_payload_preprocessing]||'旧数据未标记'],
    ['ADC 去直流',prep?prep.adc_dc_applied?'已执行':'未执行':'板端未上报'],
    ['自动选路',a?.beam?.auto_selection==='quality_cascade_energy_fallback'?'实验质量级联 · 能量兜底候选':a?.beam?.auto_selection==='single_dual_triple_cascade'?'三径 → 双径 → 单径 · 同批逐级退化':a?.beam?.auto_selection==='single_dual_gated_triple'?'单径定范围 · 双径辅助 · 三径选角':a?.beam?.auto_selection==='current_batch_strongest'?'当前批次最强路径 · 无关联 / 无迟滞':a?.beam?.path_tracking?.enabled?'路径关联 / 迟滞':'板端未上报']);
  if(v12)qualityRows.push(['V2.0 板端选择',angleMode||scanMode?`${v12.joint.model_paths} 径 · ${fmt(v12.joint.angle_deg,2)}°`:v12.localization.selected_id||'无有效定位点']);
  qualityRows.push(['检测门限',a?.min_bin_power?String(a.min_bin_power):'未配置噪声门限'],['FFT / 分析状态',a?.error||(!a?'—':a.complete?'完整':'未完成')],['PHA 有效性',a?.pha_integrity==='slot_format_only_no_hardware_validity'?'仅格式校验；硬件有效计数未上报':state.mode==='demo'?'合成演示':'未上报'],['信号显示批次',state.signal_npu?.run_id||'—']);
  kv($('quality'),qualityRows);
  $('events').replaceChildren();for(const e of state.events){const p=document.createElement('p');p.textContent=`${new Date(e.time*1000).toLocaleTimeString()} · ${e.text}`;$('events').append(p);}
  const endpoint=state.board_link?`复旦微 ${state.board_link.host}:${state.board_link.port}`:`TCP ${state.tcp_port}`;
  text('status',angleMode
    ? `${mode}　·　${endpoint}　·　接收 ${(state.received_bytes/1048576).toFixed(2)} MB　·　${jointPathList().length} 条联合路径　·　${frozen?'画面已冻结':'自动更新'}`
    : scanMode
    ? `${mode}　·　${endpoint}　·　接收 ${(state.received_bytes/1048576).toFixed(2)} MB　·　${scanCandidates().length} 个扫描峰候选　·　${frozen?'画面已冻结':'自动更新'}`
    : `${mode}　·　${endpoint}　·　接收 ${(state.received_bytes/1048576).toFixed(2)} MB　·　${candidates.length} 个路径组合　·　${frozen?'画面已冻结':'自动更新'}`);
  text('network-config',state.board_link
    ? `复旦微：${state.board_link.host}:${state.board_link.port} · ${state.board_link.status==='connected'?'已订阅':'重连中'} · 多机接收同一份数据${state.board_link.error?' · '+state.board_link.error:''}`
    : `旧版接收端：本机 IPv4:${state.tcp_port} · 当前来源：${Object.entries(state.sources).map(([k,v])=>k+' '+v.peer).join(' / ')||'尚无连接'}`);
}
async function poll(){
  if(fetching)return;fetching=true;
  try {const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);const incoming=await response.json();liveState=incoming;beamSpectrumHistory.update(incoming.aoa);if(!frozen){state=incoming;render();}renderBeamControls();}
  catch(e){text('connection','本机服务连接中断');text('batch','数据停止更新 · '+e.message);$('batch').classList.add('stale');renderBeamControls();renderProcessing();}
  finally{fetching=false;}
}
function renderBeamControls(){
  archiveControls();
  const s=liveState||state,b=s?.beam,source=s?.sources.aoa;
  syncProcessingState();
  const angleMode=processingMode==='angle6',scanMode=processingMode==='scan6',v12=v12Result(state?.aoa);
  const fresh=source?.connected&&Date.now()/1000-source.last_seen<6&&b&&Date.now()/1000-b.received_at<6;
  const applied=s?.aoa?.session===source?.session&&s.aoa?.beam?.registers_verified&&
    s.aoa.beam.applied_run_id===s.aoa.run_id?s.aoa.beam:null;
  if(fresh&&!beamFormInitialized){
    $('beam-mode').value=b.mode;
    if(applied&&Number.isFinite(applied.angle_deg))$('beam-angle').value=String(applied.angle_deg);
    beamFormInitialized=true;
  }
  // 测角由板端联合方案选角；人工只保留独立的 Manual Angle。
  const followBlocked=angleMode||scanMode;
  const followOption=$('beam-mode').querySelector('option[value="follow"]');
  if(followOption){followOption.hidden=followBlocked;followOption.disabled=followBlocked;followOption.style.display=followBlocked?'none':'';}
  if(followBlocked&&$('beam-mode').value==='follow')$('beam-mode').value='manual';
  const mode=$('beam-mode').value;
  const [lo,hi]=manualBeamRange();
  for(const id of ['beam-angle','beam-angle-slider']){$(id).min=lo;$(id).max=hi;}
  const draft=manualBeamAngle();
  if(draft!==null)$('beam-angle-slider').value=String(draft);
  text('beam-angle-min',`${fmt(lo,0)}°`);text('beam-angle-max',`${fmt(hi,0)}°`);
  text('beam-preview-angle',`${fmt(draft,1)}°`);
  const draftPreview=mode==='manual'&&draft!==null?v12ManualBeamPreview(s?.aoa,draft):null;
  const draftMatches=!!draftPreview&&Number.isFinite(applied?.angle_deg)&&Math.abs(applied.angle_deg-draft)<.05;
  text('beam-manual-status',draft===null?`请输入 ${fmt(lo,1)}°～${fmt(hi,1)}°`:
    `已应用 ${fmt(applied?.angle_deg,1)}° · 预选 ${fmt(draft,1)}°${draftMatches?' · 与已应用一致':draftPreview?' · 未应用':' · 预览等待有效指向相位回执'}`);
  text('angle-draft-label',draftMatches?'预选 · 与已应用一致':'预选 · 未应用');
  $('angle-draft-legend').hidden=mode!=='manual'||!draftPreview;
  const versionOK=b?.pl_version===b?.required_pl_version;
  const pending=s?.control?.status==='sent',candidate=state?.candidates?.[selected];
  const enabled=s?.mode==='live'&&fresh&&b?.available&&versionOK&&!frozen&&!controlPosting&&!pending;
  const ac=b?.acquisition,acEnabled=enabled&&ac?.available;
  for(const mode of ['locate','identify']){
    $('mode-'+mode).disabled=!acEnabled;
    $('mode-'+mode).setAttribute('aria-pressed',ac?.applied_mode===mode);
  }
  text('acquisition-status',!ac?'等待采集状态':ac.command_status==='pending'||pending&&s?.control?.mode==='acquisition'?'采集切换中 · 等待本批完成':ac.command_status==='error'||ac.command_status==='rejected'?ac.error:ac.applied_mode==='locate'?'持续定位采集 · NPU 暂停':ac.applied_mode==='identify'?'交替定位与识别采集':'等待首个采集批次');
  $('beam-angle-field').hidden=mode!=='manual';$('beam-target').hidden=mode!=='follow';
  text('beam-target',`待应用：${candidate?.id||'—'}`);
  $('beam-target').title='人工覆盖自动选角；后续按邻近角度跟随，不是单／双／三径自动选择';
  $('beam-apply').disabled=!enabled||(mode==='manual'&&draft===null)||(mode==='follow'&&(v12?!candidate?.valid:candidate?.status!=='candidate'));
  $('beam-pause').disabled=!enabled||!b?.tracking;
  const beamAutomatic=$('beam-mode').querySelector('option[value="auto"]');
  beamAutomatic.disabled=!b?.auto_supported;
  if(v12)beamAutomatic.textContent=angleMode||scanMode?'Auto: Board ML + CBF':'Auto: Board Position';
  else if(scanMode)beamAutomatic.textContent=scanAutomaticLabel(s?.aoa?.beam_scan);
  const preview=$('beam').querySelector('option[value="preview"]');
  if(preview){preview.disabled=angleMode||scanMode;if((angleMode||scanMode)&&$('beam').value==='preview')$('beam').value='current';}
  const names={idle:'未设置',pending:'等待批次完成',applied:'寄存器已校验',tracking:'跟踪中 · 寄存器已校验',holding:'保持相位 · 等待合格路径',paused:'跟踪已暂停',error:'设置失败',rejected:'命令被拒绝'};
  const failed={unknown:'命令结果未知',rejected:'命令被拒绝',error:'命令执行失败'};
  let info=s?.mode==='demo'?'演示模式 · 不发送硬件命令':failed[s?.control?.status]||
    (!fresh?'板端未连接':!b?.available?'板端不提供控制':!versionOK?(b?.pl_version?'需更新配套 PL 位流':'等待采集读取 PL 版本'):
    pending?'已发送 · 等待板端执行':names[b.status]||'等待板端');
  if(!pending&&b?.tracking&&b.status==='pending'&&applied)info='跟踪中 · 等待下一批回执';
  if(s?.control?.error)info+=' · '+s.control.error;
  if(b?.error&&b.error!==s?.control?.error)info+=' · '+b.error;
  if(fresh&&applied){
    let actual;
    if(angleMode){
      const target=applied.target;
      actual=target?.kind==='angle'&&Number.isFinite(target.angle_deg)
        ? `${target.track_id?`T${target.track_id} / `:''}P${(target.path_id??0)+1} · ${fmt(target.angle_deg,1)}°`
        : `${fmt(applied.angle_deg,1)}°`;
      info+=` · 已校验批次：测角 · ${actual}`;
    }else if(scanMode){
      info+=` · 已校验批次：测角 · ${fmt(applied.angle_deg,1)}°`;
    }else{
      actual=['follow','auto'].includes(applied.mode)?`${applied.mode==='auto'?'自动 · ':''}${applied.target?`G1/P${applied.paths[0]+1} × G2/P${applied.paths[1]+1}`:'保持相位'} · ${fmt(applied.angle_deg,1)}°`:applied.mode==='equal'?'等权':`${fmt(applied.angle_deg,1)}°`;
      info+=` · 已校验批次：${actual}`;
    }
  }
  const tracking=b?.path_tracking;
  if(fresh&&angleMode&&tracking?.active){
    const reason={acquiring:'确认路径中',acquired:'已获取路径',following:'路径已关联',
      challenger:'候选占优',switched:'已切换路径',holding_missing:'路径暂失',holding_ambiguous:'关联有歧义',
      no_associated_path:'等待路径',unqualified:'等待合格测角',lost:'重新获取',reset:'重新确认'};
    info+=` · ${reason[tracking.reason]||'路径关联'} · 迟滞 ${fmt(tracking.switch_margin_db,0)} dB / ${tracking.switch_frames} 批`;
    if(tracking.challenger_track_id)info+=` · T${tracking.challenger_track_id} ${tracking.challenger_frames}/${tracking.selected_track_id?tracking.switch_frames:tracking.confirm_frames}`;
  }
  if(fresh&&b?.mode==='follow')info+=' · 人工覆盖';
  if(frozen)info+=' · 画面已冻结';
  text('beam-control-status',controlError||info);
  kv($('beam-phases'),Array.from({length:6},(_,i)=>[`CH${i}`,fresh&&applied?`${fmt(applied.phase_rad[i]/rad,2)}°`:'—']));
}
async function sendBeam(mode,acquisitionMode){
  if(controlPosting||frozen)return;
  const angleMode=processingMode==='angle6',scanMode=processingMode==='scan6',request={mode};
  if(mode==='follow'&&(angleMode||scanMode)){controlError='测角模式不提供候选跟随，请使用 Manual Angle 或 Auto';renderBeamControls();return;}
  if(mode==='acquisition')request.acquisition_mode=acquisitionMode;
  if(mode==='manual'){
    request.angle_deg=manualBeamAngle();
    if(request.angle_deg===null){controlError='请输入搜索范围内的有效角度';renderBeamControls();return;}
  }
  if(mode==='follow'){
    request.candidate=selected;request.candidate_id=state?.candidates?.[selected]?.id;request.run_id=state?.aoa?.run_id;
  }
  controlError='';controlPosting=true;renderBeamControls();
  try{
    const response=await fetch('/api/beam',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
    const result=await response.json();if(!response.ok)throw Error(result.error||'命令未发送');
    if(liveState)liveState.control=result;
    if(mode==='auto'){$('selection-mode').value='auto';$('beam').value='current';jointManual=false;scanManual=false;}
  }catch(e){controlError=e.message;}
  finally{controlPosting=false;await poll();}
}
$('beam-mode').onchange=manualBeamChanged;
$('beam-angle-slider').oninput=()=>{$('beam-angle').value=$('beam-angle-slider').value;manualBeamChanged();};
$('beam-angle').oninput=manualBeamChanged;
$('beam-angle').onchange=()=>{const angle=manualBeamAngle();if(angle!==null)$('beam-angle').value=String(angle);manualBeamChanged();};
$('observe-waveform').onchange=sendObservation;
$('observe-spectrum').onchange=sendObservation;
$('enable-archive').onchange=sendArchive;
$('spectrum-hide-boundary').onchange=()=>{beamSignals(true);renderSpectrumWaterfall(true);};
$('wave-count').onchange=()=>renderIQWave();
$('wave-scale').onchange=()=>renderIQWave();
$('wave-preset').onchange=()=>renderIQWave();
$('wave-i').onclick=()=>{wavePart=0;renderIQWave();};
$('wave-q').onclick=()=>{wavePart=1;renderIQWave();};
$('wave-window').oninput=()=>{waveStart=Number($('wave-window').value);renderIQWave();};
$('wave-overview').onpointerdown=startIQWindow;
$('wave-overview').onpointermove=moveIQWindow;
for(const type of ['pointerup','pointercancel','lostpointercapture'])$('wave-overview').addEventListener(type,()=>{waveDrag=null;});
$('spectrum-waterfall-palette').onchange=()=>renderSpectrumWaterfall(true);
$('spectrum-waterfall-range').onchange=()=>renderSpectrumWaterfall(true);
for(const id of ['angle-layer-scan','angle-layer-ml','angle-layer-beam'])$(id).onchange=()=>renderV12Angle6();
$('beam-apply').onclick=()=>sendBeam($('beam-mode').value);
$('beam-pause').onclick=()=>sendBeam('pause');
$('mode-locate').onclick=()=>sendBeam('acquisition','locate');
$('mode-identify').onclick=()=>sendBeam('acquisition','identify');
$('processing-position').onclick=()=>{if(processingMode!=='position')sendProcessing('position',jointPathCount);};
$('processing-angle6').onclick=()=>{if(processingMode!=='angle6')sendProcessing('angle6',jointPathCount);};
$('processing-scan6').onclick=()=>{if(processingMode!=='scan6')sendProcessing('scan6',3);};
$('scan6-selection').onchange=()=>{scanManual=$('scan6-selection').value==='manual';if(!scanManual)scanSelected=bestScanCandidateIndex();renderScan6();renderBeamControls();};
$('freeze').onclick=()=>{frozenWaterfallView=frozen?null:beamSpectrumHistory.view(state?.server_time);frozen=!frozen;text('freeze',frozen?'Resume':'Freeze');renderBeamControls();if(!frozen)poll();else render();};
$('beam').onchange=()=>renderPlot();$('range').onchange=()=>renderPlot();$('palette').onchange=()=>heatmaps();
$('joint-palette').onchange=()=>renderJointHeatmaps(joint()?.profile,selectedJointPath());
$('selection-mode').onchange=()=>{autoSelect();renderPlot();renderInfo();renderBeamControls();};
$('plot-mode').onchange=()=>{renderPlot();renderInfo();};
$('config-open').onclick=()=>$('settings').showModal();
function updateBaseline(){text('baseline',`Array Baseline = ${fmt(2*Number($('spacing').value)+Number($('gap').value),4)} m`);}
$('spacing').oninput=updateBaseline;$('gap').oninput=updateBaseline;
$('download-array').onclick=()=>{
  const d=Number($('spacing').value),gap=Number($('gap').value);
  if(!Number.isFinite(d)||!Number.isFinite(gap)||d<=0||gap<0||d>1000||gap>10000){text('config-message','请输入有效间距：d > 0，净间距 ≥ 0。');return;}
  const positions=[0,d,2*d,2*d+gap,3*d+gap,4*d+gap],centre=positions[5]/2;
  const csv='channel,x_m,y_m,correction_real,correction_imag\n'+positions.map((x,i)=>`${i},${(x-centre).toFixed(9)},0,1,0`).join('\n')+'\n';
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'})),a=document.createElement('a');a.href=url;a.download='array.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);text('config-message','已生成文件；放入板端部署目录 config/array.csv 后重启采集。');
};
new ResizeObserver(()=>{if(processingMode==='angle6')renderAngle6();else if(processingMode==='scan6'&&!v12Result(state?.aoa))renderScan6();else if(processingMode==='position')renderPlot();beamSignals(true);}).observe($('plot'));
new ResizeObserver(()=>{if(v12FullArrayVisible(state?.aoa,processingMode))renderV12Angle6();else if(processingMode==='angle6')renderAngle6();}).observe($('angle6-plot'));
new ResizeObserver(()=>{if(v12FullArrayVisible(state?.aoa,processingMode))renderV12Angle6();}).observe($('scan-polar-plot'));
new ResizeObserver(()=>{if(processingMode==='scan6')renderScan6();}).observe($('scan6-plot'));
new ResizeObserver(()=>beamSignals(true)).observe($('wave'));
poll();setInterval(poll,250);
