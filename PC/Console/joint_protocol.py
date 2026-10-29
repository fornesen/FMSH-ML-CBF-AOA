"""[六天线测角新增] 联合结果/控制校验；上位机只校验和展示，不运行 ML。"""
import math
import base64

PROCESSING_MODES = ("position", "angle6", "scan6")
SCAN_MODEL = "phase_only_beam_power_scan"
SCAN_SOURCE = "pl_independent_fft_same_batch"
SCAN_DIRECTION_METHOD = "four_frame_beam_power_average"


def finite(v):
    try:
        return type(v) in (int, float) and math.isfinite(v)
    except OverflowError:
        return False


def validate_processing(p):
    if not isinstance(p, dict) or type(p.get("available")) is not bool:
        raise ValueError("processing capability")
    if p.get("mode") not in PROCESSING_MODES or p.get("applied_mode") not in ("", *PROCESSING_MODES):
        raise ValueError("processing mode")
    # [快速扫描新增] 新版能力列表决定页面是否出现 scan6；旧版没有该字段时只保留原两种模式。
    if "supported_modes" in p:
        supported = p["supported_modes"]
        if (not isinstance(supported, list) or not supported or len(supported) > len(PROCESSING_MODES)
                or any(mode not in PROCESSING_MODES for mode in supported)
                or len(set(supported)) != len(supported)):
            raise ValueError("processing supported modes")
        if p["mode"] not in supported or (p["applied_mode"] and p["applied_mode"] not in supported):
            raise ValueError("processing mode capability")
    elif p["mode"] == "scan6" or p.get("applied_mode") == "scan6":
        raise ValueError("scan6 capability")
    for key, allowed in (("joint_paths", (1, 2, 3)), ("applied_joint_paths", (0, 1, 2, 3))):
        if type(p.get(key)) is not int or p[key] not in allowed:
            raise ValueError(key)
    # 扫描模式不用阶数，但控制回执仍按现协议携带固定 joint_paths=3。
    if (p["mode"] == "scan6" or p.get("applied_mode") == "scan6") and p["joint_paths"] != 3:
        raise ValueError("scan6 joint paths")
    if p.get("command_status") not in ("idle", "pending", "applied", "error", "rejected"):
        raise ValueError("processing status")
    for key in ("command_id", "error"):
        if not isinstance(p.get(key), str) or len(p[key]) > 512:
            raise ValueError("processing text")


def grid_index(angle, start, step, count):
    """Return the exact search-grid index for a displayed angle, else None."""
    if not finite(angle) or not finite(start) or not finite(step) or step <= 0:
        return None
    position = (angle - start) / step
    if position < -1e-8 or position > count - 1 + 1e-8:
        return None
    index = round(position)
    if abs(start + index * step - angle) > 1e-8:
        return None
    return int(index)


