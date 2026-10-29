"""明确标注的 UI 演示数据。不会发送给板子，也不代表算法/模型验证。"""
import base64
import math
import time
from functools import lru_cache
from protocol import CLASSES

_sequence = 0


def b64(values):
    return base64.b64encode(bytes(values)).decode()


@lru_cache(maxsize=2)
def grid(group):
    angles = list(range(-80,81))
    score, mask = [], []
    for x in angles:
        for y in angles:
            score.append(round(255*max(0,min(1,.55+.3*math.cos((x+y+group*30)*.03)+.22*math.sin((x-y)*.035)**2))))
            mask.append(int(x==y))
    return {"angles_deg": angles, "score_u8": b64(score), "invalid_u8": b64(mask), "order": "theta1,theta2"}


def packets():
    global _sequence
    _sequence += 1
    n = _sequence
    run = f"DEMO-{n:06d}"
    now = time.time()
    d = 299792458/433e6/2
    pos = [-2*d-.25,-d-.25,-.25,.25,d+.25,2*d+.25]
    shared = {"schema": 1, "run_id": run, "seq": n, "produced_unix_ms": int(now*1000),
              "transport": {"sent": n, "dropped": 0, "failures": 0}}
    groups = []
    for g, (angles, power) in enumerate([([12.,35.],[-8.,0.]), ([-28.,-8.],[0.,-10.])]):
        groups.append({"id": g,"channels": list(range(g*3,g*3+3)),"has_best": True,"ambiguous": False,
                       "energy": 100.,"residual_ratio": .03,"condition": 3.2,"grid": dict(grid(g)),
                       "paths": [{"id": p,"angle_deg": a,"amplitude_rms": 10**(power[p]/20),
                                  "relative_power_db": power[p],"active": True} for p,a in enumerate(angles)]})
    aoa = dict(shared, source="aoa",session="demo_aoa",type="aoa",status="ok",complete=True,
               capture_mode="synthetic_demo",handoff="sent",rounds=4,direction_round=3,
               sample_rate_hz=100e6,baseband_sample_rate_hz=25e6,rf_hz=433e6,dds_hz=12.5e6,
               search={"start":-80,"step":1,"count":161},groups=groups,
               centres=[[-d-.25,0],[d+.25,0]],antennas=[[x,0] for x in pos],
               beam_weights_verified=False,analysis_ms=145.,capture_ms=58.,min_bin_power=0,
               counts={"attempted":n,"ipc_sent":n,"ipc_busy":0,"ipc_absent":0,"ipc_errors":0})
    spectrum = [round(8+210*math.exp(-((i-350)/24)**2)+22*abs(math.sin(i*1.31))) for i in range(640)]
    tf = [max(0,min(255,round(spectrum[i]*(.75+.22*math.sin(t*.24))))) for t in range(64) for i in range(640)]
    signal = {"channel":"PHA","round_index":3,"sample_rate_hz":25e6,"wave_start_sample":64,
              "wave_iq":[[(.55+.3*math.cos(i*.018))*math.cos(i*.2),(.55+.3*math.cos(i*.018))*math.sin(i*.2)] for i in range(512)],
              "freq_min_hz":-2e6,"freq_max_hz":2e6,"columns":640,"rows":64,"db_min":-75,"db_max":0,
              "spectrum_u8":b64(spectrum),"tf_u8":b64(tf),"native_stft_frames":48,"window":1024,"hop":64,
              "continuous_between_batches":False,"iq_divisor":1048576,"input_gain":1}
    npu = dict(shared,source="npu",session="demo_npu",type="npu",status="inferred",backend="npu",npu_executed=False,
               model_family="sixsum_ook_v6",total_ms=87.,signal=signal,
               classification={"classes":CLASSES,"probabilities":[.03,.87,.06,.01,.03],"predicted_class":"AM","predicted_class_index":1})
    return aoa,npu
