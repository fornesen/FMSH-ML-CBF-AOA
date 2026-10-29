"""Compare six-channel fixed-point window output with the previous PS math."""

import json
from pathlib import Path
import numpy as np


def main():
    root = Path(__file__).resolve().parents[1]
    values = (root / "fpai_demo_vivado.srcs/adc_dsp/coe/fft_hann_4032_q17.mem").read_text().split()
    coefficient = np.array([int(value, 16) for value in values], dtype=np.int64)
    window = np.zeros(4096)
    window[64:] = (1 - np.cos(2 * np.pi * np.arange(4032) / 4032)) / 2
    assert np.array_equal(coefficient, np.rint(window * (1 << 17)))
    assert coefficient.sum() == 2016 * (1 << 17)
    cases = []
    for amplitude in (800, 300000, 25000000):
        for frequency_bin in (0, 29, 2048, 4014):
            phase = 2 * np.pi * frequency_bin * np.arange(4096) / 4096
            signal = amplitude * np.exp(1j * (phase[None, :] + np.arange(6)[:, None] * .41))
            i_value, q_value = np.rint(signal.real), np.rint(signal.imag)
            i_value[:, :64], q_value[:, :64] = 1234567, -2345678
            original = i_value + 1j * q_value
            # Existing PS recovers PL IQ from the unwindowed FFT, then windows once.
            software = np.fft.fft(np.fft.ifft(np.fft.fft(original)) * window) / window.sum()
            fixed = np.rint(i_value * coefficient / (1 << 17)) + 1j * np.rint(q_value * coefficient / (1 << 17))
            hardware = np.fft.fft(fixed) / 2016
            assert np.all(fixed[:, :64] == 0)
            a, b = software[:, frequency_bin], hardware[:, frequency_bin]
            phase_error = np.angle((b / b[0]) / (a / a[0]), deg=True)
            gain_error = np.abs(np.abs(b) / np.abs(a) - 1)
            assert max(abs(phase_error)) < .01
            assert max(gain_error) < .0002
            cases.append({"amplitude": amplitude, "bin": frequency_bin,
                          "max_relative_phase_error_deg": float(max(abs(phase_error))),
                          "max_gain_error_fraction": float(max(gain_error))})
    print(json.dumps({"cases": cases, "note": "Fixed-point window only; FFT IP quantization and board measurements excluded"}, indent=2))


if __name__ == "__main__":
    main()
