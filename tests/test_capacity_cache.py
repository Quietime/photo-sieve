"""Retain caches at the limit; exceeding it evicts the oldest 70% by file count."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from PIL import Image
from app import Library
from cache_policy import prune_workspaces

class CapacityCacheTest(unittest.TestCase):
    def test_equal_limit_is_retained_and_exceeding_evicts_oldest_seventy_percent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); cache=root/'cache'; cache.mkdir()
            files=[]
            for i in range(10):
                path=cache/f'{i}.jpg';path.write_bytes(b'x'*10);os.utime(path,(100+i,100+i));files.append(path)
            self.assertEqual(prune_workspaces(root,limit=100),(0,100))
            self.assertTrue(all(p.exists() for p in files))
            self.assertEqual(prune_workspaces(root,limit=99),(7,30))
            self.assertFalse(any(p.exists() for p in files[:7]));self.assertTrue(all(p.exists() for p in files[7:]))

    def test_new_cache_registration_triggers_cleanup_without_waiting_for_a_timer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve(); photos=root/'photos';photos.mkdir(); library=Library(root/'data')
            library.cache_limit=99; files=[]
            for i in range(10):
                source=photos/f'{i}.jpg';Image.new('RGB',(8,8),'blue').save(source);stat=source.stat()
                with library.connect() as db:
                    pid=db.execute('INSERT INTO photos(path,size,mtime) VALUES (?,?,?)',(str(source),stat.st_size,stat.st_mtime_ns)).lastrowid
                row=library.photo(pid); path=library.cache_path(source,stat.st_size,stat.st_mtime_ns,320)
                path.write_bytes(b'x'*10);os.utime(path,(100+i,100+i));files.append(path)
                library.register_preview_cache(row,320)
            deadline=time.monotonic()+5
            while len(list(library.cache.glob('*.jpg')))!=3 and time.monotonic()<deadline: time.sleep(.005)
            with library.prune_lock: pass
            self.assertEqual(len(list(library.cache.glob('*.jpg'))),3)
            self.assertTrue(all(p.exists() for p in files[7:]));self.assertEqual(library.cache_bytes,30)
            self.assertEqual(len(list(photos.glob('*.jpg'))),10)

    def test_reading_a_cache_does_not_make_an_old_generation_newer(self):
        with tempfile.TemporaryDirectory() as temporary:
            library=Library(Path(temporary)/'data'); path=library.cache/'old.jpg'; path.write_bytes(b'preview');os.utime(path,(100,100))
            before=path.stat().st_mtime_ns;self.assertEqual(library.read_cache(path),b'preview')
            self.assertEqual(path.stat().st_mtime_ns,before)

    def test_per_library_capacity_never_prunes_other_workspace_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            library=Library(Path(temporary).resolve()); other=library.directory/'libraries'/'other'/'cache';other.mkdir(parents=True)
            file=other/'keep.jpg';file.write_bytes(b'x'*100)
            library.cache_limit=1; library.prune_cache();self.assertTrue(file.exists())
