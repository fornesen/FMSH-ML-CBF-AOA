"""[板端归档新增] Verify and restore one saved batch on Windows or Linux."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def restore(manifest, destination):
    manifest, destination = Path(manifest).resolve(), Path(destination).resolve()
    record = json.loads(manifest.read_text())
    archive_name = record['archive']
    members = record['members']
    names = [m['name'] for m in members]
    # 只恢复归档清单中的平面文件；拒绝覆盖、路径穿越和未列出的内容。
    for name in [archive_name] + names:
        if not name or name in ('.', '..') or '/' in name or '\\' in name or ':' in name:
            raise ValueError('Invalid archive filename: ' + name)
    if len(set(names)) != len(names):
        raise ValueError('Duplicate archive members')
    archive = manifest.parent/archive_name
    if sha256(archive) != record['archive_sha256']:
        raise ValueError('Archive SHA256 mismatch')
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, 'r:gz') as tar:
        if sorted(tar.getnames()) != sorted(names):
            raise ValueError('Archive member list mismatch')
        for m in members:
            info = tar.getmember(m['name'])
            if not (info.isfile() or info.islnk()):
                raise ValueError('Unsupported archive member')
            source = tar.extractfile(info)
            if source is None:
                raise ValueError('Unreadable archive member')
            output = destination/m['name']
            with source, output.open('xb') as out:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    out.write(block)
            if output.stat().st_size != m['bytes'] or sha256(output) != m['sha256']:
                raise ValueError('Restored file verification failed: ' + m['name'])
    # 原文件保持逐字节不变；另存供本次回放使用的清单，修正已释放的 RAM 路径。
    old = record['source_directory'].rstrip('/') + '/'
    def rebase(value):
        if isinstance(value, str) and value.startswith(old):
            return str(destination/value[len(old):])
        if isinstance(value, list):
            return [rebase(x) for x in value]
        if isinstance(value, dict):
            return {k: rebase(v) for k, v in value.items()}
        return value
    for name in names:
        if name.endswith(('_metadata.json', '_npu_ready.json')):
            data = json.loads((destination/name).read_text())
            (destination/('replay_' + name)).write_text(
                json.dumps(rebase(data), indent=2), encoding='utf-8')
    return {'run_id': record['run_id'], 'files_verified': len(names),
            'destination': str(destination), 'partial': record['partial']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('destination', type=Path, help='New directory; never overwrites previous data')
    args = parser.parse_args()
    print(json.dumps(restore(args.manifest, args.destination), indent=2))
