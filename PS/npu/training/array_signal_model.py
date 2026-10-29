"""Six-element 433 MHz array and numerical ADC/DDC/FIR reference.

Geometry follows network/matlab/generate_fm_array_rom.m. This is NOT a
cycle-accurate Vivado model: DDS startup/AXIS timing and FIR IP internal
quantization are approximated; coefficient files, decimation phases, ADC
encoding, phase-coefficient precision and final IQ units are explicit.
"""
from pathlib import Path
import re

import numpy as np
from scipy.signal import upfirdn

ADC_FS = 100_000_000.0
IQ_FS = ADC_FS / 4
RF = 433_000_000.0
IF = 12_500_000.0
C = 299792458.0
SCALE = float(2**20)
D = C / RF / 2
POSITIONS = np.array([0, D, D+.5, 2*D+.5, 2*D+1, 3*D+1])
POSITIONS -= POSITIONS.mean()
COE_ROOT = Path(__file__).resolve().parents[3] / 'FPGA/Fudan/fpai_demo_vivado.srcs/adc_dsp/coe'


def coefficients(name):
    text = (COE_ROOT / name).read_text().split("coefdata=", 1)[1]
    return np.array([float(x) for x in re.findall(r"-?\d+", text)])


H1 = coefficients("stage1_hb_19taps_w18_q17.coe") / 2**18
H2 = coefficients("stage2_lp_80taps_w18_q17.coe") / 2**16
H2_UP = np.zeros(2*len(H2)-1)
H2_UP[::2] = H2
H_EQ = np.convolve(H1, H2_UP)
FILTER_ENERGY = float(np.sum(H_EQ**2))


def geometry(angle, distance=None):
    theta = np.deg2rad(angle)
    if distance is None:
        return -POSITIONS*np.sin(theta)/C, np.ones(6)
    r = np.hypot(distance*np.sin(theta)-POSITIONS, distance*np.cos(theta))
    delta = (POSITIONS**2-2*distance*POSITIONS*np.sin(theta))/(r+distance)
    return delta/C, distance/r


def envelope(t, scene):
    phase = np.full_like(t, scene["initial_phase"])
    kind = scene["kind"]
    if kind.startswith("fm"):
        for f, w, p in zip(scene["message_frequencies_hz"], scene["message_weights"], scene["message_phases"]):
            phase += scene["deviation_hz"]*w/f*np.sin(2*np.pi*f*t+p)
        amp = 1.0
    elif kind == "am":
        amp = 1+scene["modulation_depth"]*np.cos(2*np.pi*scene["message_frequency_hz"]*t+scene["message_phase"])
    elif kind == "cw":
        amp = 1.0
    else:
        amp = 0.0
    return amp*np.exp(1j*phase)


def receive(t, scene):
    """Ideal complex IF at six receivers, before real ADC and receiver noise."""
    t = np.asarray(t)[None, :]
    delays, spreading = geometry(scene["angle_degrees"], scene["source_range_m"])
    actual_rf = RF+scene["center_hz"]
    direct = spreading[:, None]*envelope(t-delays[:, None], scene)*np.exp(-2j*np.pi*actual_rf*delays[:, None])
    for ray in scene.get("reflections", []):
        tau, _ = geometry(ray["angle_degrees"])
        tau = tau+ray["extra_delay_s"]
        direct += ray["amplitude"]*envelope(t-tau[:, None], scene)*np.exp(
            -2j*np.pi*actual_rf*tau[:, None]+1j*ray["phase"])
    frontend = np.array(scene["channel_gains"])*np.exp(1j*np.deg2rad(scene["channel_phase_degrees"]))
    return scene["adc_peak_codes"]*direct*frontend[:, None]*np.exp(2j*np.pi*(IF+scene["center_hz"])*t)


def beam_weights(scene):
    if scene["beam_mode"] == "uncompensated":
        weights = np.ones(6, dtype=complex)
    else:
        tau, _ = geometry(scene["steering_angle_degrees"], scene["steering_range_m"])
        weights = np.exp(2j*np.pi*RF*tau-1j*np.deg2rad(scene["calibration_phase_degrees"]))
    # Hardware sin/cos fields are signed24 Q23 and cannot encode exact +1.
    def q(value):
        return np.clip(np.rint(value*2**23), -2**23, 2**23-1)/2**23
    return q(weights.real)+1j*q(weights.imag)


