"""Real 10/16000-file import with LibRaw sensor decoding, UI progress and isolated workers.

Generated DNG sensor data use .arw names to exercise the RAW route. This validates
library/import/process isolation, not a specific camera's sensor decoder or RAW performance.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from raw_fixture import write_raw

def run(args):
    import crashlog
    import desktop
    from app import Library
    from PySide6.QtCore import QSettings,QTimer
    from PySide6.QtWidgets import QApplication
    from tools.check_performance import rss_mb
    output=args.output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    session=crashlog.install(output.with_name(output.stem+'-logs'))
    report={'mode':args.mode,'raw_files':args.photos,'preload_requested':1200,
        'fixture':'Generated 640x400 DNG CFA sensor data, hardlinked under unique .arw paths',
        'checks':{},'passed':False}
    window=None;code=1
    with tempfile.TemporaryDirectory(prefix='photosieve-large-import-') as temp:
        base=Path(temp).resolve();photos=base/'photos';photos.mkdir()
        template=base/'template.dng';write_raw(template)
        for i in range(args.photos):os.link(template,photos/f'{i:05}.arw')
        (photos/'voice.wav').write_bytes(b'non-photo fixture')
        prefs=QSettings(str(base/'preferences.ini'),QSettings.Format.IniFormat)
        prefs.setValue('preload_count',1200);prefs.setValue('warm_count',0)
        prefs.setValue('gpu_compositing',args.mode=='gpu')
        desktop.configure_compositing(prefs)
        crashlog.install_qt_handler()
        application=QApplication.instance() or QApplication([]);application.setStyleSheet(desktop.STYLE)
        delays=[];last=[time.monotonic()];heartbeat=QTimer();heartbeat.setInterval(16)
        def beat():
            now=time.monotonic();delays.append(max(0,now-last[0]-.016)*1000);last[0]=now
        heartbeat.timeout.connect(beat);heartbeat.start()
        def until(fn,timeout=120):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                application.processEvents()
                if fn():return
                time.sleep(.005)
            raise AssertionError('Large import check timed out')
        with patch('desktop.QSettings',return_value=prefs),patch('update_ui.QSettings',return_value=prefs):
            try:
                library=Library(base/'workspaces');window=desktop.Window(library,check_updates=False,
                    decoders=desktop.Decoders(2),start_empty=True,data_root=base/'workspaces')
                screen=application.primaryScreen().availableGeometry()
                window.resize(min(1280,screen.width()-40),min(800,screen.height()-60))
                window.move(screen.center()-window.rect().center());window.show()
                until(lambda:window.state_data is not None)
                start=time.monotonic();window.scan(photos)
                assert window.stack.currentIndex()==3 and window.importing
                assert not window.loading and not window.warming
                report['checks']['dedicated_import_page']=True
                window.grab().save(str(output.with_name(output.stem+'-opening.png')))
                seen_progress=[];deadline=time.monotonic()+120
                while window.importing and time.monotonic()<deadline:
                    application.processEvents()
                    job=window.library.job
                    if job['running']:
                        assert not window.loading and not window.warming,'No original-photo decode while indexing'
                    if window.import_page.bar.maximum()==args.photos:
                        seen_progress.append(window.import_page.bar.value())
                        if len(seen_progress)==1:window.grab().save(str(output.with_name(output.stem+'-progress.png')))
                    time.sleep(.005)
                assert not window.importing
                assert window.counts['all']==args.photos
                assert window.library.job['done']==args.photos and window.library.job['total']==args.photos
                report['checks']['import_seconds']=round(time.monotonic()-start,3)
                report['checks']['true_total_and_completed_count']=True
                report['checks']['no_decode_during_import']=True
                report['checks']['progress_samples']=len(seen_progress)
                until(lambda:window.photos[0].pixmap is not None and window.photos[1].pixmap is not None)
                assert len(window.queue)<=2400 and window.preload_count==1200
                assert window.ahead_target<=1200
                report['checks']['bounded_metadata_and_preload']=True
                report['effective_preload']=window.ahead_target
                window.focus_left();until(lambda:window.photos[0].full_resolution)
                assert window.photos[0].pixmap.width()==640
                report['checks']['actual_libraw_full_resolution_in_child']=True
                window.reset_zoom();window.grab().save(str(output.with_name(output.stem+'-review.png')))
                report['gpu_enabled']=desktop.COMPOSITING['gpu']
                if args.mode=='gpu' and os.name=='nt':assert report['gpu_enabled']
                report['rss_mb']=rss_mb()
                report['maximum_ui_excess_delay_ms']=round(max(delays,default=0),1)
                report['passed']=True;code=0
            except BaseException as error:
                report['error']=repr(error);crashlog.LOGGER.exception('Large import verification failed')
                if window:window.grab().save(str(output.with_name(output.stem+'-failure.png')))
            finally:
                heartbeat.stop()
                if window:
                    until(lambda:not window.library.job['running'])
                    window.busy=False;window.importing=False;window.prompt_on_close=False;window.close()
                session.finish(code)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
    return code

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--photos',type=int,default=16000)
    parser.add_argument('--mode',choices=('cpu','gpu'),default='cpu')
    parser.add_argument('--output',type=Path,default=Path('large-import-ci.json'))
    raise SystemExit(run(parser.parse_args()))
