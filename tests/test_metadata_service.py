import tempfile,threading,time,unittest
from pathlib import Path
from PIL import Image
from tests.test_face_service import Host,application,until
from app import Library
from metadata_service import MetadataService

class Label:
    text=''
    def set_text(self,text):self.text=text
    setText=set_text

class Reader:
    def __init__(self):self.calls=[];self.entered=threading.Event();self.release=threading.Event()
    def read_many(self,paths):
        self.calls.append(paths);self.entered.set();self.release.wait(3)
        return [Path(p).name for p in paths]
    def close(self):self.release.set()

class MetadataTests(unittest.TestCase):
    def test_grading_parameters_follow_selected_photo_and_reuse_shared_cache(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);photos=base/'photos';photos.mkdir()
            for i in range(3):Image.new('RGB',(20,30)).save(photos/f'{i}.jpg')
            library=Library(base/'work');library.scan(photos);until(lambda:not library.job['running'])
            host=Host(library);host.inflight=set();host.details=[Label(),Label()];host.metadata_reader=Reader()
            selected=list(host.queue[1:]);label=Label();host.rating_active=True
            host.rating_page=SimpleNamespace(active=True,nearby_photos=lambda limit:selected[:limit],camera_info=label)
            service=MetadataService(host)
            try:
                for photo in selected:service.cache[service.key(library,photo)]='ISO '+str(photo['id'])
                service.update();self.assertEqual(label.text,'ISO '+str(selected[0]['id']))
                selected.pop(0);service.update();self.assertEqual(label.text,'ISO '+str(selected[0]['id']))
                self.assertFalse(host.metadata_reader.calls);self.assertEqual(host.details[1].text,'')
            finally:service.shutdown();until(lambda:not host.tasks)
    def test_current_priority_bounded_work_and_immediate_cached_next(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);photos=base/'photos';photos.mkdir()
            for i in range(40):Image.new('RGB',(20,30)).save(photos/f'{i:03}.jpg')
            library=Library(base/'work');library.scan(photos)
            until(lambda:not library.job['running'])
            host=Host(library);host.inflight=set();host.details=[Label(),Label()];host.metadata_reader=Reader()
            submit=host.submit
            host.submit=lambda work,ok,fail,pool,priority=0:submit(work,ok,fail,pool)
            service=MetadataService(host)
            try:
                service.update();until(host.metadata_reader.entered.is_set)
                for _ in range(20):host.queue.pop(0);service.update()
                self.assertEqual(host.submissions,1)
                self.assertEqual(host.details[1].text,'正在读取拍摄参数…')
                host.metadata_reader.release.set()
                until(lambda:host.details[1].text==host.queue[0]['name'])
                self.assertLessEqual(len(host.metadata_reader.calls),2)
                self.assertEqual(host.metadata_reader.calls[1][0],host.queue[0]['path'])
                host.queue.pop(0);service.update()
                self.assertEqual(host.details[1].text,host.queue[0]['name'])
                self.assertEqual(host.max_workers,1)
            finally:service.shutdown();until(lambda:not host.tasks)

    def test_batch_ignores_deleted_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);photos=base/'photos';photos.mkdir();Image.new('RGB',(20,30)).save(photos/'one.jpg')
            library=Library(base/'work');library.scan(photos);until(lambda:not library.job['running'])
            row=library.list_photos('pending')[0];library.trash(row['id']);reader=Reader();reader.release.set()
            self.assertEqual(library.read_metadata_batch([row],reader,threading.Event()),[])
            self.assertEqual(reader.calls,[])

    def test_transient_error_is_not_cached_as_missing_and_retries_without_queue_flood(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);photos=base/'photos';photos.mkdir();Image.new('RGB',(20,30)).save(photos/'one.jpg')
            library=Library(base/'work');library.scan(photos);until(lambda:not library.job['running'])
            host=Host(library);host.inflight=set();host.details=[Label(),Label()];host.metadata_reader=Reader()
            host.metadata_reader.read_many=Mock(side_effect=[RuntimeError('temporary disk read'),['f/2.8 · ISO 800']])
            submit=host.submit;host.submit=lambda work,ok,fail,pool,priority=0:submit(work,ok,fail,pool)
            service=MetadataService(host)
            try:
                service.update();until(lambda:bool(service.retries))
                key=service.key(library,host.queue[0]);self.assertNotIn(key,service.cache)
                self.assertIn('重试',host.details[1].text)
                for _ in range(10):service.update()
                self.assertEqual(host.submissions,1)
                service.retries[key]=(1,0);service.update()
                until(lambda:host.details[1].text=='f/2.8 · ISO 800')
                self.assertNotIn(key,service.retries);self.assertEqual(host.max_workers,1)
                service.clear();self.assertFalse(service.cache);self.assertFalse(service.retries)
            finally:service.shutdown();until(lambda:not host.tasks)

    def test_partial_missing_result_does_not_discard_valid_neighbor(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);photos=base/'photos';photos.mkdir()
            for i in range(2):Image.new('RGB',(20,30)).save(photos/f'{i}.jpg')
            library=Library(base/'work');library.scan(photos);until(lambda:not library.job['running'])
            host=Host(library);host.inflight=set();host.details=[Label(),Label()];host.metadata_reader=Reader()
            host.metadata_reader.read_many=Mock(return_value=[None,'ISO 400'])
            submit=host.submit;host.submit=lambda work,ok,fail,pool,priority=0:submit(work,ok,fail,pool)
            service=MetadataService(host)
            try:
                service.update();until(lambda:bool(service.retries))
                self.assertNotIn(service.key(library,host.queue[0]),service.cache)
                self.assertEqual(service.cache[service.key(library,host.queue[1])],'ISO 400')
                host.queue.pop(0);service.update();self.assertEqual(host.details[1].text,'ISO 400')
            finally:service.shutdown();until(lambda:not host.tasks)
