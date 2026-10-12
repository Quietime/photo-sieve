import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from app import Library
from desktop import Window

application=QApplication.instance() or QApplication([])

class PreloadTest(unittest.TestCase):
    def test_custom_preload_fetches_more_than_one_metadata_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary).resolve(); photos=base/'photos'; photos.mkdir()
            library=Library(base/'records'); library.remember('root',photos)
            with library.connect() as db:
                db.executemany('INSERT INTO photos(path,size,mtime) VALUES (?,1,1)',[(str(photos/f'{i:05}.jpg'),) for i in range(2400)])
            # Verify paging independently of image decoding; metadata rows have no original image files.
            with patch.object(Window,'request_image',return_value=None):
                window=Window(library,check_updates=False); window.preload_count=2500; window.warm_count=0
                window.show(); window.display_size=None; window.update_display_size()
                deadline=time.monotonic()+10
                while (len(window.queue)!=2400 or window.fetching or not window.queue_end) and time.monotonic()<deadline:
                    application.processEvents(); time.sleep(.005)
                try:
                    self.assertEqual(window.ahead_target,2500)
                    self.assertEqual(len(window.queue),2400)
                    self.assertTrue(window.queue_end); self.assertFalse(window.fetching)
                    self.assertEqual(len({p['id'] for p in window.queue}),2400)
                finally: window.close(); application.processEvents()
