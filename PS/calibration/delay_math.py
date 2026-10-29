"""Two-tone RF-referenced phase calibration, with explicit delay ambiguity.

The measured secant includes antennas, mixers and IF electronics. It is not an
identified ADC sample delay or a broadband group-delay model.
"""
import cmath
import math

from calibration_math import CHANNELS, LIGHT_SPEED, LIMITS, vector

RF_FREQUENCIES = (425e6, 445e6)
DELAY_LIMITS = {'position_phase_deg': 10.0, 'heldout_phase_deg': 10.0,
                'position_delay_difference_ns': 2.0}


def tone_settings(base, rf_hz, lo_hz):
    if not math.isfinite(lo_hz) or lo_hz <= 0 or rf_hz == lo_hz:
        raise ValueError('本振必须为有效频率，且不能与校准单音相同')
    intermediate = abs(rf_hz - lo_hz)
    center = intermediate - base['dds_hz']
    # Capture the positive real-ADC IF image, without Nyquist folding.
    if not 0 < intermediate < base['sample_rate_hz'] / 2:
        raise ValueError('校准中频不在 ADC 第一奈奎斯特区')
    if not -base['sample_rate_hz'] / 8 < center - 50e3 < center + 50e3 < base['sample_rate_hz'] / 8:
        raise ValueError('校准中频不在当前 PL 基带范围内')
    return dict(base, rf_hz=rf_hz, lo_hz=lo_hz, if_hz=intermediate,
                rf_phase_conjugated=rf_hz < lo_hz,
                band_min_hz=center - 50e3, band_max_hz=center + 50e3)


def oriented_frames(frames, conjugated, channels):
    """Restore RF phase orientation for high-side LO; keep evidence unchanged."""
    return [dict(f, values=[[v.real, -v.imag if conjugated else v.imag]
                            for v in vector(f, channels)]) for f in frames]


def usable_channels(geometry, point):
    if len(point) != 3 or any(not math.isfinite(v) for v in point):
        raise ValueError('需要有效的 X Y Z 坐标')
    distances = [math.sqrt(sum((a - b)**2 for a, b in zip(point, ant))) for ant in geometry]
    channels = [ch for ch, d in enumerate(distances) if d >= LIMITS['minimum_range_m']]
    if len(channels) < 3:
        raise ValueError('排除过近通道后不足3路，需调整校准点')
    return channels


def shared_reference(geometry, points):
    masks = [usable_channels(geometry, p['xyz_m']) for p in points]
    common = set(range(CHANNELS)).intersection(*map(set, masks))
    if not common:
        raise ValueError('三点没有共同有效参考通道，需调整坐标')
    if any(sum(ch in mask for mask in masks) < 2 for ch in range(CHANNELS)):
        raise ValueError('每个通道至少需要两个有效位置，才能独立交叉验证')
    return min(common), masks


def phase_steering(geometry, point, rf, sign, reference, channels):
    distances = [math.sqrt(sum((a - b)**2 for a, b in zip(point, ant))) for ant in geometry]
    # Only use phase: no 1/r term at a coincident antenna and no epsilon distance.
    return [cmath.exp(-1j * sign * 2 * math.pi * rf / LIGHT_SPEED * (d - distances[reference]))
            if ch in channels else None for ch, d in enumerate(distances)]


def estimate_phase(frames, steering, reference, channels):
    rows = [vector(f, channels) for f in frames]
    reference_energy = sum(abs(row[reference])**2 for row in rows)
    gains, coherence = [None] * CHANNELS, [None] * CHANNELS
    for ch in channels:
        cross = sum(row[ch] * row[reference].conjugate() for row in rows)
        energy = sum(abs(row[ch])**2 for row in rows)
        value = cross / reference_energy / steering[ch]
        gains[ch] = units([value])[0]
        coherence[ch] = min(1.0, abs(cross)**2 / (reference_energy * energy))
    return gains, coherence


def units(values):
    if any(not math.isfinite(abs(v)) for v in values):
        raise ValueError('非有限通道相量')
    # A vanishing LS cross-spectrum is rejected by the coherence gate. Keep a
    # placeholder phase so its rejection report retains all point diagnostics.
    return [v / abs(v) if abs(v) > 1e-30 else 1 + 0j for v in values]


