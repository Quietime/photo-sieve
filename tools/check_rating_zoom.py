"""Real wheel/button/slider events and immediate splitter-fit behavior."""
import sys,tempfile,time,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QSettings,QPoint,QPointF,Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from app import Library
from desktop import Window,STYLE
from tools.check_ratings import FakeEngine

def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setStyleSheet(STYLE);checks=[];errors=[]
    def until(fn):
        end=time.monotonic()+30
        while time.monotonic()<end:
            app.processEvents()
            if fn():return
            QTest.qWait(5)
        raise AssertionError('Grading zoom condition did not finish')
    def wheel(widget,delta,ctrl=False,point=None):
        point=point or widget.rect().center()
        event=QWheelEvent(QPointF(point),QPointF(widget.mapToGlobal(point)),QPoint(),QPoint(0,delta),Qt.MouseButton.NoButton,Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        app.sendEvent(widget,event)
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);photos=base/'photos';photos.mkdir()
        for i in range(48):Image.new('RGB',(1800,600) if i==0 else (600,900) if i%2 else (900,600),(30+i*3,90,130)).save(photos/f'{i:03}.jpg')
        lib=Library(base/'work');lib.scan(photos);until(lambda:not lib.job['running'])
        for photo in lib.list_photos('all',limit=60):lib.preview_image(photo['id'],4800).close()
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat);settings.setValue('preload_count',16);settings.setValue('face_preview',False)
        with patch('desktop.QSettings',return_value=settings),patch('rating_page.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            w=Window(lib,check_updates=False,data_root=base/'work');w.error=errors.append;w.resize(1440,1000);w.show()
            try:
                until(lambda:w.keep_button.isEnabled());p=w.rating_page;p.engine.close();p.engine=FakeEngine();w.toggle_rating()
                until(lambda:not p.listing and not p.loading and p.pane.pixmap is not None)
                current=p.pid();size=p.grid.tile_width
                p.gallery_larger.click();assert p.grid.tile_width==size+20
                p.gallery_smaller.click();assert p.grid.tile_width==size
                p.gallery_size.setValue(300);assert p.grid.tile_width==300 and p.grid.iconSize().width()==280
                assert settings.value('rating_gallery_size',0,type=int)==300 and p.pid()==current
                checks.append('buttons_slider_resize_gallery_and_persist_without_changing_selection')
                until(lambda:not p.loading and all(p.thumb_sizes.get(pid)==p.grid.iconSize()*p.devicePixelRatioF() for pid in p.grid.visible_ids()))
                with patch.object(lib,'decode',side_effect=AssertionError('Zoom must reuse disk cache')):
                    p.gallery_size.setValue(340)
                    until(lambda:not p.loading and all(p.thumb_sizes.get(pid)==p.grid.iconSize()*p.devicePixelRatioF() for pid in p.grid.visible_ids()))
                assert p.pid()==current;checks.append('larger_thumbnails_refresh_at_physical_resolution_from_disk_cache')
                p.gallery_size.setValue(220);until(lambda:not p.loading)
                bar=p.grid.verticalScrollBar();bar.setValue(bar.maximum()//2);app.processEvents()
                point=p.grid.viewport().rect().center();anchor=p.grid.indexAt(point);assert anchor.isValid()
                pid=p.grid.photo_model.ids[anchor.row()]
                wheel(p.grid.viewport(),120,True,point);assert p.grid.tile_width==240
                assert p.grid.photo_model.ids[p.grid.indexAt(point).row()]==pid
                wheel(p.grid.viewport(),-120,True,point);assert p.grid.tile_width==220 and p.pid()==current
                checks.append('ctrl_wheel_in_gallery_scales_both_directions_and_keeps_cursor_photo')
                before=bar.value();wheel(p.grid.viewport(),-120);until(lambda:bar.value()>before)
                assert p.grid.tile_width==220
                wheel(p.pane.viewport(),-120);assert p.grid.tile_width==220
                checks.append('ordinary_wheel_scrolls_and_right_preview_does_not_resize_gallery')
                p.gallery_size.setValue(360);wheel(p.grid.viewport(),120,True);assert p.grid.tile_width==360
                p.gallery_size.setValue(120);wheel(p.grid.viewport(),-120,True);assert p.grid.tile_width==120
                p.gallery_size.setValue(220);p.select(0);until(lambda:not p.loading and not p.resize_timer.isActive())
                checks.append('gallery_size_limits_are_enforced')
                before=p.pane.content_rect().width();original=p.pane.pixmap
                handle=p.splitter.handle(1);point=handle.rect().center()
                QTest.mousePress(handle,Qt.MouseButton.LeftButton,pos=point);QTest.mouseMove(handle,point-QPoint(100,0),40);QTest.mouseRelease(handle,Qt.MouseButton.LeftButton,pos=point-QPoint(100,0))
                app.processEvents()
                assert p.pane.content_rect().width()>before+40
                assert abs(p.pane.content_rect().width()-p.pane.viewport().width())<8
                until(lambda:not p.loading and not p.resize_timer.isActive())
                assert p.image_sizes[current]==p.preview_size()
                w.grab().save(str(output/'grading-larger-preview.png'))
                before=p.pane.content_rect().width();p.splitter.moveSplitter(p.splitter.sizes()[0]+160,1);app.processEvents()
                assert p.pane.content_rect().width()<before-60
                assert abs(p.pane.content_rect().width()-p.pane.viewport().width())<8
                checks.append('divider_immediately_grows_and_shrinks_preview_then_refreshes_sharp_cache')
                p.gallery_size.setValue(300);until(lambda:not p.loading);w.grab().save(str(output/'grading-large-thumbnails.png'))
                w.toggle_rating();w.toggle_rating();until(lambda:not p.listing and not p.loading)
                assert p.grid.tile_width==300 and settings.value('rating_gallery_size',0,type=int)==300
                checks.append('gallery_size_survives_mode_switch')
                assert not errors,errors
            finally:w.prompt_on_close=False;w.close();app.processEvents()
    report=dict(ok=True,checks=checks);(output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report),flush=True)

if __name__=='__main__':run(sys.argv[1])