def digital_chain(adc):
    """16-bit real ADC -> reset DDS -> FIR phases [1,0] -> signed26 IQ."""
    n = np.arange(adc.shape[-1])
    angle = 2*np.pi*IF*n/ADC_FS
    cos = np.rint((2**23-1)*np.cos(angle))
    sin = np.rint(-(2**23-1)*np.sin(angle))
    mixed = np.floor(adc*cos/2**16)+1j*np.floor(adc*sin/2**16)
    length = adc.shape[-1]//4
    # Prepend one zero: even upfirdn samples now correspond to phase1
    # of the original input, n=1,3,... . Stage2 takes phase0, n=0,2,... .
    first = upfirdn(H1, np.pad(mixed, ((0,0),(1,0))), down=2, axis=-1)[:, 1:1+2*length]
    first = np.rint(first.real)+1j*np.rint(first.imag)
    second = upfirdn(H2, first, down=2, axis=-1)[:, :length]
    second = np.clip(np.rint(second.real), -2**25, 2**25-1)+1j*np.clip(np.rint(second.imag), -2**25, 2**25-1)
    return second


def sample_scene(kind, seed, profile="mixed"):
    rng = np.random.default_rng(seed)
    angle = float(rng.uniform(-65,65))
    distance = None if rng.random()<.55 else float(rng.uniform(3,25))
    modes = ("aligned", "imperfect", "uncompensated")
    beam = str(rng.choice(modes, p=[.55,.35,.10])) if profile=="mixed" else profile
    reflections = []
    has_multipath = rng.random()<.4
    if profile=="multipath_holdout":
        beam="imperfect";has_multipath=True
        distance=float(rng.uniform(1.5,3)) if rng.random()<.5 else float(rng.uniform(25,40))
        angle=float(rng.uniform(-80,80))
    if has_multipath:
        for _ in range(int(rng.integers(1,3))):
            reflections.append(dict(angle_degrees=float(rng.uniform(-80,80)),
                                    extra_delay_s=float(rng.uniform(50e-9,800e-9 if profile!="multipath_holdout" else 1500e-9)),
                                    amplitude=float(rng.uniform(.05,.35 if profile!="multipath_holdout" else .55)),
                                    phase=float(rng.uniform(-np.pi,np.pi))))
    hardware_phase = rng.normal(0,12,6)
    gains = 10**(rng.uniform(-1.5,1.5,6)/20)
    error = 0.0 if beam=="aligned" else float(rng.uniform(-4,4))
    calibration = hardware_phase.copy() if beam=="aligned" else hardware_phase+rng.normal(0,8,6)
    steering_distance = distance
    if beam=="imperfect" and distance is not None:
        steering_distance = distance*float(rng.uniform(.8,1.2))
    amplitude=float(rng.uniform(.25,.85))
    # Choose ADC level so an ideal calibrated six-channel CW sum retains
    # the previous training scale: amplitude*2^20. This is an assumed
    # receive-gain operating range, NOT a measured RF sensitivity.
    adc_peak=amplitude*SCALE/(6*64)
    snr=float(rng.choice([0,5,10,15,20,25]))
    if profile=="low_snr":snr=float(rng.choice([-10,-5,0]));beam="imperfect"
    gaps=rng.integers(250,500001,size=3)  # 10 us..20 ms at25MS/s
    starts=np.r_[0,np.cumsum(4096+gaps)]
    drift=rng.normal(0,.35,size=4)
    if profile in ("gain_step","gain_step_wide"):
        extent=4. if profile=="gain_step" else float(rng.uniform(2,6))
        drift=rng.uniform(-extent,extent,size=4);beam="imperfect"
    scene=dict(kind=kind,seed=seed,rf_carrier_hz=RF,adc_sample_rate_hz=ADC_FS,
               if_carrier_hz=IF,dds_frequency_hz=IF,center_hz=float(rng.uniform(-100e3,100e3)),
               amplitude=amplitude,adc_peak_codes=adc_peak,snr_db=snr,snr_definition="single-channel post-DDC carrier/noise reference; pre-combine",
               initial_phase=float(rng.uniform(-np.pi,np.pi)),simulation_time_offset=float(rng.uniform(0,.02)),
               angle_degrees=angle,source_range_m=distance,channel_gains=gains.tolist(),
               channel_phase_degrees=hardware_phase.tolist(),calibration_phase_degrees=calibration.tolist(),
               beam_mode=beam,steering_angle_degrees=angle+error,steering_range_m=steering_distance,
               steering_error_degrees=error,reflections=reflections,block_gain_drift_db=drift.tolist(),
               gap_iq_samples=gaps.tolist(),gap_us=(gaps/IQ_FS*1e6).tolist(),block_source_start_samples=starts.tolist(),
               profile=profile,occupied_nominal_bandwidth_hz=0.)
    if kind.startswith("fm"):
        wide=kind=="fm_wide"
        deviation=float(rng.uniform(200e3,1500e3) if wide else rng.uniform(20e3,150e3))
        count=int(rng.integers(1,4))
        frequencies=np.exp(rng.uniform(np.log(2000 if wide else 300),np.log(80000 if wide else 30000),count))
        weights=rng.uniform(.3,1,count);weights/=weights.sum()
        scene.update(deviation_hz=deviation,message_frequencies_hz=frequencies.tolist(),
                     message_weights=weights.tolist(),message_phases=rng.uniform(-np.pi,np.pi,count).tolist(),
                     occupied_nominal_bandwidth_hz=float(2*(deviation+max(frequencies))))
    elif kind=="am":
        band=(seed%3);bounds=[(300,2000),(2000,8000),(8000,80000)][band]
        f=float(np.exp(rng.uniform(np.log(bounds[0]),np.log(bounds[1]))))
        scene.update(message_frequency_hz=f,message_phase=float(rng.uniform(-np.pi,np.pi)),
                     modulation_depth=float(rng.uniform(.2,.9)),occupied_nominal_bandwidth_hz=2*f)
    return scene


