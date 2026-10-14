"""Read-only album background navigation and clicked-pixel zoom across photo views."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import Qt,QPoint,QPointF,QEvent
from PySide6.QtGui import QColor,QPixmap,QMouseEvent,QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QStackedWidget,QPushButton
from album_view import AlbumView
from widgets import PhotoView

application=QApplication.instance() or QApplication([])


def finish_zoom(pane):
    for _ in range(5):
        if pane.zoom_animation is None:return
        pane.zoom_animation.setCurrentTime(pane.zoom_animation.duration())
    raise AssertionError('Zoom did not settle')


def drag(view,start,end,return_to_start=False):
    QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start)
    for point in ([end,start] if return_to_start else [end]):
        event=QMouseEvent(QEvent.Type.MouseMove,QPointF(point),QPointF(view.viewport().mapToGlobal(point)),
            Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        application.sendEvent(view.viewport(),event)
    QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=start if return_to_start else end)


class AlbumHost(QWidget):
    """Exercise the real album widget without starting scanning, networking or metadata workers."""
    def __init__(self,view):
        super().__init__();self.view=view;self.errors=[];self.overlay_updates=0;self.mode_updates=0
        photo={'id':1,'name':'photo.jpg','path':'photo.jpg','present':view=='keep','backup':'temporary-photo.jpg'}
        self.library=SimpleNamespace(public=lambda value:value,photo=lambda *args,**kwargs:photo,
            preview_image=lambda *_:Image.new('RGB',(1500,2250),'navy'))
        self.decoders=SimpleNamespace(full_image=lambda _:Image.new('RGB',(1500,2250),'navy'));self.detail_pool=None
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);self.stack=QStackedWidget();layout.addWidget(self.stack)
        for _ in range(5):self.stack.addWidget(QWidget())
        self.album=AlbumView(self);self.stack.addWidget(self.album);self.resize(900,700)
        self.stack.setCurrentWidget(self.album);self.show();application.processEvents();self.album.open(1)
        application.processEvents()
    def button(self,text,callback):
        button=QPushButton(text);button.clicked.connect(callback);return button
    def sync_shortcut_mode(self):self.mode_updates+=1
    def place_overlays(self):self.overlay_updates+=1
    def submit(self,work,done,failed,pool=None):
        try:done(work())
        except Exception as error:self.errors.append(str(error));failed(str(error))


class AlbumNavigationTest(unittest.TestCase):
    def setUp(self):
        preference=patch('theme.REDUCED_MOTION',False);preference.start();self.addCleanup(preference.stop)
        self.hosts=[];self.views=[]
    def tearDown(self):
        for host in self.hosts:
            host.album.pane.stop_zoom();host.close();host.deleteLater()
        for view in self.views:view.stop_zoom();view.close();view.deleteLater()
        application.processEvents()
    def host(self,view):
        host=AlbumHost(view);self.hosts.append(host);self.assertFalse(host.errors);return host
    def image_point(self,pane):
        rect=pane.content_rect();return QPoint(round(rect.left()+rect.width()*.4),round(rect.top()+rect.height()*.43))
    def plain_view(self):
        view=PhotoView();view.resize(800,600);view.show();application.processEvents()
        pixmap=QPixmap(1200,1800);pixmap.fill(QColor('navy'));view.show_photo({'id':1},pixmap);view.source_width=2400
        self.views.append(view);return view

    def test_left_and_right_letterbox_return_to_the_originating_gallery(self):
        for gallery,index in (('keep',4),('deleted',1)):
            for side in ('left','right'):
                host=self.host(gallery);pane=host.album.pane;rect=pane.content_rect()
                self.assertGreater(rect.left(),20)
                x=rect.left()//2 if side=='left' else (rect.right()+pane.viewport().width())//2
                QTest.mouseClick(pane.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(x,pane.viewport().height()//2))
                self.assertEqual(host.stack.currentIndex(),index);self.assertFalse(pane.detail)

    def test_outer_album_margin_returns_but_top_bar_does_not(self):
        host=self.host('keep');album=host.album
        QTest.mouseClick(album,Qt.MouseButton.LeftButton,pos=QPoint(album.width()-5,album.top_bar.geometry().center().y()))
        self.assertIs(host.stack.currentWidget(),album)
        QTest.mouseClick(album,Qt.MouseButton.LeftButton,pos=QPoint(5,album.pane.geometry().center().y()))
        self.assertEqual(host.stack.currentIndex(),4)

    def test_image_click_remains_in_album_and_keeps_the_same_pixel_under_pointer(self):
        for gallery in ('keep','deleted'):
            host=self.host(gallery);pane=host.album.pane;point=self.image_point(pane)
            before=pane.mapToScene(point);rect=pane.sceneRect()
            fraction=QPointF(before.x()/rect.width(),before.y()/rect.height())
            QTest.mouseClick(pane.viewport(),Qt.MouseButton.LeftButton,pos=point);finish_zoom(pane)
            self.assertIs(host.stack.currentWidget(),host.album);self.assertTrue(pane.detail);self.assertTrue(pane.full_resolution)
            self.assertFalse(host.errors,'Album installs the QImage returned by full_qimage')
            after=pane.mapToScene(point);rect=pane.sceneRect()
            self.assertLess(abs(after.x()-fraction.x()*rect.width()),3)
            self.assertLess(abs(after.y()-fraction.y()*rect.height()),3)

    def test_blank_background_emits_no_image_zoom_and_image_click_carries_pointer(self):
        view=self.plain_view();images=[];blanks=[]
        view.clicked.connect(lambda photo,point:images.append((photo,point)));view.background_clicked.connect(lambda:blanks.append(True))
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(10,300))
        self.assertEqual(len(blanks),1);self.assertFalse(images)
        point=self.image_point(view);QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point)
        self.assertEqual(len(images),1);self.assertEqual(images[0][1],QPointF(point));self.assertEqual(len(blanks),1)

    def test_pending_view_uses_clicked_anchor_before_and_after_full_resolution_swap(self):
        view=self.plain_view();view.clicked.connect(lambda _,point:view.zoom_at(point));point=self.image_point(view)
        anchor=view.mapToScene(point);rect=view.sceneRect();fraction=QPointF(anchor.x()/rect.width(),anchor.y()/rect.height())
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point);finish_zoom(view)
        self.assertLess((view.mapToScene(point)-anchor).manhattanLength(),3)
        full=QPixmap(2400,3600);full.fill(QColor('navy'));view.install_full_image(full);finish_zoom(view)
        after=view.mapToScene(point);rect=view.sceneRect()
        self.assertLess(abs(after.x()-fraction.x()*rect.width()),3)
        self.assertLess(abs(after.y()-fraction.y()*rect.height()),3)

    def test_drag_pans_without_zooming_back_or_triggering_background_navigation(self):
        host=self.host('deleted');pane=host.album.pane;point=self.image_point(pane)
        QTest.mouseClick(pane.viewport(),Qt.MouseButton.LeftButton,pos=point);finish_zoom(pane)
        point=pane.viewport().rect().center();before=pane.mapToScene(point)
        drag(pane,point,point+QPoint(70,45))
        self.assertTrue(pane.detail);self.assertIs(host.stack.currentWidget(),host.album)
        self.assertGreater((pane.mapToScene(point)-before).manhattanLength(),20)
        self.assertTrue(pane.sceneRect().contains(pane.mapToScene(pane.viewport().rect().center())))

    def test_drag_that_returns_to_start_is_still_not_a_click(self):
        view=self.plain_view();clicked=[];blanks=[]
        view.clicked.connect(lambda *_:clicked.append(True));view.background_clicked.connect(lambda:blanks.append(True))
        point=self.image_point(view);drag(view,point,point+QPoint(60,20),return_to_start=True)
        drag(view,QPoint(10,200),QPoint(60,200),return_to_start=True)
        self.assertFalse(clicked);self.assertFalse(blanks)

    def test_readonly_album_right_click_and_wheel_do_not_change_photo_or_gallery(self):
        host=self.host('keep');pane=host.album.pane;point=self.image_point(pane);photo=host.album.photo
        QTest.mouseClick(pane.viewport(),Qt.MouseButton.RightButton,pos=point)
        wheel=QWheelEvent(QPointF(point),QPointF(pane.viewport().mapToGlobal(point)),QPoint(),QPoint(0,-120),
            Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        application.sendEvent(pane.viewport(),wheel)
        self.assertIs(host.album.photo,photo);self.assertIs(host.stack.currentWidget(),host.album);self.assertFalse(pane.detail)

    def test_zoomed_album_wheel_preserves_the_pixel_under_cursor(self):
        host=self.host('keep');pane=host.album.pane
        QTest.mouseClick(pane.viewport(),Qt.MouseButton.LeftButton,pos=self.image_point(pane));finish_zoom(pane)
        point=pane.viewport().rect().center()+QPoint(-70,35)
        before=pane.mapToScene(point);scale=pane.transform().m11();photo=host.album.photo
        wheel=QWheelEvent(QPointF(point),QPointF(pane.viewport().mapToGlobal(point)),QPoint(),QPoint(0,120),
            Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        application.sendEvent(pane.viewport(),wheel)
        self.assertAlmostEqual(pane.transform().m11(),scale*1.2)
        self.assertLess((pane.mapToScene(point)-before).manhattanLength(),3)
        self.assertIs(host.album.photo,photo);self.assertIs(host.stack.currentWidget(),host.album)


if __name__=='__main__':unittest.main()
