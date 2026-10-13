"""Explicit Windows taskbar identity, including the icon used by Explorer's app group."""
import os
from pathlib import Path
import subprocess
import sys

APP_ID='Quietime.PhotoSieve'

def set_taskbar_identity(handle):
    if os.name!='nt':return {}
    import pythoncom
    from win32com.propsys import propsys,pscon
    icon=Path(__file__).resolve().parent/'assets'/'photosieve.ico'
    command=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).parent/'photosieve.py')]
    values={pscon.PKEY_AppUserModel_ID:APP_ID,
            pscon.PKEY_AppUserModel_RelaunchCommand:subprocess.list2cmdline(command),
            pscon.PKEY_AppUserModel_RelaunchDisplayNameResource:'照片快筛',
            pscon.PKEY_AppUserModel_RelaunchIconResource:str(icon)+',0'}
    pythoncom.CoInitialize()
    store=None
    try:
        store=propsys.SHGetPropertyStoreForWindow(handle)
        # Set relaunch information before the identity: Explorer uses it to form the taskbar group.
        for key,value in sorted(values.items(),key=lambda entry:entry[0]==pscon.PKEY_AppUserModel_ID):
            store.SetValue(key,propsys.PROPVARIANTType(value,pythoncom.VT_LPWSTR))
        return {'app_id':store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue(),
                'icon_resource':store.GetValue(pscon.PKEY_AppUserModel_RelaunchIconResource).GetValue(),
                'relaunch_command':store.GetValue(pscon.PKEY_AppUserModel_RelaunchCommand).GetValue()}
    finally:
        store=None;pythoncom.CoUninitialize()
