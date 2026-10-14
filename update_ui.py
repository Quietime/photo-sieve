"""Native updater UI: ask once a new version is out, then download, install, restart and clean up by itself.
Photo review stays available while a download runs."""
from pathlib import Path
import json
import sys
import time
import ctypes
import os
from PySide6.QtCore import QObject, Signal, QTimer, QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication
from update_offer import UpdateOfferDialog
from version import APP_VERSION
import updater

CHECK_INTERVAL=2*60
CHECK_RETRY=60
CHECK_RETRY_MAX=15*60
DEFER_SECONDS=30*60
OFFER_ACTION_PAUSE=.15  # brief app-action guard; mouse movement never delays an offer
INSTALL_IDLE=3   # seconds without input before an accepted update closes the APP, installs and restarts it


class UpdateController(QObject):
    progress = Signal(int, int)
    def __init__(self, window, check_on_start=True):
        super().__init__(window);self.window=window;self.settings=QSettings('PhotoSieve','PhotoSieve')
        self.running=False;self.staged=None;self.offer=None;self.offer_immediate=False;self.asking=False;self.installing=False
        self.last_activity=time.monotonic();self.status='';self.tidy_tries=0;self.failed_version=None
        self.automatic_runtime=bool(check_on_start and getattr(sys,'frozen',False))
        self.next_check=time.monotonic()+2.5;self.check_failures=0;self.offer_manual=False
        self.progress.connect(lambda done,total:self.show(f'正在下载更新：{done*100//max(1,total)}%'))
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(1000)
        if self.automatic_runtime:
            QTimer.singleShot(2500,self.startup_check);QTimer.singleShot(4000,self.tidy)
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
    def startup_check(self):
        if self.auto_enabled():self.check(False,startup=True)
    def _check_finished(self,failed=False):
        self.running=False
        self.check_failures=self.check_failures+1 if failed else 0
        delay=min(CHECK_RETRY_MAX,CHECK_RETRY*2**min(4,max(0,self.check_failures-1))) if failed else CHECK_INTERVAL
        self.next_check=time.monotonic()+delay
    def _deferred(self,version):
        return (self.settings.value('deferred_update_version','')==version
                and self.settings.value('deferred_update_until',0,type=float)>time.time())
    def _offer_safe(self):
        w=self.window
        if w.busy or getattr(w,'importing',False) or getattr(w,'pending_ops',0) or getattr(w,'closing',False):return False
        if getattr(w,'library',None) and w.library.job['running']:return False
        if QApplication.mouseButtons():return False
        if time.monotonic()-self.last_activity<OFFER_ACTION_PAUSE:return False
        if os.name=='nt':
            try:
                # Detect a held shortcut including the initial autorepeat delay;
                # GetLastInputInfo would also count mouse movement and postpone
                # updates forever while somebody was actively using the app.
                if any(ctypes.windll.user32.GetAsyncKeyState(key)&0x8000 for key in range(8,255)):return False
            except (AttributeError,OSError):pass
        return True
    def check(self,manual=True,startup=False):
        if self.window.closed or self.running or self.installing or self.asking:return
        if self.offer:
            if manual:self.offer_now()
            return
        if self.staged:
            self.show('新版已下载；停止操作 3 秒后自动重启完成更新。',manual);return
        token=''  # Public releases never use previously saved credentials.
        self.running=True;self.show('正在检查 GitHub 最新版本…')
        def failed(message):self._check_finished(True);self.show('检查更新失败：'+message,manual)
        def checked(release):
            self._check_finished()
            if self.window.closed or not manual and not self.auto_enabled():return
            if not release:self.show('当前已是最新版本 '+APP_VERSION,manual);return
            if not getattr(sys,'frozen',False):
                self.show('源码运行请下载 APP 版后使用自动更新',manual);QDesktopServices.openUrl(QUrl(release['page']));return
            if self.asking or self.staged or self.installing:return   # a prompt or an update is already under way
            if not manual and release['version']==self.failed_version:
                self.show('新版本 '+release['version']+' 上次未能安装，可在 ⋯ 中检查更新重试');return
            if not manual and self._deferred(release['version']):
                self.show('新版 '+release['version']+' 已暂缓，可在 ⋯ 中随时更新');return
            self.offer=release;self.offer_manual=manual;self.offer_immediate=True;self.show('发现新版本 '+release['version'])
            if manual:self.offer_now()
            else:self.tick()   # Every discovery surfaces as soon as app actions are safe.
        self.window.submit(lambda:updater.check_release(token),checked,failed)
    def offer_now(self):
        if self.running or self.staged or self.asking or self.installing:return   # the offer waits for these
        release,self.offer=self.offer,None;self.offer_immediate=False
        if not release or self.window.closed:return
        self.asking=True
        try:accepted=self.ask(release)
        finally:self.asking=False
        if accepted:self.download(release)
        else:
            self.settings.setValue('deferred_update_version',release['version']);self.settings.setValue('deferred_update_until',time.time()+DEFER_SECONDS);self.settings.sync()
            self.show('已暂缓更新到 '+release['version']+'，可随时在 ⋯ 中检查更新',True)
    def ask(self,release):
        dialog=UpdateOfferDialog(release,self.window)
        return dialog.exec()==UpdateOfferDialog.DialogCode.Accepted
    def download(self,release):
        if self.window.closed or self.running or self.staged or self.installing:return
        self.running=True;self.show('正在后台下载 '+release['version']+'，可以继续筛选',True)
        def failed(message):self.running=False;self.show('下载更新失败：'+message.rstrip('。')+'。可在 ⋯ 中重新检查更新',True)
        def downloaded(root):
            self.running=False;self.staged=Path(root)
            self.show('新版 '+release['version']+' 已下载，停止操作 3 秒后自动重启完成更新')
            self.window.update_ready()
        self.window.submit(lambda:updater.download_release(release,'',self.progress.emit),downloaded,failed)
    def tick(self):
        w=self.window
        if w.closed or self.asking or self.installing:return
        if (self.automatic_runtime and self.auto_enabled() and not self.running and not self.offer and not self.staged
                and time.monotonic()>=self.next_check):
            self.check(False)
        panel=getattr(w,'more_panel',None)
        if QApplication.activeModalWidget() or QApplication.activePopupWidget() or (panel is not None and panel.isVisible()):
            self.touch();return   # an export dialog, folder picker or menu is open: wait until it closes and review pauses
        if self.offer and not self.running and self._offer_safe():self.offer_now()
        elif self.staged and self.idle_seconds()>=INSTALL_IDLE:self.install()
    def install(self):
        w=self.window
        if w.closed or not self.staged or self.installing:return
        if w.busy or w.fetching or w.pending_ops or w.library.job['running']:
            self.show('新版已下载，等待扫描或文件操作完成后更新');return
        self.installing=True;w.busy=True;w.centralWidget().setEnabled(False);self.show('准备更新；记录已保存，正在关闭程序…')
        w.save_cursor()
        arguments=['--data-dir',str(w.library.directory),'--cache-mb',str(w.library.cache_limit//1024//1024)]
        def failed(message):
            self.installing=False;w.busy=False;w.centralWidget().setEnabled(True);self.show('更新未安装：'+message,True)
            self.staged=None;w.badge.hide();w.submit(updater.cleanup_updates,fail=lambda _:None)   # not retried every few seconds
        def ready(stage):
            self.settings.setValue('last_update_stage',str(stage));self.settings.sync()
            w.busy=False;w.close()
        w.submit(lambda:updater.launch_helper(self.staged,Path(sys.executable).parent,arguments),ready,failed)
    def set_automatic(self,enabled):
        self.settings.setValue('automatic_updates',bool(enabled));self.settings.sync()
        if enabled:self.check()
        elif not self.offer_manual:self.offer=None
    def report_previous(self):
        stage=self.settings.value('last_update_stage','')
        if not stage:return
        result=Path(stage)/'result.json'
        if not result.is_file():
            # The installer may still be finishing; after ten minutes it never will, and tidy may clear the stage.
            try:fresh=time.time()-Path(stage).stat().st_mtime<600
            except OSError:fresh=False
            if not fresh:self.settings.remove('last_update_stage')
            return
        try:
            data=json.loads(result.read_text('utf-8'))
            if data.get('restart_error'):
                self.show('已安装新版；上次自动启动失败：'+data['restart_error'],True)
            else:self.show('已更新至 '+data['version'] if data['ok'] else '更新失败，已恢复旧版：'+data.get('error',''),True)
            if not data['ok']:self.failed_version=data['version']
        except (ValueError,OSError,KeyError,TypeError):pass
        self.settings.remove('last_update_stage')
    def tidy(self):
        """Delete what updates leave behind: the unpacked new version, the old version's backup and any download
        that never finished. The installer may still be exiting when the new version starts, so retry a few times.
        A download in progress holds updater.STAGING, so cleanup leaves it alone and is simply tried again."""
        if self.window.closed or self.installing:return
        keep=[self.settings.value('last_update_stage','') or None,self.staged and self.staged.parent.parent]
        def clean():   # thousands of files: off the interface thread
            updates=updater.cleanup_updates(*keep)
            return updater.cleanup_backups(Path(sys.executable).parent) and updates
        def done(clean):
            if not clean and self.tidy_tries<6:self.tidy_tries+=1;QTimer.singleShot(5000,self.tidy)
        self.window.submit(clean,done,lambda _:None)
