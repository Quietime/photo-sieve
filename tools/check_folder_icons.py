"""Exercise the real folder chooser against 10 and 16,000 hidden RAW files.

The files are deliberately not images: this checks selection and icon lookup, not
RAW decoding. Qt's Directory/ShowDirsOnly view still requests information for
hidden files; the app's provider must never pass their real paths to the native
shell icon/type provider. Run on Windows for physical Qt/Windows timing:

python tools/check_folder_icons.py --output folder-icons-ci.json
QT_QPA_PLATFORM=offscreen python tools/check_folder_icons.py --output folder-icons.json
"""
import argparse
import gc
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def timing(values):
    samples=sorted(values)
    if not samples: return {'samples':0}
    return {'samples':len(samples),'median_ms':round(statistics.median(samples),3),
            'p95_ms':round(samples[min(len(samples)-1,int(len(samples)*.95))],3),
            'maximum_ms':round(samples[-1],3)}


def run(output):
    from PySide6.QtCore import QFileInfo,QTimer,Qt,QCoreApplication,QEvent,QDir
    from PySide6.QtWidgets import QApplication,QFileDialog,QFileIconProvider,QFileSystemModel
    from ui_shell import RoundedFileDialog
    from theme import STYLE
    application=QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False); application.setStyleSheet(STYLE)
    base_icon=QFileIconProvider.icon
    report={'platform':platform.platform(),'qt_platform':application.platformName(),'cases':[]}
    forbidden=[]; gui_thread=threading.get_ident()

    def guarded_icon(provider,argument):
        if isinstance(argument,QFileInfo):
            forbidden.append({'method':'icon','path':argument.absoluteFilePath()})
            raise AssertionError('Real file path passed to native shell icon lookup')
        return base_icon(provider,argument)

    def guarded_type(provider,argument):
        forbidden.append({'method':'type','path':argument.absoluteFilePath()})
        raise AssertionError('Real file path passed to native shell type lookup')

    def until(predicate,seconds=30):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            application.processEvents()
            if predicate():return
            time.sleep(.001)
        raise AssertionError('Folder chooser did not finish enumerating its test directory')

    with tempfile.TemporaryDirectory(prefix='photosieve-folder-icons-') as temporary:
        base=Path(temporary).resolve()
        for count in (10,16000):
            folder=base/f'RAW-{count}'; folder.mkdir()
            for i in range(count):(folder/f'camera_{i:05}.ARW').write_bytes(b'NOT RAW: ICON TEST ONLY')
            (folder/'a real subfolder').mkdir()
            callbacks=[]; raw_icon_keys=[]; timer_excess=[]; last=[time.perf_counter()]
            def beat():
                now=time.perf_counter();timer_excess.append(max(0,(now-last[0]-.01)*1000));last[0]=now
            timer=QTimer();timer.setTimerType(Qt.TimerType.PreciseTimer);timer.timeout.connect(beat)
            start=time.perf_counter();timer.start(10)
            with patch.object(QFileIconProvider,'icon',guarded_icon),patch.object(QFileIconProvider,'type',guarded_type):
                dialog=RoundedFileDialog(None,'选择照片文件夹',str(folder))
                dialog.setFileMode(QFileDialog.FileMode.Directory);dialog.setOption(QFileDialog.Option.ShowDirsOnly)
                dialog.setFilter(QDir.Filter.Dirs|QDir.Filter.NoDotAndDotDot)
                provider=dialog.iconProvider()
                assert type(provider).__module__!='PySide6.QtWidgets','Folder chooser must install its safe generic provider'
                provider_type=type(provider); own_icon=provider_type.icon
                generic_file_key=provider.icon(QFileIconProvider.IconType.File).cacheKey()
                def tracked_icon(instance,argument):
                    icon=own_icon(instance,argument)
                    if isinstance(argument,QFileInfo):
                        if argument.suffix().casefold()=='arw' and Path(argument.absoluteFilePath()).parent==folder:
                            callbacks.append((argument.fileName(),threading.get_ident()==gui_thread))
                            raw_icon_keys.append(icon.cacheKey())
                    return icon
                models=dialog.findChildren(QFileSystemModel)
                assert models,'Actual QFileSystemModel must be exercised'
                loaded=[]
                for model in models:model.directoryLoaded.connect(lambda path:loaded.append(Path(path).resolve()))
                with patch.object(provider_type,'icon',tracked_icon):
                    dialog.show()
                    until(lambda:folder in loaded and len(callbacks)>=count)
                    # Include final paint/layout and pending heartbeat deliveries.
                    stop=time.monotonic()+.05;until(lambda:time.monotonic()>=stop)
                    timer.stop()
                model=next(model for model in models if Path(model.rootPath()).resolve()==folder)
                folder_index=model.index(str(folder))
                until(lambda:model.rowCount(folder_index)>=1)
                entries=[model.fileName(model.index(i,0,folder_index)) for i in range(model.rowCount(folder_index))]
                assert not any(name.lower().endswith('.arw') for name in entries),'RAW files must stay hidden in folder selection'
                assert 'a real subfolder' in entries,'Subfolders remain navigable'
                assert not forbidden,'Native real-path lookups are forbidden'
                assert raw_icon_keys and all(key==generic_file_key for key in raw_icon_keys),'Every hidden RAW file uses the same cached generic icon'
                report['cases'].append({'raw_files':count,'hidden_raw_icon_callbacks':len(callbacks),
                                        'raw_callbacks_on_gui_thread':all(on_gui for _,on_gui in callbacks),
                                        'cached_generic_file_icon':True,
                                        'native_real_path_lookups':0,'visible_entries':entries,
                                        'ready_seconds':round(time.perf_counter()-start,3),
                                        'gui_timer_excess_delay':timing(timer_excess)})
                dialog.close();dialog.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
                application.processEvents();del dialog;del provider;gc.collect()
    report['ok']=True
    report['limitations']='Synthetic hidden files measure the folder chooser, not RAW decoding or remote-storage throughput; native DLL crashes require Windows crash logs.'
    if output: output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path)
    run(parser.parse_args().output)
