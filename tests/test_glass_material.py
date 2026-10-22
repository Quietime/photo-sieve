import os
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from glass_material import progressive_blur, material_bytes
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from apple_ui import GlassSurface
import theme

app=QApplication.instance() or QApplication([])


class ProgressiveGlassTest(unittest.TestCase):
    def test_progressively_softens_detail_without_changing_source(self):
        # Identical fine detail at every height exposes sudden blur bands or a flat blur.
        pixels=np.zeros((96,128,4),dtype=np.uint8)
        pixels[:,:,3]=255;pixels[:,::4,:3]=255
        source=Image.fromarray(pixels);original=source.tobytes()
        result=np.asarray(progressive_blur(source,maximum=7.))[:,:,0].astype(float)
        detail=np.std(result[:,16:-16],axis=1)
        self.assertGreater(detail[0],detail[32]*2)
        self.assertGreater(detail[32],detail[64]*2)
        self.assertLess(max(abs(np.diff(detail))),8.)
        self.assertEqual(source.tobytes(),original)

    def test_material_is_reused_instead_of_recomputed_per_frame(self):
        surface=GlassSurface();surface.resize(440,48);surface.show();app.processEvents()
        before=material_bytes.cache_info().misses
        first=surface.grab().toImage();surface.grab();second=surface.grab().toImage()
        self.assertEqual(first,second)
        self.assertEqual(material_bytes.cache_info().misses,before)
        self.assertIsNone(surface.graphicsEffect())
        for width in (500,540,600):
            surface.resize(width,48);surface.grab()
        self.assertEqual(material_bytes.cache_info().misses,before)
        surface.close();surface.deleteLater();app.processEvents()

    def test_accessibility_fallback_does_not_generate_blur(self):
        for preferences in [dict(REDUCED_TRANSPARENCY=True,HIGH_CONTRAST=False),dict(HIGH_CONTRAST=True,REDUCED_TRANSPARENCY=False)]:
            with patch.multiple(theme,**preferences),patch('apple_ui.material_bytes',side_effect=AssertionError('blur disabled')):
                surface=GlassSurface();surface.resize(440,48);surface.grab()
                surface.close();surface.deleteLater();app.processEvents()


if __name__=='__main__':unittest.main()