def validate_beam_scan(p):
    """[快速扫描新增] 同批 PL FFT 等效波束功率扫描；只校验与展示，不在 PC 重算。"""
    if "joint" in p:
        raise ValueError("joint in beam scan")
    if p.get("groups") != []:
        raise ValueError("beam scan groups must be empty")
    if p.get("direction_method") != SCAN_DIRECTION_METHOD:
        raise ValueError("beam scan direction method")
    if "direction_frames" in p:
        frames = p["direction_frames"]
        if (type(frames) is not int or frames not in (0,4) or p.get("direction_rounds") != list(range(frames))
                or (p.get("complete") and frames != 4)):
            raise ValueError("beam scan direction frames")
    search = p.get("search")
    if not isinstance(search, dict):
        raise ValueError("beam scan search")
    start, step, count = search.get("start"), search.get("step"), search.get("count")
    if (type(count) is not int or not 2 <= count <= 161
            or not finite(start) or not finite(step) or step <= 0):
        raise ValueError("beam scan search")
    scan = p.get("beam_scan")
    if not isinstance(scan, dict):
        raise ValueError("beam scan")
    if scan.get("model") != SCAN_MODEL or scan.get("source") != SCAN_SOURCE:
        raise ValueError("beam scan model/source")
    if scan.get("status") not in ("ok", "error", "not_run", "no_signal", "band_mismatch", "verification_failed") or type(scan.get("valid")) is not bool:
        raise ValueError("beam scan status")
    angles, power, db = scan.get("angles_deg"), scan.get("normalized_power"), scan.get("relative_db")
    if "fusion" in scan:
        validate_scan_fusion(scan["fusion"], scan, start, start + step * (count - 1))
    if angles == [] and power == [] and db == [] and not scan['valid']:
        if scan.get('candidates') != [] or scan.get('frame_count') not in (0,4):
            raise ValueError('Empty scan has candidates or invalid frame count')
        if scan['status'] == 'ok':
            raise ValueError('Empty successful scan')
        if 'verification' in scan:
            validate_scan_verification(scan['verification'], [])
        return
    if (not isinstance(angles, list) or len(angles) != count
            or any(not finite(v) or abs(v - (start + i * step)) > 1e-8 for i, v in enumerate(angles))):
        raise ValueError("beam scan angles")
    if (not isinstance(power, list) or len(power) != count
            or any(not finite(v) or v < 0 for v in power)):
        raise ValueError("beam scan power")
    if (not isinstance(db, list) or len(db) != count
            or any(not finite(v) or v > 1e-6 for v in db)):
        raise ValueError("beam scan relative db")
    if not finite(scan.get("peak_gap_db")) or scan["peak_gap_db"] < 0:
        raise ValueError("beam scan peak gap")
    if type(scan.get("ambiguous")) is not bool:
        raise ValueError("beam scan ambiguity")
    if not finite(scan.get("input_power")) or scan["input_power"] < 0:
        raise ValueError("beam scan input power")
    if type(scan.get("frame_count")) is not int or scan["frame_count"] not in (0, 4):
        raise ValueError("beam scan frame count")
    if not finite(scan.get("elapsed_ms")) or scan["elapsed_ms"] < 0:
        raise ValueError("beam scan time")
    if not finite(scan.get("local_snr_db")):
        raise ValueError("beam scan SNR")
    frequency, nyquist = scan.get("global_peak_frequency_hz"), p.get("baseband_sample_rate_hz")
    if (not finite(frequency) or not finite(nyquist) or nyquist <= 0
            or not -nyquist / 2 <= frequency <= nyquist / 2):
        raise ValueError("beam scan peak frequency")
    # 新报文没有 ML 残差/condition，不在这里强加六天线联合字段。
    candidates = scan.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > 8:
        raise ValueError("beam scan candidates")
    for index, candidate in enumerate(candidates):
        if (not isinstance(candidate, dict) or type(candidate.get("id")) is not int
                or candidate["id"] != index):
            raise ValueError("beam scan candidate id")
        angle, value = candidate.get("angle_deg"), candidate.get("normalized_power")
        if not finite(angle) or not finite(value) or value < 0:
            raise ValueError("beam scan candidate value")
        grid = grid_index(angle, start, step, count)
        if grid is None or abs(power[grid] - value) > 1e-6 * max(1.0, value):
            raise ValueError("beam scan candidate power")
        relative = candidate.get("relative_db")
        if relative is not None and (not finite(relative) or relative > 1e-6):
            raise ValueError("beam scan candidate db")
    peak_angle, peak_power = scan.get("peak_angle_deg"), scan.get("peak_normalized_power")
    peak = grid_index(peak_angle, start, step, count)
    if peak is None or not finite(peak_power) or abs(peak_power - power[peak]) > 1e-6 * max(1.0, peak_power):
        raise ValueError("beam scan peak")
    if scan["valid"]:
        if scan["status"] != "ok" or scan["frame_count"] != 4 or not candidates:
            raise ValueError("beam scan valid status")
        if power[peak] < max(power) - 1e-9:
            raise ValueError("beam scan peak is not maximum")
    if "verification" in scan:
        validate_scan_verification(scan["verification"], candidates)


