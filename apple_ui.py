"""Native Apple-style primitives: content-tinted glass, semantic controls and proximity feedback.

Only tiny cached thumbnails feed the material. Photographs are never blurred, window screenshots are
never taken, and no browser, shader dependency or idle animation loop is needed.
"""
from __future__ import annotations
import math
from PySide6.QtCore import Qt, QPointF, QRectF, QSize, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QLinearGradient, QPixmap, QCursor
from PySide6.QtWidgets import QFrame, QPushButton, QMenu
import theme


def icon(painter, name, box, color):
    """Original consistent 18px line symbols; their identity is independent of the selected shortcut."""
    painter.save(); painter.translate(box.topLeft()); painter.scale(box.width()/18, box.height()/18)
    painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush); path=QPainterPath()
    if name=='keep':path.moveTo(3,9);path.lineTo(7,13);path.lineTo(15,5)
    elif name=='delete':
        path.moveTo(3,5);path.lineTo(15,5);path.moveTo(7,3);path.lineTo(11,3)
        path.moveTo(5,5);path.lineTo(6,15);path.lineTo(12,15);path.lineTo(13,5)
        path.moveTo(8,8);path.lineTo(8,12);path.moveTo(10,8);path.lineTo(10,12)
    elif name=='restore':
        path.moveTo(6,3);path.lineTo(2,7);path.lineTo(6,11);path.moveTo(2,7);path.lineTo(10,7)
        path.cubicTo(16,7,16,15,10,15)
    elif name=='next':path.moveTo(3,9);path.lineTo(15,9);path.moveTo(10,4);path.lineTo(15,9);path.lineTo(10,14)
    elif name=='folder':
        path.moveTo(2,5);path.lineTo(2,14);path.lineTo(16,14);path.lineTo(16,6);path.lineTo(9,6);path.lineTo(7,4);path.lineTo(2,4);path.closeSubpath()
    elif name=='export':
        path.moveTo(9,12);path.lineTo(9,2);path.moveTo(5,6);path.lineTo(9,2);path.lineTo(13,6)
        path.moveTo(3,10);path.lineTo(3,15);path.lineTo(15,15);path.lineTo(15,10)
    elif name=='minimize':path.moveTo(4,9);path.lineTo(14,9)
    elif name=='maximize':path.addRect(QRectF(4,4,10,10))
    elif name=='close':
        path.moveTo(4,4);path.lineTo(14,14);path.moveTo(14,4);path.lineTo(4,14)
    else:
        for x in (4,9,14):painter.drawEllipse(QPointF(x,9),.7,.7)
    painter.drawPath(path);painter.restore()


class AppleButton(QPushButton):
    def __init__(self, text='', parent=None, symbol='', variant='neutral'):
        super().__init__(text,parent);self.symbol=symbol;self.variant=variant;self.shortcut='';self.hint=''
        self.hover=0.;self.motion=None;self.press_amount=0.;self.press_motion=None;self.proximity=False;self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor);self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pressed.connect(self.compress);self.released.connect(self.release_spring)
    def set_shortcut(self, primary, all_keys=''):
        self.shortcut=primary;self.hint=all_keys or primary
        self.setToolTip(self.text()+(' · '+self.hint if self.hint else ''))
        self.setAccessibleName(self.text());self.setAccessibleDescription('快捷键：'+self.hint if self.hint else '')
        self.updateGeometry();self.update()
    def sizeHint(self):
        metrics=self.fontMetrics();width=32 if self.symbol=='more' else 32+metrics.horizontalAdvance(self.text())
        if self.symbol and self.symbol!='more':width+=24
        if self.shortcut:width+=18+max(18,metrics.horizontalAdvance(self.shortcut))
        return QSize(width,36)
    def set_response(self,value):
        value=0. if theme.REDUCED_MOTION else max(0.,min(1.,value))
        if abs(value-self.hover)<.015:return
        if self.motion:self.motion.stop()
        start=self.hover
        def step(t):self.hover=start+(value-start)*t;self.update()
        def done():self.motion=None
        self.motion=theme.animate(self,theme.MOTION_FAST,step,curve=theme.SPRING,done=done)
    def compress(self):
        if self.press_motion:self.press_motion.stop();self.press_motion=None
        self.press_amount=1.;self.update()
    def release_spring(self):
        if self.press_motion:self.press_motion.stop()
        if theme.REDUCED_MOTION:self.press_amount=0.;self.update();return
        start=self.press_amount
        def step(t):self.press_amount=start*(1-t);self.update()
        def done():self.press_motion=None
        self.press_motion=theme.animate(self,theme.MOTION_FAST,step,curve=theme.SPRING,done=done)
    def enterEvent(self,event):
        if not self.proximity:self.set_response(1.)
        super().enterEvent(event)
    def leaveEvent(self,event):
        if not self.proximity:self.set_response(0.)
        super().leaveEvent(event)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale=1. if theme.REDUCED_MOTION else (1.+theme.HOVER_SCALE*self.hover)*(1-self.press_amount)+theme.PRESS_SCALE*self.press_amount
        painter.translate(self.width()/2,self.height()/2-self.hover);painter.scale(scale,scale);painter.translate(-self.width()/2,-self.height()/2)
        rect=QRectF(self.rect()).adjusted(2,2,-2,-2);path=QPainterPath();path.addRoundedRect(rect,rect.height()/2,rect.height()/2)
        if self.variant=='primary':fill=QColor(theme.BLUE if self.isEnabled() else '#244969')
        elif self.variant=='dock':fill=QColor(255,255,255,round(12*self.hover)+ (12 if self.isDown() else 0))
        else:fill=QColor(255,255,255,round(22+18*self.hover))
        if theme.HIGH_CONTRAST:fill=QColor('#000000');painter.setPen(QPen(QColor('#FFFFFF'),1.5))
        else:painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill);painter.drawPath(path)
        color=QColor('#FFFFFF' if theme.HIGH_CONTRAST or self.variant=='primary' else theme.GREEN if self.symbol=='keep' else theme.RED if self.symbol=='delete' else theme.LABEL)
        if not self.isEnabled():color=QColor(theme.TERTIARY)
        metrics=self.fontMetrics();text_width=metrics.horizontalAdvance(self.text())
        badge_width=max(22,metrics.horizontalAdvance(self.shortcut)+12) if self.shortcut else 0
        symbol_only=bool(self.symbol and not self.text() and not self.shortcut)
        content=text_width+(24 if self.symbol and self.symbol!='more' else 0)+(badge_width+10 if self.shortcut else 0)
        x=(self.width()-content)/2
        if self.symbol:
            if symbol_only:icon(painter,self.symbol,QRectF(self.width()/2-9,self.height()/2-9,18,18),color)
            else:icon(painter,self.symbol,QRectF(x,self.height()/2-9,18,18),color);x+=24
        if self.symbol!='more':
            painter.setPen(color);painter.drawText(QRectF(x,0,text_width+2,self.height()),Qt.AlignmentFlag.AlignVCenter,self.text());x+=text_width+10
        if self.shortcut:
            badge=QRectF(x,self.height()/2-10,badge_width,20)
            painter.setBrush(QColor(255,255,255,18));painter.setPen(QPen(QColor(255,255,255,75 if theme.HIGH_CONTRAST else 32),1));painter.drawRoundedRect(badge,5,5)
            painter.setPen(QColor('#FFFFFF' if theme.HIGH_CONTRAST else theme.SECONDARY));painter.drawText(badge,Qt.AlignmentFlag.AlignCenter,self.shortcut)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush);painter.setPen(QPen(QColor('#FFFFFF' if theme.HIGH_CONTRAST else theme.BLUE),2));painter.drawRoundedRect(rect,rect.height()/2,rect.height()/2)


