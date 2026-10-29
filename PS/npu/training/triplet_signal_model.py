"""Two independent three-element arrays; shared source, never a six-IQ sum.

The user's 433 MHz half-wave spacing and 0.5 m group baseline are fixed.
Parallel local-Y ULAs, CH0:3/CH3:6, and a +X forward sector are explicit
assumptions, not measured installation coordinates. The ML document supplies
the steering convention, not ground-truth ML estimates or a localization NN.
"""
import numpy as np

from array_signal_model import (ADC_FS, IQ_FS, RF, IF, C, SCALE,
                                FILTER_ENERGY, digital_chain, envelope,
                                sample_scene as old_waveform_parameters)

D = C / RF / 2
LOCAL_POSITIONS = np.array([[0., 0.], [0., D], [0., 2*D]])
GROUP_ORIGINS = np.array([[-.25, 0.], [.25, 0.]])
GLOBAL_POSITIONS = GROUP_ORIGINS[:, None, :] + LOCAL_POSITIONS[None, :, :]
CHANNELS = [[0, 1, 2], [3, 4, 5]]


def steering(angle_degrees, frequency=RF, positions=LOCAL_POSITIONS):
    """Document convention: +X toward +Y, exp(+j k delta_r dot u)."""
    theta = np.deg2rad(angle_degrees)
    direction = np.array([np.cos(theta), np.sin(theta)])
    return np.exp(2j*np.pi*frequency/C*((positions-positions[0]) @ direction))


def q23(value):
    def q(x):
        return np.clip(np.rint(x*2**23), -2**23, 2**23-1)/2**23
    return q(value.real) + 1j*q(value.imag)


def beam_weights(scene):
    if scene['beam_mode'] == 'uncompensated':
        return q23(np.ones(3, dtype=complex))
    return q23(steering(scene['steering_angle_degrees']).conj() *
               np.exp(-1j*np.deg2rad(scene['calibration_phase_degrees'])))


def make_pair(kind, scene_seed, profile='mixed'):
    rng = np.random.default_rng(scene_seed)
    old = old_waveform_parameters(kind, scene_seed, 'aligned')
    keys = ['initial_phase', 'simulation_time_offset', 'center_hz',
            'occupied_nominal_bandwidth_hz', 'deviation_hz',
            'message_frequencies_hz', 'message_weights', 'message_phases',
            'message_frequency_hz', 'message_phase', 'modulation_depth']
    wave = {k: old[k] for k in keys if k in old}
    # Same transmitter geometry and waveform for both groups, independent RF chains.
    bearing = float(rng.uniform(-65, 65))
    distance = float(np.exp(rng.uniform(np.log(3), np.log(50))))
    target = np.array([distance*np.cos(np.deg2rad(bearing)),
                       D+distance*np.sin(np.deg2rad(bearing))])
    if profile == 'multipath_holdout':
        bearing = float(rng.uniform(-78, 78))
        target = np.array([distance*np.cos(np.deg2rad(bearing)),
                           D+distance*np.sin(np.deg2rad(bearing))])
    gaps = rng.integers(250, 500001, size=3)
    starts = np.r_[0, np.cumsum(4096+gaps)]
    pair = []
    for station in range(2):
        seed = scene_seed*2 + station
        sr = np.random.default_rng(seed)
        delta = target - GROUP_ORIGINS[station]
        local_bearing = float(np.rad2deg(np.arctan2(delta[1], delta[0])))
        source_range = float(np.linalg.norm(delta))
        mode = str(sr.choice(['aligned', 'imperfect', 'uncompensated'],
                             p=[.55, .35, .10])) if profile == 'mixed' else profile
        if profile in ('multipath_holdout', 'low_snr', 'gain_step'):
            mode = 'imperfect'
        error = 0. if mode == 'aligned' else float(sr.uniform(-4, 4))
        hardware_phase = sr.normal(0, 12, 3)
        calibration = hardware_phase.copy() if mode == 'aligned' else hardware_phase+sr.normal(0, 8, 3)
        amplitude = float(sr.uniform(.25, .85))
        # 3 channels x numerical ADC/DDC gain64. Hardware gain remains unmeasured.
        adc_peak = amplitude*SCALE/(3*64)
        snr = float(sr.choice([-5, 0, 5, 10, 15, 20, 25],
                             p=[.05, .10, .17, .20, .20, .16, .12]))
        if profile == 'low_snr':
            snr = float(sr.choice([-10, -5, 0]))
        elif profile == 'aligned':
            snr = float(sr.choice([0, 5, 10, 15, 20, 25]))
        drift = sr.normal(0, .35, 4)
        if profile == 'gain_step' or (profile == 'mixed' and sr.random() < .35):
            extent = 4. if profile == 'gain_step' else float(sr.uniform(2, 6))
            drift = sr.uniform(-extent, extent, 4)
        reflections = []
        if profile == 'multipath_holdout' or sr.random() < .45:
            reflections.append(dict(angle_degrees=float(sr.uniform(-80, 80)),
                                    extra_delay_s=float(sr.uniform(50e-9, 1500e-9)),
                                    amplitude=float(sr.uniform(.05, .55 if profile == 'multipath_holdout' else .40)),
                                    phase=float(sr.uniform(-np.pi, np.pi))))
        scene = dict(wave, kind=kind, seed=seed, scene_seed=scene_seed,
                     pair_id='pair_%d' % scene_seed, station_id=station,
                     channel_mapping=CHANNELS[station], profile=profile,
                     local_positions_m=LOCAL_POSITIONS.tolist(),
                     group_origin_m=GROUP_ORIGINS[station].tolist(),
                     source_position_m=target.tolist(), source_range_m=source_range,
                     source_global_bearing_degrees=bearing, angle_degrees=local_bearing,
                     rf_carrier_hz=RF, adc_sample_rate_hz=ADC_FS, if_carrier_hz=IF,
                     dds_frequency_hz=IF, amplitude=amplitude, adc_peak_codes=adc_peak,
                     snr_db=snr,
                     snr_definition='single-channel post-DDC carrier/noise reference before three-channel combine',
                     channel_gains=(10**(sr.uniform(-1.5, 1.5, 3)/20)).tolist(),
                     channel_phase_degrees=hardware_phase.tolist(),
                     calibration_phase_degrees=calibration.tolist(),
                     beam_mode=mode, steering_angle_degrees=local_bearing+error,
                     steering_error_degrees=error, reflections=reflections,
                     block_gain_drift_db=drift.tolist(), gap_iq_samples=gaps.tolist(),
                     gap_us=(gaps/IQ_FS*1e6).tolist(),
                     block_source_start_samples=starts.tolist(),
                     bearing_source='simulation truth plus sampled error; NOT outputs of the ML search',
                     propagation='local far-field plane waves; direct path plus at most one coherent echo')
        pair.append(scene)
    return pair


