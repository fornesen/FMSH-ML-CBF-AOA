"""Build the recordings adapter around the official residual-only QR collector."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]

def build(cxx,output):
    folder=ROOT/'build/beam_candidates';folder.mkdir(parents=True,exist_ok=True)
    source=(ROOT/'src/joint_ml.hpp').read_text(encoding='utf-8')
    original=source[source.index('inline void analyze_joint_search('):source.rindex('\n} // namespace aoa_app')]
    digest=hashlib.sha256(original.encode()).hexdigest()
    (folder/'candidate_hook.hpp').write_text(
        '#pragma once\n#include "joint_ml.hpp"\nnamespace beam_test {\n'
        'using namespace aoa_app;\nusing RankedCandidate=std::pair<double,std::array<std::size_t,3>>;\n'
        'inline void retained_search(const Options& o,const FftData& fft,Analysis& result,\n'
        '    const std::function<bool()>& cancelled,std::vector<RankedCandidate>& pool) {\n'
        '    JointPool retained;\n'
        '    analyze_joint_search(o,fft,result,cancelled,&retained);\n'
        '    pool.clear();pool.reserve(retained.top.size());\n'
        '    for(const auto& entry:retained.top) pool.emplace_back(entry.loss,entry.index);\n'
        '}\n}\n#define AOA_SEARCH_SOURCE_SHA "'+digest+'"\n',encoding='utf-8')
    output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    command=[cxx,'-std=c++17','-O2','-Wall','-Wextra','-Werror','-DICRAFT_AOA_HOST_ONLY',
             '-Isrc','-I'+str(folder),'tools/recordings/search_candidates.cpp','-o',str(output)]
    if os.name=='nt':command+=['-static','-municode']
    subprocess.run(command,cwd=ROOT,check=True)
    print('Original search function SHA256: '+digest)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cxx',default='g++')
    p.add_argument('--output',required=True);a=p.parse_args();build(a.cxx,a.output)
