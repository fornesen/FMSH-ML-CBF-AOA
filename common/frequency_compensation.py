"""Select measured channel corrections or interpolate between adjacent RF nodes.

No device or file access. Interpolation is a numerical estimate, not another
measured calibration; the selection records that distinction explicitly.
"""
from bisect import bisect_left
import cmath
import math


def _values(node):
    correction = [complex(*pair) for pair in node['correction']]
    offsets = list(map(float, node['phase_center_dx_m']))
    if (len(correction) != 6 or len(offsets) != 6
            or any(not math.isfinite(abs(value)) or abs(value) == 0 for value in correction)
            or not all(math.isfinite(value) for value in offsets)):
        raise ValueError('Compensation requires six finite nonzero coefficients and six offsets')
    return correction, offsets


def select(bank, rf_hz, *, interpolate=True):
    """Return an exact node or log-amplitude/shortest-phase-arc interpolation.

    Failed nodes remain in the grid: a selection never skips over them. No
    extrapolation is performed, and measured coefficients are not renormalized.
    """
    target = float(rf_hz)
    nodes = bank['nodes']
    frequencies = [float(node['rf_hz']) for node in nodes]
    if (not math.isfinite(target) or not frequencies
            or any(not math.isfinite(f) or f <= 0 for f in frequencies)
            or any(b <= a for a, b in zip(frequencies, frequencies[1:]))):
        raise ValueError('Compensation RF grid must be finite, positive, and strictly increasing')
    index = bisect_left(frequencies, target)
    if index < len(nodes) and frequencies[index] == target:
        chosen = [nodes[index]]
        source = 'measured'
    else:
        if index == 0 or index == len(nodes):
            raise ValueError('RF is outside the measured compensation band; no extrapolation')
        if not interpolate:
            raise ValueError('RF is not a measured compensation node')
        chosen = nodes[index - 1:index + 1]
        source = 'interpolated'
    if any(node.get('accepted') is not True for node in chosen):
        raise ValueError('RF selection touches an unaccepted compensation node')
    left, left_offsets = _values(chosen[0])
    if source == 'measured':
        correction, offsets = left, left_offsets
    else:
        right, right_offsets = _values(chosen[1])
        fraction = (target - frequencies[index - 1]) / (frequencies[index] - frequencies[index - 1])
        correction = [cmath.rect(
            math.exp((1 - fraction)*math.log(abs(a)) + fraction*math.log(abs(b))),
            cmath.phase(a) + fraction*cmath.phase(b/a)) for a, b in zip(left, right)]
        offsets = [(1 - fraction)*a + fraction*b for a, b in zip(left_offsets, right_offsets)]
    return {'accepted': True, 'target_rf_hz': target,
            'correction': [[value.real, value.imag] for value in correction],
            'phase_center_dx_m': offsets, 'source': source,
            'bracket_frequencies_hz': [node['rf_hz'] for node in chosen],
            'interpolation_validated': False if source == 'interpolated' else None,
            'coefficients_applied': False}


def csv_text(selection):
    """Encode the production five-column, six-channel compensation contract."""
    if selection.get('accepted') is not True:
        raise ValueError('Cannot export an unaccepted compensation selection')
    rf_hz = float(selection['target_rf_hz'])
    if not math.isfinite(rf_hz) or rf_hz <= 0:
        raise ValueError('Compensation RF must be positive and finite')
    correction, offsets = _values(selection)
    lines = ['channel,rf_hz,correction_real,correction_imag,phase_center_dx_m']
    lines.extend('%d,%.17g,%.17g,%.17g,%.17g' %
                 (ch, rf_hz, value.real, value.imag, offsets[ch])
                 for ch, value in enumerate(correction))
    return '\n'.join(lines) + '\n'
