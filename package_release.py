"""Build one deployment folder and ZIP: PC console, Fudan runtime and 7100 boot."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import shutil
import tempfile
import zipfile

from tools import package_field, prepare_board

ROOT = Path(__file__).resolve().parent


def build(output, archive, python_home=package_field.DEFAULT_PYTHON,
          cxx=package_field.DEFAULT_CXX, plan=False):
    output, archive = Path(output).resolve(), Path(archive).resolve()
    if output.exists() or archive.exists():
        raise FileExistsError('Choose a new output folder and ZIP path')
    if output == ROOT or (ROOT in output.parents and output.parts[len(ROOT.parts):len(ROOT.parts)+1] != ('dist',)):
        raise ValueError('Use an output outside the source tree or inside dist/')
    if archive == output or output in archive.parents:
        raise ValueError('ZIP must be outside the deployment folder')
    boot = ROOT / 'assets/fpga7100/BOOT.bin'
    if not boot.is_file():
        raise FileNotFoundError('Missing 7100 boot image in assets/fpga7100/BOOT.bin')
    board = prepare_board.readiness()
    if not board['ready']:
        raise RuntimeError('Board dependencies are incomplete: ' + ', '.join(board['missing']))
    pc = SimpleNamespace(output=output, zip=None, python_home=Path(python_home),
                         cxx=cxx, plan=plan)
    if plan:
        package_field.build(pc)
        return {'package': archive.name,
                'components': ['PC console', 'Fudan runtime', '7100 BOOT.bin', 'Documentation'],
                'raw_adc_viewer': False, 'filesystem_changes': False}
    package_field.build(pc)
    # A Linux tar preserves executable permissions through Windows extraction.
    with tempfile.TemporaryDirectory(prefix='aoa-board-', dir=output.parent) as staging:
        prepare_board.package(Path(staging) / 'V2.0', tar_path=output / 'Board/V2.0.tar.gz')
    (output / 'FPGA7100').mkdir()
    shutil.copyfile(boot, output / 'FPGA7100/BOOT.bin')
    (output / 'README.md').write_text(
        '# AOA System V2.0\n\n'
        '双击 `start_pc.cmd` 启动上位机。\n\n'
        '完整使用说明见 [Documentation/GETTING_STARTED.md](Documentation/GETTING_STARTED.md)。\n'
        '板端安装与构建说明见 [Documentation/BUILD_AND_DEPLOY.md](Documentation/BUILD_AND_DEPLOY.md)。\n',
        encoding='utf-8')
    docs = output / 'Documentation'
    docs.mkdir(exist_ok=True)
    for source in sorted((ROOT / 'Documentation').glob('*.md')):
        shutil.copyfile(source, docs / source.name)
    archive.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                bundle.write(path, output.name + '/' + path.relative_to(output).as_posix())
                count += 1
    return {'package': str(archive), 'files': count, 'bytes': archive.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New deployment folder')
    parser.add_argument('--zip', type=Path, help='Defaults to AOA_System_V2.0.zip beside output')
    parser.add_argument('--python-home', type=Path, default=package_field.DEFAULT_PYTHON)
    parser.add_argument('--cxx', default=package_field.DEFAULT_CXX)
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    archive = args.zip or args.output.resolve().parent / 'AOA_System_V2.0.zip'
    print(json.dumps(build(args.output, archive, args.python_home, args.cxx, args.plan),
                     indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
