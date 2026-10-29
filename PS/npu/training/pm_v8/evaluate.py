"""Evaluate the frozen target tests and all legacy test splits without fitting."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'pm_v7'),str(ROOT)]
import os
os.environ.update(YOLO_OFFLINE='true',YOLOV5_CONFIG_DIR=str(ROOT/'config/runtime'))
import hashlib
import json
import numpy as np
import torch
from torch.utils.data import DataLoader
from classification_models import build_model
from classification_runtime import CaptureDataset,class_metrics,predict
from signal_model import features,CLASSES

EXP=Path(os.environ.get('AOA_PM_EXPERIMENT',str(ROOT/'experiments/classifier_fm_pm_v8_target20k')))
DOC = Path(os.environ.get('AOA_REFERENCE_DATA', str(ROOT / 'data/reference')))


def main():
    out=EXP/'evaluation';out.mkdir(exist_ok=True)
    if (out/'float_metrics.json').exists():raise SystemExit('Preserve frozen evaluation')
    cp=EXP/'runs/branches_amp/weights/best.pt'
    checkpoint=torch.load(cp,map_location='cpu');torch.set_num_threads(4)
    model=build_model('branches_amp',num_classes=6).cuda().eval();model.load_state_dict(checkpoint['state_dict'])
    manifest=EXP/'manifest.jsonl'
    assert hashlib.sha256(manifest.read_bytes()).hexdigest()==json.loads((EXP/'dataset_checks.json').read_text())['manifest_sha256']
    rows=[json.loads(x) for x in manifest.read_text().splitlines()]
    results={};all_predictions=[]
    for split in sorted(set(r['split'] for r in rows)-{'train'}):
        subset=[r for r in rows if r['split']==split]
        loader=DataLoader(CaptureDataset(subset,'branches_amp'),batch_size=32)
        values,labels=predict(model,loader,'cuda:0',6)
        metrics=class_metrics(labels,values.argmax(1),CLASSES)
        metrics['records']=len(subset)
        if split=='val':
            select=[i for i,r in enumerate(subset) if r.get('target_hard_example')]
            metrics['target_only']=class_metrics(labels[select],values[select].argmax(1),CLASSES)
        if split=='v7_diagnostic_equivalent_pm_fm':
            metrics['excluded_from_primary_acceptance']=True
            metrics['identical_iq_different_labels']=True
            assert metrics['accuracy']<=.5
        results[split]=metrics
        all_predictions.extend(dict(name=r['name'],split=split,label=int(label),logits=v.tolist()) for r,label,v in zip(subset,labels,values))
        del loader
        print(split,metrics['accuracy'],metrics['per_class_recall'],flush=True)
    real_paths=[]
    real_paths +=[(p,'FM') for p in sorted((DOC/'fm_pm_followup_20261027').glob('batch_*/source_pha4.raw'))]
    pm=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz/real_pm_90deg_verified'
    real_paths +=[(p,'PM') for p in sorted(pm.glob('batch_*/source_pha4.raw'))]
    real_paths +=[(p,'OOK') for p in sorted((DOC/'ook_fm_followup_20261026').glob('batch_*/*source_pha4.raw'))]
    cw=DOC/'npu_small_signal/1773375228426959030_658_source_pha4.raw'
    if cw.exists():real_paths.append((cw,'CW'))
    fresh=EXP/'fresh_real_fm'
    real_paths +=[(p,'FM') for p in sorted(fresh.glob('batch_*/source_pha4.raw'))]
    real=[]
    with torch.inference_mode():
        for path,label in real_paths:
            f,gain=features(np.fromfile(path,dtype='<i4').reshape(4,4096,2))
            inputs=[torch.from_numpy(f[k].astype(np.float32)/255).unsqueeze(0).cuda() for k in ['tf','spectrum','amplitude']]
            v=model(*inputs)[0].cpu().numpy();prob=np.exp(v-v.max());prob/=prob.sum()
            real.append(dict(raw=str(path),user_label=label,prediction=CLASSES[int(v.argmax())],logits=v.tolist(),
                             probabilities=prob.tolist(),raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                             amplitude=gain,post_training_fresh=fresh in path.parents))
            print('REAL',label,real[-1]['prediction'],path,flush=True)
    baseline=json.loads((ROOT/'experiments/classifier_sixsum_ook_v6/evaluation/metrics.json').read_text())['models']['branches_amp']['splits']['test_main']
    current=results['legacy_test_main'];limits=json.loads((EXP/'dataset_plan.json').read_text())['acceptance']
    regress=[float(a-b) for a,b in zip(baseline['per_class_recall'],current['per_class_recall'][:5])]
    gates=dict(target_test_pass=all(results['test_target']['per_class_recall'][i]>=limits['target_test_recall_min'] for i in [0,5]),
               exact_test_pass=all(results['test_exact']['per_class_recall'][i]>=limits['exact_test_recall_min'] for i in [0,5]),
               legacy_total_drop=baseline['accuracy']-current['accuracy'],legacy_class_recall_drop=regress,
               legacy_regression_pass=(baseline['accuracy']-current['accuracy']<=limits['legacy_total_accuracy_drop_max'] and max(regress)<=limits['legacy_class_recall_drop_max']),
               real_diagnostic_pass=bool(real) and all(r['prediction']==r['user_label'] for r in real))
    gates['passed']=all(gates[k] for k in ['target_test_pass','exact_test_pass','legacy_regression_pass','real_diagnostic_pass'])
    report=dict(checkpoint_sha256=hashlib.sha256(cp.read_bytes()).hexdigest(),best_epoch=checkpoint['epoch'],
                results=results,real_diagnostics=real,baseline_v6_test=baseline,gates=gates,
                tests_not_used_for_checkpoint_selection=True)
    (out/'float_metrics.json').write_text(json.dumps(report,indent=2))
    (out/'predictions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in all_predictions))
    print('GATES',json.dumps(gates),flush=True)


if __name__=='__main__':main()
