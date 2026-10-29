"""Archive wire format and integrity checks shared by PC and board."""
import hashlib
import json
import os
import re
import tarfile
from pathlib import Path
MAX_UPLOAD=64*1024*1024
MAX_EXPANDED=256*1024*1024
NAME=re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}")

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def sync_directory(folder):
    if os.name == 'nt':
        return  # Windows receiver fsyncs each file; directory handles are not supported here.
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def write_json(path, value):
    temporary = Path(str(path) + '.tmp')
    with temporary.open('w') as f:
        json.dump(value, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)

def safe_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value) or '..' in value:
        raise ValueError('Invalid archive filename')
    return value

def verify_archive(path, summary):
    """No extraction. Also verify hardlink targets, member sizes and the full SHA256."""
    if path.stat().st_size != summary['archive_bytes'] or digest(path) != summary['archive_sha256']:
        raise ValueError('Archive checksum/length mismatch')
    members = summary['members']
    expected = {safe_name(m['name']): m for m in members}
    if not expected or len(expected) != len(members) or len(members) > 256:
        raise ValueError('Invalid/duplicate member list')
    if any(type(m['bytes']) is not int or m['bytes'] < 0 for m in members):
        raise ValueError('Invalid member size')
    if sum(m['bytes'] for m in members) > MAX_EXPANDED:
        raise ValueError('Archive expands beyond limit')
    with tarfile.open(path, 'r:gz') as tar:
        entries = tar.getmembers()
        if sorted(e.name for e in entries) != sorted(expected):
            raise ValueError('Member list mismatch')
        for entry in entries:
            if not entry.isfile() and not entry.islnk():
                raise ValueError('Only regular files and internal hardlinks allowed')
            if entry.islnk() and (entry.linkname not in expected or not tar.getmember(entry.linkname).isfile()):
                raise ValueError('Invalid hardlink')
            member = expected[entry.name]
            if entry.isfile() and entry.size != member['bytes']:
                raise ValueError('Member size mismatch')
            h, size = hashlib.sha256(), 0
            with tar.extractfile(entry) as source:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > member['bytes']:
                        raise ValueError('Member larger than manifest')
                    h.update(block)
            if size != member['bytes'] or h.hexdigest() != member['sha256']:
                raise ValueError('Member checksum mismatch: ' + entry.name)
