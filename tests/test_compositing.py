import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings

import desktop


class CompositingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / 'prefs.ini'), QSettings.Format.IniFormat)

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, **environment):
        clean = {k: v for k, v in os.environ.items() if not k.startswith(('QT_WIDGETS_RHI', 'PHOTOSIEVE_RHI'))}
        clean.update(QT_QPA_PLATFORM='windows', **environment)
        with patch.dict(os.environ, clean, clear=True):
            desktop.configure_compositing(self.settings, windows=True)
            return {k: os.environ.get(k) for k in ('QT_WIDGETS_RHI', 'QT_WIDGETS_RHI_BACKEND')}, desktop.COMPOSITING['gpu']

    def test_default_is_stable_cpu_and_explicit_gpu_uses_direct3d(self):
        environment,gpu=self.configure()
        self.assertFalse(gpu);self.assertIsNone(environment['QT_WIDGETS_RHI'])
        self.settings.setValue('gpu_compositing',True)
        environment, gpu = self.configure()
        self.assertEqual(environment, {'QT_WIDGETS_RHI': '1', 'QT_WIDGETS_RHI_BACKEND': 'd3d11'}); self.assertTrue(gpu)
        self.assertTrue(self.settings.value('gpu_pending', type=bool))

    def test_a_launch_that_never_became_healthy_turns_it_off(self):
        self.settings.setValue('gpu_compositing',True)
        self.configure()                                    # crashed before the window was healthy: pending stays set
        environment, gpu = self.configure()
        self.assertEqual(environment['QT_WIDGETS_RHI'], None); self.assertFalse(gpu)
        self.assertTrue(self.settings.value('gpu_fell_back', type=bool))
        self.settings.setValue('gpu_compositing', True)     # the user turns it back on from the ⋯ menu
        environment, gpu = self.configure()
        self.assertEqual(environment['QT_WIDGETS_RHI'], '1'); self.assertTrue(gpu)

    def test_user_choice_and_manual_override(self):
        self.settings.setValue('gpu_compositing', False)
        self.assertEqual(self.configure(), ({'QT_WIDGETS_RHI': None, 'QT_WIDGETS_RHI_BACKEND': None}, False))
        self.assertEqual(self.configure(QT_WIDGETS_RHI='1')[1], True)              # set by hand for an A/B test
        # An inherited automatic setting (from a restart by the updater) follows the stored preference instead.
        self.assertEqual(self.configure(QT_WIDGETS_RHI='1', PHOTOSIEVE_RHI_AUTO='1')[1], False)

    def test_offscreen_and_other_systems_stay_on_the_cpu(self):
        with patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}):
            desktop.configure_compositing(self.settings, windows=True)
            self.assertNotIn('QT_WIDGETS_RHI', os.environ)
        desktop.configure_compositing(self.settings, windows=False)
        self.assertFalse(desktop.COMPOSITING['gpu'])


if __name__ == '__main__':
    unittest.main()
