"""Entire pending-folder recognition and cache-only foreground hydration."""
import io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import QSettings
from tests.test_full_cache import Host,FakeLibrary
from tests.test_face_service import Host as QtHost,until,application
from full_faces import FullFaces
from app import Library
from face_service import FaceService,cache_key,cache_path,SCHEMA

class FaceHost(Host):
    def __init__(self,library):
        super().__init__(library);self.detected=[]
        self.faces=SimpleNamespace(enabled=True,_done=set(),_remember=lambda *args:None,changed=SimpleNamespace(emit=lambda *args:None),_read_or_compute=self.compute)
    def compute(self,library,row,quality):
        self.detected.append((row['id'],quality));return {'faces':[],'face_count':0,'imagebytes':None}

class FullFacesTest(unittest.TestCase):
    def test_all_view_recognizes_kept_photos_as_well_as_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            library=FakeLibrary(Path(temporary));library.count=40;library.keep={5,22};library.deleted={3}
            host=FaceHost(library);service=FullFaces(host,auto_timer=False);service.view='all'
            service.start(library,1600);host.drain()
            self.assertEqual({pid for pid,_ in host.detected},set(range(1,41))-{3})
    def test_all_pending_rows_recognized_beyond_fifty_even_while_photo_preload_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            library=FakeLibrary(Path(temporary));library.count=1327;library.keep={5,22,1000};library.deleted={3,13}
            host=FaceHost(library);host.loading.add(1)
            service=FullFaces(host,auto_timer=False,page_size=71);service.start(library,1600);host.drain()
            self.assertEqual({pid for pid,_ in host.detected},set(range(1,1328))-library.keep-library.deleted)
            self.assertTrue(all(quality=='original' for _,quality in host.detected));self.assertTrue(service.status()['finished'])
            self.assertTrue(all(page[0]=='pending' for page in library.pages));self.assertFalse(library.calls)
    def test_disabled_recognition_pauses_and_clearing_or_switching_discards_old_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            library=FakeLibrary(Path(temporary));host=FaceHost(library);host.faces.enabled=False
            service=FullFaces(host,auto_timer=False);service.start(library,1600);self.assertFalse(host.jobs)
            host.faces.enabled=True;service.pump();host.once()
            service.stop();host.drain();self.assertFalse(host.detected)
    def test_foreground_only_reads_durable_face_crop_without_running_detector(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);photos=base/'photos';photos.mkdir()
            for i in range(3):Image.new('RGB',(96,144),'navy').save(photos/f'{i}.jpg')
            library=Library(base/'workspace');library.scan(photos);until(lambda:not library.job['running'])
            class ReadyHost(QtHost):
                def submit(self,work,done,failed,pool,priority=0):return super().submit(work,done,failed,pool)
            host=ReadyHost(library);settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat);settings.setValue('face_preview',True);settings.setValue('face_ahead',2)
            host.ahead_target=3
            with patch('face_service.QSettings',return_value=settings):service=FaceService(host)
            service.timer.stop();service.background_all=True
            try:
                with library.connect() as db:
                    db.execute(SCHEMA)
                    for photo in host.queue:
                        data=io.BytesIO();Image.new('RGB',(40,50),'orange').save(data,'PNG');cache_path(library,photo).write_bytes(data.getvalue())
                        metadata={'faces':[{}],'face_count':1,'output_size':[40,50],'quality':'original'}
                        db.execute('INSERT INTO face_metadata VALUES (?,?,?,?,?,?,?)',(cache_key(photo),photo['path'],photo['size'],photo['mtime'],'test','[{}]',json.dumps(metadata)))
                with patch.object(service,'_isolated',side_effect=AssertionError('No frontend recognition')):
                    service._working=True;service.update()
                    until(lambda:len(service._results)==3)
                    for photo in host.queue:self.assertIsNotNone(service.result_for(photo)['imagebytes'])
                host.queue=host.queue[1:];service.update()
                self.assertEqual(service.result_for(host.queue[0])['face_count'],1)
                # Grading follows its selected photo, independently of screening.
                from types import SimpleNamespace
                rating_rows=host.queue[1:];host.rating_active=True
                host.rating_page=SimpleNamespace(active=True,nearby_photos=lambda limit:rating_rows[:limit])
                service._results.clear();service._cache_attempted.clear()
                with patch.object(service,'_isolated',side_effect=AssertionError('No grading recognition')):
                    service.update();until(lambda:service.result_for(rating_rows[0]) is not None)
                self.assertEqual(service.result_for(rating_rows[0])['photo_id'],rating_rows[0]['id'])
            finally:service.shutdown();service.pool.waitForDone();host.deleteLater();application.processEvents()

if __name__=='__main__':unittest.main()
