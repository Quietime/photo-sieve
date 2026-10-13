"""Exercise the update flow: the offer waits for a pause, declining downloads nothing, accepting downloads in the
background and installs once idle, the restart keeps the library, and the next start reports and cleans up."""
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog
from PIL import Image
from app import Library
from desktop import Window
import updater

application=QApplication([])
def wait(predicate):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        application.processEvents()
        if predicate():return
        time.sleep(.01)
    raise TimeoutError('update UI check timed out')

def controller_for(window,base):
    controller=window.updates;controller.settings=QSettings(str(base/'preferences.ini'),QSettings.Format.IniFormat)
    controller.idle_seconds=lambda:time.monotonic()-controller.last_activity   # this computer's own mouse and keyboard must not decide the check
    return controller

with tempfile.TemporaryDirectory() as temporary,patch.dict('os.environ',{'LOCALAPPDATA':str(Path(temporary)/'local')}):
    base=Path(temporary);photos=base/'photos';photos.mkdir()
    Image.new('RGB',(400,300),'red').save(photos/'original.jpg')
    original=(photos/'original.jpg').read_bytes()
    lib=Library(base/'records');lib.scan(str(photos));wait(lambda:not lib.job['running'])
    window=Window(lib);window.show();wait(lambda:window.keep_button.isEnabled())
    controller=controller_for(window,base)
    root=base/'stage/new/PhotoSieve';root.mkdir(parents=True)
    release={'version':'99.0.0','page':'https://github.com/Quietime/photo-sieve/releases/tag/v99.0.0','notes':'- 更快'}
    asked=[]
    with patch('sys.frozen',True,create=True),patch('updater.credential_store',side_effect=AssertionError('Public updates must not access credentials')),patch('updater.check_release',return_value=release) as check_release,patch('updater.download_release',return_value=root) as download:
        controller.settings.setValue('automatic_updates',False);controller.startup_check()
        assert not check_release.called,'Turning off the startup check must keep the APP from asking GitHub'
        controller.settings.setValue('automatic_updates',True)
        # Startup results appear even while input remains active; declining downloads nothing.
        controller.ask=lambda offered:(asked.append(offered['version']),False)[1]
        controller.last_activity=time.monotonic()+60;controller.startup_check();wait(lambda:bool(asked))
        check_release.assert_called_once_with('')
        assert asked==['99.0.0'] and controller.offer is None and not download.called,'Startup must prompt without waiting for idle'
        asked.clear()
        # Later background checks still wait until review pauses.
        controller.check(False);wait(lambda:controller.offer is not None)
        controller.tick();assert not asked,'The offer must not interrupt a held key'
        controller.last_activity=time.monotonic()-3;controller.tick()
        assert asked==['99.0.0'] and controller.offer is None and not download.called,'Declining must not download'
        # A pending offer waits while a check is still asking GitHub, and while a dialog or menu is open.
        controller.offer=release;controller.running=True;controller.last_activity=time.monotonic()-5;controller.tick()
        assert asked==['99.0.0'],'No second prompt while a check runs';controller.running=False
        dialog=QDialog(window);dialog.setModal(True);dialog.open();wait(lambda:QApplication.activeModalWidget() is dialog)
        controller.last_activity=time.monotonic()-5;controller.tick()
        assert asked==['99.0.0'] and controller.idle_seconds()<1,'An open dialog counts as activity';dialog.close();controller.offer=None
        # Asked for from the menu, the offer appears at once; accepting downloads while review goes on.
        controller.ask=lambda offered:True;controller.last_activity=time.monotonic()+60
        controller.check();wait(lambda:controller.staged is not None)
        assert download.call_count==1 and not window.closed and window.keep_button.isEnabled(),'Downloads must not block photo review'
    # If the installer cannot start, the update is dropped quietly: the window works again and the blue dot goes.
    window.update_ready();assert window.badge.isVisible()
    with patch('updater.launch_helper',side_effect=RuntimeError('更新安装器启动失败')):
        controller.last_activity=time.monotonic()-5;controller.tick();wait(lambda:not controller.installing)
        assert controller.staged is None and not window.badge.isVisible() and window.centralWidget().isEnabled() and not window.busy
    controller.staged=root   # downloaded again later
    with patch('updater.launch_helper',return_value=base/'stage') as launch:
        window.busy=True;controller.last_activity=time.monotonic()-5;controller.tick();assert not launch.called,'Scans and file work finish first'
        window.busy=False;controller.last_activity=time.monotonic();controller.tick();assert not launch.called,'Recent interaction must delay installation'
        controller.last_activity=time.monotonic()-5;controller.tick();wait(lambda:window.closed)
        assert launch.call_count==1
        arguments=launch.call_args.args[2]
        assert arguments[arguments.index('--data-dir')+1]==str(lib.directory),'Restart must preserve selected library'
        assert (photos/'original.jpg').read_bytes()==original
        assert not controller.timer.isActive()
        assert controller.settings.value('last_update_stage')==str(base/'stage')
    # The next start reports the result, then deletes the finished update and anything older.
    updates=updater.updates_dir();stage=updates/'v99.0.0-done'
    (stage/'new/PhotoSieve').mkdir(parents=True);(stage/'new/PhotoSieve/PhotoSieve.exe').write_bytes(b'new')
    (stage/'backup').mkdir();(stage/'backup/PhotoSieve.exe').write_bytes(b'old')
    (stage/'result.json').write_text(json.dumps({'version':'99.0.0','ok':True}),encoding='utf-8')
    (updates/'v98.0.0-abandoned').mkdir()
    restarted=Window(Library(base/'records'),check_updates=False);restarted.show();after=controller_for(restarted,base)
    after.settings.setValue('last_update_stage',str(stage))
    after.report_previous();assert '已更新至 99.0.0' in restarted.toast.message and not after.settings.value('last_update_stage')
    after.running=True   # the startup check is still waiting on a slow GitHub: cleanup goes ahead anyway
    after.tidy();wait(lambda:not any(updates.iterdir()));after.running=False
    # A version that failed to install is not offered again by the same start; a manual check still offers it.
    failed=updates/'v99.0.0-failed';failed.mkdir(parents=True)
    (failed/'result.json').write_text(json.dumps({'version':'99.0.0','ok':False,'error':'disk full'}),encoding='utf-8')
    after.settings.setValue('last_update_stage',str(failed));after.report_previous()
    assert '更新失败' in restarted.toast.message
    with patch('sys.frozen',True,create=True),patch('updater.check_release',return_value=release):
        after.check(False);wait(lambda:not after.running);assert after.offer is None,'No automatic re-offer after a failed install'
        offered=[];after.ask=lambda o:(offered.append(o['version']),False)[1]
        after.check();wait(lambda:not after.running and offered);assert offered==['99.0.0']
    restarted.close()
    print('PASS: immediate startup prompt, later offer waits for a pause and for dialogs, decline, single prompt, background download, failed launch, busy guard, idle install, preserved library, cleanup after restart, no re-offer after a failure')
