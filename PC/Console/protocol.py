"""AOA1 遥测契约：长度边界、数值校验及候选交点；只用 Python 标准库。"""
import base64
import json
import math
import struct
from joint_protocol import validate_joint, validate_processing, validate_raw_validation

MAGIC = b"AOA1"
MAX_PACKET = 1024 * 1024
CLASSES = ["FM", "AM", "CW", "noise", "OOK"]


def encode(packet):
    body = json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if not 0 < len(body) <= MAX_PACKET:
        raise ValueError("packet size")
    return MAGIC + struct.pack("!I", len(body)) + body


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(label)
    return value


def array(value, size, label):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(label)
    return value


def blob(value, size):
    if not isinstance(value, str) or len(value) != (size + 2) // 3 * 4:
        raise ValueError("invalid base64 size")
    result = base64.b64decode(value, validate=True)
    if len(result) != size:
        raise ValueError("invalid decoded size")
    return result


def validate_observation(value, packet):
    """[合成IQ观测新增] 全4096点载荷；禁用时不允许夹带相应数据。"""
    o = mapping(value, 'observation')
    if o.get('source') != 'PL_PHA_beamformed_IQ' or o.get('run_id') != packet['run_id']:
        raise ValueError('observation source/batch')
    for name in ('valid', 'waveform_enabled', 'spectrum_enabled'):
        if type(o.get(name)) is not bool:
            raise ValueError('observation boolean')
    fs = o.get('sample_rate_hz')
    if (o.get('sample_count') != 4096 or o.get('iq_scale') != 1048576
            or not finite(fs) or fs <= 0 or fs != packet['baseband_sample_rate_hz']
            or type(o.get('round_index')) is not int or o['round_index'] not in range(4)):
        raise ValueError('observation shape/rate')
    if not isinstance(o.get('error'), str) or len(o['error']) > 512:
        raise ValueError('observation error')
    if o['valid'] and o['waveform_enabled']:
        blob(o.get('wave_iq_s32le'), 4096*8)
    elif 'wave_iq_s32le' in o:
        raise ValueError('unexpected observation waveform')
    if o['valid'] and o['spectrum_enabled']:
        s = mapping(o.get('spectrum'), 'observation spectrum')
        if (s.get('bins') != 4096 or s.get('reference') != 'per_frame_peak'
                or s.get('window') != 'periodic_Hann' or s.get('dc_removed') is not False
                or s.get('frequency_start_hz') != -fs/2 or s.get('frequency_step_hz') != fs/4096
                or s.get('db_min') != -120 or s.get('db_max') != 0):
            raise ValueError('observation spectrum axes')
        # Legacy packets omit the contract; never label them as startup-filtered.
        fields = ('preprocessing', 'startup_samples', 'retained_samples')
        if any(name in s for name in fields):
            if (s.get('preprocessing') != 'zero64_hann4032'
                    or type(s.get('startup_samples')) is not int or s['startup_samples'] != 64
                    or type(s.get('retained_samples')) is not int or s['retained_samples'] != 4032):
                raise ValueError('observation spectrum preprocessing')
        levels = struct.unpack('<4096f', blob(s.get('db_f32le'), 4096*4))
        if not all(math.isfinite(v) and -120.001 <= v <= .001 for v in levels):
            raise ValueError('observation spectrum levels')
        if s.get('peak_frequency_hz') is not None and not (finite(s['peak_frequency_hz']) and -fs/2 <= s['peak_frequency_hz'] < fs/2):
            raise ValueError('observation spectrum peak')
    elif 'spectrum' in o:
        raise ValueError('unexpected observation spectrum')


