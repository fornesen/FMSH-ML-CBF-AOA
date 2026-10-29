"""Run paired PS/PL, publish through the local hub, and retain unacknowledged archives."""
import asyncio
import csv
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.archive_io import write_json
from common.frequency_compensation import select as select_compensation, csv_text
from archive_delivery import ArchiveDelivery, PcFirstArchive, temporary_bytes
from load_pl import prepare_runtime  # [启动修正] PL版本检查与DMA生命周期互斥。
from telemetry_hub import Hub


class HubService:
    """One board-owned publisher serves every connected PC independently."""
    def __init__(self):
        self.ready = threading.Event()
        self.error = None
        self.loop = None
        self.stopped = None
        self.archive_endpoints = ()
        self.thread = threading.Thread(target=self.run, name='telemetry-hub', daemon=True)

    def run(self):
        async def serve():
            hub = Hub(on_receivers=lambda value: setattr(self, 'archive_endpoints', value))
            self.loop = asyncio.get_running_loop()
            self.stopped = asyncio.Event()
            try:
                await hub.start(ingress_host='127.0.0.1', ingress_port=9001,
                                client_host='0.0.0.0', client_port=9000)
                self.ready.set()
                await self.stopped.wait()
            finally:
                await hub.close()
        try:
            asyncio.run(serve())
        except Exception as error:
            self.error = str(error)
        finally:
            self.ready.set()

    def start(self):
        self.thread.start()
        if not self.ready.wait(5) or self.error:
            self.stop()
            raise RuntimeError('Telemetry hub did not start: ' + (self.error or 'timeout'))

    def stop(self):
        if self.loop and self.stopped and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.stopped.set)
        if self.thread.is_alive():
            self.thread.join(timeout=5)


class LogPump(threading.Thread):
    """Drain pipes into two bounded RAM log segments; logs are not raw archives."""
    def __init__(self, name, pipe, spool, failed):
        super().__init__(daemon=True)
        self.name, self.pipe, self.spool, self.failed = name, pipe, spool, failed
        self.index, self.buffer = 0, bytearray()

    def flush(self):
        if not self.buffer:
            return
        path = self.spool/(self.name + '_stdout_' + str(self.index % 2) + '.txt')
        with path.open('wb') as out:
            out.write(self.buffer)
            out.flush()
            os.fsync(out.fileno())
        self.buffer.clear()
        self.index += 1

    def run(self):
        try:
            for line in iter(self.pipe.readline, b''):
                self.buffer.extend(line)
                if b'Error:' in line or b'NPU_ERROR' in line:
                    self.failed.set()
                if len(self.buffer) >= 128 * 1024:
                    self.flush()
            self.flush()
        except Exception:
            self.failed.set()
            raise
        finally:
            self.pipe.close()



def wait_for_source(host, port, stopped, stop_file):
    """Wait for TCP availability without issuing START or touching PL/ADC data."""
    attempts = 0
    while not stopped.is_set() and not stop_file.exists():
        try:
            with socket.create_connection((host, port), timeout=1):
                print('SOURCE_TCP_READY %s:%s' % (host, port), flush=True)
                return True
        except OSError as error:
            attempts += 1
            if attempts == 1 or attempts % 5 == 0:
                print('WAITING_FOR_SOURCE %s:%s: %s' % (host, port, error), flush=True)
        # stop.sh and SIGTERM remain effective while the 7100 is powered off.
        stopped.wait(2)
    return False


def runtime_file(base, value):
    if not isinstance(value, str):
        raise ValueError('Runtime file must be a path string')
    base = Path(base).resolve()
    path = (base / value).resolve()
    if base not in path.parents:
        raise ValueError('Runtime file must stay inside its V2.0 folder: ' + str(path))
    if not path.is_file():
        raise ValueError('Runtime file not found: ' + str(path))
    return path


def archive_enabled(path):
    try:
        return json.loads(path.read_text()).get('enabled') is True
    except FileNotFoundError:
        return False


def compensation_path(base, job):
    value = job.get('compensation_file', '')
    return None if value in (None, '') else runtime_file(base, value)


