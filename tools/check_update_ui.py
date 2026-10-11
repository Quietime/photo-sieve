"""Exercise asynchronous private update checks and idle/busy installation rules."""
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from PIL import Image
from app import Library
from desktop import Window

application=QApplication([])
def wait(predicate):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        application.processEvents()
        if predicate():return
        time.sleep(.01)
    raise TimeoutError('update UI check timed out')

with tempfile.TemporaryDirectory() as temporary:
    base=Path(temporary);photos=base/'photos';photos.mkdir()
    Image.new('RGB',(400,300),'red').save(photos/'original.jpg')
    original=(photos/'original.jpg').read_bytes()
    lib=Library(base/'records');lib.scan(str(photos));wait(lambda:not lib.job['running'])
    window=Window(lib);window.show();wait(lambda:window.keep_button.isEnabled())
    controller=window.updates;controller.settings=QSettings(str(base/'preferences.ini'),QSettings.Format.IniFormat)
    controller.idle_seconds=lambda:time.monotonic()-controller.last_activity   # this computer's own mouse and keyboard must not decide the check
    root=base/'stage/new/PhotoSieve';root.mkdir(parents=True)
    release={'version':'99.0.0','page':'https://github.com/Quietime/photo-sieve/releases/tag/v99.0.0'}
    with patch('sys.frozen',True,create=True),patch('updater.credential_store',side_effect=AssertionError('Public updates must not access credentials')),patch('updater.check_release',return_value=release) as check_release,patch('updater.download_release',return_value=root):
        controller.check();wait(lambda:controller.staged is not None)
        check_release.assert_called_once_with('')
        assert not window.closed and window.keep_button.isEnabled(),'Downloads must not block photo review'
    controller.last_activity=time.monotonic()-31
    with patch('updater.launch_helper',return_value=base/'stage') as launch:
        window.busy=True;controller.maybe_install();assert not launch.called
        window.busy=False;controller.settings.setValue('automatic_updates',False)
        controller.maybe_install();assert not launch.called,'Disabled auto update must remain idle'
        controller.settings.setValue('automatic_updates',True)
        controller.last_activity=time.monotonic();controller.maybe_install();assert not launch.called,'Recent interaction must delay installation'
        controller.last_activity=time.monotonic()-31;controller.maybe_install();wait(lambda:window.closed)
        assert launch.call_count==1
        arguments=launch.call_args.args[2]
        assert arguments[arguments.index('--data-dir')+1]==str(lib.directory),'Restart must preserve selected library'
        assert (photos/'original.jpg').read_bytes()==original
        assert not controller.timer.isActive()
    print('PASS: asynchronous download, busy guard, idle delay, opt-out, preserved library on restart')
