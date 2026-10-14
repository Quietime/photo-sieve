import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from photo_metadata import shooting_info,shooting_infos,format_info,MetadataReader

class MetadataTest(unittest.TestCase):
    def test_jpeg_read_is_complete_and_leaves_file_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'photo.jpg';exif=Image.Exif()
            exif[271]='Sony';exif[272]='ILCE-7M4';exif[34665]={33437:2.8,33434:1/160,34855:800,36867:'2026:10:14 12:30:00',37386:85,42036:'FE 85mm F1.8'}
            Image.new('RGB',(50,60)).save(path,exif=exif);before=hashlib.sha256(path.read_bytes()).digest()
            result=shooting_info(path)
            for value in ['Sony ILCE-7M4','FE 85mm F1.8','85 mm','f/2.8','1/160s','ISO 800','2026-10-14 12:30:00']:self.assertIn(value,result)
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),before)
    def test_no_exif_does_not_fabricate_camera_or_shooting_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'photo.jpg';Image.new('RGB',(30,30)).save(path);os.utime(path,(1,1))
            self.assertEqual(shooting_info(path),'')
    def test_raw_component_reads_new_fields_and_omits_unknown_lens(self):
        row={'Make':'SONY','Model':'ILCE-7S','LensModel':'----','ISO':400,'FocalLength':35,'ExposureTime':1/250,'DateTimeOriginal':'2017:07:13 16:58:28'}
        with patch('embedded_rating._run',return_value=json.dumps([row]).encode()) as command:
            result=shooting_info('camera.ARW')
        self.assertIn('SONY ILCE-7S',result);self.assertIn('35 mm',result);self.assertNotIn('----',result)
        self.assertIn('-LensModel',command.call_args.args[0]);self.assertIn('-FocalLength',command.call_args.args[0])
    def test_camera_brand_is_not_duplicated_and_missing_fields_are_hidden(self):
        self.assertEqual(format_info(make='Sony',model='Sony ILCE-7M4',lens='Unknown'),'Sony ILCE-7M4')

    def test_raw_batch_uses_one_command_and_keeps_results_aligned_after_missing_or_reordered_files(self):
        rows=[{'SourceFile':'third.ARW','ISO':300},{'SourceFile':'first.ARW','ISO':100}]
        with patch('embedded_rating._run',return_value=json.dumps(rows).encode()) as command:
            result=shooting_infos(['first.ARW','missing.ARW','third.ARW'])
        self.assertEqual(result,['ISO 100','','ISO 300'])
        self.assertEqual(command.call_count,1)
        self.assertEqual(command.call_args.args[0][-3:],['first.ARW','missing.ARW','third.ARW'])

    def test_jpeg_and_raw_batch_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'photo.jpg';exif=Image.Exif();exif[271]='Sony'
            Image.new('RGB',(20,20)).save(path,exif=exif)
            with patch('embedded_rating._run',return_value=json.dumps([{'SourceFile':'camera.ARW','ISO':800}]).encode()) as command:
                result=shooting_infos([path,'camera.ARW'])
        self.assertEqual(result,['Sony','ISO 800'])
        self.assertEqual(command.call_args.args[0][-2:],['--','camera.ARW'])

    def test_corrupt_raw_metadata_does_not_hide_other_valid_rows(self):
        rows=[{'SourceFile':'bad.ARW','FNumber':'not a number'},{'SourceFile':'good.ARW','FNumber':2.8}]
        with patch('embedded_rating._run',return_value=json.dumps(rows).encode()):
            self.assertEqual(shooting_infos(['bad.ARW','good.ARW']),['','f/2.8'])
        with patch('embedded_rating._run',side_effect=ValueError('unavailable')):
            self.assertEqual(shooting_infos(['first.ARW','second.ARW']),['',''])

    def test_duplicate_raw_paths_are_aligned_and_empty_batch_starts_no_command(self):
        with patch('embedded_rating._run',return_value=json.dumps([{'SourceFile':'camera.ARW','ISO':400}]).encode()) as command:
            self.assertEqual(shooting_infos([]),[])
            self.assertEqual(command.call_count,0)
            self.assertEqual(shooting_infos(['camera.ARW','camera.ARW']),['ISO 400','ISO 400'])

    def test_reader_read_and_read_many_share_one_real_spawn_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'photo.jpg';exif=Image.Exif();exif[271]='Sony'
            Image.new('RGB',(20,20)).save(path,exif=exif)
            reader=MetadataReader()
            try:
                self.assertEqual(reader.read(path),'Sony')
                executor=reader.executor
                self.assertEqual(reader.read_many([path,path]),['Sony','Sony'])
                self.assertIs(reader.executor,executor)
                self.assertEqual(len(executor._processes),1)
            finally:reader.close()
            self.assertEqual(reader.read_many([path,path]),['',''])
            self.assertEqual(reader.read(path),'')
            self.assertEqual(reader.read_many([]),[])

    def test_close_terminates_hung_batch_and_joins_after_kill(self):
        import threading,time
        from concurrent.futures import Future
        class Process:
            alive=True
            def __init__(self):self.joins=0;self.killed=False
            def is_alive(self):return self.alive
            def terminate(self):pass
            def join(self,timeout):self.joins+=1
            def kill(self):self.alive=False;self.killed=True
        class Executor:
            def __init__(self):self.process=Process();self._processes={1:self.process};self.called=threading.Event();self.stopped=False
            def submit(self,*args):self.called.set();return Future()
            def shutdown(self,**kwargs):self.stopped=True
        executor=Executor();reader=MetadataReader();results=[]
        with patch('concurrent.futures.ProcessPoolExecutor',return_value=executor):
            worker=threading.Thread(target=lambda:results.append(reader.read_many(['first.ARW','second.ARW'])))
            worker.start();self.assertTrue(executor.called.wait(1));reader.close();worker.join(1)
        self.assertFalse(worker.is_alive());self.assertEqual(results,[['','']])
        self.assertTrue(executor.process.killed);self.assertEqual(executor.process.joins,2);self.assertTrue(executor.stopped)

if __name__=='__main__':unittest.main()
