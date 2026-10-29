"""Preserve the first stage and freeze 3.33 kHz PM coverage in a new experiment."""
import os
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
from concurrent.futures import ProcessPoolExecutor
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import time
from build_dataset import worker
from signal_model import ROOT,CLASSES


def main():
    base=ROOT/'experiments/classifier_sixsum_pm_v7'
    out=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz'
    if out.exists():raise SystemExit('Preserve existing extension')
    out.mkdir()
    for d in ('captures','features','source_snapshot'):(out/d).mkdir()
    cp=base/'runs/branches_amp/weights/best.pt'
    shutil.copy2(cp,out/'initial_sixclass.pt')
    rows=[json.loads(x) for x in (base/'manifest.jsonl').read_text().splitlines()]
    jobs=[]
    counts=dict(train_low_pm=1000,val_low_pm=180,test_main_low_pm=250,test_target_pm3330=120)
    for si,(split,count) in enumerate(counts.items()):
        jobs.extend((str(out),split,'pm',920_000_000_000+si*1_000_000+j) for j in range(count))
    start=time.monotonic();extra=[]
    with ProcessPoolExecutor(max_workers=6) as pool:
        for row in pool.map(worker,jobs,chunksize=3):
            row['split']={'train_low_pm':'train','val_low_pm':'val','test_main_low_pm':'test_main'}.get(row['split'],row['split'])
            row['pm_band']='2_8khz' if row['split']!='test_target_pm3330' else '3333hz_exact'
            extra.append(row)
            if len(extra)%100==0:print('Extended',len(extra),'/',len(jobs),flush=True)
    assert set(r['iq_sha256'] for r in rows).isdisjoint(r['iq_sha256'] for r in extra)
    rows+=extra
    (out/'manifest.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    digest=hashlib.sha256((out/'manifest.jsonl').read_bytes()).hexdigest()
    plan=json.loads((base/'dataset_plan.json').read_text())
    plan.update(pm_frequency_bands_hz=[[2000,8000],[15000,25000]],
        user_confirmed_live_target=dict(carrier_hz=445000000,message_hz=10000./3,phase_deviation_degrees=90),
        first_stage_checkpoint_sha256=hashlib.sha256((out/'initial_sixclass.pt').read_bytes()).hexdigest(),
        first_stage_manifest_sha256=hashlib.sha256((base/'manifest.jsonl').read_bytes()).hexdigest(),
        extension_frozen_before_training=True)
    (out/'dataset_plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    meta=json.loads((base/'preprocess.json').read_text())
    meta.update(pm_frequency_bands_hz=plan['pm_frequency_bands_hz'],pm_frequency_range_hz=[2000,25000],
        pm_profile=plan['user_confirmed_live_target'])
    (out/'preprocess.json').write_text(json.dumps(meta,indent=2)+'\n')
    checks=dict(passed=True,fresh_tests_frozen_before_training=True,manifest_sha256=digest,
        initial_checkpoint_sha256=plan['first_stage_checkpoint_sha256'],
        base_scene_splits_preserved=True,additional_pm_scenes=len(extra),records=len(rows),
        splits={s:dict(Counter(CLASSES[r['class_index']] for r in rows if r['split']==s)) for s in sorted(set(r['split'] for r in rows))},
        seconds=time.monotonic()-start)
    (out/'dataset_checks.json').write_text(json.dumps(checks,indent=2)+'\n')
    shutil.copytree(ROOT/'pm_v7',out/'source_snapshot/pm_v7',ignore=shutil.ignore_patterns('__pycache__','*.log','*.json'))
    for path in (base/'source_snapshot').glob('*.py'):shutil.copy2(path,out/'source_snapshot'/path.name)
    print(json.dumps(checks,indent=2),flush=True)


if __name__=='__main__':main()
