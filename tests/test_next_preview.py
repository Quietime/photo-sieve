import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import QSettings,QSize
from PySide6.QtWidgets import QApplication
from desktop import Window
from app import Library
from tests.test_face_service import until,application

class NextPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();base=Path(self.temp.name);photos=base/'photos';photos.mkdir()
        for i in range(4):Image.new('RGB',(600,900),['navy','red','green','gray'][i]).save(photos/f'{i}.jpg')
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat);settings.setValue('face_preview',False);settings.setValue('warm_count',0)
        self.patches=[patch(module+'.QSettings',return_value=settings) for module in ('desktop','face_service','update_ui')]
        for item in self.patches:item.start()
        self.window=Window(Library(base/'work'),check_updates=False,start_empty=True,data_root=base);self.window.show();self.window.scan(photos)
        until(lambda:len(self.window.queue)==4 and all(self.window.preview_ready(p['id']) for p in self.window.queue) and not self.window.loading)
    def tearDown(self):
        self.window.prompt_on_close=False;self.window.close();application.processEvents()
        for item in self.patches:item.stop()
        self.temp.cleanup()
    def test_cached_next_is_visible_immediately_without_scaling_ghost_or_opacity_fade(self):
        window=self.window;target=window.queue[1]['id'];pixels=window.cache[target].cacheKey()
        window.next_button.click()
        self.assertEqual(window.queue[0]['id'],target);self.assertEqual(window.photos[0].pixmap.cacheKey(),pixels)
        self.assertEqual(window.photos[0].item.opacity(),1);self.assertIsNone(window.pending_next);self.assertFalse(window.motions)
    def test_cold_next_keeps_current_sharp_and_coalesces_clicks_until_target_ready(self):
        window=self.window;current=window.queue[0]['id'];target=window.queue[1]['id'];pixels=window.photos[0].pixmap.cacheKey()
        window.cache_size[target]=QSize(1,1);submit=window.submit;jobs=[]
        def defer(work,ok=lambda _:None,fail=None,pool=None,priority=0):
            if pool is window.image_pool:jobs.append((work,ok,fail));return
            return submit(work,ok,fail,pool,priority)
        with patch.object(window,'submit',side_effect=defer):
            for _ in range(6):window.next_button.click()
            self.assertEqual(window.queue[0]['id'],current);self.assertEqual(window.photos[0].pixmap.cacheKey(),pixels)
            self.assertEqual(window.photos[0].item.opacity(),1);self.assertEqual(len(jobs),1)
            work,done,_=jobs[0];done(work())
            self.assertEqual(window.queue[0]['id'],target);self.assertEqual(window.photos[0].item.opacity(),1)
            self.assertTrue(window.preview_ready(target));self.assertIsNone(window.pending_next)
    def test_preview_resolution_follows_physical_pane(self):
        self.assertEqual(Window.preview_edge(QSize(1000,900)),1600)
        self.assertEqual(Window.preview_edge(QSize(2200,1800)),2400)
        self.assertEqual(Window.preview_edge(QSize(2800,1600)),3200)
