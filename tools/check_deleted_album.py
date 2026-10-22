"""Use real temporary files to test multi-selection, restore and permanent-trash controls."""
import hashlib,json,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QSettings,QTimer,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop import Window,STYLE,ROLE
from app import Library

def run(output):
    import faulthandler
    output=Path(output);output.mkdir(parents=True,exist_ok=True);trace=(output/'threads.log').open('w');faulthandler.dump_traceback_later(60,file=trace,exit=True)
    application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE);checks=[]
    def until(test):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            application.processEvents()
            if test():return
            QTest.qWait(5)
        raise AssertionError('Deleted album did not finish: '+repr({'errors':errors if 'errors' in locals() else [],'state':window.library.state()['counts'] if 'window' in locals() else 'unavailable'}))
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);photos=base/'photos';photos.mkdir();settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat)
        settings.setValue('face_preview',False);settings.setValue('warm_count',0)
        for i in range(8):Image.new('RGB',(400,600),((i*35)%256,80,140)).save(photos/f'{i}.jpg')
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in photos.iterdir()}
        library=Library(base/'workspace');library.scan(photos);until(lambda:not library.job['running']);rows=library.list_photos('pending')
        library.mark(rows[0]['id'],1);library.mark(rows[2]['id'],1)
        backups={photo['id']:library.trash(photo['id'])['backup'] for photo in rows[2:]}
        errors=[]
        with patch('desktop.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            window=Window(library,check_updates=False,data_root=base);window.error=errors.append
            try:
                window.resize(1280,900);window.show();until(lambda:window.counts.get('deleted')==6 and not window.fetching)
                window.switch('deleted');until(lambda:window.deleted_grid.count()==6 and not window.fetching)
                assert window.restore_selected.isVisible() and window.purge_selected.isVisible() and not hasattr(window,'clear_deleted') and '磁盘' in window.purge_selected.text()
                window.deleted_multiselect.click();grid=window.deleted_grid
                def choose(index):QTest.mouseClick(grid.viewport(),Qt.MouseButton.LeftButton,pos=grid.visualItemRect(grid.item(index)).center());application.processEvents()
                choose(0);choose(1)
                assert len(grid.selectedItems())==2 and window.stack.currentIndex()==1
                assert '2' in window.deleted_note.text();window.grab().save(str(output/'deleted-multi-select.png'));checks.append('multi_click_selects_without_opening_and_shows_count')
                window.restore_selected.click();until(lambda:not window.busy and not window.pending_ops and not window.fetching and library.state()['counts']['deleted']==4 and grid.count()==4)
                assert Path(rows[2]['path']).exists() and library.photo(rows[2]['id'])['status']==1
                assert Path(rows[3]['path']).exists();checks.append('batch_restore_preserves_original_keep_status')
                choose(0);choose(1);assert len(grid.selectedItems())==2
                selected_ids={item.data(ROLE) for item in grid.selectedItems()};QTest.qWait(240)
                assert {item.data(ROLE) for item in grid.selectedItems()}==selected_ids,'Late batch refresh cleared the new selection'
                def confirm(accept,name,expected):
                    def interact():
                        try:
                            dialog=application.activeModalWidget();assert dialog is not None
                            assert expected in dialog.text() and dialog.defaultButton().text()=='取消'
                            assert '磁盘上的实际文件' in dialog.informativeText();dialog.grab().save(str(output/(name+'.png')))
                            button=next(button for button in dialog.buttons() if button.text()==('永久删除' if accept else '取消'));button.click()
                        except BaseException as error:
                            errors.append(repr(error))
                            if application.activeModalWidget():application.activeModalWidget().reject()
                    QTimer.singleShot(80,interact)
                confirm(False,'batch-delete-cancel','所选 2');window.purge_selected.click();assert library.state()['counts']['deleted']==4;checks.append('permanent_delete_defaults_to_cancel_and_cancel_preserves_files')
                confirm(True,'batch-delete-confirm','所选 2');window.purge_selected.click();until(lambda:not window.busy and not window.pending_ops and not window.fetching and library.state()['counts']['deleted']==2 and grid.count()==2)
                assert all(not Path(backups[p['id']]).is_file() for p in rows[4:6]);checks.append('batch_delete_only_removes_selected_trash')
                Path(backups[rows[6]['id']]).unlink();window.scan(photos)
                until(lambda:not window.importing and not window.busy and not window.fetching and library.state()['counts']['deleted']==1 and grid.count()==1)
                window.grab().save(str(output/'external-disk-delete-after-rescan.png'))
                checks.append('rescan_removes_manually_deleted_disk_file_from_gallery')
                until(lambda:window.counts.get('deleted')==1 and not window.pending_ops and not window.job_running() and window.select_all_deleted.isEnabled())
                window.select_all_deleted.click();assert window.all_deleted_selected and len(grid.selectedItems())==1
                window.select_all_deleted.click();assert not grid.selectedItems();checks.append('select_all_and_clear_selection_operate')
                window.select_all_deleted.click();assert window.all_deleted_selected and '全部' in window.purge_selected.text()
                confirm(True,'all-selected-disk-delete-confirm','全部 1');window.purge_selected.click();until(lambda:not window.busy and not window.pending_ops and not window.fetching and library.state()['counts']['deleted']==0 and grid.count()==0)
                for photo in rows[:4]:assert hashlib.sha256(Path(photo['path']).read_bytes()).hexdigest()==hashes[photo['name']]
                assert all(not Path(backups[row['id']]).exists() for row in rows[4:])
                assert not errors,errors;checks.append('select_all_permanently_deletes_actual_disk_files_preserves_kept_pending_and_restored_files');window.grab().save(str(output/'deleted-empty.png'))
                report={'ok':True,'platform':application.platformName(),'checks':checks}
            finally:window.prompt_on_close=False;window.close();application.processEvents()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');faulthandler.cancel_dump_traceback_later();trace.close();print('PASS deleted album',len(checks),'checks')

if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support();run(sys.argv[1])
