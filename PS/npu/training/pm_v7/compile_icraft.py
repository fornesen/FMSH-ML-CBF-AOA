"""Native Windows Icraft 3.36.1 six-class compile; training-only calibration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import numpy as np
from signal_model import ROOT, CLASSES

NAME='fm_sixsum_pm_v7'


def calibrate(exp):
    cal=exp/'calibration';cal.mkdir()
    rows=[json.loads(x) for x in (exp/'manifest.jsonl').read_text().splitlines()]
    rng=np.random.default_rng(20261027)
    groups=[]
    for c in range(6):
        candidates=[r for r in rows if r['split']=='train' and r['class_index']==c]
        rng.shuffle(candidates);groups.append(candidates[:25])
    selected=[groups[c][i] for i in range(25) for c in range(6)]
    forward=[];provenance=[]
    for i,row in enumerate(selected):
        inputs=[]
        with np.load(row['feature_path']) as fields:
            for key in ('tf','spectrum','amplitude'):
                data=fields[key][:,0].transpose(1,2,0)[None].astype('<f4')/255
                filename='sample%03d_%s.ftmp'%(i,key)
                data.tofile(str(cal/filename));inputs.append(filename)
        forward.append(';'.join(inputs))
        provenance.append(dict(name=row['name'],class_index=row['class_index'],source_split=row['split'],
            source_iq_sha256=row['iq_sha256'],feature_sha256=hashlib.sha256(Path(row['feature_path']).read_bytes()).hexdigest(),inputs=inputs))
    assert all(r['source_split']=='train' for r in provenance)
    (cal/'forward_list.txt').write_text('\n'.join(forward)+'\n')
    (cal/'sample_provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    return cal


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment',type=Path,default=ROOT/'experiments/classifier_sixsum_pm_v7')
    p.add_argument('--sdk',type=Path,default=os.environ.get('ICRAFT_ROOT'),
                   required=not os.environ.get('ICRAFT_ROOT'),help='Icraft SDK directory; defaults to ICRAFT_ROOT')
    p.add_argument('--dtype',choices=['int8','fp16'],default='int8')
    p.add_argument('--stage',choices=['all','parse','optimize','quantize','adapt','generate'],default='all')
    a=p.parse_args();exp=a.experiment.resolve();sdk=a.sdk.resolve()
    out=exp/'deployment'/('icraft_'+a.dtype);out.mkdir(exist_ok=True)
    evidence=out/'logs';evidence.mkdir(exist_ok=True)
    static=exp/'deployment/npu_static.pt'
    meta=json.loads(static.with_suffix('.json').read_text())
    assert meta['classes']==CLASSES and meta['output_shape']==[1,6]
    assert hashlib.sha256(static.read_bytes()).hexdigest()==meta['export_sha256']
    cal=exp/'calibration'
    if not cal.exists():cal=calibrate(exp)
    env=dict(os.environ);env['PATH']=str(sdk/'bin')+os.pathsep+env.get('PATH','')
    env['OMP_NUM_THREADS']='4'
    stages={
        'parse':['--net_name',NAME,'--network',str(static),'--jr_path',str(out),'--framework','pytorch','--target','zhuge','--frame_version','2.0.1',
                 '--inputs','1,64,640,4;1,1,640,4;1,1,48,4','--inputs_layout','NHWC;NHWC;NHWC',
                 '--inputs_dtype','FP32;FP32;FP32','--pre_method','nop;nop;nop',
                 '--pre_scale','1,1,1,1;1,1,1,1;1,1,1,1','--pre_mean','0,0,0,0;0,0,0,0;0,0,0,0','--channel_swap','0,1,2,3;0,1,2,3;0,1,2,3'],
        'optimize':['--json',str(out/(NAME+'_parsed.json')),'--raw',str(out/(NAME+'_parsed.raw')),'--jr_path',str(out),'--target','zhuge'],
        'quantize':['--json',str(out/(NAME+'_optimized.json')),'--raw',str(out/(NAME+'_optimized.raw')),'--jr_path',str(out),'--target','zhuge',
                    '--qdtype',a.dtype,'--forward_mode','image','--forward_dir',str(cal),'--forward_list',str(cal/'forward_list.txt'),
                    '--saturation','kld','--per','channel','--batch','5','--no_transinput','true','--log_path',str(evidence/'native_quant')],
        'adapt':['--json',str(out/(NAME+'_quantized.json')),'--raw',str(out/(NAME+'_quantized.raw')),'--jr_path',str(out),'--target','zhuge'],
        'generate':['--json',str(out/(NAME+'_adapted.json')),'--raw',str(out/(NAME+'_adapted.raw')),'--jr_path',str(out),'--rows','4','--cols','4','--cores','1']}
    suffix=dict(parse='parsed',optimize='optimized',quantize='quantized',adapt='adapted',generate='ZG')
    status_path=out/'compile_status.json'
    status=json.loads(status_path.read_text()) if status_path.exists() else dict(state='running',classes=CLASSES,completed=[])
    for stage in (stages if a.stage=='all' else [a.stage]):
        if (out/(NAME+'_'+suffix[stage]+'.json')).exists(): raise SystemExit('Preserve existing '+stage+' output')
        exe=sdk/'bin'/('icraft-'+stage+'.exe')
        command=[str(exe)]+stages[stage]
        start=time.monotonic()
        with (evidence/(stage+'.log')).open('w',encoding='utf-8') as f:
            result=subprocess.run(command,cwd=str(out),env=env,stdout=f,stderr=subprocess.STDOUT)
        status['completed'].append(dict(stage=stage,command=command,exit_code=result.returncode,
            seconds=time.monotonic()-start,tool_sha256=hashlib.sha256(exe.read_bytes()).hexdigest()))
        status['state']='failed' if result.returncode else 'running'
        status_path.write_text(json.dumps(status,indent=2)+'\n')
        if result.returncode:raise SystemExit('Icraft failed '+stage+'; see '+str(evidence))
        print('Completed',stage,flush=True)
    status['state']='complete';status_path.write_text(json.dumps(status,indent=2)+'\n')


if __name__=='__main__':main()