def validate_preprocessing(packet):
    """Check the ML contract without applying it again or inferring old payload formats."""
    value = packet.get('preprocessing')
    if value is None:
        if packet.get('capture_mode') == 'hardware' and packet.get('pl_version') == 0x00010001:
            raise ValueError('Windowed PL requires preprocessing metadata')
        return
    p = mapping(value, 'preprocessing')
    for name in ('adc_dc_applied', 'ml_applied'):
        if type(p.get(name)) is not bool:
            raise ValueError('preprocessing boolean')
    removed = array(p.get('adc_dc_removed_codes'), 6, 'ADC DC offsets')
    if any(type(v) is not int or not -65535 <= v <= 65535 for v in removed):
        raise ValueError('ADC DC offsets')
    startup, retained = p.get('ml_startup_samples'), p.get('ml_retained_samples')
    if (type(startup) is not int or not 0 <= startup < 4096
            or type(retained) is not int or retained != 4096 - startup):
        raise ValueError('ML sample counts')
    if p.get('ml_window') not in ('none', 'periodic_hann'):
        raise ValueError('ML window')
    if not finite(p.get('ml_processing_ms')) or p['ml_processing_ms'] < 0:
        raise ValueError('ML processing time')
    backend = p.get('ml_backend')
    payload = p.get('fft_payload_preprocessing')
    if backend is not None and backend not in ('none', 'ps', 'pl'):
        raise ValueError('ML backend')
    if payload is not None and payload not in ('unknown', 'raw', 'pl-hann64'):
        raise ValueError('FFT payload preprocessing')
    if not p['ml_applied']:
        if (packet.get('capture_mode') == 'hardware' and packet.get('pl_version') == 0x00010001
                and packet.get('status') == 'ok'):
            raise ValueError('Successful windowed PL result cannot skip preprocessing')
        return  # A failed capture may report the intended format before any ML processing.
    if backend == 'none':
        raise ValueError('ML applied without a backend')
    if backend == 'pl' and (payload != 'pl-hann64' or startup != 64 or p['ml_window'] != 'periodic_hann'):
        raise ValueError('PL ML window contract')
    if backend == 'ps' and payload not in (None, 'raw'):
        raise ValueError('PS cannot window an already windowed FFT')
    if (packet.get('capture_mode') == 'hardware' and packet.get('pl_version') == 0x00010001
            and (backend != 'pl' or payload != 'pl-hann64')):
        raise ValueError('Windowed PL must use the PL preprocessing backend')


