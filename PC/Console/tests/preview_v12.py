"""Local read-only preview of source-emitted C++ fixtures; no board connection."""
import argparse
import http.server
import json
import math
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from protocol import validate
from server import State, handler


def synthetic_beam_for_preview(source):
    """Explicit test-only equal-amplitude phases; never a hardware claim."""
    angle = source['v12']['joint'].get('angle_deg') or 0.0
    ref = source['antennas'][0]
    k = source.get('phase_sign', 1)*2*math.pi*source['rf_hz']/299792458
    theta = math.radians(angle)
    phases = [math.remainder(-k*((x-ref[0])*math.sin(theta)+(y-ref[1])*math.cos(theta)),
                             2*math.pi) for x, y in source['antennas']]
    return dict(available=True, mode='manual', tracking=False, status='applied',
                error='', command_id='', applied_run_id=source['run_id'], angle_deg=angle,
                command_status='applied', command_error='', pl_version=0,
                required_pl_version=0, registers_verified=True,
                hardware_response_verified=False, phase_rad=phases, paths=[0, 0],
                synthetic_preview=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', choices=('position', 'angle6', 'scan6', 'k1', 'invalid', 'position_no_reference', 'position_ambiguous', 'position_raw_bad'), default='position')
    parser.add_argument('--fixture-file', type=pathlib.Path,
                        help='Load an external offline packet instead of the built-in fixture; demo mode remains enabled')
    parser.add_argument('--http-port', type=int, default=18775)
    parser.add_argument('--beam-preview', action='store_true',
                        help='Explicit synthetic applied phases for visual QA only; controls stay disabled')
    args = parser.parse_args()
    source_path = args.fixture_file or (pathlib.Path(__file__).parent / 'fixtures' / 'v12' /
                                       ('packet_'+args.fixture+'.json'))
    source = json.loads(source_path.read_text(encoding='utf-8'))
    if args.beam_preview:
        source['beam'] = synthetic_beam_for_preview(source)
        source['capture_mode'] = 'synthetic_demo'
    validate(source)
    state = State(mode='demo')
    state.ingest(source, peer='offline C++ fixture')
    # Demo-only capability metadata reveals the fixture's analysis view. The
    # HTTP control route still rejects all commands because mode remains demo.
    state.beam = dict(available=False, session=source['session'], received_at=time.time(),
                      mode='equal', processing=dict(available=True,
                        mode=source.get('processing_mode', 'position'),
                        applied_mode=source.get('processing_mode', 'position'),
                        supported_modes=['position', 'angle6', 'scan6'], joint_paths=3,
                        applied_joint_paths=3, command_status='applied', error=''))
    def refresh():
        while True:
            time.sleep(1)
            with state.lock:
                state.aoa['received_at'] = time.time()
                state.sources['aoa']['last_seen'] = time.time()
    threading.Thread(target=refresh, daemon=True).start()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.http_port), handler(state))
    print(f'V1.2 OFFLINE FIXTURE / no board control: http://127.0.0.1:{args.http_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
