#!/bin/bash
set -e
export WINEDEBUG=-all
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
cd /work/app
wine64 python -m pip install --no-index --find-links 'Z:\work\wheels' -r requirements-desktop.txt 'pyinstaller>=6,<7' 'numpy==1.26.4' pefile pywin32-ctypes colorama
wine64 python -c 'from PySide6.QtWidgets import QApplication; from PIL import Image; import rawpy,pillow_heif; import app,desktop; print("Windows Python and native Qt imports OK")'
wine64 python -m PyInstaller --noconfirm --clean photo-sieve.spec
