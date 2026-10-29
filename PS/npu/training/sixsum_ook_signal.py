"""Six-channel pha reference with random NRZ OOK, retaining the v5 geometry.

Only the Q23 rotation/wide sum/final rounding is RTL exact. ADC/DDC/FIR is
the existing numerical reference, not a cycle-accurate FPGA simulation.
"""
import numpy as np

from array_signal_model import ADC_FS, IQ_FS, RF, IF, C, SCALE, FILTER_ENERGY, digital_chain, envelope
from triplet_signal_model import make_pair, beam_weights, LOCAL_POSITIONS
from sixsum_validation import combine, q23_codes, verify_arithmetic

CLASSES = ['FM', 'AM', 'CW', 'noise', 'OOK']
CLASS_INDEX = dict(fm_wide=0, fm_narrow=0, am=1, cw=2, noise=3, ook=4)


def bits(index, seed, probability):
    """Counter-based bits: shared by every antenna, no block reset/repetition."""
    x = np.asarray(index, dtype=np.int64).astype(np.uint64) ^ np.uint64(seed)
    with np.errstate(over='ignore'):
        x = x + np.uint64(0x9E3779B97F4A7C15)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        x = x ^ (x >> np.uint64(31))
    return ((x >> np.uint64(11)).astype(np.float64) / 2**53 < probability).astype(float)


def ook_envelope(t, scene):
    position = np.asarray(t) * scene['ook_bitrate_bps'] + scene['ook_clock_phase']
    index = np.floor(position).astype(np.int64)
    fraction = position - index
    current = bits(index, scene['ook_bit_seed'], scene['ook_one_probability'])
    previous = bits(index-1, scene['ook_bit_seed'], scene['ook_one_probability'])
    # A finite edge, with stable symbol timing across the unobserved gaps.
    progress = np.clip(fraction/scene['ook_rise_symbol_fraction'], 0, 1)
    edge = .5-.5*np.cos(np.pi*progress)
    state = previous+(current-previous)*edge
    amplitude = scene['ook_off_amplitude']+(1-scene['ook_off_amplitude'])*state
    return amplitude*np.exp(1j*scene['initial_phase'])


def modulated_envelope(t, scene):
    return ook_envelope(t, scene) if scene['kind']=='ook' else envelope(t, scene)


def receive(t, scene):
    t = np.asarray(t)[None, :]
    theta = np.deg2rad(scene['angle_degrees'])
    tau = (scene['source_range_m']-LOCAL_POSITIONS @ np.array([np.cos(theta), np.sin(theta)]))/C
    actual_rf = RF+scene['center_hz']
    direct = modulated_envelope(t-tau[:, None], scene)*np.exp(-2j*np.pi*actual_rf*tau[:, None])
    for ray in scene['reflections']:
        th = np.deg2rad(ray['angle_degrees'])
        ray_tau = scene['source_range_m']/C+ray['extra_delay_s']-LOCAL_POSITIONS @ np.array([np.cos(th),np.sin(th)])/C
        direct += ray['amplitude']*np.exp(1j*ray['phase'])*modulated_envelope(t-ray_tau[:,None],scene)*np.exp(-2j*np.pi*actual_rf*ray_tau[:,None])
    frontend = np.array(scene['channel_gains'])*np.exp(1j*np.deg2rad(scene['channel_phase_degrees']))
    return scene['adc_peak_codes']*direct*frontend[:,None]*np.exp(2j*np.pi*(IF+scene['center_hz'])*t)


def sample_pair(kind, seed, profile='mixed'):
    # OOK replaces the shared CW envelope; geometry and noise distributions
    # are sampled by the same code used for all the existing classes.
    pair = make_pair('cw' if kind=='ook' else kind, seed, profile if profile!='slow_ook' else 'mixed')
    rng = np.random.default_rng(seed+10000000000)
    if kind=='ook':
        bounds=[(2000,10000),(10000,50000),(50000,200000)][seed%3]
        if profile=='slow_ook': bounds=(100,2000)
        params=dict(kind='ook',ook_bitrate_bps=float(np.exp(rng.uniform(np.log(bounds[0]),np.log(bounds[1])))),
                    ook_bit_seed=int(seed+30000000000),ook_clock_phase=float(rng.random()),
                    ook_one_probability=float(rng.uniform(.3,.7)),ook_rise_symbol_fraction=float(rng.uniform(.02,.15)),
                    ook_off_amplitude=float(rng.uniform(0,.03)),
                    ook_waveform='aperiodic random NRZ, raised-cosine finite transitions, common source clock')
        for scene in pair:
            scene.update(params)
            scene['occupied_nominal_bandwidth_hz']=2*params['ook_bitrate_bps']
    return pair


