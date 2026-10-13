import tempfile,time,unittest
from pathlib import Path
from PIL import Image
from app import Library
from native_rating import RatingError,VALUES,value_from_stars,stars_from_value
from rating_records import RatingRecords

class Engine:
    def __init__(self):self.stars=0;self.fail=False
    def run(self,path,stars=None,expected=None):
        before=self.stars
        if expected is not None and expected!=before:raise RatingError('external change')
        if stars is not None:
            with open(path,'ab') as handle:handle.write(b'new metadata')
            if self.fail:raise RatingError('handler crashed')
            self.stars=stars
        return dict(stars=self.stars,value=VALUES[self.stars],before=before,writable=True,reason='')

class Ratings(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.photos=self.root/'photos';self.photos.mkdir();self.path=self.photos/'one.jpg';Image.new('RGB',(60,90),'blue').save(self.path)
        self.library=Library(self.root/'workspace');self.library.scan(self.photos)
        deadline=time.monotonic()+5
        while self.library.job['running'] and time.monotonic()<deadline:time.sleep(.01)
        self.pid=self.library.list_photos()[0]['id'];self.records=RatingRecords(self.library);self.engine=Engine()
    def tearDown(self):self.temp.cleanup()
    def test_native_mapping(self):
        self.assertEqual([stars_from_value(value_from_stars(i)) for i in range(6)],list(range(6)))
        self.assertEqual([stars_from_value(i) for i in (0,1,12,13,37,38,62,63,87,88,99)],[0,1,1,2,2,3,3,4,4,5,5])
        for i in (-1,6,True,'5'):
            with self.assertRaises(RatingError):value_from_stars(i)
    def test_kept_status_and_culling_undo_survive_rating(self):
        self.library.mark(self.pid,1);mtime=self.path.stat().st_mtime_ns
        self.records.write(self.pid,4,self.engine)
        self.assertEqual(self.library.photo(self.pid)['status'],1);self.assertEqual(self.path.stat().st_mtime_ns,mtime)
        self.assertEqual(self.records.ids('keep',4),[self.pid]);self.library.undo();self.assertEqual(self.library.photo(self.pid)['status'],0)
    def test_partial_native_write_restores_entire_original_and_database(self):
        old=self.path.read_bytes();row=dict(self.library.photo(self.pid));self.engine.fail=True
        with self.assertRaises(RatingError):self.records.write(self.pid,5,self.engine)
        self.assertEqual(self.path.read_bytes(),old);self.assertEqual(dict(self.library.photo(self.pid)),row)
        with self.library.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_history').fetchone()[0],0)
    def test_rating_undo_checks_external_changes(self):
        self.records.write(self.pid,3,self.engine);self.engine.stars=5
        with self.assertRaises(RatingError):self.records.undo(self.engine)
        self.engine.stars=3;self.records.undo(self.engine);self.assertEqual(self.engine.stars,0)
    def test_unknown_is_not_zero_and_stale_cache_excluded(self):
        self.assertEqual(self.records.ids(stars=0),[]);self.assertEqual(self.records.ids(unknown=True),[self.pid])
        self.records.read(self.pid,self.engine);self.assertEqual(self.records.ids(stars=0),[self.pid])
        with self.library.connect() as db:db.execute('UPDATE photos SET size=size+1')
        self.assertEqual(self.records.ids(stars=0),[])
    def test_unavailable_read_is_not_fake_unrated(self):
        class Broken:
            def run(self,*_):raise RatingError('unsupported')
        result=self.records.read(self.pid,Broken());self.assertEqual(result['state'],'error');self.assertIsNone(result['stars']);self.assertEqual(self.records.ids(stars=0),[])

    def test_database_commit_failure_rolls_back_metadata_and_fingerprint(self):
        old=self.path.read_bytes();row=dict(self.library.photo(self.pid))
        with self.library.connect() as db:db.execute("CREATE TRIGGER fail_rating BEFORE INSERT ON rating_history BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        with self.assertRaises(Exception):self.records.write(self.pid,4,self.engine)
        self.assertEqual(self.path.read_bytes(),old);self.assertEqual(dict(self.library.photo(self.pid)),row)

    def test_external_changes_invalidate_cached_system_stars_and_disable_writes(self):
        self.records.read(self.pid,self.engine)
        self.path.write_bytes(self.path.read_bytes()+b'external metadata')
        self.assertEqual(self.records.cached([self.pid]),{})
        self.assertFalse(self.records.read(self.pid,self.engine)['writable'])

    def test_removed_photo_history_does_not_offer_impossible_rating_undo(self):
        self.records.write(self.pid,4,self.engine)
        with self.library.connect() as db:db.execute('UPDATE photos SET present=0 WHERE id=?',(self.pid,))
        with self.assertRaises(RatingError):self.records.undo(self.engine)
