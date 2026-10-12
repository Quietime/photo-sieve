"""Reproducible native review audit, with 100,000 metadata rows and real small JPEGs.

QT_QPA_PLATFORM=offscreen python tools/check_performance.py --output performance.json
Timings describe this machine and synthetic JPEG workload, not camera RAW throughput.
No user files, project data or existing settings are touched. Cache-budget tests use a
scaled byte limit to exercise the real eviction pass without allocating 10 GiB.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def distribution(values):
    ordered = sorted(values)
    return {'samples': len(ordered), 'median_ms': round(statistics.median(ordered), 3),
            'p95_ms': round(ordered[min(len(ordered)-1, int(len(ordered)*.95))], 3),
            'maximum_ms': round(ordered[-1], 3)} if ordered else {'samples': 0}


def rss_mb():
    if sys.platform.startswith('linux'):
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'): return round(int(line.split()[1])/1024, 1)
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        class Memory(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD),
                        *[(name, ctypes.c_size_t) for name in ('peak', 'working', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]]
        memory = Memory(); memory.cb = ctypes.sizeof(memory)
        process = ctypes.windll.kernel32.GetCurrentProcess; process.restype=wintypes.HANDLE; process.argtypes=[]
        info=ctypes.windll.psapi.GetProcessMemoryInfo
        info.argtypes=[wintypes.HANDLE,ctypes.POINTER(Memory),wintypes.DWORD]; info.restype=wintypes.BOOL
        if info(process(), ctypes.byref(memory), memory.cb):
            return round(memory.working/1024**2, 1)
    return None


def run(args):
    from PIL import Image
    from PySide6.QtCore import QSettings, QTimer, Qt
    from PySide6.QtWidgets import QApplication
    from app import Library
    from cache_policy import MAX_BYTES, prune_workspaces, expire_inactive_workspaces
    from desktop import Window, Decoders, STYLE
    import machine

    report = {'platform': platform.platform(), 'profile': machine.PROFILE.describe(),
              'workload': {'metadata_rows': args.rows, 'real_jpegs': 520, 'jpeg_pixels': [640, 400],
                           'decoder_processes': 2, 'manual_preload': 350},
              'limitations': 'Synthetic JPEGs and local temporary storage; real RAW/NAS and physical Windows gestures require separate checks.',
              'checks': {}, 'warnings': []}
    with tempfile.TemporaryDirectory(prefix='photo-sieve-performance-') as temporary:
        base = Path(temporary).resolve(); photos = base/'photos'; photos.mkdir()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(base/'settings'))
        settings = QSettings('PhotoSieve', 'PhotoSieve')
        settings.setValue('preload_count', 350); settings.setValue('warm_count', 0)
        application = QApplication.instance() or QApplication([]); application.setStyleSheet(STYLE)

        def until(predicate, seconds=60):
            deadline = time.monotonic()+seconds
            while time.monotonic()<deadline:
                application.processEvents()
                if predicate(): return
                time.sleep(.001)
            raise AssertionError('Performance audit timed out')

        rows = []
        for i in range(1, 521):
            path = photos/f'{i:06}.jpg'
            Image.new('RGB', (640, 400), (i*7%255, i*11%255, i*19%255)).save(path, quality=88)
            stat = path.stat(); rows.append((i, str(path), stat.st_size, stat.st_mtime_ns))
        rows.extend((i, str(photos/f'{i:06}.jpg'), 1, 1) for i in range(521, args.rows+1))
        library = Library(base/'workspaces'); library.remember('root', str(photos))
        with library.connect() as db:
            db.executemany('INSERT INTO photos(id,path,size,mtime) VALUES (?,?,?,?)', rows)
        del rows

        begin = time.perf_counter(); anchor = 0; count = 0; page_times = []; peak_page = 0
        while True:
            start = time.perf_counter(); page = library.list_photos('pending', anchor, limit=1200)
            page_times.append((time.perf_counter()-start)*1000); peak_page = max(peak_page,len(page))
            if not page: break
            assert all(p['id']>anchor for p in page)
            anchor = page[-1]['id']; count += len(page)
        assert count == args.rows and peak_page<=1200
        report['checks']['pagination'] = {'rows_seen': count, 'maximum_page_rows': peak_page,
                                          'full_traversal_seconds': round(time.perf_counter()-begin,3), **distribution(page_times)}
        start = time.perf_counter(); counts = library.state()['counts']
        assert counts['pending']==args.rows
        report['checks']['count_queries']={'elapsed_ms':round((time.perf_counter()-start)*1000,3),'counts':counts}

        window = Window(library, check_updates=False, decoders=Decoders(2), data_root=base/'workspaces')
        window.resize(1280, 720); window.show()
        stalls=[]; last=[time.perf_counter()]
        def beat():
            now=time.perf_counter(); stalls.append(max(0,(now-last[0]-.01)*1000)); last[0]=now
        heartbeat=QTimer(); heartbeat.setTimerType(Qt.TimerType.PreciseTimer); heartbeat.timeout.connect(beat); heartbeat.start(10)
        try:
            begin=time.perf_counter()
            until(lambda:len(window.cache)>=350 and not window.fetching)
            assert window.ahead_target==350 and len(window.queue)<=2400
            assert len(window.cache)<=window.cache_limit and len(window.thumb_cache)<=64
            report['checks']['preload_350']={'fill_seconds':round(time.perf_counter()-begin,3),
                                            'ready':len(window.cache),'queue_metadata':len(window.queue),
                                            'ram_cache_limit':window.cache_limit,'thumbnail_count':len(window.thumb_cache),
                                            'main_process_rss_mb':rss_mb(),'errors':len(window.image_errors)}
            assert not window.image_errors

            # A cached full preview must regenerate its strip tile without touching the original.
            from unittest.mock import patch
            window.thumb_cache.clear()
            for i in window.strip.visible_rows(): window.strip.item(i).setIcon(window.placeholder)
            begin=time.perf_counter()
            with patch.object(library,'preview_image',side_effect=AssertionError('Unexpected original decode')):
                window.update_strip()
            assert all(window.strip.item(i).data(Qt.ItemDataRole.UserRole) in window.thumb_cache for i in window.strip.visible_rows())
            report['checks']['thumbnail_recovery']={'tiles':len(window.strip.visible_rows()),'total_indexed':window.strip.count(),'elapsed_ms':round((time.perf_counter()-begin)*1000,3),'original_decodes':0}

            initial = [p['id'] for p in window.queue[:80]]; handler=[]; backlog=0; begin=time.perf_counter()
            for i in range(80):
                due=time.perf_counter()+.025
                action='delete' if i%2==0 else 'keep'
                pid=window.queue[0]['id']; start=time.perf_counter(); window.act(action)
                handler.append((time.perf_counter()-start)*1000)
                assert window.queue[0]['id']!=pid, 'A ready review action must advance immediately'
                backlog=max(backlog,window.pending_ops)
                until(lambda:time.perf_counter()>=due,seconds=2)
            until(lambda:not window.pending_ops and not window.inflight)
            counts=library.state()['counts']; assert counts['deleted']==40 and counts['keep']==40
            assert all(p['id'] not in initial for p in window.queue[:16])
            report['checks']['rapid_review']={'operations':80,'deleted':40,'kept':40,
                                               'elapsed_seconds':round(time.perf_counter()-begin,3),'maximum_record_backlog':backlog,
                                               'handler':distribution(handler)}
            begin=time.perf_counter()
            for _ in range(40): window.restore()
            until(lambda:not window.pending_ops)
            assert library.state()['counts']['deleted']==0
            assert all((photos/f'{pid:06}.jpg').is_file() for pid in initial)
            report['checks']['rapid_restore']={'restored':40,'elapsed_seconds':round(time.perf_counter()-begin,3)}

            # Repeated resizes must converge to the final pane size, bounded by the chosen horizon.
            begin=time.perf_counter(); previous_size=window.display_size
            for width,height in [(1450,900),(1280,720),(1600,960),(1450,900)]:
                window.resize(width,height); application.processEvents()
            until(lambda:window.display_size!=previous_size)
            until(lambda:not window.loading and all(window.cache_size.get(p['id'])==window.display_size for p in window.queue[:350]))
            assert len(window.cache)<=window.cache_limit and len(window.thumb_cache)<=64
            report['checks']['resize_recovery']={'elapsed_seconds':round(time.perf_counter()-begin,3),'ready':len(window.cache),
                                                'cache_limit':window.cache_limit,'errors':len(window.image_errors)}

            until(lambda:not window.loading)
            window.clear_cache(); until(lambda:not window.cache_clearing and len(window.cache)>=350)
            assert (library.directory/'library.sqlite3').is_file()
            assert all((photos/f'{pid:06}.jpg').is_file() for pid in initial)
            report['checks']['manual_cache_clear']={'ready_reloaded':len(window.cache),'originals_preserved':True,'records_preserved':library.state()['counts']['keep']==40}

            # Metadata paging and the configurable horizon also work beyond the first 1,200 rows.
            # Tiny already-decoded images isolate paging/RAM scheduling from original-file access.
            with patch.object(library,'preview_image',side_effect=lambda *_:Image.new('RGB',(16,16),(64,128,192))), \
                 patch.object(machine.Profile,'preview_capacity',side_effect=lambda requested,*_:requested):
                window.preload_count=1500; window.display_size=None; window.update_display_size()
                window.cache.clear(); window.cache_size.clear(); window.thumb_cache.clear(); window.prefetch()
                until(lambda:len(window.cache)>=1500 and not window.loading and not window.fetching)
                assert window.ahead_target==1500 and 1500<=len(window.queue)<2700 and len(window.cache)<=window.cache_limit
                report['checks']['preload_beyond_metadata_page']={'target':1500,'ready':len(window.cache),
                                                                 'queue_metadata':len(window.queue),'synthetic_decoded_previews':True}
            window.preload_count=350; window.display_size=None; window.update_display_size(); window.trim_previews()

            # Scale the global-budget test; exercise ordering across multiple workspaces.
            budget_root=base/'budget'; first=budget_root/'cache'; second=budget_root/'libraries'/'other'/'cache'
            first.mkdir(parents=True); second.mkdir(parents=True); cache_files=[]
            for i in range(6):
                path=(first if i%2==0 else second)/f'{i}.jpg';path.write_bytes(b'x'*4096)
                os.utime(path,(time.time()-1000+i,time.time()-1000+i)); cache_files.append(path)
            protected=budget_root/'original.jpg'; protected.write_bytes(b'photo')
            removed,total=prune_workspaces(budget_root,limit=3*4096)
            assert MAX_BYTES==10*1024**3 and removed==5 and total==4096
            assert all(not p.exists() for p in cache_files[:5]) and all(p.exists() for p in cache_files[5:]) and protected.exists()
            report['checks']['global_cache_budget']={'configured_bytes':MAX_BYTES,'scaled_bytes':3*4096,'removed_in_one_pass':removed,'oldest_first':True,'originals_preserved':True}

            # Diagnostic-only legacy indexed housekeeping helper and real records: mature kept previews expire, recent ones survive.
            kept_id=initial[1]; row=library.photo(kept_id)
            library.preview(kept_id,1600)
            old=library.cache_path(Path(row['path']),row['size'],row['mtime'],320)
            fresh=library.cache_path(Path(row['path']),row['size'],row['mtime'],1600)
            assert old.exists() and fresh.exists()
            os.utime(old,(time.time()-300,time.time()-300))
            library.cleanup_processed_cache(grace=120)
            assert not old.exists() and fresh.exists() and Path(row['path']).exists()
            report['checks']['diagnostic_indexed_cleanup']={'expired_preview_removed':True,'recent_preview_retained':True,'original_preserved':True}

            heartbeat.stop(); report['checks']['ui_timer_excess_delay']=distribution(stalls)
            # Report performance changes without encoding this machine's speed into brittle assertions.
            if report['checks']['ui_timer_excess_delay'].get('maximum_ms',0)>100:
                report['warnings'].append('At least one UI timer tick exceeded its 10 ms interval by 100 ms; inspect stage metrics and real-device latency before claiming smoothness.')
        finally:
            heartbeat.stop(); window.close(); application.processEvents()

        # Cost at the end of a 100,000-photo review with only a tiny recent cache remaining.
        with library.connect() as db: db.execute('UPDATE photos SET status=1')
        # Match the app's background maintenance, while the GUI timer remains live.
        import threading
        removed=[]; start=time.perf_counter(); stalls.clear(); last[0]=time.perf_counter(); heartbeat.start(10)
        maintenance=threading.Thread(target=lambda:removed.append(library.cleanup_processed_cache()))
        maintenance.start(); until(lambda:not maintenance.is_alive()); maintenance.join(); heartbeat.stop()
        report['checks']['processed_cleanup_100k']={'rows':args.rows,'elapsed_seconds':round(time.perf_counter()-start,3),
                                                   'removed':removed[0], 'ui_timer_excess_delay':distribution(stalls)}
        if report['checks']['processed_cleanup_100k']['elapsed_seconds']>2:
            report['warnings'].append('Processed-cache expiry traverses all reviewed metadata even when few previews remain; consider storing a source-to-cache index to reduce the per-minute scan.')
        dormant=Library(base/'workspaces'/'libraries'/'dormant'); dormant.remember('root',str(photos))
        with dormant.connect() as db: db.execute('INSERT INTO photos(id,path,size,mtime,status) VALUES (?,?,?,?,1)',(kept_id,row['path'],row['size'],row['mtime']))
        dormant.preview(kept_id,1600)
        dormant_cache=dormant.cache_path(Path(row['path']),row['size'],row['mtime'],320)
        os.utime(dormant_cache,(time.time()-300,time.time()-300))
        assert expire_inactive_workspaces(base/'workspaces',library.directory)==1 and not dormant_cache.exists()
        report['checks']['inactive_workspace_expiry']={'expired_preview_removed':True,'database_preserved':dormant.database.is_file()}
    report['passed']=True
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--rows',type=int,default=100000);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.rows<3000: parser.error('--rows must be at least 3000')
    result=run(args); payload=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output: args.output.write_text(payload+'\n',encoding='utf-8')
    print(payload)
