"""445 MHz six-channel numerical reference and the deployed batch-gain contract.

RF propagation uses 445 MHz; physical antenna positions retain the existing
433 MHz half-wave spacing. ADC/DDC/FIR is numerical, not RTL timing simulation.
PM is the user's analog sine PM, not PSK. PM/FM can be waveform-equivalent.
"""
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sixsum_ook_signal import sample_pair as old_pair, ook_envelope
from sixsum_validation import combine, q23_codes
from triplet_signal_model import LOCAL_POSITIONS, q23, steering
from array_signal_model import ADC_FS, IQ_FS, IF, C, SCALE, FILTER_ENERGY, digital_chain, envelope

RF = 445_000_000.
CLASSES = ['FM', 'AM', 'CW', 'noise', 'OOK', 'PM']
KINDS = ['fm_wide', 'fm_narrow', 'am', 'cw', 'noise', 'ook', 'pm']
CLASS_INDEX = dict(fm_wide=0, fm_narrow=0, am=1, cw=2, noise=3, ook=4, pm=5)


def sample_pair(kind, seed, profile='mixed'):
    pair = old_pair('cw' if kind == 'pm' else kind, seed,
                    'low_snr' if profile == 'low_snr' else 'mixed')
    rng = np.random.default_rng(seed + 42_000_000_000)
    center = float(rng.uniform(-800e3, 800e3))
    if seed % 3 == 0: center = float(rng.uniform(-100e3, 100e3))
    if seed % 3 == 1: center = float(rng.uniform(-550e3, -450e3))
    pm_f = float(rng.uniform(15e3, 25e3))
    pm_deg = float(rng.uniform(60, 120))
    if profile == 'target_pm': pm_f, pm_deg = 20e3, 90.
    if profile == 'low_pm': pm_f = float(rng.uniform(2e3, 8e3))
    if profile == 'target_pm3330': pm_f, pm_deg = 10000./3, 90.
    pm_phase = float(rng.uniform(-np.pi, np.pi))
    snr = float(rng.choice([-5, 0, 5, 10, 15, 20, 25, 35, 45, 55, 65]))
    if profile == 'low_snr': snr = float(rng.choice([-10, -5, 0, 5]))
    if profile == 'high_snr': snr = float(rng.uniform(40, 70))
    periodic = kind == 'ook' and rng.random() < .5
    period = float(rng.uniform(35e-6, 65e-6))
    duty = float(rng.uniform(.35, .65))
    adc_peak = float(np.exp(rng.uniform(np.log(5), np.log(1200))))
    for i, scene in enumerate(pair):
        scene.update(kind=kind, rf_carrier_hz=RF, center_hz=center,
                     adc_peak_codes=adc_peak * float(rng.uniform(.85, 1.15)),
                     snr_db=snr + float(rng.uniform(-2, 2)), adc_bits=12)
        if kind == 'pm':
            scene.update(pm_waveform='sine', pm_frequency_hz=pm_f,
                         pm_deviation_degrees=pm_deg, pm_deviation_rad=np.deg2rad(pm_deg).item(),
                         pm_message_phase=pm_phase,
                         occupied_nominal_bandwidth_hz=2*(np.deg2rad(pm_deg)+1)*pm_f)
        if periodic:
            scene.update(ook_periodic=True, ook_period_s=period, ook_duty=duty,
                         ook_period_phase=pm_phase/(2*np.pi), ook_edge_s=0.5e-6)
    return pair


def modulated_envelope(t, s):
    if s['kind'] == 'pm':
        return np.exp(1j*(s['initial_phase'] + s['pm_deviation_rad']*
                         np.sin(2*np.pi*s['pm_frequency_hz']*t+s['pm_message_phase'])))
    if s['kind'] == 'ook':
        if not s.get('ook_periodic'): return ook_envelope(t, s)
        u = (np.asarray(t)/s['ook_period_s']+s['ook_period_phase']) % 1
        edge = s['ook_edge_s']/s['ook_period_s']
        on = np.clip(u/edge, 0, 1)*np.clip((s['ook_duty']-u)/edge, 0, 1)
        on = .5-.5*np.cos(np.pi*on)
        return (s['ook_off_amplitude']+(1-s['ook_off_amplitude'])*on)*np.exp(1j*s['initial_phase'])
    return envelope(t, s)


def receive(t, scene):
    t = np.asarray(t)[None, :]
    th = np.deg2rad(scene['angle_degrees'])
    tau = (scene['source_range_m']-LOCAL_POSITIONS @ np.array([np.cos(th), np.sin(th)]))/C
    actual_rf = RF + scene['center_hz']
    direct = modulated_envelope(t-tau[:, None], scene)*np.exp(-2j*np.pi*actual_rf*tau[:, None])
    for ray in scene['reflections']:
        th = np.deg2rad(ray['angle_degrees'])
        rt = scene['source_range_m']/C+ray['extra_delay_s']-LOCAL_POSITIONS @ np.array([np.cos(th), np.sin(th)])/C
        direct += ray['amplitude']*np.exp(1j*ray['phase'])*modulated_envelope(t-rt[:, None], scene)*np.exp(-2j*np.pi*actual_rf*rt[:, None])
    frontend = np.array(scene['channel_gains'])*np.exp(1j*np.deg2rad(scene['channel_phase_degrees']))
    return scene['adc_peak_codes']*direct*frontend[:, None]*np.exp(2j*np.pi*(IF+scene['center_hz'])*t)


