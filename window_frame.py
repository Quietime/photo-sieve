"""Frameless appearance with Windows' own resize, move and screen-edge snapping."""
import ctypes
import os
from PySide6.QtCore import Qt, QObject, QEvent, QPoint, QRect, QRectF, QSize,QTimer
from PySide6.QtGui import QRegion, QPainterPath
from PySide6.QtWidgets import QWidget, QAbstractButton, QComboBox, QAbstractSlider, QAbstractSpinBox, QLineEdit
INTERACTIVE = (QAbstractButton, QComboBox, QAbstractSlider, QAbstractSpinBox, QLineEdit)

def resize_geometry(rect, edges, delta, minimum, maximum):
    x,y,w,h=rect.x(),rect.y(),rect.width(),rect.height()
    if edges & Qt.Edge.LeftEdge:
        nw=max(minimum.width(),min(maximum.width(),w-delta.x())); x+=w-nw; w=nw
    elif edges & Qt.Edge.RightEdge: w=max(minimum.width(),min(maximum.width(),w+delta.x()))
    if edges & Qt.Edge.TopEdge:
        nh=max(minimum.height(),min(maximum.height(),h-delta.y())); y+=h-nh; h=nh
    elif edges & Qt.Edge.BottomEdge: h=max(minimum.height(),min(maximum.height(),h+delta.y()))
    return QRect(x,y,w,h)

class ResizeGrip(QWidget):
    def __init__(self,window,edges,cursor):
        super().__init__(window); self.host=window; self.edges=edges; self.drag=None
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground); self.setCursor(cursor)
    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton: return
        handle=self.host.windowHandle()
        if handle and handle.startSystemResize(self.edges): event.accept(); return
        self.drag=(event.globalPosition().toPoint(),self.host.geometry()); event.accept()
    def mouseMoveEvent(self,event):
        if self.drag:
            start,rect=self.drag
            minimum=self.host.minimumSize().expandedTo(self.host.minimumSizeHint())
            self.host.setGeometry(resize_geometry(rect,self.edges,event.globalPosition().toPoint()-start,minimum,self.host.maximumSize()))
            event.accept()
    def mouseReleaseEvent(self,event): self.drag=None; event.accept()

