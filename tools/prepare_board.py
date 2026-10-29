"""Assemble the minimal V2.0 board runtime. Never contacts or builds on a board."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = '/root/aoa_system/V2.0'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def readiness():
    assets = ROOT / 'assets/board'
    missing = []
    for name in ('Board/install_service.sh', 'Board/systemd/aoa-pl.service',
                 'Board/systemd/aoa-v2.service', 'PS/calibration/calibrate.py',
                 'PS/calibration/calibration_math.py', 'PS/calibration/delay_math.py'):
        if not (ROOT / name).is_file():
            missing.append(name)
    for name in ('bin/icraft_aoa_ml3', 'bin/icraft_npu_yolo', 'pl/aoa_console.bin',
                 'models/fm_sixsum_fm_pm_v8_ZG.json', 'models/fm_sixsum_fm_pm_v8_ZG.raw',
                 'models/fm_sixsum_fm_pm_v8_contract.json',
                 'python/bin/python3', 'lib/ld-linux-aarch64.so.1'):
        if not (assets / name).is_file():
            missing.append(name)
    return dict(version='2.0', target=TARGET, ready=not missing, missing=missing)


def package(output, allow_incomplete=False, tar_path=None):
    status = readiness()
    if not status['ready'] and not allow_incomplete:
        raise RuntimeError('Board runtime incomplete: ' + ', '.join(status['missing']))
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Refusing to overwrite: ' + str(output))
    if tar_path is not None:
        tar_path = Path(tar_path).resolve()
        if tar_path == output or output in tar_path.parents:
            raise ValueError('Tar archive cannot be inside the output folder')
        if tar_path.exists():
            raise FileExistsError('Refusing to overwrite: ' + str(tar_path))
    for name in ('Board', 'assets/board', 'common'):
        source = (ROOT / name).resolve()
        for label, destination in (('Output', output), ('Tar archive', tar_path)):
            if destination is not None and (destination == source or source in destination.parents):
                raise ValueError(label + ' cannot be inside a board package input: ' + str(destination))
    output.mkdir(parents=True)
    for source_name, target_name in (('Board/runtime', 'runtime'), ('Board/config', 'config'),
                                     ('Board/systemd', 'systemd'),
                                     ('assets/board/bin', 'bin'), ('assets/board/models', 'models'),
                                     ('assets/board/lib', 'lib'),
                                     ('assets/board/python', 'python')):
        source = ROOT / source_name
        if source.is_dir():
            shutil.copytree(source, output / target_name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    image = ROOT / 'assets/board/pl/aoa_console.bin'
    if image.is_file():
        (output / 'pl').mkdir()
        shutil.copyfile(image, output / 'pl/aoa_console.bin')
    (output / 'common').mkdir()
    for name in ('__init__.py', 'archive_io.py', 'frequency_compensation.py'):
        shutil.copyfile(ROOT / 'common' / name, output / 'common' / name)
    calibration = ROOT / 'PS/calibration'
    if calibration.is_dir():
        (output / 'calibration').mkdir()
        for name in ('calibrate.py', 'calibration_math.py', 'delay_math.py'):
            shutil.copyfile(calibration / name, output / 'calibration' / name)
    for name in ('python.sh', 'start.sh', 'stop.sh', 'status.sh', 'install_service.sh'):
        if name == 'install_service.sh' and not (ROOT / 'Board' / name).is_file():
            continue
        shutil.copyfile(ROOT / 'Board' / name, output / name)
        (output / name).chmod(0o755)
    for folder in ('runs', 'logs'):
        (output / folder).mkdir()
    job_path = output / 'config/job.json'
    job = json.loads(job_path.read_text(encoding='utf-8'))
    job['elf_sha256'] = {p.name: digest(p) for p in (output / 'bin').iterdir() if p.is_file()}
    job_path.write_text(json.dumps(job, indent=2) + '\n', encoding='utf-8')
    for folder in ('bin', 'python/bin', 'lib'):
        if (output / folder).is_dir():
            for path in (output / folder).iterdir():
                if path.is_file():
                    path.chmod(0o755)
    if tar_path is not None:
        def unix_mode(member):
            name = PurePosixPath(member.name)
            executable = (name.suffix == '.sh' or name.parts[:1] == ('bin',)
                          or name.parts[:2] == ('python', 'bin')
                          or (name.parts[:1] == ('lib',) and name.name.startswith('ld-linux')))
            member.mode = 0o755 if member.isdir() or executable else 0o644
            return member
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        with tar_path.open('xb') as stream:
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                archive.add(output, arcname='.', filter=unix_mode)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New board runtime folder; omitted: readiness only')
    parser.add_argument('--tar', type=Path, help='New tar.gz with Unix permissions; requires --output')
    parser.add_argument('--allow-incomplete', action='store_true', help='Inspection staging only; start remains blocked')
    args = parser.parse_args(argv)
    if args.tar is not None and args.output is None:
        parser.error('--tar requires --output')
    result = package(args.output, args.allow_incomplete, args.tar) if args.output else readiness()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