def validate_v12(value, packet=None):
    """Validate the board result; never fit angles or supply a PC selection."""
    v = mapping(value, 'v12')
    if v.get('version') != '1.2':
        raise ValueError('v12 version')
    scan, joint, loc = (mapping(v.get(k), 'v12 '+k) for k in ('scan', 'joint', 'localization'))

    def number(obj, key, nullable=True):
        val = obj.get(key)
        if val is None and nullable:
            return
        if not finite(val):
            raise ValueError('v12 '+key)

    if 'valid' in scan and type(scan['valid']) is not bool:
        raise ValueError('v12 scan valid')
    for key in ('angle_deg', 'coarse_angle_deg', 'local_angle_deg', 'secondary_angle_deg'):
        number(scan, key)
    angles, levels = scan.get('angles_deg'), scan.get('relative_db')
    if (not isinstance(angles, list) or not isinstance(levels, list)
            or len(angles) != len(levels) or len(angles) == 1 or len(angles) > 2001
            or any(not finite(a) or not -90 < a < 90 for a in angles)
            or any(a >= b for a, b in zip(angles, angles[1:]))
            or any(not finite(d) or d > 1e-6 for d in levels)):
        raise ValueError('v12 scan curve')
    # POSITION omits full-array display arrays after board-side computation to
    # leave room for its two original triad heatmaps inside the same AOA1 cap.
    position = (packet or {}).get('processing_mode', 'position') == 'position'
    if scan.get('valid') and ((len(angles) < 2 and not position) or not finite(scan.get('angle_deg'))):
        raise ValueError('v12 valid scan')
    if type(joint.get('valid')) is not bool:
        raise ValueError('v12 joint valid')
    k, index, paths = joint.get('model_paths'), joint.get('path_index'), joint.get('angles')
    if (type(k) is not int or k not in (0, 1, 2, 3)
            or not isinstance(paths, list) or len(paths) not in (0, k)
            or any(not finite(a) or not -90 < a < 90 for a in paths)
            or any(a >= b for a, b in zip(paths, paths[1:]))
            or (index is not None and (type(index) is not int or not 0 <= index < len(paths)))):
        raise ValueError('v12 joint paths')
    if joint['valid'] and (k == 0 or index is None or not finite(joint.get('angle_deg'))):
        raise ValueError('v12 joint decision')
    for key in ('angle_deg', 'residual_ratio', 'score', 'main_match'):
        number(joint, key)
    for key in ('residual_ratio', 'score', 'main_match'):
        if joint.get(key) is not None and not 0 <= joint[key] <= 1+1e-8:
            raise ValueError('v12 joint '+key)
    if joint['valid'] and abs(joint['angle_deg']-paths[index]) > 1e-8:
        raise ValueError('v12 joint selected angle')
    old_joint = (packet or {}).get('joint')
    if old_joint and joint['valid'] and (old_joint.get('path_count') != k
            or [p.get('angle_deg') for p in old_joint.get('paths', [])] != paths):
        raise ValueError('v12 joint display model')
    if 'pool_counts' in joint:
        if any(type(n) is not int or not 0 <= n <= 30
               for n in array(joint['pool_counts'], 3, 'v12 pool counts')):
            raise ValueError('v12 pool counts')
    if joint.get('top30_rank') is not None and (type(joint['top30_rank']) is not int or not 1 <= joint['top30_rank'] <= 30):
        raise ValueError('v12 top30 rank')
    if (joint['valid'] and 'pool_counts' in joint and joint.get('top30_rank') is not None
            and joint['top30_rank'] > joint['pool_counts'][k-1]):
        raise ValueError('v12 rank outside selected model pool')
    if 'secondary' in joint:
        second = mapping(joint['secondary'], 'v12 secondary')
        if second.get('display_only') is not True:
            raise ValueError('v12 secondary display only')
        for key in ('angle_deg', 'ml_angle_deg', 'error_deg', 'score'):
            number(second, key)
        if k == 1 and any(second.get(key) is not None for key in ('ml_angle_deg', 'error_deg', 'score')):
            raise ValueError('v12 single-path secondary ML diagnostics')
    number(loc, 'reference_angle_deg')
    energy_selection = loc.get('selection_method') == 'triad_ml_energy'
    if energy_selection and packet is not None and packet.get('processing_mode') != 'position':
        raise ValueError('v12 triad energy requires position mode')
    if 'selection_method' in loc:
        if loc['selection_method'] not in ('direction_residual_angle', 'triad_ml_energy'):
            raise ValueError('v12 localization selection method')
        if not energy_selection:
            number(loc, 'residual_weight', nullable=False)
            number(loc, 'angle_sigma_deg', nullable=False)
            if not 0 <= loc['residual_weight'] <= 1 or loc['angle_sigma_deg'] <= 0:
                raise ValueError('v12 localization scoring parameters')
    cs = loc.get('candidates')
    if not isinstance(cs, list) or len(cs) not in (0, 4):
        raise ValueError('v12 four candidates')
    seen = set()
    for c in cs:
        mapping(c, 'v12 candidate')
        cid = c.get('id')
        if (cid not in ('C1', 'C2', 'C3', 'C4') or cid in seen
                or type(c.get('valid')) is not bool or not isinstance(c.get('status'), str)
                or len(c['status']) > 128
                or any(type(i) is not int or i not in (0, 1)
                       for i in array(c.get('paths'), 2, 'v12 candidate paths'))):
            raise ValueError('v12 candidate identity')
        seen.add(cid)
        for key in ('x', 'y', 'bearing_deg', 'angle_error_deg', 'crossing_deg', 'geometry_condition'):
            number(c, key)
        required = ('x', 'y', 'bearing_deg', 'angle_error_deg') if joint['valid'] and not energy_selection else ('x', 'y', 'bearing_deg')
        if c['valid'] and not all(finite(c.get(key)) for key in required):
            raise ValueError('v12 valid candidate coordinates')
        if energy_selection and 'energy_share' not in c:
            raise ValueError('v12 candidate energy_share')
        if 'energy_share' in c:
            number(c, 'energy_share', nullable=not (energy_selection and c['valid']))
            if c['energy_share'] is not None and not 0 <= c['energy_share'] <= 1:
                raise ValueError('v12 candidate energy_share')
        for key in ('direction_residual_ratio', 'angle_cost', 'selection_cost'):
            if key in c:
                number(c, key)
                if c[key] is not None and not 0 <= c[key] <= 1+1e-8:
                    raise ValueError('v12 candidate '+key)
        if 'direction_residuals' in c:
            for residual in array(c['direction_residuals'], 2, 'v12 direction residuals'):
                if residual is not None and (not finite(residual) or not 0 <= residual <= 1+1e-8):
                    raise ValueError('v12 direction residual')
    selected = loc.get('selected_id')
    if energy_selection:
        # Shares cover all four path pairs, including invalid intersections.
        # Check the declared board decision without deriving powers or a fix.
        if len(cs) == 4 and all(c['energy_share'] is not None for c in cs):
            if abs(sum(c['energy_share'] for c in cs)-1) > 1e-5:
                raise ValueError('v12 candidate energy sum')
        eligible = sorted((c for c in cs if c['valid']), key=lambda c: c['id'])
        best = max(eligible, key=lambda c: c['energy_share'], default=None)
        if selected is not None and (best is None or selected != best['id']):
            raise ValueError('v12 selected energy candidate')
    elif selected is not None and (not joint['valid'] or not finite(loc.get('reference_angle_deg'))
            or not any(c['id'] == selected and c['valid'] for c in cs)):
        raise ValueError('v12 selected candidate')


