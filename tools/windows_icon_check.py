"""Check the actual pixels of a Windows HICON, not just existence of an icon resource."""
import ctypes
from ctypes import wintypes as W

def check_photo_icon(icon):
    class Header(ctypes.Structure):
        _fields_=[('size',ctypes.c_uint32),('width',ctypes.c_int32),('height',ctypes.c_int32),
            ('planes',ctypes.c_uint16),('bits',ctypes.c_uint16),('compression',ctypes.c_uint32),
            ('image_size',ctypes.c_uint32),('xppm',ctypes.c_int32),('yppm',ctypes.c_int32),
            ('used',ctypes.c_uint32),('important',ctypes.c_uint32)]
    class Info(ctypes.Structure):
        _fields_=[('header',Header),('colors',ctypes.c_uint32*1)]
    api=ctypes.WinDLL('user32',use_last_error=True);gdi=ctypes.WinDLL('gdi32',use_last_error=True)
    gdi.CreateCompatibleDC.argtypes=[W.HDC];gdi.CreateCompatibleDC.restype=W.HDC
    gdi.CreateDIBSection.argtypes=[W.HDC,ctypes.POINTER(Info),W.UINT,ctypes.POINTER(ctypes.c_void_p),W.HANDLE,W.DWORD];gdi.CreateDIBSection.restype=W.HANDLE
    gdi.SelectObject.argtypes=[W.HDC,W.HANDLE];gdi.SelectObject.restype=W.HANDLE
    gdi.DeleteObject.argtypes=[W.HANDLE];gdi.DeleteObject.restype=W.BOOL
    gdi.DeleteDC.argtypes=[W.HDC];gdi.DeleteDC.restype=W.BOOL
    api.DrawIconEx.argtypes=[W.HDC,ctypes.c_int,ctypes.c_int,W.HANDLE,ctypes.c_int,ctypes.c_int,W.UINT,W.HANDLE,W.UINT];api.DrawIconEx.restype=W.BOOL
    assert icon,'Windows exposes a real window/taskbar icon'
    dc=gdi.CreateCompatibleDC(None);info=Info();info.header=Header(40,32,-32,1,32,0,4096,0,0,0,0);pixels=ctypes.c_void_p()
    bitmap=gdi.CreateDIBSection(dc,ctypes.byref(info),0,ctypes.byref(pixels),None,0)
    assert dc and bitmap and pixels.value,'An icon verification surface is available'
    old=gdi.SelectObject(dc,bitmap)
    try:
        ctypes.memset(pixels,0,4096)
        assert api.DrawIconEx(dc,0,0,icon,32,32,0,None,3)
        data=ctypes.string_at(pixels,4096)
        blue=sum(abs(data[i]-244)+abs(data[i+1]-121)+abs(data[i+2]-18)<15 for i in range(0,len(data),4))
        white=sum(min(data[i:i+3])>245 for i in range(0,len(data),4))
        assert blue>80 and white>40,f'Icon must be the blue photo/focus logo, not the generic executable icon: {blue=}, {white=}'
        return {'blue_pixels':blue,'white_pixels':white}
    finally:
        gdi.SelectObject(dc,old);gdi.DeleteObject(bitmap);gdi.DeleteDC(dc)