def circular_mean(columns):
    result = []
    for ch in range(CHANNELS):
        available = [row[ch] for row in columns if row[ch] is not None]
        if not available:
            result.append(None)
            continue
        value = sum(available)
        # Still return diagnostics for conflicting points; reject in solve().
        result.append(value / abs(value) if abs(value) > 1e-12 else 1 + 0j)
    return result


def phase_errors(actual, expected):
    return [abs(math.degrees(cmath.phase(a / e))) if a is not None and e is not None else None
            for a, e in zip(actual, expected)]


def principal_delays(low, high, separation):
    return [-cmath.phase(b / a) / (2 * math.pi * separation) * 1e9
            if a is not None and b is not None else None
            for a, b in zip(low, high)]


def delay_difference(a, b, period):
    return abs((a - b + period / 2) % period - period / 2)


def checked_frames(tone, channels):
    frames = tone['frames']
    batches = sorted({f['batch'] for f in frames})
    if len(batches) < 4 or len(batches) % 2 or batches != list(range(len(batches))):
        raise ValueError('每个位置/频率需要不少于4个、且为偶数的完整批次')
    for batch in batches:
        if sorted(f['round'] for f in frames if f['batch'] == batch) != [0, 1, 2, 3]:
            raise ValueError('每批需要四个不同采集轮次')
    for frame in frames:
        if 'quality_channels' in frame and frame['quality_channels'] != channels:
            raise ValueError('采集通道掩码与校准点几何不一致')
        if (len(frame['peak_to_local_floor_db']) != CHANNELS or
                len(frame['tone_band_fraction']) != CHANNELS or
                any(not math.isfinite(frame['peak_to_local_floor_db'][ch]) or
                    frame['peak_to_local_floor_db'][ch] < LIMITS['peak_to_local_floor_db'] for ch in channels) or
                any(not math.isfinite(frame['tone_band_fraction'][ch]) or
                    frame['tone_band_fraction'][ch] < LIMITS['tone_band_fraction'] for ch in channels)):
            raise ValueError('存在不合格的单音采样帧')
    return frames


