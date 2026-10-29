"""Final graph replay of labeled real captures, separate from synthetic accuracy."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import hashlib
import numpy as np
from signal_model import ROOT,CLASSES,features


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dtype',default='int8')
    p.add_argument('--sdk',type=Path,default=os.environ.get('ICRAFT_ROOT'),
                   required=not os.environ.get('ICRAFT_ROOT'),help='Icraft SDK directory; defaults to ICRAFT_ROOT')
    p.add_argument('--folder',type=Path)
    a=p.parse_args()
    exp=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz'
    source=json.loads((exp/'evaluation/metrics.json').read_text())['results']['previous_labeled_real_captures']
    if a.folder:
        source=[dict(raw=r['raw'],label='PM',predicted=r['float_prediction'],
            raw_sha256=hashlib.sha256(Path(r['raw']).read_bytes()).hexdigest())
            for r in json.loads((a.folder/'float_and_phase_check.json').read_text())]
    out=exp/'evaluation'/('real_icraft_'+a.dtype+('_'+a.folder.name if a.folder else ''))
    if out.exists():raise SystemExit('Preserve real replay')
    out.mkdir();requests=[]
    for i,row in enumerate(source):
        fields,gain=features(np.fromfile(row['raw'],dtype='<i4').reshape(4,4096,2))
        paths=[]
        for key in ('tf','spectrum','amplitude'):
            path=out/('%02d_%s.ftmp'%(i,key))
            (fields[key][:,0].transpose(1,2,0)[None].astype('<f4')/255).tofile(str(path))
            paths.append(str(path))
        requests.append(dict(name='real_%d'%i,inputs=paths))
    request=out/'requests.jsonl';request.write_text(''.join(json.dumps(x)+'\n' for x in requests))
    model=exp/'deployment'/('icraft_'+a.dtype)/'fm_sixsum_pm_v7_ZG'
    command=[str(exp/'runtime_candidate/build/numeric/Release/validate_numeric_windows.exe'),
             str(model.with_suffix('.json')),str(model.with_suffix('.raw')),str(request),str(out/'logits.jsonl')]
    env=dict(os.environ);env['PATH']=str(a.sdk.resolve()/'bin')+os.pathsep+env.get('PATH','')
    with (out/'console.log').open('w',encoding='utf-8') as f:
        subprocess.run(command,env=env,cwd=str(out),stdout=f,stderr=subprocess.STDOUT,check=True)
    logits=[json.loads(x) for x in (out/'logits.jsonl').read_text().splitlines()]
    assert len(logits)==len(source)
    result=[]
    for row,values in zip(source,logits):
        x=np.array(values['logits']);p=np.exp(x-x.max());p/=p.sum()
        result.append(dict(raw=row['raw'],raw_sha256=row['raw_sha256'],user_label=row['label'],
            float_prediction=row['predicted'],zg_prediction=CLASSES[int(x.argmax())],zg_logits=x.tolist(),
            zg_probabilities=p.tolist(),hardware_executed=False))
    (out/'metrics.json').write_text(json.dumps(dict(real_diagnostics=result,backend='Icraft HostBackend CPU',
        formal_measured_rf_accuracy_claimed=False,pm_source_parameters_pending_confirmation=not bool(a.folder)),indent=2)+'\n')
    for r in result:print(r['user_label'],r['float_prediction'],r['zg_prediction'],r['raw'])


if __name__=='__main__':main()
