import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPointF, QSettings
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtWidgets import QApplication, QHBoxLayout
from apple_ui import AppleButton, GlassDock
import theme

application=QApplication.instance() or QApplication([])

class ApplePrimitivesTest(unittest.TestCase):
    def setUp(self):
        self.preferences=(theme.REDUCED_MOTION,theme.REDUCED_TRANSPARENCY,theme.HIGH_CONTRAST)
        theme.REDUCED_MOTION=theme.REDUCED_TRANSPARENCY=theme.HIGH_CONTRAST=False
        self.dock=GlassDock();self.dock.resize(400,48);layout=QHBoxLayout(self.dock)
        self.keep=AppleButton('保留',symbol='keep',variant='dock');self.delete=AppleButton('删除',symbol='delete',variant='dock')
        layout.addWidget(self.keep);layout.addWidget(self.delete);self.dock.register([self.keep,self.delete]);self.dock.show();application.processEvents()
    def tearDown(self):
        self.dock.close();self.dock.deleteLater();application.processEvents()
        theme.REDUCED_MOTION,theme.REDUCED_TRANSPARENCY,theme.HIGH_CONTRAST=self.preferences
    def image(self,color):
        pixmap=QPixmap(104,68);pixmap.fill(QColor(color));return pixmap
    def test_shortcuts_do_not_change_semantic_icon_or_action_name(self):
        self.keep.set_shortcut('↑','↑ / P');self.keep.set_shortcut('P','P / ↑')
        self.assertEqual((self.keep.symbol,self.keep.text(),self.keep.shortcut),('keep','保留','P'))
        self.assertIn('P / ↑',self.keep.toolTip());self.assertEqual(self.keep.accessibleName(),'保留')
        self.assertIn('P',self.keep.accessibleDescription())
    def test_material_tracks_content_and_caches_unchanged_photos(self):
        red=self.image('red');blue=self.image('blue')
        self.dock.set_sources([red]);first=self.dock.texture.cacheKey()
        self.dock.set_sources([red]);self.assertEqual(self.dock.texture.cacheKey(),first)
        red_view=self.dock.grab().toImage().pixelColor(15,24)
        self.dock.set_sources([blue]);blue_view=self.dock.grab().toImage().pixelColor(15,24)
        self.assertGreater(red_view.red(),blue_view.red());self.assertGreater(blue_view.blue(),red_view.blue())
    def test_opaque_fallback_removes_photo_tint(self):
        theme.REDUCED_TRANSPARENCY=True
        self.dock.set_sources([self.image('red')]);first=self.dock.grab().toImage().pixelColor(15,24)
        self.dock.set_sources([self.image('blue')]);second=self.dock.grab().toImage().pixelColor(15,24)
        self.assertEqual(first,second)
    def test_proximity_response_gradually_reaches_neighbors(self):
        self.dock.response_at(QPointF(self.keep.mapTo(self.dock,self.keep.rect().center())))
        self.keep.motion.setCurrentTime(self.keep.motion.duration())
        if self.delete.motion:self.delete.motion.setCurrentTime(self.delete.motion.duration())
        self.assertGreater(self.keep.hover,self.delete.hover);self.assertGreater(self.delete.hover,0.)
        self.assertFalse(theme.clock().timer.isActive())
    def test_press_compresses_then_spring_returns_without_idle_animation(self):
        self.keep.compress();self.assertEqual(self.keep.press_amount,1.)
        self.keep.release_spring();motion=self.keep.press_motion
        self.assertIsNotNone(motion);motion.setCurrentTime(motion.duration())
        self.assertEqual(self.keep.press_amount,0.);self.assertIsNone(self.keep.press_motion)
    def test_reduced_motion_has_no_magnification(self):
        theme.REDUCED_MOTION=True;self.keep.set_response(1.)
        self.assertEqual(self.keep.hover,0.);self.assertIsNone(self.keep.motion)
    def test_segmented_control_supports_keyboard_navigation(self):
        from widgets import Segmented
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        control=Segmented([('pending','未筛选'),('keep','保留'),('deleted','已删除')]);control.set_counts({})
        control.changed.connect(lambda value:control.set_current(value,False));control.show();application.processEvents()
        QTest.keyClick(control,Qt.Key.Key_Right);self.assertEqual(control.index,1)
        QTest.keyClick(control,Qt.Key.Key_End);self.assertEqual(control.index,2)
        QTest.keyClick(control,Qt.Key.Key_Home);self.assertEqual(control.index,0)
        control.close();control.deleteLater()
    def test_accessibility_respects_os_preferences_and_user_overrides(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings=QSettings(str(Path(temporary)/'prefs.ini'),QSettings.Format.IniFormat)
            with patch('theme.accessibility_defaults',return_value=(False,False,False)):
                settings.setValue('reduced_motion',True);theme.configure_accessibility(settings)
                self.assertTrue(theme.REDUCED_MOTION)
            with patch('theme.accessibility_defaults',return_value=(True,False,True)):
                settings.setValue('reduced_motion',False);theme.configure_accessibility(settings)
                self.assertTrue(theme.REDUCED_MOTION);self.assertTrue(theme.REDUCED_TRANSPARENCY);self.assertTrue(theme.HIGH_CONTRAST)

if __name__=='__main__':unittest.main()
