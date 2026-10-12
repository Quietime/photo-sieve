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
from PySide6.QtWidgets import QApplication, QMessageBox
from version import APP_VERSION
import updater

ASK_IDLE=2       # seconds without input before the offer appears, so a held key is never cut off by a dialog
INSTALL_IDLE=3   # seconds without input before an accepted update closes the APP, installs and restarts it


class UpdateController(QObject):
    progress = Signal(int, int)
    def __init__(self, window, check_on_start=True):
        super().__init__(window);self.window=window;self.settings=QSettings('PhotoSieve','PhotoSieve')
        self.running=False;self.staged=None;self.offer=None;self.asking=False;self.installing=False
        self.last_activity=time.monotonic();self.status='';self.tidy_tries=0;self.failed_version=None
        self.progress.connect(lambda done,total:self.show(f'正在下载更新：{done*100//max(1,total)}%'))
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(1000)
        if check_on_start and getattr(sys,'frozen',False):
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
        if self.auto_enabled():self.check(False)
    def check(self,manual=True):
        if self.window.closed or self.running or self.installing or self.asking:return
        if self.staged:
            self.show('新版已下载；停止操作 3 秒后自动重启完成更新。',manual);return
        token=''  # Public releases never use previously saved credentials.
        self.running=True;self.show('正在检查 GitHub 最新版本…')
        def failed(message):self.running=False;self.show('检查更新失败：'+message,manual)
        def checked(release):
            self.running=False
            if not release:self.show('当前已是最新版本 '+APP_VERSION,manual);return
            if not getattr(sys,'frozen',False):
                self.show('源码运行请下载 APP 版后使用自动更新',manual);QDesktopServices.openUrl(QUrl(release['page']));return
            if self.asking or self.staged or self.installing:return   # a prompt or an update is already under way
            if not manual and release['version']==self.failed_version:
                self.show('新版本 '+release['version']+' 上次未能安装，可在 ⋯ 中检查更新重试');return
            self.offer=release;self.show('发现新版本 '+release['version'])
            if manual:self.offer_now()   # asked for: answer at once; otherwise tick waits for a pause in review
        self.window.submit(lambda:updater.check_release(token),checked,failed)
    def offer_now(self):
        if self.running or self.staged or self.asking or self.installing:return   # the offer waits for these
        release,self.offer=self.offer,None
        if not release or self.window.closed:return
        self.asking=True
        try:accepted=self.ask(release)
        finally:self.asking=False
        if accepted:self.download(release)
        else:self.show('已暂缓更新到 '+release['version']+'，可随时在 ⋯ 中检查更新',True)
    def ask(self,release):
        box=QMessageBox(self.window);box.setWindowTitle('发现新版本')
        box.setText(f"照片快筛 {release['version']} 已发布（当前 {APP_VERSION}）")
        # The first few lines of the release notes, shortened; Qt's own details button would be labelled in English.
        lines=[line.strip() for line in str(release.get('notes','')).splitlines() if line.strip()]
        summary='\n'.join(line if len(line)<=46 else line[:45]+'…' for line in lines[:5])+('\n…' if len(lines)>5 else '')
        box.setInformativeText('下载后自动安装并重启。照片、筛选记录和设置都会保留，装好后自动删除下载的安装包。'+('\n\n'+summary if summary else ''))
        box.setStyleSheet('QLabel#qt_msgbox_label, QLabel#qt_msgbox_informativelabel { min-width: 420px; }')
        accept=box.addButton('下载并更新',QMessageBox.ButtonRole.AcceptRole)
        later=box.addButton('以后再说',QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(accept);box.setEscapeButton(later);box.exec()
        return box.clickedButton() is accept
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
        if QApplication.activeModalWidget() or QApplication.activePopupWidget():
            self.touch();return   # an export dialog, folder picker or menu is open: wait until it closes and review pauses
        if self.offer and not self.running and not w.busy and self.idle_seconds()>=ASK_IDLE:self.offer_now()
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
            self.show('已更新至 '+data['version'] if data['ok'] else '更新失败，已恢复旧版：'+data.get('error',''),True)
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
