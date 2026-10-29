"""Six-channel raw ADC viewer. Python standard library only; board does capture.

The 7100 protocol is START + 16384 sample groups, each CH0..CH5 uint16 LE.
The real ADC build puts the raw 12-bit code in bits11:0 and zeros bits15:12.
This tool never masks invalid high bits, so an old ROM/64-bit build is visible.
"""
import argparse
import array
import hashlib
import http.server
import json
import math
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

SAMPLES, CHANNELS, SAMPLE_RATE = 16384, 6, 100_000_000
FRAME_BYTES = SAMPLES * CHANNELS * 2
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from pc_config import load_settings, resolve_path

ENCODINGS = ('biased12', 'twos12', 'offset12', 'raw12')
# Periodic Hann window: reused for full-frame spectra, independent of waveform zoom.
HANN = tuple(0.5-0.5*math.cos(2*math.pi*i/SAMPLES) for i in range(SAMPLES))


def statistics(values):
    mean = sum(values) / len(values)
    return {'min': min(values), 'max': max(values), 'mean': mean,
            'ac_rms': math.sqrt(sum((v-mean)**2 for v in values)/len(values))}


def display_codes(values, encoding):
    """Interpret only validated 12-bit words; never change the saved payload."""
    if encoding == 'biased12':
        # Two's complement -> offset binary: flip bit11 (equivalent to signed+2048).
        # This is a display conversion; the current six-channel PL sends raw bits.
        return [v ^ 2048 for v in values]
    if encoding == 'twos12':
        return [v-4096 if v >= 2048 else v for v in values]
    if encoding == 'offset12':
        return [v-2048 for v in values]
    return list(values)


def decode_frame(raw):
    if len(raw) != FRAME_BYTES:
        raise ValueError(f'Expected {FRAME_BYTES} bytes, received {len(raw)}')
    words = array.array('H')
    words.frombytes(raw)
    if sys.byteorder != 'little':
        words.byteswap()
    channels = [words[ch::CHANNELS] for ch in range(CHANNELS)]
    stats = []
    for values in channels:
        stats.append({**statistics(values),
                      'high_bits_nonzero': sum(v > 4095 for v in values)})
    return channels, stats


