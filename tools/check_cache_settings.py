"""Open actual cache settings, save linked horizons, and exercise timer cleanup."""
import hashlib,json,os,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QSettings,QTimer
from PySide6.QtWidgets import QApplication,QLabel
from app import Library
from desktop import Window,STYLE,CacheSpinBox,QDialog
from apple_ui import AppleButton

def run(output):
    import faulthandler
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    trace=(output/'threads.log').open('w');faulthandler.dump_traceback_later(40,file=trace,exit=True)
    application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE)
    def until(condition):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            application.processEvents()
            if condition():return
            time.sleep(.005)
        raise AssertionError('Cache settings/maintenance did not finish')
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat)
        settings.setValue('face_preview',False);settings.setValue('warm_count',0)
        library=Library(base/'workspace');source=base/'source.jpg';Image.new('RGB',(200,300),'navy').save(source);fingerprint=hashlib.sha256(source.read_bytes()).hexdigest()
        stat=source.stat();library.remember('root',str(base))
        with library.connect() as db:db.execute('INSERT INTO photos(id,path,size,mtime) VALUES (1,?,?,?)',(str(source),stat.st_size,stat.st_mtime_ns))
        with patch('desktop.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            window=Window(library,check_updates=False,start_empty=True,data_root=base)
            try:
                window.show();application.processEvents();until(lambda:not window.maintenance_running)
                errors=[];checks=[]
                def dialog_case(memory,warm,name):
                    def interact():
                        try:
                            dialog=application.activeModalWidget();assert isinstance(dialog,QDialog)
                            fields=dialog.findChildren(CacheSpinBox);assert len(fields)==3
                            for field,value in zip(fields,(memory,warm,64)):field.setValue(value)
                            application.processEvents();dialog.grab().save(str(output/(name+'.png')))
                            text='\n'.join(label.text() for label in dialog.findChildren(QLabel))
                            assert '人脸跟随照片缓存' in text and '70%' in text
                            for field in fields:assert dialog.rect().contains(field.mapTo(dialog,field.rect().bottomRight()))
                            explanation=next(label for label in dialog.findChildren(QLabel) if '人脸跟随照片缓存' in label.text())
                            assert explanation.y()>max(field.mapTo(dialog,field.rect().bottomRight()).y() for field in fields)+8, 'Explanation overlaps cache controls'
                            save=next(button for button in dialog.findChildren(AppleButton) if button.text()=='保存')
                            assert save.mapTo(dialog,save.rect().topLeft()).y()>explanation.geometry().bottom()+8, 'Save row overlaps explanation'
                            next(button for button in dialog.findChildren(AppleButton) if button.text()=='保存').click()
                        except BaseException as exc:
                            errors.append(repr(exc))
                            if application.activeModalWidget():application.activeModalWidget().reject()
                    QTimer.singleShot(60,interact);window.cache_dialog();assert not errors,errors
                dialog_case(16,0,'cache-memory-only')
                assert window.ahead_target==16 and window.disk_horizon==0
                assert window.faces.effective_ahead==15 and window.faces.ram_limit==20
                checks.append('disk_zero_keeps_only_memory_window_for_faces')
                dialog_case(32,1000,'cache-linked')
                assert window.ahead_target==32 and window.disk_horizon==51
                assert window.faces.effective_ahead==50 and window.faces.ram_limit==36
                assert settings.value('warm_count',type=int)==1000 and settings.value('preload_count',type=int)==32
                checks.append('saved_memory_disk_and_face_horizons_match_capacity')
                until(lambda:not window.maintenance_running)
                library.preview(1,1600);library.mark(1,1)
                for path in library.cache.glob('*.jpg'):os.utime(path,(1,1))
                window.maintain_cache();until(lambda:not window.maintenance_running)
                assert not list(library.cache.glob('*.jpg'))
                assert hashlib.sha256(source.read_bytes()).hexdigest()==fingerprint and library.photo(1)['status']==1
                checks.append('scheduled_cleanup_expires_old_processed_previews_preserves_source_and_records')
                library.preview(1,1600)
                other=Library(base/'libraries'/'inactive');(other.cache/'face-old.png').write_bytes(b'derived face')
                window.metadata_service.cache['stale']='metadata'
                window.session_active=True;window.show_menu();application.processEvents()
                texts=[button.text() for button in window.more_panel.action_controls]
                assert not any('撤回上一步' in text or '撤销评级' in text for text in texts)
                next(button for button in window.more_panel.action_controls if button.text()=='一键清理全部缓存').trigger()
                window.session_active=False
                until(lambda:not window.cache_clearing and not window.busy)
                assert not list(library.cache.glob('*.jpg')) and not (other.cache/'face-old.png').exists()
                assert 'stale' not in window.metadata_service.cache
                assert hashlib.sha256(source.read_bytes()).hexdigest()==fingerprint and library.photo(1)['status']==1
                checks.append('one_click_menu_clears_photo_face_metadata_caches_and_removes_undo_entry')
                report={'ok':True,'platform':application.platformName(),'checks':checks,'memory_count':window.ahead_target,'disk_horizon':window.disk_horizon,'face_ahead':window.faces.effective_ahead,'face_ram':window.faces.ram_limit}
            finally:window.prompt_on_close=False;window.close();application.processEvents()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print('PASS linked cache settings and scheduled cleanup');faulthandler.cancel_dump_traceback_later();trace.close()

if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support();run(sys.argv[1])
