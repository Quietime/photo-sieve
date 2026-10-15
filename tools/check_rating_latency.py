"""Hold verified writes in a worker and measure first-event rating feedback."""
import hashlib,json,sys,tempfile,threading,time
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

class HeldEngine(FakeEngine):
    def __init__(self):
        super().__init__();self.permits=threading.Semaphore(0);self.entered=threading.Event();self.calls=[];self.fail=set()
    def run(self,path,stars=None,expected=None):
        if stars is not None:
            self.calls.append((str(path),stars));self.entered.set()
            if not self.permits.acquire(timeout=15):raise RuntimeError('Test write permit timed out')
            if str(path) in self.fail:
                self.fail.remove(str(path));Path(path).write_bytes(b'partial failed write');raise RuntimeError('Simulated write failure')
        return super().run(path,stars,expected)
    def close(self):
        for _ in range(256):self.permits.release()

def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);app=QApplication.instance() or QApplication([]);app.setStyleSheet(STYLE);checks=[];timings=[];errors=[]
    def until(fn):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            app.processEvents()
            if fn():return
            QTest.qWait(5)
        raise AssertionError('Rating queue did not finish')
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);photos=base/'photos';photos.mkdir()
        for i in range(24):Image.new('RGB',(600,900) if i%2 else (900,600),(30+i*5,90,130)).save(photos/f'{i:03}.jpg')
        original={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in photos.iterdir()}
        lib=Library(base/'work');lib.scan(photos);until(lambda:not lib.job['running'])
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat);settings.setValue('preload_count',16);settings.setValue('face_preview',False)
        with patch('desktop.QSettings',return_value=settings),patch('rating_page.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            w=Window(lib,check_updates=False,data_root=base/'work');w.error=errors.append;w.resize(1440,1000);w.show();engine=HeldEngine()
            try:
                until(lambda:w.keep_button.isEnabled());p=w.rating_page;p.engine.close();p.engine=engine;w.toggle_rating()
                until(lambda:not p.listing and not p.loading and all(pid in p.info for pid in p.ids[:16]))
                p.auto.setChecked(True);first=p.pid();start=time.perf_counter();p.grade(4);timings.append((time.perf_counter()-start)*1000)
                assert p.pane.photo['id']==p.pid() and p.index==1 and p.info[first]['stars']==4 and p.info[first]['pending'] and '待保存' in p.grid.captions[first]
                assert p.star_buttons[0].isEnabled() and p.writing and w.pending_ops==1
                until(engine.entered.is_set)
                with lib.connect() as db:assert not db.execute('SELECT 1 FROM rating_history').fetchone()
                w.grab().save(str(output/'rating-immediate-pending.png'));checks.append('star_and_auto_next_update_before_blocked_write_finishes')
                for stars in (1,2,3,4,5,1,2):
                    start=time.perf_counter();p.grade(stars);timings.append((time.perf_counter()-start)*1000)
                assert p.index==8 and p.pending_count==8 and w.pending_ops==8
                for _ in range(8):engine.permits.release()
                until(lambda:not p.writing and not w.pending_ops)
                assert [star for _,star in engine.calls]==[4,1,2,3,4,5,1,2]
                assert all(not p.info[pid].get('pending') for pid in p.ids[:8])
                checks.append('eight_rapid_ratings_queue_without_dropped_input_and_save_in_order')
                p.auto.setChecked(False);p.select(0);p.grade(2);p.grade(5)
                assert p.stars.text()=='★★★★★' and p.info[first]['stars']==5 and p.pending_count==2
                engine.permits.release();until(lambda:p.pending_count==1)
                assert p.info[first]['stars']==5 and p.info[first]['pending']
                engine.permits.release();until(lambda:not p.writing)
                assert p.info[first]['stars']==5 and not p.info[first].get('pending')
                checks.append('older_completion_never_overwrites_newer_star_choice')
                p.select(9);pid=p.pid();path=Path(lib.photo(pid)['path']);before=p.info[pid]['stars'];engine.fail.add(str(path));p.grade(5);p.select(10)
                assert p.info[pid]['pending'];engine.permits.release();until(lambda:not p.writing)
                assert p.info[pid]['stars']==before and not p.info[pid].get('pending') and pid in p.rating_failures
                assert p.pid()==p.ids[10] and hashlib.sha256(path.read_bytes()).hexdigest()==original[path.name]
                assert errors and path.name in errors[-1];errors.clear()
                checks.append('failure_restores_original_file_and_stars_and_names_failed_photo_without_navigation_jump')
                p.select(9);p.grade(3);engine.permits.release();until(lambda:not p.writing)
                assert not p.rating_failures and p.info[pid]['stars']==3
                checks.append('failed_rating_can_be_retried_successfully')
                p.select(12);until(lambda:p.pane.photo and p.pane.photo['id']==p.pid() and p.pane.pixmap is not None)
                unknown=p.pid();p.info.pop(unknown,None)
                with patch.object(p,'read'):
                    p.grade(2)
                    assert p.info[unknown]['stars']==2 and p.info[unknown]['pending'] and p.pending_count==1
                engine.permits.release();until(lambda:not p.writing)
                assert p.info[unknown]['stars']==2 and not p.info[unknown].get('pending')
                checks.append('prepared_photo_can_be_rated_without_waiting_for_old_star_read')
                p.select(13);until(lambda:p.pane.photo and p.pane.photo['id']==p.pid() and p.pane.pixmap is not None)
                unread=p.pid();path=Path(lib.photo(unread)['path']);p.info.pop(unread,None);engine.fail.add(str(path))
                with patch.object(p,'read'):
                    p.grade(5);engine.permits.release();until(lambda:not p.writing)
                    assert p.info[unread]['stars'] is None and p.info[unread]['state']=='unread'
                assert hashlib.sha256(path.read_bytes()).hexdigest()==original[path.name];errors.clear()
                p.read(unread,force=True);until(lambda:p.info[unread]['state']=='ready')
                assert p.info[unread]['stars']==0
                p.grade(2);engine.permits.release();until(lambda:not p.writing)
                assert not p.rating_failures
                checks.append('failed_unread_rating_waits_for_verified_original_stars_instead_of_inventing_zero')
                p.auto.setChecked(True);p.filter.setCurrentIndex(1)
                until(lambda:not p.listing and p.pid() in p.info and not p.loading)
                p.grade(4);selected=p.pid();engine.permits.release()
                until(lambda:not p.writing and not p.listing and p.pid() in p.info)
                assert p.pid()==selected
                p.filter.setCurrentIndex(0);until(lambda:not p.listing and p.pid() in p.info)
                checks.append('filtered_auto_next_keeps_selected_photo_when_write_finishes')
                p.auto.setChecked(False)
                p.select(11);until(lambda:p.pid() in p.info);last=p.pid();p.grade(4);w.prompt_on_close=False;w.close();app.processEvents()
                assert not w.closed and w.closing and w.pending_ops==1
                p.grade(1);assert p.pending_count==1
                engine.permits.release();until(lambda:w.closed)
                assert engine.values[lib.photo(last)['path']]==4 and not w.pending_ops
                checks.append('close_waits_for_queued_verified_writes_and_rejects_new_ratings')
                assert not errors,errors
            finally:
                engine.close()
                if not w.closed:
                    until(lambda:not w.pending_ops);w.prompt_on_close=False;w.close();app.processEvents()
    report=dict(ok=True,checks=checks,click_handler_ms=timings,max_click_handler_ms=max(timings));(output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report),flush=True)

if __name__=='__main__':run(sys.argv[1])
