"""Read-only single-photo view shared by the kept and deleted albums."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap,QShortcut,QKeySequence
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel
from widgets import PhotoView

class AlbumView(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host;self.revision=0;self.photo=None;self.preview=None
        layout=QVBoxLayout(self);layout.setContentsMargins(24,12,24,16);layout.setSpacing(12)
        top=QHBoxLayout();top.addWidget(host.button('← 返回相册',self.back));top.addStretch();self.title=QLabel();self.title.setObjectName('caption');top.addWidget(self.title);layout.addLayout(top)
        self.pane=PhotoView();layout.addWidget(self.pane,1);self.caption=QLabel();self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter);self.caption.setObjectName('caption');layout.addWidget(self.caption)
        self.pane.clicked.connect(self.zoom);self.pane.escape_requested.connect(self.back)
        self.escape=QShortcut(QKeySequence('Esc'),self)
        self.escape.setContext(Qt.ShortcutContext.WindowShortcut)
        self.escape.setEnabled(False)
        self.escape.activated.connect(self.back)
    def open(self,pid):
        self.revision+=1;revision=self.revision;library=self.host.library
        self.photo=None;self.preview=None;self.pane.show_photo(None,message='正在读取照片…');self.caption.clear()
        self.title.setText('保留相册' if self.host.view=='keep' else '已删除相册')
        self.host.stack.setCurrentWidget(self);self.host.sync_shortcut_mode();self.host.place_overlays()
        from desktop import display_image
        dpr=self.devicePixelRatioF();size=self.pane.viewport().size()
        def work():
            photo=library.public(library.photo(pid,include_deleted=True))
            return photo,display_image(library.preview_image(pid,1600),max(320,round(size.width()*dpr)),max(240,round(size.height()*dpr)),(8,8))
        def done(result):
            if revision!=self.revision or library is not self.host.library:return
            self.photo,data=result;self.preview=QPixmap.fromImage(data[0][0]);self.preview.setDevicePixelRatio(dpr)
            self.pane.show_photo(self.photo,self.preview);self.pane.source_width=data[2][0];self.caption.setText(self.photo['name']+' · 单击查看原图，Esc 返回相册')
        def failed(text):
            if revision==self.revision and library is self.host.library:self.caption.setText(text)
        self.host.submit(work,done,failed,self.host.detail_pool)
    def back(self):
        self.revision+=1;self.host.stack.setCurrentIndex(4 if self.host.view=='keep' else 1);self.host.sync_shortcut_mode();self.host.place_overlays()
    def showEvent(self,event):
        super().showEvent(event);self.escape.setEnabled(True)
    def hideEvent(self,event):
        self.escape.setEnabled(False);super().hideEvent(event)
    def zoom(self,*_):
        if not self.photo or not self.pane.pixmap:return
        if self.pane.detail:self.pane.unzoom(lambda:self.pane.show_photo(self.photo,self.preview));return
        self.pane.zoom_at();revision=self.revision;photo=self.photo
        from pathlib import Path
        from desktop import full_qimage
        source=Path(photo['path'] if photo['present'] else photo['backup'])
        def work():return full_qimage(self.host.decoders.full_image(str(source)) if self.host.decoders else __import__('app').build_full_image(str(source)))
        def done(result):
            if revision!=self.revision or not self.pane.detail:return
            pixmap=QPixmap.fromImage(result[0]);pixmap.setDevicePixelRatio(self.devicePixelRatioF());self.pane.install_full_image(pixmap)
            self.caption.setText(photo['name']+' · 原图 100% · 滚轮缩放 / 拖动查看 · Esc 返回相册')
        def failed(text):
            if revision==self.revision and self.photo is photo:self.caption.setText(text)
        self.host.submit(work,done,failed,self.host.detail_pool)
