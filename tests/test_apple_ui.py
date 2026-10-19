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

def settle(*buttons):
    for button in buttons:
        for spring in (button.hover_spring,button.press_spring,button.pull_x,button.pull_y):
            for _ in range(2000):
                if not spring.running:break
                spring.advance(spring.last+1/240)


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
    def test_navy_glass_is_never_tinted_by_the_current_photo(self):
        self.dock.set_sources([self.image('red')]);first=self.dock.grab().toImage()
        self.dock.set_sources([self.image('blue')]);second=self.dock.grab().toImage()
        for point in ((15,24),(200,3),(200,44)):
            self.assertEqual(first.pixelColor(*point),second.pixelColor(*point))
        self.assertGreater(first.pixelColor(15,24).blue(),first.pixelColor(15,24).red())
        self.assertGreater(first.pixelColor(200,3).lightness(),first.pixelColor(200,44).lightness())
    def test_opaque_fallback_removes_photo_tint(self):
        theme.REDUCED_TRANSPARENCY=True
        self.dock.set_sources([self.image('red')]);first=self.dock.grab().toImage().pixelColor(15,24)
        self.dock.set_sources([self.image('blue')]);second=self.dock.grab().toImage().pixelColor(15,24)
        self.assertEqual(first,second)
    def test_proximity_response_gradually_reaches_neighbors(self):
        self.dock.response_at(QPointF(self.keep.mapTo(self.dock,self.keep.rect().center())))
        settle(self.keep,self.delete)
        self.assertGreater(self.keep.hover,self.delete.hover);self.assertGreater(self.delete.hover,0.)
        self.assertFalse(theme.clock().timer.isActive())
    def test_press_compresses_then_spring_returns_without_idle_animation(self):
        self.keep.compress();settle(self.keep);self.assertAlmostEqual(self.keep.press_amount,1.,places=2)
        self.keep.release_spring();peak=0.
        for _ in range(400):
            self.keep.press_spring.advance(self.keep.press_spring.last+1/240);peak=min(peak,self.keep.press_amount)
            if not self.keep.press_spring.running:break
        self.assertLess(peak,-.01)                     # under-damped: it bounces past rest once
        self.assertEqual(self.keep.press_amount,0.);self.assertFalse(self.keep.press_spring.running)
        self.assertFalse(theme.clock().timer.isActive())
    def test_reduced_motion_has_no_magnification(self):
        theme.REDUCED_MOTION=True;self.keep.set_response(1.);self.keep.compress()
        self.assertEqual(self.keep.hover,0.);self.assertEqual(self.keep.press_amount,0.);self.assertFalse(self.keep.hover_spring.running)
    def test_segmented_control_only_switches_on_mouse_click(self):
        from widgets import Segmented
        from PySide6.QtCore import Qt,QPoint
        from PySide6.QtTest import QTest
        control=Segmented([('pending','未筛选'),('keep','保留'),('deleted','已删除')]);control.set_counts({})
        control.changed.connect(lambda value:control.set_current(value,False));control.show();application.processEvents()
        self.assertEqual(control.focusPolicy(),Qt.FocusPolicy.NoFocus)
        for key in (Qt.Key.Key_Right,Qt.Key.Key_Left,Qt.Key.Key_End,Qt.Key.Key_Home,Qt.Key.Key_Down,Qt.Key.Key_Space):
            QTest.keyClick(control,key);self.assertEqual(control.index,0)
        QTest.mouseClick(control,Qt.MouseButton.LeftButton,pos=QPoint(2+round(control.segment_width()*1.5),16))
        self.assertEqual(control.index,1)
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
