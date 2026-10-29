"""Reuse frozen training/validation, and freeze fresh target tests for revision B."""
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
import shutil
from collections import Counter
from build_dataset import worker


def main():
    base=ROOT/'experiments/classifier_fm_pm_v8_target20k'
    out=Path(os.environ['AOA_PM_EXPERIMENT'])
    assert out!=base and not out.exists()
    out.mkdir()
    for name in ['captures','features','source_snapshot']:(out/name).mkdir()
    rows=[json.loads(x) for x in (base/'manifest.jsonl').read_text().splitlines()]
    for r in rows:
        if r['split'] in ['test_target','test_exact']:r['split']='development_v8a_'+r['split']
    jobs=[]
    for i,(split,n) in enumerate([('test_target',300),('test_exact',100)]):
        for k,kind in enumerate(['fm','pm']):
            jobs.extend((split,kind,940_000_000_000+i*1_000_000+k*100_000+j) for j in range(n))
    with ProcessPoolExecutor(max_workers=6) as pool:
        for i,row in enumerate(pool.map(worker,jobs,chunksize=3),1):
            rows.append(row)
            if i%100==0:print('Fresh B tests',i,'/',len(jobs),flush=True)
    train={r['iq_sha256'] for r in rows if r['split']=='train'}
    val={r['iq_sha256'] for r in rows if r['split']=='val'}
    tests={r['iq_sha256'] for r in rows if r['split'] not in ['train','val']}
    assert train.isdisjoint(val|tests) and val.isdisjoint(tests)
    manifest=out/'manifest.jsonl';manifest.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    for name in ['preprocess.json','dataset_plan.json']:shutil.copy2(base/name,out/name)
    plan=json.loads((out/'dataset_plan.json').read_text());plan.update(revision='B: old-five-class distillation',
        prior_attempt_gate_failed='CW recall drop 6.33pp exceeded frozen5pp limit',
        new_primary_tests_frozen_before_training=True,new_primary_test_seed_base=940000000000,
        previous_target_tests_development_only=True,legacy_tests_reused_as_fixed_regression_gate=True)
    (out/'dataset_plan.json').write_text(json.dumps(plan,indent=2))
    checks=json.loads((base/'dataset_checks.json').read_text())
    checks.update(manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),records=len(rows),
        revision='B',fresh_primary_target_tests=True,
        splits={s:dict(Counter(['FM','AM','CW','noise','OOK','PM'][r['class_index']] for r in rows if r['split']==s)) for s in sorted(set(r['split'] for r in rows))})
    (out/'dataset_checks.json').write_text(json.dumps(checks,indent=2))
    shutil.copytree(ROOT/'pm_v8',out/'source_snapshot/pm_v8',ignore=shutil.ignore_patterns('__pycache__','*.log'))
    print('Frozen',len(rows),'records')


if __name__=='__main__':main()
