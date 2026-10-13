"""Real Windows frozen updater: wait for parent, replace, preserve data, restart."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from updater import BACKUP, cleanup_backups, intact, unpack
from version import UPDATE_ASSET
from tools.raw_fixture import write_raw

if os.name!='nt':raise SystemExit('This integration check requires Windows')
# The stage lives in the temporary folder (C: on GitHub's runners, like %LOCALAPPDATA%) and the installed APP in the
# workspace (D: there), so the update must work across drives, as it does for an APP unzipped to another disk.
installed=Path('update-check-installed').resolve();shutil.rmtree(installed,ignore_errors=True)
with tempfile.TemporaryDirectory(prefix='photo-sieve-update-check-') as temporary:
    base=Path(temporary);stage=base/'stage';stage.mkdir()
    source=unpack(Path('release')/UPDATE_ASSET,stage/'new')
    target=unpack(Path('release')/UPDATE_ASSET,installed)
    print('stage on',source.drive or '/','installed APP on',target.drive or '/')
    (target/'photos').mkdir();(target/'photos/user.jpg').write_bytes(b'unchanged original')
    (target/'library.sqlite3').write_bytes(b'unchanged records')
    (target/'old-marker.txt').write_text('user file',encoding='utf-8')
    old_manifest=json.loads((target/'installed-files.json').read_text('utf-8'));old_manifest['version']='2.9.9'
    (target/'installed-files.json').write_text(json.dumps(old_manifest),encoding='utf-8')
    parent=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
    plan={'target':str(target),'pid':parent.pid,'arguments':['--self-test-output',str(base/'restart-check.json')]}
    (stage/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
    process=subprocess.Popen([str(source/'PhotoSieve.exe'),'--apply-update',str(stage/'plan.json')],cwd=source)
    try:
        deadline=time.monotonic()+60
        while not (stage/'ready').exists():
            if process.poll() is not None:raise RuntimeError('Helper exited before ready: '+str((stage/'helper-error.txt').read_text('utf-8') if (stage/'helper-error.txt').exists() else process.returncode))
            if time.monotonic()>deadline:raise RuntimeError('Helper readiness timeout')
            time.sleep(.1)
        assert json.loads((target/'installed-files.json').read_text('utf-8'))['version']=='2.9.9','Must wait for old PID'
        parent.terminate();parent.wait(timeout=15)
        assert process.wait(timeout=180)==0
        assert json.loads((stage/'result.json').read_text('utf-8'))['ok']
        deadline=time.monotonic()+120
        while not (base/'restart-check.json').exists():
            if time.monotonic()>deadline:raise RuntimeError('Updated APP did not restart successfully')
            time.sleep(.25)
        assert json.loads((base/'restart-check.json').read_text('utf-8'))['ok']
        assert (target/'photos/user.jpg').read_bytes()==b'unchanged original'
        assert (target/'library.sqlite3').read_bytes()==b'unchanged records'
        assert (target/'old-marker.txt').read_text('utf-8')=='user file'
        assert (target/BACKUP/'stage'/'PhotoSieve.exe').is_file(),'The old version is kept beside the APP until the next start'
        assert intact(target) and cleanup_backups(target) and not (target/BACKUP).exists(),'A complete update deletes the backup'
        print('PASS: frozen Windows helper waits, replaces, preserves photos and records, restarts APP')
        # The historical check above uses a sleep process and a self-test restart.
        # This one uses the actual GUI/controller, loaded RAW decoders and crash monitor.
        normal_stage=base/'normal-stage';normal_stage.mkdir()
        normal_source=unpack(Path('release')/UPDATE_ASSET,normal_stage/'new')
        photos=base/'normal-raw';photos.mkdir()
        for index in range(10):write_raw(photos/f'{index:03}.arw')
        check_plan=base/'normal-check.json';check_plan.write_text(json.dumps({'folder':str(photos),'source':str(normal_source)}))
        env=os.environ.copy();env['QT_QPA_PLATFORM']='windows';env['QT_WIDGETS_RHI']='0'
        old_gui=subprocess.Popen([str(target/'PhotoSieve.exe'),'--update-check-plan',str(check_plan)],env=env,cwd=target)
        try:
            assert old_gui.wait(timeout=150)==0,'Real old GUI exits after production updater handoff'
            deadline=time.monotonic()+120
            while not (normal_stage/'result.json').exists():
                if time.monotonic()>deadline:raise RuntimeError('Normal update/restart did not finish')
                time.sleep(.1)
            result=json.loads((normal_stage/'result.json').read_text('utf-8'))
            assert result['ok'] and not result.get('restart_error'),result
            restarted=result['restart'];assert restarted['visible'] and restarted['pid']!=old_gui.pid
            assert Path(restarted['executable']).resolve()==(target/'PhotoSieve.exe').resolve()
            assert Path(restarted['runtime']).resolve()==(target/'_internal').resolve(),'Restart loads installed DLLs, not staged/old runtime'
            assert restarted['taskbar']['app_id']=='Quietime.PhotoSieve'
            assert Path(restarted['taskbar']['icon_resource'].rsplit(',',1)[0]).is_file()
            import ctypes
            from ctypes import wintypes as W
            from tools.windows_icon_check import check_photo_icon
            api=ctypes.WinDLL('user32');api.SendMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];api.SendMessageW.restype=ctypes.c_ssize_t
            check_photo_icon(api.SendMessageW(restarted['window'],0x7F,1,0))
            api.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
            api.PostMessageW(restarted['window'],0x0010,0,0)
            from crashlog import _alive
            deadline=time.monotonic()+30
            while _alive(restarted['pid']) and time.monotonic()<deadline:time.sleep(.1)
            assert not _alive(restarted['pid']),'Restarted GUI remains responsive and accepts normal close'
            assert (target/'photos/user.jpg').read_bytes()==b'unchanged original'
            assert Path(result['log']).is_file()
            print('PASS: actual GUI idle update, RAW decoders and watcher exit, visible normal restart, fresh installed runtime, taskbar identity/icon, responsive close',flush=True)
        finally:
            if old_gui.poll() is None:old_gui.kill();old_gui.wait()
        # Let the restarted self-test process release DLLs before TemporaryDirectory cleanup.
        time.sleep(3)
    finally:
        if parent.poll() is None:parent.terminate();parent.wait(timeout=15)
        if process.poll() is None:process.terminate();process.wait(timeout=15)
        shutil.rmtree(installed,ignore_errors=True)
