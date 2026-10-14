"""Concurrent camera metadata and Windows sharing violations during rapid trash."""
import os,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import Library,LibraryError

class DeleteSharingTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.photos=self.root/'photos';self.photos.mkdir()
        for i in range(3):Image.new('RGB',(30,40)).save(self.photos/f'{i}.jpg')
        self.lib=Library(self.root/'workspace');self.lib.scan(self.photos)
        while self.lib.job['running']:time.sleep(.01)
        self.ids=[p['id'] for p in self.lib.list_photos()]
    def tearDown(self):self.temp.cleanup()
    def test_metadata_read_finishes_before_move_and_deleted_files_are_not_reopened(self):
        entered=threading.Event();release=threading.Event();moved=threading.Event();errors=[]
        def reader(path):
            with open(path,'rb') as handle:
                entered.set();release.wait(3);return handle.read(3)
        def reading():
            try:self.lib.read_metadata(self.ids[0],reader)
            except Exception as exc:errors.append(exc)
        def deleting():
            try:self.lib.trash(self.ids[0]);moved.set()
            except Exception as exc:errors.append(exc)
        a=threading.Thread(target=reading);b=threading.Thread(target=deleting);a.start();self.assertTrue(entered.wait(2));b.start()
        try:self.assertFalse(moved.wait(.08))
        finally:release.set();a.join(3);b.join(3)
        self.assertTrue(moved.is_set());self.assertEqual(errors,[])
        with patch('builtins.open',side_effect=AssertionError('deleted original reopened')):
            self.assertEqual(self.lib.read_metadata(self.ids[0],reader),'')
    def test_reader_failure_releases_lock_for_delete(self):
        with self.assertRaises(OSError):self.lib.read_metadata(self.ids[0],lambda _:(_ for _ in ()).throw(OSError('bad EXIF')))
        self.assertTrue(self.lib.trash(self.ids[0])['staged'])
    def test_transient_windows_share_errors_retry_one_move_and_one_history_entry(self):
        real=os.rename;attempts=[]
        def rename(source,target):
            attempts.append(target)
            if len(attempts)<3:
                error=OSError('sharing');error.winerror=32;raise error
            return real(source,target)
        with patch('app.os.rename',side_effect=rename):result=self.lib.trash(self.ids[0])
        self.assertEqual(len(attempts),3);self.assertEqual(len(set(attempts)),1)
        with self.lib.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM history WHERE photo_id=?',(self.ids[0],)).fetchone()[0],1)
        self.assertTrue(Path(result['backup']).exists())
    def test_persistent_sharing_is_bounded_and_does_not_mark_deleted(self):
        error=OSError('sharing');error.winerror=32;source=self.photos/'0.jpg'
        with patch('app.os.rename',side_effect=error),self.assertRaises(OSError):self.lib.rename_for_trash(source,self.photos/'target',timeout=.04)
        self.assertTrue(source.exists());self.assertEqual(self.lib.photo(self.ids[0])['present'],1)
    def test_other_failures_do_not_retry(self):
        with patch('app.os.rename',side_effect=PermissionError('denied')) as rename,self.assertRaises(PermissionError):
            self.lib.rename_for_trash(self.photos/'0.jpg',self.photos/'target')
        self.assertEqual(rename.call_count,1)

if __name__=='__main__':unittest.main()
