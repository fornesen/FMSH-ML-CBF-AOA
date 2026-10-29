"""Freeze v6 training/validation rehearsal before fitting the new class."""
import hashlib
import json
from pathlib import Path
import numpy as np
from signal_model import ROOT, CLASSES


def main():
    exp=ROOT/'experiments/classifier_sixsum_pm_v7'
    assert not (exp/'pipeline_status.json').exists(),'Cannot change data after training starts'
    path=exp/'manifest.jsonl'
    rows=[json.loads(x) for x in path.read_text().splitlines()]
    assert not any(r.get('rehearsal_v6') for r in rows),'Already appended'
    old=ROOT/'experiments/classifier_sixsum_ook_v6/manifest.jsonl'
    source=[json.loads(x) for x in old.read_text().splitlines()]
    rng=np.random.default_rng(20261027)
    selected=[]
    for split,count in [('train',200),('val',40)]:
        for c in range(5):
            candidates=[r for r in source if r['split']==split and r['class_index']==c]
            rng.shuffle(candidates)
            for r in candidates[:count]: selected.append(dict(r,rehearsal_v6=True))
    assert set(r['iq_sha256'] for r in rows).isdisjoint(r['iq_sha256'] for r in selected)
    # No v6 test samples can enter rehearsal or quantization calibration.
    assert all(r['split'] in ('train','val') for r in selected)
    rows+=selected
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    checks_path=exp/'dataset_checks.json';checks=json.loads(checks_path.read_text())
    checks.update(manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  records=len(rows),v6_rehearsal_train=1000,v6_rehearsal_val=200,
                  v6_test_excluded_from_rehearsal=True)
    from collections import Counter
    checks['splits']={s:dict(Counter(CLASSES[r['class_index']] for r in rows if r['split']==s)) for s in sorted(set(r['split'] for r in rows))}
    checks_path.write_text(json.dumps(checks,indent=2)+'\n')
    plan_path=exp/'dataset_plan.json';plan=json.loads(plan_path.read_text())
    plan['v6_rehearsal']=dict(training=1000,validation=200,original_test_excluded=True,
        original_feature_scale_preserved=True,source_manifest_sha256=hashlib.sha256(old.read_bytes()).hexdigest())
    plan_path.write_text(json.dumps(plan,indent=2)+'\n')
    print('Appended train1000/val200 v6 rehearsal; final records',len(rows))


if __name__=='__main__':main()
