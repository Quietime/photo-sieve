"""Keep cyclic destruction of Python-owned Qt objects on their GUI thread.

Python's automatic cyclic collector otherwise runs in whichever allocating
thread crosses its threshold, including folder-index and preview workers.
"""
import gc
from PySide6.QtCore import QObject,QTimer,Slot
from PySide6.QtWidgets import QApplication

class GuiCollector(QObject):
    def __init__(self,application):
        super().__init__(application)
        self.was_enabled=gc.isenabled();gc.disable()
        self.timer=QTimer(self);self.timer.setInterval(3000)
        self.timer.timeout.connect(self.collect);self.timer.start()
        application.aboutToQuit.connect(self.stop)
    @Slot()
    def collect(self):
        gc.collect()
    @Slot()
    def stop(self):
        self.timer.stop()
        if self.was_enabled:gc.enable()

def install():
    application=QApplication.instance()
    if application is None:raise RuntimeError('GUI collection requires QApplication')
    if not hasattr(application,'photosieve_collector'):
        application.photosieve_collector=GuiCollector(application)
    return application.photosieve_collector
