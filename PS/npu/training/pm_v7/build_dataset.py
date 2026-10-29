"""Freeze independent six-class scenes before fitting; never alter v6 artifacts."""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
import argparse
from concurrent.futures import ProcessPoolExecutor
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import time
import numpy as np
from signal_model import ROOT, RF, CLASSES, CLASS_INDEX, sample_pair, capture, features, modulated_envelope


def worker(job):
    out, split, kind, seed = job
    profile = {'test_low_snr':'low_snr', 'test_high_snr':'high_snr',
               'diagnostic_equivalent_pm_fm':'target_pm', 'test_target_pm':'target_pm',
               'train_low_pm':'low_pm', 'val_low_pm':'low_pm', 'test_main_low_pm':'low_pm',
               'test_target_pm3330':'target_pm3330'}.get(split, 'mixed')
    pair = sample_pair(kind, seed, profile)
    mode = 'reset_unity' if seed % 3 else 'local_group_weights'
    raw, info = capture(pair, mode)
    fields, gain = features(raw)
    name = '%s_%s_%d' % (split, kind, seed)
    out = Path(out)
    iq = out/'captures'/(name+'.raw'); feature = out/'features'/(name+'.npz')
    raw.tofile(str(iq)); np.savez_compressed(str(feature), **fields)
    row = dict(name=name, split=split, kind=kind, class_index=CLASS_INDEX[kind], seed=seed,
               scene_id='scene_%d'%seed, simulated=True, rf_carrier_hz=RF, mode=mode,
               iq_path=iq.as_posix(), feature_path=feature.as_posix(),
               iq_sha256=hashlib.sha256(raw.tobytes()).hexdigest(), station_scenes=pair,
               center_hz=pair[0]['center_hz'], amplitude_preprocessing=gain, **info)
    return row