def compensation_config(base, job):
    bank_name = job.get('compensation_bank')
    if bank_name:
        bank_path = runtime_file(base, bank_name)
        payload = bank_path.read_bytes()
        selection = select_compensation(json.loads(payload), job['rf_hz'])
        selection.pop('coefficients_applied', None)
        return None, dict(selection, bank_file=bank_name,
                          bank_sha256=hashlib.sha256(payload).hexdigest())
    return compensation_path(base, job), None


def main():
    job_path = Path(sys.argv[1]).resolve()
    job = json.loads(job_path.read_text())
    # A self-contained deployment or test copy owns its own config, binaries and results.
    root = Path(__file__).resolve().parents[1]
    base = (root / job.get('runtime_base', '.')).resolve()
    binary = (root / job.get('bin_dir', 'bin')).resolve()
    tag = job['tag']
    results_subdir = job.get('results_subdir', 'results')
    if ((root / job.get('project_root', '.')).resolve() != root
            or results_subdir not in ('runs', 'results')
            or (base != root and root not in base.parents)
            or (binary != root and root not in binary.parents)
            or not tag.startswith('moving_')
            or not tag.replace('_', '').isalnum()):
        raise ValueError('Invalid workspace/session')
    for name, expected in job['elf_sha256'].items():
        if hashlib.sha256((binary/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Executable checksum changed: ' + name)
    with (base/'config/array.csv').open() as file:
        coordinates = [[float(r['x_m']), float(r['y_m'])] for r in csv.DictReader(file)]
    if len(coordinates) != 6 or any(abs(coordinates[i][j] - job['array_xy_m'][i][j]) > 1e-12
                                   for i in range(6) for j in range(2)):
        raise ValueError('Array geometry changed')
    compensation, selection = compensation_config(base, job)
    # [启动修正] 保持此锁直到采集/NPU子进程退出，阻止运行中重新配置PL。
    pl_lease = prepare_runtime()
    stat = os.statvfs('/dev/shm')
    if stat.f_bavail * stat.f_frsize < 256 * 1024**2:
        raise RuntimeError('Need 256 MiB free RAM filesystem')
    out = root/results_subdir/tag
    out.mkdir(exist_ok=False)
    if type(job.get('record_raw', False)) is not bool:
        raise ValueError('record_raw must be boolean')
    # A previous run's setting never enables recording on restart. The PS
    # applies the runtime switch at batch boundaries; AOA originals stay in RAM.
    job = dict(job, record_raw=False, archive_switch_default=False)
    ram = Path(tempfile.mkdtemp(prefix=tag + '_', dir='/dev/shm'))
    for name in ('aoa', 'npu', 'logs'):
        (ram/name).mkdir()
    if selection is not None:
        compensation = ram / 'channel_compensation.csv'
        compensation.write_text(csv_text(selection), encoding='ascii')
    hub = HubService()
    archive_root = ram/'pending'
    gzip_level = job.get('archive_gzip_level', 1)
    if type(gzip_level) is not int or not 0 <= gzip_level <= 9:
        raise ValueError('archive_gzip_level must be an integer in 0..9')
    os.environ['AOA_ARCHIVE_GZIP_LEVEL'] = str(gzip_level)
    archives = [PcFirstArchive(ram/'aoa', archive_root/'aoa', 'aoa')]
    delivery = ArchiveDelivery(archive_root)
    pc_cancel = threading.Event()
    for archive in archives:
        archive.configure_pc_delivery(lambda: hub.archive_endpoints, pc_cancel,
                                      lambda: archive_enabled(ram/'archive_control.json'))
    # [7100实采切换] 真实板只使用原始START；模拟标签只在显式启用时加入。
    env = dict(os.environ, AOA_CONSOLE_HOST='127.0.0.1', AOA_CONSOLE_PORT='9001',
               AOA_ARCHIVE_BACKLOG='1', AOA_LEGACY_SIGNAL_PREVIEW='0',
               AOA_ARCHIVE_SWITCH='1', AOA_ARCHIVE_CONTROL_PATH=str(ram/'archive_control.json'))
    env.pop('AOA_SIM_TAGGED_START', None)
    env['AOA_ARCHIVE_MIN_FREE_MB'] = '64'
    env['AOA_BEAM_AUTO_START'] = '1' if job.get('beam_auto_start', False) else '0'
    if job.get('sim_tagged_start', False):
        env['AOA_SIM_TAGGED_START'] = '1'
    ipc = '/' + tag
    npu = [str(binary/'icraft_npu_yolo'), '--ipc-name', ipc, '--model-json',
           str(runtime_file(base, job.get('npu_model_json', 'models/fm_sixsum_fm_pm_v8_ZG.json'))), '--model-raw',
           str(runtime_file(base, job.get('npu_model_raw', 'models/fm_sixsum_fm_pm_v8_ZG.raw'))), '--backend', 'npu',
           '--output-dir', str(ram/'npu'), '--wait-seconds', '0']
    aoa = [str(binary/'icraft_aoa_ml3'), '--tcp-host', job.get('tcp_host', '192.168.10.50'),
           '--tcp-port', str(job.get('tcp_port', 7)),
           '--adc-encoding', job.get('adc_encoding', 'twos-complement'),
           '--adc-bits', str(job.get('adc_bits', 12)),
           # RF is the received carrier, not the external analog mixer LO.
           # Select direction bins in the PL complex-baseband frequency domain.
           '--rf-hz', str(job.get('rf_hz', 433e6)),
           '--band-min-hz', str(job.get('band_min_hz', -250e3)),
           '--band-max-hz', str(job.get('band_max_hz', 250e3)),
           '--capture-rounds', '4', '--paths', '2',
           '--array', str(base/'config/array.csv'), '--ipc-name', ipc, '--max-batches', '0',
           '--interval-ms', str(job.get('interval_ms', 800)), '--save-results',
           '--output-prefix', str(ram/'aoa/aoa'), '--compact-results',
           '--processing-mode', job.get('processing_mode', 'angle6'),
           '--joint-paths', str(job.get('joint_paths', 3))]
    if compensation is not None:
        aoa += ['--compensation', str(compensation)]
    if not job.get('scan_verify', True):
        aoa.append('--no-scan-verify')
    for key, flag in (('scan_ml_weight', '--scan-ml-weight'),
                      ('scan_max_checks', '--scan-max-checks'),
                      ('scan_candidate_gap_db', '--scan-candidate-gap-db'),
                      ('scan_min_snr_db', '--scan-min-snr-db')):
        if key in job:
            aoa += [flag, str(job[key])]
    # PS removes ADC DC before PL. Paired PL handles startup64/Hann before the ML FFT.
    # The ELF verifies the actual PL version and prevents a second ML window.
    if job.get('adc_remove_dc', False):
        aoa.append('--adc-remove-dc')
    aoa += ['--ml-startup-samples', str(job.get('ml_startup_samples', 64)),
            '--ml-fft-window', job.get('ml_fft_window', 'hann')]
    # [合成IQ观测新增] 默认关闭，可由上位机在批次边界独立使能。
    if job.get('observe_waveform', False):
        aoa.append('--observe-waveform')
    if job.get('observe_spectrum', False):
        aoa.append('--observe-spectrum')
    if job.get('acquisition_mode') == 'locate':
        aoa.append('--no-ipc')
    # Context exists before a later runtime enable. Each capture additionally
    # freezes its actual Options and beam/archive state in its paired packet.
    context = out/'recording_context.json'
    write_json(context, {'schema': 'aoa.recording_context.v1', 'job': job,
                            'root': str(root), 'aoa_command': aoa, 'npu_command': npu,
                            'array_sha256': hashlib.sha256((base/'config/array.csv').read_bytes()).hexdigest(),
                            'compensation_file': str(compensation) if compensation else None,
                            'compensation_sha256': hashlib.sha256(compensation.read_bytes()).hexdigest() if compensation else None,
                            'compensation_selection': selection,
                            'capture_storage': 'registered_PC_archive_receiver',
                            'temporary_ram': str(ram), 'max_pending_aoa_batches': 1,
                            'ram_limit_bytes': 128 * 1024**2, 'retain_after_pc_ack': False,
                            'archive_switch_default': False,
                            'scope': 'paired_ADC_PL_PS_only; NPU telemetry preserved; bounded_RAM_logs'})
    env['AOA_RECORDING_CONTEXT'] = str(context)
    stopped, failed = threading.Event(), threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    children, pumps = [], []
    reason = 'requested stop'
    try:
        hub.start()
        for archive in archives:
            archive.thread.start()
        # Publish the manager PID before waiting so start/status/stop remain usable.
        session = {'job': job, 'manager_pid': os.getpid(), 'ram': str(ram),
                   'state': 'waiting_for_source', 'npu_pid': None, 'aoa_pid': None,
                   'npu_command': npu, 'aoa_command': aoa}
        write_json(out/'session.json', session)
        if wait_for_source(job.get('tcp_host', '192.168.10.50'),
                           int(job.get('tcp_port', 7)), stopped, out/'STOP'):
            if stopped.is_set() or (out/'STOP').exists():
                return 0
            for name, command in (('npu', npu), ('aoa', aoa)):
                launch = [str(root/'lib/ld-linux-aarch64.so.1'), '--library-path', str(root/'lib')] + command
                if name == 'npu':
                    launch = ['nice', '-n', '10'] + launch
                process = subprocess.Popen(launch, env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           cwd=ram/'npu' if name == 'npu' else None,
                                           start_new_session=True)
                children.append(process)
                pump = LogPump(name, process.stdout, ram/'logs', failed)
                pumps.append(pump)
                pump.start()
            session.update(state='running', npu_pid=children[0].pid, aoa_pid=children[1].pid)
            write_json(out/'session.json', session)
            print('CONTINUOUS_READY ' + str(out), flush=True)
        while children and not stopped.wait(.25) and not (out/'STOP').exists():
            if any(child.poll() is not None for child in children):
                reason = 'hardware process exited'
                break
            if failed.is_set() or hub.error or any(a.error for a in archives):
                reason = 'runtime/storage error; unacknowledged originals retained in RAM'
                break
            stat = os.statvfs(ram)
            if stat.f_bavail * stat.f_frsize < 64 * 1024**2:
                reason = 'RAM low; acquisition stopped to preserve pending data'
                break
            if temporary_bytes(ram) > 128 * 1024**2:
                reason = '128 MiB recording RAM limit; acquisition stopped, no board disk accumulation'
                break
    except Exception:
        reason = 'runtime exception; unacknowledged originals retained in RAM'
        raise
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        for pump in pumps:
            pump.join()
        # Writers have exited; only now may the final partial AOA batch be archived.
        for archive in archives:
            archive.done.set()
        # Disabled archives remain paused in RAM. Shutdown cancels their wait;
        # only a PC ACK can release originals.
        deadline = time.monotonic() + 10
        for archive in archives:
            if archive.thread.ident is not None:
                archive.thread.join(timeout=max(0, deadline-time.monotonic()))
        pc_cancel.set()
        for archive in archives:
            if archive.thread.ident is not None:
                archive.thread.join()
        hub.stop()
        state = {'reason': reason, 'ram': str(ram),
                 'archive_switch_managed': True,
                 'exit_codes': [c.returncode for c in children],
                 'committed': {a.category: a.committed for a in archives},
                 'archive_errors': [a.error for a in archives],
                 'pending_archives': len(delivery.pending_manifests()),
                 'delivery_error': [a.delivery_error for a in archives],
                 'remaining_files': [str(p.relative_to(ram)) for p in ram.rglob('*') if p.is_file()]}
        state['pending_data'] = [name for name in state['remaining_files']
                                 if name.startswith(('aoa/', 'pending/'))]
        state['ram_retained'] = reason != 'requested stop' or bool(state['pending_data']) or any(a.error for a in archives)
        if not state['ram_retained']:
            shutil.rmtree(ram)
        write_json(out/'stop.json', state)
        print('CONTINUOUS_STOP ' + json.dumps(state), flush=True)
    return 0 if (reason == 'requested stop' and not any(a.error for a in archives)
                 and not state['pending_data']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