def validate(p):
    if not isinstance(p, dict) or p.get("schema") != 1:
        raise ValueError("schema")
    transport = mapping(p.get("transport", {}), "transport")
    for value in transport.values():
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 2**63:
            raise ValueError("transport counter")
    if p.get("source") not in ("aoa", "npu") or p.get("type") not in ("aoa", "npu", "heartbeat", "service_error"):
        raise ValueError("source/type")
    for name, limit in (("run_id", 256), ("session", 256)):
        if not isinstance(p.get(name), str) or len(p[name]) > limit:
            raise ValueError(name)
    if not isinstance(p.get("seq"), int) or not 0 < p["seq"] < 2**63:
        raise ValueError("seq")
    if p["type"] in ("aoa", "npu") and (p["source"] != p["type"] or not p["run_id"]):
        raise ValueError("batch identity")
    if "beam" in p:
        b = mapping(p["beam"], "beam")
        if p["source"] != "aoa" or b.get("mode") not in ("equal", "manual", "follow", "auto"):
            raise ValueError("beam mode/source")
        if b.get("status") not in ("idle", "pending", "applied", "tracking", "holding", "paused", "error", "rejected"):
            raise ValueError("beam status")
        for key in ("available", "tracking", "registers_verified", "hardware_response_verified"):
            if not isinstance(b.get(key), bool): raise ValueError("beam boolean")
        if b.get("command_status") not in ("idle", "pending", "applied", "tracking", "holding", "paused", "error", "rejected"):
            raise ValueError("beam command status")
        for key in ("command_id", "applied_run_id", "error", "command_error"):
            if not isinstance(b.get(key), str) or len(b[key]) > 512: raise ValueError("beam text")
        for key in ("pl_version", "required_pl_version"):
            if type(b.get(key)) is not int or not 0 <= b[key] <= 0xffffffff: raise ValueError("beam PL version")
        if not finite(b.get("angle_deg")) or abs(b["angle_deg"]) > 180: raise ValueError("beam angle")
        if not all(finite(v) and abs(v) <= math.pi+1e-8 for v in array(b.get("phase_rad"), 6, "beam phases")):
            raise ValueError("beam phase")
        if not all(type(v) is int and v in (0, 1) for v in array(b.get("paths"), 2, "beam paths")):
            raise ValueError("beam paths")
        if "auto_supported" in b and type(b["auto_supported"]) is not bool: raise ValueError("beam auto capability")
        if "acquisition" in b:
            ac=mapping(b["acquisition"],"acquisition")
            if ac.get("mode") not in ("locate","identify") or ac.get("applied_mode") not in ("","locate","identify"):
                raise ValueError("acquisition mode")
            if ac.get("command_status") not in ("idle","pending","applied","error","rejected") or type(ac.get("available")) is not bool:
                raise ValueError("acquisition status")
            for key in ("command_id","error"):
                if not isinstance(ac.get(key),str) or len(ac[key])>512:raise ValueError("acquisition text")
        if "processing" in b:
            validate_processing(b["processing"])
        if "observation" in b:
            ob = mapping(b['observation'], 'observation control')
            if any(type(ob.get(k)) is not bool for k in ('available', 'waveform_enabled', 'spectrum_enabled')):
                raise ValueError('observation control switches')
            if ob.get('command_status') not in ('idle', 'pending', 'applied', 'rejected'):
                raise ValueError('observation control status')
            if any(not isinstance(ob.get(k), str) or len(ob[k]) > 512 for k in ('command_id', 'error')):
                raise ValueError('observation control text')
        if 'archive' in b:
            archive = mapping(b['archive'], 'archive control')
            if any(type(archive.get(k)) is not bool for k in ('available', 'enabled')):
                raise ValueError('archive control switches')
            if archive.get('command_status') not in ('idle', 'pending', 'applied', 'rejected'):
                raise ValueError('archive control status')
            if any(not isinstance(archive.get(k), str) or len(archive[k]) > 512 for k in ('command_id', 'error')):
                raise ValueError('archive control text')
            pending=archive.get('pending_batches')
            if pending is not None and (type(pending) is not int or pending<0):
                raise ValueError('archive pending batch count')
        if b.get("target") is not None:
            target = mapping(b["target"], "beam target")
            if target.get("kind") == "angle":
                if not finite(target.get("angle_deg")) or not -90 < target["angle_deg"] < 90 or type(target.get("path_id")) is not int or target["path_id"] not in range(3):
                    raise ValueError("joint beam target")
                if "x" in target or "y" in target:
                    raise ValueError("angle target cannot contain range/XY")
            elif not all(finite(target.get(k)) and abs(target[k]) < 1e9 for k in ("x", "y")):
                raise ValueError("beam target coordinate")
            if not isinstance(target.get("source_run_id"), str) or len(target["source_run_id"])>256:
                raise ValueError("beam target batch")
            if target.get("energy_share") is not None and not (finite(target["energy_share"]) and 0<=target["energy_share"]<=1):
                raise ValueError("beam target score")
    if p["type"] == "aoa":
        validate_preprocessing(p)
        centres = array(p.get("centres"), 2, "centres")
        ants = array(p.get("antennas"), 6, "antennas")
        for pair in centres + ants:
            if not all(finite(v) and abs(v) < 1e9 for v in array(pair, 2, "coordinate")):
                raise ValueError("coordinate")
        for key in ("sample_rate_hz", "baseband_sample_rate_hz", "rf_hz"):
            if not finite(p.get(key)) or p[key] <= 0:
                raise ValueError(key)
        search = mapping(p.get("search"), "search")
        if not isinstance(p.get("complete"), bool):
            raise ValueError("complete")
        for key, default, low, high in (("min_crossing_angle_deg", 1, 0, 90), ("max_bearing_residual", .1, -1e-9, 1.000001)):
            value = p.get(key, default)
            if not finite(value) or not low < value < high:
                raise ValueError(key)
        if not isinstance(search.get("count"), int) or not 2 <= search["count"] <= 2001:
            raise ValueError("search count")
        if not finite(search.get("start")) or not finite(search.get("step")) or search["step"] <= 0:
            raise ValueError("search range")
        end = search["start"] + (search["count"]-1)*search["step"]
        if not -90 < search["start"] < end < 90:
            raise ValueError("search coverage")
        groups = p.get("groups")
        if not isinstance(groups, list) or len(groups) > 2:
            raise ValueError("groups")
        seen = set()
        for g in groups:
            mapping(g, "group")
            if "raw_validation" in g:
                validate_raw_validation(g["raw_validation"])
            ratio = g.get("residual_ratio")
            if ratio is not None and (not finite(ratio) or ratio < 0):
                raise ValueError("residual_ratio")
            gid = g.get("id")
            if gid not in (0, 1) or gid in seen:
                raise ValueError("group id")
            seen.add(gid)
            channels = array(g.get("channels"), 3, "channels")
            if len(set(channels)) != 3 or any(not isinstance(c, int) or c not in range(6) for c in channels):
                raise ValueError("channels")
            paths = g.get("paths")
            if not isinstance(paths, list) or len(paths) > 2:
                raise ValueError("paths")
            for i, path in enumerate(paths):
                mapping(path, "path")
                if path.get("id") != i or not finite(path.get("angle_deg")) or not -90 < path["angle_deg"] < 90:
                    raise ValueError("path angle/id")
                power = path.get("relative_power_db")
                if power is not None and (not finite(power) or power > 1e-6):
                    raise ValueError("relative power")
            if "grid" in g:
                grid = mapping(g["grid"], "grid")
                angles = grid.get("angles_deg")
                if not isinstance(angles, list) or not 2 <= len(angles) <= 161 or not all(finite(v) for v in angles):
                    raise ValueError("grid angles")
                if any(a >= b for a, b in zip(angles, angles[1:])) or grid.get("order") != "theta1,theta2":
                    raise ValueError("grid order")
                blob(grid.get("score_u8"), len(angles)**2)
                mask = blob(grid.get("invalid_u8"), len(angles)**2)
                if any(v > 1 for v in mask):
                    raise ValueError("grid mask")
        if 'v12' in p:
            validate_v12(p['v12'], p)
        validate_joint(p)
        if 'observation' in p:
            validate_observation(p['observation'], p)
    if p["type"] == "npu":
        if p.get("backend") not in ("npu", "host", "preprocess"):
            raise ValueError("backend")
        c = p.get("classification")
        if c is not None:
            mapping(c, "classification")
            if c.get("classes") not in (CLASSES, CLASSES + ["PM"]):
                raise ValueError("classes")
            probs = array(c.get("probabilities"), len(c["classes"]), "probabilities")
            if not all(finite(v) and 0 <= v <= 1 for v in probs) or abs(sum(probs)-1) > 1e-4:
                raise ValueError("probability sum")
        if "signal" in p:
            s = mapping(p["signal"], "signal")
            if s.get("columns") != 640 or s.get("rows") != 64 or s.get("round_index") != 3:
                raise ValueError("signal shape")
            blob(s.get("spectrum_u8"), 640)
            blob(s.get("tf_u8"), 64*640)
            wave = s.get("wave_iq")
            if not isinstance(wave, list) or not 2 <= len(wave) <= 512:
                raise ValueError("wave shape")
            for pair in wave:
                if not all(finite(v) for v in array(pair, 2, "IQ")):
                    raise ValueError("IQ finite")
            for key in ("sample_rate_hz", "freq_min_hz", "freq_max_hz", "db_min", "db_max", "wave_start_sample"):
                if not finite(s.get(key)):
                    raise ValueError(key)
            if s["sample_rate_hz"] <= 0 or s["freq_min_hz"] >= s["freq_max_hz"] or s["db_min"] >= s["db_max"]:
                raise ValueError("signal axes")
    return p