def adc_spectrum(codes):
    """Single-sided peak-amplitude spectrum of the physical two's-complement ADC.

    Viewer-only FFT: no board processing or saved samples are modified. A full-scale
    sine has a peak amplitude of 2048 codes = 0 dBFS. This is not a PSD in dB/Hz.
    """
    if len(codes) != SAMPLES or any(v < 0 or v > 4095 for v in codes):
        raise ValueError('Spectrum requires one complete, valid 12-bit ADC channel')
    values = display_codes(codes, 'twos12')
    mean = sum(values)/SAMPLES
    work = [(v-mean)*w for v, w in zip(values, HANN)]
    # In-place radix-2 FFT keeps the monitor standard-library-only.
    j = 0
    for i in range(1, SAMPLES):
        bit = SAMPLES >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            work[i], work[j] = work[j], work[i]
    size = 2
    while size <= SAMPLES:
        half = size//2
        angle = -2*math.pi/size
        root = complex(math.cos(angle), math.sin(angle))
        for start in range(0, SAMPLES, size):
            phase = 1
            for i in range(start, start+half):
                even, odd = work[i], phase*work[i+half]
                work[i], work[i+half] = even+odd, even-odd
                phase *= root
        size *= 2
    levels = []
    gain = sum(HANN)
    for k in range(SAMPLES//2+1):
        # DC/Nyquist occur only once; all other bins have a negative-frequency twin.
        amplitude = abs(work[k])/gain * (1 if k in (0, SAMPLES//2) else 2)
        levels.append(20*math.log10(max(1e-7, amplitude/2048)))
    peak_bin = max(range(1, len(levels)), key=levels.__getitem__)
    has_peak = levels[peak_bin] > -140
    return {'fft_size': SAMPLES, 'bin_hz': SAMPLE_RATE/SAMPLES,
            'max_hz': SAMPLE_RATE/2, 'unit': 'dBFS', 'window': 'Hann',
            'dc_removed': True, 'input_coding': 'twos-complement',
            'peak_hz': peak_bin*SAMPLE_RATE/SAMPLES if has_peak else None,
            'peak_dbfs': round(levels[peak_bin], 2) if has_peak else None,
            'amplitude_dbfs': [round(v, 2) for v in levels]}


class Capture:
    def __init__(self, args):
        self.args, self.lock, self.stop = args, threading.Lock(), threading.Event()
        self.enabled = threading.Event()
        if not getattr(args, 'paused', False):
            self.enabled.set()
        self.raw, self.channels = None, None
        self.signed_stats = []
        self.spectrum_cache = {}
        self.state = {'sequence': 0, 'status': 'waiting', 'error': None,
                      'board': f'{args.board}:{args.board_port}', 'sample_rate': SAMPLE_RATE,
                      'sample_count': SAMPLES, 'frame_bytes': FRAME_BYTES, 'received_at': None,
                      'stats': [], 'saved_path': None, 'failures': 0}
        self.state['acquisition_enabled'] = self.enabled.is_set()

    def run(self):
        # One request/connection at a time; partial frames are discarded, never joined.
        while not self.stop.is_set():
            if not self.enabled.is_set():
                self.stop.wait(0.1)
                continue
            started = time.monotonic()
            try:
                with socket.create_connection((self.args.board, self.args.board_port), timeout=3) as sock:
                    sock.settimeout(5)
                    sock.sendall(b'START\n')
                    raw = bytearray()
                    deadline = time.monotonic() + 8
                    while len(raw) < FRAME_BYTES:
                        remaining = deadline-time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError(f'Incomplete frame: {len(raw)}/{FRAME_BYTES} bytes')
                        sock.settimeout(min(3, remaining))
                        block = sock.recv(FRAME_BYTES-len(raw))
                        if not block:
                            raise ConnectionError(f'Connection closed: {len(raw)}/{FRAME_BYTES} bytes')
                        raw.extend(block)
                raw = bytes(raw)
                channels, stats = decode_frame(raw)
                valid = not any(s['high_bits_nonzero'] for s in stats)
                # A signed waveform can cross 4095 -> 0. Compute its statistics
                # after decoding, otherwise MIN/MAX and RMS would be misleading.
                signed_stats = [statistics(display_codes(v, 'twos12')) for v in channels] if valid else []
                with self.lock:
                    self.raw, self.channels = raw, channels
                    self.signed_stats = signed_stats
                    self.spectrum_cache = {}
                    self.state.update(sequence=self.state['sequence']+1,
                                      status='ok' if valid else 'format_error', error=None,
                                      received_at=time.time(), stats=stats,
                                      receive_ms=round((time.monotonic()-started)*1000, 1),
                                      raw_12bit_valid=valid)
            except (OSError, ValueError) as exc:
                with self.lock:
                    self.state.update(status='disconnected', error=str(exc),
                                      failures=self.state['failures']+1)
            self.stop.wait(max(0.1, self.args.interval-(time.monotonic()-started)))

    def snapshot(self, count=2048, offset=0, encoding='biased12', fft_channel=None):
        if encoding not in ENCODINGS:
            raise ValueError('Invalid ADC encoding')
        if fft_channel is not None and fft_channel not in range(CHANNELS):
            raise ValueError('FFT channel must be between 0 and 5')
        with self.lock:
            result = dict(self.state)
            channels, signed_stats = self.channels, self.signed_stats
            spectrum = self.spectrum_cache.get(fft_channel)
        if not result['acquisition_enabled']:
            result['status'] = 'paused'
        # Keep invalid high bits visible instead of interpreting corrupt/old
        # firmware words as valid signed samples. Raw stats always remain available.
        actual_encoding = encoding if result.get('raw_12bit_valid') else 'raw12'
        result.update(window_count=count, window_offset=offset,
                      encoding=actual_encoding, requested_encoding=encoding,
                      raw_stats=result['stats'])
        if actual_encoding == 'twos12':
            result['stats'] = signed_stats
        elif actual_encoding == 'biased12':
            result['stats'] = [{**s, 'min': s['min']+2048, 'max': s['max']+2048,
                                'mean': s['mean']+2048} for s in signed_stats]
        elif actual_encoding == 'offset12':
            result['stats'] = [{**s, 'min': s['min']-2048, 'max': s['max']-2048,
                                'mean': s['mean']-2048} for s in result['raw_stats']]
        result['channels'] = [display_codes(v[offset:offset+count], actual_encoding)
                              for v in channels] if channels else []
        result['spectrum'] = None
        if channels and result.get('raw_12bit_valid') and fft_channel is not None:
            if spectrum is None:
                # Compute outside the capture lock; only cache against the same frame.
                spectrum = {**adc_spectrum(channels[fft_channel]), 'channel': fft_channel,
                            'sequence': result['sequence']}
                with self.lock:
                    if self.state['sequence'] == result['sequence']:
                        self.spectrum_cache[fft_channel] = spectrum
            result['spectrum'] = spectrum
        return result

    def set_acquisition(self, enabled):
        # Keep the viewer available while Fudan owns the7100 START connection.
        if type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        with self.lock:
            self.state['acquisition_enabled'] = enabled
            self.enabled.set() if enabled else self.enabled.clear()
        return {'acquisition_enabled': enabled}

    def save(self):
        with self.lock:
            if self.raw is None:
                raise ValueError('No complete frame received yet')
            raw, meta = self.raw, dict(self.state)
        # Each saved .bin is the exact network payload, with a verified SHA-256 sidecar.
        self.args.save_dir.mkdir(parents=True, exist_ok=True)
        stem = f"adc_{time.time_ns()}_{meta['sequence']:06}"
        path = self.args.save_dir / (stem+'.bin')
        path.write_bytes(raw)
        checksum = hashlib.sha256(raw).hexdigest()
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise OSError('Saved frame checksum mismatch')
        meta.update(sha256=checksum,
                    encoding='raw12-in-uint16-le' if meta.get('raw_12bit_valid') else 'uint16-le-unvalidated',
                    adc_output_coding='twos-complement (user-confirmed); unmodified ADC bits in this file',
                    layout='sample-major CH0..CH5')
        path.with_suffix('.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
        with self.lock:
            self.state['saved_path'] = str(path)
        return {'path': str(path), 'sha256': checksum, 'sequence': meta['sequence']}


def handler(capture):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, data, kind='application/json; charset=utf-8'):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8') if isinstance(data, dict) else data
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == '/':
                return self.send(200, (HERE/'index.html').read_bytes(), 'text/html; charset=utf-8')
            if url.path == '/api/frame':
                try:
                    q = parse_qs(url.query)
                    count = min(SAMPLES, max(64, int(q.get('count', ['2048'])[0])))
                    offset = min(SAMPLES-count, max(0, int(q.get('offset', ['0'])[0])))
                    encoding = q.get('encoding', ['biased12'])[0]
                    fft_channel = int(q.get('fft_channel', ['2'])[0])
                    return self.send(200, capture.snapshot(count, offset, encoding, fft_channel))
                except ValueError as exc:
                    return self.send(400, {'error': str(exc)})
            return self.send(404, {'error': 'Not found'})

        def do_POST(self):
            if self.path == '/api/capture':
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 128:
                        raise ValueError('Invalid request length')
                    self.connection.settimeout(3)
                    request = json.loads(self.rfile.read(size))
                    return self.send(200, capture.set_acquisition(request['enabled']))
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    return self.send(400, {'error': str(exc)})
            if self.path != '/api/save':
                return self.send(404, {'error': 'Not found'})
            try:
                return self.send(200, capture.save())
            except (ValueError, OSError) as exc:
                return self.send(409, {'error': str(exc)})
    return Handler


def parse_args(argv=None):
    config = load_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', default=config['board_ip'])
    parser.add_argument('--board-port', type=int, default=config['board_port'])
    parser.add_argument('--http-port', type=int, default=8780)
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--paused', action='store_true', help='Serve the viewer without polling7100 until enabled')
    parser.add_argument('--save-dir', type=Path, default=config['adc_save_dir'])
    args = parser.parse_args(argv)
    if args.interval < 0.1:
        parser.error('--interval must be at least 0.1 seconds')
    args.save_dir = resolve_path(args.save_dir)
    return args


def main():
    args = parse_args()
    capture = Capture(args)
    # Bind first: a port failure must not leave an invisible capture worker running.
    try:
        server = http.server.ThreadingHTTPServer(('127.0.0.1', args.http_port), handler(capture))
    except OSError as exc:
        parser.exit(1, f'Cannot listen on 127.0.0.1:{args.http_port}: {exc}\nTry --http-port 8781\n')
    worker = threading.Thread(target=capture.run, name='7100-adc-capture', daemon=True)
    worker.start()
    print(f'ADC monitor: http://127.0.0.1:{args.http_port}/', flush=True)
    print(f'Board {args.board}:{args.board_port}; raw frames saved on demand to {args.save_dir}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop.set()
        server.server_close()
        worker.join(timeout=10)


if __name__ == '__main__':
    main()
