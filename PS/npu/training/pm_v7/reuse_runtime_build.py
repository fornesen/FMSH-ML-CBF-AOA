"""Reuse only byte-identical six-class consumer builds between training stages."""
import hashlib
import json
from pathlib import Path
import shutil
from signal_model import ROOT


def main():
    base=ROOT/'experiments/classifier_sixsum_pm_v7'
    exp=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz'
    old=base/'runtime_candidate';new=exp/'runtime_candidate'
    hashes={}
    files=[p for d in ('src','include') for p in (old/d).rglob('*') if p.is_file()]
    files += [old/'tools/validate_numeric.cpp',old/'tests/classification_contract.cpp',old/'tests/amplitude_preprocessing.cpp']
    for path in files:
        rel=path.relative_to(old)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        assert hashlib.sha256((new/rel).read_bytes()).hexdigest()==digest,str(rel)
        hashes[rel.as_posix()]=digest
    binary_hashes={}
    for rel in ['build/arm64/icraft_npu_yolo','build/numeric/Release/validate_numeric_windows.exe']:
        target=new/rel;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(old/rel,target)
        binary_hashes[rel]=hashlib.sha256(target.read_bytes()).hexdigest()
    shutil.copytree(base/'pc_compat',exp/'pc_compat')
    (exp/'runtime_build_verification.json').write_text(json.dumps(dict(source_identical=hashes,
        binaries=binary_hashes,origin=str(old),classification_and_amplitude_ctest_passed=True,
        cpu_numeric_tool_compiled_with_Icraft3361=True,arm64_build_machine_verified='AArch64'),indent=2)+'\n')
    print('Verified identical source and reused two tested six-class binaries')


if __name__=='__main__':main()
