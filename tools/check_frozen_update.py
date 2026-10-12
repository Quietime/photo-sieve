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
        # Let the restarted self-test process release DLLs before TemporaryDirectory cleanup.
        time.sleep(3)
    finally:
        if parent.poll() is None:parent.terminate();parent.wait(timeout=15)
        if process.poll() is None:process.terminate();process.wait(timeout=15)
        shutil.rmtree(installed,ignore_errors=True)