def receive(t, scene):
    t = np.asarray(t)[None, :]
    theta = np.deg2rad(scene['angle_degrees'])
    direction = np.array([np.cos(theta), np.sin(theta)])
    tau = (scene['source_range_m'] - LOCAL_POSITIONS @ direction)/C
    actual_rf = RF + scene['center_hz']
    direct = envelope(t-tau[:, None], scene)*np.exp(-2j*np.pi*actual_rf*tau[:, None])
    for ray in scene['reflections']:
        th = np.deg2rad(ray['angle_degrees'])
        ray_tau = scene['source_range_m']/C + ray['extra_delay_s'] - LOCAL_POSITIONS @ np.array([np.cos(th), np.sin(th)])/C
        direct += (ray['amplitude']*np.exp(1j*ray['phase']) *
                   envelope(t-ray_tau[:, None], scene) * np.exp(-2j*np.pi*actual_rf*ray_tau[:, None]))
    frontend = np.array(scene['channel_gains'])*np.exp(1j*np.deg2rad(scene['channel_phase_degrees']))
    return scene['adc_peak_codes']*direct*frontend[:, None]*np.exp(2j*np.pi*(IF+scene['center_hz'])*t)


def capture(scene):
    rng = np.random.default_rng(scene['seed']+700000000)
    weights = beam_weights(scene)
    noise_std = scene['adc_peak_codes']*.5/np.sqrt(FILTER_ENERGY)*10**(-scene['snr_db']/20)
    words, signal_power, noise_power = [], [], []
    clipped = 0
    for block, start in enumerate(scene['block_source_start_samples']):
        t = scene['simulation_time_offset'] + (start*4+np.arange(16384))/ADC_FS
        clean = receive(t, scene).real*10**(scene['block_gain_drift_db'][block]/20)
        noisy = clean+noise_std*rng.normal(size=clean.shape)
        clipped += int(np.sum((noisy < -32768) | (noisy > 32767)))
        streams = digital_chain(np.clip(np.rint(noisy), -32768, 32767))
        combined = np.sum(streams*weights[:, None], axis=0)
        components = np.column_stack([combined.real, combined.imag])[64:]
        assert components.shape == (4032, 2) and np.max(np.abs(components)) < 2**30
        words.append(np.rint(components).astype('<i4'))
        clean_streams = digital_chain(np.clip(np.rint(clean), -32768, 32767))
        y0 = np.sum(clean_streams*weights[:, None], axis=0)[64:]
        signal_power.append(float(np.mean(np.abs(y0)**2)))
        noise_power.append(float(np.mean(np.abs(combined[64:]-y0)**2)))
    return np.stack(words), dict(adc_clipped_samples=clipped, adc_total_samples=4*3*16384,
        combined_signal_rms=float(np.sqrt(np.mean(signal_power))/SCALE),
        combined_noise_rms=float(np.sqrt(np.mean(noise_power))/SCALE),
        output_snr_db=None if scene['kind'] == 'noise' else float(10*np.log10(max(np.mean(signal_power), 1e-20)/max(np.mean(noise_power), 1e-20))),
        simulation='real ADC100MS/s + numerical DDC/FIR26bit + Q23 three-channel sum; no cycle-accurate IP timing')
