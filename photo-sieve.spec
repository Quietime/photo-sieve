# Native Windows build. Qt Widgets only, no QtWebEngine or webview.
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files
binaries = collect_dynamic_libs('rawpy') + collect_dynamic_libs('pillow_heif') + collect_dynamic_libs('cv2')
datas = collect_data_files('pillow_heif') + [('assets/photosieve.ico','assets'),('assets/photosieve.png','assets'),('assets/welcome-background.png','assets'),('assets/face_detection_yunet_2023mar.onnx','assets'),('assets/face-model-license.txt','assets'),('assets/haarcascade_eye_tree_eyeglasses.xml','assets'),('assets/eye-model-license.txt','assets'),('assets/exiftool','assets/exiftool'),('assets/remote','assets/remote'),('assets/fonts','assets/fonts')]
a = Analysis(['photosieve.py'], pathex=[], binaries=binaries, datas=datas,
    hiddenimports=['win32com.propsys.propsys','win32com.propsys.pscon','pythoncom','pywintypes','rawpy', 'pillow_heif', 'PIL.JpegImagePlugin', 'PIL.TiffImagePlugin', 'PIL.PngImagePlugin'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtWebEngineQuick',
              'PySide6.QtPdf','PySide6.QtPdfWidgets','PySide6.QtQml','PySide6.QtQuick',
              'fastapi','starlette','uvicorn','pydantic'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PhotoSieve', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, icon='assets/photosieve.ico')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PhotoSieve')
