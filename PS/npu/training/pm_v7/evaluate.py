"""Independent six-class tests, original v6 regression and PM/FM ambiguity."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import numpy as np
import torch
from torch.utils.data import DataLoader
from signal_model import ROOT, CLASSES, features
from classification_models import build_model
from classification_runtime import CaptureDataset, class_metrics, predict


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment',type=Path,default=ROOT/'experiments/classifier_sixsum_pm_v7')
    a=p.parse_args(); exp=a.experiment.resolve()
    out=exp/'evaluation'
    if out.exists(): raise SystemExit('Preserve previous evaluation')
    out.mkdir()
    torch.set_num_threads(6)
    os.environ.update(YOLO_OFFLINE='true',YOLOV5_CONFIG_DIR=str(ROOT/'config/runtime'))
    cp=exp/'runs/branches_amp/weights/best.pt'
    checkpoint=torch.load(cp,map_location='cpu')
    assert checkpoint['classes']==CLASSES
    model=build_model('branches_amp',num_classes=6).cuda().eval()
    model.load_state_dict(checkpoint['state_dict'])
    rows=[json.loads(x) for x in (exp/'manifest.jsonl').read_text().splitlines()]
    results={}
    all_predictions=[]
    for split in sorted(set(r['split'] for r in rows)-{'train'}):
        subset=[r for r in rows if r['split']==split]
        loader=DataLoader(CaptureDataset(subset,'branches_amp'),batch_size=16)
        logits,labels=predict(model,loader,'cuda:0',6)
        result=class_metrics(labels,logits.argmax(1),CLASSES)
        result['records']=len(subset)
        result['pm_by_frequency_band']={}
        for name,lo,hi in [('2_8khz',2000,8000),('15_25khz',15000,25000)]:
            ids=[i for i,r in enumerate(subset) if r['class_index']==5 and
                 lo<=r['station_scenes'][0]['pm_frequency_hz']<=hi]
            if ids:result['pm_by_frequency_band'][name]=dict(records=len(ids),recall=float(np.mean(logits[ids].argmax(1)==5)))
        results[split]=result
        for row,values in zip(subset,logits):
            all_predictions.append(dict(name=row['name'],split=split,label=row['class_index'],logits=values.tolist()))
        if split=='diagnostic_equivalent_pm_fm':
            groups={}
            for row,values in zip(subset,logits): groups.setdefault(row['equivalence_group'],[]).append(values)
            assert all(len(v)==2 and np.allclose(v[0],v[1],atol=1e-5,rtol=1e-5)
                       and v[0].argmax()==v[1].argmax() for v in groups.values())
            assert result['accuracy']<=.5
            result.update(identical_iq_and_logits=True,balanced_accuracy_ceiling=.5,
                          excluded_from_primary_acceptance=True)
        print(split, result['accuracy'], result['per_class_recall'], flush=True)
    old=ROOT/'experiments/classifier_sixsum_ook_v6'
    old_rows=[json.loads(x) for x in (old/'manifest.jsonl').read_text().splitlines()]
    subset=[r for r in old_rows if r['split']=='test_main']
    loader=DataLoader(CaptureDataset(subset,'branches_amp'),batch_size=16)
    values,labels=predict(model,loader,'cuda:0',6)
    results['v6_original_test_features']=class_metrics(labels,values.argmax(1),CLASSES)
    print('v6_original_test_features',results['v6_original_test_features']['accuracy'],flush=True)
    # Previously labeled captures are independent diagnostics, never train/calibration.
    captured = Path(os.environ.get('AOA_REFERENCE_DATA', str(ROOT / 'data/reference')))
    real=[(p,4) for p in sorted((captured/'ook_fm_followup_20261026').glob('batch_*/*source_pha4.raw'))]
    cw=captured/'npu_small_signal/1773375228426959030_658_source_pha4.raw'
    if cw.exists(): real.append((cw,2))
    real.extend((p,5) for p in sorted((exp/'real_pm_board').glob('batch_*/source_pha4.raw')))
    real_results=[]
    with torch.inference_mode():
        for path,label in real:
            fields,gain=features(np.fromfile(str(path),dtype='<i4').reshape(4,4096,2))
            inputs=[torch.from_numpy(fields[k].astype(np.float32)/255).unsqueeze(0).cuda() for k in ('tf','spectrum','amplitude')]
            logits=model(*inputs)[0].cpu().numpy()
            probability=np.exp(logits-logits.max());probability/=probability.sum()
            real_results.append(dict(raw=str(path),label=CLASSES[label],predicted=CLASSES[int(logits.argmax())],
                probabilities=probability.tolist(),logits=logits.tolist(),amplitude=gain,
                raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    results['previous_labeled_real_captures']=real_results
    # This is a candidate gate, not a claim of measured RF or hardware accuracy.
    main_test=results['test_main']; val=results['val']
    results['candidate_gate']=dict(pm_recall_target=.85, validation_pm_pass=val['per_class_recall'][5]>=.85,
        test_pm_pass=main_test['per_class_recall'][5]>=.85,
        original_five_class_test_accuracy=results['v6_original_test_features']['accuracy'],
        measured_rf_validated=False, npu_board_validated=False,
        general_pm_fm_discrimination_supported=False)
    result=dict(classes=CLASSES,checkpoint_sha256=hashlib.sha256(cp.read_bytes()).hexdigest(),
        results=results,simulated_only=True)
    (out/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'predictions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in all_predictions))


if __name__=='__main__':main()
