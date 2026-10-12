import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image
from app import Library,LibraryError
from desktop import Decoders
from machine import Profile,GB

def crash_in_worker(*_):
    os._exit(86)

class CrashIsolationTest(unittest.TestCase):
    def test_first_decoder_crash_never_retries_the_photo_in_the_app(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);photos=base/'photos';photos.mkdir()
            Image.new('RGB',(160,100),'blue').save(photos/'image.jpg')
            library=Library(base/'records');library.scan(photos)
            deadline=time.monotonic()+30
            while library.job['running'] and time.monotonic()<deadline:time.sleep(.01)
            photo=library.list_photos()[0];decoders=Decoders(1);library.offload=decoders
            try:
                with patch('desktop.build_preview',crash_in_worker),patch.object(library,'decode',side_effect=AssertionError('unsafe parent fallback')):
                    with self.assertRaises(LibraryError) as error:library.preview(photo['id'],320)
                    self.assertIn('已隔离',error.exception.detail)
                self.assertFalse(decoders.working)
                self.assertEqual(library.state()['counts']['pending'],1)
                # A new decoder generation can still show the following healthy photo.
                self.assertTrue(library.preview(photo['id'],1600))
            finally:decoders.close()

    def test_full_resolution_also_survives_a_native_worker_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);photos=base/'photos';photos.mkdir()
            Image.new('RGB',(200,100),'green').save(photos/'image.jpg')
            library=Library(base/'records');library.scan(photos)
            while library.job['running']:time.sleep(.01)
            decoder=Decoders(1);library.offload=decoder
            try:
                with patch('desktop.build_full_image',crash_in_worker),patch.object(library,'decode',side_effect=AssertionError('unsafe full-resolution parent decode')):
                    with self.assertRaises(LibraryError):library.preview_image(library.list_photos()[0]['id'],0)
                self.assertEqual(library.state()['counts']['all'],1)
            finally:decoder.close()

    def test_a_missing_decoder_cannot_enable_thread_fallback(self):
        decoder=Decoders(1)
        with patch('desktop.ProcessPoolExecutor',side_effect=OSError('spawn unavailable')):
            with self.assertRaisesRegex(ValueError,'安全解码'):decoder('image.arw')
        with self.assertRaisesRegex(ValueError,'安全解码'):decoder('next.arw')

    def test_custom_raw_preload_has_ram_and_gpu_headroom(self):
        profile=Profile(24,16,8,8,64*GB)
        pixel_bytes=10*1024**2
        cpu=profile.preview_capacity(1200,pixel_bytes)
        gpu=profile.preview_capacity(1200,pixel_bytes,True)
        self.assertLess(cpu,1200);self.assertLess(gpu,cpu)
        self.assertLessEqual(cpu*pixel_bytes,2*GB)
        self.assertLessEqual(gpu*pixel_bytes,512*1024**2)
        self.assertEqual(profile.preview_capacity(1200,32*1024),1200)

    def test_native_abort_and_early_exit_leave_persistent_logs(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            logs=Path(temp)/'logs'
            code="import os,crashlog; exec(\"if os.name!='nt':\\n import resource; resource.setrlimit(resource.RLIMIT_CORE,(0,0))\"); crashlog.install(%r); crashlog.event('opening_raw',file='fault-test.arw'); os.abort()" % str(logs)
            result=subprocess.run([sys.executable,'-c',code],cwd=root,capture_output=True,timeout=60)
            self.assertNotEqual(result.returncode,0)
            deadline=time.monotonic()+10
            while not list(logs.glob('session-*/unexpected-exit.json')) and time.monotonic()<deadline:time.sleep(.05)
            reports=list(logs.glob('session-*/unexpected-exit.json'));self.assertEqual(len(reports),1)
            state=json.loads(reports[0].read_text('utf-8'))
            self.assertEqual(state['stage'],'opening_raw');self.assertEqual(state['file'],'fault-test.arw')
            fault=reports[0].with_name('native-fault.log').read_text('utf-8')
            self.assertIn('Fatal',fault)
            self.assertTrue(reports[0].with_name('application.log').is_file())
