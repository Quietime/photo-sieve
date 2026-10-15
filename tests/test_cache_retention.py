"""4.2.8: retained imports, strict 10 GiB eviction and safe folder-switch cleanup."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from app import Library, LibraryError
from cache_policy import MAX_BYTES, prune_workspaces, expire_inactive_workspaces
from workspaces import clear_previous_workspace_cache


class CacheRetentionTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.base=Path(self.temporary.name).resolve()
        self.photos=self.base/'photos';self.photos.mkdir()
        for index in range(3):Image.new('RGB',(160,100),(index*50,30,180)).save(self.photos/f'{index}.jpg')
        self.library=Library(self.base/'workspaces')
        self.library.scan(self.photos)
        deadline=time.monotonic()+5
        while self.library.job['running'] and time.monotonic()<deadline:time.sleep(.005)
        self.assertFalse(self.library.job['running'])
        self.rows=self.library.list_photos()

    def tearDown(self):self.temporary.cleanup()

    def preview(self,row):
        self.library.preview(row['id'],320)
        path=self.library.cache_path(Path(row['path']),row['size'],row['mtime'],320)
        os.utime(path,(1,1));return path

    def face_cache(self,row):
        path=self.library.cache/('face-'+str(row['id'])+'.png')
        Image.new('RGB',(32,32),'pink').save(path);os.utime(path,(1,1))
        with self.library.connect() as db:
            db.execute('INSERT INTO cache_index VALUES (?,?,?,?,?)',(path.name,row['id'],row['size'],row['mtime'],9001))
        return path

    def test_default_library_budget_is_ten_gib_with_explicit_small_test_limit_supported(self):
        self.assertEqual(self.library.cache_limit,MAX_BYTES)
        small=Library(self.base/'small',cache_mb=1)
        self.assertEqual(small.cache_limit,1024**2)

    def test_all_present_pending_and_kept_preview_and_face_caches_are_retained(self):
        files=[]
        for row in self.rows[:2]:files.extend((self.preview(row),self.face_cache(row)))
        self.library.mark(self.rows[1]['id'],1)
        before={path:path.read_bytes() for path in files}
        self.assertEqual(self.library.cleanup_processed_cache(),0)
        self.assertEqual({path:path.read_bytes() for path in files},before)
        self.assertEqual(self.library.state()['counts']['keep'],1)

    def test_deleted_cache_still_expires_and_keeps_reversible_original_and_history(self):
        row=self.rows[0];files=[self.preview(row),self.face_cache(row)]
        original=Path(row['path']).read_bytes();self.library.trash(row['id'])
        backup=Path(self.library.photo(row['id'],include_deleted=True)['backup'])
        self.assertEqual(self.library.cleanup_processed_cache(),2)
        self.assertFalse(any(path.exists() for path in files))
        self.assertEqual(backup.read_bytes(),original)
        self.assertTrue(self.library.undo(only_delete=True)['restored'])
        self.assertEqual(Path(row['path']).read_bytes(),original)

    def test_outdated_fingerprint_is_cleaned_even_if_photo_is_present_and_kept(self):
        row=self.rows[0];stale=[self.preview(row),self.face_cache(row)]
        self.library.mark(row['id'],1)
        with self.library.connect() as db:db.execute('UPDATE photos SET size=size+1 WHERE id=?',(row['id'],))
        self.assertEqual(self.library.cleanup_processed_cache(),2)
        self.assertFalse(any(path.exists() for path in stale))
        self.assertTrue(Path(row['path']).is_file())
        self.assertEqual(self.library.photo(row['id'])['status'],1)

    def test_exact_ten_gib_keeps_every_cache_and_one_byte_over_deletes_oldest_seventy_percent(self):
        cache=self.base/'budget'/'cache';cache.mkdir(parents=True)
        sizes={}
        for index in range(10):
            path=cache/f'{index}.jpg';path.write_bytes(b'x');os.utime(path,(100+index,100+index));sizes[path]=1024**3
        original_stat=Path.stat
        def stat_with_logical_size(path,*args,**kwargs):
            information=original_stat(path,*args,**kwargs)
            if path in sizes:
                values=list(information);values[6]=sizes[path];return os.stat_result(values)
            return information
        # Model 10 GiB using tiny files so Windows runners do not need huge sparse-file support.
        with patch.object(Path,'stat',stat_with_logical_size):
            self.assertEqual(prune_workspaces(cache.parent),(0,MAX_BYTES))
            last=cache/'new-face.png';last.write_bytes(b'x');os.utime(last,(200,200));sizes[last]=1
            self.assertEqual(prune_workspaces(cache.parent),(8,2*1024**3+1))
        self.assertEqual(sorted(path.name for path in cache.iterdir()),['8.jpg','9.jpg','new-face.png'])

    def test_equal_limit_does_not_launch_an_async_pruning_thread(self):
        self.library.cache_bytes=self.library.cache_limit
        with patch('app.threading.Thread') as thread:
            self.library.schedule_prune();thread.assert_not_called()

    def test_capacity_eviction_counter_is_monotonic_and_ignores_manual_or_deleted_cleanup(self):
        self.assertEqual(self.library.cache_evictions,0)
        self.library.cache_limit=10
        for index in range(2):(self.library.cache/f'{index}.jpg').write_bytes(b'123456')
        self.assertEqual(self.library.prune_cache(),2)
        self.assertEqual(self.library.cache_evictions,2)
        (self.library.cache/'manual.png').write_bytes(b'png')
        self.assertEqual(self.library.clear_preview_cache(),1)
        self.assertEqual(self.library.cache_evictions,2)
        self.face_cache(self.rows[0]);self.library.trash(self.rows[0]['id'])
        self.assertEqual(self.library.cleanup_processed_cache(),1)
        self.assertEqual(self.library.cache_evictions,2)

    def test_returning_to_same_folder_keeps_previews_and_face_metadata(self):
        path=self.preview(self.rows[0])
        before=path.read_bytes()
        self.assertEqual(clear_previous_workspace_cache(self.library,self.photos/'..'/'photos'),0)
        self.assertEqual(path.read_bytes(),before)

    def test_switching_different_folder_clears_only_previous_derived_cache(self):
        for row in self.rows:self.preview(row)
        self.library.mark(self.rows[0]['id'],1);self.library.trash(self.rows[1]['id'])
        face=self.face_cache(self.rows[0]);self.library.recount_cache()
        unknown=self.library.cache/'user-notes.json';unknown.write_text('{"note":"keep"}')
        with self.library.connect() as db:
            db.execute('CREATE TABLE user_ratings(photo_id INTEGER PRIMARY KEY,stars INTEGER)')
            db.execute('INSERT INTO user_ratings VALUES (?,5)',(self.rows[0]['id'],))
            db.execute('CREATE TABLE face_metadata(cache_key TEXT PRIMARY KEY,path TEXT,source_size INTEGER,source_mtime INTEGER)')
            db.execute('INSERT INTO face_metadata VALUES (?,?,?,?)',('face',self.rows[0]['path'],self.rows[0]['size'],self.rows[0]['mtime']))
            history=[tuple(row) for row in db.execute('SELECT * FROM history ORDER BY seq')]
        before=self.library.state()['counts']
        backup=Path(self.library.photo(self.rows[1]['id'],include_deleted=True)['backup'])
        originals={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in [Path(self.rows[0]['path']),Path(self.rows[2]['path']),backup]}
        other=Library(self.base/'other-workspace');(other.cache/'other.jpg').write_bytes(b'other preview')
        folder=self.base/'next-photos';folder.mkdir()
        self.assertEqual(clear_previous_workspace_cache(self.library,folder),7)
        self.assertFalse(face.exists());self.assertEqual(list(self.library.cache.iterdir()),[unknown])
        self.assertTrue((other.cache/'other.jpg').exists())
        self.assertEqual(self.library.state()['counts'],before)
        self.assertEqual({path:hashlib.sha256(path.read_bytes()).hexdigest() for path in originals},originals)
        with self.library.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM history ORDER BY seq')],history)
            self.assertEqual(db.execute('SELECT stars FROM user_ratings').fetchone()[0],5)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cache_index').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM face_metadata').fetchone()[0],0)
        self.assertEqual(self.library.cache_bytes,0)

    def test_changed_cache_directory_link_cannot_clear_original_photos(self):
        self.library.cache.rmdir()
        try:self.library.cache.symlink_to(self.photos,target_is_directory=True)
        except OSError:self.skipTest('Directory symlinks unavailable')
        before={path:path.read_bytes() for path in self.photos.iterdir()}
        with self.assertRaisesRegex(LibraryError,'链接'):
            clear_previous_workspace_cache(self.library,self.base/'next-folder')
        self.assertEqual({path:path.read_bytes() for path in self.photos.iterdir()},before)

    def test_global_budget_skips_linked_external_workspaces(self):
        outside=self.base/'outside';(outside/'cache').mkdir(parents=True)
        source=outside/'cache'/'original.jpg';source.write_bytes(b'original')
        projects=self.library.directory/'libraries';projects.mkdir()
        try:(projects/'linked').symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('Directory symlinks unavailable')
        self.assertEqual(prune_workspaces(self.library.directory,limit=1),(0,0))
        self.assertEqual(source.read_bytes(),b'original')


if __name__=='__main__':unittest.main()
