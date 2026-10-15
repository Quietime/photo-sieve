"""Exercise grading cache reuse with real Qt panes and disk preview files."""
import json,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import Library
from desktop import Window,STYLE
from tools.check_ratings import FakeEngine

def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE)
    def until(test):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            application.processEvents()
            if test():return
            QTest.qWait(5)
        raise AssertionError('Grading cache did not become ready')
    checks=[];errors=[]
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);photos=base/'photos';photos.mkdir()
        for i in range(40):Image.new('RGB',(600,900) if i%2 else (900,600),(40+i*3,90,130)).save(photos/f'{i:03}.jpg')
        library=Library(base/'workspace');library.scan(photos);until(lambda:not library.job['running'])
        for row in library.list_photos('all',limit=50):library.preview_image(row['id'],4800).close()
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat)
        settings.setValue('face_preview',False);settings.setValue('preload_count',16)
        with patch('desktop.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings),patch('rating_page.QSettings',return_value=settings):
            window=Window(library,check_updates=False,data_root=base/'workspace');window.error=errors.append
            try:
                window.resize(1400,940);window.show();until(lambda:window.keep_button.isEnabled())
                page=window.rating_page;page.engine.close();page.engine=FakeEngine()
                # No original decoding is allowed once the whole-folder cache exists.
                with patch.object(library,'decode',side_effect=AssertionError('Grading reopened an original')):
                    window.toggle_rating()
                    until(lambda:len(page.images)==16 and not page.loading and all(pid in page.thumbs for pid in page.strip.visible_ids()))
                    checks.append('whole_folder_disk_previews_reused_without_decoding_originals')
                    assert set(page.images)==set(page.cache_plan()[0]);assert len(page.images)>3
                    checks.append('sixteen_nearby_large_previews_follow_preload_setting')
                    for index in range(10):
                        page.select(index)
                        assert page.pane.photo['id']==page.ids[index] and page.pane.pixmap is not None
                        assert page.panes[1].photo['id']==page.ids[index+1]
                    until(lambda:not page.loading)
                    assert len(page.images)<=page.cache_plan()[1]
                    checks.append('cached_pair_switches_synchronously_and_ram_stays_bounded')
                    window.preload_count=32;page.prefetch()
                    until(lambda:not page.loading and len(page.images)==32)
                    window.preload_count=16;page.prefetch()
                    until(lambda:not page.loading and len(page.images)==16)
                    checks.append('preload_setting_grows_and_trims_grading_ram_window')
                    page.select(0);until(lambda:not page.loading and page.ids[0] in page.images)
                    first=page.ids[0];pix=page.images[first][1];thumb=page.thumbs[first]
                    window.toggle_rating();window.toggle_rating()
                    until(lambda:page.pid()==first and page.pane.pixmap is not None and not page.loading)
                    assert page.images[first][1] is pix and page.thumbs[first] is thumb
                    checks.append('leaving_and_reentering_grading_preserves_same_folder_cache')
                    page.toggle_gallery();until(lambda:all(pid in page.thumbs for pid in page.grid.visible_ids()))
                    window.grab().save(str(output/'grading-cached-gallery.png'))
                    page.toggle_gallery();window.grab().save(str(output/'grading-cached-pair.png'))
                    checks.append('gallery_and_pair_share_prepared_thumbnail_cache')
                # Changed files must invalidate old pixels, even in the same workspace.
                before=page.images[first][1]
                Image.new('RGB',(900,600),'red').save(photos/'000.jpg')
                library.scan(photos);until(lambda:not library.job['running'])
                page.reload();until(lambda:not page.loading and first in page.images and page.images[first][1] is not before)
                assert page.images[first][1].toImage().pixelColor(10,10).red()>200
                checks.append('rescan_invalidates_changed_photo_fingerprint')
                old=page.images[first][1];window.clear_cache()
                until(lambda:not window.cache_clearing and not window.busy and first in page.images and page.images[first][1] is not old and not page.loading)
                checks.append('clear_all_cache_discards_and_rebuilds_grading_previews')
                assert not errors,errors
            finally:
                window.prompt_on_close=False;window.close();application.processEvents()
    report={'ok':True,'checks':checks};(output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report),flush=True)

if __name__=='__main__':run(sys.argv[1])
