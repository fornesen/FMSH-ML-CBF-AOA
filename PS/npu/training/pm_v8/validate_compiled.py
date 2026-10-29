"""Replay a fixed stratified subset plus all real diagnostics through the final graph."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'pm_v7'),str(ROOT)]
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import subprocess
import time
import numpy as np
from classification_runtime import class_metrics
from signal_model import features,CLASSES

EXP=Path(os.environ.get('AOA_PM_EXPERIMENT',str(ROOT/'experiments/classifier_fm_pm_v8_target20k')))
MODEL=EXP/'deployment/icraft_fp16/fm_sixsum_fm_pm_v8_ZG'
WORKERS=6


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk',type=Path,default=os.environ.get('ICRAFT_ROOT'),
                        required=not os.environ.get('ICRAFT_ROOT'),help='Icraft SDK directory; defaults to ICRAFT_ROOT')
    args=parser.parse_args()
    out=EXP/'evaluation/icraft_fp16'
    if out.exists():raise SystemExit('Preserve final graph replay')
    out.mkdir();inputs=out/'inputs';inputs.mkdir()
    rows=[json.loads(x) for x in (EXP/'manifest.jsonl').read_text().splitlines()]
    if (EXP/'extended_exact_manifest.jsonl').exists():
        rows += [json.loads(x) for x in (EXP/'extended_exact_manifest.jsonl').read_text().splitlines()]
    rows=[r for r in rows if r['split'] in ['val','test_target','test_exact','legacy_test_main']]
    source_counts={s:sum(r['split']==s for r in rows) for s in ['val','test_target','test_exact','legacy_test_main']}
    rng=np.random.default_rng(20261030);selected=[]
    for split,per_class in [('val',40),('test_target',50),('test_exact',25),('legacy_test_main',30)]:
        for c in range(6):
            candidates=[r for r in rows if r['split']==split and r['class_index']==c]
            if split=='val' and c in [0,5]:
                for target in [False,True]:
                    group=[r for r in candidates if bool(r.get('target_hard_example'))==target]
                    rng.shuffle(group);selected.extend(group[:per_class//2])
            else:
                rng.shuffle(candidates);selected.extend(candidates[:per_class])
    rows=selected
    (out/'fixed_selection.json').write_text(json.dumps(dict(seed=20261030,source_counts=source_counts,
        selected_names=[r['name'] for r in rows],selection='class stratified; validation FM/PM half target, half rehearsal'),indent=2))
    floating=json.loads((EXP/'evaluation/float_metrics.json').read_text())
    fp={r['name']:r for r in [json.loads(x) for x in (EXP/'evaluation/predictions.jsonl').read_text().splitlines()]}
    requests=[];labels=[]
    for i,row in enumerate(rows):
        with np.load(row['feature_path']) as z:
            paths=[]
            for k in ['tf','spectrum','amplitude']:
                p=inputs/('%04d_%s.ftmp'%(i,k))
                (z[k][:,0].transpose(1,2,0)[None].astype('<f4')/255).tofile(p)
                paths.append(str(p))
        requests.append(dict(name=row['name'],inputs=paths));labels.append(row['class_index'])
    real=floating['real_diagnostics']
    for i,row in enumerate(real):
        f,_=features(np.fromfile(row['raw'],dtype='<i4').reshape(4,4096,2));paths=[]
        for k in ['tf','spectrum','amplitude']:
            p=inputs/('real_%03d_%s.ftmp'%(i,k))
            (f[k][:,0].transpose(1,2,0)[None].astype('<f4')/255).tofile(p);paths.append(str(p))
        requests.append(dict(name='real_%03d'%i,inputs=paths))
    (out/'requests.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in requests))
    exe=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz/runtime_candidate/build/numeric/Release/validate_numeric_windows.exe'
    env=dict(os.environ);env['PATH']=str(args.sdk.resolve()/'bin')+os.pathsep+env.get('PATH','')
    env.update(OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2')
    started=time.monotonic()
    def work(i):
        part=out/('requests_part%d.jsonl'%i);part.write_text(''.join(json.dumps(r)+'\n' for r in requests[i::WORKERS]))
        target=out/('logits_part%d.jsonl'%i);cwd=out/('sdk_work%d'%i);cwd.mkdir()
        command=[str(exe),str(MODEL.with_suffix('.json')),str(MODEL.with_suffix('.raw')),str(part),str(target)]
        with (out/('console_part%d.log'%i)).open('w',encoding='utf-8') as log:
            subprocess.run(command,cwd=str(cwd),env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        result=[json.loads(x) for x in target.read_text().splitlines()]
        assert [r['name'] for r in result]==[r['name'] for r in requests[i::WORKERS]]
        return result
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:parts=list(pool.map(work,range(WORKERS)))
    by_name={r['name']:r for part in parts for r in part}
    assert len(by_name)==len(requests)
    (out/'logits.jsonl').write_text(''.join(json.dumps(by_name[r['name']])+'\n' for r in requests))
    logits=np.array([by_name[r['name']]['logits'] for r in rows]);labels=np.array(labels)
    assert logits.shape==(len(rows),6) and np.isfinite(logits).all()
    results={}
    for split in ['val','test_target','test_exact','legacy_test_main']:
        mask=np.array([r['split']==split for r in rows]);subset=[r for r in rows if r['split']==split]
        current=class_metrics(labels[mask],logits[mask].argmax(1),CLASSES)
        reference=np.array([fp[r['name']]['logits'] for r in subset])
        current.update(records=len(subset),source_records=source_counts[split],sampled=True,
                       float_prediction_agreement=float(np.mean(reference.argmax(1)==logits[mask].argmax(1))))
        current['float_reference']=class_metrics(labels[mask],reference.argmax(1),CLASSES)
        if split=='val':
            ids=[i for i,r in enumerate(subset) if r.get('target_hard_example')]
            current['target_only']=class_metrics(labels[mask][ids],logits[mask][ids].argmax(1),CLASSES)
            current['target_only_float']=class_metrics(labels[mask][ids],reference[ids].argmax(1),CLASSES)
        results[split]=current
        print('ZG',split,current['accuracy'],current['per_class_recall'],flush=True)
    real_results=[]
    for i,row in enumerate(real):
        v=np.array(by_name['real_%03d'%i]['logits']);prob=np.exp(v-v.max());prob/=prob.sum()
        real_results.append(dict(raw=row['raw'],user_label=row['user_label'],float_prediction=row['prediction'],
                                 prediction=CLASSES[int(v.argmax())],logits=v.tolist(),probabilities=prob.tolist(),
                                 post_training_fresh=row['post_training_fresh']))
    limits=json.loads((EXP/'dataset_plan.json').read_text())['acceptance']
    val=results['val'];base=floating['baseline_v6_test'];old=results['legacy_test_main']
    drop=val['float_reference']['accuracy']-val['accuracy']
    target_drop=[val['target_only_float']['per_class_recall'][i]-val['target_only']['per_class_recall'][i] for i in [0,5]]
    legacy_recall_drop=[a-b for a,b in zip(old['float_reference']['per_class_recall'][:5],old['per_class_recall'][:5])]
    target_test_drop={s:[results[s]['float_reference']['per_class_recall'][i]-results[s]['per_class_recall'][i] for i in [0,5]]
                      for s in ['test_target','test_exact']}
    gates=dict(quantization_accuracy_drop=drop,quantization_target_recall_drop=target_drop,
        quantization_pass=drop<=limits['quantized_total_accuracy_drop_max'] and max(target_drop)<=limits['quantized_target_recall_drop_max'],
        legacy_same_sample_quantization_drop=old['float_reference']['accuracy']-old['accuracy'],legacy_same_sample_class_recall_drop=legacy_recall_drop,
        legacy_regression_pass=floating['gates']['legacy_regression_pass'] and old['float_reference']['accuracy']-old['accuracy']<=.02 and max(legacy_recall_drop)<=.03,
        independent_test_same_sample_quantization_drop=target_test_drop,
        target_test_pass=floating['gates']['target_test_pass'] and max(target_test_drop['test_target'])<=.03,
        exact_test_pass=floating['gates']['exact_test_pass'] and max(target_test_drop['test_exact'])<=.03,
        real_diagnostic_pass=bool(real_results) and all(r['prediction']==r['user_label'] for r in real_results))
    gates['passed']=all(gates[k] for k in ['quantization_pass','legacy_regression_pass','target_test_pass','exact_test_pass','real_diagnostic_pass'])
    report=dict(results=results,real_diagnostics=real_results,gates=gates,backend='Icraft 3.36.1 HostBackend CPU',
        npu_hardware_executed=False,sampled_replay=True,sampling_seed=20261030,workers=WORKERS,
        model_json_sha256=hashlib.sha256(MODEL.with_suffix('.json').read_bytes()).hexdigest(),
        model_raw_sha256=hashlib.sha256(MODEL.with_suffix('.raw').read_bytes()).hexdigest(),seconds=time.monotonic()-started)
    (out/'metrics.json').write_text(json.dumps(report,indent=2))
    print('GATES',json.dumps(gates),flush=True)


if __name__=='__main__':main()
