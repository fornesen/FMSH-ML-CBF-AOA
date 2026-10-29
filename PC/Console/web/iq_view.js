'use strict';
// Display-only helpers. Preserve signed PL IQ values and use a fixed zero offset;
// never normalize each frame's amplitude or feed display codes back into FFT/NPU.
function decodeIQCodes(view,sampleCount,first=64){
  const count=Math.max(0,Math.min(sampleCount,Math.floor(view.byteLength/8))-first);
  return [0,1].map(part=>Float64Array.from({length:count},(_,i)=>2048+view.getInt32((first+i)*8+part*4,true)));
}
function iqWindow(total,requested,start=0){
  const count=Math.min(total,Math.max(1,Math.round(requested)||512)),maxStart=Math.max(0,total-count);
  return {count,start:Math.max(0,Math.min(maxStart,Math.round(start)||0)),maxStart};
}
function iqCodeBounds(values,mode='auto',zoom=1){
  if(mode==='fixed'){
    const factor=[1,2,4,8,16].includes(Number(zoom))?Number(zoom):1,half=2048/factor;
    return {min:2048-half,max:2048+half};
  }
  if(!values.length)return {min:0,max:4096};
  const lo=Math.min(...values),hi=Math.max(...values),pad=Math.max(2,(hi-lo)*.1);
  // The PL sum is wider than one 12-bit ADC. Auto/overview must include every
  // original code even when the user-selected 0..4096 fixed viewport would clip.
  return {min:Math.floor(lo-pad),max:Math.ceil(hi+pad)};
}
function iqCodeStats(values){
  if(!values.length)return null;
  const mean=values.reduce((s,v)=>s+v,0)/values.length;
  return {mean,peakToPeak:Math.max(...values)-Math.min(...values),
    acRms:Math.sqrt(values.reduce((s,v)=>s+(v-mean)**2,0)/values.length),
    outside:values.reduce((s,v)=>s+Number(v<0||v>4096),0)};
}
if(typeof module!=='undefined')module.exports={decodeIQCodes,iqWindow,iqCodeBounds,iqCodeStats};
