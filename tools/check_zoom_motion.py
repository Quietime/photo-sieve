"""Exercise the changed review and read-only album zoom paths in native/frozen Qt."""
import json,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from PySide6.QtCore import Qt,QPoint,QObject,QEvent,QSettings,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import Library
from desktop import Window,STYLE
import theme

def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE)
    checks=[];measurements=[]
    def until(predicate):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            application.processEvents()
            if predicate():return
            QTest.qWait(2)
        raise AssertionError('Zoom interaction did not complete')
    class Paints(QObject):
        def __init__(self,pane):super().__init__();self.times=[];self.pane=pane
        def eventFilter(self,owner,event):
            # Do not count static time waiting for a decoder as a dropped frame;
            # only compare consecutive paints belonging to the same animation.
            if event.type()==QEvent.Type.Paint and self.pane.zoom_animation:
                self.times.append((id(self.pane.zoom_animation),time.perf_counter()))
            return False
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);photos=base/'photos';photos.mkdir();settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat)
        for key,value in [('face_preview',False),('warm_count',0),('review/reduced_motion',False),('auto_update',False)]:settings.setValue(key,value)
        for index,size in enumerate([(3000,4500),(4500,3000),(3000,4500)]):
            image=Image.new('RGB',size,'#4e687d');draw=ImageDraw.Draw(image)
            for y in range(0,size[1],120):
                for x in range(0,size[0],120):
                    draw.rectangle((x,y,x+118,y+118),fill=('#91aaba' if (x//120+y//120)%2 else '#415a70'))
            draw.text((100,100),'ZOOM '+str(index),fill='white',font_size=140);image.save(photos/f'{index}.jpg',quality=96)
        with patch('desktop.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            window=Window(Library(base/'workspace'),check_updates=False,start_empty=True,data_root=base);errors=[];window.error=errors.append
            try:
                window.resize(1280,900);window.show();window.scan(photos)
                until(lambda:len(window.queue)==3 and window.ready());photo=window.queue[0];pane=window.photos[0]
                observer=Paints(pane);pane.viewport().installEventFilter(observer)
                point=pane.content_rect().center();scale=pane.transform().m11();began=time.perf_counter()
                QTest.mouseClick(pane.viewport(),Qt.MouseButton.LeftButton,pos=point)
                first_ms=(time.perf_counter()-began)*1000
                assert pane.detail and pane.transform().m11()>scale,'Click must change zoom before waiting for decode or animation tick'
                motion=pane.zoom_animation;assert motion and motion.duration()==200
                QTimer.singleShot(70,lambda:window.grab().save(str(output/'review-zoom-moving.png')))
                until(lambda:pane.full_resolution and pane.zoom_animation is None)
                window.grab().save(str(output/'review-zoom-sharp.png'))
                gaps=[(b[1]-a[1])*1000 for a,b in zip(observer.times,observer.times[1:]) if a[0]==b[0]]
                measurements.append({'view':'review','reported_display_hz':window.screen().refreshRate(),'timer_ms':theme.FrameClock.interval(pane),'same_event_response_ms':round(first_ms,3),'paint_events':len(observer.times),'mean_paint_gap_ms':round(sum(gaps)/len(gaps),3) if gaps else None,'max_paint_gap_ms':round(max(gaps),3) if gaps else None})
                checks.append('review_responds_in_click_event_and_installs_original_without_serial_zoom_animation')
                for _ in range(4):
                    QTest.mouseClick(pane.viewport(),Qt.MouseButton.LeftButton,pos=point);assert not pane.detail
                    QTest.qWait(25)
                    QTest.mouseClick(pane.viewport(),Qt.MouseButton.LeftButton,pos=point);assert pane.detail
                    until(lambda:pane.full_resolution and pane.zoom_animation is None)
                checks.append('review_reverses_repeatedly_during_shrink')
                window.reset_zoom();until(lambda:pane.zoom_animation is None)
                window.library.mark(photo['id'],1);window.switch('keep');until(lambda:not window.fetching)
                def album_check(gallery):
                    window.album.open(photo['id']);album=window.album
                    until(lambda:album.photo is not None and album.transition is None)
                    p=album.pane;position=p.content_rect().center();full_reads=[];original=window.library.preview_image
                    def read(pid,edge):
                        if edge==0:full_reads.append(pid)
                        return original(pid,edge)
                    with patch.object(window.library,'preview_image',side_effect=read):
                        for cycle in range(3):
                            QTest.mouseClick(p.viewport(),Qt.MouseButton.LeftButton,pos=position)
                            assert p.detail;until(lambda:p.full_resolution and p.zoom_animation is None)
                            if cycle==0:window.grab().save(str(output/(gallery+'-zoom.png')))
                            QTest.mouseClick(p.viewport(),Qt.MouseButton.LeftButton,pos=position)
                            assert not p.detail;until(lambda:p.zoom_animation is None)
                        assert len(full_reads)==1,'One current-photo original must serve repeated album zooms'
                    checks.append(gallery+'_three_zoom_cycles_reuse_one_original_and_keep_source_dimensions')
                    rect=p.content_rect();assert rect.left()>10
                    QTest.mouseClick(p.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(rect.left()//2,p.viewport().height()//2))
                    assert album.returning and album.transition and album.transition.duration()==240
                    QTest.qWait(70);window.grab().save(str(output/(gallery+'-return-moving.png')))
                    until(lambda:window.stack.currentWidget() is not album)
                    assert album.full_preview is None and album.preview is None
                    window.grab().save(str(output/(gallery+'-gallery.png')))
                    checks.append(gallery+'_blank_returns_with_animation_and_releases_original')
                album_check('keep')
                window.library.trash(photo['id']);window.switch('deleted');until(lambda:not window.fetching)
                album_check('deleted')
                assert not errors,errors
                report={'ok':True,'platform':application.platformName(),'checks':checks,'measurements':measurements,'note':'Paint timings describe this runner; 240 Hz timer selection and migration are tested separately with simulated screen rates, not physical 240 Hz display hardware.'}
            finally:window.prompt_on_close=False;window.close();application.processEvents()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('PASS zoom interaction',len(checks),'checks')

if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support();run(sys.argv[1])