def validate_scan_fusion(fusion, scan, lower, upper):
    """Validate the PS current-batch blend; this does not rerun ML on the PC."""
    if not isinstance(fusion, dict) or fusion.get("domain") != "sine":
        raise ValueError("scan fusion model")
    for key in ("enabled", "valid", "ml_available", "ml_qualified"):
        if type(fusion.get(key)) is not bool:
            raise ValueError("scan fusion boolean")
    if fusion.get("status") not in ("disabled", "unavailable", "scan_only", "blended"):
        raise ValueError("scan fusion status")
    for key in ("requested_ml_weight", "effective_ml_weight"):
        if not finite(fusion.get(key)) or not 0 <= fusion[key] <= 1:
            raise ValueError("scan fusion weight")
    if (not finite(fusion.get("ml_elapsed_ms")) or fusion["ml_elapsed_ms"] < 0):
        raise ValueError("scan fusion time")
    for key in ("scan_angle_deg", "ml_angle_deg", "angle_deg"):
        value = fusion.get(key)
        if key not in fusion or (value is not None and
                (not finite(value) or not -90 <= value <= 90 or not lower - 1e-6 <= value <= upper + 1e-6)):
            raise ValueError("scan fusion angle")
    model = fusion.get("ml_model")
    if not isinstance(model, dict):
        raise ValueError("scan fusion ML model")
    angles, amplitudes = model.get("angles_deg"), model.get("amplitudes")
    if (not isinstance(angles, list) or len(angles) > 3 or not isinstance(amplitudes, list)
            or len(amplitudes) != len(angles)
            or any(not finite(v) or not lower - 1e-6 <= v <= upper + 1e-6 for v in angles)
            or any(not finite(v) or v < 0 for v in amplitudes)):
        raise ValueError("scan fusion ML paths")
    if (type(model.get("model_paths")) is not int or not 0 <= model["model_paths"] <= 3
            or (angles and model["model_paths"] != len(angles))):
        raise ValueError("scan fusion ML order")
    if (type(model.get("path_index")) is not int or not -1 <= model["path_index"] <= 2
            or (model["path_index"] >= 0 and model["path_index"] >= len(angles))):
        raise ValueError("scan fusion ML path index")
    for key in ("ambiguous", "selection_accepted"):
        if type(model.get(key)) is not bool:
            raise ValueError("scan fusion ML boolean")
    if not isinstance(model.get("selection_status"), str) or len(model["selection_status"]) > 512:
        raise ValueError("scan fusion ML selection")
    for key in ("condition", "fused_residual_ratio", "raw_residual_ratio", "latest_residual_ratio"):
        value = model.get(key)
        if key not in model or (value is not None and (not finite(value) or value < 0)):
            raise ValueError("scan fusion ML quality")
    enabled, valid, available = fusion["enabled"], fusion["valid"], fusion["ml_available"]
    status, effective = fusion["status"], fusion["effective_ml_weight"]
    if fusion["ml_qualified"] and (not available or model["ambiguous"]):
        raise ValueError("scan fusion ML qualification")
    if available:
        path = model["path_index"]
        if (fusion["ml_angle_deg"] is None or not model["selection_accepted"] or path < 0
                or abs(fusion["ml_angle_deg"] - angles[path]) > 1e-6):
            raise ValueError("scan fusion ML selected angle")
    elif fusion["ml_angle_deg"] is not None:
        raise ValueError("scan fusion unavailable ML angle")
    if not enabled:
        if (status != "disabled" or valid or available or fusion["ml_qualified"] or effective != 0
                or fusion["ml_elapsed_ms"] != 0
                or any(fusion[key] is not None for key in ("angle_deg", "scan_angle_deg", "ml_angle_deg"))):
            raise ValueError("disabled scan fusion")
        return
    if not scan["valid"]:
        if status != "unavailable" or valid or effective != 0 or fusion["angle_deg"] is not None:
            raise ValueError("unavailable scan fusion")
        return
    if (not valid or fusion["scan_angle_deg"] is None or fusion["angle_deg"] is None
            or not finite(scan.get("peak_angle_deg"))
            or abs(fusion["scan_angle_deg"] - scan["peak_angle_deg"]) > 1e-6):
        raise ValueError("scan fusion source angle")
    if not available:
        if status != "scan_only" or effective != 0 or abs(fusion["angle_deg"] - fusion["scan_angle_deg"]) > 1e-6:
            raise ValueError("scan-only fusion")
        return
    if status != "blended" or abs(effective - fusion["requested_ml_weight"]) > 1e-12:
        raise ValueError("blended scan fusion weight")
    value = ((1 - effective) * math.sin(math.radians(fusion["scan_angle_deg"]))
             + effective * math.sin(math.radians(fusion["ml_angle_deg"])))
    expected = math.degrees(math.asin(max(-1., min(1., value))))
    if abs(fusion["angle_deg"] - expected) > 1e-6:
        raise ValueError("scan fusion formula")