def capture(scene):
    rng=np.random.default_rng(scene["seed"]+700000000)
    weights=beam_weights(scene)
    noise_std=scene["adc_peak_codes"]*.5/np.sqrt(FILTER_ENERGY)*10**(-scene["snr_db"]/20)
    words=[];clipped=0;signal_power=[];noise_power=[]
    for block,start in enumerate(scene["block_source_start_samples"]):
        t=scene["simulation_time_offset"]+(start*4+np.arange(16384))/ADC_FS
        clean=receive(t,scene).real*10**(scene["block_gain_drift_db"][block]/20)
        noisy=clean+noise_std*rng.normal(size=clean.shape)
        clipped+=int(np.sum(np.abs(noisy)>32767))
        adc=np.clip(np.rint(noisy),-32768,32767)
        streams=digital_chain(adc)
        combined=np.sum(streams*weights[:,None],axis=0)
        components=np.column_stack([combined.real,combined.imag])[64:]
        raw=np.rint(components).astype("<i4")
        assert raw.shape==(4032,2) and np.max(np.abs(components))<2**30
        # A paired noise-only chain gives a useful post-combine reference;
        # it includes ADC quantization residuals but is not RF hardware truth.
        noiseless=digital_chain(np.clip(np.rint(clean),-32768,32767))
        y0=np.sum(noiseless*weights[:,None],axis=0)[64:]
        delta=combined[64:]-y0
        signal_power.append(float(np.mean(np.abs(y0)**2)))
        noise_power.append(float(np.mean(np.abs(delta)**2)))
        words.append(raw)
    out=np.stack(words)
    extras=dict(adc_clipped_samples=clipped,adc_total_samples=4*6*16384,
                combined_signal_rms=float(np.sqrt(np.mean(signal_power))/SCALE),
                combined_noise_rms=float(np.sqrt(np.mean(noise_power))/SCALE),
                output_snr_db=None if scene["kind"]=="noise" else float(10*np.log10(max(np.mean(signal_power),1e-20)/max(np.mean(noise_power),1e-20))),
                simulation="real ADC100MS/s + numerical DDC/FIR26bit + Q23 beam sum; no cycle-accurate IP timing")
    return out,extras
