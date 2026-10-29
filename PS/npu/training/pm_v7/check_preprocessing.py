"""Check new training preprocessing against three saved real board outputs."""
import argparse
import json
from pathlib import Path
import numpy as np
from signal_model import features


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archives',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); results=[]
    for folder in sorted(a.archives.glob('batch_*')):
        raws=list(folder.rglob('*source_pha4.raw'))
        if len(raws)!=1: continue
        raw=raws[0]; fields,gain=features(np.fromfile(str(raw),dtype='<i4').reshape(4,4096,2))
        diffs={}
        for key,suffix in [('tf','tf'),('spectrum','spectrum'),('amplitude','sequence')]:
            other=raw.with_name(raw.name.replace('source_pha4.raw',suffix+'.u8'))
            actual=np.fromfile(str(other),dtype='u1')
            diffs[key]=int(np.count_nonzero(fields[key].ravel()!=actual))
        assert all(v==0 for v in diffs.values()),diffs
        results.append(dict(raw=str(raw),differences=diffs,gain=gain))
    assert results,'No board reference found'
    a.output.write_text(json.dumps(dict(passed=True,results=results),indent=2)+'\n')
    print('PASS exact board preprocessing parity:',len(results),'batches')


if __name__=='__main__':main()