def validate_scan_verification(v, candidates):
    if not isinstance(v, dict) or type(v.get("enabled")) is not bool:
        raise ValueError("scan verification")
    if v.get("status") not in ("disabled", "pending", "not_run", "offline_not_run", "checking", "matched", "mismatch", "error", "restore_failed"):
        raise ValueError("scan verification status")
    if not v["enabled"]:
        if v["status"] not in ("disabled", "pending", "not_run", "error"):
            raise ValueError("scan verification disabled status")
        return
    source_round = v.get("source_round")
    if type(source_round) is not int or source_round not in range(4):
        raise ValueError("scan verification round")
    if (type(v.get("restore_verified")) is not bool
            or not finite(v.get("elapsed_ms")) or v["elapsed_ms"] < 0):
        raise ValueError("scan verification restore/time")
    checks = v.get("checks")
    if not isinstance(checks, list) or len(checks) > len(candidates):
        raise ValueError("scan verification checks")
    if v["status"] == "matched" and (not checks or not v["restore_verified"] or not all(c.get("valid") for c in checks)):
        raise ValueError("scan verification matched checks")
    used = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("scan verification check")
        candidate_id = check.get("candidate_id")
        if type(candidate_id) is not int or not 0 <= candidate_id < len(candidates) or candidate_id in used:
            raise ValueError("scan verification candidate")
        used.add(candidate_id)
        angle = check.get("angle_deg")
        if not finite(angle) or abs(candidates[candidate_id]["angle_deg"] - angle) > 1e-8:
            raise ValueError("scan verification angle")
        for key in ("expected_power", "observed_power", "relative_error", "input_shape_error"):
            if not finite(check.get(key)) or check[key] < 0:
                raise ValueError("scan verification power")
        for key in ("registers_verified", "valid"):
            if type(check.get(key)) is not bool:
                raise ValueError("scan verification boolean")
        if check["valid"] and not check["registers_verified"]:
            raise ValueError("scan verification registers")
        if not isinstance(check.get("capture_id"), str) or len(check["capture_id"]) > 512:
            raise ValueError("scan verification capture")
        for key in ("fft_file", "pha_file", "source_input_file"):
            value = check.get(key, "")
            if not isinstance(value, str) or len(value) > 1024:
                raise ValueError("scan verification file")
        if check.get("npu_eligible") is not False:
            raise ValueError("scan verification NPU flag")


