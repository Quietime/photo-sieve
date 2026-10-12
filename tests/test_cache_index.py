"""Cache maintenance scales with derived files and preserves originals and undo records."""
import os
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image
from app import Library
from cache_policy import expire_inactive_workspaces


class CacheIndexTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.base=Path(self.temporary.name).resolve()
        self.photos=self.base/'photos'; self.photos.mkdir()
        self.source=self.photos/'photo.jpg'; Image.new('RGB',(160,100),'navy').save(self.source)
        self.library=Library(self.base/'workspaces')
        self.library.remember('root',str(self.photos)); stat=self.source.stat()
        with self.library.connect() as db:
            db.execute('INSERT INTO photos(id,path,size,mtime) VALUES (1,?,?,?)',(str(self.source),stat.st_size,stat.st_mtime_ns))
        self.row=self.library.photo(1)

    def tearDown(self): self.temporary.cleanup()

    def preview(self):
        self.library.preview(1,1600)
        return self.library.cache_path(self.source,self.row['size'],self.row['mtime'],320)

    def test_processed_cleanup_does_not_hash_uncached_ten_thousand_rows(self):
        old=self.preview(); self.library.mark(1,1); os.utime(old,(1,1))
        with self.library.connect() as db:
            db.executemany('INSERT INTO photos(id,path,size,mtime,status) VALUES (?,?,1,1,1)',
                           ((i,str(self.photos/f'{i}.jpg')) for i in range(2,10002)))
        with patch('app.cache_file',side_effect=AssertionError('Recurring cleanup must not derive hashes from all photos')):
            self.assertEqual(self.library.cleanup_processed_cache(),1)
        self.assertTrue(self.source.is_file()); self.assertFalse(old.exists())
        self.assertEqual(self.library.state()['counts']['keep'],10001)

    def test_legacy_previews_migrate_once_and_preserve_undo(self):
        old=self.preview(); self.library.trash(1); os.utime(old,(1,1))
        legacy=self.library.cache/(hashlib.sha256(f'v2|{self.source}|{self.row["size"]}|{self.row["mtime"]}|0'.encode()).hexdigest()+'.jpg')
        legacy.write_bytes(b'old full-size derived preview'); os.utime(legacy,(1,1))
        with self.library.connect() as db:
            db.execute('DELETE FROM cache_index'); db.execute("DELETE FROM meta WHERE key='cache_index_version'")
        self.library.cache_indexed.clear()
        self.assertEqual(self.library.cleanup_processed_cache(),2); self.assertFalse(legacy.exists())
        with patch('app.cache_file',side_effect=AssertionError('Legacy migration must only run once')):
            self.assertEqual(self.library.cleanup_processed_cache(),0)
        self.assertTrue(self.library.undo(only_delete=True)['restored']); self.assertTrue(self.source.exists())

    def test_manual_clear_removes_index_and_new_preview_registers_again(self):
        self.preview()
        with self.library.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM cache_index').fetchone()[0],2)
        self.assertEqual(self.library.clear_preview_cache(),2)
        with self.library.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM cache_index').fetchone()[0],0)
        self.preview(); self.library.mark(1,1)
        for path in self.library.cache.glob('*.jpg'): os.utime(path,(1,1))
        self.assertEqual(self.library.cleanup_processed_cache(),2); self.assertTrue(self.source.exists())

    def test_missing_preview_index_is_removed_and_rebuilt_after_decode(self):
        old=self.preview(); old.unlink(); self.library.cleanup_processed_cache()
        with self.library.connect() as db:
            self.assertFalse(db.execute('SELECT 1 FROM cache_index WHERE filename=?',(old.name,)).fetchone())
        self.library.preview(1,320)
        with self.library.connect() as db:
            self.assertTrue(db.execute('SELECT 1 FROM cache_index WHERE filename=?',(old.name,)).fetchone())

    def test_inactive_workspace_uses_registered_files_and_skips_large_metadata(self):
        project=self.base/'workspaces'/'libraries'/'inactive'; library=Library(project)
        with library.connect() as db:
            db.execute('INSERT INTO photos(id,path,size,mtime) VALUES (1,?,?,?)',(str(self.source),self.row['size'],self.row['mtime']))
        library.preview(1,1600); library.mark(1,1)
        for path in library.cache.glob('*.jpg'): os.utime(path,(1,1))
        with patch('app.cache_file',side_effect=AssertionError('Inactive cleanup must reuse its cache index')):
            self.assertEqual(expire_inactive_workspaces(self.base/'workspaces',self.library.directory),2)
        self.assertTrue(self.source.exists()); self.assertTrue(library.database.exists())

    def test_cache_index_cannot_delete_files_outside_derived_cache(self):
        with self.library.connect() as db:
            db.execute('INSERT INTO cache_index VALUES (?,?,?,?,?)',('../../photos/photo.jpg',1,self.row['size'],self.row['mtime'],320))
        self.library.mark(1,1)
        self.assertEqual(self.library.cleanup_processed_cache(grace=0),0); self.assertTrue(self.source.exists())


if __name__=='__main__': unittest.main()
