"""PC-only HTTP receiver; verify and fsync before acknowledging a batch."""
import argparse
import json
import os
import socket
import shutil
import struct
import time
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.archive_io import MAX_UPLOAD,safe_name,verify_archive,digest,sync_directory,write_json

class ArchiveHTTPServer(HTTPServer):
    # Two copied launchers must never share the same Windows listening socket.
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != '/health':
            self.send_error(404)
            return
        body = json.dumps({
            'application': 'fpga-aoa-archive',
            'root': str(Path(__file__).resolve().parents[1]),
            'output': str(self.server.output),
            'bind': self.server.server_address[0],
            'port': self.server.server_address[1],
            'pid': os.getpid(),
        }).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        temporary = None
        try:
            if self.path != '/archive':
                raise ValueError('Unexpected endpoint')
            self.connection.settimeout(20)
            length = int(self.headers.get('Content-Length', '0'))
            if not 5 <= length <= MAX_UPLOAD:
                raise ValueError('Invalid upload size')
            if shutil.disk_usage(self.server.output).free < 1024**3:
                raise OSError('PC free space below 1 GiB; acquisition must stop')
            first = self.rfile.read(4)
            if len(first) != 4:
                raise ValueError('Truncated header')
            header_size = struct.unpack('!I', first)[0]
            if not 1 <= header_size <= min(131072, length - 4):
                raise ValueError('Invalid manifest size')
            summary = json.loads(self.rfile.read(header_size))
            category = summary['category']
            if category not in ('aoa', 'npu', 'logs'):
                raise ValueError('Invalid category')
            name = safe_name(summary['archive'])
            if not name.endswith('.tar.gz'):
                raise ValueError('Invalid archive extension')
            folder = self.server.output/category
            folder.mkdir(exist_ok=True)
            sync_directory(self.server.output)
            target = folder/name
            temporary = folder/(name + '.incoming')
            remaining = length - 4 - header_size
            if remaining != summary['archive_bytes']:
                raise ValueError('Body length differs from manifest')
            with temporary.open('wb') as out:
                while remaining:
                    block = self.rfile.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError('Truncated upload')
                    out.write(block)
                    remaining -= len(block)
                out.flush()
                os.fsync(out.fileno())
            verify_archive(temporary, summary)
            if target.exists():
                if digest(target) != summary['archive_sha256']:
                    raise ValueError('Refusing to overwrite different saved data')
                temporary.unlink()
            else:
                os.replace(temporary, target)
                sync_directory(folder)
            write_json(folder/(name[:-7] + '.archive.json'), summary)
            write_json(self.server.output/'receiver_status.json', {
                'pid': os.getpid(), 'saved': True, 'received_pc_ns': time.time_ns(),
                'category': category, 'archive': name, 'sha256': summary['archive_sha256']})
            body = json.dumps({'saved': True, 'archive': name,
                               'sha256': summary['archive_sha256']}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            print('ARCHIVE_RECEIVE_ERROR ' + str(exc), flush=True)
            try:
                self.send_error(503, 'Archive not acknowledged')
            except OSError:
                pass
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=7012)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with ArchiveHTTPServer((args.bind, args.port), Receiver) as server:
        server.output = args.output.resolve()
        print('ARCHIVE_RECEIVER_READY ' + str(server.output), flush=True)
        server.serve_forever()

if __name__=="__main__":main()
