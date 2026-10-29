"""Block-local feature fusion; no FFT or normalization crosses a capture gap."""
import cv2
import numpy as np

FS = 25_000_000
N = 4032
K = 4
WINDOW = 1024
HOP = 64
FRAMES = 48
SEPARATOR = 4
SCALE = float(2**20)
KINDS = ("single_stft", "multi_stft", "feature_fusion")
FREQUENCIES = -2e6+(np.arange(640)+0.5)*4e6/640


def contract(kind):
    assert kind in KINDS
    return dict(version=3, representation=kind, sample_rate_hz=FS,
                iq_samples=N, source_block_samples=4096,
                discard_initial_iq_samples=64, blocks=1 if kind == KINDS[0] else K,
                iq_storage="little-endian signed32 [block,sample,(I,Q)]",
                iq_scale=SCALE, fft_size=WINDOW, hop_samples=HOP,
                time_frames_per_block=FRAMES, window="periodic Hann",
                fft_scale="divide by sum(window)", ref_power=1.0,
                db_min=-75.0, db_max=0.0, frequency_crop_hz=[-2e6, 2e6],
                frequency_columns=640, frequency_interpolation="linear POWER before log",
                full_block_spectrum_window=N, full_block_spectrum_fft_length=N,
                envelope_interval_samples=64, envelope_range=[0.0, 2.0],
                separator_rows=0 if kind == KINDS[0] else SEPARATOR,
                separator_rgb=[0, 0, 0], image_height_width=[640, 640],
                resize="uint8 native feature rows, OpenCV INTER_LINEAR vertically",
                channels="RGB=(short STFT, full-block spectrum, envelope)" if kind == KINDS[2]
                         else "gray STFT repeated in RGB",
                per_block_amplitude_normalization=False, dc_removal=False,
                gaps_used_only_in_simulation_and_metadata=True,
                missing_gap_duration_not_encoded_in_image=True,
                label_policy="one full-image FM box; AM/CW/noise empty",
                source="simulated pha domain after target-specific DDS; no ADC/FIR model")


def hann(n):
    return 0.5-0.5*np.cos(2*np.pi*np.arange(n)/n)


def power_grid(x, length):
    window = hann(length)
    f = np.fft.fftshift(np.fft.fftfreq(length, 1/FS))
    p = np.abs(np.fft.fftshift(np.fft.fft(x*window, axis=-1), axes=-1)/window.sum())**2
    flat = np.atleast_2d(p)
    mapped = np.array([np.interp(FREQUENCIES, f, row) for row in flat])
    db = 10*np.log10(np.maximum(mapped, 1e-12))
    return np.rint(255*np.clip((db+75)/75, 0, 1)).astype(np.uint8)


def native_features(words):
    words = np.asarray(words)
    if words.shape != (K, N, 2):
        raise ValueError("Expected four 4032-point signed32 IQ blocks")
    iq = (words[:, :, 0].astype(float)+1j*words[:, :, 1].astype(float))/SCALE
    if not np.isfinite(iq).all():
        raise ValueError("Nonfinite IQ")
    features = []
    for x in iq:
        windows = np.lib.stride_tricks.sliding_window_view(x, WINDOW)[::HOP]
        assert windows.shape == (FRAMES, WINDOW)
        red = power_grid(windows, WINDOW)
        green = np.repeat(power_grid(x, N), FRAMES, axis=0)
        centers = np.arange(FRAMES)*HOP+WINDOW//2
        envelope = np.array([np.sqrt(np.mean(np.abs(x[c-32:c+32])**2)) for c in centers])
        blue = np.repeat(np.rint(255*np.clip(envelope/2, 0, 1)).astype(np.uint8)[:, None], 640, axis=1)
        features.append(np.stack([red, green, blue], axis=-1))
    return features


def render(features, kind):
    if kind not in KINDS:
        raise ValueError(kind)
    pieces = []
    for index, value in enumerate(features[:1] if kind == KINDS[0] else features):
        if index:
            pieces.append(np.zeros((SEPARATOR, 640, 3), dtype=np.uint8))
        if kind != KINDS[2]:
            value = np.repeat(value[:, :, :1], 3, axis=-1)
        pieces.append(value)
    return cv2.resize(np.concatenate(pieces), (640, 640), interpolation=cv2.INTER_LINEAR)


def iq_to_features_image(words, kind="feature_fusion"):
    return render(native_features(words), kind)
