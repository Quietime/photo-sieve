import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from photo_metadata import shooting_info,shooting_infos,format_info,MetadataReader,MetadataReadError,_run_metadata

RAW_FIXTURE=Path(os.environ.get('PHOTOSIEVE_ARW_SAMPLE','/workspace/arw-fixtures/sony.ARW'))
if not RAW_FIXTURE.is_file():RAW_FIXTURE=Path('feature-fixtures/sony.ARW')
EXIFTOOL_FIXTURE=Path(os.environ.get('PHOTOSIEVE_EXIFTOOL','/workspace/arw-fixtures/exiftool-13.59/exiftool'))
if not EXIFTOOL_FIXTURE.is_file():EXIFTOOL_FIXTURE=Path('assets/exiftool/exiftool.exe' if os.name=='nt' else 'assets/exiftool/exiftool')

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
        with patch('photo_metadata._run_metadata',return_value=[row]) as command:
            result=shooting_info('camera.ARW')
        self.assertIn('SONY ILCE-7S',result);self.assertIn('35 mm',result);self.assertNotIn('----',result)
        self.assertEqual(command.call_args.args[0],['camera.ARW'])
    def test_camera_brand_is_not_duplicated_and_missing_fields_are_hidden(self):
        self.assertEqual(format_info(make='Sony',model='Sony ILCE-7M4',lens='Unknown'),'Sony ILCE-7M4')

    def test_raw_batch_uses_one_command_and_keeps_results_aligned_after_missing_or_reordered_files(self):
        rows=[{'SourceFile':'third.ARW','ISO':300},{'SourceFile':'first.ARW','ISO':100}]
        with patch('photo_metadata._run_metadata',return_value=rows) as command:
            result=shooting_infos(['first.ARW','missing.ARW','third.ARW'])
        self.assertEqual(result,['ISO 100','','ISO 300'])
        self.assertEqual(command.call_count,1)
        self.assertEqual(command.call_args.args[0][-3:],['first.ARW','missing.ARW','third.ARW'])

    def test_jpeg_and_raw_batch_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'photo.jpg';exif=Image.Exif();exif[271]='Sony'
            Image.new('RGB',(20,20)).save(path,exif=exif)
            with patch('photo_metadata._run_metadata',return_value=[{'SourceFile':'camera.ARW','ISO':800}]) as command:
                result=shooting_infos([path,'camera.ARW'])
        self.assertEqual(result,['Sony','ISO 800'])
        self.assertEqual(command.call_args.args[0],['camera.ARW'])

    def test_corrupt_raw_metadata_does_not_hide_other_valid_rows(self):
        rows=[{'SourceFile':'bad.ARW','FNumber':'not a number'},{'SourceFile':'good.ARW','FNumber':2.8}]
        with patch('photo_metadata._run_metadata',return_value=rows):
            self.assertEqual(shooting_infos(['bad.ARW','good.ARW']),['','f/2.8'])
        with patch('photo_metadata._run_metadata',side_effect=ValueError('unavailable')):
            self.assertEqual(shooting_infos(['first.ARW','second.ARW']),['',''])

    def test_duplicate_raw_paths_are_aligned_and_empty_batch_starts_no_command(self):
        with patch('photo_metadata._run_metadata',return_value=[{'SourceFile':'camera.ARW','ISO':400}]) as command:
            self.assertEqual(shooting_infos([]),[])
            self.assertEqual(command.call_count,0)
            self.assertEqual(shooting_infos(['camera.ARW','camera.ARW']),['ISO 400','ISO 400'])

    def test_strict_batch_distinguishes_missing_from_genuine_empty_exif(self):
        rows=[{'SourceFile':'empty.ARW'},{'SourceFile':'good.ARW','ISO':800}]
        with patch('photo_metadata._run_metadata',return_value=rows):
            self.assertEqual(shooting_infos(['empty.ARW','missing.ARW','good.ARW'],strict=True),['',None,'ISO 800'])
        with patch('photo_metadata._run_metadata',side_effect=MetadataReadError('retry')):
            with self.assertRaisesRegex(MetadataReadError,'retry'):shooting_infos(['good.ARW'],strict=True)
            self.assertEqual(shooting_info('good.ARW'),'')

    def test_windows_unicode_source_names_match_slashes_case_and_long_path_prefix(self):
        requested=['C:\\照片\\晚上场\\当前.ARW','\\\\?\\C:\\照片\\晚上场\\另一张.ARW']
        rows=[{'SourceFile':'c:/照片/晚上场/当前.ARW','ISO':400},{'SourceFile':'C:/照片/晚上场/另一张.ARW','ISO':800}]
        with patch('photo_metadata._run_metadata',return_value=rows):
            self.assertEqual(shooting_infos(requested,strict=True),['ISO 400','ISO 800'])

    def test_metadata_runner_keeps_good_stdout_on_nonzero_partial_error_and_sets_utf8_filenames(self):
        from types import SimpleNamespace
        rows=[{'SourceFile':'照片/正确.ARW','ISO':400}]
        result=SimpleNamespace(returncode=1,stdout=json.dumps(rows).encode(),stderr=b'one missing file')
        with patch('embedded_rating.executable',return_value='/fake/exiftool'),patch('photo_metadata.subprocess.run',return_value=result) as command:
            self.assertEqual(_run_metadata(['照片/正确.ARW','照片/不存在.ARW']),rows)
        arguments=command.call_args.args[0]
        self.assertEqual(arguments[1:3],['-charset','filename=UTF8'])
        self.assertIn('-LensModel',arguments);self.assertIn('-FocalLength',arguments)
        self.assertEqual(arguments[-2:],['照片/正确.ARW','照片/不存在.ARW'])
        self.assertFalse(command.call_args.kwargs['check'],'A partial metadata error must not erase valid rows')

    def test_metadata_runner_reports_command_timeout_and_invalid_output_as_retryable(self):
        import subprocess
        from types import SimpleNamespace
        with patch('embedded_rating.executable',return_value='/fake/exiftool'):
            with patch('photo_metadata.subprocess.run',side_effect=subprocess.TimeoutExpired('exiftool',4)):
                with self.assertRaisesRegex(MetadataReadError,'超时'):_run_metadata(['photo.ARW'])
            with patch('photo_metadata.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout=b'[]',stderr=b'cannot read')):
                with self.assertRaisesRegex(MetadataReadError,'cannot read'):_run_metadata(['photo.ARW'])
            with patch('photo_metadata.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'invalid',stderr=b'')):
                with self.assertRaisesRegex(MetadataReadError,'结果无效'):_run_metadata(['photo.ARW'])

    def test_reader_propagates_strict_worker_failure_instead_of_caching_empty_parameters(self):
        from concurrent.futures import Future
        class Executor:
            _processes={}
            def submit(self,*args):
                future=Future();future.set_exception(MetadataReadError('component unavailable'));return future
            def shutdown(self,**kwargs):pass
        reader=MetadataReader()
        with patch('concurrent.futures.ProcessPoolExecutor',return_value=Executor()):
            with self.assertRaisesRegex(MetadataReadError,'component unavailable'):reader.read_many(['photo.ARW'])
        self.assertIsNone(reader.executor)
        reader.close()

    def test_broken_worker_submission_is_reset_so_next_read_can_retry(self):
        from concurrent.futures import Future
        class Executor:
            _processes={}
            def __init__(self,broken):self.broken=broken;self.stopped=False
            def submit(self,*args):
                if self.broken:raise RuntimeError('broken worker')
                future=Future();future.set_result(['ISO 400']);return future
            def shutdown(self,**kwargs):self.stopped=True
        broken,good=Executor(True),Executor(False);reader=MetadataReader()
        with patch('concurrent.futures.ProcessPoolExecutor',side_effect=[broken,good]):
            with self.assertRaisesRegex(MetadataReadError,'broken worker'):reader.read_many(['photo.ARW'])
            self.assertTrue(broken.stopped);self.assertIsNone(reader.executor)
            self.assertEqual(reader.read_many(['photo.ARW']),['ISO 400'])
        reader.close()

    @unittest.skipUnless(RAW_FIXTURE.is_file() and EXIFTOOL_FIXTURE.is_file(),'Real Sony ARW and ExifTool fixture required')
    def test_real_sony_arw_chinese_batch_retains_valid_parameters_beside_missing_file(self):
        import shutil
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)/'照片 晚上场';folder.mkdir();path=folder/'当前照片.ARW'
            shutil.copyfile(RAW_FIXTURE,path)
            before=hashlib.sha256(path.read_bytes()).digest()
            with patch.dict(os.environ,{'PHOTOSIEVE_EXIFTOOL':str(EXIFTOOL_FIXTURE.resolve())}):
                values=shooting_infos([path,folder/'已移动.ARW'],strict=True)
                reader=MetadataReader()
                try:self.assertEqual(reader.read_many([path,folder/'已移动.ARW']),values)
                finally:reader.close()
            self.assertIn('SONY ILCE-7S',values[0]);self.assertIn('ISO 400',values[0]);self.assertIn('1/250s',values[0]);self.assertIsNone(values[1])
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),before)

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
            self.assertEqual(reader.read_many([path,path]),[None,None])
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
        self.assertFalse(worker.is_alive());self.assertEqual(results,[[None,None]])
        self.assertTrue(executor.process.killed);self.assertEqual(executor.process.joins,2);self.assertTrue(executor.stopped)

if __name__=='__main__':unittest.main()
