"""Exercise rapid GUI deletion while metadata holds real Windows file handles."""
import ctypes,hashlib,json,os,sys,tempfile,threading,time
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

def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE)
    def until(test):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            application.processEvents()
            if test():return
            QTest.qWait(5)
        raise AssertionError('Rapid delete did not complete')
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);photos=base/'photos';photos.mkdir();digests={}
        for i in range(40):
            path=photos/f'{i:03}.jpg';Image.new('RGB',(200,140),(i*5,60,90)).save(path);digests[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat);settings.setValue('face_preview',False);settings.setValue('warm_count',0)
        reader=Reader()
        with patch('desktop.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings),patch('desktop.MetadataReader',return_value=reader):
            window=Window(Library(base/'workspace'),check_updates=False,start_empty=True);errors=[];window.error=errors.append
            try:
                window.resize(1280,900);window.show();window.scan(photos);until(lambda:len(window.queue)==40 and window.ready() and reader.entered.is_set() and not window.busy)
                window.activateWindow();window.setFocus();deleted=[]
                for _ in range(24):
                    until(lambda:window.ready() and window.keep_button.isEnabled())
                    deleted.append(window.queue[0]['id']);QTest.keyClick(window,Qt.Key.Key_Down);application.processEvents()
                    assert window.queue[0]['id']!=deleted[-1],'Delete key did not advance'
                until(lambda:not window.pending_ops and not window.inflight)
                assert not errors,errors
                with window.library.connect() as db:
                    rows=db.execute("SELECT photo_id,path,backup,result FROM trash_log WHERE result='staged'").fetchall()
                    assert len(rows)==24 and len({r['photo_id'] for r in rows})==24
                    assert db.execute("SELECT COUNT(*) FROM history WHERE action='trash'").fetchone()[0]==24
                    assert db.execute("SELECT COUNT(*) FROM trash_log WHERE result='started'").fetchone()[0]==0
                for row in rows:
                    assert not Path(row['path']).exists();assert hashlib.sha256(Path(row['backup']).read_bytes()).hexdigest()==digests[Path(row['path']).name]
                assert len(list(photos.glob('*.jpg')))==16
                window.grab().save(str(output/'rapid-delete.png'))
                # An external Windows handle has no application lock: verify retry
                # handles its short-lived sharing violation as well.
                target=window.queue[0]['id'];path=window.library.photo(target)['path'];locked=threading.Event()
                def external():
                    with held_file(path):locked.set();time.sleep(.12)
                thread=threading.Thread(target=external);thread.start();assert locked.wait(2)
                result=window.library.trash(target);thread.join(2);assert Path(result['backup']).exists()
                report={'ok':True,'platform':application.platformName(),'deleted':24,'external_handle_move':True,'metadata_reads':reader.count,'errors':errors,'checks':['rapid_keyboard_delete_with_active_metadata_handle','all_deleted_backups_match_original_bytes','one_history_entry_per_photo','no_unfinished_moves','external_share_lock_retry']}
            finally:window.prompt_on_close=False;window.close();application.processEvents()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print('PASS rapid delete: 24 keys, actual file handles, no failed moves')

if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support();run(sys.argv[1])