def verify():
    scene = sample_pair('pm', 910_000_000_001, 'target_pm')[0]
    fm = deepcopy(scene)
    fm.update(kind='fm_narrow', deviation_hz=scene['pm_deviation_rad']*scene['pm_frequency_hz'],
              message_frequencies_hz=[scene['pm_frequency_hz']], message_weights=[1.],
              message_phases=[scene['pm_message_phase']])
    t = np.arange(10000)/25e6
    np.testing.assert_allclose(modulated_envelope(t, scene), modulated_envelope(t, fm), atol=2e-14)
    a = np.exp(2j*np.pi*.02*np.arange(4032))[None].repeat(4, axis=0)*.01
    from signal_model import batch_gain
    b, meta = batch_gain(a)
    np.testing.assert_allclose(b, a*25, atol=1e-14)
    np.testing.assert_array_equal(batch_gain(np.zeros_like(a))[0], np.zeros_like(a))
    return dict(equivalent_pm_fm_waveform_verified=True, expected_balanced_accuracy_ceiling=.5,
                common_batch_gain_verified=True, zero_input_finite=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=ROOT/'experiments/classifier_sixsum_pm_v7')
    p.add_argument('--workers', type=int, default=6)
    p.add_argument('--smoke', action='store_true')
    a = p.parse_args(); out = a.output.resolve()
    if out.exists(): raise SystemExit('Preserve existing experiment: '+str(out))
    checks = verify()
    out.mkdir(parents=True)
    for d in ('captures', 'features', 'source_snapshot'): (out/d).mkdir()
    counts = dict(train=1000, val=180, test_main=250, test_low_snr=50, test_high_snr=50)
    if a.smoke: counts = {k:2 for k in counts}
    jobs = []
    for si, (split, n) in enumerate(counts.items()):
        for c in range(6):
            for j in range(n):
                kind = ('fm_wide' if j%2==0 else 'fm_narrow') if c == 0 else ['am','cw','noise','ook','pm'][c-1]
                jobs.append((str(out), split, kind, 910_000_000_000+si*1_000_000+c*100_000+j))
    for split, n, base in [('test_target_pm', 2 if a.smoke else 120, 916_000_000_000),
                           ('diagnostic_equivalent_pm_fm', 2 if a.smoke else 40, 917_000_000_000)]:
        jobs.extend((str(out), split, 'pm', base+j) for j in range(n))
    plan = dict(classes=CLASSES, counts_per_class=counts, rf_carrier_hz=RF,
        pm_waveform='sine', pm_frequency_hz=[15000,25000], pm_deviation_degrees=[60,120],
        user_target=dict(carrier_hz=445000000, message_hz=20000, phase_deviation_degrees=90),
        residual_center_hz=[-800000,800000], adc_bits=12, adc_peak_codes=[5,1200],
        snr_reference_db=[-5,65], amplitude_preprocessing='deployed batch v1: p95 64-sample RMS, target .25, max256, coherence>=.5, peak<=1.5',
        pm_fm_labels_are_parameter_prior_conditional=True, general_blind_pm_fm_discrimination_possible=False,
        all_tests_frozen_before_training=True, seed_base=910000000000, simulated_only=True)
    (out/'dataset_plan.json').write_text(json.dumps(plan, indent=2)+'\n', encoding='utf-8')
    started = time.monotonic(); rows=[]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for row in pool.map(worker, jobs, chunksize=3):
            rows.append(row)
            if len(rows)%100==0 or len(rows)==len(jobs):
                status=dict(state='building', records=len(rows), total=len(jobs), seconds=time.monotonic()-started)
                (out/'build_status.json').write_text(json.dumps(status))
                print('Built', len(rows), '/', len(jobs), 'seconds', round(status['seconds'],1), flush=True)
    assert len(set(r['seed'] for r in rows))==len(rows)
    assert len(set(r['iq_sha256'] for r in rows))==len(rows)
    # Exact input duplicates with different transmitter labels are confined to
    # the ambiguity diagnostic, excluded from train, validation and accuracy gate.
    twins=[]
    for row in rows:
        if row['split']=='diagnostic_equivalent_pm_fm':
            twin=dict(row, name=row['name']+'_fm_equivalent', kind='fm_equivalent', class_index=0,
                      equivalence_group=row['scene_id'])
            row['equivalence_group']=row['scene_id']; twins.append(twin)
    rows += twins
    from classification_features import contract
    meta=contract()
    meta.update(version=8, classes=CLASSES, classification_logits=6, source_rf_carrier_hz=[RF,RF],
        target_residual_center_hz=[-800000,800000], source_block_samples=4096, discard_initial_iq_samples=64,
        raw_storage_shape=[4,4096,2], source='six-channel ADC12 numerical DDC/FIR/Q23 sum at445MHz',
        amplitude_preprocessing=plan['amplitude_preprocessing'], input_gain=1.,
        pm_profile=plan['user_target'], pm_frequency_range_hz=[15000,25000], pm_deviation_range_degrees=[60,120],
        pm_fm_parameter_prior_conditional=True, measured_RF_accuracy_validated=False)
    (out/'preprocess.json').write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')
    (out/'manifest.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    files=['train_classification.py','classification_models.py','classification_runtime.py','classification_features.py',
           'fusion_features.py','array_signal_model.py','triplet_signal_model.py','sixsum_ook_signal.py','sixsum_validation.py','export_npu_static.py']
    hashes={}
    for f in files:
        payload=(ROOT/f).read_bytes(); (out/'source_snapshot'/f).write_bytes(payload)
        hashes[f]=hashlib.sha256(payload).hexdigest()
    shutil.copytree(Path(__file__).parent,out/'source_snapshot/pm_v7',ignore=shutil.ignore_patterns('__pycache__'))
    result=dict(passed=True, checks=checks, source_hashes=hashes,
        fresh_tests_frozen_before_training=True, unique_primary_scene_hashes=True,
        manifest_sha256=hashlib.sha256((out/'manifest.jsonl').read_bytes()).hexdigest(),
        splits={s:dict(Counter(CLASSES[r['class_index']] for r in rows if r['split']==s)) for s in sorted(set(r['split'] for r in rows))},
        records=len(rows), seconds=time.monotonic()-started)
    (out/'dataset_checks.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'build_status.json').write_text(json.dumps(dict(state='complete',records=len(rows))))
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__': main()