class WindowFrame(QObject):
    def __init__(self,window,chrome):
        super().__init__(window); self.window=window; self.chrome=chrome; self.native=False; self.native_handle=None; self.dwm_round=False
        self.grips=[]; E=Qt.Edge; C=Qt.CursorShape
        for edges,cursor in [(E.LeftEdge,C.SizeHorCursor),(E.RightEdge,C.SizeHorCursor),
            (E.TopEdge,C.SizeVerCursor),(E.BottomEdge,C.SizeVerCursor),
            (E.TopEdge|E.LeftEdge,C.SizeFDiagCursor),(E.TopEdge|E.RightEdge,C.SizeBDiagCursor),
            (E.BottomEdge|E.LeftEdge,C.SizeBDiagCursor),(E.BottomEdge|E.RightEdge,C.SizeFDiagCursor)]:
            self.grips.append(ResizeGrip(window,edges,cursor))
        self.state_timer=QTimer(self);self.state_timer.setSingleShot(True);self.state_timer.timeout.connect(self.place)
        window.installEventFilter(self); self.place()

    def place(self):
        w,h=self.window.width(),self.window.height(); b=6; corner=14
        rects=[QRect(0,corner,b,max(0,h-2*corner)),QRect(w-b,corner,b,max(0,h-2*corner)),
            QRect(corner,0,max(0,w-2*corner),b),QRect(corner,h-b,max(0,w-2*corner),b),
            QRect(0,0,corner,corner),QRect(w-corner,0,corner,corner),
            QRect(0,h-corner,corner,corner),QRect(w-corner,h-corner,corner,corner)]
        for grip,rect in zip(self.grips,rects):
            grip.setGeometry(rect); grip.setVisible(not self.window.isMaximized() and not self.window.isFullScreen()); grip.raise_()
        if self.window.isMaximized() or self.window.isFullScreen() or self.dwm_round: self.window.clearMask()
        else:
            path=QPainterPath(); path.addRoundedRect(QRectF(self.window.rect()),10,10)
            self.window.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.WindowStateChange):
            self.place()
            # A native WM_SIZE can reach the filter before Qt commits its final window state.
            self.state_timer.start(0)
        if event.type()==QEvent.Type.Show: self.enable_native()
        elif event.type()==QEvent.Type.WinIdChange:QTimer.singleShot(0,self.enable_native)
        return False

    def enable_native(self):
        if os.name!='nt': return
        from ctypes import wintypes as W
        api=ctypes.WinDLL('user32',use_last_error=True)
        api.IsWindow.argtypes=[W.HWND]; api.IsWindow.restype=W.BOOL
        handle=int(self.window.winId())
        if self.native and handle==self.native_handle:return
        if not api.IsWindow(handle): return  # offscreen tests have no real HWND
        api.GetWindowLongPtrW.argtypes=[W.HWND,ctypes.c_int]; api.GetWindowLongPtrW.restype=ctypes.c_ssize_t
        api.SetWindowLongPtrW.argtypes=[W.HWND,ctypes.c_int,ctypes.c_ssize_t]; api.SetWindowLongPtrW.restype=ctypes.c_ssize_t
        api.SetWindowPos.argtypes=[W.HWND,W.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.UINT]
        self.native=True; self.native_handle=handle; self.api=api
        # The system needs these styles for snap/maximize/edge resizing; NCCALCSIZE keeps its title bar invisible.
        style=api.GetWindowLongPtrW(handle,-16)
        api.SetWindowLongPtrW(handle,-16,style|0x00CF0000)
        api.SetWindowPos(handle,None,0,0,0,0,0x0027)
        self.install_icons(handle)
        try:
            from app_identity import set_taskbar_identity
            self.taskbar_identity=set_taskbar_identity(handle)
        except Exception:
            import crashlog
            crashlog.LOGGER.exception('Could not set Windows taskbar identity')
        try:
            preference=ctypes.c_int(2)
            dwm=ctypes.WinDLL('dwmapi'); dwm.DwmSetWindowAttribute.argtypes=[W.HWND,W.DWORD,ctypes.c_void_p,W.DWORD]
            self.dwm_round=dwm.DwmSetWindowAttribute(handle,33,ctypes.byref(preference),ctypes.sizeof(preference))==0
            self.place()
        except (AttributeError,OSError): pass

    def install_icons(self,handle):
        """Set both actual HWND icons after native frame creation, independent of Qt's ICO plugin."""
        from pathlib import Path
        from ctypes import wintypes as W
        api=self.api
        api.LoadImageW.argtypes=[W.HINSTANCE,W.LPCWSTR,W.UINT,ctypes.c_int,ctypes.c_int,W.UINT]
        api.LoadImageW.restype=W.HANDLE
        api.SendMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];api.SendMessageW.restype=ctypes.c_ssize_t
        api.DestroyIcon.argtypes=[W.HANDLE];api.DestroyIcon.restype=W.BOOL
        path=str(Path(__file__).parent/'assets'/'photosieve.ico')
        self.native_icons=[]
        for kind,size in ((0,16),(1,32)):
            icon=api.LoadImageW(None,path,1,size,size,0x10)
            if icon:
                self.native_icons.append(icon);api.SendMessageW(handle,0x80,kind,icon)
        handles=tuple(self.native_icons)
        self.window.destroyed.connect(lambda: [api.DestroyIcon(icon) for icon in handles])

    def native_event(self,message):
        if not self.native: return None
        from ctypes import wintypes as W
        msg=W.MSG.from_address(int(message)); api=self.api
        if msg.message==0x0083:  # WM_NCCALCSIZE: draw content over the whole frame
            api.IsZoomed.argtypes=[W.HWND]; api.IsZoomed.restype=W.BOOL
            if msg.wParam and api.IsZoomed(msg.hWnd):
                class MonitorInfo(ctypes.Structure):
                    _fields_=[('size',W.DWORD),('monitor',W.RECT),('work',W.RECT),('flags',W.DWORD)]
                api.MonitorFromWindow.argtypes=[W.HWND,W.DWORD]; api.MonitorFromWindow.restype=W.HANDLE
                api.GetMonitorInfoW.argtypes=[W.HANDLE,ctypes.POINTER(MonitorInfo)]
                info=MonitorInfo(); info.size=ctypes.sizeof(info)
                if api.GetMonitorInfoW(api.MonitorFromWindow(msg.hWnd,2),ctypes.byref(info)):
                    rect=W.RECT.from_address(msg.lParam)
                    rect.left,rect.top,rect.right,rect.bottom=info.work.left,info.work.top,info.work.right,info.work.bottom
            return True,0
        if msg.message!=0x0084: return None  # WM_NCHITTEST
        point=W.POINT(ctypes.c_short(msg.lParam&0xffff).value,ctypes.c_short((msg.lParam>>16)&0xffff).value)
        api.ScreenToClient.argtypes=[W.HWND,ctypes.POINTER(W.POINT)]; api.ScreenToClient(msg.hWnd,ctypes.byref(point))
        dpr=self.window.devicePixelRatioF(); local=QPoint(round(point.x/dpr),round(point.y/dpr))
        x,y=local.x(),local.y(); w,h=self.window.width(),self.window.height(); b=6
        if not self.window.isMaximized() and not self.window.isFullScreen():
            left,right=x<b,x>=w-b; top,bottom=y<b,y>=h-b
            hit=13 if top and left else 14 if top and right else 16 if bottom and left else 17 if bottom and right else 10 if left else 11 if right else 12 if top else 15 if bottom else 0
            if hit: return True,hit
        here=self.chrome.mapFrom(self.window,local)
        if self.chrome.rect().contains(here):
            child=self.chrome.childAt(here)
            # Toolbar controls live in this row too (combo boxes, sliders): any control under the cursor, or a
            # control containing it, stays HTCLIENT so it can be clicked; labels and empty space drag the window.
            while child is not None and child is not self.chrome and not isinstance(child,INTERACTIVE):child=child.parentWidget()
            if child is None or child is self.chrome: return True,2  # HTCAPTION gives Windows its own drag/snap/double-click
        return True,1  # HTCLIENT: custom controls must never become the system's hidden caption
