"""Only check the changed review header, passive face image and current-photo parameters."""
import io,json,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from PySide6.QtCore import QSettings,QPoint,QPointF,Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import Library
from desktop import Window,STYLE

def run(output):
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
        settings.setValue('face_preview',False);settings.setValue('warm_count',0)
        for i in range(3):
            image=Image.new('RGB',(600,900) if i==0 else (900,600),['#687c93','#487864','#7d596a'][i])
            ImageDraw.Draw(image).text((40,40),'CURRENT' if i==0 else 'COMPARE',fill='white',font_size=35)
            exif=Image.Exif();exif[34665]={33437:2.8+i,33434:1/(100+i*100),34855:100+i*100,36867:f'2026:10:14 12:0{i}:30'}
            image.save(photos/f'{i}.jpg',exif=exif)
        with patch('desktop.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            window=Window(Library(base/'workspace'),check_updates=False,start_empty=True)
            try:
                window.faces.timer.stop();window.resize(1280,900);window.show();window.scan(photos)
                until(lambda:len(window.queue)==3 and all(p['id'] in window.cache for p in window.queue[:2]) and 'ISO 100' in window.details[1].text())
                check('metadata_is_current_left_photo','f/2.8' in window.details[1].text() and '12:00:30' in window.details[1].text() and 'ISO 200' not in window.details[1].text())
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
                check('header_sits_above_comparison',window.review_info.mapTo(window, QPoint()).y()+window.review_info.height()<=window.photos[1].mapTo(window,QPoint()).y())
                check('face_has_no_title_or_tooltip',panel.toolTip()=='' and not panel.findChildren(type(window.details[1])))
                before=panel.grab().toImage();pid=window.queue[0]['id']
                QTest.mouseClick(panel,Qt.MouseButton.LeftButton);QTest.mouseDClick(panel,Qt.MouseButton.LeftButton);QTest.mouseClick(panel,Qt.MouseButton.RightButton)
                wheel=QWheelEvent(QPointF(50,50),QPointF(panel.mapToGlobal(QPoint(50,50))),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
                application.sendEvent(panel,wheel);application.processEvents()
                check('mouse_does_not_change_face',panel.grab().toImage()==before)
                check('mouse_does_not_advance_or_zoom_photo',window.queue[0]['id']==pid and not any(p.detail for p in window.photos))
                QTest.qWait(150);check('face_remains_visible_in_header',panel.isVisible())
                window.grab().save(str(output/'review-header.png'))
                window.next_photo();until(lambda:window.queue and window.queue[0]['name']=='1.jpg' and 'ISO 200' in window.details[1].text())
                check('parameters_follow_left_photo_after_switch','12:01:30' in window.details[1].text() and 'ISO 100' not in window.details[1].text())
                window.face_changed(window.queue[0]['id'],{'faces':[]});check('no_face_hides_image',not panel.isVisible())
            finally:window.prompt_on_close=False;window.close();application.processEvents()
    (output/'report.json').write_text(json.dumps({'ok':True,'checks':checks},ensure_ascii=False,indent=2),encoding='utf8')
    print('PASS review header',len(checks),'checks')

if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support();run(sys.argv[1])
