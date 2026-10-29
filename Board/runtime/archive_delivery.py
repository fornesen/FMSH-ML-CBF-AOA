"""PC-acknowledged delivery; unacknowledged originals remain in bounded RAM."""
import hashlib
import json
from pathlib import Path
import struct
import stat
import urllib.error
import urllib.request

from common.archive_io import sync_directory
from archive_sender import AoaArchive


def temporary_bytes(root):
    total = 0
    for path in Path(root).rglob('*'):
        try:
            info = path.stat()
        except FileNotFoundError:
            continue  # An ACK may release files while the manager measures RAM.
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


class ArchiveDelivery:
    def __init__(self, folder):
        self.folder = Path(folder)

    def deliver(self, manifest, endpoint, checkpoint=None):
        if checkpoint:
            checkpoint()
        summary = json.loads(manifest.read_text())
        archive = manifest.parent / summary['archive']
        if archive.parent != manifest.parent or archive.suffixes[-2:] != ['.tar', '.gz']:
            raise ValueError('Invalid local archive name')
        # Commit already verified every member. Check the exact compressed bytes
        # being sent; the PC verifies members again before acknowledging receipt.
        # Reopening every gzip member here competes with four-frame acquisition.
        payload = archive.read_bytes()
        if (len(payload) != summary['archive_bytes'] or
                hashlib.sha256(payload).hexdigest() != summary['archive_sha256']):
            raise ValueError('Archive checksum/length mismatch')
        summary['category'] = manifest.parent.name
        header = json.dumps(summary).encode()
        body = struct.pack('!I', len(header)) + header + payload
        request = urllib.request.Request(endpoint, data=body,
                                         headers={'Content-Type': 'application/octet-stream'})
        if checkpoint:
            checkpoint()
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                ack = json.loads(response.read(65536))
        except urllib.error.HTTPError as error:
            error.close()
            raise
        if (ack.get('saved'), ack.get('archive'), ack.get('sha256')) != (
                True, archive.name, summary['archive_sha256']):
            raise ValueError('Receiver did not acknowledge this archive')
        # Remove the committed marker first. A crash can leave an orphan tar, never
        # a pending manifest pointing at a deleted archive. No raw data is overwritten.
        manifest.unlink()
        sync_directory(manifest.parent)
        archive.unlink()
        sync_directory(manifest.parent)

    def pending_manifests(self):
        return sorted(self.folder.glob('*/*.archive.json'))

class PcFirstStorage:
    """Bounded RAM originals stay until a registered PC verifies and fsyncs them."""
    def configure_pc_delivery(self, endpoints, cancel, enabled=lambda: True):
        self.pc_endpoints = endpoints
        self.pc_cancel = cancel
        self.pc_enabled = enabled
        self.delivery_error = ''

    def checkpoint(self):
        while not self.pc_enabled():
            if self.pc_cancel.wait(.25):
                raise RuntimeError('PC did not acknowledge; bounded RAM originals retained: archive paused')

    def commit_archive(self, archive, manifest, summary):
        sender = ArchiveDelivery(self.output.parent)
        while not self.pc_cancel.is_set():
            self.checkpoint()
            endpoints = self.pc_endpoints()
            for endpoint in endpoints:
                if self.pc_cancel.is_set():
                    break
                try:
                    sender.deliver(manifest, endpoint, checkpoint=self.checkpoint)
                    self.delivery_error = ''
                    return
                except Exception as error:
                    self.delivery_error = str(error)
            # Keep the raw metadata marker in place: backlog=1 prevents another
            # acquisition batch, including when the PC disk rejects an upload.
            if not endpoints:
                self.delivery_error = 'Waiting for a registered PC archive receiver'
            self.pc_cancel.wait(.25)
        raise RuntimeError('PC did not acknowledge; bounded RAM originals retained: ' + self.delivery_error)


class PcFirstArchive(PcFirstStorage, AoaArchive):
    pass
