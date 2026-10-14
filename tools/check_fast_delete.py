"""Exercise rapid GUI deletion while metadata holds real Windows file handles."""
import argparse,ctypes,hashlib,json,os,shutil,sys,tempfile,threading,time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QSettings,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import Library
from desktop import Window,STYLE

@contextmanager
def test_directory(output):
    temporary=tempfile.TemporaryDirectory()
    try:yield temporary.name
    except BaseException:
        # Preserve evidence and avoid deleting files still owned by failed jobs.
        temporary._finalizer.detach()
        (output/'preserved-workspace.txt').write_text(temporary.name,encoding='utf8')
        raise
    else:temporary.cleanup()

@contextmanager
def held_file(path):
    if os.name!='nt':
        with open(path,'rb'):yield
        return
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    api.CreateFileW.restype=ctypes.c_void_p;api.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=api.CreateFileW(str(path),0x80000000,1,None,3,0,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:yield
    finally:api.CloseHandle(handle)

class Reader:
    def __init__(self):self.entered=threading.Event();self.closed=False;self.count=0
    def read(self,path):
        if self.closed:return ''
        with held_file(path):self.count+=1;self.entered.set();time.sleep(.07)
        return 'ISO 100'
    def close(self):self.closed=True

def run(output,count=24,raw_fixture=None):
    if not 1<=count<=2000:raise ValueError('count must be 1..2000')
    total=max(40,count+16);suffix='.ARW' if raw_fixture else '.jpg'
    output=Path(output);output.mkdir(parents=True,exist_ok=True);application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE)
    def until(test):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            application.processEvents()
            if test():return
            QTest.qWait(5)
        raise AssertionError('Rapid delete did not complete')
    with test_directory(output) as temporary:
        base=Path(temporary);photos=base/'photos';photos.mkdir();digests={}
        for i in range(total):
            path=photos/(f'{i:05}'+suffix)
            if raw_fixture:shutil.copyfile(raw_fixture,path)
            else:Image.new('RGB',(200,140),((i*5)%256,60,90)).save(path)
            digests[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat);settings.setValue('face_preview',False);settings.setValue('warm_count',0)
        reader=Reader()
        with patch('desktop.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings),patch('desktop.MetadataReader',return_value=reader):
            window=Window(Library(base/'workspace'),check_updates=False,start_empty=True);errors=[];window.error=errors.append
            try:
                window.resize(1280,900);window.show();window.scan(photos);until(lambda:len(window.queue)==total and window.ready() and reader.entered.is_set() and not window.busy)
                window.activateWindow();window.setFocus();deleted=[];expected=[p['id'] for p in window.queue[:count]];peak_pending=0;key_start=time.monotonic()
                for _ in range(count):
                    until(lambda:window.ready() and window.keep_button.isEnabled())
                    deleted.append(window.queue[0]['id']);QTest.keyClick(window,Qt.Key.Key_Down);application.processEvents();peak_pending=max(peak_pending,window.pending_ops)
                    assert window.queue[0]['id']!=deleted[-1],'Delete key did not advance'
                key_seconds=time.monotonic()-key_start
                # Durable filesystem/SQLite commits vary considerably on Windows.
                # Require ongoing progress, rather than the 24-photo fixed wait.
                drain_start=time.monotonic();last_progress=drain_start;previous=window.pending_ops;progress=[]
                while window.pending_ops or window.inflight:
                    application.processEvents();QTest.qWait(5);now=time.monotonic()
                    if window.pending_ops<previous:
                        previous=window.pending_ops;last_progress=now
                        progress.append([round(now-key_start,3),previous])
                    if now-last_progress>30:raise AssertionError('Deletion made no progress for 30 seconds')
                    if now-drain_start>max(120,count*.8):raise AssertionError('Deletion exceeded bounded total drain time')
                complete_seconds=time.monotonic()-key_start
                assert not errors,errors
                assert deleted==expected,'Deletion skipped or reordered photos'
                with window.library.connect() as db:
                    rows=db.execute("SELECT photo_id,path,backup,result FROM trash_log WHERE result='staged'").fetchall()
                    assert len(rows)==count and {r['photo_id'] for r in rows}==set(deleted)
                    assert db.execute("SELECT COUNT(*) FROM history WHERE action='trash'").fetchone()[0]==count
                    assert db.execute("SELECT COUNT(*) FROM trash_log WHERE result='started'").fetchone()[0]==0
                for row in rows:
                    assert not Path(row['path']).exists();assert hashlib.sha256(Path(row['backup']).read_bytes()).hexdigest()==digests[Path(row['path']).name]
                assert len(list(photos.glob('*'+suffix)))==total-count
                window.grab().save(str(output/'rapid-delete.png'))
                # An external Windows handle has no application lock: verify retry
                # handles its short-lived sharing violation as well.
                target=window.queue[0]['id'];path=window.library.photo(target)['path'];locked=threading.Event()
                def external():
                    with held_file(path):locked.set();time.sleep(.12)
                thread=threading.Thread(target=external);thread.start();assert locked.wait(2)
                result=window.library.trash(target);thread.join(2);assert Path(result['backup']).exists()
                report={'ok':True,'platform':application.platformName(),'deleted':count,'imported':total,'peak_pending':peak_pending,'in_order':deleted==expected,'format':'Sony ARW' if raw_fixture else 'generated JPEG','key_seconds':round(key_seconds,3),'complete_seconds':round(complete_seconds,3),'drain_progress':progress,'external_handle_move':True,'metadata_reads':reader.count,'errors':errors,'checks':['rapid_keyboard_delete_with_active_metadata_handle','all_deleted_backups_match_original_bytes','one_history_entry_per_photo','no_unfinished_moves','external_share_lock_retry']}
            except BaseException as exc:
                import faulthandler
                with (output/'threads.txt').open('w') as stream:faulthandler.dump_traceback(file=stream,all_threads=True)
                with window.library.connect() as db:
                    stages={r[0]:r[1] for r in db.execute('SELECT result,COUNT(*) FROM trash_log GROUP BY result')}
                (output/'report.json').write_text(json.dumps({'ok':False,'error':str(exc),'pending':window.pending_ops,'inflight':list(window.inflight),'stages':stages,'metadata_reads':reader.count,'errors':errors},indent=2),encoding='utf8')
                raise
            finally:
                window.prompt_on_close=False
                if window.pending_ops:
                    window.metadata_service.shutdown();window.faces.shutdown()
                    for pool in (window.pool,window.image_pool,window.thumb_pool,window.detail_pool):pool.clear()
                    deadline=time.monotonic()+10
                    while window.pending_ops and time.monotonic()<deadline:application.processEvents();QTest.qWait(5)
                if not window.pending_ops:window.close();application.processEvents()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(f'PASS rapid delete: {count} keys, actual file handles, no failed moves; keys {key_seconds:.2f}s, complete {complete_seconds:.2f}s')

if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support();parser=argparse.ArgumentParser();parser.add_argument('output');parser.add_argument('--count',type=int,default=24);parser.add_argument('--raw-fixture',type=Path);args=parser.parse_args();run(args.output,args.count,args.raw_fixture)
