"""Frozen v5 validation with six-channel pha sums; no model training.

Reconstruct the six ADC/DDC streams from previous held-out paired scenes.
Rotate with signed24 Q23 coefficients, sum in wide integer precision, then
round ONCE as beamformer_sum.v does. Do not add separately rounded pha files.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import json
import os
from pathlib import Path
import time

os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
import cv2
import numpy as np

from array_signal_model import ADC_FS, RF, C, SCALE, FILTER_ENERGY, digital_chain
from triplet_signal_model import receive, beam_weights
from classification_features import extract

MODES = ('reset_unity', 'local_group_weights', 'cross_group_aligned')
GAINS = (1., .5)
EXPECTED_MODEL_SHA = 'd77d4773811f9ed5e713fe0a35938cdc23ddb49826c945932bf2f0abb1a4e846'


def q23_codes(weights):
    return np.clip(np.rint(weights.real*2**23), -2**23, 2**23-1).astype(np.int64), np.clip(
        np.rint(weights.imag*2**23), -2**23, 2**23-1).astype(np.int64)


def scale_rtl(value):
    """Signed54 -> nearest-even /2^23 -> signed31 -> signed32 container."""
    value = np.asarray(value, dtype=np.int64)
    quotient = value >> 23
    rem = value & ((1 << 23)-1)
    quotient += ((rem > (1 << 22)) | ((rem == (1 << 22)) & ((quotient & 1) != 0)))
    return (((quotient+(1 << 30)) & ((1 << 31)-1))-(1 << 30)).astype('<i4')


def combine(streams, cos_codes, sin_codes):
    i = streams.real.astype(np.int64); q = streams.imag.astype(np.int64)
    # Full products fit the RTL multiplier's signed50 field for signed26 IQ
    # and signed24 coefficients. Signed51 channel and signed54 sums also fit.
    real = cos_codes @ i - sin_codes @ q
    imag = sin_codes @ i + cos_codes @ q
    return np.stack((scale_rtl(real), scale_rtl(imag)), axis=-1)


def coefficient_modes(pair):
    local = np.concatenate([beam_weights(s) for s in pair])
    aligned = local.copy()
    delta_range = pair[1]['source_range_m']-pair[0]['source_range_m']
    phase = 2*np.pi*(RF+pair[0]['center_hz'])*delta_range/C
    aligned[3:] *= np.exp(1j*phase)
    complex_weights = np.stack((np.ones(6, dtype=complex), local, aligned))
    return q23_codes(complex_weights), phase


def verify_arithmetic():
    # Independent scalar implementation of the exact tie and sign rules.
    rng = np.random.default_rng(20261015)
    values = rng.integers(-(1 << 52), 1 << 52, size=2048, dtype=np.int64)
    edges = np.array([k*(1 << 23)+offset for k in range(-4, 5)
                      for offset in ((1 << 22)-1, 1 << 22, (1 << 22)+1)], dtype=np.int64)
    values = np.r_[values, edges]
    actual = scale_rtl(values)
    for v, out in zip(values, actual):
        base, rem = divmod(int(v), 1 << 23)
        if rem > (1 << 22) or (rem == (1 << 22) and base % 2):
            base += 1
        expected = (base+(1 << 30)) % (1 << 31)-(1 << 30)
        assert int(out) == expected
    streams = rng.integers(-(1 << 25), 1 << 25, size=(6, 50)) + 1j*rng.integers(-(1 << 25), 1 << 25, size=(6, 50))
    weights = np.exp(1j*rng.uniform(-np.pi, np.pi, 6))
    cos, sin = q23_codes(weights)
    result = combine(streams, cos[None], sin[None])[0]
    for t in range(50):
        real = sum(int(cos[ch])*int(streams[ch, t].real)-int(sin[ch])*int(streams[ch, t].imag) for ch in range(6))
        imag = sum(int(sin[ch])*int(streams[ch, t].real)+int(cos[ch])*int(streams[ch, t].imag) for ch in range(6))
        np.testing.assert_array_equal(result[t], scale_rtl([real, imag]))
    return dict(signed_Q23_and_six_channel_sum=True, nearest_even_negative_and_tie_cases=True,
                signed31_output_wrap=True, scalar_checked_samples=len(values)+100)


def worker(job):
    output, pair = job
    cv2.setNumThreads(1)
    assert pair[0]['station_id'] == 0 and pair[1]['station_id'] == 1
    for key in ('kind', 'center_hz', 'block_source_start_samples', 'source_position_m', 'class_index', 'pair_id'):
        assert pair[0][key] == pair[1][key]
    (cos, sin), phase = coefficient_modes(pair)
    # Extra rows reconstruct the original two independent group sums exactly.
    control_cos = np.zeros((2, 6), dtype=np.int64); control_sin = control_cos.copy()
    for g in range(2):
        a, b = q23_codes(beam_weights(pair[g]))
        control_cos[g, g*3:g*3+3] = a; control_sin[g, g*3:g*3+3] = b
    all_cos = np.r_[cos, control_cos]; all_sin = np.r_[sin, control_sin]
    rngs = [np.random.default_rng(s['seed']+700000000) for s in pair]
    noise_stds = [s['adc_peak_codes']*.5/np.sqrt(FILTER_ENERGY)*10**(-s['snr_db']/20) for s in pair]
    mode_words = [[] for _ in range(5)]
    signal_power = [[] for _ in range(3)]; noise_power = [[] for _ in range(3)]
    clipped = 0
    for block, start in enumerate(pair[0]['block_source_start_samples']):
        streams = []; clean_streams = []
        for g, scene in enumerate(pair):
            t = scene['simulation_time_offset']+(start*4+np.arange(16384))/ADC_FS
            clean = receive(t, scene).real*10**(scene['block_gain_drift_db'][block]/20)
            noisy = clean+noise_stds[g]*rngs[g].normal(size=clean.shape)
            clipped += int(np.sum((noisy < -32768) | (noisy > 32767)))
            streams.append(digital_chain(np.clip(np.rint(noisy), -32768, 32767)))
            clean_streams.append(digital_chain(np.clip(np.rint(clean), -32768, 32767)))
        words = combine(np.concatenate(streams), all_cos, all_sin)[:, 64:]
        clean_words = combine(np.concatenate(clean_streams), cos, sin)[:, 64:]
        for k in range(5):
            mode_words[k].append(words[k])
        for k in range(3):
            a = clean_words[k].astype(float)
            d = words[k].astype(float)-a
            signal_power[k].append(float(np.mean(np.sum(a*a, axis=-1))))
            noise_power[k].append(float(np.mean(np.sum(d*d, axis=-1))))
    mode_words = [np.stack(x) for x in mode_words]
    for g in range(2):
        saved = np.fromfile(pair[g]['iq_path'], dtype='<i4').reshape(4, 4032, 2)
        np.testing.assert_array_equal(mode_words[3+g], saved)
    out = Path(output); rows = []
    for k, mode in enumerate(MODES):
        name = pair[0]['pair_id']+'_'+mode
        raw = out/'captures'/(name+'.raw')
        mode_words[k].tofile(str(raw))
        raw_hash = hashlib.sha256(mode_words[k].tobytes()).hexdigest()
        signal = float(np.mean(signal_power[k])); noise = float(np.mean(noise_power[k]))
        reference_signal = np.mean([s['combined_signal_rms']**2 for s in pair])*SCALE**2
        # Output vs mean signal power in one group: 4 means two equal,
        # coherent group outputs; small values indicate destructive addition.
        relative_power = None if pair[0]['kind'] == 'noise' else signal/max(reference_signal, 1e-20)
        coeff = dict(cos_q23=cos[k].tolist(), sin_q23=sin[k].tolist())
        for gain in GAINS:
            scaled = np.rint(mode_words[k].astype(float)*gain).astype('<i4')
            fields, _ = extract(scaled)
            tag = mode+'_gain_'+('1' if gain == 1 else '0p5')
            feature = out/'features'/(pair[0]['pair_id']+'_'+tag+'.npz')
            np.savez_compressed(str(feature), **fields)
            rows.append(dict(name=pair[0]['pair_id']+'_'+tag, pair_id=pair[0]['pair_id'],
                source_split=pair[0]['split'], kind=pair[0]['kind'], class_index=pair[0]['class_index'],
                mode=mode, input_gain=gain, condition=tag, phase_alignment_radians=phase,
                coefficients=coeff, iq_path=raw.as_posix(), iq_sha256=raw_hash,
                feature_path=feature.as_posix(),
                output_snr_db=None if pair[0]['kind'] == 'noise' else float(10*np.log10(max(signal, 1e-20)/max(noise, 1e-20))),
                rms_after_gain=float(np.sqrt(signal+noise)/SCALE*gain),
                sixsum_signal_power_relative_to_one_group=relative_power,
                amplitude_feature_saturation_fraction=float(np.mean(fields['amplitude'] == 255)),
                raw_iq_s32_overflow=False, adc_clipped_samples=clipped,
                source_group_iq_paths=[s['iq_path'] for s in pair],
                source_range_m=[s['source_range_m'] for s in pair],
                angle_degrees=[s['angle_degrees'] for s in pair], simulated=True))
    return rows


def build(args):
    root = Path(__file__).resolve().parent
    output = args.output.resolve(); source = args.source.resolve()
    if output.exists():
        raise SystemExit('Preserve existing validation; choose another output')
    checks = verify_arithmetic()
    model_path = source/'runs/branches_amp/weights/best.pt'
    assert hashlib.sha256(model_path.read_bytes()).hexdigest() == EXPECTED_MODEL_SHA
    old = [json.loads(x) for x in (source/'manifest.jsonl').read_text().splitlines()]
    pairs = defaultdict(list)
    forbidden = set()
    for item in old:
        if item['split'] in ('train', 'val') and 'pair_id' in item:
            forbidden.add(item['pair_id'])
        elif item['split'].startswith('test_'):
            pairs[item['pair_id']].append(item)
    assert len(pairs) == 1200 and not forbidden.intersection(pairs)
    for pair in pairs.values():
        pair.sort(key=lambda s:s['station_id']); assert len(pair) == 2
    output.mkdir(parents=True)
    (output/'captures').mkdir(); (output/'features').mkdir()
    plan = dict(model_weights_sha256=EXPECTED_MODEL_SHA, model_retrained=False,
        source_experiment=source.as_posix(), source_test_scenes=len(pairs),
        modes=MODES, gains=GAINS, condition_count=6, scene_count_per_condition=1200,
        primary_condition='reset_unity_gain_1', gains_fixed_before_inference=True,
        coefficients_known_from_simulation=True, actual_board_coefficients_not_observed=True,
        provenance='previous held-out transmitter scenes; no training/validation scenes',
        inter_group_alignment='idealized geometric direct-path phase reference; no ML estimation',
        group_geometry=json.loads((source/'preprocess.json').read_text()), arithmetic_checks=checks,
        bit_accuracy_scope='phase multiply, wide sum, final Q23 round/wrap only; upstream ADC/DDC/FIR numerical reference')
    (output/'validation_plan.json').write_text(json.dumps(plan, indent=2)+'\n')
    started = time.monotonic(); rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for new in pool.map(worker, [(str(output), p) for p in pairs.values()], chunksize=4):
            rows.extend(new)
            if len(rows) % 300 == 0:
                status = dict(state='building', completed_scenes=len(rows)//6, total_scenes=len(pairs),
                              elapsed_seconds=time.monotonic()-started)
                (output/'build_status.json').write_text(json.dumps(status, indent=2))
                print('Reconstructed', len(rows)//6, '/', len(pairs), 'scenes', round(status['elapsed_seconds'], 1), 's', flush=True)
    for tag, count in Counter(x['condition'] for x in rows).items():
        assert count == 1200
    summary = dict(records=len(rows), raw_files=len(rows)//2, scenes=len(pairs),
        all_2400_group_raw_captures_reconstructed_bit_exact=True,
        no_model_training=True, no_training_scenes=True, original_weights_sha256=EXPECTED_MODEL_SHA,
        arithmetic_checks=checks, elapsed_seconds=time.monotonic()-started,
        labels_per_condition={tag:dict(Counter(x['class_index'] for x in rows if x['condition'] == tag))
                              for tag in sorted({x['condition'] for x in rows})})
    (output/'manifest.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows), encoding='utf-8')
    (output/'build_checks.json').write_text(json.dumps(summary, indent=2)+'\n')
    (output/'build_status.json').write_text(json.dumps(dict(state='complete', scenes=len(pairs)), indent=2))
    print(json.dumps(summary, indent=2), flush=True)


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=root/'experiments/classifier_triplet_v5')
    parser.add_argument('--output', type=Path, default=root/'experiments/classifier_triplet_v5/sixsum_validation')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args(); build(args)


if __name__ == '__main__':
    main()
