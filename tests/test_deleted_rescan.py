"""Rescan reconciles manual disk deletion without mistaking offline storage for deletion."""
import hashlib,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import Library,LibraryError,TRASH_DIRECTORY

def wait(library):
    end=time.monotonic()+5
    while library.job['running'] and time.monotonic()<end:time.sleep(.005)
    assert not library.job['running']
    assert library.job['message']=='已完成',library.job

class DeletedRescanTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.photos=self.base/'photos';self.photos.mkdir()
        for i in range(3):Image.new('RGB',(40,60),'navy').save(self.photos/f'{i}.jpg')
        self.library=Library(self.base/'data');self.library.scan(self.photos);wait(self.library)
        self.rows=self.library.list_photos('all');self.library.mark(self.rows[0]['id'],1)
        self.backups=[Path(self.library.trash(row['id'])['backup']) for row in self.rows[1:]]
    def tearDown(self):self.tmp.cleanup()
    def rescan(self):self.library.scan(self.photos);wait(self.library)
    def test_missing_backup_is_removed_after_rescan_and_remaining_disk_files_are_untouched(self):
        kept=Path(self.rows[0]['path']);digest=hashlib.sha256(kept.read_bytes()).hexdigest();backup_digest=hashlib.sha256(self.backups[1].read_bytes()).hexdigest()
        self.backups[0].unlink();self.rescan()
        self.assertEqual([p['id'] for p in self.library.list_photos('deleted')],[self.rows[2]['id']])
        self.assertEqual(self.library.state()['counts']['deleted'],1)
        self.assertEqual(hashlib.sha256(kept.read_bytes()).hexdigest(),digest);self.assertEqual(hashlib.sha256(self.backups[1].read_bytes()).hexdigest(),backup_digest)
        with self.library.connect() as db:self.assertEqual(db.execute('SELECT result FROM trash_log WHERE photo_id=?',(self.rows[1]['id'],)).fetchone()[0],'missing')
        self.rescan();self.assertEqual(self.library.state()['counts']['deleted'],1)
    def test_removed_whole_trash_directory_is_confirmed_from_accessible_parent(self):
        for path in self.backups:path.unlink()
        self.backups[0].parent.rmdir();self.rescan()
        self.assertEqual(self.library.state()['counts']['deleted'],0)
        self.assertTrue(Path(self.rows[0]['path']).is_file())
    def test_permission_failure_preserves_deleted_records(self):
        real=os.scandir;folder=self.backups[0].parent
        def denied(path):
            if Path(path)==folder:raise PermissionError('temporarily unavailable')
            return real(path)
        with patch('app.os.scandir',side_effect=denied):self.rescan()
        self.assertEqual(self.library.state()['counts']['deleted'],2)
        self.assertTrue(all(p.is_file() for p in self.backups))
    def test_offline_root_never_clears_deleted_records(self):
        elsewhere=self.base/'offline';self.photos.rename(elsewhere)
        with self.assertRaises(LibraryError):self.library.scan(self.photos)
        self.assertEqual(self.library.state()['counts']['deleted'],2)
        elsewhere.rename(self.photos)
    def test_recreated_root_does_not_resurrect_deleted_history(self):
        kept=Path(self.rows[0]['path']);kept.unlink()
        for path in self.backups:path.unlink()
        self.backups[0].parent.rmdir();self.photos.rmdir()
        self.photos.mkdir();self.rescan()
        self.assertEqual(self.library.state()['counts']['deleted'],0)

if __name__=='__main__':unittest.main()
