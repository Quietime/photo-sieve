"""Bounded all-photo disk warming, independent of the visible queue and view."""
from collections import Counter,deque
from itertools import islice
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from PIL import Image
from app import Library,LibraryError,PREVIEW_EDGES
from full_cache import FullCache,cache_exists


class Host:
    def __init__(self,library,workers=4):
        self.library=library;self.workers=workers;self.image_pool=object();self.jobs=deque()
        self.loading=set();self.focus_loading=set();self.inflight=set();self.closed=False
        self.max_queued=0;self.view='keep';self.queue=[];self.warmed=set();self.start_ns=100;self.end_ns=101
    def submit(self,work,done,failed,pool=None,priority=0):
        self.jobs.append((work,done,failed,pool,priority));self.max_queued=max(self.max_queued,len(self.jobs))
    def once(self):
        work,done,failed,_,_=self.jobs.popleft()
        try:result=work()
        except Exception as error:failed(str(error))
        else:done(result)
    def drain(self,limit=50000):
        for _ in range(limit):
            if not self.jobs:return
            self.once()
        raise AssertionError('Full cache did not stop scheduling work')


class FakeLibrary:
    def __init__(self,root,count=20):
        self.cache=Path(root)/'cache';self.cache.mkdir(parents=True)
        self.count=count;self.job={'running':False};self.calls=[];self.pages=[];self.failures=Counter();self.deleted=set()
        self.evicted=set();self.keep=set();self.cache_sizes={};self.offload=object()
        out=io.BytesIO();Image.new('RGB',(16,12),'navy').save(out,'JPEG');self.payload=out.getvalue();self.cache_evictions=0
    def row(self,pid):return {'id':pid,'path':str(self.cache.parent/f'{pid}.arw'),'size':pid+100,'mtime':pid,'status':int(pid in self.keep),'present':1}
    def list_photos(self,view,anchor=0,direction='next',limit=60,sort='file',**filters):
        self.pages.append((view,anchor,direction,limit,sort,filters))
        return list(islice((self.row(pid)for pid in range(anchor+1,self.count+1)if pid not in self.deleted),limit))
    def photo(self,pid):
        if pid in self.deleted:raise LibraryError(404,'deleted')
        return self.row(pid)
    def cache_path(self,source,size,mtime,edge):return self.cache/f'{Path(source).stem}-{edge}.jpg'
    def preview(self,pid,edge):
        self.calls.append((pid,edge))
        if self.failures[pid]:self.failures[pid]-=1;raise ValueError('decode failed')
        if pid not in self.evicted:self.cache_path(Path(self.row(pid)['path']),pid+100,pid,edge).write_bytes(self.payload)
        return b'preview bytes'


class FullCacheTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        self.library=FakeLibrary(self.root);self.host=Host(self.library)
    def tearDown(self):self.temporary.cleanup()
    def service(self,**kwargs):return FullCache(self.host,auto_timer=False,**kwargs)

    def test_every_pending_and_kept_photo_is_cached_despite_empty_date_filtered_view(self):
        self.library.count=1327;self.library.keep=set(range(1,1328,2));self.library.deleted={4,10,1201}
        service=self.service(page_size=73);service.start(self.library,3200,total=9999);self.host.drain()
        status=service.status();expected=set(range(1,1328))-self.library.deleted
        self.assertEqual({pid for pid,_ in self.library.calls},expected)
        self.assertTrue(status['complete']);self.assertEqual(status['visited'],len(expected));self.assertEqual(status['total'],len(expected))
        self.assertTrue(all(edge==3200 for _,edge in self.library.calls))
        self.assertTrue(all(view=='all' and direction=='next' and sort=='file' and not filters for view,_,direction,_,sort,filters in self.library.pages))
        self.assertLessEqual(self.host.max_queued,service.max_jobs+1)

    def test_metadata_and_retry_storage_stay_bounded_for_a_hundred_thousand_rows(self):
        self.library.count=100000;service=self.service(page_size=64,max_jobs=2);service.start(self.library,2400)
        for _ in range(200):
            self.host.once()
            self.assertLessEqual(len(service.pending)+len(service.active)+len(service.retries),64+2+128)
        self.assertLess(service.discovered,100000);service.stop();self.host.drain()

    def test_foreground_loading_preempts_new_background_work_and_resumes(self):
        self.host.loading.add(1);service=self.service();service.start(self.library,1600)
        self.assertFalse(self.host.jobs);self.assertEqual(service.status()['phase'],'paused')
        self.host.loading.clear();service.pump();self.host.once()
        self.assertTrue(self.host.jobs)
        self.host.focus_loading.add(1);self.host.drain()
        self.assertFalse(self.library.calls,'Queued work rechecks foreground priority before opening an original')
        self.host.focus_loading.clear();service.pump();self.host.drain()
        self.assertTrue(service.status()['complete']);self.assertEqual(len(self.library.calls),20)

    def test_real_files_override_stale_warmed_and_byte_ledger_entries(self):
        self.library.count=1;self.host.warmed.add(1)
        path=self.library.cache_path(Path(self.library.row(1)['path']),101,1,3200)
        self.library.cache_sizes[path.name]=999
        service=self.service();service.start(self.library,3200);self.host.drain()
        self.assertEqual(self.library.calls,[(1,3200)]);self.assertTrue(service.status()['complete'])

    def test_matching_higher_photo_cache_is_reused_but_face_and_full_original_are_not(self):
        self.library.count=3
        for pid,edge in ((1,4800),(2,9001),(3,0)):
            row=self.library.row(pid);self.library.cache_path(Path(row['path']),row['size'],row['mtime'],edge).write_bytes(self.library.payload)
        service=self.service();service.start(self.library,3200);self.host.drain()
        self.assertEqual(service.status()['cached'],1);self.assertEqual({pid for pid,_ in self.library.calls},{2,3})

    def test_capacity_eviction_does_not_trigger_another_sweep_or_false_resident_success(self):
        self.library.count=4;self.library.evicted={1,2}
        service=self.service();service.start(self.library,2400);self.host.drain()
        self.assertTrue(service.status()['finished']);self.assertFalse(service.status()['complete'])
        self.assertEqual(service.status()['ready'],2);self.assertEqual(service.status()['evicted'],2)
        for path in self.library.cache.glob('*.jpg'):path.unlink()
        for _ in range(10):service.pump()
        self.host.drain();self.assertEqual(len(self.library.calls),4)

    def test_transient_errors_retry_once_and_permanent_errors_are_not_complete(self):
        self.library.count=3;self.library.failures.update({1:1,2:99})
        service=self.service();service.start(self.library,1600);self.host.drain()
        status=service.status();counts=Counter(pid for pid,_ in self.library.calls)
        self.assertEqual(counts,Counter({1:2,2:2,3:1}));self.assertEqual(status['ready'],2);self.assertEqual(status['errors'],1)
        self.assertTrue(status['finished']);self.assertFalse(status['complete']);self.assertEqual(status['visited'],3)

    def test_retry_and_error_details_are_bounded_even_if_all_images_fail(self):
        self.library.count=300;self.library.failures.update({pid:99 for pid in range(1,301)})
        service=self.service(page_size=35);service.start(self.library,1600);self.host.drain()
        status=service.status();self.assertEqual(status['errors'],300);self.assertLessEqual(len(status['error_details']),128)
        self.assertLessEqual(len(self.library.calls),300+128);self.assertFalse(status['complete'])

    def test_switching_library_cancels_queued_old_work_and_ignores_old_results(self):
        service=self.service();service.start(self.library,1600);self.host.once()
        other=FakeLibrary(self.root/'other',count=2);self.host.library=other;service.start(other,3200)
        self.host.drain()
        self.assertFalse(self.library.calls);self.assertEqual(other.calls,[(1,3200),(2,3200)])
        self.assertTrue(service.status()['complete']);self.assertEqual(service.status()['visited'],2)

    def test_deleted_photo_disappearing_after_page_load_is_skipped_without_reopening(self):
        service=self.service();service.start(self.library,1600);self.host.once();self.library.deleted.add(1);self.host.drain()
        self.assertNotIn(1,{pid for pid,_ in self.library.calls});self.assertEqual(service.status()['skipped'],1)

    def test_edge_zero_is_rejected_and_manual_restart_verifies_missing_files_again(self):
        service=self.service()
        with self.assertRaises(ValueError):service.start(self.library,0)
        self.library.count=1;service.start(self.library,1600);self.host.drain()
        for path in self.library.cache.glob('*.jpg'):path.unlink()
        service.start(self.library,1600);self.host.drain();self.assertEqual(len(self.library.calls),2)

    def test_restored_photo_is_refreshed_without_relisting_all_photos(self):
        self.library.count=10;self.library.deleted.add(1);service=self.service(page_size=3)
        service.start(self.library,3200);self.host.drain();pages=len(self.library.pages)
        self.library.deleted.clear();self.assertTrue(service.refresh_photo(1));self.host.drain()
        self.assertEqual(len(self.library.pages),pages);self.assertIn((1,3200),self.library.calls)
        self.assertEqual(service.status()['refreshed'],1);self.assertEqual(service.status()['refresh_ready'],1)
        self.assertEqual(service.status()['visited'],9);self.assertTrue(service.status()['finished'])

    def test_repeated_restore_notifications_are_deduplicated_and_bounded(self):
        service=self.service();service.start(self.library,1600);self.host.loading.add(1)
        for _ in range(5):self.assertTrue(service.refresh_photo(1))
        for pid in range(2,129):self.assertTrue(service.refresh_photo(pid))
        self.assertFalse(service.refresh_photo(129));self.assertEqual(service.status()['refresh_queued'],128)
        service.stop();self.host.drain()

    def test_corrupt_nonempty_cache_is_rebuilt_instead_of_counted_ready(self):
        self.library.count=1;row=self.library.row(1)
        path=self.library.cache_path(Path(row['path']),row['size'],row['mtime'],2400);path.write_bytes(b'not an image')
        self.library.cache_sizes[path.name]=path.stat().st_size
        service=self.service();service.start(self.library,2400);self.host.drain()
        self.assertEqual(self.library.calls,[(1,2400)]);self.assertEqual(service.status()['generated'],1)
        self.assertTrue(cache_exists(self.library,row,2400))

    def test_capacity_eviction_counter_prevents_claiming_the_whole_folder_is_still_cached(self):
        self.library.count=4;service=self.service();service.start(self.library,1600);self.host.once()
        self.library.cache_evictions+=3;self.host.drain()
        self.assertTrue(service.status()['finished']);self.assertFalse(service.status()['complete'])
        self.assertEqual(service.status()['phase'],'capacity_limited');self.assertEqual(service.status()['capacity_evictions'],3)

    def test_real_library_kept_photo_warms_requested_resolution_and_updates_its_index(self):
        folder=self.root/'photos';folder.mkdir();source=folder/'photo.jpg';Image.new('RGB',(400,300),'navy').save(source)
        library=Library(self.root/'records');stat=source.stat();library.remember('root',str(folder))
        with library.connect() as db:db.execute('INSERT INTO photos(id,path,size,mtime,status) VALUES (1,?,?,?,1)',(str(source),stat.st_size,stat.st_mtime_ns))
        self.host.library=library;service=self.service();service.start(library,3200);self.host.drain()
        self.assertTrue(service.status()['complete']);self.assertTrue(cache_exists(library,dict(library.photo(1)),3200))
        with library.connect() as db:self.assertEqual({row[0] for row in db.execute('SELECT edge FROM cache_index')},{320,1600,3200})


if __name__=='__main__':unittest.main()
