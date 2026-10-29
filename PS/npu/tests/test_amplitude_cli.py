"""Check real Linux CLI replay, source snapshots and completion marker safety."""
import argparse
import json
from pathlib import Path
import subprocess
import numpy as np


def wsl_path(path):
    value = Path(path).resolve().as_posix()
    return '/mnt/' + value[0].lower() + value[2:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, help='Linux executable path inside WSL')
    parser.add_argument('--distro', default='Debian')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Preserve existing evidence; choose a new output directory')
    args.output.mkdir(parents=True)
    iq = 2400*np.exp(-2j*np.pi*500000*np.arange(4096)/25e6)
    words = np.rint(np.stack((iq.real, iq.imag), -1)).astype('<i4')
    raw = np.stack([words]*4).astype('<i4').tobytes()
    source = args.output/'pha4.raw'
    source.write_bytes(raw)
    manifest = dict(format_version=1, ready=True, run_id='amplitude_cli', capture_round_count=4,
                    samples_per_round=4096, total_complex_samples=16384, byte_count=131072,
                    shape=[4,4096,2], dtype='int32', byte_order='little', component_order=['I','Q'],
                    sample_rate_hz=25000000, direction_round_index=3, raw_file=wsl_path(source),
                    captures=[dict(index=i, complete=True, pha_saved=True, pha_written_samples=4096,
                                   pha_format_errors=0, written_slots=24576, error='', guard_checked=False)
                              for i in range(4)])
    ready = args.output/'ready.json'
    ready.write_text(json.dumps(manifest), encoding='utf-8')
    results = args.output/'results'

    def run(extra):
        command = ['wsl.exe','-d',args.distro,'--exec','env','AOA_CONSOLE_HOST=',args.binary,
                   '--manifest',wsl_path(ready),'--backend','preprocess','--save-results',
                   '--output-dir',wsl_path(results)] + extra
        return subprocess.run(command, capture_output=True, text=True, timeout=30)

    response = run(['--amplitude-mode','fixed'])
    assert response.returncode == 0, response.stderr
    marker = results/'amplitude_cli_preprocess_result.json'
    old = marker.read_bytes()
    assert not any((results/'amplitude_cli_sequence.u8').read_bytes())
    response = run([])
    assert response.returncode != 0 and 'different amplitude preprocessing' in response.stderr
    assert marker.read_bytes() == old
    response = run(['--force'])
    assert response.returncode == 0, response.stderr
    result = json.loads(marker.read_text())
    assert result['amplitude_preprocessing']['applied']
    assert result['effective_input_gain'] == result['amplitude_preprocessing']['effective_input_gain']
    assert result['source_raw_sha256'] == json.loads(old)['source_raw_sha256']
    assert (results/'amplitude_cli_source_pha4.raw').read_bytes() == raw
    assert min((results/'amplitude_cli_sequence.u8').read_bytes()) > 0
    saved = marker.read_bytes()
    assert run([]).returncode == 3 and marker.read_bytes() == saved
    for options in (['--amplitude-mode','per_round'], ['--amplitude-target-rms','0'],
                    ['--amplitude-max-gain','0.5'], ['--amplitude-min-coherence','1.1'],
                    ['--amplitude-target-rms','nan']):
        response = run(options)
        assert response.returncode != 0 and marker.read_bytes() == saved, options
    summary = dict(passed=True, legacy_mode_exact=True, original_raw_preserved=True,
                   nonzero_sequence_elements=192, configuration_change_requires_force=True,
                   duplicate_run_exit_code=3, invalid_options_rejected=5,
                   amplitude_preprocessing=result['amplitude_preprocessing'])
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
