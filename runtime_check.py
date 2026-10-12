"""Hidden release verification: the *normal frozen entry* and real QApplication.exec.

Called only with developer CLI arguments; all photos and installation paths are
supplied by tools/check_frozen_runtime.py's temporary test installation.
"""
import json
from pathlib import Path
import threading
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ui_shell import RoundedFileDialog
import crashlog


def install(window,output,folders):
    output=Path(output);folders=[Path(p).resolve() for p in folders]
    app=QApplication.instance();started=time.monotonic();gui=threading.get_ident()
    report={'ok':False,'event_loop':'QApplication.exec','folders':[], 'gui_ticks':0}
    state={'index':0,'opening':False,'folder':None,'deadline':time.monotonic()+150}
    timer=QTimer(window);timer.setInterval(40)
    window.prompt_on_close=False;window.updates.timer.stop();window.warm_count=0
    def finish(error=None):
        timer.stop()
        if error:report['error']=repr(error)
        else:report['ok']=True
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        report['log_directory']=str(crashlog.SESSION.directory)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if error:
            crashlog.LOGGER.error('Frozen normal-runtime check failed: %s',error)
            # Exit check failures without synchronously waiting for a stuck task.
            app.exit(1)
        else:window.close()
    def accept_picker():
        for dialog in app.topLevelWidgets():
            if isinstance(dialog,RoundedFileDialog) and dialog.isVisible():
                dialog.setDirectory(str(state['folder']))
                dialog.accept();return
        QTimer.singleShot(50,accept_picker)
    def open_next():
        state['opening']=True;state['folder']=folders[state['index']];state['last_target']=None
        QTimer.singleShot(150,accept_picker)
        # This actually opens and closes the production folder picker; the
        # earlier test suite bypassed this step with window.scan directly.
        window.choose_source()
    def tick():
        try:
            assert threading.get_ident()==gui
            report['gui_ticks']+=1
            if time.monotonic()>state['deadline']:raise TimeoutError('Normal frozen import did not finish')
            if not state['opening']:
                if window.state_data is not None:open_next()
                return
            if window.importing or window.busy or window.library.job['running'] or not window.queue:return
            if window.library.meta('root')!=str(state['folder']):return
            if any(p.pixmap is None or not p.photo or not Path(p.photo['path']).is_relative_to(state['folder']) for p in window.photos):return
            assert window.counts['all']==len(list(state['folder'].glob('*.arw')))
            # The full inventory comes back asynchronously, separately from
            # cached pane images, especially when reopening a large workspace.
            if window.strip.count()!=window.counts['all']:return
            if state['last_target'] is None:
                state['last_target']=window.strip.item(window.strip.count()-2).data(256)
                bar=window.strip.horizontalScrollBar();bar.setValue(bar.maximum())
                app.processEvents()
                from PySide6.QtTest import QTest
                from PySide6.QtCore import Qt
                item=window.strip.item(window.strip.count()-2)
                QTest.mouseClick(window.strip.viewport(),Qt.MouseButton.LeftButton,pos=window.strip.visualItemRect(item).center())
                return
            if window.queue[0]['id']!=state['last_target']:return

            row={'folder':str(state['folder']),'photos':window.counts['all'],'rendered_pair':True,'all_photos_indexed':True,'scroll_and_distant_jump':True}
            report['folders'].append(row)
            window.grab().save(str(output.with_name(output.stem+f'-{state["index"]}.png')))
            state['index']+=1;state['opening']=False
            if state['index']==len(folders):finish()
        except BaseException as exc:
            crashlog.LOGGER.exception('Frozen normal-runtime verification failed')
            finish(exc)
    timer.timeout.connect(tick);timer.start()
    window.runtime_test_timer=timer