def capture(pair, mode):
    assert mode in ('reset_unity','local_group_weights')
    weights=np.ones(6,dtype=complex) if mode=='reset_unity' else np.concatenate([beam_weights(s) for s in pair])
    cos,sin=q23_codes(weights)
    rngs=[np.random.default_rng(s['seed']+700000000) for s in pair]
    stds=[s['adc_peak_codes']*.5/np.sqrt(FILTER_ENERGY)*10**(-s['snr_db']/20) for s in pair]
    raw=[]; signals=[]; noises=[]; clipped=0
    transitions=[]; levels=[]
    for block,start in enumerate(pair[0]['block_source_start_samples']):
        noisy_streams=[]; clean_streams=[]
        for group,scene in enumerate(pair):
            t=scene['simulation_time_offset']+(start*4+np.arange(16384))/ADC_FS
            clean=receive(t,scene).real*10**(scene['block_gain_drift_db'][block]/20)
            noisy=clean+stds[group]*rngs[group].normal(size=clean.shape)
            clipped+=int(np.sum((noisy < -32768)|(noisy > 32767)))
            noisy_streams.append(digital_chain(np.clip(np.rint(noisy),-32768,32767)))
            clean_streams.append(digital_chain(np.clip(np.rint(clean),-32768,32767)))
        words=combine(np.concatenate(noisy_streams),cos[None],sin[None])[0]
        clean_words=combine(np.concatenate(clean_streams),cos[None],sin[None])[0]
        raw.append(words)
        a=clean_words[64:].astype(float); d=words[64:].astype(float)-a
        signals.append(float(np.mean(np.sum(a*a,axis=-1))))
        noises.append(float(np.mean(np.sum(d*d,axis=-1))))
        if pair[0]['kind']=='ook':
            scene=pair[0]
            t=scene['simulation_time_offset']+(start+np.arange(64,4096))/IQ_FS-scene['source_range_m']/C
            values=np.abs(ook_envelope(t,scene))
            states=values>.5
            transitions.append(int(np.count_nonzero(states[1:]!=states[:-1])))
            levels.append(float(np.mean(states)))
    raw=np.stack(raw).astype('<i4')
    signal=float(np.mean(signals)); noise=float(np.mean(noises))
    extras=dict(adc_clipped_samples=clipped,adc_total_samples=4*6*16384,
                output_snr_db=None if pair[0]['kind']=='noise' else float(10*np.log10(max(signal,1e-20)/max(noise,1e-20))),
                combined_signal_rms=float(np.sqrt(signal)/SCALE),combined_noise_rms=float(np.sqrt(noise)/SCALE),
                coefficients=dict(cos_q23=cos.tolist(),sin_q23=sin.tolist()),
                ook_observed_transitions_per_block=transitions,ook_observed_on_fraction_per_block=levels)
    if transitions:
        extras['ook_visible_transition']=sum(transitions)>0
        extras['ook_observed_both_levels']=min(levels)<.95 and max(levels)>.05
    return raw,extras


def verify():
    from triplet_signal_model import receive as old_receive
    checks=verify_arithmetic()
    for kind in ('fm_wide','fm_narrow','am','cw','noise'):
        scene=sample_pair(kind,6100000000)[0]
        t=scene['simulation_time_offset']+np.arange(16384)/ADC_FS
        np.testing.assert_array_equal(receive(t,scene),old_receive(t,scene))
    scene=sample_pair('ook',6100000123)[0]
    t=np.linspace(-.01,.1,50000)
    a=ook_envelope(t,scene)
    np.testing.assert_array_equal(a[:1000],ook_envelope(t[:1000],scene))
    assert np.abs(a).min()<.04 and np.abs(a).max()>.99
    assert np.unique(bits(np.arange(10000),scene['ook_bit_seed'],.5)).tolist()==[0.,1.]
    checks.update(old_class_receive_reference_exact=True,ook_random_bits_and_block_invariance=True)
    return checks
