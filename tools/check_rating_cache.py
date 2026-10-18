"""Exercise grading cache reuse with real Qt panes and disk preview files."""
import io,json,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QSettings,Qt,QPoint
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
        raise AssertionError('Grading cache did not become ready: '+str(dict(scroll=(page.grid.verticalScrollBar().value(),page.grid.verticalScrollBar().maximum()),visible=page.grid.visible_ids(),loading=page.loading,errors=errors,camera=page.camera_info.text(),face=page.face_panel.result,enabled=window.faces.enabled,face_loading=window.faces._cache_loading,results=len(window.faces._results),attempted=len(window.faces._cache_attempted))))
    checks=[];errors=[]
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);photos=base/'photos';photos.mkdir()
        for i in range(40):
            exif=Image.Exif()
            for tag,value in {271:'NIKON CORPORATION',272:'NIKON D800E',42036:'24.0-70.0 mm f/2.8',33437:2.8,33434:.005,34855:250+i,37386:70,36867:'2024:06:29 15:45:08'}.items():exif[tag]=value
            Image.new('RGB',(600,900) if i%2 else (900,600),(40+i*3,90,130)).save(photos/f'{i:03}.jpg',exif=exif)
        library=Library(base/'workspace');library.scan(photos);until(lambda:not library.job['running'])
        for row in library.list_photos('all',limit=50):library.preview_image(row['id'],4800).close()
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat)
        settings.setValue('face_preview',False);settings.setValue('preload_count',16)
        with patch('desktop.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings),patch('rating_page.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings):
            window=Window(library,check_updates=False,data_root=base/'workspace');window.error=errors.append
            try:
                window.resize(1400,940);window.show();until(lambda:window.keep_button.isEnabled())
                page=window.rating_page;page.engine.close();page.engine=FakeEngine()
                # No original decoding is allowed once the whole-folder cache exists.
                with patch.object(library,'decode',side_effect=AssertionError('Grading reopened an original')):
                    window.toggle_rating()
                    until(lambda:len(page.images)==16 and not page.listing and not page.loading and all(pid in page.thumbs for pid in page.grid.visible_ids()))
                    checks.append('whole_folder_disk_previews_reused_without_decoding_originals')
                    assert set(page.images)==set(page.cache_plan()[0]);assert len(page.images)>3
                    checks.append('sixteen_nearby_large_previews_follow_preload_setting')
                    for index in range(10):
                        page.select(index)
                        assert page.pane.photo['id']==page.ids[index] and page.pane.pixmap is not None
                    until(lambda:not page.listing and not page.loading)
                    assert len(page.images)<=page.cache_plan()[1]
                    checks.append('cached_preview_switches_synchronously_and_ram_stays_bounded')
                    window.preload_count=32;page.prefetch()
                    until(lambda:not page.listing and not page.loading and len(page.images)==32)
                    window.preload_count=16;page.prefetch()
                    until(lambda:not page.listing and not page.loading and len(page.images)==16)
                    checks.append('preload_setting_grows_and_trims_grading_ram_window')
                    page.select(0);until(lambda:not page.listing and not page.loading and page.ids[0] in page.images)
                    first=page.ids[0];pix=page.images[first][1];thumb=page.thumbs[first]
                    window.toggle_rating();window.toggle_rating()
                    until(lambda:page.pid()==first and page.pane.pixmap is not None and not page.listing and not page.loading)
                    assert page.images[first][1] is pix and page.thumbs[first] is thumb
                    assert page.grid.captions[first].split('\n')[0]=='000.jpg'
                    checks.append('leaving_and_reentering_grading_preserves_same_folder_cache')
                    until(lambda:all(pid in page.thumbs for pid in page.grid.visible_ids()))
                    window.grab().save(str(output/'grading-cached-gallery.png'))
                    window.grab().save(str(output/'grading-cached-preview.png'))
                    checks.append('gallery_and_preview_share_prepared_thumbnail_cache')
                # Verify the grading face viewer hydrates only durable results.
                from face_service import SCHEMA,cache_key
                from face_engine import ENGINE_VERSION
                window.full_faces.stop();window.faces.enabled=False;window.faces.ahead=16
                crops={}
                with library.connect() as db:
                    db.execute(SCHEMA)
                    for photo in library.list_photos('all',limit=50):
                        data=io.BytesIO();Image.new('RGB',(80,100),(photo['id']*5,120,160)).save(data,'PNG');payload=data.getvalue();crops[photo['id']]=payload
                        metadata=dict(faces=[{}],face_count=1,output_size=[80,100],quality='original')
                        db.execute('INSERT OR REPLACE INTO face_metadata VALUES (?,?,?,?,?,?,?)',(cache_key(photo),photo['path'],photo['size'],photo['mtime'],ENGINE_VERSION,'[{}]',json.dumps(metadata)))
                for photo in library.list_photos('all',limit=50):window.faces._save_crop(library,photo,crops[photo['id']])
                window.faces.enabled=True;window.faces.clear_memory()
                with patch.object(window.faces,'_isolated',side_effect=AssertionError('No grading click recognition')):
                    page.show_aux();until(lambda:page.face_panel.result is not None and page.face_panel.result['photo_id']==page.pid() and 'ISO 250' in page.camera_info.text())
                    current=page.pid();assert page.face_panel.isVisible() and page.face_panel.result['imagebytes']==crops[current],(page.face_panel.isVisible(),page.face_panel.result)
                    assert 'NIKON D800E' in page.camera_info.text() and '24.0-70.0 mm' in page.camera_info.text() and '2024-06-29' in page.camera_info.text()
                    # The face and camera card floats in the preview's top-right corner instead of taking a row.
                    from PySide6.QtCore import QRect
                    pane=QRect(page.pane.mapTo(page,page.pane.rect().topLeft()),page.pane.size());card=QRect(page.aux.mapTo(page,page.aux.rect().topLeft()),page.aux.size())
                    assert page.aux.isVisible() and pane.contains(card) and pane.right()-card.right()<=12,(pane,card)
                    page.select(1);until(lambda:page.face_panel.result is not None and page.face_panel.result['photo_id']==page.pid() and 'ISO 251' in page.camera_info.text())
                    assert page.face_panel.result['imagebytes']==crops[page.pid()]
                    page.select(0);until(lambda:page.face_panel.result is not None and page.face_panel.result['photo_id']==first and 'ISO 250' in page.camera_info.text())
                window.grab().save(str(output/'grading-face-camera-below-preview.png'))
                checks.append('rounded_face_and_camera_info_below_preview_follow_selection_using_cache')
                window.faces.enabled=False;page.show_aux()
                assert len(page.panes)==1 and page.grid.isVisible() and page.pane.isVisible()
                assert page.grid.x()<page.pane.mapTo(page,page.pane.rect().topLeft()).x()
                checks.append('simultaneous_left_gallery_and_right_single_preview')
                handle=page.splitter.handle(1);before_sizes=page.splitter.sizes();point=handle.rect().center()
                QTest.mousePress(handle,Qt.MouseButton.LeftButton,pos=point)
                QTest.mouseMove(handle,point-QPoint(120,0),40)
                QTest.mouseRelease(handle,Qt.MouseButton.LeftButton,pos=point-QPoint(120,0))
                until(lambda:abs(page.splitter.sizes()[0]-before_sizes[0])>40)
                until(lambda:not page.listing and not page.loading and not page.resize_timer.isActive())
                after=page.splitter.sizes();assert after[0]<before_sizes[0] and after[1]>before_sizes[1],(before_sizes,after)
                ratio=settings.value('rating_split_ratio',0,type=float);assert abs(ratio-after[0]/sum(after))<.01
                window.toggle_rating();window.toggle_rating();until(lambda:not page.listing and not page.loading and page.pid()==first)
                assert abs(page.splitter.sizes()[0]/sum(page.splitter.sizes())-ratio)<.02
                window.grab().save(str(output/'grading-dragged-divider.png'))
                checks.append('mouse_drag_resizes_both_panes_and_remembers_ratio')
                bar=page.grid.verticalScrollBar();assert bar.maximum()>0
                bar.setValue(bar.maximum());until(lambda:page.ids[-1] in page.grid.visible_ids() and all(pid in page.thumbs for pid in page.grid.visible_ids()))
                assert page.pid()==first and page.pane.photo['id']==first
                last=page.grid.item(len(page.ids)-1)
                QTest.mouseClick(page.grid.viewport(),Qt.MouseButton.LeftButton,pos=page.grid.visualItemRect(last).center())
                until(lambda:page.pid()==page.ids[-1] and page.pane.photo['id']==page.ids[-1] and page.pane.pixmap is not None)
                window.grab().save(str(output/'grading-scrolled-last-photo.png'))
                checks.append('vertical_gallery_reaches_last_photo_and_click_updates_right_preview')
                page.select(0);until(lambda:not page.listing and not page.loading and first in page.images)
                # Changed files must invalidate old pixels, even in the same workspace.
                before=page.images[first][1]
                Image.new('RGB',(900,600),'red').save(photos/'000.jpg')
                library.scan(photos);until(lambda:not library.job['running'])
                page.reload();until(lambda:not page.listing and not page.loading and first in page.images and page.images[first][1] is not before)
                assert page.images[first][1].toImage().pixelColor(10,10).red()>200
                checks.append('rescan_invalidates_changed_photo_fingerprint')
                old=page.images[first][1];window.clear_cache()
                until(lambda:not window.cache_clearing and not window.busy and first in page.images and page.images[first][1] is not old and not page.listing and not page.loading)
                checks.append('clear_all_cache_discards_and_rebuilds_grading_previews')
                assert not errors,errors
            finally:
                window.prompt_on_close=False;window.close();application.processEvents()
    report={'ok':True,'checks':checks};(output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report),flush=True)

if __name__=='__main__':run(sys.argv[1])
