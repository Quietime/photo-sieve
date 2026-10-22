"""Passive rounded face image, isolated from the main photo's mouse actions."""
from PySide6.QtCore import Qt,QRectF,QSize
from PySide6.QtGui import QPainter,QPainterPath,QPixmap
from PySide6.QtWidgets import QWidget

class FacePanel(QWidget):
    def __init__(self,parent):
        super().__init__(parent);self.result=None;self.picture=QPixmap();self.preview_limit=QSize(160,180)
        self.move(4,4);self.setFocusPolicy(Qt.FocusPolicy.NoFocus);self.hide()
    def show_result(self,result):
        payload=result.get('imagebytes') if result else None
        if payload is getattr(self,'_payload',None) and not self.picture.isNull():self.result=result;return
        self._payload=payload
        self.result=result;self.picture=QPixmap()
        if result and result.get('imagebytes'):self.picture.loadFromData(result['imagebytes'])
        if self.picture.isNull():self.hide();return
        size=self.picture.size().scaled(self.preview_limit,Qt.AspectRatioMode.KeepAspectRatio)
        self.setFixedSize(size);self.show();self.raise_();self.update()
    def set_preview_limit(self,width,height):
        self.preview_limit=QSize(width,height)
        if not self.picture.isNull():self.setFixedSize(self.picture.size().scaled(self.preview_limit,Qt.AspectRatioMode.KeepAspectRatio))
    def paintEvent(self,event):
        if self.picture.isNull():return
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area=QRectF(self.rect());clip=QPainterPath();clip.addRoundedRect(area,10,10)
        painter.setClipPath(clip);painter.drawPixmap(area,self.picture,QRectF(self.picture.rect()))
    # Consume these events so they cannot zoom, advance or decide the main photo.
    def mousePressEvent(self,event):event.accept()
    def mouseReleaseEvent(self,event):event.accept()
    def mouseDoubleClickEvent(self,event):event.accept()
    def wheelEvent(self,event):event.accept()
