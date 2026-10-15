"""Rapid zoom reversals, first-frame feedback and a single sharp-pixel transition."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint,QPointF,QEvent
from PySide6.QtGui import QPixmap,QColor,QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from widgets import PhotoView

application=QApplication.instance() or QApplication([])


class ZoomInteractionTest(unittest.TestCase):
    def setUp(self):
        preference=patch('theme.REDUCED_MOTION',False);preference.start();self.addCleanup(preference.stop)
        self.view=PhotoView();self.view.resize(700,560);self.view.show();application.processEvents()
        self.preview=QPixmap(1000,1500);self.preview.fill(QColor('navy'))
        self.view.show_photo({'id':1},self.preview);self.view.source_width=2000
        self.point=QPoint(300,230)
    def tearDown(self):
        self.view.stop_zoom();self.view.close();self.view.deleteLater();application.processEvents()
    def full(self):
        pixmap=QPixmap(2000,3000);pixmap.fill(QColor('navy'));return pixmap
    def toggle(self,*_):
        if self.view.detail:self.view.unzoom(lambda:self.view.show_photo({'id':1},self.preview))
        else:self.view.zoom_at(self.point)
    def finish(self):
        if self.view.zoom_animation:
            motion=self.view.zoom_animation;motion.setCurrentTime(motion.duration())

    def test_first_click_changes_scale_synchronously_without_waiting_for_frame_timer(self):
        initial=self.view.transform().m11();self.view.zoom_at(self.point)
        self.assertGreater(self.view.transform().m11(),initial)
        self.assertLessEqual(self.view.zoom_animation.duration(),220)
        self.assertLess(self.view.transform().m11(),self.view.zoom_target())

    def test_sharp_pixels_retarget_the_same_motion_without_a_second_settle(self):
        self.view.zoom_at(self.point);motion=self.view.zoom_animation
        self.view.install_full_image(self.full());self.assertIsNotNone(self.view.pending_full)
        motion.setCurrentTime(motion.duration()//3)
        self.assertTrue(self.view.full_resolution);self.assertIsNone(self.view.pending_full)
        self.assertIs(self.view.zoom_animation,motion)
        motion.setCurrentTime(motion.duration())
        self.assertIsNone(self.view.zoom_animation)
        self.assertAlmostEqual(self.view.transform().m11(),1.0)

    def test_reversal_keeps_full_pixel_identity_and_cancels_old_reset_callback(self):
        self.view.zoom_at(self.point);self.view.install_full_image(self.full());self.finish()
        reset=[];self.view.unzoom(lambda:reset.append(True));shrink=self.view.zoom_animation
        self.assertFalse(self.view.detail);self.assertTrue(self.view.full_resolution)
        self.assertGreaterEqual(shrink.duration(),220)
        shrink.setCurrentTime(shrink.duration()//4)
        self.view.zoom_at(self.point);grow=self.view.zoom_animation
        self.assertTrue(self.view.detail);self.assertTrue(self.view.full_resolution)
        self.assertFalse(shrink.running);grow.setCurrentTime(grow.duration())
        self.assertAlmostEqual(self.view.transform().m11(),1.0)
        self.assertFalse(reset,'An interrupted unzoom must never later replace the pixels')

    def test_double_click_press_and_third_click_each_deliver_a_toggle(self):
        clicks=[]
        def clicked(photo,point):clicks.append(point);self.toggle(photo,point)
        self.view.clicked.connect(clicked)
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point)
        second=QMouseEvent(QEvent.Type.MouseButtonDblClick,QPointF(self.point),
            QPointF(self.view.viewport().mapToGlobal(self.point)),Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        application.sendEvent(self.view.viewport(),second)
        QTest.mouseRelease(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point)
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point)
        self.assertEqual(len(clicks),3);self.assertTrue(self.view.detail)

    def test_same_full_pixmap_reuse_does_not_replace_scene_or_add_another_motion(self):
        full=self.full();self.view.zoom_at(self.point);self.view.install_full_image(full);self.finish()
        item=self.view.item;self.view.unzoom(lambda:None);self.view.zoom_at(self.point);motion=self.view.zoom_animation
        self.view.install_full_image(full)
        self.assertIs(self.view.item,item);self.assertIs(self.view.zoom_animation,motion);self.assertIsNone(self.view.pending_full)

    def test_sharp_swap_preserves_clicked_anchor_during_the_single_motion(self):
        rect=self.view.sceneRect();anchor=self.view.mapToScene(self.point)
        fraction=QPointF(anchor.x()/rect.width(),anchor.y()/rect.height())
        self.view.zoom_at(self.point);self.view.install_full_image(self.full())
        self.finish();after=self.view.mapToScene(self.point);rect=self.view.sceneRect()
        self.assertLess(abs(after.x()-fraction.x()*rect.width()),3)
        self.assertLess(abs(after.y()-fraction.y()*rect.height()),3)

    def test_drag_interrupts_zoom_and_owns_the_position_when_original_arrives(self):
        self.view.zoom_at(self.point);motion=self.view.zoom_animation
        motion.setCurrentTime(motion.duration()//2)
        QTest.mousePress(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point)
        end=self.point+QPoint(45,30)
        move=QMouseEvent(QEvent.Type.MouseMove,QPointF(end),QPointF(self.view.viewport().mapToGlobal(end)),
            Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        application.sendEvent(self.view.viewport(),move)
        QTest.mouseRelease(self.view.viewport(),Qt.MouseButton.LeftButton,pos=end)
        self.assertIsNone(self.view.zoom_animation);self.assertTrue(self.view.wheel_adjusted)
        anchor=self.view.mapToScene(self.point);rect=self.view.sceneRect()
        fraction=QPointF(anchor.x()/rect.width(),anchor.y()/rect.height());scale=self.view.transform().m11()
        self.view.install_full_image(self.full())
        self.assertIsNone(self.view.zoom_animation)
        self.assertAlmostEqual(self.view.transform().m11(),scale*.5)
        after=self.view.mapToScene(self.point);rect=self.view.sceneRect()
        self.assertLess(abs(after.x()-fraction.x()*rect.width()),3)
        self.assertLess(abs(after.y()-fraction.y()*rect.height()),3)


if __name__=='__main__':unittest.main()
