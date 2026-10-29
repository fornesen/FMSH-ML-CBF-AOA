"""Freeze a conditional FM/PM repair experiment without changing older datasets."""
import os
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'pm_v7'),str(ROOT)]
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import shutil
import time
import numpy as np
from signal_model import sample_pair,capture,features,CLASSES

OUT=Path(os.environ.get('AOA_PM_EXPERIMENT',str(ROOT/'experiments/classifier_fm_pm_v8_target20k')))
V7=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz'
V6=ROOT/'experiments/classifier_sixsum_ook_v6'


def ambiguous_fm(row):
    s=row['station_scenes'][0]
    f=s.get('message_frequencies_hz',[])
    return row['class_index']==0 and len(f)==1 and (2000<=f[0]<=8000 or 15000<=f[0]<=25000) and 75<=np.rad2deg(s['deviation_hz']/f[0])<=105


def worker(job):
    split,kind,seed=job
    rng=np.random.default_rng(seed+73_000_000_000)
    pair=sample_pair('pm' if kind=='pm' else 'fm_narrow',seed)
    exact=split=='test_exact'
    message=20000. if exact else float(rng.uniform(18000,22000))
    deviation=20000. if exact else float(rng.uniform(18000,22000))
    degrees=90. if exact else float(rng.uniform(80,100))
    message_phase=float(rng.uniform(-np.pi,np.pi))
    for s in pair:
        if kind=='fm':
            s.update(kind='fm_narrow',deviation_hz=deviation,message_frequencies_hz=[message],
                     message_weights=[1.],message_phases=[message_phase],occupied_nominal_bandwidth_hz=2*(message+deviation))
        else:
            s.update(kind='pm',pm_frequency_hz=message,pm_deviation_degrees=degrees,
                     pm_deviation_rad=float(np.deg2rad(degrees)),pm_message_phase=message_phase,
                     occupied_nominal_bandwidth_hz=2*(np.deg2rad(degrees)+1)*message)
    # Same RF, SNR, level, channel and beam distributions for both labels.
    mode='reset_unity' if seed%3 else 'local_group_weights'
    raw,info=capture(pair,mode);field,gain=features(raw)
    name='%s_target_%s_%d'%(split,kind,seed)
    iq=OUT/'captures'/(name+'.raw');fp=OUT/'features'/(name+'.npz')
    raw.tofile(iq);np.savez_compressed(fp,**field)
    return dict(name=name,split=split,kind='fm_narrow' if kind=='fm' else 'pm',class_index=0 if kind=='fm' else 5,
                seed=seed,scene_id='v8_%d'%seed,simulated=True,rf_carrier_hz=445000000,
                mode=mode,iq_path=iq.as_posix(),feature_path=fp.as_posix(),
                iq_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),station_scenes=pair,
                center_hz=pair[0]['center_hz'],amplitude_preprocessing=gain,target_hard_example=True,**info)


