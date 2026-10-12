import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import gc
import threading
import time
import unittest
from PySide6.QtCore import QObject,QThread,QTimer
from PySide6.QtWidgets import QApplication
from app import Library
from desktop import Window,Completion
import gui_runtime

application=QApplication.instance() or QApplication([])

class GuiRuntimeTest(unittest.TestCase):
    def test_allocating_on_workers_cannot_collect_a_gui_object_cycle(self):
        collector=gui_runtime.install();seen=[];gui=threading.get_ident()
        class Owned(QObject):
            def __init__(self):super().__init__();self.cycle=self
            def __del__(self):seen.append(threading.get_ident())
        item=Owned();del item
        def allocate():
            for _ in range(8):[threading.RLock() for _ in range(4096)]
        worker=threading.Thread(target=allocate);worker.start();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertFalse(gc.isenabled())
        self.assertEqual(seen,[],'Qt cycle cannot be finalized by allocating worker')
        collector.collect()
        self.assertEqual(seen,[gui])

    def test_completed_tasks_dispatch_to_explicit_gui_receiver(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            window=Window(Library(Path(temp)),check_updates=False,start_empty=True)
            seen=[];gui=threading.get_ident()
            window.submit(lambda:threading.get_ident(),lambda worker:seen.append((worker,threading.get_ident())))
            deadline=time.monotonic()+5
            while not seen and time.monotonic()<deadline:application.processEvents();time.sleep(.01)
            self.assertEqual(len(seen),1);self.assertNotEqual(seen[0][0],gui);self.assertEqual(seen[0][1],gui)
            window.close();application.processEvents()

    def test_nonfatal_hang_produces_thread_stacks_without_a_crash(self):
        from pathlib import Path
        import subprocess,sys,tempfile
        with tempfile.TemporaryDirectory() as temp:
            code="import time,crashlog;from PySide6.QtWidgets import QApplication;a=QApplication([]);s=crashlog.install(%r,monitor=False);crashlog.install_gui_watchdog(a,timeout=.15);time.sleep(.35);s.finish(0)" % temp
            subprocess.run([sys.executable,'-c',code],check=True,timeout=10)
            fault=next(Path(temp).glob('session-*/native-fault.log')).read_text()
            self.assertIn('Timeout',fault);self.assertIn('most recent call first',fault)