def validate_joint(p):
    mode = p.get("processing_mode", "position")
    v12 = p.get("v12", {}).get("version") == "1.2" if isinstance(p.get("v12"), dict) else False
    if mode not in PROCESSING_MODES:
        raise ValueError("processing mode")
    if mode == "scan6" and not v12:
        validate_beam_scan(p)
        return
    if "direction_frames" in p:
        frames = p["direction_frames"]
        if (type(frames) is not int or frames not in (0, 1, 4)
                or p.get("direction_rounds") != list(range(frames))
                or p.get("direction_method") not in (("single_frame", "normalized_covariance_principal_vector", SCAN_DIRECTION_METHOD) if v12 else ("single_frame", "normalized_covariance_principal_vector"))
                or (frames == 4 and not v12 and p["direction_method"] != "normalized_covariance_principal_vector")
                or not finite(p.get("covariance_concentration"))
                or not 0 <= p["covariance_concentration"] <= 1
                or type(p.get("raw_validation")) is not bool):
            raise ValueError("direction snapshot contract")
    if mode == "position" and not v12:
        if "joint" in p:
            raise ValueError("joint result in position mode")
        return
    if p["groups"] and not v12:
        raise ValueError("angle-only groups must be empty")
    j = p.get("joint")
    if not isinstance(j, dict) or j.get("model") != "far_field_ml":
        raise ValueError("joint model")
    n = j.get("path_count")
    if type(n) is not int or n not in (1, 2, 3) or j.get("channels") != list(range(6)):
        raise ValueError("joint shape")
    centre = j.get("reference_xy")
    if not isinstance(centre, list) or len(centre) != 2 or not all(finite(v) for v in centre):
        raise ValueError("joint reference")
    if any(type(j.get(k)) is not bool for k in ("has_best", "ambiguous")):
        raise ValueError("joint flags")
    for key in ("condition", "residual_ratio"):
        value = j.get(key)
        if value is not None and (not finite(value) or value < 0):
            raise ValueError("joint quality")
        if j["has_best"] and value is None:
            raise ValueError("missing joint quality")
    paths = j.get("paths")
    if not isinstance(paths, list) or len(paths) != (n if j["has_best"] else 0):
        raise ValueError("joint path count")
    search = p["search"]
    lo, hi = search["start"], search["start"]+(search["count"]-1)*search["step"]
    previous = -math.inf
    for i, path in enumerate(paths):
        if not isinstance(path, dict) or path.get("id") != i:
            raise ValueError("joint path id")
        angle = path.get("angle_deg")
        if not finite(angle) or not lo <= angle <= hi or angle <= previous:
            raise ValueError("joint angle")
        previous = angle
        if not finite(path.get("amplitude_rms")) or path["amplitude_rms"] < 0:
            raise ValueError("joint amplitude")
        power = path.get("relative_power_db")
        if power is not None and (not finite(power) or power > 1e-6):
            raise ValueError("joint relative power")
        if type(path.get("active")) is not bool or not finite(path.get("energy_share")) or not 0 <= path["energy_share"] <= 1:
            raise ValueError("joint energy")
    if paths and abs(sum(p["energy_share"] for p in paths)-1) > 1e-6:
        raise ValueError("joint energy sum")
    selection = j.get("selection")
    if "raw_validation" in j:
        validate_raw_validation(j["raw_validation"])
        q=j["raw_validation"]
        if q["passed"] and any(q[k] > p.get("max_bearing_residual", .1)
                               for k in ("raw_residual_ratio", "latest_residual_ratio")):
            raise ValueError("raw quality passed despite excessive residual")
    if p.get("raw_validation") is True:
        position_energy = (v12 and mode == "position"
                           and p["v12"].get("localization", {}).get("selection_method") == "triad_ml_energy")
        if position_energy:
            # POSITION validates the two triad fits; full-array ML is not run.
            if len(p["groups"]) != 2 or any("raw_validation" not in g for g in p["groups"]):
                raise ValueError("missing triad raw snapshot validation")
        elif "raw_validation" not in j:
            raise ValueError("missing raw snapshot validation")
    if selection is not None:
        if v12:
            # V1.2 selects on the board. Legacy hard angle/quality gates do not
            # describe the full-complex scan + Top30 soft matching decision.
            if (not isinstance(selection, dict)
                    or selection.get("method") != "original_ml_top30_softmatch_v1.2"
                    or type(selection.get("accepted")) is not bool):
                raise ValueError("v12 joint selection")
            index = selection.get("path_index")
            if index is not None and (type(index) is not int or not 0 <= index < len(paths)):
                raise ValueError("v12 joint selection index")
            if selection["accepted"] and index is None:
                raise ValueError("v12 missing selected path")
        else:
            validate_selection(selection, paths, n, lo, hi)
        if selection.get("method") == "quality_cascade_energy_fallback":
            for model in selection["models"]:
                if model["passed"] and (any(model[k] > p.get("max_bearing_residual", .1)
                        for k in ("raw_residual_ratio", "latest_residual_ratio"))
                        or min(model["amplitudes"]) <= 0
                        or min(model["amplitudes"]) < .1*max(model["amplitudes"])
                        or (model["path_count"] > 1 and model["incremental_contribution"] <= 1e-12)):
                    raise ValueError("model quality flag disagrees with metrics")
    profile = j.get("profile")
    if not isinstance(profile, dict):
        raise ValueError("joint profile")
    angles, db = profile.get("angles_deg"), profile.get("relative_db")
    if not isinstance(angles, list) or not isinstance(db, list) or len(angles) != len(db):
        raise ValueError("joint profile arrays")
    if len(angles) not in (0, search["count"]) or len(angles) > 161 or (j["has_best"] and not angles):
        raise ValueError("joint profile shape")
    if any(not finite(v) or abs(v-(lo+i*search["step"])) > 1e-8 for i, v in enumerate(angles)):
        raise ValueError("joint profile angles")
    if any(not finite(v) or not -80 <= v <= 1e-8 for v in db):
        raise ValueError("joint profile db")
    # 可选字段兼容原板端。这里只校验显示网格，绝不从其量化值重新选角度/权值。
    if "score_grid" in j:
        grid = j["score_grid"]
        if (not isinstance(grid, dict) or type(grid.get("dimensions")) is not int
                or grid["dimensions"] != n or grid.get("order") != "ascending_lexicographic"
                or grid.get("scale") != 254 or grid.get("invalid_value") != 255
                or len(angles) != search["count"] or len(angles) < n):
            raise ValueError("joint score grid shape")
        size = math.comb(len(angles), n)
        data = grid.get("score_u8")
        if not isinstance(data, str) or len(data) != (size+2)//3*4:
            raise ValueError("joint score grid size")
        if len(base64.b64decode(data, validate=True)) != size:
            raise ValueError("joint score grid bytes")


