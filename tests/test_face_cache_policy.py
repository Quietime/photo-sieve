"""Native PNG face crops share the preview budget without touching source/records."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest

from PIL import Image
from app import Library
from cache_policy import prune_workspaces


class FaceCachePolicyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / 'source.jpg'
        Image.new('RGB', (160,100), 'navy').save(self.source)
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.library = Library(self.base / 'workspaces')
        self.library.remember('root', str(self.base))
        stat = self.source.stat()
        with self.library.connect() as db:
            db.execute('INSERT INTO photos(id,path,size,mtime) VALUES (1,?,?,?)',
                       (str(self.source),stat.st_size,stat.st_mtime_ns))
        self.face = self.library.cache / 'face-test.png'
        Image.new('RGB', (32,32), 'pink').save(self.face)
        with self.library.connect() as db:
            db.execute('INSERT INTO cache_index VALUES (?,1,?,?,9001)',
                       (self.face.name,stat.st_size,stat.st_mtime_ns))

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.source_hash)
        self.assertTrue(self.library.database.is_file())
        self.temporary.cleanup()

    def test_clear_removes_png_but_keeps_unknown_file_and_records(self):
        records = self.library.cache / 'do-not-delete.json'
        records.write_text('{"stars":5}')
        self.library.mark(1,1)
        self.assertEqual(self.library.clear_preview_cache(),1)
        self.assertFalse(self.face.exists())
        self.assertTrue(records.exists())
        self.assertEqual(self.library.photo(1)['status'],1)
        with self.library.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cache_index').fetchone()[0],0)

    def test_processed_png_obeys_two_minute_undo_grace(self):
        self.library.mark(1,1)
        os.utime(self.face,(time.time()-60,time.time()-60))
        self.assertEqual(self.library.cleanup_processed_cache(),0)
        self.assertTrue(self.face.exists())
        os.utime(self.face,(time.time()-121,time.time()-121))
        self.assertEqual(self.library.cleanup_processed_cache(),1)
        self.assertFalse(self.face.exists())
        self.assertEqual(self.library.photo(1)['status'],1)

    def test_limit_evicts_oldest_seventy_percent_of_mixed_png_jpeg(self):
        self.face.unlink()
        paths = []
        for index in range(10):
            path = self.library.cache / ('%02d.%s' % (index,'png' if index % 2 else 'jpg'))
            path.write_bytes(bytes(100))
            os.utime(path,(index+1,index+1))
            paths.append(path)
        removed,total = prune_workspaces(self.library.directory,limit=1000)
        self.assertEqual((removed,total),(7,300))
        self.assertEqual([p.name for p in paths if p.exists()],['07.png','08.jpg','09.png'])


if __name__ == '__main__':
    unittest.main()
