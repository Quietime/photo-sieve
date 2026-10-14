"""Adaptive display previews must contain enough native pixels without unsafe RAW fallback."""
import importlib.util
import io
import multiprocessing
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image,ImageDraw
from app import Library,LibraryError,PREVIEW_EDGES,build_preview


class PreviewResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=tempfile.TemporaryDirectory();cls.fixture_path=Path(cls.fixture.name)/'detail.jpg'
        image=Image.new('RGB',(4000,3000),'white');drawing=ImageDraw.Draw(image)
        for y in range(0,3000,32):
            for x in range(0,4000,32):
                if (x//32+y//32)%2==0:drawing.rectangle((x,y,x+31,y+31),fill='black')
        image.save(cls.fixture_path,quality=97)

    @classmethod
    def tearDownClass(cls):cls.fixture.cleanup()

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.base=Path(self.temporary.name).resolve()
        self.source=self.base/'detail.jpg';shutil.copyfile(self.fixture_path,self.source)
        self.library=Library(self.base/'workspace');self.library.remember('root',str(self.base))
        stat=self.source.stat()
        with self.library.connect() as db:
            db.execute('INSERT INTO photos(id,path,size,mtime) VALUES (1,?,?,?)',(str(self.source),stat.st_size,stat.st_mtime_ns))
        self.row=self.library.photo(1)

    def tearDown(self):self.temporary.cleanup()

    def cache(self,edge):return self.library.cache_path(self.source,self.row['size'],self.row['mtime'],edge)

    def test_high_display_previews_have_real_2400_and_3200_pixels_in_workers(self):
        self.assertEqual(PREVIEW_EDGES,(1600,2400,3200,4800))
        with ProcessPoolExecutor(1,mp_context=multiprocessing.get_context('spawn')) as pool:
            self.library.offload=lambda *job:pool.submit(build_preview,*job).result() or True
            # The parent process is not permitted to decode an original at any requested edge.
            with patch.object(self.library,'decode',side_effect=AssertionError('Parent original decode')):
                for edge in (2400,3200):
                    image=self.library.preview_image(1,edge)
                    self.assertEqual(image.size,(edge,edge*3//4));image.load()
                    self.assertLess(image.getpixel((edge//400,edge//400))[0],30)
            self.library.offload=None
        for edge in (320,1600,2400,3200):self.assertTrue(self.cache(edge).is_file())
        with self.library.connect() as db:
            self.assertEqual({row[0] for row in db.execute('SELECT edge FROM cache_index')},{320,1600,2400,3200})
        self.assertEqual(self.library.cache_bytes,sum(p.stat().st_size for p in self.library.cache.glob('*.jpg')))

    def test_existing_1600_cache_never_satisfies_a_3200_request(self):
        self.library.preview(1,1600)
        self.library.offload=MagicMock(return_value=False)
        with patch.object(self.library,'decode',side_effect=AssertionError('Unsafe parent fallback')):
            with self.assertRaises(LibraryError):self.library.preview_image(1,3200)
        self.library.offload.assert_called_once()
        self.assertEqual(self.library.offload.call_args.args[-1],3200)
        self.assertFalse(self.cache(3200).exists());self.assertTrue(self.cache(1600).exists())

    def test_cached_higher_preview_reuses_pixels_without_another_original_decode(self):
        self.library.preview(1,3200);self.library.offload=MagicMock(side_effect=AssertionError('Redundant original decode'))
        with patch.object(self.library,'decode',side_effect=AssertionError('Parent original decode')):
            image=self.library.preview_image(1,2400);image.load()
        self.assertEqual(image.size,(3200,2400));self.library.offload.assert_not_called()
        self.assertFalse(self.cache(2400).exists(),'Reuse does not generate another lossy JPEG or waste disk space')

    def test_face_cache_is_never_reused_as_a_higher_resolution_photo(self):
        face=self.library.cache/'face-crop.png';Image.new('RGB',(96,96),'blue').save(face)
        with self.library.connect() as db:
            db.execute('INSERT INTO cache_index VALUES (?,?,?,?,?)',
                (face.name,1,self.row['size'],self.row['mtime'],9001))
        self.library.offload=MagicMock(side_effect=lambda *job:build_preview(*job) or True)
        image=self.library.preview_image(1,2400)
        self.library.offload.assert_called_once()
        self.assertEqual(image.size,(2400,1800),'A cached face must not replace the original photo')
        self.assertTrue(face.exists(),'Display preview loading preserves independent face metadata')

    def test_high_worker_decodes_once_and_preserves_existing_fast_cache_age(self):
        self.library.preview(1,1600);fast=self.cache(1600);small=self.cache(320)
        before={path.name:(path.stat().st_mtime_ns,path.read_bytes()) for path in (fast,small)}
        decode=Library.decode
        with patch.object(Library,'decode',wraps=decode) as spy:
            build_preview(str(self.source),str(self.source),self.row['size'],self.row['mtime'],str(self.library.cache),False,3200)
        self.assertEqual(spy.call_count,1)
        self.assertEqual(before,{path.name:(path.stat().st_mtime_ns,path.read_bytes()) for path in (fast,small)})
        with Image.open(self.cache(3200)) as image:self.assertEqual(image.size,(3200,2400))

    def test_failed_worker_and_unsupported_full_worker_never_fall_back(self):
        self.library.offload=MagicMock(return_value=False)
        with patch.object(self.library,'decode',side_effect=AssertionError('Parent original decode')) as decode:
            with self.assertRaises(LibraryError):self.library.preview_image(1,4800)
            decode.assert_not_called()
        self.library.offload=lambda *job:False
        with patch.object(self.library,'decode',side_effect=AssertionError('Parent full RAW decode')) as decode:
            with self.assertRaisesRegex(LibraryError,'安全解码进程不支持完整原图'):self.library.preview_image(1,0)
            decode.assert_not_called()

    def test_jpeg_draft_covers_requested_long_edge_for_portrait_and_landscape(self):
        for dimensions in ((4000,3000),(3000,4000),(7008,4672),(1616,1080)):
            data=io.BytesIO();Image.new('RGB',dimensions,'red').save(data,'JPEG')
            for edge in PREVIEW_EDGES:
                image=Library.camera_jpeg(data.getvalue(),0,edge)
                self.assertGreaterEqual(max(image.size),min(edge,max(dimensions)))

    def test_mpo_small_camera_preview_cannot_satisfy_large_display(self):
        path=self.base/'camera.jpg'
        full=Image.new('RGB',(4000,3000),'red');small=Image.new('RGB',(1616,1212),'blue')
        full.save(path,format='MPO',save_all=True,append_images=[small],quality=92)
        with Image.open(path) as image:entry=image.mpinfo[0xB002][1]
        old=struct.pack('<LLL',0,entry['Size'],entry['DataOffset']);data=path.read_bytes()
        self.assertEqual(data.count(old),1)
        path.write_bytes(data.replace(old,struct.pack('<LLL',0x010002,entry['Size'],entry['DataOffset'])))
        ordinary=Library.decode(path,1600);large=Library.decode(path,3200)
        self.assertGreater(ordinary.getpixel((100,100))[2],200,'Normal preview uses the fast embedded blue frame')
        self.assertEqual(large.size,(3200,2400))
        self.assertGreater(large.getpixel((100,100))[0],200,'A large display uses the full red source frame')

    @unittest.skipUnless(importlib.util.find_spec('rawpy'),'optional RAW dependency absent')
    def test_small_raw_thumbnail_uses_sensor_at_sufficient_native_size(self):
        import numpy as np
        import rawpy
        raw=MagicMock();raw.__enter__.return_value=raw;raw.sizes=SimpleNamespace(width=6000,height=4000,flip=0)
        out=io.BytesIO();Image.new('RGB',(1616,1080),'blue').save(out,'JPEG')
        raw.extract_thumb.return_value=SimpleNamespace(format=rawpy.ThumbFormat.JPEG,data=out.getvalue())
        def sensor(**options):
            return np.zeros((2000,3000,3) if options['half_size'] else (4000,6000,3),dtype=np.uint8)
        raw.postprocess.side_effect=sensor
        for edge,half in ((2400,True),(3200,False)):
            with patch('rawpy.imread',return_value=raw),patch.object(Library,'raw_preview',return_value=None):
                image=Library.decode(self.base/'camera.arw',edge)
            self.assertEqual(max(image.size),edge)
            self.assertEqual(raw.postprocess.call_args.kwargs['half_size'],half)


if __name__=='__main__':unittest.main()
