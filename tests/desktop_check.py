"""Native Qt integration checks; run with QT_QPA_PLATFORM=offscreen."""
import sys
import tempfile
import time
import threading
from pathlib import Path
from PIL import Image
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton, QDialog
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Library
from desktop import Window, STYLE

application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE)
def until(fn, seconds=15):
    stop=time.monotonic()+seconds
    while time.monotonic()<stop:
        application.processEvents()
        if fn():return
        time.sleep(.005)
    raise AssertionError('timed out')

def ready(window):
    until(lambda:window.keep_button.isEnabled())

def key(window,k,modifier=Qt.KeyboardModifier.NoModifier):
    window.activateWindow();window.raise_();window.setFocus();QTest.qWait(80);QTest.keyClick(window,k,modifier)

def run():
    with tempfile.TemporaryDirectory() as t:
        base=Path(t);root=base/'中文照片';root.mkdir()
        for i in range(80):
            Image.new('RGB',(3200,1800) if i<2 else (600,400),(i*3%255,i*7%255,i*11%255)).save(root/f'{i:04}.jpg')
        lib=Library(base/'data')
        w=Window(lib);w.show();until(lambda:w.state_data is not None and not w.fetching);assert w.folder.text()==''
        w.folder.setText(str(root));w.scan();ready(w);assert len(w.queue)==40
        first=w.queue[0]['id'];second=w.queue[1]['id']
        assert w.folder.text()==str(root)
        until(lambda:len(w.thumb_cache)>=16)
        assert all(w.strip.item(i).data(Qt.ItemDataRole.UserRole) in w.thumb_cache for i in range(w.strip.count()))
        assert w.strip.item(15).icon().isNull() is False
        top_levels=set(application.topLevelWidgets())
        # Click left image: immediate in-place zoom, no top-level window added.
        QTest.mouseClick(w.photos[0].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(200,200))
        assert w.photos[0].detail
        assert w.photos[0].transform().m11()>=.99
        until(lambda:w.photos[0].full_resolution)
        assert (w.photos[0].pixmap.width(),w.photos[0].pixmap.height())==(3200,1800)
        assert set(application.topLevelWidgets())==top_levels
        # Drag pans without toggling back; a normal click restores the fitted preview.
        QTest.mousePress(w.photos[0].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(200,200))
        QTest.mouseMove(w.photos[0].viewport(),QPoint(250,240))
        QTest.mouseRelease(w.photos[0].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(250,240))
        assert w.photos[0].detail
        QTest.mouseClick(w.photos[0].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(200,200))
        assert not w.photos[0].detail and not w.photos[0].full_resolution
        # Right pane works in place too; Escape only resets zoom, not the app.
        QTest.mouseClick(w.photos[1].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(200,200))
        until(lambda:w.photos[1].full_resolution)
        assert (w.photos[1].pixmap.width(),w.photos[1].pixmap.height())==(3200,1800)
        key(w,Qt.Key.Key_Escape);assert not w.photos[1].detail and w.isVisible()
        # A slow original (as with RAW) zooms immediately; changing photos discards its result.
        gate=threading.Event();started=threading.Event();preview=lib.preview
        def delayed(pid,edge):
            if pid==first and edge==0:started.set();gate.wait(5)
            return preview(pid,edge)
        with patch.object(lib,'preview',side_effect=delayed):
            QTest.mouseClick(w.photos[0].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(200,200))
            assert w.photos[0].detail and not w.photos[0].full_resolution
            until(started.is_set);w.next_photo();assert w.photos[0].photo['id']==second
            gate.set();until(lambda:not w.focus_loading)
            assert w.photos[0].photo['id']==second and not w.photos[0].detail
        w.previous_photo();until(lambda:not w.fetching and w.queue[0]['id']==first);ready(w)
        key(w,Qt.Key.Key_Down);until(lambda:not w.busy and w.queue and w.queue[0]['id']==second);ready(w)
        deleted=lib.list_photos('deleted');assert len(deleted)==1 and deleted[0]['id']==first and Path(deleted[0]['backup']).is_file()
        key(w,Qt.Key.Key_Up);until(lambda:not w.busy and w.queue and w.queue[0]['id']>second);ready(w)
        assert lib.photo(second)['status']==1
        assert all(w.strip.item(i).data(Qt.ItemDataRole.UserRole)!=second for i in range(w.strip.count()))
        key(w,Qt.Key.Key_Left);until(lambda:not w.busy and w.queue and w.queue[0]['id']==first);ready(w)
        assert Path(lib.photo(first)['path']).is_file() and lib.photo(second)['status']==1
        assert lib.state()['counts']['deleted']==0
        # General undo reverses the keep that followed the delete.
        key(w,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);until(lambda:not w.busy and lib.photo(second)['status']==0);ready(w)
        key(w,Qt.Key.Key_Down);until(lambda:not w.busy);w.switch('deleted');until(lambda:not w.fetching and len(w.deleted_rows)==1)
        assert w.deleted_rows[0]['id']==second
        w.restore(photo_id=second);until(lambda:not w.busy and not w.fetching and not w.deleted_rows)
        w.switch('pending');ready(w)
        # Keep view available, but kept entries never enter the filmstrip.
        key(w,Qt.Key.Key_Up);until(lambda:not w.busy);w.switch('keep');ready(w);assert w.strip.count()==0
        w.switch('pending');ready(w)
        # Export through native modal using Qt controls (no HTTP/browser).
        copy=base/'输出副本';copy.mkdir();move=base/'移动照片';move.mkdir()
        def do_export(destination,label):
            from PySide6.QtCore import QTimer
            def click():
                dialog=application.activeModalWidget();assert isinstance(dialog,QDialog)
                choose=next(b for b in dialog.findChildren(QPushButton) if b.text()=='选择目录…')
                with patch.object(QFileDialog,'getExistingDirectory',return_value=str(destination)):choose.click()
                next(b for b in dialog.findChildren(QPushButton) if b.text()==label).click()
            QTimer.singleShot(100,click);w.export_dialog();until(lambda:not lib.job['running'])
        do_export(copy,'复制全部保留照片');assert len(list(copy.glob('*.jpg')))==1
        do_export(move,'移动全部保留照片');assert len(list(move.glob('*.jpg')))==1;assert lib.state()['counts']['keep']==0
        # Repeat culling crosses buffer boundary without loss; scan ongoing jobs blocks actions.
        w.load();ready(w);start=time.monotonic()
        for _ in range(45):
            ready(w);pid=w.queue[0]['id'];key(w,Qt.Key.Key_Down)
            until(lambda:not w.busy and (not w.queue or w.queue[0]['id']!=pid))
        print(f'Native Qt: 45 synthetic deletes in {time.monotonic()-start:.2f}s')
        until(lambda:not w.fetching);w.poll();until(lambda:w.state_data['counts']['deleted']==45)
        assert len(w.queue)>0;assert len(w.cache)<=16;assert len(w.thumb_cache)<=64
        until(lambda:all(w.strip.item(i).data(Qt.ItemDataRole.UserRole) in w.thumb_cache for i in range(w.strip.count())))
        w.grab().save('/workspace/photo-sieve-desktop-preview.png')
        other=base/'other-photos';other.mkdir()
        for i in range(3):Image.new('RGB',(600,400),'green').save(other/f'{i}.jpg')
        w.folder.setText(str(other))
        with patch('desktop.Library',side_effect=lambda directory:Library(base/'other-data')),patch('desktop.QSettings'):
            w.scan();until(lambda:w.library is not lib and not w.library.job['running'] and not w.fetching and len(w.queue)==3)
        assert lib.state()['counts']['deleted']==45 and w.library.state()['counts']['pending']==3
        w.resize(1280,720);application.processEvents();assert w.width()<=1280 and w.height()<=720
        w.close();application.processEvents()
        reopened=Library(base/'data');assert reopened.state()['counts']['deleted']==45
        restored=reopened.undo(only_delete=True);assert restored['restored']
        reopened_window=Window(reopened);reopened_window.show();ready(reopened_window);assert reopened_window.folder.text()==''
        assert reopened.state()['counts']['deleted']==44;reopened_window.close()
        print('Native checks passed: click-to-focus, arrows, persistent deletion, restore, kept strip, native copy/move, bounded cache.')

if __name__=='__main__':run()
