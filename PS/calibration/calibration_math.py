"""Relative six-channel calibration from known point sources; standard library only."""
import cmath
import csv
import math
from pathlib import Path
import statistics
import struct

CHANNELS = 6
FFT_LENGTH = 4096
SLOT_BYTES = 64
LIGHT_SPEED = 299792458.0
HEADER = ['channel', 'x_m', 'y_m', 'correction_real', 'correction_imag']
LIMITS = {
    'minimum_range_m': 0.5,
    'peak_to_local_floor_db': 20.0,
    'tone_band_fraction': 0.8,
    'coherence': 0.98,
    'point_phase_difference_deg': 10.0,
    'point_gain_difference_db': 1.5,
    'validation_residual': 0.05,
    'maximum_correction_db': 18.0,
    'maximum_adc_near_rail_fraction': 0.001,
}


class InconsistentPoints(ValueError):
    """No common correction exists under the point-source model."""


def check_adc(payload, settings):
    if len(payload) != 16384 * CHANNELS * 2:
        raise ValueError('Incomplete ADC frame')
    bits = settings['adc_bits']
    if bits not in (12, 14, 16) or settings['adc_encoding'] not in ('twos-complement', 'offset-binary'):
        raise ValueError('Unsupported ADC format')
    mask, sign = (1 << bits) - 1, 1 << (bits - 1)
    counts = [0] * CHANNELS
    for i, (word,) in enumerate(struct.iter_unpack('<H', payload)):
        if word & ~mask:
            raise ValueError('Nonzero ADC padding bits')
        if settings['adc_encoding'] == 'offset-binary':
            word ^= sign
        value = word - (1 << bits) if word & sign else word
        if abs(value) >= sign * .98:
            counts[i % CHANNELS] += 1
    fractions = [n / 16384 for n in counts]
    if max(fractions) > LIMITS['maximum_adc_near_rail_fraction']:
        raise ValueError('ADC 接近满量程/削顶，请降低校准信号幅度')
    return fractions


