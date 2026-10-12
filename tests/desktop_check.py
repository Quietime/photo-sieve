"""Native Qt integration checks; run with QT_QPA_PLATFORM=offscreen [--screenshot PATH]."""
import argparse
import sys
import tempfile
import time
from pathlib import Path
from PIL import Image
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton, QDialog
from unittest.mock import patch
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Library, LibraryError
from desktop import Window, Decoders, STYLE, QFileDialog
import keys
import machine

application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE)
def until(fn, seconds=15):
    stop=time.monotonic()+seconds
    while time.monotonic()<stop:
        application.processEvents()
        if fn():return
        time.sleep(.005)
    raise AssertionError('timed out')

def ready(window):
    until(lambda:window.delete_button.isEnabled())

def settle(window):
    until(lambda:not window.pending_ops and not window.inflight)

def key(window,k,modifier=Qt.KeyboardModifier.NoModifier):
    window.activateWindow();window.raise_();window.setFocus();QTest.qWait(80);QTest.keyClick(window,k,modifier)

def run(screenshot=None):
    with tempfile.TemporaryDirectory() as t:
        base=Path(t);root=base/'中文照片';root.mkdir()
        from PySide6.QtCore import QSettings
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,str(base/'preferences'))
        for i in range(80):
            Image.new('RGB',(3200,1800) if i<2 else (600,400),(i*3%255,i*7%255,i*11%255)).save(root/f'{i:04}.jpg')
        lib=Library(base/'data')
        preferences=QSettings(str(base/'test-preferences.ini'),QSettings.Format.IniFormat)
        with patch('desktop.QSettings',return_value=preferences): w=Window(lib,decoders=Decoders(2))
        w.apply_shortcuts(keys.defaults());w.show();until(lambda:w.state_data is not None and not w.fetching)
        assert w.stack.currentIndex()==2,'A library without a folder opens on the empty state'
        assert all(s.autoRepeat() for s in w.shortcuts[:4]),'Held ↑ ↓ → ← follow the keyboard repeat rate'
        w.scan(str(root));ready(w);assert len(w.queue)==80 and w.stack.currentIndex()==0
        first=w.queue[0]['id'];second=w.queue[1]['id']
        # Decoder processes finish in any order; every filmstrip entry gets its own thumbnail.
        until(lambda:w.strip.count()==80 and all(w.strip.item(i).data(Qt.ItemDataRole.UserRole) in w.thumb_cache for i in w.strip.visible_rows()))
        assert w.strip.item(15).icon().isNull() is False and w.strip.currentRow()==0
        # The ready buffer fills to every queued photo here (80 < the 100-200 target), then stays within its bound.
        assert 100<=w.ahead_target<=200 or w.ahead_target==machine.PROFILE.buffer(w.display_size.width()*w.display_size.height()*4+160*110*4)
        until(lambda:all(p['id'] in w.cache for p in w.queue[:w.ahead_target]),30)
        # Regression: small previews may be evicted even though pane previews are still ready.
        w.thumb_cache.clear()
        for i in w.strip.visible_rows(): w.strip.item(i).setIcon(w.placeholder)
        with patch.object(lib,'preview_image',side_effect=AssertionError('A cached pane must rebuild the thumbnail without decoding the original')):
            w.update_strip()
            assert all(w.strip.item(i).data(Qt.ItemDataRole.UserRole) in w.thumb_cache for i in w.strip.visible_rows())
            for i in range(150): w.store_thumbnail(10000+i,w.cache[first])
            assert len(w.thumb_cache)<=64
            assert all(w.strip.item(i).data(Qt.ItemDataRole.UserRole) in w.thumb_cache for i in w.strip.visible_rows())
        w.show_menu(); panel=w.more_panel; original_size=panel.size()
        application.processEvents(); panel_size=panel.size(); w.activateWindow(); application.processEvents()
        assert panel.isVisible() and panel.size()==panel_size,'Focus loss does not dismiss or shrink the settings sheet'
        panel.close()
        unbound={action:['',''] for action,_,_,_ in keys.ACTIONS}; w.apply_shortcuts(unbound)
        assert not w.shortcuts and w.keep_button.text()=='保留' and w.keep_button.shortcut==''
        w.apply_shortcuts(keys.defaults())
        w.preload_count=350; w.display_size=None; w.update_display_size(); assert w.ahead_target==350
        w.preload_count=0; w.display_size=None; w.update_display_size()
        assert w.windowFlags() & Qt.WindowType.FramelessWindowHint and not w.windowIcon().isNull()
        size=w.photos[0].viewport().size();shown=w.photos[0].pixmap.deviceIndependentSize()
        assert shown.width()<=size.width()+1 and shown.height()<=size.height()+1,'Previews are decoded for the pane size'
        assert w.photos[0].transform().m11()==1,'A pane-sized preview draws without resampling'
        top_levels=set(application.topLevelWidgets())
        # Click left image: in-place animated zoom, no top-level window added.
        QTest.mouseClick(w.photos[0].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(200,200))
        assert w.photos[0].detail
        until(lambda:w.photos[0].full_resolution and not w.photos[0].zoom_animation)
        assert (w.photos[0].pixmap.width(),w.photos[0].pixmap.height())==(3200,1800)
        assert abs(w.photos[0].transform().m11()-1)<1e-6,'Zoom settles at one image pixel per screen pixel'
        assert set(application.topLevelWidgets())==top_levels
        # Drag pans without toggling back; a normal click restores the fitted preview.
        QTest.mousePress(w.photos[0].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(200,200))
        QTest.mouseMove(w.photos[0].viewport(),QPoint(250,240))
        QTest.mouseRelease(w.photos[0].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(250,240))
        assert w.photos[0].detail
        QTest.mouseClick(w.photos[0].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(200,200))
        assert not w.photos[0].detail and not w.photos[0].full_resolution
        until(lambda:not w.photos[0].zoom_animation and w.photos[0].pixmap is w.cache[first])
        # Right pane works in place too; Escape only resets zoom, not the app.
        QTest.mouseClick(w.photos[1].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(200,200))
        until(lambda:w.photos[1].full_resolution)
        assert (w.photos[1].pixmap.width(),w.photos[1].pixmap.height())==(3200,1800)
        colour=w.photos[1].pixmap.toImage().pixelColor(1600,900);assert max(abs(colour.red()-3),abs(colour.green()-7),abs(colour.blue()-11))<=3,'The original keeps its colours'
        key(w,Qt.Key.Key_Escape);assert not w.photos[1].detail and w.isVisible()
        # A slow original (as with RAW) zooms immediately; changing photos discards its result.
        gate=threading.Event();started=threading.Event();preview=lib.preview_image
        def delayed(pid,edge):
            if pid==first and edge==0:started.set();gate.wait(5)
            return preview(pid,edge)
        with patch.object(lib,'preview_image',side_effect=delayed):
            QTest.mouseClick(w.photos[0].viewport(),Qt.MouseButton.LeftButton,pos=QPoint(200,200))
            assert w.photos[0].detail and not w.photos[0].full_resolution
            until(started.is_set);w.next_photo();assert w.photos[0].photo['id']==second
            gate.set();until(lambda:not w.focus_loading)
            assert w.photos[0].photo['id']==second and not w.photos[0].detail
        w.load(first-1);until(lambda:not w.fetching and w.queue and w.queue[0]['id']==first);ready(w)
        # ↓ moves on at once; the file move completes in order behind the interface.
        key(w,Qt.Key.Key_Down);assert w.queue[0]['id']==second;settle(w);ready(w)
        deleted=lib.list_photos('deleted');assert len(deleted)==1 and deleted[0]['id']==first and Path(deleted[0]['backup']).is_file()
        key(w,Qt.Key.Key_Up);assert w.queue[0]['id']>second;settle(w);ready(w)
        assert lib.photo(second)['status']==1
        assert all(w.strip.item(i).data(Qt.ItemDataRole.UserRole)!=second for i in range(w.strip.count()))
        key(w,Qt.Key.Key_Left);until(lambda:w.queue and w.queue[0]['id']==first);settle(w);ready(w)
        assert Path(lib.photo(first)['path']).is_file() and lib.photo(second)['status']==1
        assert lib.state()['counts']['deleted']==0
        # General undo reverses the keep that followed the delete.
        key(w,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);until(lambda:lib.photo(second)['status']==0 and w.queue[0]['id']==second);settle(w);ready(w)
        key(w,Qt.Key.Key_Down);settle(w);w.switch('deleted');until(lambda:not w.fetching and len(w.deleted_rows)==1)
        assert w.deleted_rows[0]['id']==second and w.deleted_grid.count()==1
        w.restore(photo_id=second);until(lambda:not w.deleted_rows and w.deleted_grid.count()==0);settle(w)
        w.switch('pending');ready(w)
        # Keep view available, but kept entries never enter the filmstrip.
        # Keys follow the user's settings: here P keeps, and the button shows it.
        chosen=keys.defaults();chosen['keep']=['P',''];w.apply_shortcuts(chosen);assert w.keep_button.text()=='保留' and w.keep_button.shortcut=='P'
        kept=w.queue[0]['id'];key(w,Qt.Key.Key_P);settle(w);assert lib.photo(kept)['status']==1,'A custom key keeps the photo'
        w.apply_shortcuts(keys.defaults());assert w.keep_button.text()=='保留' and w.keep_button.shortcut=='↑'
        w.switch('keep');ready(w);assert w.strip.count()==0
        w.switch('pending');ready(w)
        # A failed file move puts the photo back in front and reports why.
        # Mid-burst the delete toast waits 80 ms; the error that arrives first must not be covered by it.
        current=w.queue[0]['id'];w.last_motion=time.monotonic()+.1
        with patch.object(lib,'trash',side_effect=LibraryError(409,'模拟：硬盘已断开')):
            key(w,Qt.Key.Key_Down);assert w.queue[0]['id']!=current
            until(lambda:w.queue[0]['id']==current and not w.pending_ops)
        QTest.qWait(200)
        assert w.toast.isVisible() and '硬盘' in w.toast.message and lib.photo(current)['status']==0
        ready(w)
        # Export through the native sheet (no HTTP/browser).
        copy=base/'输出副本';copy.mkdir();move=base/'移动照片';move.mkdir()
        def do_export(destination,label):
            from PySide6.QtCore import QTimer
            def click():
                dialog=application.activeModalWidget();assert isinstance(dialog,QDialog)
                choose=next(b for b in dialog.findChildren(QPushButton) if b.text()=='选择…')
                with patch.object(QFileDialog,'getExistingDirectory',return_value=str(destination)):choose.click()
                next(b for b in dialog.findChildren(QPushButton) if b.text()==label).click()
            QTimer.singleShot(100,click);w.export_dialog();until(lambda:not lib.job['running'] and not w.timer.isActive())
        do_export(copy,'复制');assert len(list(copy.glob('*.jpg')))==1
        do_export(move,'移动');assert len(list(move.glob('*.jpg')))==1;assert lib.state()['counts']['keep']==0
        # Rapid presses: every accepted press advances at once; records land in order.
        w.load();ready(w);start=time.monotonic();expected=[]
        for _ in range(45):
            ready(w);pid=w.queue[0]['id'];expected.append(pid);w.act('delete')
            assert not w.queue or w.queue[0]['id']!=pid
        print(f'Native Qt: 45 synthetic deletes accepted in {time.monotonic()-start:.2f}s')
        settle(w);w.poll();until(lambda:w.state_data['counts']['deleted']==45)
        assert sorted(p['id'] for p in lib.list_photos('deleted',limit=100))==sorted(expected)
        assert len(w.queue)>0;assert len(w.cache)<=w.cache_limit;assert len(w.thumb_cache)<=64
        until(lambda:all(w.strip.item(i).data(Qt.ItemDataRole.UserRole) in w.thumb_cache for i in w.strip.visible_rows()))
        if screenshot:QTest.qWait(400);w.grab().save(str(screenshot))
        # Dropping a folder onto the window opens it.
        other=base/'other-photos';other.mkdir()
        for i in range(3):Image.new('RGB',(600,400),'green').save(other/f'{i}.jpg')
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(other))])
        with patch.object(w,'scan') as scan:
            w.dropEvent(QDropEvent(QPointF(20,20),Qt.DropAction.CopyAction,mime,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier))
            scan.assert_called_once_with(str(other))
        with patch('desktop.Library',side_effect=lambda directory,*args:Library(base/'other-data')),patch('desktop.QSettings'):
            w.scan(str(other));until(lambda:w.library is not lib and not w.library.job['running'] and not w.fetching and len(w.queue)==3)
        assert lib.state()['counts']['deleted']==45 and w.library.state()['counts']['pending']==3
        w.resize(1280,720);application.processEvents();assert w.width()<=1280 and w.height()<=720
        # Frame meter, GPU compositing preference and the machine line in the ⋯ menu.
        w.show_fps(True);w.frames=0;w.photos[0].viewport().update();QTest.qWait(1100);assert w.fps_label.text().endswith('帧/秒')
        w.show_fps(False);assert w.fps_label is None
        from PySide6.QtCore import QSettings
        with patch('desktop.QSettings',return_value=QSettings(str(base/'prefs.ini'),QSettings.Format.IniFormat)):
            w.set_gpu(False);assert not QSettings(str(base/'prefs.ini'),QSettings.Format.IniFormat).value('gpu_compositing',True,type=bool)
        assert '解码' in w.machine_status() and '预读' in w.machine_status()
        w.close();application.processEvents()
        reopened=Library(base/'data');assert reopened.state()['counts']['deleted']==45
        restored=reopened.undo(only_delete=True);assert restored['restored']
        reopened_window=Window(reopened);reopened_window.show();ready(reopened_window);assert reopened_window.stack.currentIndex()==0
        assert reopened.state()['counts']['deleted']==44;reopened_window.close()
        blank=Window(reopened,check_updates=False,start_empty=True); blank.show()
        until(lambda:blank.state_data is not None)
        assert blank.stack.currentIndex()==2 and not blank.queue and not blank.loading
        assert not any(pane.pixmap for pane in blank.photos),'Startup leaves all photos hidden until a folder is chosen'
        blank.close()
        print('Native checks passed: animated zoom, arrows, held-key flow, failure rollback, persistent deletion, restore, kept strip, copy/move, drop, bounded cache.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--screenshot',type=Path);run(parser.parse_args().screenshot)
