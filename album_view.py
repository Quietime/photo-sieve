"""Read-only single-photo view shared by the kept and deleted albums."""
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QPixmap,QShortcut,QKeySequence,QTransform
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel
from widgets import PhotoView
import theme

class AlbumView(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host;self.revision=0;self.photo=None;self.preview=None;self.gallery_view=None;self.background_press=None;self.background_dragged=False
        self.transition=None;self.returning=False;self.source_width=0;self.full_preview=None;self.full_loading=None
        layout=QVBoxLayout(self);layout.setContentsMargins(24,12,24,16);layout.setSpacing(12)
        top=self.top_bar=QHBoxLayout();top.addWidget(host.button('← 返回相册',self.back));top.addStretch();self.title=QLabel();self.title.setObjectName('caption');top.addWidget(self.title);layout.addLayout(top)
        self.pane=PhotoView();layout.addWidget(self.pane,1);self.caption=QLabel();self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter);self.caption.setObjectName('caption');layout.addWidget(self.caption)
        self.pane.clicked.connect(self.zoom);self.pane.background_clicked.connect(self.back);self.pane.escape_requested.connect(self.back)
        self.escape=QShortcut(QKeySequence('Esc'),self)
        self.escape.setContext(Qt.ShortcutContext.WindowShortcut)
        self.escape.setEnabled(False)
        self.escape.activated.connect(self.back)
    def open(self,pid):
        self.stop_transition();self.full_preview=None;self.full_loading=None;self.source_width=0
        self.revision+=1;revision=self.revision;library=self.host.library
        self.gallery_view=self.host.view
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
            self.source_width=data[2][0];self.reset_preview();self.present()
        def failed(text):
            if revision==self.revision and library is self.host.library:self.caption.setText(text)
        self.host.submit(work,done,failed,self.host.detail_pool)
    def back(self):
        if self.returning:return
        self.stop_transition();self.returning=True;self.pane.stop_zoom();self.pane.pending_full=None;self.background_press=None
        if not self.pane.item:self.finish_back();return
        self.pane.detail=False
        self.transition_to(self.pane.fit_scale()*.94,0.0,240,self.finish_back)

    def finish_back(self):
        self.revision+=1;self.stop_transition();self.full_preview=None;self.full_loading=None
        self.pane.show_photo(None);self.photo=None;self.preview=None;self.source_width=0
        self.host.stack.setCurrentIndex(4 if (self.gallery_view or self.host.view)=='keep' else 1);self.host.sync_shortcut_mode();self.host.place_overlays()

    def stop_transition(self):
        if self.transition:
            motion,self.transition=self.transition,None;motion.stop()
        self.returning=False
        if self.pane.item:self.pane.item.setOpacity(1.0)

    def transition_to(self,target,opacity,duration,done=None):
        pane=self.pane;item=pane.item;start=pane.transform().m11();alpha=item.opacity()
        centre=pane.mapToScene(pane.viewport().rect().center());end=pane.sceneRect().center()
        def step(t):
            if pane.item is not item:return
            scale=start*(target/start)**t;pane.setTransform(QTransform.fromScale(scale,scale))
            pane.centerOn(centre+(end-centre)*t);item.setOpacity(alpha+(opacity-alpha)*t)
        def finished():
            self.transition=None
            if done:done()
        self.transition=theme.animate(self,duration,step,theme.EASE,finished)

    def present(self):
        self.stop_transition()
        if not self.pane.item:return
        target=self.pane.fit_scale();self.pane.setTransform(QTransform.fromScale(target*.96,target*.96));self.pane.item.setOpacity(.25)
        self.transition_to(target,1.0,220)

    def reset_preview(self):
        self.pane.show_photo(self.photo,self.preview);self.pane.source_width=self.source_width
        if self.photo:self.caption.setText(self.photo['name']+' · 单击图片查看原图 · 单击两侧空白或 Esc 返回相册')

    def blank_margin(self,point):
        point=QPointF(point).toPoint()
        return point.y()>=self.pane.geometry().top() and not self.pane.geometry().contains(point)

    def mousePressEvent(self,event):
        self.background_dragged=False
        self.background_press=event.position() if event.button()==Qt.MouseButton.LeftButton and self.blank_margin(event.position()) else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.background_press is not None and (event.position()-self.background_press).manhattanLength()>=5:self.background_dragged=True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        start=self.background_press;self.background_press=None
        if event.button()==Qt.MouseButton.LeftButton and start is not None and not self.background_dragged and self.blank_margin(event.position()) and (event.position()-start).manhattanLength()<5:
            self.back();event.accept();return
        super().mouseReleaseEvent(event)
    def showEvent(self,event):
        super().showEvent(event);self.escape.setEnabled(True)
    def hideEvent(self,event):
        self.stop_transition();self.escape.setEnabled(False);super().hideEvent(event)
    def zoom(self,photo=None,point=None):
        if not self.photo or not self.pane.pixmap:return
        self.stop_transition()
        if self.pane.detail:self.pane.unzoom(self.reset_preview);return
        self.pane.zoom_at(point);revision=self.revision;photo=self.photo
        if self.full_preview is not None:
            self.pane.install_full_image(self.full_preview);return
        if self.full_loading==(revision,photo['id']):return
        self.full_loading=(revision,photo['id'])
        from desktop import full_qimage
        library=self.host.library
        def work():return full_qimage(library.preview_image(photo['id'],0))
        def done(result):
            if revision!=self.revision or photo is not self.photo:return
            self.full_loading=None
            pixmap=QPixmap.fromImage(result);pixmap.setDevicePixelRatio(self.devicePixelRatioF());self.full_preview=pixmap
            if self.pane.detail:
                self.pane.install_full_image(pixmap)
                self.caption.setText(photo['name']+' · 原图 100% · 滚轮缩放 / 拖动查看 · Esc 返回相册')
        def failed(text):
            if revision==self.revision and self.photo is photo:self.full_loading=None;self.caption.setText(text)
        self.host.submit(work,done,failed,self.host.detail_pool)
