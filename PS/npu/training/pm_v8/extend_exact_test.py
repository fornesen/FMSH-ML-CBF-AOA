"""One fixed test-only extension; keep initial failures and freeze model weights."""
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
os.environ.setdefault('OMP_NUM_THREADS','1');os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
from concurrent.futures import ProcessPoolExecutor
import hashlib,json,shutil
import numpy as np
from build_dataset import worker


def main():
    exp=Path(os.environ['AOA_PM_EXPERIMENT'])
    cp=exp/'runs/branches_amp/weights/best.pt';cp_hash=hashlib.sha256(cp.read_bytes()).hexdigest()
    out=exp/'evaluation';original=out/'float_metrics.json'
    assert not (out/'exact_extension_plan.json').exists(),'Never add tests repeatedly until passing'
    plan=dict(reason='initial exact FM94/100 had1pp margin to frozen95% gate; improve sample size without fitting',
        additional_per_class=900,pooled_per_class=1000,seed_base=950000000000,
        model_checkpoint_sha256=cp_hash,weights_frozen_before_extra_scenes=True,
        initial_cases_retained=True,only_one_extension=True,acceptance_threshold_unchanged=.95)
    (out/'exact_extension_plan.json').write_text(json.dumps(plan,indent=2))
    shutil.copy2(original,out/'float_metrics.initial100.json')
    for name in ['captures','features']:(exp/name).mkdir(exist_ok=True)
    jobs=[('test_exact',kind,950_000_000_000+c*100_000+j) for c,kind in enumerate(['fm','pm']) for j in range(900)]
    rows=[]
    with ProcessPoolExecutor(max_workers=6) as pool:
        for i,r in enumerate(pool.map(worker,jobs,chunksize=3),1):
            rows.append(r)
            if i%200==0:print('Extended exact tests',i,'/',len(jobs),flush=True)
    prior=[json.loads(x) for x in (exp/'manifest.jsonl').read_text().splitlines()]
    assert {r['iq_sha256'] for r in rows}.isdisjoint(r['iq_sha256'] for r in prior)
    extra=exp/'extended_exact_manifest.jsonl';extra.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    import torch
    from torch.utils.data import DataLoader
    from classification_models import build_model
    from classification_runtime import CaptureDataset,predict,class_metrics
    from signal_model import CLASSES
    torch.set_num_threads(4)
    os.environ.update(YOLO_OFFLINE='true',YOLOV5_CONFIG_DIR=str(ROOT/'config/runtime'))
    checkpoint=torch.load(cp,map_location='cpu');model=build_model('branches_amp',num_classes=6).cuda().eval()
    model.load_state_dict(checkpoint['state_dict'])
    loader=DataLoader(CaptureDataset(rows,'branches_amp'),batch_size=32)
    logits,labels=predict(model,loader,'cuda:0',6)
    previous=[json.loads(x) for x in (out/'predictions.jsonl').read_text().splitlines()]
    initial=[r for r in previous if r['split']=='test_exact']
    combined_logits=np.concatenate([np.array([r['logits'] for r in initial]),logits])
    combined_labels=np.r_[np.array([r['label'] for r in initial]),labels]
    metrics=class_metrics(combined_labels,combined_logits.argmax(1),CLASSES)
    metrics.update(records=len(combined_labels),initial_records=len(initial),additional_records=len(rows),
                   checkpoint_sha256=cp_hash,model_unchanged=True,primary_test_manifest=extra.name)
    metrics['wilson95_intervals']={}
    for c in [0,5]:
        mask=combined_labels==c;n=int(mask.sum());correct=int(np.sum(combined_logits[mask].argmax(1)==c));p=correct/n;z=1.95996398454
        center=(p+z*z/(2*n))/(1+z*z/n);half=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
        metrics['wilson95_intervals'][CLASSES[c]]={'correct':correct,'total':n,'low':float(center-half),'high':float(center+half)}
    report=json.loads(original.read_text());report['results']['test_exact_initial100']=report['results']['test_exact']
    report['initial_gates_before_extension']=report['gates'].copy();report['results']['test_exact']=metrics
    threshold=json.loads((exp/'dataset_plan.json').read_text())['acceptance']['exact_test_recall_min']
    report['gates']['exact_test_pass']=all(metrics['per_class_recall'][i]>=threshold for i in [0,5])
    report['gates']['passed']=all(report['gates'][k] for k in ['target_test_pass','exact_test_pass','legacy_regression_pass','real_diagnostic_pass'])
    report['test_only_extension']=plan;report['test_only_extension']['manifest_sha256']=hashlib.sha256(extra.read_bytes()).hexdigest()
    original.write_text(json.dumps(report,indent=2))
    with (out/'predictions.jsonl').open('a') as stream:
        for r,label,values in zip(rows,labels,logits):stream.write(json.dumps(dict(name=r['name'],split='test_exact',label=int(label),logits=values.tolist()))+'\n')
    assert hashlib.sha256(cp.read_bytes()).hexdigest()==cp_hash
    (out/'extended_exact_metrics.json').write_text(json.dumps(metrics,indent=2))
    print('POOLED_EXACT',json.dumps(metrics),flush=True);print('GATES',json.dumps(report['gates']),flush=True)


if __name__=='__main__':main()
