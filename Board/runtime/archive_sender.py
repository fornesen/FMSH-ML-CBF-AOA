"""Discover complete AOA markers and final interrupted batches."""
import re
from archive_spool import ArchiveSpool
from common.archive_io import write_json

class AoaArchive(ArchiveSpool):
    def drain(self, partial=False):
        super().drain(partial)
        if partial and self.category == 'aoa':
            # A killed capture can leave raw files before its first metadata commit.
            stems = {match[1] for path in self.spool.iterdir()
                     if (match := re.match(r'(aoa_\d+_\d+)_', path.name))}
            for stem in sorted(stems):
                marker = self.spool/(stem + '_metadata.json')
                write_json(marker, {'run_id': stem, 'processing_complete': False,
                                    'status': 'interrupted_before_metadata'})
                self.archive(marker, partial=True, stem=stem)