def capture(pair, mode):
    weights = []
    for s in pair:
        w = np.ones(3, dtype=complex) if mode == 'reset_unity' or s['beam_mode'] == 'uncompensated' else (
            steering(s['steering_angle_degrees'], frequency=RF).conj()*np.exp(-1j*np.deg2rad(s['calibration_phase_degrees'])))
        weights.extend(q23(w))
    cos, sin = q23_codes(np.asarray(weights))
    rngs = [np.random.default_rng(s['seed']+700000000) for s in pair]
    stds = [s['adc_peak_codes']*.5/np.sqrt(FILTER_ENERGY)*10**(-s['snr_db']/20) for s in pair]
    # A noise-only reference has no carrier SNR. Retain a finite ADC noise
    # floor instead of quantizing many independent scenes to identical zeros.
    if pair[0]['kind']=='noise': stds = [max(.5, v) for v in stds]
    raw, signal, noise = [], [], []
    clipped = 0
    for block, start in enumerate(pair[0]['block_source_start_samples']):
        streams, clean_streams = [], []
        for group, scene in enumerate(pair):
            t = scene['simulation_time_offset']+(start*4+np.arange(16384))/ADC_FS
            clean = receive(t, scene).real*10**(scene['block_gain_drift_db'][block]/20)
            noisy = clean+stds[group]*rngs[group].normal(size=clean.shape)
            clipped += int(np.sum((noisy < -2048) | (noisy > 2047)))
            streams.append(digital_chain(np.clip(np.rint(noisy), -2048, 2047)))
            clean_streams.append(digital_chain(np.clip(np.rint(clean), -2048, 2047)))
        words = combine(np.concatenate(streams), cos[None], sin[None])[0]
        clean_words = combine(np.concatenate(clean_streams), cos[None], sin[None])[0]
        raw.append(words)
        a = clean_words[64:].astype(float); d = words[64:].astype(float)-a
        signal.append(float(np.mean(np.sum(a*a, axis=-1))))
        noise.append(float(np.mean(np.sum(d*d, axis=-1))))
    return np.stack(raw).astype('<i4'), dict(adc_clipped_samples=clipped,
        adc_total_samples=4*6*16384, output_snr_db=None if pair[0]['kind']=='noise' else
        float(10*np.log10(max(np.mean(signal), 1e-20)/max(np.mean(noise), 1e-20))),
        combined_signal_rms=float(np.sqrt(np.mean(signal))/SCALE),
        combined_noise_rms=float(np.sqrt(np.mean(noise))/SCALE))


def batch_gain(x):
    """Same percentile, lag-1 gate, common gain, bounds as PS io.cpp v1."""
    rms = np.sqrt(np.mean(np.abs(x.reshape(4, 63, 64))**2, axis=-1)).ravel()
    ref = float(np.sort(rms)[int(np.ceil(.95*len(rms)))-1])
    left, right = x[:, :-1], x[:, 1:]
    denom = np.sqrt(np.sum(abs(left)**2)*np.sum(abs(right)**2))
    coherence = min(1., float(abs(np.sum(right*left.conj()))/denom)) if denom else 0.
    peak = float(abs(x).max())
    gain = 1.
    if 0 < ref < .25 and coherence >= .5:
        gain = min(.25/ref, 256., max(1., 1.5/peak))
    return x*gain, dict(gain=gain, reference_rms=ref, lag1_coherence=coherence, peak_before=peak)


def grid(x):
    n = x.shape[-1]
    w = .5-.5*np.cos(2*np.pi*np.arange(n)/n)
    p = abs(np.fft.fftshift(np.fft.fft(x*w, axis=-1), axes=-1)/w.sum())**2
    pos = (-2e6+(np.arange(640)+.5)*4e6/640+IQ_FS/2)*n/IQ_FS
    lo = np.floor(pos).astype(int); q = pos-lo
    db = 10*np.log10(np.maximum(p[..., lo]*(1-q)+p[..., lo+1]*q, 1e-12))
    return np.rint(255*np.clip((db+75)/75, 0, 1)).astype(np.uint8)


def features(raw):
    a = raw[:, 64:, :]
    x, meta = batch_gain((a[:, :, 0].astype(float)+1j*a[:, :, 1])/SCALE)
    windows = np.lib.stride_tricks.sliding_window_view(x, 1024, axis=-1)[:, ::64]
    native = grid(windows).astype(np.int32)
    y = (np.arange(64)+.5)*.75-.5
    lo = np.floor(y).astype(int); q = np.rint((y-lo)*8).astype(int)
    q[(lo < 0) | (lo >= 47)] = 0
    lo = np.clip(lo, 0, 47); hi = np.minimum(lo+1, 47)
    tf = ((native[:, lo]*(8-q)[None, :, None]//2+native[:, hi]*q[None, :, None]//2+2)//4).astype(np.uint8)
    centers = 512+np.arange(48)*64
    rms = np.sqrt(np.mean(abs(x[:, centers[:, None]+np.arange(-32, 32)])**2, axis=-1))
    amp = np.rint(255*np.clip(rms/2, 0, 1)).astype(np.uint8)[:, None, None, :]
    return dict(tf=tf[:, None], spectrum=grid(x)[:, None, None], amplitude=amp,
                phase=np.zeros((4, 1, 1, 48), np.float32)), meta
