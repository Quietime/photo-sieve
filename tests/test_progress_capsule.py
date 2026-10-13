import unittest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor
from widgets import CapsuleProgress
import theme

class CapsuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_busy_never_splits_or_exposes_square_blue_corners(self):
        bar=CapsuleProgress();bar.resize(540,18);bar.setRange(0,0);bar.show();bar.timer.stop()
        blue=QColor(theme.BLUE)
        for phase in (0,.125,.25,.5,.75,.875):
            bar.phase=phase;image=bar.grab().toImage();runs=0;inside=False
            for x in range(image.width()):
                filled=image.pixelColor(x,9)==blue
                if filled and not inside:runs+=1
                inside=filled
            self.assertEqual(runs,1,phase)
            for x in (0,image.width()-1):self.assertNotEqual(image.pixelColor(x,0),blue)
        bar.hide();self.assertFalse(bar.timer.isActive());bar.close()
    def test_real_values_and_full_progress_keep_rounded_ends(self):
        bar=CapsuleProgress();bar.resize(540,18);bar.setRange(0,16000);bar.setValue(12000);bar.show();self.app.processEvents()
        self.assertEqual(bar.value(),12000);self.assertEqual(bar.maximum(),16000);self.assertFalse(bar.timer.isActive())
        bar.setValue(16000);image=bar.grab().toImage();blue=QColor(theme.BLUE)
        self.assertEqual(image.pixelColor(270,9),blue)
        self.assertNotEqual(image.pixelColor(0,0),blue);self.assertNotEqual(image.pixelColor(539,0),blue)
        bar.close()