def validate_selection(s, paths, count, lo, hi):
    """Validate PS decisions without selecting a different path on the PC."""
    if not isinstance(s, dict):
        raise ValueError("joint selection shape")
    if s.get("method") == "quality_cascade_energy_fallback":
        validate_quality_selection(s, paths, count, lo, hi)
        return
    cascade = s.get("method") == "single_dual_triple_cascade"
    failures = ("no_reference", "no_constrained_candidate", "inactive_candidate", "residual_gap")
    if ((not cascade and s.get("method") != "single_dual_gated_triple")
            or count not in ((1, 2, 3) if cascade else (3,))
            or type(s.get("accepted")) is not bool
            or s.get("status") not in ("ok", *failures)):
        raise ValueError("joint selection shape")
    if cascade:
        stages = s.get("stages")
        if (not isinstance(stages, list) or len(stages) != 4-count
                or any(status not in failures for status in stages[:-1])
                or stages[-1] != s["status"] or (not s["accepted"] and count != 1)):
            raise ValueError("joint selection stages")
    for key in ("single_angle_deg", "half_width_deg", "dual_tolerance_deg", "amplitude_ratio",
                "residual_budget", "global_residual_ratio", "residual_gap_ratio"):
        if not finite(s.get(key)):
            raise ValueError("joint selection value")
    if (s["half_width_deg"] != 10 or s["dual_tolerance_deg"] != (5 if cascade else 3)
            or s["amplitude_ratio"] != .1 or s["residual_budget"] != .001):
        raise ValueError("joint selection contract")
    window, angles, amplitudes = s.get("range_deg"), s.get("dual_angles_deg"), s.get("dual_amplitudes")
    if (not isinstance(window, list) or len(window) != 2 or not all(finite(v) for v in window)
            or not isinstance(angles, list) or not isinstance(amplitudes, list)
            or len(angles) != len(amplitudes) or len(angles) not in (0, 2)
            or not all(finite(v) and lo <= v <= hi for v in angles)
            or not all(finite(v) and v >= 0 for v in amplitudes)):
        raise ValueError("joint selection references")
    if not s["accepted"]:
        if s.get("path_index") is not None or s["status"] == "ok":
            raise ValueError("rejected joint selection")
        return
    index = s.get("path_index")
    if (s["status"] != "ok" or type(index) is not int or not 0 <= index < len(paths)
            or (count >= 2 and len(angles) != 2)
            or not 0 <= s["residual_gap_ratio"] <= s["residual_budget"]):
        raise ValueError("accepted joint selection")
    expected = [max(lo, s["single_angle_deg"]-10), min(hi, s["single_angle_deg"]+10)]
    if any(abs(a-b) > 1e-8 for a, b in zip(window, expected)):
        raise ValueError("joint selection range")
    angle = paths[index]["angle_deg"]
    peak = max(p["amplitude_rms"] for p in paths)
    if not paths[index]["amplitude_rms"] > 0 or paths[index]["amplitude_rms"] < .1*peak:
        raise ValueError("joint selected amplitude")
    if not window[0] <= angle <= window[1]:
        raise ValueError("joint selected angle")
    if count < 3:
        if s.get("dual_distance_deg") is not None:
            raise ValueError("unused joint dual distance")
        if count == 1 and (index != 0 or abs(angle-s["single_angle_deg"]) > 1e-8
                           or s["residual_gap_ratio"] != 0):
            raise ValueError("joint single fallback")
        return
    valid = [a for a, amp in zip(angles, amplitudes) if amp > 0 and amp >= .1*max(amplitudes)]
    if not valid:
        raise ValueError("joint selected angle")
    distance = min(abs(angle-a) for a in valid)
    if (distance > s["dual_tolerance_deg"] or not finite(s.get("dual_distance_deg"))
            or abs(distance-s["dual_distance_deg"]) > 1e-8):
        raise ValueError("joint dual agreement")


