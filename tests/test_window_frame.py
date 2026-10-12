import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from PySide6.QtCore import Qt,QRect,QPoint,QSize
from PySide6.QtWidgets import QApplication
from apple_ui import AppleButton
from window_frame import resize_geometry
import theme
application=QApplication.instance() or QApplication([])

class WindowFrameTest(unittest.TestCase):
    def test_welcome_circle_and_all_brand_lines_share_one_visible_center_axis(self):
        from widgets import WelcomePage
        from PySide6.QtGui import QImage
        from PIL import Image
        page=WelcomePage();page.resize(1672,1041)
        image=page.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        pixels=Image.frombytes('RGBA',(image.width(),image.height()),bytes(image.bits())).convert('L')
        centers=[]
        for top,bottom in ((180,650),(760,813),(813,827),(827,850)):
            box=pixels.crop((0,top,pixels.width,bottom)).point(lambda value:255 if value>100 else 0).getbbox()
            self.assertIsNotNone(box)
            centers.append((box[0]+box[2]-1)/2)
        self.assertLessEqual(max(centers)-min(centers),1)
        self.assertTrue(all(abs(center-(pixels.width-1)/2)<=1 for center in centers))

    def test_all_edges_preserve_the_opposite_edge_and_minimum_size(self):
        E=Qt.Edge; rect=QRect(100,120,600,400)
        for edges in (E.LeftEdge,E.RightEdge,E.TopEdge,E.BottomEdge,E.LeftEdge|E.TopEdge,E.RightEdge|E.TopEdge,E.LeftEdge|E.BottomEdge,E.RightEdge|E.BottomEdge):
            changed=resize_geometry(rect,edges,QPoint(80,50),QSize(300,200),QSize(2000,2000))
            self.assertGreaterEqual(changed.width(),300); self.assertGreaterEqual(changed.height(),200)
            if edges&E.LeftEdge:self.assertEqual(changed.right(),rect.right())
            if edges&E.TopEdge:self.assertEqual(changed.bottom(),rect.bottom())
            shrunk=resize_geometry(rect,edges,QPoint(2000,2000),QSize(300,200),QSize(900,800))
            self.assertTrue(300<=shrunk.width()<=900);self.assertTrue(200<=shrunk.height()<=800)

    def test_window_control_glyph_pixels_are_centered_in_both_axes(self):
        for symbol in ('minimize','maximize','close'):
            button=AppleButton('',symbol=symbol);button.setFocusPolicy(Qt.FocusPolicy.NoFocus);button.resize(40,30)
            image=button.grab().toImage();points=[]
            for y in range(image.height()):
                for x in range(image.width()):
                    pixel=image.pixelColor(x,y)
                    if min(pixel.red(),pixel.green(),pixel.blue())>180:points.append((x,y))
            self.assertTrue(points,symbol)
            cx=(min(x for x,y in points)+max(x for x,y in points))/2
            cy=(min(y for x,y in points)+max(y for x,y in points))/2
            self.assertLessEqual(abs(cx-(image.width()-1)/2),1,symbol)
            self.assertLessEqual(abs(cy-(image.height()-1)/2),1,symbol)
