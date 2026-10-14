"""Rounded native-pixel eye/face preview; it never applies a decision to another photo."""
from PySide6.QtCore import Qt,QRectF,QPointF
from PySide6.QtGui import QPainter,QPainterPath,QColor,QPen,QPixmap,QFont
from PySide6.QtWidgets import QWidget
import theme

class FacePanel(QWidget):
    def __init__(self,parent):
        super().__init__(parent);self.result=None;self.picture=QPixmap();self.fit=False;self.scale=1.0
        self.setFixedSize(224,208);self.move(12,12);self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip('仅对应左图 · 原图像素\n单击切换适应小窗 / 100% · 滚轮缩放');self.hide()
    def show_result(self,result):
        self.result=result;self.picture=QPixmap();self.fit=False;self.scale=1.0
        if result and result.get('imagebytes'):self.picture.loadFromData(result['imagebytes'])
        if result and not result.get('faces'):self.hide();return
        self.show();self.raise_();self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.SEPARATOR),1));painter.setBrush(QColor(theme.ELEVATED));painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),12,12)
        font=QFont(self.font());font.setPixelSize(12);painter.setFont(font);painter.setPen(QColor(theme.LABEL))
        if self.picture.isNull():painter.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'正在检测左图人脸…');return
        area=QRectF(8,32,self.width()-16,self.height()-40);dpr=self.devicePixelRatioF()
        scale=min(area.width()*dpr/self.picture.width(),area.height()*dpr/self.picture.height()) if self.fit else self.scale
        title=('原图局部' if self.result.get('is_partial') else '原图人脸')+f' · {scale*100:.0f}%'
        painter.drawText(QRectF(10,6,self.width()-20,22),Qt.AlignmentFlag.AlignVCenter,title)
        clip=QPainterPath();clip.addRoundedRect(area,8,8);painter.setClipPath(clip)
        centre=QPointF(self.picture.width()/2,self.picture.height()/2)
        if not self.fit:
            eyes=(self.result.get('face') or {}).get('landmarks',[])[:2]
            if len(eyes)==2:
                ow,oh=self.result['original_size'];left,top,_,_=self.result['crop_box']
                centre=QPointF(sum(p[0] for p in eyes)*ow/2-left,sum(p[1] for p in eyes)*oh/2-top)
        origin=area.center()-centre*(scale/dpr)
        origin=QPointF(round(origin.x()*dpr)/dpr,round(origin.y()*dpr)/dpr)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,self.fit or abs(scale-1)>.001)
        painter.drawPixmap(QRectF(origin.x(),origin.y(),self.picture.width()*scale/dpr,self.picture.height()*scale/dpr),self.picture,QRectF(self.picture.rect()))
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and not self.picture.isNull():self.fit=not self.fit;self.scale=1.0;self.update()
        event.accept()
    def mouseReleaseEvent(self,event):event.accept()
    def wheelEvent(self,event):
        if not self.picture.isNull():self.fit=False;self.scale=max(.25,min(4,self.scale*1.2**(event.angleDelta().y()/120)));self.update()
        event.accept()