def validate_raw_validation(q):
    if (not isinstance(q, dict) or q.get("available") is not True
            or type(q.get("passed")) is not bool
            or q.get("basis") != "original_snapshots_fixed_angles"
            or any(not finite(q.get(k)) or not 0 <= q[k] <= 1+1e-8
                   for k in ("raw_residual_ratio", "latest_residual_ratio"))):
        raise ValueError("raw snapshot quality")


def validate_quality_selection(s, paths, count, lo, hi):
    if (s.get("quality_profile") != "raw_batch_residual_amplitude_ambiguity"
            or any(type(s.get(k)) is not bool for k in ("accepted", "fallback", "has_candidate"))
            or s.get("status") not in ("qualified", "fallback", "no_candidate")
            or not finite(s.get("selected_score")) or not 0 <= s["selected_score"] <= 1+1e-8
            or s.get("dual_tolerance_deg") != 5 or s.get("amplitude_ratio") != .1):
        raise ValueError("quality selection contract")
    models = s.get("models")
    reasons = {"no_candidate", "singular", "invalid_snapshot", "raw_residual", "latest_residual",
               "ambiguous", "weak_path", "qualified", "no_increment"}
    if not isinstance(models, list) or len(models) != 3:
        raise ValueError("quality models")
    for k, model in enumerate(models, 1):
        if (not isinstance(model, dict) or type(model.get("path_count")) is not int
                or model["path_count"] != k or type(model.get("passed")) is not bool
                or model.get("reason") not in reasons
                or model["passed"] != (model["reason"] == "qualified")):
            raise ValueError("quality model status")
        for key in ("raw_residual_ratio", "latest_residual_ratio", "incremental_contribution"):
            if not finite(model.get(key)) or not 0 <= model[key] <= 1+1e-8:
                raise ValueError("quality model residual")
        angles, amps = model.get("angles_deg"), model.get("amplitudes")
        if (not isinstance(angles, list) or len(angles) not in (0, k)
                or not isinstance(amps, list) or len(amps) != len(angles)
                or any(not finite(v) or not lo <= v <= hi for v in angles)
                or any(a >= b for a, b in zip(angles, angles[1:]))
                or any(not finite(v) or v < 0 for v in amps)
                or (model["passed"] and not angles)):
            raise ValueError("quality model paths")
    candidates = s.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > 6:
        raise ValueError("quality candidates")
    previous = float('inf')
    used_support=set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("quality candidate shape")
        k, i, angle, score = (candidate.get(key) for key in ("model_paths", "path_index", "angle_deg", "score"))
        if (type(k) is not int or k not in (1, 2, 3) or type(i) is not int
                or not 0 <= i < len(models[k-1]["angles_deg"])
                or not finite(angle) or abs(angle-models[k-1]["angles_deg"][i]) > 1e-8
                or not finite(score) or not 0 < score <= 1+1e-8 or score > previous+1e-8):
            raise ValueError("quality candidate value")
        previous = score
        support, indices = candidate.get("support_models"), candidate.get("support_paths")
        if (not isinstance(support, list) or not 1 <= len(support) <= 2
                or not isinstance(indices, list) or len(indices) != len(support)
                or any(type(v) is not int or v not in (1, 2, 3) for v in support)
                or len(set(support)) != len(support) or (k, i) not in zip(support, indices)):
            raise ValueError("quality support models")
        for order, index in zip(support, indices):
            if (type(index) is not int or not 0 <= index < len(models[order-1]["angles_deg"])
                    or abs(models[order-1]["angles_deg"][index]-angle) > 5+1e-8
                    or (order,index) in used_support):
                raise ValueError("quality support paths")
            used_support.add((order,index))
    if sum(c["score"] for c in candidates) > 1+1e-8:
        raise ValueError("quality candidate score sum")
    if s["has_candidate"] != bool(candidates):
        raise ValueError("quality candidate presence")
    if candidates:
        c=candidates[0];i=s.get("path_index")
        if (type(i) is not int or not 0 <= i < len(paths) or c["model_paths"] != count
                or c["path_index"] != i or abs(c["angle_deg"]-paths[i]["angle_deg"]) > 1e-8
                or abs(c["score"]-s["selected_score"]) > 1e-8):
            raise ValueError("quality selected candidate")
    elif s.get("path_index") is not None or s["selected_score"] != 0:
        raise ValueError("quality absent candidate")
    stages=[]
    for model in reversed(models):
        stages.append(model["reason"])
        if model["passed"]:
            break
    if s.get("stages") != stages:
        raise ValueError("quality selection stages")
    passed = any(m["passed"] for m in models)
    if s["accepted"]:
        if (not s["has_candidate"] or s["fallback"] or s["status"] != "qualified"
                or not models[count-1]["passed"] or any(m["passed"] for m in models[count:])):
            raise ValueError("quality accepted status")
    elif (passed or s["fallback"] != s["has_candidate"]
          or s["status"] != ("fallback" if candidates else "no_candidate")):
        raise ValueError("quality rejected status")


