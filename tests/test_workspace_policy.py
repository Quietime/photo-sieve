import os
from pathlib import Path
import tempfile
import time
import unittest
from PIL import Image
from app import Library, LibraryError
from cache_policy import prune_workspaces
from workspaces import open_workspace

class WorkspacePolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name).resolve()
        self.photos=self.base/'photos'; self.photos.mkdir()
        for i in range(3): Image.new('RGB',(100,80),(i*50,10,40)).save(self.photos/f'{i}.jpg')
        (self.photos/'recording.wav').write_bytes(b'not a photograph')
        (self.photos/'movie.mp4').write_bytes(b'not a photograph')
        self.lib=Library(self.base/'records'); self.lib.scan(self.photos); self.wait()

    def tearDown(self): self.temp.cleanup()

    def wait(self):
        deadline=time.monotonic()+10
        while self.lib.job['running'] and time.monotonic()<deadline: time.sleep(.01)
        self.assertFalse(self.lib.job['running']); self.assertEqual(self.lib.job['message'],'已完成')

    def test_import_only_photos_and_migrate_records_without_copying_originals(self):
        rows=self.lib.list_photos(); self.assertEqual(len(rows),3)
        self.lib.mark(rows[0]['id'],1); self.lib.trash(rows[1]['id']); self.lib.remember('cursor',rows[2]['id'])
        migrated=open_workspace(self.base/'install'/'workspaces',self.photos,self.lib.directory)
        self.assertTrue(migrated.directory.is_relative_to(self.base/'install'))
        self.assertEqual(migrated.state()['counts'],self.lib.state()['counts'])
        self.assertEqual(migrated.meta('cursor'),self.lib.meta('cursor'))
        self.assertEqual(list((self.base/'install').rglob('*.jpg')),[])
        self.assertTrue(migrated.undo(only_delete=True)['restored'])

    def test_permanent_delete_only_selected_and_cannot_be_undone(self):
        rows=self.lib.list_photos(); self.lib.trash(rows[0]['id']); self.lib.trash(rows[1]['id'])
        backup=Path(self.lib.photo(rows[0]['id'],include_deleted=True)['backup'])
        self.lib.purge_deleted([rows[0]['id']]); self.wait()
        self.assertFalse(backup.exists()); self.assertEqual(self.lib.state()['counts']['deleted'],1)
        self.assertTrue(self.lib.undo(only_delete=True)['restored'])
        with self.assertRaises(LibraryError): self.lib.undo(only_delete=True)
        self.assertTrue(Path(rows[2]['path']).exists())

    def test_permanent_delete_refuses_changed_backup(self):
        row=self.lib.list_photos()[0]; self.lib.trash(row['id'])
        backup=Path(self.lib.photo(row['id'],include_deleted=True)['backup']); backup.write_bytes(b'modified by user')
        self.lib.purge_deleted(); self.wait()
        self.assertTrue(backup.exists()); self.assertTrue(self.lib.job['errors'])
        self.assertEqual(self.lib.state()['counts']['deleted'],1)

    def test_deleted_preview_expires_but_old_kept_and_pending_previews_remain(self):
        rows=self.lib.list_photos()
        for row in rows: self.lib.preview(row['id'],320)
        self.lib.mark(rows[0]['id'],1); self.lib.trash(rows[1]['id'])
        recent=self.lib.cache_path(Path(rows[0]['path']),rows[0]['size'],rows[0]['mtime'],320)
        old=self.lib.cache_path(Path(rows[1]['path']),rows[1]['size'],rows[1]['mtime'],320)
        pending=self.lib.cache_path(Path(rows[2]['path']),rows[2]['size'],rows[2]['mtime'],320)
        for path in (recent,old,pending):os.utime(path,(1,1))
        self.lib.cleanup_processed_cache()
        self.assertTrue(recent.exists()); self.assertFalse(old.exists()); self.assertTrue(pending.exists())
        self.assertEqual(self.lib.state()['counts']['deleted'],1); self.lib.undo(only_delete=True)
        self.assertTrue(Path(rows[1]['path']).is_file())

    def test_oldest_first_budget_across_projects_finishes_in_one_pass(self):
        root=self.base/'install'/'workspaces'; files=[]
        for i in range(4):
            folder=root/'libraries'/str(i)/'cache'; folder.mkdir(parents=True)
            path=folder/'preview.jpg'; path.write_bytes(b'x'*10); os.utime(path,(100+i,100+i)); files.append(path)
        removed,total=prune_workspaces(root,limit=15)
        self.assertEqual((removed,total),(3,10)); self.assertFalse(files[0].exists()); self.assertTrue(files[-1].exists())

    def test_manual_clear_never_clears_photos_history_or_staged_files(self):
        row=self.lib.list_photos()[0]; self.lib.preview(row['id'],320); self.lib.trash(row['id'])
        self.assertGreater(self.lib.clear_preview_cache(),0); self.assertTrue(self.lib.database.exists())
        self.assertEqual(self.lib.state()['counts']['deleted'],1); self.assertTrue(self.lib.undo(only_delete=True)['restored'])

    def test_purge_all_includes_every_page(self):
        for i in range(3,70): Image.new('RGB',(10,10),'blue').save(self.photos/f'{i}.jpg')
        self.lib.scan(self.photos); self.wait()
        for row in self.lib.list_photos(limit=100): self.lib.trash(row['id'])
        self.assertEqual(len(self.lib.list_photos('deleted')),60)
        self.lib.purge_deleted(); self.wait()
        self.assertEqual(self.lib.state()['counts']['deleted'],0)
        self.assertFalse(any(self.photos.rglob('*.jpg')))
        self.assertTrue((self.photos/'recording.wav').exists())
