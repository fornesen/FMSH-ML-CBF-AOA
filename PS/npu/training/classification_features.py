"""Native segmented tensors for classification; exact old RGB is also retained."""
import cv2
import numpy as np

from fusion_features import K, N, FS, HOP, WINDOW, FRAMES, SCALE, native_features, render

CLASSES = ["FM", "AM", "CW", "noise"]
CLASS_INDEX = dict(fm_wide=0, fm_narrow=0, am=1, cw=2, noise=3)
VARIANTS = ["rgb_classifier", "branches_amp", "branches_phase"]


def contract():
    return dict(version=4, sample_rate_hz=FS, blocks=K, iq_samples=N,
                iq_storage="little-endian signed32 [4,4032,2] I,Q", iq_scale=SCALE,
                target_frequency_crop_hz=[-2e6, 2e6], target_residual_center_hz=[-100e3,100e3],
                source_rf_carrier_hz=[3e6,30e6], requires_target_tuned_dds=True,
                stft_window=WINDOW, stft_hop=HOP, stft_frames=FRAMES,
                spectrum_window=N, spectrum_fft_length=N, window="periodic Hann",
                power_range_db=[-75,0], power_interpolation="linear before log",
                envelope_interval=64, envelope_range=[0,2],
                temporal_feature_centers="512+64*m; all windows block-local",
                phase_lag=4, phase_feature="angle(sum(x[n+4]*conj(x[n])))/pi over 64 pairs",
                phase_low_energy="angle(0)=0; noisy phase retained for joint quality learning",
                tf_uint8_shape=[4,1,64,640], spectrum_uint8_shape=[4,1,1,640],
                amp_uint8_shape=[4,1,1,48], phase_float_shape=[4,1,1,48],
                tensor_scale="uint8 fields /255; phase already in [-1,1]",
                tf_time_resize="48 rows to 64 within each block, INTER_LINEAR",
                rgb_control="unchanged original R=STFT,G=spectrum,B=envelope 640x640 image",
                independent_block_processing=True, gap_duration_encoded=False,
                per_block_amplitude_normalization=False, source="synthetic pha domain; no ADC/FIR/AGC model",
                classes=CLASSES, classification_logits=4, anchors_and_nms=False)


def extract(words):
    features = native_features(words)
    tf = np.stack([cv2.resize(f[:,:,0], (640,64), interpolation=cv2.INTER_LINEAR)
                   for f in features])[:,None]
    spectrum = np.stack([f[0,:,1] for f in features])[:,None,None,:]
    amplitude = np.stack([f[:,0,2] for f in features])[:,None,None,:]
    iq = (words[:,:,0].astype(float)+1j*words[:,:,1].astype(float))/SCALE
    centers = np.arange(FRAMES)*HOP+WINDOW//2
    phase = np.array([[np.angle(np.sum(x[c-32+4:c+32+4]*x[c-32:c+32].conj()))/np.pi
                       for c in centers] for x in iq], dtype=np.float32)[:,None,None,:]
    assert phase.shape == (K,1,1,FRAMES) and np.isfinite(phase).all()
    return dict(tf=tf, spectrum=spectrum, amplitude=amplitude, phase=phase), render(features,"feature_fusion")
