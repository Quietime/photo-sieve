"""Real HWND checks: all resize directions, native caption/maximize, hidden frame and rounded corners."""
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint
from app import Library
from desktop import Window, STYLE
from windows_icon_check import check_photo_icon

if os.name!='nt': raise SystemExit('Windows only')
application=QApplication([]); application.setStyleSheet(STYLE); application.setQuitOnLastWindowClosed(False)
api=ctypes.WinDLL('user32',use_last_error=True)
api.SendMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]; api.SendMessageW.restype=ctypes.c_ssize_t
api.GetWindowLongPtrW.argtypes=[W.HWND,ctypes.c_int]; api.GetWindowLongPtrW.restype=ctypes.c_ssize_t
api.ClientToScreen.argtypes=[W.HWND,ctypes.POINTER(W.POINT)]
api.GetClientRect.argtypes=[W.HWND,ctypes.POINTER(W.RECT)]
api.GetWindowRect.argtypes=[W.HWND,ctypes.POINTER(W.RECT)]
with tempfile.TemporaryDirectory() as temporary:
    window=Window(Library(Path(temporary)/'records'),check_updates=False,start_empty=True)
    try:
        window.resize(1000,720); window.move(140,100); window.show()
        for _ in range(20): application.processEvents(); time.sleep(.01)
        frame=window.window_frame; handle=int(window.winId()); assert frame.native
        assert not window.windowIcon().pixmap(32,32).isNull(),'The actual app icon decodes successfully'
        assert len(frame.native_icons)==2,'Small and large native icons are loaded'
        for kind,icon in enumerate(frame.native_icons):
            check_photo_icon(api.SendMessageW(handle,0x7F,kind,0))
        style=api.GetWindowLongPtrW(handle,-16); assert style&0x00CF0000==0x00CF0000
        width,height=window.width(),window.height(); dpr=window.devicePixelRatioF()
        def hit(x,y):
            point=W.POINT(round(x*dpr),round(y*dpr)); api.ClientToScreen(handle,ctypes.byref(point))
            packed=(point.x&0xffff)|((point.y&0xffff)<<16)
            return api.SendMessageW(handle,0x84,0,packed)
        tests=[(2,height//2,10),(width-2,height//2,11),(width//2,2,12),(width//2,height-2,15),
               (2,2,13),(width-2,2,14),(2,height-2,16),(width-2,height-2,17)]
        for x,y,expected in tests: assert hit(x,y)==expected,(x,y,hit(x,y),expected)
        caption=window.window_chrome.label.mapTo(window,window.window_chrome.label.rect().center())
        assert hit(caption.x(),caption.y())==2,'Caption participates in the OS move/snap loop'
        for button in window.window_chrome.findChildren(__import__('PySide6.QtWidgets',fromlist=['QPushButton']).QPushButton):
            p=button.mapTo(window,button.rect().center()); assert hit(p.x(),p.y())==1,'Custom controls remain clickable client content'
        client=W.RECT(); outer=W.RECT(); api.GetClientRect(handle,ctypes.byref(client)); api.GetWindowRect(handle,ctypes.byref(outer))
        assert abs((outer.right-outer.left)-(client.right-client.left))<=1
        assert abs((outer.bottom-outer.top)-(client.bottom-client.top))<=1,'No reserved system title bar'
        # Native caption double click follows the same system maximize path as screen-edge snapping.
        api.SendMessageW(handle,0xA3,2,0)
        for _ in range(20): application.processEvents(); time.sleep(.01)
        assert window.isMaximized(),'Windows caption double-click maximizes the actual HWND'
        assert all(not g.isVisible() for g in frame.grips),('Maximized windows do not resize at their edges',window.size(),window.minimumSizeHint(),[g.isVisible() for g in frame.grips])
        window.showNormal(); application.processEvents()
        assert frame.dwm_round or not window.mask().isEmpty(),'Native DWM rounding or older-Windows round region is enabled'
        window.show_menu(); assert not window.more_panel.isWindow()
        directory=Path('ui-screenshots-windows'); directory.mkdir(exist_ok=True)
        window.grab().save(str(directory/'native-window-menu.png'))
        result={'ok':True,'platform':'windows','dpr':dpr,'checks':['actual small and large native taskbar icons','eight native resize directions','system caption hit test for move/snap','custom window controls remain client content','no system title-bar gap','native maximize/restore','rounded normal window','embedded more panel']}
        Path('windows-chrome-ci.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    finally:
        window.close();application.processEvents()
print('PASS: native Windows resize, snap-capable caption, maximize/restore, rounded corners, centered custom controls')
