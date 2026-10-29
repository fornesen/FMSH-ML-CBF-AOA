#!/usr/bin/env python3
"""SSH wizard: L/O/R, 425/445 MHz relative phase and effective-delay calibration."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from calibration_math import (array_text, check_adc, extract_frame, read_array, response, solve)
from delay_math import RF_FREQUENCIES, shared_reference, solve_delay, tone_settings, usable_channels


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.%d.tmp' % os.getpid())
    try:
        with temporary.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temporary), str(path))
        if os.name == 'posix':
            directory = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def save_json(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8'))


def wait_for_enter(prompt):
    """Only a new Enter after this prompt can release a capture.

    Read the controlling SSH terminal directly: input() may already have read
    ahead into Python's stdin buffer. Flush queued terminal input before showing
    the prompt, not after, so a deliberate new Enter is never discarded.
    """
    import termios
    terminal = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
    try:
        termios.tcflush(terminal, termios.TCIFLUSH)
        print(prompt, end='', flush=True)
        while True:
            line = bytearray()
            while True:
                char = os.read(terminal, 1)
                if not char:
                    raise EOFError('SSH 终端输入已关闭，未开始采集')
                if char == b'\n':
                    break
                line.extend(char)
            if not line.strip():
                return
            print('请摆好发射天线后，只按回车开始采集：', end='', flush=True)
    finally:
        os.close(terminal)


def service(root, action):
    command = ([str(root / (action + '.sh'))] if action in ('start', 'stop') else
               [str(root / 'python.sh'), str(root / 'runtime/service.py'), action])
    result = subprocess.run(command, cwd=str(root), capture_output=True,
                            text=True, check=True, timeout=130)
    return json.loads(result.stdout) if action == 'status' else result.stdout.strip()


def stopped_service(root):
    print(service(root, 'stop'), flush=True)
    deadline = time.monotonic() + 130
    while service(root, 'status')['running']:
        if time.monotonic() > deadline:
            raise RuntimeError('正常服务未退出；本次没有启动独立采集，请检查 status.sh')
        time.sleep(0.5)


@contextlib.contextmanager
def exclusive_session(root):
    """Same PL lifetime lock as the manager; never race a second collector."""
    import fcntl
    lock_path = root / 'data/calibration/.wizard.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as wizard_lock:
        fcntl.flock(wizard_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        was_running = service(root, 'status')['running']
        lease = None
        try:
            if was_running:
                print('暂停正常采集，标定结束后自动恢复。', flush=True)
                stopped_service(root)
            spec = importlib.util.spec_from_file_location('calibration_pl', root / 'runtime/load_pl.py')
            loader = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(loader)
            # Retain EX throughout all positions: manager startup/reload must not interleave.
            lease = loader.lock_file(fcntl.LOCK_EX)
            loader.reject_active_dma()
            loader.image_path()
            if loader.read_version() != 0x00010001:
                raise RuntimeError('需要已加载的 PL Hann64 0x00010001；此工具不会下载位流')
            yield
        finally:
            if lease:
                lease.close()
            if was_running and not service(root, 'status')['running']:
                print('恢复正常采集：' + service(root, 'start'), flush=True)


def settings_from_job(job):
    settings = {'rf_hz': float(job.get('rf_hz', 445e6)),
                'sample_rate_hz': float(job.get('sample_rate_hz', 100e6)),
                'dds_hz': float(job.get('dds_hz', 12.5e6)), 'phase_sign': 1,
                'band_min_hz': float(job.get('band_min_hz', -550e3)),
                'band_max_hz': float(job.get('band_max_hz', -450e3)),
                'tcp_host': job.get('tcp_host', '192.168.10.50'),
                'tcp_port': int(job.get('tcp_port', 7)),
                'adc_bits': int(job.get('adc_bits', 12)),
                'adc_encoding': job.get('adc_encoding', 'twos-complement')}
    if (not all(math.isfinite(settings[k]) for k in ('rf_hz', 'sample_rate_hz', 'dds_hz', 'band_min_hz', 'band_max_hz'))
            or settings['rf_hz'] <= 0 or settings['sample_rate_hz'] <= 0
            or not -settings['sample_rate_hz'] / 8 < settings['band_min_hz'] < settings['band_max_hz'] < settings['sample_rate_hz'] / 8):
        raise ValueError('Invalid deployed RF/sample-rate/search-band configuration')
    # run_board.py currently uses PS default +1. Refuse an unimplemented job override.
    if job.get('phase_sign', 1) != 1:
        raise ValueError('Deployment phase-sign override needs explicit review')
    if job.get('sim_tagged_start', False) or job.get('source_kind') != 'zynq7100_physical_adc':
        raise ValueError('本程序只接受真实 7100 的独立六路 ADC 数据')
    if not job.get('adc_remove_dc', False) or job.get('ml_startup_samples') != 64 or job.get('ml_fft_window') != 'hann':
        raise ValueError('需当前 ADC 去直流 / PL 屏蔽64点并加 Hann 窗的正式配置')
    return settings


def prompt_legacy_points(geometry, settings):
    defaults = [[geometry[0][0], 3.0, 0.0], [0.0, 3.0, 0.0], [geometry[-1][0], 3.0, 0.0]]
    print('''
                         +Y：正前方，0°
                                ↑
         负角度                 │                 正角度
                                │
   A                            B                            C
   ●                            ●                            ●   Y = 3 m
   ┊                            ┊                            ┊
   ┊                            ┊                            ┊
  CH0       CH1       CH2        O        CH3       CH4       CH5 → +X
   ●─────────●─────────●─────────┼─────────●─────────●─────────●   Y = 0
%s
  └────── 第一组 G1 ──────┘              └────── 第二组 G2 ──────┘
''' % ''.join('%10.5f' % x for x in [geometry[0][0], geometry[1][0], geometry[2][0], 0.0,
                                    geometry[3][0], geometry[4][0], geometry[5][0]]))
    print('图为建议默认摆位（不按比例）：A/B/C 同高，Z=0；使用默认值请摆到对应位置。')
    print('坐标单位 m；回车用默认值，或输入 X Y [Z]（Z 为相对接收天线的高度，省略为0）。')
    points = []
    for name, default in zip('ABC', defaults):
        while True:
            text = input('%s 点 [%.6f %.3f %.3f]：' % (name, *default)).strip()
            try:
                xyz = [float(v) for v in text.replace(',', ' ').replace('，', ' ').split()] if text else list(default)
                if len(xyz) == 2:
                    xyz.append(0.0)
                response(geometry, xyz, settings['rf_hz'], settings['phase_sign'])
                if any(xyz == p['xyz_m'] for p in points):
                    raise ValueError('三个校准点不能重合')
                points.append({'name': name, 'xyz_m': xyz, 'frames': [],
                               'coordinate_source': 'entered' if text else 'default_suggested'})
                break
            except ValueError as error:
                print('坐标无效：' + str(error))
    return points


def prompt_points(geometry, settings, lo_hz=433e6):
    defaults = [[min(a[0] for a in geometry), 0.0, 0.0],
                [0.0, 0.0, 0.0], [max(a[0] for a in geometry), 0.0, 0.0]]
    print('''
                         全部同高，Y=0

  L / CH0    CH1    CH2       O       CH3    CH4    CH5 / R
     ◎────────●──────●───────◎────────●──────●────────◎
     └──────────────── 接收阵列 ────────────────────┘
      L、O、R 为发射校准点，接收天线保持不动
''')
    print('接收天线 X / m：' + '  '.join('CH%d=%+.5f' % (ch, p[0]) for ch, p in enumerate(geometry)))
    print('默认 L 在CH0左侧0 m，O 为中心，R 在CH5右侧0 m；这是摆位坐标，不是已测坐标。')
    print('每点依次设置 425、445 MHz；外部共同本振保持 %.3f MHz。' % (lo_hz / 1e6))
    print('单位 m；回车用默认值，或输入实测 X Y [Z]。与该点距离不足0.5 m的通道不参与拟合。')
    points = []
    for name, default in zip(('L', 'O', 'R'), defaults):
        while True:
            text = input('%s 点 [%.6f %.3f %.3f]：' % (name, *default)).strip()
            try:
                xyz = [float(v) for v in text.replace(',', ' ').replace('，', ' ').split()] if text else list(default)
                if len(xyz) == 2:
                    xyz.append(0.0)
                channels = usable_channels(geometry, xyz)
                if name == 'L' and xyz[0] > min(a[0] for a in geometry):
                    raise ValueError('L 必须在CH0位置或更左侧')
                if name == 'R' and xyz[0] < max(a[0] for a in geometry):
                    raise ValueError('R 必须在CH5位置或更右侧')
                if any(xyz == p['xyz_m'] for p in points):
                    raise ValueError('校准点不能重合')
                points.append({'name': name, 'xyz_m': xyz,
                               'coordinate_source': 'entered' if text else 'default_suggested',
                               'tones': [{'settings': tone_settings(settings, f, lo_hz), 'frames': []}
                                         for f in RF_FREQUENCIES]})
                print('%s 点参与通道：%s' % (name, ' '.join('CH%d' % ch for ch in channels)))
                break
            except ValueError as error:
                print('坐标无效：' + str(error))
    reference, unused = shared_reference(geometry, points)
    print('三点共同测量参考 CH%d；最终结果统一换算成 CH0 参考。' % reference)
    return points


def capture_command(root, prefix, array_path, settings):
    command = [str(root / 'lib/ld-linux-aarch64.so.1'), '--library-path', str(root / 'lib'),
               str(root / 'bin/icraft_aoa_ml3'), '--once', '--no-ipc', '--save-results',
               '--compact-results', '--capture-rounds', '4', '--processing-mode', 'position', '--paths', '1',
               '--array', str(array_path), '--output-prefix', str(prefix), '--adc-remove-dc',
               '--ml-startup-samples', '64', '--ml-fft-window', 'hann']
    for name, key in [('tcp-host', 'tcp_host'), ('tcp-port', 'tcp_port'), ('rf-hz', 'rf_hz'),
                      ('sample-rate', 'sample_rate_hz'), ('dds-hz', 'dds_hz'), ('phase-sign', 'phase_sign'),
                      ('band-min-hz', 'band_min_hz'), ('band-max-hz', 'band_max_hz'),
                      ('adc-bits', 'adc_bits'), ('adc-encoding', 'adc_encoding')]:
        command.extend(['--' + name, str(settings[key])])
    return command


def run_capture(command, root, log_path):
    # No telemetry controls, NPU IPC, auto beam, simulator tag, or archive queue in this session.
    env = {key: value for key, value in os.environ.items() if not key.startswith('AOA_')}
    with log_path.open('xb') as log:
        child = subprocess.Popen(command, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            status = child.wait(timeout=180)
        except BaseException:
            # Ask the existing ELF to unwind DMA before releasing the lifetime lease.
            child.send_signal(signal.SIGTERM)
            try:
                child.wait(timeout=40)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            raise
    if status:
        raise RuntimeError('采集未完成，见 ' + str(log_path))


def validate_metadata(meta, settings, geometry):
    if (meta.get('capture_mode') != 'hardware' or not meta.get('capture_complete')
            or not meta.get('batch_capture_complete') or meta.get('completed_capture_rounds') != 4
            or meta.get('capture_round_count') != 4 or meta.get('pl_version') != 0x00010001
            or meta.get('npu_handoff_status') != 'disabled' or meta.get('error')):
        raise ValueError('采集批次/PL版本/隔离状态不符合校准要求')
    pre = meta.get('preprocessing', {})
    if (pre.get('fft_payload_preprocessing') != 'pl-hann64' or pre.get('ml_backend') != 'pl'
            or pre.get('ml_startup_samples') != 64 or pre.get('ml_retained_samples') != 4032
            or pre.get('ml_window') != 'periodic_hann' or not pre.get('adc_dc_applied')):
        raise ValueError('预处理来源不一致；不能把旧 raw FFT 当作新 PL Hann64')
    for key, expected in [('rf_carrier_hz', settings['rf_hz']), ('sample_rate_hz', settings['sample_rate_hz']),
                          ('dds_requested_hz', settings['dds_hz']), ('phase_sign', settings['phase_sign']),
                          ('adc_bits', settings['adc_bits']), ('adc_encoding', settings['adc_encoding']),
                          ('adc_endian', 'little')]:
        if meta.get(key) != expected:
            raise ValueError('采集参数不一致：' + key)
    if any(len(meta.get('positions_' + axis + '_m', [])) != 6 for axis in ('x', 'y')):
        raise ValueError('缺少六通道坐标')
    if any(abs(meta['positions_' + axis + '_m'][ch] - ant[i]) > 1e-9
           for ch, ant in enumerate(geometry) for i, axis in enumerate(('x', 'y'))):
        raise ValueError('采集天线坐标不一致')
    captures = meta.get('captures', [])
    if len(captures) != 4 or [c.get('index') for c in captures] != [0, 1, 2, 3]:
        raise ValueError('Incomplete or duplicate capture rounds')
    for capture in captures:
        if (not capture.get('complete') or capture.get('error') or not capture.get('guard_checked')
                or not capture.get('guard_ok') or capture.get('written_slots') != 24576
                or capture.get('adc_received_bytes') != 196608 or not capture.get('adc_dc_applied')):
            raise ValueError('Incomplete or invalid capture round')


def collect_legacy(root, directory, session, batches):
    identity_array = directory / 'array_uncalibrated.csv'
    atomic_bytes(identity_array, array_text(session['geometry'], [1 + 0j] * 6).encode('ascii'))
    for point in session['points']:
        session['progress'] = {'state': 'waiting_for_enter', 'point': point['name']}
        save_json(directory / 'session.json', session)
        wait_for_enter('\n把发射天线移到 %s (X=%.6f, Y=%.6f, Z=%.6f m)，保持单音及本振锁定。就绪按回车：' %
                       (point['name'], *point['xyz_m']))
        point['confirmed_unix_s'] = time.time()
        session['progress'] = {'state': 'capturing', 'point': point['name']}
        save_json(directory / 'session.json', session)
        print('已收到回车，开始采集 %s 点。' % point['name'], flush=True)
        for batch in range(batches):
            # Includes raw ADC + padded input + raw FFT, not just extracted complex vectors.
            if shutil.disk_usage(directory).free < 64 * 1024**2:
                raise RuntimeError('剩余空间不足 64 MiB，停止并保留已有证据')
            prefix = directory / ('%s_%02d' % (point['name'], batch + 1))
            run_capture(capture_command(root, prefix, identity_array, session['settings']),
                        root, prefix.with_suffix('.log'))
            meta_path = Path(str(prefix) + '_metadata.json')
            meta = json.loads(meta_path.read_text())
            validate_metadata(meta, session['settings'], session['geometry'])
            frames = []
            for index in range(4):
                raw_path = Path(str(prefix) + '_round%d_fft_slots.raw' % (index + 1))
                payload = raw_path.read_bytes()
                frame = extract_frame(payload, session['settings'])
                adc_path = Path(str(prefix) + '_round%d_adc.raw' % (index + 1))
                adc = adc_path.read_bytes()
                frame.update(batch=batch, round=index, run_id=meta['run_id'],
                             raw_file=raw_path.name, raw_sha256=digest(payload),
                             adc_file=adc_path.name, adc_sha256=digest(adc),
                             adc_near_rail_fraction=check_adc(adc, session['settings']),
                             metadata_file=meta_path.name, metadata_sha256=digest(meta_path.read_bytes()))
                frames.append(frame)
            point['frames'].extend(frames)
            save_json(directory / 'session.json', session)
            print('%s：%d/%d 批（每批4帧），最低峰值/底噪 %.1f dB' %
                  (point['name'], batch + 1, batches,
                   min(min(f['peak_to_local_floor_db']) for f in frames)), flush=True)


def collect(root, directory, session, batches):
    if session.get('format_version', 1) == 1:
        return collect_legacy(root, directory, session, batches)
    identity_array = directory / 'array_uncalibrated.csv'
    atomic_bytes(identity_array, array_text(session['geometry'], [1 + 0j] * 6).encode('ascii'))
    for point in session['points']:
        channels = usable_channels(session['geometry'], point['xyz_m'])
        point['used_channels'] = channels
        for tone in point['tones']:
            settings = tone['settings']
            mhz = settings['rf_hz'] / 1e6
            session['progress'] = {'state': 'waiting_for_enter', 'point': point['name'], 'rf_hz': settings['rf_hz']}
            save_json(directory / 'session.json', session)
            wait_for_enter('\n发射点 %s (X=%.6f, Y=%.6f, Z=%.6f m)，设置单音 %.3f MHz，'
                           '本振保持 %.3f MHz 并锁定。就绪按回车：' %
                           (point['name'], *point['xyz_m'], mhz, session['lo_hz'] / 1e6))
            tone['confirmed_unix_s'] = time.time()
            session['progress']['state'] = 'capturing'
            save_json(directory / 'session.json', session)
            print('已收到回车，开始采集 %s / %.0f MHz。' % (point['name'], mhz), flush=True)
            for batch in range(batches):
                if shutil.disk_usage(directory).free < 64 * 1024**2:
                    raise RuntimeError('剩余空间不足 64 MiB，停止并保留已有证据')
                prefix = directory / ('%s_%03d_%02d' % (point['name'], mhz, batch + 1))
                run_capture(capture_command(root, prefix, identity_array, settings), root, prefix.with_suffix('.log'))
                meta_path = Path(str(prefix) + '_metadata.json')
                meta = json.loads(meta_path.read_text())
                validate_metadata(meta, settings, session['geometry'])
                frames = []
                for index in range(4):
                    raw_path = Path(str(prefix) + '_round%d_fft_slots.raw' % (index + 1))
                    payload = raw_path.read_bytes()
                    frame = extract_frame(payload, dict(settings, calibration_channels=channels))
                    adc_path = Path(str(prefix) + '_round%d_adc.raw' % (index + 1))
                    adc = adc_path.read_bytes()
                    frame.update(batch=batch, round=index, run_id=meta['run_id'],
                                 raw_file=raw_path.name, raw_sha256=digest(payload),
                                 adc_file=adc_path.name, adc_sha256=digest(adc),
                                 adc_near_rail_fraction=check_adc(adc, settings),
                                 metadata_file=meta_path.name, metadata_sha256=digest(meta_path.read_bytes()))
                    frames.append(frame)
                tone['frames'].extend(frames)
                save_json(directory / 'session.json', session)
                print('%s / %.0f MHz：%d/%d 批，最低峰值/底噪 %.1f dB' %
                      (point['name'], mhz, batch + 1, batches,
                       min(f['peak_to_local_floor_db'][ch] for f in frames for ch in channels)), flush=True)


def solve_session(session):
    return solve_delay(session) if session.get('format_version', 1) == 2 else solve(session)


def delay_record(session, report, array_hash):
    return {'format_version': 1, 'reference_channel': 0, 'target_rf_hz': report['target_rf_hz'],
            'applies_to_array_sha256': array_hash,
            'measurement': report, 'points_xyz_m': [p['xyz_m'] for p in session['points']],
            'runtime_use': 'measured_target_frequency_phase_only; delay_is_a_measurement_record'}


def install_result(root, directory, session, report):
    if not report['accepted']:
        raise ValueError('不合格校正不能写入')
    active = root / 'config/array.csv'
    if digest(active.read_bytes()) != session['array_sha256']:
        raise RuntimeError('采集中阵列配置被修改；保留结果，不覆盖新配置')
    if digest((root / 'config/job.json').read_bytes()) != session['job_sha256']:
        raise RuntimeError('采集中运行配置被修改；保留结果，不覆盖新配置')
    data = array_text(session['geometry'], [complex(*c) for c in report['correction']]).encode('ascii')
    atomic_bytes(directory / 'array_calibrated.csv', data)
    delay_target = root / 'config/channel_delay.json'
    delay_before = delay_target.read_bytes() if delay_target.exists() else None
    record = {'array_sha256': digest(data), 'previous_sha256': session['array_sha256'],
              'active_file': str(active), 'reference_channel': 0}
    if session.get('format_version') == 2:
        if delay_before is not None:
            atomic_bytes(directory / 'channel_delay_before.json', delay_before)
        measurement = delay_record(session, report, digest(data))
        save_json(directory / 'channel_delay.json', measurement)
        # The array hash binds this measurement to its matching phase correction.
        # A crash between these two writes cannot make an unmatched record valid.
        save_json(delay_target, measurement)
        record.update(delay_sha256=digest(delay_target.read_bytes()),
                      previous_delay_sha256=digest(delay_before) if delay_before is not None else None)
    # Durable backup was saved before the first capture; install is an atomic replacement.
    try:
        atomic_bytes(active, data)
    except BaseException:
        if session.get('format_version') == 2:
            if delay_before is None:
                delay_target.unlink()
            else:
                atomic_bytes(delay_target, delay_before)
        raise
    save_json(directory / 'applied.json', record)


def restore(root, directory):
    directory = directory.resolve()
    record = json.loads((directory / 'applied.json').read_text(encoding='utf-8'))
    original = (directory / 'array_before.csv').read_bytes()
    if digest(original) != record['previous_sha256']:
        raise ValueError('备份哈希不符，未恢复')
    with exclusive_session(root):
        target = root / 'config/array.csv'
        if digest(target.read_bytes()) != record['array_sha256']:
            raise ValueError('当前参数不是这次标定的结果，未覆盖后续修改')
        delay_target = root / 'config/channel_delay.json'
        if 'delay_sha256' in record:
            if not delay_target.exists() or digest(delay_target.read_bytes()) != record['delay_sha256']:
                raise ValueError('当前延迟记录已改变，未覆盖后续修改')
            previous = record['previous_delay_sha256']
            backup = (directory / 'channel_delay_before.json').read_bytes() if previous else None
            if previous and digest(backup) != previous:
                raise ValueError('延迟备份哈希不符，未恢复')
        atomic_bytes(target, original)
        if 'delay_sha256' in record:
            if backup is None:
                delay_target.unlink()
            else:
                atomic_bytes(delay_target, backup)
        save_json(directory / 'restored.json', {'array_sha256': digest(original), 'unix_s': time.time()})
        print('已恢复本次标定前的阵列参数。', flush=True)


def summarize(report):
    print('\n' + ('校准检查通过。' if report['accepted'] else '校准未通过：原参数保持不变。'))
    for reason in report['reasons']:
        print('  ' + reason)
    for ch, (gain, phase) in enumerate(zip(report['correction_gain_db'], report['correction_phase_deg'])):
        print('CH%d：相对增益 %+7.3f dB，相位 %+8.3f°' % (ch, gain, phase))
    if 'delay_principal_ns' in report:
        print('双频等效相对延迟主值 / ns（CH0参考，模 %.1f ns；未辨识物理时延）：' % report['delay_alias_period_ns'])
        for ch, value in enumerate(report['delay_principal_ns']):
            print('  CH%d：%+.4f ns' % (ch, value))
        print('幅度保持原值；运行校正使用 %.0f MHz 实测相位，未启用宽带延迟补偿。' % (report['target_rf_hz'] / 1e6))
        for point in report['points']:
            for tone in point['tones']:
                print('%s / %.0f MHz：留一位置最大相位误差 %.3f°' %
                      (point['name'], tone['rf_hz'] / 1e6, tone['leave_position_out_max_phase_error_deg']))
        return
    for point in report['points']:
        print('%s 独立批次残差：%.5f -> %.5f；留一位置最大残差：%.5f' %
              (point['name'], point['heldout_before_residual'], point['heldout_after_residual'],
               point['leave_position_out_max_residual']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--batches', type=int, default=8, help='Each point: batches of four captures, even and >=4')
    parser.add_argument('--no-apply', action='store_true', help='Collect and validate; keep existing correction')
    parser.add_argument('--lo-mhz', type=float, default=433.0, help='External common LO, unchanged throughout calibration')
    parser.add_argument('--legacy-amplitude', action='store_true', help='Previous single-RF A/B/C gain+phase method')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--replay', type=Path, help='Recompute a saved session; never touch hardware or active config')
    mode.add_argument('--restore', type=Path, help='Restore array_before.csv from this calibration session')
    args = parser.parse_args(argv)
    if args.replay:
        report = solve_session(json.loads(args.replay.read_text(encoding='utf-8')))
        summarize(report)
        return 0 if report['accepted'] else 2
    if sys.platform != 'linux':
        parser.error('采集请在复旦微 Linux 的 SSH 终端运行；本机可执行 --replay')
    if not sys.stdin.isatty():
        parser.error('请在交互 SSH 终端运行，以便逐点移动发射天线')
    if args.batches < 4 or args.batches % 2:
        parser.error('--batches 必须是 >=4 的偶数')
    root = args.root.resolve()
    if args.restore:
        restore(root, args.restore)
        return 0
    job_bytes = (root / 'config/job.json').read_bytes()
    job = json.loads(job_bytes)
    settings = settings_from_job(job)
    geometry, old = read_array(root / 'config/array.csv')
    if any(abs(geometry[ch][axis] - job['array_xy_m'][ch][axis]) > 1e-12 for ch in range(6) for axis in range(2)):
        raise ValueError('job.json 与 array.csv 天线位置不一致')
    binary = root / 'bin/icraft_aoa_ml3'
    if digest(binary.read_bytes()) != job['elf_sha256']['icraft_aoa_ml3']:
        raise ValueError('正式采集 ELF 哈希与启动配置不符')
    if args.legacy_amplitude:
        print('旧版单频幅相标定 | RF %.3f MHz' % (settings['rf_hz'] / 1e6))
        points = prompt_legacy_points(geometry, settings)
    else:
        if settings['rf_hz'] != 445e6 or args.lo_mhz >= 445:
            raise ValueError('正式程序须使用445 MHz高侧RF；425 MHz仅辅助测量，不改变正式RF')
        for rf in RF_FREQUENCIES:
            tone_settings(settings, rf, args.lo_mhz * 1e6)
        print('六路双频相位/等效延迟标定 | 425 + 445 MHz | 每位置每频率 %d × 4 帧 | CH0参考' % args.batches)
        print('相隔20 MHz，延迟仅确定模50 ns的主值；不等同于ADC物理延迟或宽带补偿。')
        print('保持共同本振、接收增益和天线方向不变；每次换频锁定后按新回车才采集。')
        points = prompt_points(geometry, settings, args.lo_mhz * 1e6)
    with exclusive_session(root):
        directory = root / 'data/calibration' / (time.strftime('%Y%m%d_%H%M%S') + '_%d' % os.getpid())
        directory.mkdir(exist_ok=False)
        old_bytes = (root / 'config/array.csv').read_bytes()
        if read_array(root / 'config/array.csv') != (geometry, old):
            raise RuntimeError('输入坐标期间阵列配置被修改，请重启校准')
        if (root / 'config/job.json').read_bytes() != job_bytes:
            raise RuntimeError('输入坐标期间运行配置被修改，请重启校准')
        atomic_bytes(directory / 'array_before.csv', old_bytes)
        session = {'format_version': 1 if args.legacy_amplitude else 2, 'settings': settings, 'geometry': geometry,
                   'old_correction': [[c.real, c.imag] for c in old], 'array_sha256': digest(old_bytes),
                   'job_sha256': digest(job_bytes),
                   'elf_sha256': digest(binary.read_bytes()), 'points': points}
        if not args.legacy_amplitude:
            session.update(frequencies_hz=list(RF_FREQUENCIES), lo_hz=args.lo_mhz * 1e6)
        save_json(directory / 'session.json', session)
        print('原始数据和备份：' + str(directory), flush=True)
        try:
            collect(root, directory, session, args.batches)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            # Explain and record the rejection before the context resumes normal capture.
            session.setdefault('progress', {}).update(state='rejected', reason=str(error))
            save_json(directory / 'session.json', session)
            save_json(directory / 'failure.json', {'reason': str(error), 'applied': False})
            print('\n标定停止：%s\n原校正参数未修改；现在退出标定。' % error, flush=True)
            if not args.legacy_amplitude:
                print('请将信号源恢复445 MHz；正式采集配置仍为445 MHz。', flush=True)
            return 2
        report = solve_session(session)
        session['progress'] = {'state': 'accepted' if report['accepted'] else 'rejected'}
        save_json(directory / 'session.json', session)
        save_json(directory / 'report.json', report)
        summarize(report)
        if report['accepted']:
            atomic_bytes(directory / 'array_calibrated.csv',
                         array_text(geometry, [complex(*c) for c in report['correction']]).encode('ascii'))
            if not args.no_apply:
                install_result(root, directory, session, report)
                print('已保存到 config/array.csv；正常程序下次启动时读取。', flush=True)
                if not args.legacy_amplitude:
                    print('相对延迟测量记录已写入 config/channel_delay.json。', flush=True)
        return 0 if report['accepted'] else 2


if __name__ == '__main__':
    # SSH disconnect and termination follow the same cleanup as Ctrl+C.
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    for sig in (signal.SIGTERM, getattr(signal, 'SIGHUP', signal.SIGTERM)):
        signal.signal(sig, interrupted)
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print('\n已中止标定；未通过检查的参数不会写入。', file=sys.stderr)
        print('若已切换信号源，请恢复正式运行频率445 MHz。', file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('\n标定停止：' + str(error), file=sys.stderr)
        raise SystemExit(1)
