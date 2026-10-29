"""Independently check all six new simulated classes through the C++ loader."""
import hashlib
import json
from pathlib import Path
import subprocess
import numpy as np
from signal_model import ROOT


def linux(p):
    s=Path(p).resolve().as_posix()
    return '/mnt/'+s[0].lower()+s[2:]


def main():
    exp=ROOT/'experiments/classifier_sixsum_pm_v7'
    folder=exp/'preprocessing_verification';folder.mkdir()
    rows=[json.loads(x) for x in (exp/'manifest.jsonl').read_text().splitlines()]
    selected=[next(r for r in rows if r['split']=='train' and r['class_index']==c) for c in range(6)]
    requests=[]
    for i,row in enumerate(selected):
        meta=dict(format_version=1,ready=True,capture_round_count=4,samples_per_round=4096,
            total_complex_samples=16384,byte_count=131072,shape=[4,4096,2],dtype='int32',byte_order='little',
            component_order=['I','Q'],sample_rate_hz=25000000,direction_round_index=3,run_id='parity_%d'%i,transport='shm',
            captures=[dict(index=k,complete=True,pha_collected=True,pha_written_samples=4096,pha_format_errors=0,
                written_slots=24576,error='',guard_checked=False) for k in range(4)])
        requests.append(dict(name=meta['run_id'],manifest=meta,raw_file=linux(row['iq_path'])))
    inp=folder/'requests.jsonl';inp.write_text(''.join(json.dumps(r)+'\n' for r in requests))
    output=folder/'cpp'
    exe=exp/'runtime_candidate/build/host/dump_amplitude_features'
    command=['wsl.exe','-d','Debian','--exec',linux(exe),linux(inp),linux(output)]
    subprocess.run(command,check=True)
    result=[]
    for i,row in enumerate(selected):
        diffs={}
        with np.load(row['feature_path']) as f:
            for key,suffix in [('tf','tf'),('spectrum','spectrum'),('amplitude','sequence')]:
                expected=f[key][:,0].transpose(1,2,0)[None].astype('<f4')/255
                actual=np.fromfile(str(output/('parity_%d_%s.ftmp'%(i,suffix))),dtype='<f4').reshape(expected.shape)
                diffs[key]=int(np.count_nonzero(expected!=actual))
        assert all(v==0 for v in diffs.values()),(row['kind'],diffs)
        result.append(dict(kind=row['kind'],differences=diffs))
    (folder/'result.json').write_text(json.dumps(dict(passed=True,classes=result),indent=2)+'\n')
    print('PASS C++/training feature parity for all six classes')


if __name__=='__main__':main()
