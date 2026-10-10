"""Non-blocking native updater UI. Photo actions remain available during download."""
from pathlib import Path
import sys
import time
from PySide6.QtCore import QObject, Signal, QTimer, QSettings, QEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QCheckBox, QMessageBox
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from version import APP_VERSION, UPDATE_REPOSITORY
import updater


class UpdateController(QObject):
    progress = Signal(int, int)
    def __init__(self, window):
        super().__init__(window);self.window=window;self.settings=QSettings('PhotoSieve','PhotoSieve')
        self.running=False;self.staged=None;self.last_activity=time.monotonic();self.installing=False
        self.progress.connect(lambda done,total:self.show(f'下载更新：{done*100//max(1,total)}%'))
        window.installEventFilter(self)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)
        self.timer=QTimer(self);self.timer.timeout.connect(self.maybe_install);self.timer.start(1000)
        if getattr(sys,'frozen',False) and self.auto_enabled():QTimer.singleShot(2500,lambda:self.check(False))
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.KeyPress,QEvent.Type.MouseButtonPress,QEvent.Type.Wheel):self.last_activity=time.monotonic()
        return False
    def show(self,message):self.window.update_label.setText(message)
    def auto_enabled(self):return self.settings.value('automatic_updates',True,type=bool)
    def check(self,manual=True):
        if self.window.closed or self.running or self.installing:return
        if self.staged:
            self.show('新版已下载；停止操作 30 秒后自动更新。');return
        try:token=updater.credential_store()
        except Exception as error:self.show(str(error));return
        if not token:
            self.show('私有仓库：请先设置 GitHub 更新授权')
            if manual:self.configure()
            return
        self.running=True;self.show('正在检查 GitHub 最新版本…')
        def failed(message):self.running=False;self.show('检查更新失败：'+message)
        def checked(release):
            self.running=False
            if not release:self.show('当前已是最新版本 '+APP_VERSION);return
            if not getattr(sys,'frozen',False):
                self.show('源码运行请下载 APP 版后使用自动更新');QDesktopServices.openUrl(QUrl(release['page']));return
            self.running=True;self.show('发现 '+release['version']+'，正在下载…')
            def downloaded(root):
                self.running=False;self.staged=Path(root);self.last_activity=time.monotonic()
                self.show('新版已下载。停止操作 30 秒后更新，或点击立即安装。')
                self.window.install_update_button.show()
            self.window.submit(lambda:updater.download_release(release,token,self.progress.emit),downloaded,failed)
        self.window.submit(lambda:updater.check_release(token),checked,failed)
    def maybe_install(self):
        if self.staged and self.auto_enabled() and time.monotonic()-self.last_activity>=30:self.install()
    def install(self):
        w=self.window
        if w.closed or not self.staged or self.installing:return
        if w.busy or w.fetching or w.library.job['running']:
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
    def configure(self):
        dialog=QDialog(self.window);dialog.setWindowTitle('GitHub 更新设置');dialog.resize(620,330)
        layout=QVBoxLayout(dialog);layout.addWidget(QLabel(f'当前版本：{APP_VERSION}\n私有仓库：{UPDATE_REPOSITORY}'))
        instructions=QLabel('请创建 GitHub 细粒度令牌：只选择 photo-sieve 仓库，\nRepository permissions → Contents → Read-only。\n令牌保存在本机 Windows 凭据管理器，不包含在安装包中。')
        instructions.setWordWrap(True);layout.addWidget(instructions)
        link=QPushButton('打开 GitHub 令牌创建页面');link.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://github.com/settings/personal-access-tokens/new')));layout.addWidget(link)
        token=QLineEdit();token.setEchoMode(QLineEdit.EchoMode.Password);token.setPlaceholderText('粘贴 GitHub 授权令牌；留空保留已有授权');layout.addWidget(token)
        automatic=QCheckBox('启动检查更新，下载完成并停止操作 30 秒后自动安装重启');automatic.setChecked(self.auto_enabled());layout.addWidget(automatic)
        def save():
            try:
                if token.text().strip():updater.credential_store(token.text())
                self.settings.setValue('automatic_updates',automatic.isChecked());self.settings.sync();dialog.accept();self.check()
            except Exception as error:QMessageBox.warning(dialog,'无法保存授权',str(error))
        button=QPushButton('保存并检查更新');button.clicked.connect(save);layout.addWidget(button)
        revoke=QPushButton('清除本机 GitHub 授权')
        def clear():
            try:updater.credential_store(delete=True);self.show('GitHub 授权已清除');dialog.accept()
            except Exception as error:QMessageBox.warning(dialog,'无法清除授权',str(error))
        revoke.clicked.connect(clear);layout.addWidget(revoke);dialog.exec()
    def report_previous(self):
        stage=self.settings.value('last_update_stage','')
        if not stage:return
        result=Path(stage)/'result.json'
        if not result.is_file():return
        import json
        try:
            data=json.loads(result.read_text('utf-8'))
            self.show('已更新至 '+data['version'] if data['ok'] else '更新失败，已恢复旧版：'+data.get('error',''))
            self.settings.remove('last_update_stage')
        except (ValueError,OSError):pass