def read_array(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != HEADER:
            raise ValueError('Array CSV header does not match the PS format')
        rows = list(reader)
    if len(rows) != CHANNELS or [int(r['channel']) for r in rows] != list(range(CHANNELS)):
        raise ValueError('Array CSV must list CH0..CH5 once, in order')
    geometry = [[float(r['x_m']), float(r['y_m']), 0.0] for r in rows]
    gains = [complex(float(r['correction_real']), float(r['correction_imag'])) for r in rows]
    if any(not math.isfinite(v) for p in geometry for v in p):
        raise ValueError('Nonfinite antenna coordinate')
    if any(not math.isfinite(abs(g)) or abs(g) == 0 for g in gains):
        raise ValueError('Invalid existing correction')
    return geometry, gains


def array_text(geometry, correction):
    lines = [','.join(HEADER)]
    for ch, (point, gain) in enumerate(zip(geometry, correction)):
        lines.append('%d,%.17g,%.17g,%.17g,%.17g' %
                     (ch, point[0], point[1], gain.real, gain.imag))
    return '\n'.join(lines) + '\n'


def response(geometry, point, rf_hz, phase_sign=1):
    """Spherical 1/r voltage response, referenced to CH0, matching PS phase sign."""
    if len(point) != 3 or any(not math.isfinite(v) for v in point):
        raise ValueError('A point needs finite X, Y, Z coordinates')
    if not math.isfinite(rf_hz) or rf_hz <= 0 or phase_sign not in (-1, 1):
        raise ValueError('Invalid RF frequency or phase sign')
    distances = [math.sqrt(sum((a - b)**2 for a, b in zip(point, ant))) for ant in geometry]
    if min(distances) < LIMITS['minimum_range_m']:
        raise ValueError('校准点距某根天线不足 0.5 m；请移远并重新测量位置')
    k = phase_sign * 2 * math.pi * rf_hz / LIGHT_SPEED
    return [(distances[0] / r) * cmath.exp(-1j * k * (r - distances[0])) for r in distances]


def decode39(data):
    bits = int.from_bytes(data, 'little') & ((1 << 39) - 1)
    return bits - (1 << 39) if bits & (1 << 38) else bits


def decode_slots(payload):
    if len(payload) != CHANNELS * FFT_LENGTH * SLOT_BYTES:
        raise ValueError('Incomplete FFT slots file')
    values = [[] for _ in range(CHANNELS)]
    for record in range(CHANNELS * FFT_LENGTH):
        start = record * SLOT_BYTES
        slot = payload[start:start + SLOT_BYTES]
        if any(slot[10:16]) or any(slot[24:64]):
            raise ValueError('Invalid FFT slot padding at record %d' % record)
        # Same signed 39-bit real/imaginary layout as pl_fft_decode80().
        values[record // FFT_LENGTH].append(complex(decode39(slot[:5]), decode39(slot[5:10])))
    return values


def extract_frame(payload, settings):
    """Read the already-windowed PL FFT; never window, IFFT, or apply old gains."""
    fft = decode_slots(payload)
    channels = settings.get('calibration_channels', list(range(CHANNELS)))
    if not channels or len(set(channels)) != len(channels) or any(ch not in range(CHANNELS) for ch in channels):
        raise ValueError('Invalid calibration channel mask')
    df = settings['sample_rate_hz'] / 4 / FFT_LENGTH
    band = [b for b in range(FFT_LENGTH) if settings['band_min_hz'] <=
            (b if b < FFT_LENGTH // 2 else b - FFT_LENGTH) * df <= settings['band_max_hz']]
    if len(band) < 5:
        raise ValueError('Calibration search band needs at least five FFT bins')
    peak = max(band, key=lambda b: sum(abs(fft[ch][b])**2 for ch in channels))
    near = {(peak + delta) % FFT_LENGTH for delta in range(-2, 3)}
    floor = [(peak + delta) % FFT_LENGTH for delta in range(-40, 41) if abs(delta) >= 6]
    snr, fractions = [], []
    for ch, row in enumerate(fft):
        power = abs(row[peak])**2
        noise = statistics.median(abs(row[b])**2 for b in floor)
        ratio_db = 10 * math.log10(max(power, 1e-30) / max(noise, 1.0))
        total = sum(abs(row[b])**2 for b in band)
        fraction = sum(abs(row[b])**2 for b in band if b in near) / max(total, 1e-30)
        if ch in channels and (power == 0 or ratio_db < LIMITS['peak_to_local_floor_db']):
            raise ValueError('CH%d 校准单音太弱：峰值/局部底噪 %.1f dB，需要 >= 20 dB' % (ch, ratio_db))
        if ch in channels and fraction < LIMITS['tone_band_fraction']:
            raise ValueError('CH%d 搜索带内不是占优单音；请使用固定连续波' % ch)
        snr.append(ratio_db)
        fractions.append(fraction)
    return {'bin': peak, 'frequency_hz': (peak if peak < 2048 else peak - 4096) * df,
            'values': [[row[peak].real, row[peak].imag] for row in fft],
            'peak_to_local_floor_db': snr, 'tone_band_fraction': fractions,
            'quality_channels': channels}


def vector(frame, required_channels=None):
    values = [complex(*v) for v in frame['values']]
    required = range(CHANNELS) if required_channels is None else required_channels
    if (len(values) != CHANNELS or any(not math.isfinite(abs(v)) for v in values)
            or any(abs(values[ch]) == 0 for ch in required)):
        raise ValueError('Invalid six-channel observation')
    return values


def estimate(frames, steering):
    """LS ratios cancel each capture's arbitrary common phase and source amplitude."""
    rows = [vector(f) for f in frames]
    reference_energy = sum(abs(row[0])**2 for row in rows)
    gains, coherences = [], []
    for ch in range(CHANNELS):
        cross = sum(row[ch] * row[0].conjugate() for row in rows)
        energy = sum(abs(row[ch])**2 for row in rows)
        gains.append(cross / reference_energy / steering[ch])
        coherences.append(min(1.0, abs(cross)**2 / (reference_energy * energy)))
    return gains, coherences


def combine(gains):
    """Give each spatial calibration point equal weight, not its received power."""
    result = []
    for ch in range(CHANNELS):
        column = [g[ch] for g in gains]
        if any(abs(g) < 1e-15 or not math.isfinite(abs(g)) for g in column):
            raise ValueError('Unusable channel gain')
        phase = sum(g / abs(g) for g in column)
        if abs(phase) < 0.5 * len(column):
            raise InconsistentPoints('校准点的相位校正互相矛盾，不能生成统一校正')
        magnitude = math.exp(sum(math.log(abs(g)) for g in column) / len(column))
        result.append(cmath.rect(magnitude, cmath.phase(phase)))
    return result


def residual(frame, steering, correction):
    values = [y * c for y, c in zip(vector(frame), correction)]
    coefficient = sum(a.conjugate() * y for a, y in zip(steering, values)) / sum(abs(a)**2 for a in steering)
    return sum(abs(y - coefficient * a)**2 for y, a in zip(values, steering)) / sum(abs(y)**2 for y in values)


def solve(session):
    try:
        return _solve(session)
    except InconsistentPoints as error:
        return {'accepted': False, 'reasons': [str(error)], 'correction': [],
                'correction_gain_db': [], 'correction_phase_deg': [], 'points': [], 'limits': dict(LIMITS)}


def _solve(session):
    settings, geometry = session['settings'], session['geometry']
    points = session['points']
    if len(points) != 3 or len({tuple(p['xyz_m']) for p in points}) != 3:
        raise ValueError('Need three distinct measured calibration positions')
    estimates, responses, metrics = [], [], []
    reasons = []
    all_bins = []
    for point in points:
        frames = point['frames']
        batches = sorted({f['batch'] for f in frames})
        if len(batches) < 4 or batches != list(range(len(batches))):
            raise ValueError('Each point needs at least four complete batches')
        if any(sorted(f['round'] for f in frames if f['batch'] == b) != [0, 1, 2, 3] for b in batches):
            raise ValueError('Each batch must contain four distinct rounds')
        train = [f for f in frames if f['batch'] % 2 == 0]
        steering = response(geometry, point['xyz_m'], settings['rf_hz'], settings['phase_sign'])
        gain, coherence = estimate(train, steering)
        estimates.append(gain)
        responses.append(steering)
        metrics.append({'name': point['name'], 'coherence': coherence})
        if min(coherence) < LIMITS['coherence']:
            reasons.append('%s: 跨帧相干度不足（本振、信号或环境不稳定）' % point['name'])
        for f in frames:
            all_bins.append(f['bin'])
            if min(f['peak_to_local_floor_db']) < LIMITS['peak_to_local_floor_db'] or min(f['tone_band_fraction']) < LIMITS['tone_band_fraction']:
                reasons.append('%s: 存在不合格的单音采样帧' % point['name'])
    if max(all_bins) - min(all_bins) > 1:
        reasons.append('校准期间主频点变化超过一个 FFT 格；请检查信号源/本振')
    common = combine(estimates)
    correction = [1 / g for g in common]
    for index, (point, steering, gains) in enumerate(zip(points, responses, estimates)):
        phase_error = [abs(math.degrees(cmath.phase(g / h))) for g, h in zip(gains, common)]
        gain_error = [abs(20 * math.log10(abs(g / h))) for g, h in zip(gains, common)]
        heldout = [f for f in point['frames'] if f['batch'] % 2 == 1]
        # Leave this entire position out: its multipath must not train its own check.
        loo = [1 / g for g in combine([g for j, g in enumerate(estimates) if j != index])]
        validation = [residual(f, steering, correction) for f in heldout]
        cross_position = [residual(f, steering, loo) for f in heldout]
        before = [residual(f, steering, [complex(*v) for v in session['old_correction']]) for f in heldout]
        metrics[index].update(phase_difference_deg=phase_error, gain_difference_db=gain_error,
                              heldout_before_residual=statistics.mean(before),
                              heldout_after_residual=statistics.mean(validation),
                              heldout_max_residual=max(validation), leave_position_out_max_residual=max(cross_position))
        if max(phase_error) > LIMITS['point_phase_difference_deg'] or max(gain_error) > LIMITS['point_gain_difference_db']:
            reasons.append('%s: 三点幅相不一致，可能有位置误差、多径或天线方向图差异' % point['name'])
        if max(validation + cross_position) > LIMITS['validation_residual']:
            reasons.append('%s: 独立批次/留一位置验证残差超限' % point['name'])
    correction_db = [20 * math.log10(abs(c)) for c in correction]
    if max(abs(v) for v in correction_db) > LIMITS['maximum_correction_db']:
        reasons.append('相对增益校正超过 18 dB，应先检查接线及接收链路')
    return {'accepted': not reasons, 'reasons': reasons, 'reference_channel': 0,
            'correction': [[c.real, c.imag] for c in correction],
            'correction_gain_db': correction_db,
            'correction_phase_deg': [math.degrees(cmath.phase(c)) for c in correction],
            'points': metrics, 'limits': dict(LIMITS),
            'method': 'known_spherical_source_relative_complex_gain; heldout_batches_and_positions',
            'scope': 'single_RF_relative_calibration_not_absolute_power_or_position_accuracy'}
