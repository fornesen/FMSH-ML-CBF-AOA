"""[启动修正] Verify or load the paired PL before any PS/NPU DMA starts."""
import argparse
import fcntl
import hashlib
import mmap
import os
from pathlib import Path
import shutil
import struct

EXPECTED_VERSION = 0x00010001
EXPECTED_SHA = '587baf73244a7173af007cbf1b1d57b8c85fbb7db6b09cc2ef99f0da92638458'
ROOT = Path(__file__).resolve().parents[1]
MANAGER = Path('/sys/class/fpga_manager/fpga0')
LOCK = Path('/run/lock/aoa-pl.lock')


def image_path():
    image = ROOT / 'pl/aoa_console.bin'
    if hashlib.sha256(image.read_bytes()).hexdigest() != EXPECTED_SHA:
        raise RuntimeError('PL image SHA256 mismatch')
    return image


def read_version():
    if (MANAGER / 'state').read_text().strip() != 'operating':
        return None
    with open('/dev/mem', 'rb', buffering=0) as memory:
        with mmap.mmap(memory.fileno(), 4096, access=mmap.ACCESS_READ,
                       offset=0x400c0000) as regs:
            return struct.unpack_from('<I', regs, 0xc0)[0]


def lock_file(mode):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK.open('a')
    try:
        fcntl.flock(handle, mode | fcntl.LOCK_NB)
        return handle
    except BaseException:
        handle.close()
        raise RuntimeError('PL is in use; stop the application before reconfiguration')


def reject_active_dma():
    # [启动修正] Check every deployment, including orphaned children of a stopped manager.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            name = (proc / 'exe').resolve(strict=True).name
        except OSError:
            continue
        names = {name}
        if name.startswith('ld-'):
            try:
                names.update(Path(arg.decode()).name for arg in (proc/'cmdline').read_bytes().split(b'\0') if arg)
            except (OSError, UnicodeDecodeError):
                continue
        if names.intersection(('icraft_aoa_ml3', 'icraft_npu_yolo')):
            raise RuntimeError('Stop the hardware application first: PID ' + proc.name)


def ensure_locked(force=False):
    image = image_path()
    version = read_version()
    print('PL version: ' + ('unconfigured' if version is None else '0x%08x' % version), flush=True)
    if version == EXPECTED_VERSION and not force:
        print('Paired PL version and image SHA256 verified; no reload', flush=True)
        return
    reject_active_dma()
    firmware = Path('/lib/firmware/aoa_console.bin')
    temporary = firmware.with_name('.aoa_console.bin.%d.tmp' % os.getpid())
    try:
        shutil.copyfile(image, temporary)
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != EXPECTED_SHA:
            raise RuntimeError('Firmware copy SHA256 mismatch')
        os.replace(temporary, firmware)
    finally:
        temporary.unlink(missing_ok=True)
    (MANAGER / 'flags').write_text('0\n')
    (MANAGER / 'firmware').write_text(firmware.name + '\n')
    version = read_version()
    if version != EXPECTED_VERSION:
        raise RuntimeError('Loaded PL version mismatch: %r' % version)
    print('Loaded and verified PL 0x%08x' % version, flush=True)


def prepare_runtime():
    # [启动修正] Exclusive preparation becomes a shared lifetime lease. Forced reload
    # cannot race with DMA startup; the manager holds this handle until children exit.
    lease = lock_file(fcntl.LOCK_EX)
    try:
        reject_active_dma()
        ensure_locked()
        fcntl.flock(lease, fcntl.LOCK_SH)
        return lease
    except BaseException:
        lease.close()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true', help='Force reload while stopped')
    group.add_argument('--ensure', action='store_true', help='Load only if the version differs')
    args = parser.parse_args()
    image_path()
    with lock_file(fcntl.LOCK_SH) as lock:
        version = read_version()
        if args.apply or (args.ensure and version != EXPECTED_VERSION):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('PL is in use; stop the application before reconfiguration')
            ensure_locked(force=args.apply)
        elif version != EXPECTED_VERSION:
            raise RuntimeError('PL version mismatch; stop services, then run --ensure')
        else:
            print('PL version: 0x%08x; paired image SHA256 verified' % version)


if __name__ == '__main__':
    main()
