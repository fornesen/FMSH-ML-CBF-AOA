"""Run the final ZG graph using actual Icraft HostBackend, not NPU hardware."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import numpy as np
from signal_model import ROOT, CLASSES
from classification_runtime import class_metrics


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk',type=Path,default=os.environ.get('ICRAFT_ROOT'),
                   required=not os.environ.get('ICRAFT_ROOT'),help='Icraft SDK directory; defaults to ICRAFT_ROOT')
    p.add_argument('--experiment',type=Path,default=ROOT/'experiments/classifier_sixsum_pm_v7')
    p.add_argument('--splits',default='val')
    p.add_argument('--dtype',default='int8')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--tag',default='parallel')
    p.add_argument('--test-per-class',type=int,default=0,help='Deterministic independent-test sample; validation is always complete')
    a=p.parse_args();exp=a.experiment.resolve()
    assert 1<=a.workers<=4
    out=exp/'evaluation'/('icraft_'+a.dtype+'_'+a.splits.replace(',','_')+('_'+a.tag if a.tag else ''))
    if out.exists():raise SystemExit('Preserve existing Icraft evaluation')
    out.mkdir()
    rows=[json.loads(x) for x in (exp/'manifest.jsonl').read_text().splitlines()]
    rows=[r for r in rows if r['split'] in a.splits.split(',')]
    assert rows
    source_counts=dict(Counter(r['split'] for r in rows))
    if a.test_per_class:
        selected=set();rng=np.random.default_rng(20261027)
        for split in a.splits.split(','):
            if split in ('val','diagnostic_equivalent_pm_fm'):
                selected.update(r['name'] for r in rows if r['split']==split);continue
            for c in range(6):
                candidates=[r for r in rows if r['split']==split and r['class_index']==c]
                if c==5 and split=='test_main':
                    bands=[[r for r in candidates if r['station_scenes'][0]['pm_frequency_hz']<10000],
                           [r for r in candidates if r['station_scenes'][0]['pm_frequency_hz']>=10000]]
                    for band in bands:
                        rng.shuffle(band);selected.update(r['name'] for r in band[:a.test_per_class//2])
                else:
                    rng.shuffle(candidates);selected.update(r['name'] for r in candidates[:a.test_per_class])
        rows=[r for r in rows if r['name'] in selected]
    inputs_dir=out/'inputs';inputs_dir.mkdir()
    requests=[]
    for i,row in enumerate(rows):
        paths=[]
        with np.load(row['feature_path']) as fields:
            for key in ('tf','spectrum','amplitude'):
                target=inputs_dir/('%04d_%s.ftmp'%(i,key))
                (fields[key][:,0].transpose(1,2,0)[None].astype('<f4')/255).tofile(str(target))
                paths.append(str(target))
        requests.append(dict(name=row['name'],inputs=paths))
    request_path=out/'requests.jsonl'
    request_path.write_text(''.join(json.dumps(r)+'\n' for r in requests))
    model=exp/'deployment'/('icraft_'+a.dtype)/'fm_sixsum_pm_v7_ZG'
    exe=exp/'runtime_candidate/build/numeric/Release/validate_numeric_windows.exe'
    output=out/'logits.jsonl'
    env=dict(os.environ);env['PATH']=str(a.sdk.resolve()/'bin')+os.pathsep+env.get('PATH','')
    env.update(OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4')
    start=time.monotonic()
    def run_part(index):
        part=out/('requests_part%d.jsonl'%index)
        selected=requests[index::a.workers]
        part.write_text(''.join(json.dumps(r)+'\n' for r in selected))
        result_path=out/('logits_part%d.jsonl'%index)
        command=[str(exe),str(model.with_suffix('.json')),str(model.with_suffix('.raw')),str(part),str(result_path)]
        work=out/('sdk_work_part%d'%index);work.mkdir()
        with (out/('console_part%d.log'%index)).open('w',encoding='utf-8') as f:
            result=subprocess.run(command,cwd=str(work),env=env,stdout=f,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError('Icraft replay failed: part%d'%index)
        values=[json.loads(x) for x in result_path.read_text().splitlines()]
        assert [r['name'] for r in values]==[r['name'] for r in selected]
        return command,values
    with ThreadPoolExecutor(max_workers=a.workers) as pool:parts=list(pool.map(run_part,range(a.workers)))
    by_name={r['name']:r for _,part in parts for r in part}
    logits=[by_name[r['name']] for r in rows]
    output.write_text(''.join(json.dumps(r)+'\n' for r in logits))
    assert len(logits)==len(rows)
    assert [r['name'] for r in logits]==[r['name'] for r in rows]
    values=np.array([r['logits'] for r in logits]); assert values.shape==(len(rows),6) and np.isfinite(values).all()
    fp={r['name']:r for r in [json.loads(x) for x in (exp/'evaluation/predictions.jsonl').read_text().splitlines()]}
    report={}
    for split in a.splits.split(','):
        select=np.array([r['split']==split for r in rows])
        labels=np.array([r['class_index'] for r in rows])[select]
        metrics=class_metrics(labels,values[select].argmax(1),CLASSES)
        metrics.update(records=int(select.sum()),source_split_records=source_counts[split],
            test_sample_per_class=a.test_per_class if split!='val' else 0,
            selection='seed20261027 per class, PM primary test balanced across both frequency bands' if a.test_per_class else 'complete split')
        floating=np.array([fp[r['name']]['logits'] for r in rows if r['split']==split])
        metrics['float_prediction_agreement']=float(np.mean(floating.argmax(1)==values[select].argmax(1)))
        metrics['float_reference']=class_metrics(labels,floating.argmax(1),CLASSES)
        subset=[r for r in rows if r['split']==split]
        metrics['pm_by_frequency_band']={}
        for band,lo,hi in [('2_8khz',2000,8000),('15_25khz',15000,25000)]:
            ids=[i for i,r in enumerate(subset) if r['class_index']==5 and lo<=r['station_scenes'][0]['pm_frequency_hz']<=hi]
            if ids:metrics['pm_by_frequency_band'][band]=dict(records=len(ids),
                recall=float(np.mean(values[select][ids].argmax(1)==5)),
                float_recall=float(np.mean(floating[ids].argmax(1)==5)))
        if split=='val':
            ref=metrics['float_reference']
            metrics['quantization_gate']=dict(accuracy_drop_limit=.02, pm_recall_drop_limit=.03,
                accuracy_drop=ref['accuracy']-metrics['accuracy'],pm_recall_drop=ref['per_class_recall'][5]-metrics['per_class_recall'][5])
            metrics['quantization_gate']['passed']=(metrics['quantization_gate']['accuracy_drop']<=.02 and metrics['quantization_gate']['pm_recall_drop']<=.03)
            metrics['quantization_gate']['pm_band_recall_drop_limit']=.03
            metrics['quantization_gate']['passed'] &= all(v['float_recall']-v['recall']<=.03 for v in metrics['pm_by_frequency_band'].values())
        report[split]=metrics
        print(split,metrics['accuracy'],metrics['per_class_recall'],flush=True)
    (out/'metrics.json').write_text(json.dumps(dict(results=report,backend='Icraft3.36.1 HostBackend CPU',
        npu_hardware_executed=False,seconds=time.monotonic()-start,commands=[x[0] for x in parts],workers=a.workers),indent=2)+'\n')


if __name__=='__main__':main()