class GlassSurface(QFrame):
    """A restrained navy glass surface with stable colour and a soft specular rim."""
    def __init__(self,parent=None):
        super().__init__(parent);self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,False)
    def set_sources(self,pixmaps):
        # Keep the navy tint stable rather than mixing the current photograph into it.
        return
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(self.rect()).adjusted(1,1,-1,-1);radius=rect.height()/2
        path=QPainterPath();path.addRoundedRect(rect,radius,radius)
        base=QColor('#000000' if theme.HIGH_CONTRAST else theme.ELEVATED)
        if not theme.HIGH_CONTRAST and not theme.REDUCED_TRANSPARENCY:base.setAlpha(235)
        painter.fillPath(path,base)
        if theme.HIGH_CONTRAST:
            painter.setPen(QPen(QColor('#FFFFFF'),1.5));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawPath(path);return
        # Two small gradients provide depth without photo tinting, blur or
        # continually animated shaders. Their colour remains a muted navy.
        light=QLinearGradient(rect.topLeft(),rect.bottomLeft())
        light.setColorAt(0,QColor(255,255,255,27));light.setColorAt(.48,QColor(255,255,255,3));light.setColorAt(1,QColor(0,0,0,15))
        painter.fillPath(path,light)
        rim=QLinearGradient(rect.topLeft(),rect.bottomLeft())
        rim.setColorAt(0,QColor(210,230,250,82));rim.setColorAt(.5,QColor(210,230,250,15));rim.setColorAt(1,QColor(210,230,250,31))
        painter.setPen(QPen(rim,.8));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawPath(path)
        inner=QLinearGradient(rect.topLeft(),rect.bottomLeft())
        inner.setColorAt(0,QColor(255,255,255,30));inner.setColorAt(.6,QColor(255,255,255,0))
        painter.setPen(QPen(inner,.6));painter.drawRoundedRect(rect.adjusted(1.5,1.5,-1.5,-1.5),max(0,radius-1.5),max(0,radius-1.5))


class GlassDock(GlassSurface):
    def __init__(self,parent=None):super().__init__(parent);self.controls=[];self.setMouseTracking(True)
    def register(self, controls):
        self.controls=list(controls)
        for control in self.controls:control.proximity=True;control.installEventFilter(self)
    def response_at(self, point):
        for control in self.controls:
            centre=control.mapTo(self,control.rect().center());offset=point-QPointF(centre);distance=math.hypot(offset.x(),offset.y())
            control.set_response(math.exp(-distance*distance/(2*96**2)))
    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.Enter,QEvent.Type.MouseMove):self.response_at(QPointF(self.mapFromGlobal(QCursor.pos())))
        elif event.type()==QEvent.Type.Leave:
            point=self.mapFromGlobal(QCursor.pos())
            if not self.rect().contains(point):
                for control in self.controls:control.set_response(0.)
        return False
    def leaveEvent(self,event):
        for control in self.controls:control.set_response(0.)
        super().leaveEvent(event)


class GlassPopover(QMenu):
    """Short fade anchored to the native menu trigger; keyboard/menu behaviour stays native."""
    def showEvent(self,event):
        super().showEvent(event)
        if theme.REDUCED_MOTION:return
        origin=self.pos();self.setWindowOpacity(.0)
        def step(t):
            self.setWindowOpacity(t);self.move(origin.x(),origin.y()+round(4*(1-t)))
        self.motion=theme.animate(self,theme.MOTION_FAST,step,curve=theme.SPRING)
