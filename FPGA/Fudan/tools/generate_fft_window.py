"""Generate the fixed ML window ROM; no runtime floating-point logic in PL."""

import math
from pathlib import Path


def coefficients():
    retained = 4032
    scale = 1 << 17
    return [0] * 64 + [
        round((0.5 - 0.5 * math.cos(2 * math.pi * index / retained)) * scale)
        for index in range(retained)
    ]


def main():
    values = coefficients()
    assert len(values) == 4096
    assert min(values) == 0 and max(values) == 1 << 17
    assert sum(values) == 2016 * (1 << 17)
    target = Path(__file__).resolve().parents[1] / "fpai_demo_vivado.srcs/adc_dsp/coe/fft_hann_4032_q17.mem"
    target.write_text("".join(f"{value:05x}\n" for value in values), encoding="ascii")
    print(f"Generated {len(values)} Q17 coefficients; coherent gain = 2016 / 4096")


if __name__ == "__main__":
    main()
