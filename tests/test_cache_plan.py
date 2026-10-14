import unittest
from cache_plan import photo_limits,face_limits

class PlanTests(unittest.TestCase):
    def test_disk_zero_still_preloads_memory_but_faces_never_run_farther(self):
        self.assertEqual(photo_limits(16,0,64*1024**2),(16,0))
        self.assertEqual(face_limits(100,16,0),(15,15,20))
    def test_face_window_tracks_disk_and_memory_settings(self):
        self.assertEqual(face_limits(100,16,80),(15,79,20))
        self.assertEqual(face_limits(100,120,0),(100,100,105))
        self.assertEqual(face_limits(0,120,1200),(0,0,5))
    def test_tiny_cache_and_large_photos_cap_disk_prefetch(self):
        self.assertEqual(photo_limits(16,50000,64*1024**2),(16,51))
        self.assertEqual(photo_limits(16,50000,64*1024**2,2*1024**2),(16,12))
    def test_high_settings_keep_ram_and_disk_bounded(self):
        self.assertEqual(face_limits(500,5000,50000),(500,500,128))
        self.assertLess(photo_limits(200,50000,10*1024**3)[1],10000)
