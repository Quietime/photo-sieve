"""Verify whole-folder disk caching and a late-writer-safe folder handoff in real Qt."""
import hashlib,json,os,sys,tempfile,threading,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QSettings,QSize,QTimer
from PySide6.QtWidgets import QApplication
from app import Library
from desktop import Window,STYLE,Decoders
from full_cache import cache_exists

def run(output,portrait=None):
    import faulthandler
    output=Path(output);output.mkdir(parents=True,exist_ok=True);trace=(output/'threads.log').open('w');faulthandler.dump_traceback_later(100,file=trace,exit=True)
    app=QApplication.instance() or QApplication([]);app.setStyleSheet(STYLE);checks=[];gate=threading.Event()
    def until(condition):
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            app.processEvents()
            if condition():return
            time.sleep(.003)
        raise AssertionError('Whole-folder cache did not complete')
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);photos=base/'A';other=base/'B';photos.mkdir();other.mkdir();root=base/'workspaces'
        for index in range(42):Image.new('RGB',(4000,3000) if index==0 else (96,144),(40+index*3,80,130)).save(photos/f'{index:03}.jpg')
        for index in range(4):Image.new('RGB',(96,144),(70,100+index*10,110)).save(other/f'{index:03}.jpg')
        if portrait and Path(portrait).is_file():
            for index in range(2):
                with Image.open(portrait) as source:source.convert('RGB').save(other/f'{index:03}.jpg')
        original_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in photos.iterdir()}
        library=Library(root/'libraries'/'initial');library.scan(photos);until(lambda:not library.job['running']);rows=library.list_photos('all',limit=60)
        for row in rows[:2]:library.mark(row['id'],1)
        backups=[library.trash(row['id'])['backup'] for row in rows[-2:]]
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat)
        for key,value in [('preload_count',16),('warm_count',0),('cache_mb',64),('face_preview',False)]:settings.setValue(key,value)
        errors=[]
        with patch('desktop.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            window=Window(library,decoders=Decoders(2),check_updates=False,data_root=root);window.error=errors.append
            try:
                window.resize(1280,900);window.show()
                until(lambda:window.full_cache.status()['finished'])
                state=window.full_cache.status();present=library.list_photos('all',limit=60)
                assert len(present)==40 and state['visited']==40 and not state['errors'],state
                assert all(cache_exists(library,row,state['edge']) for row in present)
                assert len(window.cache)<=window.cache_limit and window.ahead_target==16
                checks.append('all_40_pending_and_kept_cached_beyond_16_photo_ram_and_legacy_zero_warm_setting')
                window.grab().save(str(output/'full-folder-cached.png'))
                window.display_size=QSize(2500,1800);window.ensure_full_cache(force=True)
                until(lambda:window.full_cache.status()['finished'])
                state=window.full_cache.status();assert state['edge']==3200 and not state['errors']
                assert all(cache_exists(library,row,3200) for row in present)
                checks.append('whole_folder_adapts_to_physical_3200_preview_bucket')
                for path in library.cache.glob('*.jpg'):os.utime(path,(1,1))
                window.maintain_cache();until(lambda:not window.maintenance_running)
                assert all(cache_exists(library,row,3200) for row in present)
                assert library.cache_limit==10*1024**3 and settings.value('cache_mb',type=int)==10240
                checks.append('aged_pending_and_kept_previews_survive_maintenance_under_fixed_10gib')
                # Restore a photo outside the RAM window after the initial sweep.
                restored=library.restore_deleted([rows[-1]['id']]);until(lambda:not library.job['running'])
                window.full_cache.refresh_photo(rows[-1]['id'])
                until(lambda:window.full_cache.status()['finished'])
                assert cache_exists(library,library.photo(rows[-1]['id']),3200)
                checks.append('restored_photo_is_added_without_restarting_the_whole_pass')
                entered=threading.Event();sentinel=library.cache/'late-old-writer.png'
                def late_writer():
                    entered.set();gate.wait(10);sentinel.write_bytes(b'old derived face');return True
                window.submit(late_writer,lambda _:None,errors.append,window.image_pool)
                until(entered.is_set);window.scan(other);QTimer.singleShot(120,gate.set)
                until(lambda:window.library is not library and not window.importing and not window.busy)
                until(lambda:window.full_cache.status()['finished'])
                assert not list(library.cache.glob('*.jpg')) and not list(library.cache.glob('*.png'))
                assert library.photo(rows[0]['id'])['status']==1 and library.database.is_file()
                assert Path(backups[0]).is_file()
                for row in rows[:-2]:assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==original_hashes[row['name']]
                time.sleep(.15);app.processEvents();assert not sentinel.exists()
                checks.append('folder_switch_drains_late_writer_then_erases_old_cache_preserving_originals_history_and_trash')
                assert window.library.state()['counts']['all']==4
                assert all(cache_exists(window.library,row,window.full_cache.edge) for row in window.library.list_photos('all'))
                window.grab().save(str(output/'new-folder-cached-old-cleared.png'))
                checks.append('new_folder_all_photos_cached_after_handoff')
                window.faces.configure(True,16);window.full_faces.pump()
                until(lambda:window.full_faces.status()['finished'])
                assert window.full_faces.status()['visited']==4 and not window.full_faces.status()['errors'],window.full_faces.status()
                with window.library.connect() as db:assert db.execute('SELECT COUNT(*) FROM face_metadata').fetchone()[0]==4
                checks.append('all_new_folder_pending_photos_recognized_in_background')
                until(lambda:len(window.queue)>1 and window.preview_ready(window.queue[1]['id']) and window.faces.result_for(window.queue[1]) is not None)
                from face_service import cache_key
                target=window.queue[1];expected=window.faces.result_for(target)
                if portrait:assert expected.get('face_count',0)>0,expected
                started=time.perf_counter();window.next_photo();face_switch_ms=(time.perf_counter()-started)*1000
                assert window.queue[0]['id']==target['id']
                assert window.face_panel.result and window.face_panel.result['cache_key']==cache_key(target)
                window.grab().save(str(output/'background-face-ready-on-next.png'))
                checks.append('prepared_face_switches_in_same_event_without_foreground_detection')

                assert not errors,errors
                report={'ok':True,'platform':app.platformName(),'checks':checks,'old_folder_photos':42,'kept':2,'initial_present_cached':40,'ram_preload':16,'high_resolution_edge':3200,'source_and_record_preservation':True,'cached_face_switch_ms':round(face_switch_ms,3),'real_portrait':bool(portrait)}
            finally:
                gate.set();window.prompt_on_close=False;window.close();app.processEvents()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');faulthandler.cancel_dump_traceback_later();trace.close();print('PASS full-folder cache',len(checks),'checks')

if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support();run(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None)
