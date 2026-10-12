import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication
from widgets import PhotoView

application = QApplication.instance() or QApplication([])

class WheelZoomTest(unittest.TestCase):
    def setUp(self):
        self.view = PhotoView(); self.view.resize(600, 480); self.view.show(); application.processEvents()
        pixmap = QPixmap(1000, 750); pixmap.fill(QColor('blue'))
        self.view.show_photo({'id': 1}, pixmap); self.view.source_width = 2000
    def tearDown(self):self.view.close();self.view.deleteLater();application.processEvents()
    def wheel(self, delta=120, pixel=0, point=QPointF(240, 190)):
        event = QWheelEvent(point, QPointF(self.view.viewport().mapToGlobal(point.toPoint())), QPoint(0, pixel), QPoint(0, delta),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        self.view.wheelEvent(event)
    def zoomed(self):
        self.view.zoom_at();self.view.zoom_animation.setCurrentTime(self.view.zoom_animation.duration())
    def test_fitted_photo_does_not_pan_or_zoom(self):
        before = self.view.transform().m11(); point = self.view.mapToScene(QPoint(240,190))
        self.wheel()
        self.assertEqual(self.view.transform().m11(), before)
        self.assertEqual(self.view.mapToScene(QPoint(240,190)), point)
    def test_wheel_zooms_around_cursor_and_returns_with_reverse_delta(self):
        self.zoomed();before = self.view.transform().m11();anchor = self.view.mapToScene(QPoint(240,190))
        self.wheel();self.assertAlmostEqual(self.view.transform().m11(), before*1.2)
        after = self.view.mapToScene(QPoint(240,190));self.assertLess((after-anchor).manhattanLength(), 3)
        self.wheel(-120);self.assertAlmostEqual(self.view.transform().m11(), before)
        self.assertTrue(self.view.detail)
    def test_limits_and_fractional_trackpad_delta(self):
        self.zoomed();self.wheel(30)
        self.assertAlmostEqual(self.view.transform().m11(), 2 * 1.2**.25)
        self.wheel(0, 25);self.assertAlmostEqual(self.view.transform().m11(), 2 * 1.2**.5)
        for _ in range(8):self.wheel(2400)
        self.assertAlmostEqual(self.view.transform().m11(), self.view.zoom_target()*8)
        for _ in range(8):self.wheel(-2400)
        self.assertAlmostEqual(self.view.transform().m11(), self.view.fit_scale())
    def test_original_swap_keeps_wheel_magnification_and_pending_decode(self):
        self.zoomed();self.wheel();old_scale = self.view.transform().m11()
        original=QPixmap(2000,1500);original.fill(QColor('blue'))
        self.view.install_full_image(original)
        self.assertTrue(self.view.full_resolution)
        self.assertAlmostEqual(self.view.transform().m11(), old_scale*.5)
        self.assertIsNone(self.view.zoom_animation)
    def test_wheel_cancels_click_animation_and_installs_waiting_original(self):
        self.view.zoom_at();original=QPixmap(2000,1500);original.fill(QColor('blue'))
        self.view.install_full_image(original);self.assertIsNotNone(self.view.pending_full)
        self.wheel()
        self.assertTrue(self.view.full_resolution);self.assertIsNone(self.view.pending_full)
        self.assertIsNone(self.view.zoom_animation)

if __name__ == '__main__':unittest.main()