def decode(body):
    def bad_number(_):
        raise ValueError("nonfinite JSON")
    return validate(json.loads(body, parse_constant=bad_number))


def path_energy_shares(paths):
    """[上位机新增] 组内相对功率转能量份额；未知功率不能按零能量处理。"""
    if len(paths) != 2 or not all(finite(p.get("relative_power_db")) for p in paths):
        return None
    # dB 已是 20log10(幅度比)，恢复能量必须除以 10，不是 20。
    # 减去本组最大值以避免极小值同时下溢；只比较组内比例。
    peak = max(p["relative_power_db"] for p in paths)
    energy = [10 ** ((p["relative_power_db"] - peak) / 10) for p in paths]
    total = sum(energy)
    return {p["id"]: value / total for p, value in zip(paths, energy)}


def best_candidate(cs):
    """[自动跟踪新增] 保留所有组合的原始能量份额，仅在合格交点中排名。"""
    eligible = [c for c in cs if c["status"] == "candidate" and
                finite(c["x"]) and finite(c["y"]) and finite(c["energy_share"])]
    return max(eligible, key=lambda c: c["energy_share"], default=None)


def automatic_candidate(aoa, cs=None):
    """Use the board-selected V1.2 ID; retain energy ranking for legacy packets."""
    cs = candidates(aoa) if cs is None else cs
    v12 = (aoa or {}).get('v12', {})
    if v12.get('version') == '1.2':
        selected = v12['localization'].get('selected_id')
        return next((c for c in cs if c['id'] == selected and c['valid']), None)
    return best_candidate(cs)


