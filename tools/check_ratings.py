"""Exercise independent grading UI, actual Windows handlers, and mixed-orientation filmstrip."""
import os,sys,time,json,tempfile,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from PIL import Image
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QSettings
from PySide6.QtTest import QTest
from app import Library
from desktop import Window,STYLE
from native_rating import VALUES

class FakeEngine:
    """Linux UI harness only. Windows uses the real isolated property-store engine."""
    def __init__(self):self.values={}
    def run(self,path,stars=None,expected=None):
        path=str(path);before=self.values.get(path,0)
        if expected is not None and expected!=before:raise ValueError('external change')
        if stars is not None:self.values[path]=stars
        value=self.values.get(path,0);return dict(stars=value,value=VALUES[value],before=before,writable=True,reason='')
    def close(self):pass

def main():
    output=Path(sys.argv[1] if len(sys.argv)>1 else 'ratings-ui-ci');output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setStyleSheet(STYLE)
    def until(fn):
        end=time.monotonic()+45
        while time.monotonic()<end:
            app.processEvents()
            if fn():return
            time.sleep(.01)
        raise TimeoutError('rating UI condition')
    report={'native_windows':os.name=='nt','checks':[]}
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp);photos=base/'mixed';photos.mkdir()
        for i in range(20):Image.new('RGB',(600,900) if i<5 else (900,600),(40+i*5,100,160)).save(photos/f'{i:03}.jpg')
        lib=Library(base/'workspace');lib.scan(photos);until(lambda:not lib.job['running'])
        for photo in lib.list_photos(limit=4):lib.mark(photo['id'],1)
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat);settings.setValue('warm_count',0);settings.setValue('preload_count',16)
        with patch('desktop.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            window=Window(lib,check_updates=False,data_root=base/'workspace');window.resize(1480,960);window.show()
            try:
                until(lambda:window.strip.count()==16 and window.keep_button.isEnabled());window.show();QTest.qWait(250)
                until(lambda:all(pid in window.thumb_cache for pid in window.strip.visible_ids()))
                until(lambda:not window.strip.layout_timer.isActive() and all(window.thumbnail(pid) is not None for pid in window.strip.visible_ids()))
                window.grab().save(str(output/'mixed-filmstrip.png'))
                for pid in window.strip.visible_ids():
                    rect=window.strip.visualRect(window.strip.photo_model.index(window.strip.index_of(pid)));pix=window.thumbnail(pid)
                    assert rect.width()>=round(68*pix.width()/pix.height()),(rect,pix.size())
                report['checks'].append('mixed_landscape_cells_fit_full_width')
                page=window.rating_page
                if os.name!='nt':page.engine=FakeEngine()
                window.toggle_rating();until(lambda:page.pid() is not None and page.star_buttons[0].isEnabled() and page.pane.pixmap is not None)
                assert all(not key.isEnabled() for key in window.shortcuts);assert window.stack.currentIndex()==5
                page.auto.setChecked(False);first=page.pid();path=Path(lib.photo(first)['path']);pixels=hashlib.sha256(Image.open(path).tobytes()).hexdigest()
                window.activateWindow();window.more_button.setFocus();app.processEvents();QTest.qWait(150);QTest.keyClick(window.more_button,Qt.Key.Key_4);until(lambda:not page.writing and page.info[first]['stars']==4)
                if os.name=='nt':assert page.engine.run(path)['stars']==4
                assert hashlib.sha256(Image.open(path).tobytes()).hexdigest()==pixels
                assert lib.photo(first)['status']==1
                report['checks'].append('rating_digit_shortcut_writes_native_stars_preserving_pixels_and_keep')
                until(lambda:all(pane.pixmap is not None for pane in page.panes));QTest.qWait(150);window.grab().save(str(output/'rating-all.png'))
                assert len(page.panes)==2 and page.panes[1].photo['id']==page.ids[1]
                page.toggle_gallery();until(lambda:all(pid in page.thumbs for pid in page.grid.visible_ids()));window.grab().save(str(output/'rating-gallery.png'));page.toggle_gallery()
                assert page.star_buttons[3].shortcut=='4'
                report['checks'].append('dual_photo_layout_gallery_and_visible_shortcut_badges')
                page.undo();until(lambda:not page.writing and page.info[first]['stars']==0)
                report['checks'].append('rating_undo_restores_original_system_value')
                page.auto.setChecked(True);page.grade(0);assert page.index==1;page.auto.setChecked(False);page.select(0)
                report['checks'].append('confirm_same_rating_advances_without_rewriting_metadata')
                page.scope.setCurrentIndex(1);until(lambda:len(page.ids)==4 and page.star_buttons[0].isEnabled());QTest.qWait(250);window.grab().save(str(output/'rating-kept.png'))
                page.grade(5);until(lambda:not page.writing and page.info[page.pid()]['stars']==5)
                page.filter.setCurrentIndex(6);until(lambda:len(page.ids)==1 and page.pane.pixmap is not None);window.grab().save(str(output/'rating-filter.png'))
                report['checks'].append('kept_scope_and_native_star_filter')
                page.zoom();until(lambda:page.pane.full_resolution);assert page.pane.detail and page.pane.pixmap.devicePixelRatioF()==window.devicePixelRatioF();window.grab().save(str(output/'rating-focus.png'));page.unzoom();QTest.qWait(400)
                window.show_menu();app.processEvents();assert all(not key.isEnabled() for key in page.shortcut_objects);window.more_panel.hide();app.processEvents();assert all(key.isEnabled() for key in page.shortcut_objects);assert all(not key.isEnabled() for key in window.shortcuts)
                report['checks'].append('mode_shortcuts_stay_isolated_after_menu')
                from rating_page import ACTIONS
                import keys
                dialog=keys.ShortcutDialog(window,page.chosen,ACTIONS,'照片分级快捷键');dialog.show();QTest.qWait(250);dialog.grab().save(str(output/'rating-shortcuts.png'));dialog.close()
                window.toggle_rating();assert all(key.isEnabled() for key in window.shortcuts);assert all(not key.isEnabled() for key in page.shortcut_objects)
                report['checks'].append('return_to_culling_without_rating_key_conflicts')
                window.toggle_rating();until(lambda:len(page.ids)==1 and page.star_buttons[0].isEnabled())
                lib.export_kept(base/'export','move');window.poll();until(lambda:not lib.job['running'] and len(page.ids)==0)
                assert not page.star_buttons[0].isEnabled() and not page.undo_button.isEnabled()
                if os.name=='nt':assert page.engine.run(base/'export'/'000.jpg')['stars']==5
                report['checks'].append('move_export_preserves_native_stars_and_refreshes_grading_inventory')
            finally:window.prompt_on_close=False;window.close();app.processEvents()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
