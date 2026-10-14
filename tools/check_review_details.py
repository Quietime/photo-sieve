"""Only check the changed review header, passive face image and current-photo parameters."""
import io,json,sys,tempfile,time,shutil
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from PySide6.QtCore import QSettings,QPoint,QPointF,Qt,QSize
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import Library
from desktop import Window,STYLE,Decoders

def run(output,raw_fixture=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE)
    checks=[]
    def check(name,result):assert result,name;checks.append(name)
    def until(test):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            application.processEvents()
            if test():return
            QTest.qWait(10)
        raise AssertionError('Review header did not update')
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);photos=base/'photos';photos.mkdir()
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat)
        settings.setValue('face_preview',False);settings.setValue('warm_count',0);settings.setValue('last_folder',str(photos))
        for i in range(3):
            image=Image.new('RGB',(600,900) if i==0 else (900,600),['#687c93','#487864','#7d596a'][i])
            ImageDraw.Draw(image).text((40,40),'CURRENT' if i==0 else 'COMPARE',fill='white',font_size=35)
            exif=Image.Exif();exif[34665]={33437:2.8+i,33434:1/(100+i*100),34855:100+i*100,36867:f'2026:10:14 12:0{i}:30',37386:85+i,42036:'FE 85mm F1.8'};exif[271]='Sony';exif[272]='ILCE-7M4'
            image.save(photos/f'{i}.jpg',exif=exif)
        with patch('desktop.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            window=Window(Library(base/'workspace'),check_updates=False,start_empty=True)
            try:
                window.faces.timer.stop();window.resize(1280,900);window.show();application.processEvents()
                check('startup_has_resume_and_no_work_controls',window.resume_button.isVisible() and not window.rating_button.isVisible() and not window.segments.isVisible() and not hasattr(window,'export_button'))
                window.grab().save(str(output/'startup.png'));window.show_menu()
                check('startup_menu_has_no_folder_actions',not any('重新扫描' in a.text() or '排序与日期' in a.text() or '撤回' in a.text() for a in window.more_panel.action_controls));window.more_panel.hide();window.scan(photos)
                until(lambda:len(window.queue)==3 and all(p['id'] in window.cache for p in window.queue[:2]) and 'ISO 100' in window.details[1].text())
                check('metadata_is_current_left_photo','f/2.8' in window.details[1].text() and '12:00:30' in window.details[1].text() and 'ISO 200' not in window.details[1].text())
                check('expanded_camera_information','Sony ILCE-7M4' in window.details[1].text() and 'FE 85mm F1.8' in window.details[1].text() and '85 mm' in window.details[1].text())
                check('opened_folder_hides_resume_and_export',not window.resume_button.isVisible() and not hasattr(window,'export_button') and window.rating_button.isVisible() and window.segments.isVisible())
                check('compact_action_dock',window.keep_button.height()==26 and window.actionbar.width()<510)
                check('comparison_filename_hidden',not window.names[1].isVisible() and window.names[1].text()=='')
                metadata=window.details[1].text();window.caption(1,window.queue[1],'comparison zoom information')
                check('comparison_zoom_keeps_current_parameters',window.details[1].text()==metadata)
                crop=Image.new('RGB',(140,180),'#c9ac87');draw=ImageDraw.Draw(crop)
                draw.ellipse((20,10,120,170),fill='#e0b49a');draw.ellipse((42,57,55,66),fill='#334552');draw.ellipse((86,57,99,66),fill='#334552')
                png=io.BytesIO();crop.save(png,'PNG');result={'faces':[{}],'imagebytes':png.getvalue()}
                from face_service import cache_key
                window.faces.enabled=True;window.faces._synchronize();key=cache_key(window.queue[0]);window.faces._remember(key,result);window.faces._done.add(key)
                window.face_changed(window.queue[0]['id'],result);application.processEvents()
                panel=window.face_panel;check('face_and_metadata_share_right_header',panel.parentWidget() is window.face_container and window.details[1].parentWidget() is window.review_info)
                role=window.photo_roles[1]
                check('comparison_caption_is_below_face_and_above_photo',window.review_info.mapTo(window,QPoint()).y()+window.review_info.height()<=role.mapTo(window,QPoint()).y() and role.mapTo(window,QPoint()).y()+role.height()<=window.photos[1].mapTo(window,QPoint()).y())
                check('header_sits_above_comparison',window.review_info.mapTo(window, QPoint()).y()+window.review_info.height()<=window.photos[1].mapTo(window,QPoint()).y())
                check('face_has_no_title_or_tooltip',panel.toolTip()=='' and not panel.findChildren(type(window.details[1])))
                before=panel.grab().toImage();pid=window.queue[0]['id']
                QTest.mouseClick(panel,Qt.MouseButton.LeftButton);QTest.mouseDClick(panel,Qt.MouseButton.LeftButton);QTest.mouseClick(panel,Qt.MouseButton.RightButton)
                wheel=QWheelEvent(QPointF(50,50),QPointF(panel.mapToGlobal(QPoint(50,50))),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
                application.sendEvent(panel,wheel);application.processEvents()
                check('mouse_does_not_change_face',panel.grab().toImage()==before)
                check('mouse_does_not_advance_or_zoom_photo',window.queue[0]['id']==pid and not any(p.detail for p in window.photos))
                QTest.qWait(150);check('face_remains_visible_in_header',panel.isVisible())
                shot=window.grab().toImage();point=panel.mapTo(window,panel.rect().center());dpr=shot.devicePixelRatio()
                check('face_pixels_appear_in_full_window',shot.pixelColor(round(point.x()*dpr),round(point.y()*dpr)).name()=='#e0b49a')
                shot.save(str(output/'review-header.png'))
                window.next_photo();until(lambda:window.queue and window.queue[0]['name']=='1.jpg' and 'ISO 200' in window.details[1].text())
                check('parameters_follow_left_photo_after_switch','12:01:30' in window.details[1].text() and 'ISO 100' not in window.details[1].text())
                window.face_changed(window.queue[0]['id'],{'faces':[]});check('no_face_hides_image',not panel.isVisible())
                if raw_fixture:
                    window.faces.enabled=False
                    raw_folder=base/'中文拍摄目录';raw_folder.mkdir()
                    shutil.copyfile(raw_fixture,raw_folder/'000-正常照片.ARW')
                    (raw_folder/'001-损坏相邻照片.ARW').write_bytes(b'invalid RAW for partial metadata failure')
                    window.scan(raw_folder)
                    until(lambda:len(window.queue)==2 and window.queue[0]['name']=='000-正常照片.ARW' and 'ISO 400' in window.current_metadata.text())
                    check('real_arw_metadata_survives_bad_neighbor_and_chinese_path','ILCE-7S' in window.current_metadata.text() and '2017-07-13' in window.current_metadata.text())
                    window.grab().save(str(output/'raw-metadata.png'))
                sharp_folder=base/'清晰预览';sharp_folder.mkdir()
                for i in range(3):
                    large=Image.new('RGB',(4000,3000),'#d9dfe4');grid=ImageDraw.Draw(large)
                    for x in range(0,4000,16):grid.line((x,0,x,3000),fill='#263644',width=2)
                    grid.text((100,100),f'SHARP {i}',fill='black',font_size=100);large.save(sharp_folder/f'{i}.jpg',quality=96)
                window.faces.enabled=False;window.decoders=Decoders(1);window.scan(sharp_folder)
                until(lambda:len(window.queue)==3 and window.queue[0]['name']=='0.jpg' and not window.busy)
                window.resize_timer.stop();window.display_size=QSize(2500,1800);window.prefetch()
                until(lambda:all(window.preview_ready(p['id']) for p in window.queue))
                check('high_resolution_frames_preloaded_in_isolated_worker',all(window.cache[p['id']].width()>=2400 and window.cache[p['id']].height()>=1800 for p in window.queue))
                target=window.queue[1]['id'];window.next_button.click()
                check('next_uses_sharp_pixels_immediately_without_ghost',window.queue[0]['id']==target and window.photos[0].item.opacity()==1 and window.pending_next is None and not window.motions)
                window.grab().save(str(output/'sharp-next.png'))


            finally:window.prompt_on_close=False;window.close();application.processEvents()
    (output/'report.json').write_text(json.dumps({'ok':True,'checks':checks},ensure_ascii=False,indent=2),encoding='utf8')
    print('PASS review header',len(checks),'checks')

if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support();run(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None)
