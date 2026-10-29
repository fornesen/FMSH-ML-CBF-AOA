"""Capture user-confirmed PM display IQ without changing board control mode.

These are final-round display snapshots from distinct acquisition run IDs,
not an original four-round NPU archive. Keep this distinction in all results.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import time
import urllib.request


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seconds',type=float,default=60)
    a=p.parse_args();out=a.output.resolve();out.mkdir()
    seen=set();samples=[];start=time.monotonic();baseline=None
    while time.monotonic()-start<a.seconds and len(samples)<4:
        with urllib.request.urlopen('http://127.0.0.1:8775/api/state',timeout=8) as response:
            state=json.loads(response.read().decode('utf-8'))
        aoa=state.get('aoa') or {};obs=aoa.get('observation') or {}
        run=aoa.get('run_id')
        if baseline is None:baseline=run
        if run and run!=baseline and run not in seen and obs.get('valid') and obs.get('waveform_enabled'):
            raw=base64.b64decode(obs['wave_iq_s32le'],validate=True)
            assert len(raw)==4096*8
            path=out/('round_%d.raw'%len(samples));path.write_bytes(raw)
            record=dict(run_id=run,received_at=aoa.get('received_at'),round_index=obs['round_index'],
                        sha256=hashlib.sha256(raw).hexdigest(),path=str(path))
            samples.append(record);seen.add(run)
            (out/('snapshot_%d.json'%(len(samples)-1))).write_text(json.dumps(state),encoding='utf-8')
            print('Captured fresh PM display snapshot',len(samples),run,flush=True)
        time.sleep(.8)
    result=dict(user_truth=dict(carrier_hz=445000000,message_hz_approx=3333,phase_deviation_deg_approx=90),
        source='PC API final-round IQ display',original_four_round_archive=False,
        control_mode_unchanged=True,samples=samples,seconds=time.monotonic()-start,baseline_run_id=baseline)
    (out/'capture.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Finished fresh snapshots',len(samples),flush=True)


if __name__=='__main__':main()
