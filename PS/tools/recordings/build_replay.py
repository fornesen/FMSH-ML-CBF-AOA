"""Build the same PS numeric code for Windows/Linux without SDK or network access."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]

def build(output,cxx,fixture=False):
    output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    sources=['tests/test_raw_recording.cpp' if fixture else 'tools/recordings/replay_capture.cpp','src/fft_capture.cpp','src/pl_fft_input.cpp',
             'src/direction.cpp','src/localization.cpp','src/export.cpp','src/aoa_ml3.cpp']
    command=[cxx,'-std=c++17','-O2','-Wall','-Wextra','-Werror','-DICRAFT_AOA_HOST_ONLY','-Isrc','-pthread']
    if os.name=='nt':
        command.append('-static')
        if not fixture:command.append('-municode')
    subprocess.run(command+sources+['-o',str(output)],cwd=ROOT,check=True)
    return output

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cxx',default='g++');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--fixture',action='store_true')
    a=p.parse_args();print(build(a.output,a.cxx,a.fixture))