def eligible_paths(aoa):
    """可供显示选择/控制的路径。能量份额来自 PS，不转换为校准概率。"""
    if not aoa or aoa.get("processing_mode") != "angle6" or not aoa.get("complete") or aoa.get("status") != "ok":
        return []
    v12 = aoa.get("v12", {})
    if v12.get("version") == "1.2":
        chosen = v12.get("joint", {})
        return [p for p in aoa.get("joint", {}).get("paths", [])
                if chosen.get("valid") and p["id"] == chosen.get("path_index")]
    j = aoa.get("joint", {})
    limit = aoa.get("max_bearing_residual", .1)
    q = j.get("raw_validation")
    if q is not None:
        try:
            validate_raw_validation(q)
        except ValueError:
            return []
        if not q["passed"] or q["raw_residual_ratio"] > limit or q["latest_residual_ratio"] > limit:
            return []
    selection = j.get("selection", {})
    if selection.get("method") == "quality_cascade_energy_fallback":
        models = selection.get("models", [])
        count = j.get("path_count")
        if (selection.get("fallback") or not selection.get("accepted") or count not in (1, 2, 3)
                or len(models) != 3 or not models[count-1].get("passed")
                or any(not finite(models[count-1].get(k)) or models[count-1][k] > limit
                       for k in ("raw_residual_ratio", "latest_residual_ratio"))):
            return []
    if j.get("selection", {}).get("accepted") is False:
        return []
    ratio = j.get("residual_ratio")
    if not j.get("has_best") or j.get("ambiguous") or not finite(ratio) or ratio > aoa.get("max_bearing_residual", .1):
        return []
    return [p for p in j.get("paths", []) if p["active"]]


def automatic_path(aoa):
    paths = eligible_paths(aoa)
    if (aoa or {}).get("v12", {}).get("version") == "1.2":
        return next(iter(paths), None)
    selection = (aoa or {}).get("joint", {}).get("selection")
    if selection is not None:
        return next((p for p in paths if selection["accepted"] and p["id"] == selection["path_index"]), None)
    return max(paths, key=lambda p: p["energy_share"], default=None)
