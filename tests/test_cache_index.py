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

    def test_kept_cache_is_retained_without_hashing_uncached_ten_thousand_rows(self):
        old=self.preview(); self.library.mark(1,1); os.utime(old,(1,1))
        with self.library.connect() as db:
            db.executemany('INSERT INTO photos(id,path,size,mtime,status) VALUES (?,?,1,1,1)',
                           ((i,str(self.photos/f'{i}.jpg')) for i in range(2,10002)))
        with patch('app.cache_file',side_effect=AssertionError('Recurring cleanup must not derive hashes from all photos')):
            self.assertEqual(self.library.cleanup_processed_cache(),0)
        self.assertTrue(self.source.is_file()); self.assertTrue(old.exists())
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
        self.assertEqual(self.library.cleanup_processed_cache(),0); self.assertTrue(self.source.exists())
        self.assertEqual(len(list(self.library.cache.glob('*.jpg'))),2)

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
            self.assertEqual(expire_inactive_workspaces(self.base/'workspaces',self.library.directory),0)
        self.assertEqual(len(list(library.cache.glob('*.jpg'))),2)
        self.assertTrue(self.source.exists()); self.assertTrue(library.database.exists())

    def test_cache_index_cannot_delete_files_outside_derived_cache(self):
        with self.library.connect() as db:
            db.execute('INSERT INTO cache_index VALUES (?,?,?,?,?)',('../../photos/photo.jpg',1,self.row['size'],self.row['mtime'],320))
        self.library.mark(1,1)
        self.assertEqual(self.library.cleanup_processed_cache(grace=0),0); self.assertTrue(self.source.exists())


    def test_cleanup_skips_busy_photo_then_removes_after_release(self):
        import threading
        old=self.preview();self.library.trash(1);os.utime(old,(1,1))
        locked=threading.Event();release=threading.Event()
        def reader():
            with self.library.file_locks[1]:locked.set();release.wait(3)
        worker=threading.Thread(target=reader);worker.start();self.assertTrue(locked.wait(1))
        try:
            start=time.monotonic();self.assertEqual(self.library.cleanup_processed_cache(),0)
            self.assertLess(time.monotonic()-start,.2);self.assertTrue(old.exists())
        finally:release.set();worker.join(1)
        self.assertEqual(self.library.cleanup_processed_cache(),1)

    def test_cleanup_drops_stale_face_metadata_but_preserves_valid_detection(self):
        from face_service import SCHEMA
        with self.library.connect() as db:
            db.execute(SCHEMA)
            for key,size in [('valid',self.row['size']),('outdated',self.row['size']+1)]:
                db.execute('INSERT INTO face_metadata VALUES (?,?,?,?,?,?,?)',(key,str(self.source),size,self.row['mtime'],'model','[]','{}'))
        self.library.cleanup_processed_cache()
        with self.library.connect() as db:self.assertEqual([r[0] for r in db.execute('SELECT cache_key FROM face_metadata')],['valid'])

    def test_one_click_clears_all_workspace_derived_caches_only(self):
        from cache_policy import clear_workspace_caches
        from face_service import SCHEMA
        other=Library(self.library.directory/'libraries'/'other')
        for library in (self.library,other):
            if library is other:
                with library.connect() as db:db.execute('INSERT INTO photos(id,path,size,mtime) VALUES (1,?,?,?)',(str(self.source),self.row['size'],self.row['mtime']))
            library.mark(1,1)
            (library.cache/'photo.jpg').write_bytes(b'preview')
            (library.cache/'face.png').write_bytes(b'crop')
            (library.cache/'keep.json').write_text('records')
            with library.connect() as db:
                db.execute(SCHEMA);db.execute('INSERT INTO face_metadata VALUES (?,?,?,?,?,?,?)',('known',str(self.source),self.row['size'],self.row['mtime'],'model','[]','{}'))
        before=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(clear_workspace_caches(self.library.directory,self.library),4)
        for library in (self.library,other):
            self.assertEqual([p.name for p in library.cache.iterdir()],['keep.json'])
            self.assertEqual(library.photo(1)['status'],1)
            with library.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM history').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM face_metadata').fetchone()[0],0)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),before)

if __name__=='__main__': unittest.main()
