"""Independent fixed-point stimuli and numerical comparison for the PL window."""

import argparse
import json
import math
from pathlib import Path
import random


def pack(i_value, q_value, padding=0):
    mask = (1 << 26) - 1
    return (i_value & mask) | ((q_value & mask) << 32) | padding


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7301)
    source = []
    reference = []
    max_error = 0.0
    coefficients = []
    for index in range(4096):
        window = 0 if index < 64 else (1 - math.cos(2 * math.pi * (index - 64) / 4032)) / 2
        coefficients.append(round(window * (1 << 17)))
    for frame in range(12):
        for index, coefficient in enumerate(coefficients):
            if frame % 6 == 0:
                i_value, q_value = -(1 << 25), (1 << 25) - 1
            elif frame % 6 == 1:
                i_value, q_value = 1, -1
            elif frame % 6 == 2:
                i_value, q_value = 3, -3
            elif frame % 6 == 3:
                i_value, q_value = rng.randrange(-(1 << 25), 1 << 25), rng.randrange(-(1 << 25), 1 << 25)
            else:
                phase = 2 * math.pi * 4014 * index / 4096 + frame * 0.41
                i_value = round(300000 * math.cos(phase))
                q_value = round(300000 * math.sin(phase))
            # Exact rational rounding oracle, independent of RTL signed bit slicing.
            output = [round(value * coefficient / (1 << 17)) for value in (i_value, q_value)]
            source.append(pack(i_value, q_value, 0xfc000000fc000000))
            reference.append(pack(*output))
            for value, rounded in zip((i_value, q_value), output):
                max_error = max(max_error, abs(rounded - value * coefficient / (1 << 17)))
    for name, data in [("window_input.mem", source), ("window_expected.mem", reference)]:
        (args.output / name).write_text("".join(f"{value:016x}\n" for value in data), encoding="ascii")
    print(json.dumps({"samples": len(source), "coefficient_sum": sum(coefficients),
                      "coherent_gain_sum": sum(coefficients) / (1 << 17),
                      "max_rounding_error_lsb": max_error}))


if __name__ == "__main__":
    main()
