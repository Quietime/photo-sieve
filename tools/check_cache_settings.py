"""Open fixed-budget full-folder cache settings and verify retention/explicit cleanup."""
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
        deleted_source=base/'deleted.jpg';Image.new('RGB',(300,200),'teal').save(deleted_source);deleted_fingerprint=hashlib.sha256(deleted_source.read_bytes()).hexdigest()
        stat=source.stat();library.remember('root',str(base))
        with library.connect() as db:
            db.execute('INSERT INTO photos(id,path,size,mtime) VALUES (1,?,?,?)',(str(source),stat.st_size,stat.st_mtime_ns))
            deleted_stat=deleted_source.stat();db.execute('INSERT INTO photos(id,path,size,mtime) VALUES (2,?,?,?)',(str(deleted_source),deleted_stat.st_size,deleted_stat.st_mtime_ns))
        with patch('desktop.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            window=Window(library,check_updates=False,start_empty=True,data_root=base)
            try:
                window.show();application.processEvents();until(lambda:not window.maintenance_running)
                errors=[];checks=[]
                def dialog_case(memory,name):
                    def interact():
                        try:
                            dialog=application.activeModalWidget();assert isinstance(dialog,QDialog)
                            fields=dialog.findChildren(CacheSpinBox);assert len(fields)==1,'Only RAM preload count should be editable'
                            fields[0].setValue(memory)
                            application.processEvents();dialog.grab().save(str(output/(name+'.png')))
                            text='\n'.join(label.text() for label in dialog.findChildren(QLabel))
                            assert '全部未删除照片，后台自动生成' in text and '固定 10 GB' in text
                            assert '70%' in text and '保留' in text and '切换' in text
                            assert '2 分钟' not in text or '已删除' in text,'Kept previews must not expire after2min'
                            for field in fields:assert dialog.rect().contains(field.mapTo(dialog,field.rect().bottomRight()))
                            explanation=next(label for label in dialog.findChildren(QLabel) if '70%' in label.text())
                            static_rows=[label for label in dialog.findChildren(QLabel) if label.text() in ('全部未删除照片，后台自动生成','固定 10 GB')]
                            bottom=max(control.mapTo(dialog,control.rect().bottomRight()).y() for control in fields+static_rows)
                            assert explanation.y()>bottom+8, 'Explanation overlaps cache controls'
                            save=next(button for button in dialog.findChildren(AppleButton) if button.text()=='保存')
                            assert save.mapTo(dialog,save.rect().topLeft()).y()>explanation.geometry().bottom()+8, 'Save row overlaps explanation'
                            next(button for button in dialog.findChildren(AppleButton) if button.text()=='保存').click()
                        except BaseException as exc:
                            errors.append(repr(exc))
                            if application.activeModalWidget():application.activeModalWidget().reject()
                    QTimer.singleShot(60,interact);window.cache_dialog();assert not errors,errors
                dialog_case(16,'cache-memory-16')
                assert window.ahead_target==16 and window.faces.ram_limit==20
                assert library.cache_limit==10*1024**3 and settings.value('cache_mb',type=int)==10240
                checks.append('single_editable_ram_field_fixed_full_folder_and_10gb_policy')
                dialog_case(32,'cache-memory-32')
                assert window.ahead_target==32 and window.faces.ram_limit==36
                assert settings.value('preload_count',type=int)==32
                assert library.cache_limit==10*1024**3 and settings.value('cache_mb',type=int)==10240
                checks.append('saved_ram_count_keeps_face_memory_bounded_and_disk_budget_fixed')
                until(lambda:not window.maintenance_running)
                library.preview(1,1600);library.mark(1,1)
                kept_paths=set(library.cache.glob('*.jpg'))
                library.preview(2,1600);deleted_paths=set(library.cache.glob('*.jpg'))-kept_paths
                deletion=library.trash(2)
                for path in library.cache.glob('*.jpg'):os.utime(path,(1,1))
                window.maintain_cache();until(lambda:not window.maintenance_running)
                assert kept_paths and all(path.exists() for path in kept_paths),'Kept photos must retain aged previews below10GB'
                assert deleted_paths and not any(path.exists() for path in deleted_paths),'Old deleted-photo previews must expire'
                assert hashlib.sha256(source.read_bytes()).hexdigest()==fingerprint and library.photo(1)['status']==1
                assert hashlib.sha256(Path(deletion['backup']).read_bytes()).hexdigest()==deleted_fingerprint
                checks.append('scheduled_cleanup_retains_kept_previews_expires_deleted_preserves_sources_and_records')
                sentinel=library.cache/'old-photo-sentinel.jpg';sentinel.write_bytes(b'old derived preview')
                other=Library(base/'libraries'/'inactive');(other.cache/'face-old.png').write_bytes(b'derived face')
                window.metadata_service.cache['stale']='metadata'
                window.session_active=True;window.show_menu();application.processEvents()
                texts=[button.text() for button in window.more_panel.action_controls]
                assert not any('撤回上一步' in text or '撤销评级' in text for text in texts)
                next(button for button in window.more_panel.action_controls if button.text()=='一键清理全部缓存').trigger()
                window.session_active=False
                until(lambda:not window.cache_clearing and not window.busy)
                assert not sentinel.exists() and not (other.cache/'face-old.png').exists()
                assert 'stale' not in window.metadata_service.cache
                assert hashlib.sha256(source.read_bytes()).hexdigest()==fingerprint and library.photo(1)['status']==1
                checks.append('one_click_clears_old_photo_face_metadata_sentinels_and_removes_undo_entry')
                report={'ok':True,'platform':application.platformName(),'checks':checks,'memory_count':window.ahead_target,'disk_horizon':window.disk_horizon,'face_ahead':window.faces.effective_ahead,'face_ram':window.faces.ram_limit}
            finally:window.prompt_on_close=False;window.close();application.processEvents()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print('PASS fixed10GB full-folder settings, bounded RAM and kept/deleted retention cleanup');faulthandler.cancel_dump_traceback_later();trace.close()

if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support();run(sys.argv[1])
