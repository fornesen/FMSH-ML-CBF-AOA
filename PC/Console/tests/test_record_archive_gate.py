"""A result-ready marker cannot commit an incomplete/mixed recording."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'Board/runtime'))
from archive_spool import ArchiveSpool

class RecordArchiveGateTests(unittest.TestCase):
    def test_complete_result_requires_its_frozen_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);spool=root/'spool';spool.mkdir()
            metadata=spool/'aoa_1_metadata.json'
            metadata.write_text(json.dumps({'run_id':'1','record_raw':True,'processing_complete':True,
                                            'capture_round_count':4,'completed_capture_rounds':4}))
            archive=ArchiveSpool(spool,root/'output')
            with self.assertRaises(FileNotFoundError):archive.archive(metadata)
            self.assertTrue(metadata.exists());self.assertEqual(list((root/'output').iterdir()),[])

    def test_capture_may_not_refer_to_other_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);spool=root/'spool';spool.mkdir()
            metadata=spool/'aoa_1_metadata.json'
            metadata.write_text(json.dumps({'run_id':'1','record_raw':True,'processing_complete':True,
                                            'capture_round_count':4,'completed_capture_rounds':4}))
            (spool/'aoa_1_capture.json').write_text(json.dumps({'schema':'aoa.raw_capture.v1','run_id':'2'}))
            with self.assertRaisesRegex(ValueError,'identity'):
                ArchiveSpool(spool,root/'output').archive(metadata)
            self.assertTrue(metadata.exists())

if __name__=='__main__':unittest.main()
