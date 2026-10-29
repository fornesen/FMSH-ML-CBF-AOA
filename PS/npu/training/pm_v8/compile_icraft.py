"""Use the verified six-class compiler with a unique model artifact name."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'pm_v7'),str(ROOT)]
import compile_icraft as compiler
compiler.NAME='fm_sixsum_fm_pm_v8'
if __name__=='__main__':compiler.main()
