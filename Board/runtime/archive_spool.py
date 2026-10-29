"""Bounded board-RAM archive writer; preserves originals until PC ACK."""
import hashlib
import json
import os
import tarfile
import threading
import time
from pathlib import Path
from common.archive_io import digest,sync_directory,write_json

class ArchiveSpool:
    """One writer; a committed manifest is the durable-save acknowledgement."""
    def __init__(self, spool, output, prefix='aoa'):
        self.spool = Path(spool).resolve()
        self.output = Path(output).resolve()
        self.prefix = prefix
        self.category = self.output.name
        self.verbose = False
        self.compresslevel = int(os.environ.get("AOA_ARCHIVE_GZIP_LEVEL", "1"))
        if not 0 <= self.compresslevel <= 9:
            raise ValueError("Archive gzip level must be 0..9")
        self.output.mkdir(parents=True, exist_ok=True)
        for folder in (self.output, self.output.parent, self.output.parent.parent):
            sync_directory(folder)
        self.error = None
        self.done = threading.Event()
        self.committed = 0
        self.thread = threading.Thread(target=self.run, name='board-archive', daemon=True)

    def commit_archive(self, archive, manifest, summary):
        """Subclasses acknowledge storage before archive() releases originals."""

    def checkpoint(self):
        """Subclasses may pause work between archive operations."""

    def archive(self, metadata, partial=False, stem=None):
        self.checkpoint()
        started = time.monotonic()
        record = json.loads(metadata.read_text())
        stem = stem or metadata.name[:-len('_metadata.json')]
        assert metadata.parent.resolve() == self.spool
        assert (not self.prefix or stem.startswith(self.prefix + '_')) and '/' not in stem
        files = sorted(self.spool.glob(stem + '_*'))
        capture_file = self.spool/(stem + '_capture.json')
        if record.get('record_raw') and record.get('processing_complete'):
            capture = json.loads(capture_file.read_text())
            if capture.get('schema') != 'aoa.raw_capture.v1' or capture.get('run_id') != record.get('run_id'):
                raise ValueError('Capture/result identity mismatch')
            if capture.get('completed_rounds') != record.get('completed_capture_rounds'):
                raise ValueError('Capture/result round mismatch')
            expected = record.get('capture_round_count')
            if (capture.get('expected_rounds') != expected or capture.get('completed_rounds') != expected
                    or len(capture.get('rounds', [])) != expected
                    or capture.get('result', {}).get('run_id') != record.get('run_id')
                    or not capture.get('result', {}).get('complete')):
                raise ValueError('Incomplete or mismatched final capture result')
            for index, row in enumerate(capture['rounds']):
                if row.get('index') != index or row.get('run_id') != record.get('run_id') or not row.get('complete'):
                    raise ValueError('Capture round identity/completeness mismatch')
                for artifact in row['files'].values():
                    name, size = artifact['name'], artifact['bytes']
                    if Path(name).name != name or not name.startswith(stem + '_'):
                        raise ValueError('Capture artifact does not belong to this batch')
                    if size is not None and (self.spool/name).stat().st_size != size:
                        raise ValueError('Capture artifact size changed before archive')
        if not partial and any(p.name.endswith(('.tmp', '.linktmp')) for p in files):
            return False
        assert all(p.parent.resolve() == self.spool and p.is_file() and not p.is_symlink()
                   for p in files)
        members = []
        for path in files:
            self.checkpoint()
            members.append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path)})
        archive = self.output/(stem + '.tar.gz')
        manifest = self.output/(stem + '.archive.json')
        if archive.exists() or manifest.exists():
            raise RuntimeError('Refuse to overwrite a previous archive: ' + stem)
        temporary = self.output/(stem + '.tar.gz.tmp')
        # tar 保留硬链接；gzip 无损压缩传输槽的零填充，原始文件格式与内容不变。
        self.checkpoint()
        with tarfile.open(temporary, 'w:gz', compresslevel=self.compresslevel, dereference=False) as tar:
            for path in files:
                self.checkpoint()
                tar.add(path, arcname=path.name, recursive=False)
        with temporary.open('r+b') as f:
            os.fsync(f.fileno())
        # 回读落盘归档，逐文件验证长度和 SHA256；失败时 RAM 原件全部保留。
        self.checkpoint()
        with tarfile.open(temporary, 'r:gz') as tar:
            assert sorted(tar.getnames()) == sorted(m['name'] for m in members)
            for member in members:
                self.checkpoint()
                f = tar.extractfile(member['name'])
                if f is None:
                    raise RuntimeError('Unreadable archive member')
                h, size = hashlib.sha256(), 0
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    size += len(block)
                    h.update(block)
                if size != member['bytes'] or h.hexdigest() != member['sha256']:
                    raise RuntimeError('Archive verification failed: ' + member['name'])
        self.checkpoint()
        summary = {'gzip_level': self.compresslevel, 'run_id': record.get('run_id'), 'source_directory': str(self.spool),
                   'archive': archive.name, 'archive_sha256': digest(temporary),
                   'archive_bytes': temporary.stat().st_size,
                   'members': members, 'partial': partial,
                   'processing_complete': record.get(
                       'processing_complete', record.get('status') in ('inferred', 'preprocessed'))}
        if capture_file.exists():
            summary['capture_file'] = capture_file.name
            summary['capture_schema'] = 'aoa.raw_capture.v1'
        os.replace(temporary, archive)
        sync_directory(self.output)
        summary['archive_ms'] = (time.monotonic() - started) * 1000
        write_json(manifest, summary)
        self.checkpoint()
        self.commit_archive(archive, manifest, summary)
        # 只有保存与回读验证均完成，才释放这个批次的 RAM。metadata 最后移除，
        # PS 因而不会提前认为有空位；任何失败都会停止后续采集并保留未释放的数据。
        for path in files:
            if path != metadata:
                path.unlink()
        metadata.unlink()
        self.committed += 1
        if getattr(self, 'verbose', True):
            print('ARCHIVE_COMMITTED ' + json.dumps({k: v for k, v in summary.items()
                                                    if k != 'members'}), flush=True)
        return True

    def drain(self, partial=False):
        for metadata in sorted(self.spool.glob(self.prefix + '_*_metadata.json')):
            record = json.loads(metadata.read_text())
            if record.get('processing_complete') or partial:
                self.archive(metadata, partial=not record.get('processing_complete', False))

    def run(self):
        try:
            while not self.done.is_set():
                self.drain()
                self.done.wait(.05)
            # 调用方先等待采集进程退出，再触发 done；此时可保存最后的失败/中断批次。
            self.drain(partial=True)
        except Exception as exc:
            self.error = str(exc)
            print('Error: board archive failed: ' + self.error, flush=True)
