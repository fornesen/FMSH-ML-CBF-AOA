"""Reproducible local-only six-class training, evaluation and static export."""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from signal_model import ROOT


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment',type=Path,default=ROOT/'experiments/classifier_sixsum_pm_v7')
    p.add_argument('--epochs',type=int,default=30)
    p.add_argument('--init-checkpoint',type=Path)
    a=p.parse_args();exp=a.experiment.resolve()
    checks=json.loads((exp/'dataset_checks.json').read_text())
    assert checks['passed'] and checks['fresh_tests_frozen_before_training']
    digest=hashlib.sha256((exp/'manifest.jsonl').read_bytes()).hexdigest()
    assert digest==checks['manifest_sha256']
    status=exp/'pipeline_status.json'
    if status.exists():raise SystemExit('Preserve previous pipeline')
    init=a.init_checkpoint.resolve() if a.init_checkpoint else ROOT/'experiments/classifier_sixsum_ook_v6/runs/branches_amp/weights/best.pt'
    commands=[('train',[ROOT/'train_classification.py','--experiment',exp,'--variant','branches_amp',
        '--init-checkpoint',init,'--expand-classifier','--epochs',a.epochs,'--batch-size',16,
        '--backbone-lr',.0001,'--head-lr',.0005,'--patience',8,'--seed',20261027]),
        ('evaluate',[Path(__file__).parent/'evaluate.py','--experiment',exp]),
        ('export',[ROOT/'export_npu_static.py','--experiment',exp,'--validation-records',42])]
    state=dict(state='running',completed=[],manifest_sha256=digest)
    for step,command in commands:
        state['current']=step;status.write_text(json.dumps(state,indent=2))
        started=time.monotonic()
        with (exp/(step+'.log')).open('w',encoding='utf-8') as f:
            result=subprocess.run([sys.executable,'-X','utf8']+list(map(str,command)),cwd=str(ROOT),stdout=f,stderr=subprocess.STDOUT)
        state['completed'].append(dict(step=step,exit_code=result.returncode,seconds=time.monotonic()-started))
        state['state']='failed' if result.returncode else 'running'
        status.write_text(json.dumps(state,indent=2))
        if result.returncode:raise SystemExit('Failed '+step+'; see log')
        print('Completed',step,flush=True)
    assert hashlib.sha256((exp/'manifest.jsonl').read_bytes()).hexdigest()==digest
    state.update(state='complete',current=None);status.write_text(json.dumps(state,indent=2))


if __name__=='__main__':main()