def main():
    if '--finalize-existing' in sys.argv:
        assert (OUT/'manifest.jsonl').exists() and not (OUT/'dataset_checks.json').exists()
        rows=[json.loads(x) for x in (OUT/'manifest.jsonl').read_text().splitlines()]
        plan=json.loads((OUT/'dataset_plan.json').read_text())
        old=[json.loads(x) for x in (V6/'manifest.jsonl').read_text().splitlines()]
        finish(rows,plan,[r['name'] for r in old if r['split'] in ['train','val'] and ambiguous_fm(r)],time.monotonic(),True)
        return
    if OUT.exists():raise SystemExit('Preserve existing experiment: '+str(OUT))
    OUT.mkdir()
    for name in ['captures','features','source_snapshot']:(OUT/name).mkdir()
    old=[json.loads(x) for x in (V6/'manifest.jsonl').read_text().splitlines()]
    prior=[json.loads(x) for x in (V7/'manifest.jsonl').read_text().splitlines()]
    plan=dict(classes=CLASSES,rf_carrier_hz=445000000,
        user_confirmed_fm=dict(waveform='sine',message_hz=20000,peak_deviation_hz=20000),
        user_confirmed_pm=dict(waveform='sine',message_hz=20000,peak_phase_deviation_degrees=90),
        target_fm_training=dict(message_hz=[18000,22000],peak_deviation_hz=[18000,22000]),
        target_pm_training=dict(message_hz=[18000,22000],peak_phase_deviation_degrees=[80,100]),
        retained_pm_domain=dict(frequency_bands_hz=[[2000,8000],[15000,25000]],phase_degrees=[75,105]),
        conditional_parameter_prior=True,general_blind_pm_fm_discrimination=False,
        train_per_target=1000,val_per_target=200,test_target_per_class=300,test_exact_per_class=100,
        real_captures_used_for_training=False,real_captures_used_for_checkpoint_selection=False,
        acceptance=dict(target_test_recall_min=.90,exact_test_recall_min=.95,
            legacy_total_accuracy_drop_max=.03,legacy_class_recall_drop_max=.05,
            quantized_total_accuracy_drop_max=.02,quantized_target_recall_drop_max=.03),
        source_manifest_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [V6/'manifest.jsonl',V7/'manifest.jsonl']})
    (OUT/'dataset_plan.json').write_text(json.dumps(plan,indent=2))
    rows=[];excluded=[]
    for r in old:
        if r['split'] in ['train','val']:
            if ambiguous_fm(r):excluded.append(r['name']);continue
            rows.append(dict(r,source_dataset='v6',selection_group='legacy'))
        else:
            rows.append(dict(r,split='legacy_'+r['split'],source_dataset='v6',selection_group='independent_legacy'))
    hashes={r['iq_sha256'] for r in rows}
    rng=np.random.default_rng(20261028)
    for split,n in [('train',300),('val',60)]:
        for c in range(5):
            candidates=[r for r in prior if r['split']==split and r['class_index']==c and r['iq_sha256'] not in hashes and not ambiguous_fm(r)]
            rng.shuffle(candidates)
            for r in candidates[:n]:
                rows.append(dict(r,source_dataset='v7',selection_group='new_chain_rehearsal'));hashes.add(r['iq_sha256'])
    for r in prior:
        if r['class_index']==5 and r['split'] in ['train','val']:
            if 75<=r['station_scenes'][0]['pm_deviation_degrees']<=105:
                if r['iq_sha256'] not in hashes:
                    rows.append(dict(r,source_dataset='v7',selection_group='retained_pm'));hashes.add(r['iq_sha256'])
        elif r['split'] not in ['train','val']:
            # Preserve the broad v7 test results as diagnostics, including out-of-contract PM.
            rows.append(dict(r,split='v7_'+r['split'],source_dataset='v7',selection_group='independent_prior'))
    jobs=[]
    for i,(split,n) in enumerate([('train',1000),('val',200),('test_target',300),('test_exact',100)]):
        for k,kind in enumerate(['fm','pm']):
            jobs.extend((split,kind,930_000_000_000+i*1_000_000+k*100_000+j) for j in range(n))
    start=time.monotonic()
    with ProcessPoolExecutor(max_workers=6) as pool:
        for i,r in enumerate(pool.map(worker,jobs,chunksize=3),1):
            rows.append(r)
            if i%100==0:print('Built',i,'/',len(jobs),'seconds',round(time.monotonic()-start,1),flush=True)
    training={r['iq_sha256'] for r in rows if r['split']=='train'}
    validation={r['iq_sha256'] for r in rows if r['split']=='val'}
    tests={r['iq_sha256'] for r in rows if r['split'] not in ['train','val']}
    assert training.isdisjoint(validation|tests) and validation.isdisjoint(tests)
    manifest=OUT/'manifest.jsonl';manifest.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    finish(rows,plan,excluded,start)


def finish(rows,plan,excluded,start,resumed=False):
    manifest=OUT/'manifest.jsonl'
    training={r['iq_sha256'] for r in rows if r['split']=='train'}
    validation={r['iq_sha256'] for r in rows if r['split']=='val'}
    tests={r['iq_sha256'] for r in rows if r['split'] not in ['train','val']}
    assert training.isdisjoint(validation|tests) and validation.isdisjoint(tests)
    meta=json.loads((V7/'preprocess.json').read_text());meta.update(version=9,
        conditional_fm_pm_domain=dict(plan['target_fm_training'],pm=plan['retained_pm_domain']),
        pm_deviation_range_degrees=[75,105],
        target_specific=True)
    (OUT/'preprocess.json').write_text(json.dumps(meta,indent=2))
    checks=dict(passed=True,fresh_tests_frozen_before_training=True,real_iq_excluded=True,
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),train_val_test_hashes_disjoint=True,
        excluded_ambiguous_fm_train_val=excluded,records=len(rows),
        splits={s:dict(Counter(CLASSES[r['class_index']] for r in rows if r['split']==s)) for s in sorted(set(r['split'] for r in rows))},
        seconds=time.monotonic()-start,metadata_finalization_resumed=resumed)
    (OUT/'dataset_checks.json').write_text(json.dumps(checks,indent=2))
    for name in ['train_classification.py','classification_models.py','classification_runtime.py','export_npu_static.py']:
        shutil.copy2(ROOT/name,OUT/'source_snapshot'/name)
    shutil.copytree(ROOT/'pm_v8',OUT/'source_snapshot/pm_v8',ignore=shutil.ignore_patterns('__pycache__','*.log'))
    print(json.dumps(checks,indent=2),flush=True)


if __name__=='__main__':main()
