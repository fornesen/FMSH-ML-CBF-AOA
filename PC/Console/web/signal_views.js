'use strict';
// Display helpers for the board's PL beamformed IQ spectrum; no FFT runs here.
class BeamSpectrumHistory {
  constructor(){this.rows=[];this.key='';this.axis=null;this.version=0;this.maxAge=64;this.maxRows=512;}
  update(a){
    if(!a||!Number.isFinite(a.received_at))return false;
    const o=a.observation,s=o?.spectrum,fs=o?.sample_rate_hz;
    const key=JSON.stringify([a.session,a.processing_mode,a.rf_hz,a.dds_hz,fs,s?.bins||4096,s?.frequency_start_hz??-fs/2,s?.frequency_step_hz??fs/4096]);
    if(key!==this.key){this.key=key;this.rows=[];this.axis=null;this.version++;}
    const id=`${a.run_id}:${o?.round_index??0}`,last=this.rows.at(-1);
    if(this.rows.some(r=>r.id===id))return false;
    // A wall-clock discontinuity starts a new timeline instead of rejecting future batches.
    if(last&&a.received_at<last.time){this.rows=[];this.version++;}
    if(Number.isFinite(fs)&&fs>0&&!this.axis)this.axis={bins:4096,start:-fs/2,step:fs/4096};
    let values=null;
    if(o?.valid&&o.spectrum_enabled&&s&&s.bins===4096&&s.reference==='per_frame_peak'){
      const raw=Uint8Array.from(atob(s.db_f32le),c=>c.charCodeAt(0));
      if(raw.byteLength===s.bins*4){
        const view=new DataView(raw.buffer,raw.byteOffset,raw.byteLength);
        const decoded=Float32Array.from({length:s.bins},(_,i)=>view.getFloat32(i*4,true));
        if(decoded.every(Number.isFinite)){
          values=decoded;this.axis={bins:s.bins,start:s.frequency_start_hz,step:s.frequency_step_hz};
        }
      }
    }
    this.rows.push({id,time:a.received_at,values});
    this.rows=this.rows.filter(r=>r.time>=a.received_at-this.maxAge-1).slice(-this.maxRows);
    this.version++;return true;
  }
  view(now){
    const latest=this.rows.at(-1);
    if(!latest||!this.axis)return null;
    const anchor=Number.isFinite(now)?now:latest.time;
    const cells=[];
    for(const row of this.rows){
      const age=anchor-row.time;
      if(age<0||age>=this.maxAge)continue;
      // Each received batch is a point in time, drawn as a one-pixel glyph.
      cells.push({...row,top:age,bottom:age});
    }
    return {axis:this.axis,span:this.maxAge,cells,anchor,
      counts:{valid:cells.filter(c=>c.values).length,missing:cells.filter(c=>!c.values).length}};
  }
}

function spectrumPeaks(values,visible,floor){
  const n=values.length,candidates=[];
  for(let i=0;i<n;i++){
    const v=values[i];if(!visible(i)||!Number.isFinite(v)||v<=floor)continue;
    const a=values[(i+n-1)%n],b=values[(i+1)%n];
    // Hidden neighbours still determine whether this is a real local maximum.
    if(v>=a&&v>=b&&(v>a||v>b))candidates.push(i);
  }
  candidates.sort((a,b)=>values[b]-values[a]||a-b);
  const peaks=[];
  for(const i of candidates){
    if(peaks.every(j=>Math.min(Math.abs(i-j),n-Math.abs(i-j))>2))peaks.push(i);
    if(peaks.length===2)break;
  }
  return peaks;
}

function spectrumRasterRow(values,columns,hideBoundary){
  const n=values.length,result=new Float32Array(columns).fill(-Infinity);
  // All bins contribute; retain the strongest bin in each display pixel column.
  for(let i=0;i<n;i++){
    if(hideBoundary&&Math.min(i,n-i)<=2)continue;
    const column=Math.min(columns-1,Math.floor(i*columns/n));
    if(Number.isFinite(values[i]))result[column]=Math.max(result[column],values[i]);
  }
  return result;
}

function estimatePeakSnr(values,index,peaks,stepHz,floor=-120){
  // Keep pages opened during the update compatible with the earlier six-argument call.
  if(typeof stepHz==='function'){stepHz=floor;floor=arguments[5]??-120;}
  if(!Number.isFinite(stepHz)||stepHz<=0)return {snrDb:null,reason:'页面版本不匹配，请刷新',noiseBins:0};
  // A single-bin peak SNR estimate, not integrated modulation/channel SNR.
  // For a noise-only periodogram with exponential bin power, mean=median/ln(2).
  const radius=Math.max(16,Math.floor(500000/stepHz)),guard=6,noise=[],left=[],right=[];
  for(let i=Math.max(0,index-radius);i<=Math.min(values.length-1,index+radius);i++){
    if(Math.min(i,values.length-i)<=2||!Number.isFinite(values[i])||values[i]<=floor+.001||
       [index,...peaks].some(p=>Math.min(Math.abs(i-p),values.length-Math.abs(i-p))<=guard))continue;
    const power=10**(values[i]/10);noise.push(power);(i<index?left:right).push(power);
  }
  if(noise.length<32)return {snrDb:null,reason:'附近可用噪底不足',noiseBins:noise.length};
  const median=a=>{const sorted=a.slice().sort((a,b)=>a-b),m=sorted.length>>1;return sorted.length%2?sorted[m]:(sorted[m-1]+sorted[m])/2;};
  const noisePower=median(noise)/Math.LN2,excess=10**(values[index]/10)-noisePower;
  if(excess<=0)return {snrDb:null,reason:'峰值未高于估算噪底',noiseBins:noise.length};
  const gradientDb=left.length>8&&right.length>8?Math.abs(10*Math.log10(median(left)/median(right))):null;
  return {snrDb:10*Math.log10(excess/noisePower),noiseDb:10*Math.log10(noisePower),
    noiseBins:noise.length,neighbourhoodHz:500000,guardBins:guard,rbwHz:1.5*stepHz,
    noiseBandBins:[Math.max(0,index-radius),Math.min(values.length-1,index+radius)],
    gradientDb,reason:gradientDb!==null&&gradientDb>6?'附近噪底有明显斜率，估算可能偏移':''};
}
if(typeof module!=='undefined')module.exports={BeamSpectrumHistory,estimatePeakSnr,spectrumPeaks,spectrumRasterRow};
