"""Actual Windows install/uninstall at a chosen location; verify icon and preserve workspace data."""
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import json

if os.name!='nt': raise SystemExit('Windows only')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from updater import intact

with tempfile.TemporaryDirectory(prefix='photosieve-installer-') as temporary:
    base=Path(temporary); target=base/'Selected installation location'
    installer=Path('release/PhotoSieve-Setup-Windows-x64.exe').resolve()
    subprocess.run([str(installer),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/DIR='+str(target)],check=True,timeout=120)
    exe=target/'PhotoSieve.exe'
    assert exe.is_file() and intact(target),'Chosen installation folder has every verified program file'
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    shell.ExtractIconExW.argtypes=[ctypes.c_wchar_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint]
    shell.ExtractIconExW.restype=ctypes.c_uint
    assert shell.ExtractIconExW(str(exe),-1,None,None,0)>0,'EXE contains an actual Windows icon resource'
    diagnostics=base/'installed-diagnostics.json'
    subprocess.run([str(exe),'--self-test-output',str(diagnostics)],check=True,timeout=120)
    assert json.loads(diagnostics.read_text('utf-8'))['ok']
    workspace=target/'workspaces'/'test-library'; workspace.mkdir(parents=True)
    record=workspace/'user-record.txt'; record.write_text('preserved workspace',encoding='utf-8')
    subprocess.run([str(target/'unins000.exe'),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],check=True,timeout=120)
    assert record.read_text('utf-8')=='preserved workspace','Uninstall preserves all user workspace files'
    assert not exe.exists(),'Uninstaller removes the program'
    print('PASS: install at selected location, embedded EXE icon, installed Qt/RAW/HEIC checks, uninstall preserves workspace')