def solve_delay(session):
    geometry, points = session['geometry'], session['points']
    frequencies = session['frequencies_hz']
    if frequencies != list(RF_FREQUENCIES):
        raise ValueError('本版本校准频率固定为425和445 MHz')
    if len(points) != 3 or [p['name'] for p in points] != ['L', 'O', 'R']:
        raise ValueError('需要 L、O、R 三个校准位置')
    if len({tuple(p['xyz_m']) for p in points}) != 3:
        raise ValueError('校准位置不能重合')
    reference, masks = shared_reference(geometry, points)
    target = session['settings']['rf_hz']
    if target != 445e6 or session['lo_hz'] >= target:
        raise ValueError('当前运行程序按445 MHz高侧RF测角；425 MHz只作辅助测量')
    estimates, details, reasons = [[], []], [], []
    all_bins = [[], []]
    heldouts = []
    for point, channels in zip(points, masks):
        if [t['settings']['rf_hz'] for t in point['tones']] != frequencies:
            raise ValueError('各位置必须包含425、445 MHz两次独立测量')
        point_details, point_heldout = [], []
        for fi, tone in enumerate(point['tones']):
            expected = tone_settings(session['settings'], frequencies[fi], session['lo_hz'])
            if tone['settings'] != expected:
                raise ValueError('频率、本振或采集设置不一致')
            raw_frames = checked_frames(tone, channels)
            frames = oriented_frames(raw_frames, expected['rf_phase_conjugated'], channels)
            steering = phase_steering(geometry, point['xyz_m'], frequencies[fi], expected['phase_sign'], reference, channels)
            train = [f for f in frames if f['batch'] % 2 == 0]
            phase, coherence = estimate_phase(train, steering, reference, channels)
            estimates[fi].append(phase)
            point_heldout.append((frames, steering))
            point_details.append({'rf_hz': frequencies[fi], 'coherence': coherence,
                                  'phase_reference_channel': reference,
                                  'channel_phase_deg': [math.degrees(cmath.phase(g)) if g is not None else None for g in phase]})
            if min(coherence[ch] for ch in channels) < LIMITS['coherence']:
                reasons.append('%s / %.0f MHz: 跨帧相干度不足' % (point['name'], frequencies[fi] / 1e6))
            all_bins[fi].extend(f['bin'] for f in raw_frames)
        details.append({'name': point['name'], 'tones': point_details, 'used_channels': channels,
                        'excluded_channels': [ch for ch in range(CHANNELS) if ch not in channels]})
        heldouts.append(point_heldout)
    common = [circular_mean(rows) for rows in estimates]
    separation = frequencies[1] - frequencies[0]
    period = 1e9 / separation
    delays = principal_delays(*common, separation)
    for fi, bins in enumerate(all_bins):
        if max(bins) - min(bins) > 1:
            reasons.append('%.0f MHz: 主频点漂移超过一个 FFT 格' % (frequencies[fi] / 1e6))
    for pi, point in enumerate(points):
        local = principal_delays(estimates[0][pi], estimates[1][pi], separation)
        channels = masks[pi]
        delta = [delay_difference(a, b, period) if a is not None else None for a, b in zip(local, delays)]
        details[pi].update(delay_principal_ns=local, delay_difference_ns=delta, delay_reference_channel=reference)
        if max(delta[ch] for ch in channels) > DELAY_LIMITS['position_delay_difference_ns']:
            reasons.append('%s: 跨位置双频延迟不一致' % point['name'])
        for fi in range(2):
            frames, steering = heldouts[pi][fi]
            error = phase_errors(estimates[fi][pi], common[fi])
            loo = circular_mean([row for j, row in enumerate(estimates[fi]) if j != pi])
            validation, cross_position = [], []
            for frame in frames:
                if frame['batch'] % 2:
                    values = vector(frame, channels)
                    observed = [units([v / values[reference] / a])[0] if ch in channels else None
                                for ch, (v, a) in enumerate(zip(values, steering))]
                    validation.extend(phase_errors(observed, common[fi])[ch] for ch in channels)
                    cross_position.extend(phase_errors(observed, loo)[ch] for ch in channels)
            details[pi]['tones'][fi].update(position_phase_error_deg=error,
                heldout_max_phase_error_deg=max(validation),
                leave_position_out_max_phase_error_deg=max(cross_position))
            if max(error[ch] for ch in channels) > DELAY_LIMITS['position_phase_deg']:
                reasons.append('%s / %.0f MHz: 跨位置相位不一致' % (point['name'], frequencies[fi] / 1e6))
            if max(validation + cross_position) > DELAY_LIMITS['heldout_phase_deg']:
                reasons.append('%s / %.0f MHz: 独立批次/留一位置相位验证未通过' % (point['name'], frequencies[fi] / 1e6))
    # Apply the measured phase at the exact operating RF, never a delay extrapolation.
    # Output remains CH0-referenced even when an endpoint excludes CH0 locally.
    common = [[value / row[0] for value in row] for row in common]
    delays = principal_delays(*common, separation)
    ti = frequencies.index(target)
    target_raw = common[ti]
    if target < session['lo_hz']:
        target_raw = [v.conjugate() for v in target_raw]
    old = [complex(*v) for v in session['old_correction']]
    if len(old) != CHANNELS or any(not math.isfinite(abs(v)) or abs(v) == 0 for v in old):
        raise ValueError('原幅度校正无效')
    correction = [abs(g) / phase for g, phase in zip(old, target_raw)]
    return {'accepted': not reasons, 'reasons': reasons, 'reference_channel': 0,
            'measurement_reference_channel': reference,
            'minimum_used_range_m': LIMITS['minimum_range_m'],
            'method': 'two_tone_rf_oriented_phase_secant', 'target_rf_hz': target,
            'frequencies_hz': frequencies, 'lo_hz': session['lo_hz'],
            'delay_kind': 'effective_relative_delay_principal_not_identified_physical_delay',
            'delay_principal_ns': delays, 'delay_alias_period_ns': period,
            'delay_unwrapped': False, 'physical_delay_identified': False,
            'delay_used_for_runtime_compensation': False,
            'rf_oriented_channel_phase_deg': [[math.degrees(cmath.phase(g)) for g in row] for row in common],
            'correction': [[c.real, c.imag] for c in correction],
            'correction_gain_db': [20 * math.log10(abs(c)) for c in correction],
            'correction_phase_deg': [math.degrees(cmath.phase(c)) for c in correction],
            'amplitude_policy': 'preserve_existing_magnitudes', 'points': details,
            'limits': dict(DELAY_LIMITS, coherence=LIMITS['coherence']),
            'scope': 'fixed_LO_two_RF_phase_measurement; no_ADC_delay_or_broadband_alignment_claim'}
