"""Build current PS contracts without the proprietary Icraft SDK."""
import argparse
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cxx', default='g++')
    args = parser.parse_args()
    build = ROOT/'build/contract-tests'
    build.mkdir(parents=True, exist_ok=True)
    flags = ['-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
             '-DICRAFT_AOA_HOST_ONLY', '-Isrc', '-pthread']
    if os.name == 'nt':
        flags.append('-static')
    cases = {
        'compensation': ['tests/test_compensation.cpp', 'src/app_config.cpp', 'src/fft_capture.cpp',
                         'src/pl_fft_input.cpp', 'src/direction.cpp', 'src/localization.cpp',
                         'src/export.cpp', 'src/aoa_ml3.cpp'],
        'archive_pipeline': ['tests/test_archive_switch_pipeline.cpp', 'src/capture_batch.cpp',
                             'src/fft_capture.cpp', 'src/pl_fft_input.cpp', 'src/direction.cpp',
                             'src/localization.cpp', 'src/export.cpp', 'src/aoa_ml3.cpp'],
        'archive_control': ['tests/test_archive_control.cpp'],
        'position_energy': ['tests/test_position_energy.cpp', 'src/localization.cpp'],
        'joint_math': ['tests/test_joint_math.cpp', 'src/direction.cpp',
                       'src/localization.cpp', 'src/export.cpp', 'src/aoa_ml3.cpp'],
        'v12': ['tests/test_v12.cpp', 'src/direction.cpp', 'src/localization.cpp',
                'src/export.cpp', 'src/aoa_ml3.cpp', 'src/fft_capture.cpp', 'src/pl_fft_input.cpp'],
        'beam_scan': ['tests/test_beam_scan.cpp'],
        'scan_mode': ['tests/test_scan_mode.cpp', 'src/direction.cpp', 'src/localization.cpp',
                        'src/export.cpp', 'src/aoa_ml3.cpp'],
        'raw_recording': ['tests/test_raw_recording.cpp', 'src/fft_capture.cpp', 'src/pl_fft_input.cpp',
                          'src/direction.cpp', 'src/localization.cpp', 'src/export.cpp', 'src/aoa_ml3.cpp'],
        'ml_preprocessing': ['tests/test_ml_preprocessing.cpp'],
        'fft_window_decode': ['tests/test_fft_window_decode.cpp', 'src/fft_capture.cpp', 'src/pl_fft_input.cpp'],
        'auto_selection': ['tests/test_auto_selection.cpp'],
        'batch_covariance': ['tests/test_batch_covariance.cpp'],
        'four_frame_pipeline': ['tests/test_four_frame_pipeline.cpp', 'src/capture_batch.cpp',
                                'src/fft_capture.cpp', 'src/pl_fft_input.cpp', 'src/direction.cpp',
                                'src/localization.cpp', 'src/export.cpp', 'src/aoa_ml3.cpp'],
    }
    for name, sources in cases.items():
        binary = build/(name+('.exe' if os.name == 'nt' else ''))
        subprocess.run([args.cxx, *flags, *sources, '-o', str(binary)], cwd=ROOT, check=True)
        command=[str(binary)]
        if name=='raw_recording':command.append(str(build/'recording-fixture'))
        if name=='scan_mode':command.append(str(build/'scan_mode_packet.json'))
        if name in ('compensation', 'archive_pipeline'):
            command.append(str(build/(name+'_'+uuid.uuid4().hex[:8])))
        test_env=dict(os.environ)
        if os.name=="nt":test_env.update(TEMP=str(build),TMP=str(build))
        subprocess.run(command, cwd=ROOT, check=True, env=test_env)
        print('PASS', name, flush=True)


if __name__ == '__main__':
    main()