def candidates(aoa):
    """四种路径组合。返回原因；拒绝近似平行、后向、非有限交点。"""
    if aoa and aoa.get('v12', {}).get('version') == '1.2':
        # Coordinates, geometry decisions and final choice were computed on PS.
        return [dict(c) for c in aoa['v12']['localization']['candidates']]
    if not aoa or not aoa.get("complete") or aoa.get("status") != "ok":
        return []
    groups = {g["id"]: g for g in aoa["groups"]}
    if not all(i in groups and groups[i].get("has_best") for i in (0, 1)):
        return []
    shares = [path_energy_shares(groups[i]["paths"]) for i in (0, 1)]
    a, b = aoa["centres"]
    dx, dy = b[0]-a[0], b[1]-a[1]
    out = []
    for p in groups[0]["paths"]:
        for q in groups[1]["paths"]:
            item = {"id": f"C{len(out)+1}", "paths": [p["id"], q["id"]], "x": None, "y": None,
                    "status": "candidate", "powers": [p["relative_power_db"], q["relative_power_db"]]}
            # [上位机新增] 四种路径组合的能量权重，总和为 1；不是校准的直达概率。
            # 几何/拟合质量仍独立报告，不因筛掉无效交点而把剩余权重抬高。
            item["energy_share"] = (shares[0][p["id"]] * shares[1][q["id"]]
                                    if all(s is not None for s in shares) else None)
            u = [math.sin(math.radians(p["angle_deg"])), math.cos(math.radians(p["angle_deg"]))]
            v = [math.sin(math.radians(q["angle_deg"])), math.cos(math.radians(q["angle_deg"]))]
            det = u[0]*v[1]-u[1]*v[0]
            crossing = math.degrees(math.atan2(abs(det), abs(u[0]*v[0]+u[1]*v[1])))
            item["crossing_deg"] = crossing
            if math.hypot(dx, dy) <= 1e-9:
                item["status"] = "coincident_centres"
            elif crossing < aoa.get("min_crossing_angle_deg", 1) or abs(det) < 1e-12:
                item["status"] = "near_parallel"
            else:
                t, s = (dx*v[1]-dy*v[0])/det, (dx*u[1]-dy*u[0])/det
                x, y = a[0]+t*u[0], a[1]+t*u[1]
                if t <= 0 or s <= 0 or y < 0:
                    item["status"] = "behind_station"
                elif not all(math.isfinite(z) for z in (x, y)):
                    item["status"] = "nonfinite"
                else:
                    item.update(x=x, y=y)
                    if not p.get("active") or not q.get("active"):
                        item["status"] = "weak_path"
                    elif any(g.get("ambiguous") for g in groups.values()):
                        item["status"] = "ambiguous_fit"
                    elif any((g.get("residual_ratio") or 0) > aoa.get("max_bearing_residual", .1) for g in groups.values()):
                        item["status"] = "poor_fit"
                    elif any("raw_validation" in g and (not g["raw_validation"]["passed"] or
                             max(g["raw_validation"]["raw_residual_ratio"],g["raw_validation"]["latest_residual_ratio"]) >
                             aoa.get("max_bearing_residual", .1)) for g in groups.values()):
                        item["status"] = "poor_fit"
            out.append(item)
    return out
