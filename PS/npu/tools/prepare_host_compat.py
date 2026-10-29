"""Project-local compatibility copy; the original Icraft SDK is never edited."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'tools/host-deps/root/usr/lib/x86_64-linux-gnu/libtorch_cpu.so'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk',type=Path,default=os.environ.get('ICRAFT_ROOT'),
                        required=not os.environ.get('ICRAFT_ROOT'),help='Icraft SDK directory; defaults to ICRAFT_ROOT')
    args=parser.parse_args()
    source=args.sdk.resolve()/'lib/x86_64-linux-gnu/libtorch_cpu.so'
    if TARGET.exists():
        raise SystemExit('Project compatibility copy already exists; preserve it')
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    TARGET.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,TARGET)
    changes=[]
    with TARGET.open('r+b') as f:
        header=f.read(64)
        assert header[:6]==b'\x7fELF\x02\x01'
        phoff=struct.unpack_from('<Q',header,32)[0]
        phsize,phnum=struct.unpack_from('<HH',header,54)
        for i in range(phnum):
            offset=phoff+i*phsize
            f.seek(offset);entry=f.read(phsize)
            kind,flags=struct.unpack_from('<II',entry)
            if kind==0x6474e551 and flags&1:
                f.seek(offset+4);f.write(struct.pack('<I',flags&~1))
                changes.append(dict(program_header=i,old_flags=flags,new_flags=flags&~1))
    assert changes
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    result=dict(source='ICRAFT_ROOT/lib/x86_64-linux-gnu/libtorch_cpu.so',
        target=TARGET.relative_to(ROOT).as_posix(),source_sha256=before,
        target_sha256=hashlib.sha256(TARGET.read_bytes()).hexdigest(),changes=changes,
        reason='Current WSL loader rejects legacy libtorch executable-stack request; remove PF_X from GNU_STACK on local copy only')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
