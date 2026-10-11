"""Non-blocking native updater UI. Photo actions remain available during download."""
from pathlib import Path
import sys
import time
import ctypes
import os
from PySide6.QtCore import QObject, Signal, QTimer, QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from version import APP_VERSION
import updater


class UpdateController(QObject):
    progress = Signal(int, int)
    def __init__(self, window, check_on_start=True):
        super().__init__(window);self.window=window;self.settings=QSettings('PhotoSieve','PhotoSieve')
        self.running=False;self.staged=None;self.last_activity=time.monotonic();self.installing=False;self.status=''
        self.progress.connect(lambda done,total:self.show(f'下载更新：{done*100//max(1,total)}%'))
        self.timer=QTimer(self);self.timer.timeout.connect(self.maybe_install);self.timer.start(1000)
        if check_on_start and getattr(sys,'frozen',False) and self.auto_enabled():QTimer.singleShot(2500,lambda:self.check(False))
    def touch(self):
        # Called from the window's own actions. An application-wide event filter would route every Qt event
        # through Python, which cost the interface thread about 8% while keys were held.
        self.last_activity=time.monotonic()
    def idle_seconds(self):
        idle=time.monotonic()-self.last_activity
        if os.name=='nt':
            # Mouse, wheel and typing anywhere count too: GetLastInputInfo gives the system-wide idle time.
            class LASTINPUTINFO(ctypes.Structure):_fields_=[('cbSize',ctypes.c_uint),('dwTime',ctypes.c_uint)]
            info=LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
            try:
                if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                    idle=min(idle,((ctypes.windll.kernel32.GetTickCount()-info.dwTime)&0xFFFFFFFF)/1000)
            except (AttributeError,OSError):pass
        return idle
    def show(self,message,toast=False):
        # Status lives in the ⋯ menu; only results the user asked about surface as a toast.
        self.status=message
        if toast:self.window.toast.show_message(message,'info',3200)
    def auto_enabled(self):return self.settings.value('automatic_updates',True,type=bool)
    def check(self,manual=True):
        if self.window.closed or self.running or self.installing:return
        if self.staged:
            self.show('新版已下载；停止操作 30 秒后自动更新。',manual);return
        token=''  # Public releases never use previously saved credentials.
        self.running=True;self.show('正在检查 GitHub 最新版本…')
        def failed(message):self.running=False;self.show('检查更新失败：'+message,manual)
        def checked(release):
            self.running=False
            if not release:self.show('当前已是最新版本 '+APP_VERSION,manual);return
            if not getattr(sys,'frozen',False):
                self.show('源码运行请下载 APP 版后使用自动更新',manual);QDesktopServices.openUrl(QUrl(release['page']));return
            self.running=True;self.show('发现 '+release['version']+'，正在下载…')
            def downloaded(root):
                self.running=False;self.staged=Path(root);self.last_activity=time.monotonic()
                self.show('新版 '+release['version']+' 已下载，空闲 30 秒后更新')
                self.window.update_ready()
            self.window.submit(lambda:updater.download_release(release,token,self.progress.emit),downloaded,failed)
        self.window.submit(lambda:updater.check_release(token),checked,failed)
    def maybe_install(self):
        if self.staged and self.auto_enabled() and self.idle_seconds()>=30:self.install()
    def install(self):
        w=self.window
        if w.closed or not self.staged or self.installing:return
        if w.busy or w.fetching or w.pending_ops or w.library.job['running']:
            self.show('新版已下载，等待扫描或文件操作完成后更新');return
        self.installing=True;w.busy=True;w.centralWidget().setEnabled(False);self.show('准备更新；记录已保存，正在关闭程序…')
        w.save_cursor()
        arguments=['--data-dir',str(w.library.directory),'--cache-mb',str(w.library.cache_limit//1024//1024)]
        def failed(message):
            self.installing=False;w.busy=False;w.centralWidget().setEnabled(True);self.show('更新未安装：'+message)
        def ready(stage):
            self.settings.setValue('last_update_stage',str(stage));self.settings.sync()
            w.busy=False;w.close()
        w.submit(lambda:updater.launch_helper(self.staged,Path(sys.executable).parent,arguments),ready,failed)
    def set_automatic(self,enabled):
        self.settings.setValue('automatic_updates',bool(enabled));self.settings.sync()
        if enabled:self.check()
    def report_previous(self):
        stage=self.settings.value('last_update_stage','')
        if not stage:return
        result=Path(stage)/'result.json'
        if not result.is_file():return
        import json
        try:
            data=json.loads(result.read_text('utf-8'))
            self.show('已更新至 '+data['version'] if data['ok'] else '更新失败，已恢复旧版：'+data.get('error',''),True)
            self.settings.remove('last_update_stage')
        except (ValueError,OSError):pass
