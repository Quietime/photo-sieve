"""Native Qt desktop photo review. No webview, browser, HTTP server or ports."""
from __future__ import annotations
import argparse
from collections import OrderedDict
from pathlib import Path
import sys
import traceback

from PySide6.QtCore import Qt, Signal, QObject, QRunnable, QThreadPool, QTimer, QSize, QUrl, QLockFile, QSettings
from PySide6.QtGui import QImage, QPixmap, QIcon, QShortcut, QKeySequence, QDesktopServices, QPainter, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QMessageBox, QListWidget, QListWidgetItem,
    QAbstractItemView, QStackedWidget, QGraphicsView, QGraphicsScene, QDialog, QProgressBar,
    QScrollArea, QFrame)
from app import Library
from version import APP_VERSION
from update_ui import UpdateController

STYLE = '''
QWidget { background:#121821; color:#dce5f4; font-family:"Microsoft YaHei UI","Noto Sans CJK SC",sans-serif; font-size:14px; }
QLabel {background:transparent;}
QFrame#sidebar {background:#1a222f; border-radius:12px;} QLabel#title {font-size:23px; font-weight:700;color:#fff;}
QLabel#muted {color:#99a9c0;} QLineEdit {padding:9px; border:1px solid #364356;border-radius:7px;background:#101620;}
QPushButton {background:#253348;border:1px solid #35465e;border-radius:7px;padding:10px;text-align:left;}
QPushButton:hover {background:#334a6b;} QPushButton:disabled {color:#64758c;background:#1b2636;}
QPushButton:checked {background:#315887;border-color:#628be8;} QPushButton#primary {background:#4978e8;border:0;}
QPushButton#danger {background:#60333a;border-color:#8d4855;}
QGraphicsView {background:#090e15;border:1px solid #293649;border-radius:8px;}
QListWidget {background:#101620;border:1px solid #2b3c51;border-radius:8px;}
QListWidget::item {padding:5px;} QListWidget::item:selected {background:#2d4c76;border-radius:6px;}
QProgressBar {border:1px solid #35465e;border-radius:5px;text-align:center;min-height:20px;}
QProgressBar::chunk {background:#487be7;} QScrollBar:vertical {width:12px;background:#162231;}
QScrollBar::handle:vertical {background:#3c506e;min-height:25px;} QFrame#card {background:#1a2635;border:1px solid #364356;border-radius:9px;}
'''

class Result(QObject):
    ok = Signal(object)
    fail = Signal(str)

class Work(QRunnable):
    def __init__(self, fn):
        super().__init__(); self.fn = fn; self.signals = Result()
    def run(self):
        try: self.signals.ok.emit(self.fn())
        except Exception as e: self.signals.fail.emit(str(getattr(e, 'detail', str(e))))

class PhotoView(QGraphicsView):
    clicked = Signal(object, object)
    next_requested = Signal()
    escape_requested = Signal()
    def __init__(self):
        super().__init__(); self.setScene(QGraphicsScene(self)); self.detail = False
        self.photo = None; self.pixmap = None; self.press = None; self.full_resolution = False
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    def show_photo(self, photo, pixmap=None, error=''):
        self.detail = False; self.full_resolution = False; self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.photo = photo; self.pixmap = pixmap; self.scene().clear(); self.resetTransform()
        if pixmap:
            item=self.scene().addPixmap(pixmap); self.scene().setSceneRect(item.boundingRect())
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        else:
            item = self.scene().addText(error or ('正在读取…' if photo else '没有照片'))
            item.setDefaultTextColor(Qt.GlobalColor.gray); self.scene().setSceneRect(item.boundingRect())
            self.centerOn(item)
    def zoom_at(self, point=None):
        self.detail=True; self.resetTransform(); self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        # One image pixel per screen pixel, including Windows display scaling.
        self.scale(1/self.devicePixelRatioF(),1/self.devicePixelRatioF())
        rect=self.sceneRect(); x,y=point or (.5,.5)
        self.centerOn(rect.left()+x*rect.width(),rect.top()+y*rect.height())
    def install_full_image(self, pixmap):
        # Preserve the area reached by dragging while the original file was decoding.
        rect=self.sceneRect(); center=self.mapToScene(self.viewport().rect().center())
        point=((center.x()-rect.left())/max(1,rect.width()),(center.y()-rect.top())/max(1,rect.height()))
        self.pixmap=pixmap; self.scene().clear(); item=self.scene().addPixmap(pixmap)
        self.scene().setSceneRect(item.boundingRect()); self.full_resolution=True; self.zoom_at(point)
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.pixmap and not self.detail:self.fitInView(self.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)
    def mousePressEvent(self, event):
        self.press = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            self.next_requested.emit(); event.accept(); return
        super().mousePressEvent(event)
    def mouseReleaseEvent(self, event):
        # Finish Qt's drag before toggling; a drag must not count as a click.
        click=event.button()==Qt.MouseButton.LeftButton and self.press is not None and (event.position()-self.press).manhattanLength()<5
        point=self.mapToScene(event.position().toPoint());rect=self.sceneRect()
        fraction=((point.x()-rect.left())/max(1,rect.width()),(point.y()-rect.top())/max(1,rect.height()))
        super().mouseReleaseEvent(event)
        if click and self.photo:self.clicked.emit(self.photo,fraction)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:self.escape_requested.emit();event.accept();return
        super().keyPressEvent(event)

class Window(QMainWindow):
    def __init__(self, library, check_updates=True):
        super().__init__(); self.library=library; self.setWindowTitle('照片快筛 · 原生桌面版 '+APP_VERSION); self.resize(1480,960)
        self.pool=QThreadPool(self); self.pool.setMaxThreadCount(2)
        self.image_pool=QThreadPool(self); self.image_pool.setMaxThreadCount(2)
        self.thumb_pool=QThreadPool(self); self.thumb_pool.setMaxThreadCount(1)
        self.detail_pool=QThreadPool(self); self.detail_pool.setMaxThreadCount(1)
        self.tasks=set(); self.cache=OrderedDict(); self.loading=set(); self.image_errors={}
        self.thumb_cache=OrderedDict(); self.thumb_loading=set(); self.thumb_errors={}
        self.queue=[]; self.view='pending'; self.busy=False; self.fetching=False; self.state_pending=False
        self.state_data=None; self.generation=0; self.focus_loading=set(); self.was_running=False; self.closed=False
        self.cursor_id=0; self.cursor_timer=QTimer(self); self.cursor_timer.setSingleShot(True); self.cursor_timer.timeout.connect(self.save_cursor)
        self.deleted_rows=[]; self.deleted_cards={}; self.deleted_more=False
        self.build(); self.update_actions()
        self.updates=UpdateController(self,check_on_start=check_updates);self.updates.report_previous()
        self.shortcuts=[]
        for key,fn in [('Up',lambda:self.act('keep')),('Down',lambda:self.act('delete')),('Right',self.next_photo),
                       ('Left',lambda:self.restore()),('Ctrl+Z',lambda:self.restore(general=True)),('Z',self.focus_left),('Esc',self.reset_zoom)]:
            shortcut=QShortcut(QKeySequence(key),self); shortcut.setAutoRepeat(False); shortcut.activated.connect(lambda fn=fn:fn() if not isinstance(QApplication.focusWidget(),QLineEdit) else None);self.shortcuts.append(shortcut)
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(900);self.poll()
    def submit(self, fn, ok=lambda _:None, fail=None, pool=None, priority=0):
        task=Work(fn); self.tasks.add(task)
        def success(value):
            try:
                if not self.closed:ok(value)
            finally:self.tasks.discard(task)
        def failure(message):
            try:
                if not self.closed:(fail or self.error)(message)
            finally:self.tasks.discard(task)
        task.signals.ok.connect(success);task.signals.fail.connect(failure);(pool or self.pool).start(task,priority)
    def error(self,message):
        self.statusBar().showMessage(message,12000); QMessageBox.warning(self,'照片快筛',message)
    def button(self,text,fn,primary=False):
        b=QPushButton(text);b.clicked.connect(fn)
        if primary:b.setObjectName('primary')
        return b
    def build(self):
        root=QWidget();self.setCentralWidget(root);outer=QHBoxLayout(root);outer.setContentsMargins(14,14,14,14)
        sidebar=QFrame();sidebar.setObjectName('sidebar');sidebar.setFixedWidth(250);side=QVBoxLayout(sidebar);side.setSpacing(12)
        title=QLabel('照片快筛');title.setObjectName('title');side.addWidget(title)
        label=QLabel('原生桌面 · 筛选记录自动保存');label.setObjectName('muted');side.addWidget(label)
        side.addWidget(QLabel('照片文件夹'));self.folder=QLineEdit();self.folder.setPlaceholderText('选择文件夹或粘贴路径');side.addWidget(self.folder)
        side.addWidget(self.button('选择文件夹…',self.choose_source));self.scan_button=self.button('扫描 / 更新目录',self.scan,True);side.addWidget(self.scan_button)
        self.nav={}
        for name,text in [('pending','未筛选'),('keep','保留'),('deleted','已删除 · 可恢复'),('all','全部未删除'),('duplicates','完全重复'),('errors','预览异常')]:
            b=self.button(text,lambda checked=False,n=name:self.switch(n));b.setCheckable(True);b.setChecked(name==self.view);side.addWidget(b);self.nav[name]=(b,text)
        side.addWidget(self.button('检查完全重复文件',lambda:self.submit(self.library.duplicates,lambda _:self.poll())))
        side.addStretch();side.addWidget(QLabel('↑ 保留    ↓ 删除左图\n← 恢复删除    → 下一张\nCtrl+Z 撤回    单击图片检查对焦'))
        side.addWidget(self.button('复制 / 移动保留照片…',self.export_dialog,True));side.addWidget(self.button('打开筛选记录文件夹',lambda:self.open_folder(str(self.library.directory))))
        side.addWidget(self.button('检查更新',lambda:self.updates.check()))
        side.addWidget(self.button('更新设置…',lambda:self.updates.configure()))
        self.update_label=QLabel('版本 '+APP_VERSION);self.update_label.setWordWrap(True);side.addWidget(self.update_label)
        self.install_update_button=self.button('立即安装更新并重启',lambda:self.updates.install());self.install_update_button.hide();side.addWidget(self.install_update_button)
        side_scroll=QScrollArea();side_scroll.setFixedWidth(270);side_scroll.setFrameShape(QFrame.Shape.NoFrame);side_scroll.setWidgetResizable(True);side_scroll.setWidget(sidebar);outer.addWidget(side_scroll)
        content=QVBoxLayout();outer.addLayout(content,1)
        heading=QHBoxLayout();self.heading=QLabel('未筛选');self.heading.setObjectName('title');heading.addWidget(self.heading);heading.addStretch()
        self.prev_button=self.button('上一张',self.previous_photo);self.next_button=self.button('下一张 → / 右键',self.next_photo);heading.addWidget(self.prev_button);heading.addWidget(self.next_button);content.addLayout(heading)
        self.progress=QProgressBar();self.progress.hide();content.addWidget(self.progress)
        self.message=QLabel('正在读取筛选记录…');self.message.setWordWrap(True);self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);content.addWidget(self.message)
        latest=QHBoxLayout();self.last_deleted=QLabel('删除会移入原文件夹下的 .photo-sieve-trash，可按 ← 恢复');self.last_deleted.setWordWrap(True);self.last_deleted.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);latest.addWidget(self.last_deleted,1)
        self.restore_button=self.button('恢复最近删除 ←',lambda:self.restore());latest.addWidget(self.restore_button)
        self.trash_location=self.button('打开删除位置',self.open_last_trash);latest.addWidget(self.trash_location);content.addLayout(latest)
        self.stack=QStackedWidget();content.addWidget(self.stack,1)
        review=QWidget();review_layout=QVBoxLayout(review);review_layout.setContentsMargins(0,0,0,0);pair=QHBoxLayout()
        self.photos=[];self.captions=[]
        for index,title in enumerate(['当前照片 · ↑ 保留 / ↓ 删除','下一张 · 用于比较']):
            column=QVBoxLayout();column.addWidget(QLabel(title));photo=PhotoView();photo.clicked.connect(self.focus);photo.next_requested.connect(self.next_photo);photo.escape_requested.connect(self.reset_zoom);column.addWidget(photo,1)
            caption=QLabel();caption.setWordWrap(True);caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);column.addWidget(caption);pair.addLayout(column,1);self.photos.append(photo);self.captions.append(caption)
        review_layout.addLayout(pair,1)
        actions=QHBoxLayout();self.keep_button=self.button('↑ 保留左图',lambda:self.act('keep'),True);self.delete_button=self.button('↓ 删除左图',lambda:self.act('delete'));self.delete_button.setObjectName('danger')
        actions.addWidget(self.keep_button);actions.addWidget(self.delete_button);actions.addStretch();actions.addWidget(QLabel('下方仅显示未保留的照片'));review_layout.addLayout(actions)
        self.strip=QListWidget();self.strip.setViewMode(QListWidget.ViewMode.IconMode);self.strip.setFlow(QListWidget.Flow.LeftToRight);self.strip.setWrapping(False)
        self.strip.setGridSize(QSize(150,112));self.strip.setUniformItemSizes(True);self.strip.setIconSize(QSize(124,80));self.strip.setFixedHeight(125);self.strip.setResizeMode(QListWidget.ResizeMode.Adjust);self.strip.setFocusPolicy(Qt.FocusPolicy.NoFocus);self.strip.itemClicked.connect(self.strip_select);review_layout.addWidget(self.strip)
        self.stack.addWidget(review)
        deleted=QWidget();dl=QVBoxLayout(deleted);notice=QLabel('文件尚未永久删除，仍占用硬盘空间。每张卡片显示原位置和实际暂存位置，可单独恢复。');notice.setWordWrap(True);dl.addWidget(notice)
        self.deleted_focus=PhotoView();self.deleted_focus.setFixedHeight(350);self.deleted_focus.hide();self.deleted_focus.clicked.connect(self.focus);self.deleted_focus.escape_requested.connect(self.reset_zoom);dl.addWidget(self.deleted_focus)
        scroll=QScrollArea();scroll.setWidgetResizable(True);self.deleted_container=QWidget();self.deleted_layout=QVBoxLayout(self.deleted_container);self.deleted_layout.setAlignment(Qt.AlignmentFlag.AlignTop);scroll.setWidget(self.deleted_container);dl.addWidget(scroll,1)
        self.more_button=self.button('加载更多已删除照片',self.load_more_deleted);dl.addWidget(self.more_button);self.stack.addWidget(deleted)
    def poll(self):
        if self.state_pending or self.closed:return
        self.state_pending=True; library=self.library
        def done(state):
            if library is not self.library:return
            self.state_pending=False;first=self.state_data is None;old=self.state_data;self.state_data=state
            if first:self.load(anchor=max(0,state['cursor']-1))
            for name,(button,text) in self.nav.items():button.setText(f"{text}    {state['counts'].get(name,'!')}")
            job=state['job'];running=job['running'];self.progress.setVisible(running)
            if running:
                self.progress.setRange(0,job['total'] or 0);self.progress.setValue(job['done']);self.progress.setFormat(f"{job['kind']}：{job['done']} / {job['total'] or '…'}")
                self.message.setText(f"{job['kind']}在后台运行；完成后可继续筛选。" )
            elif self.was_running or (old and job.get('revision')!=old['job'].get('revision')):
                errors=job.get('errors',[]);self.message.setText(job['kind']+'：'+job['message']+(f"；失败 {len(errors)} 项，点击导出按钮查看结果" if errors else ''))
                if errors:self.statusBar().showMessage('\n'.join(errors[:3]),20000)
                self.load(anchor=0 if self.view=='deleted' or not self.queue else self.queue[0]['id']-1)
            last=state['last_deleted'];self.restore_button.setEnabled(bool(last) and not running and not self.busy);self.trash_location.setEnabled(bool(last))
            self.last_deleted.setText(f"最近删除：{last['name']}\n实际暂存：{last['backup']}" if last else '删除会移入原文件夹下的 .photo-sieve-trash，可按 ← 恢复')
            if first and not running:self.message.setText(f"共 {state['counts']['all']:,} 张未删除照片 · 保留 {state['counts']['keep']:,} · 已删除 {state['counts']['deleted']:,}")
            self.was_running=running;self.update_actions()
        def failed(message):self.state_pending=False;self.statusBar().showMessage(message)
        self.submit(library.state,done,failed)
    def switch(self,view):
        if self.busy or self.fetching:return
        self.view=view;self.heading.setText(self.nav[view][1]);self.stack.setCurrentIndex(1 if view=='deleted' else 0)
        for n,(button,_) in self.nav.items():button.setChecked(n==view)
        self.load()
    def load(self,anchor=0):
        self.generation+=1;generation=self.generation;view=self.view;self.fetching=True;self.queue=[];self.render()
        if view=='deleted':
            self.deleted_rows=[];self.deleted_cards={}
            while self.deleted_layout.count():
                item=self.deleted_layout.takeAt(0)
                if item.widget():item.widget().deleteLater()
        def done(rows):
            if generation!=self.generation:return
            self.fetching=False
            if view=='deleted':self.add_deleted(rows)
            else:
                self.queue=rows
                if not rows and anchor:
                    self.load();return
                self.render();self.prefetch()
        def failed(message):self.fetching=False;self.error(message)
        self.submit(lambda:self.library.list_photos(view,anchor,'next',40),done,failed)
    def refill(self):
        if self.fetching or len(self.queue)>=12 or not self.queue:return
        self.fetching=True;anchor=self.queue[-1]['id'];generation=self.generation;view=self.view
        def done(rows):
            self.fetching=False
            if generation!=self.generation:return
            ids={p['id'] for p in self.queue};self.queue.extend(p for p in rows if p['id'] not in ids);self.render();self.prefetch()
        self.submit(lambda:self.library.list_photos(view,anchor,'next',40),done,lambda message:setattr(self,'fetching',False))
    def request_image(self,photo):
        pid=photo['id']; library=self.library
        if pid in self.cache or pid in self.loading or pid in self.image_errors:return
        self.loading.add(pid)
        def done(image):
            if library is not self.library:return
            self.loading.discard(pid)
            if image.isNull():failed('无法解码预览');return
            self.cache[pid]=QPixmap.fromImage(image)
            while len(self.cache)>16:self.cache.popitem(last=False)
            for pane in self.photos:
                if pane.photo and pane.photo['id']==pid and not pane.detail:pane.show_photo(pane.photo,self.cache[pid])
            self.thumb_cache[pid]=self.cache[pid].scaled(124,80,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            while len(self.thumb_cache)>64:self.thumb_cache.popitem(last=False)
            self.update_thumbnail(pid)
            card=self.deleted_cards.get(pid)
            if card:card.setPixmap(self.cache[pid].scaled(220,140,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
            self.update_actions()
        def failed(message):
            if library is not self.library:return
            self.loading.discard(pid);self.image_errors[pid]=message
            for pane in self.photos:
                if pane.photo and pane.photo['id']==pid and not pane.detail:pane.show_photo(pane.photo,error=message)
            self.update_actions()
        self.submit(lambda:QImage.fromData(library.preview(pid,1600)),done,failed,self.image_pool,priority=1)
    def update_thumbnail(self,pid):
        for i in range(self.strip.count()):
            item=self.strip.item(i)
            if item.data(Qt.ItemDataRole.UserRole)!=pid:continue
            if pid in self.thumb_cache:item.setIcon(QIcon(self.thumb_cache[pid]));item.setToolTip(item.data(Qt.ItemDataRole.UserRole+1))
            elif pid in self.thumb_errors:
                item.setText('预览失败\n'+item.data(Qt.ItemDataRole.UserRole+2));item.setToolTip(self.thumb_errors[pid])
    def request_thumbnail(self,photo):
        pid=photo['id'];library=self.library
        if pid in self.thumb_cache or pid in self.thumb_loading or pid in self.thumb_errors or len(self.thumb_loading)>=4:return
        self.thumb_loading.add(pid)
        def done(image):
            if library is not self.library:return
            self.thumb_loading.discard(pid)
            if image.isNull():failed('无法读取缩略图');return
            self.thumb_cache[pid]=QPixmap.fromImage(image).scaled(124,80,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            while len(self.thumb_cache)>64:self.thumb_cache.popitem(last=False)
            self.update_thumbnail(pid);self.prefetch_thumbnails()
        def failed(message):
            if library is not self.library:return
            self.thumb_loading.discard(pid);self.thumb_errors[pid]=message;self.update_thumbnail(pid);self.prefetch_thumbnails()
        self.submit(lambda:QImage.fromData(library.preview(pid,320)),done,failed,self.thumb_pool)
    def prefetch(self):
        # Large previews remain bounded; every filmstrip item gets its own small thumbnail.
        for photo in self.queue[:6]:
            if len(self.loading)>=6:break
            self.request_image(photo)
        self.prefetch_thumbnails()
    def prefetch_thumbnails(self):
        for photo in self.queue[:16]:
            if photo['status']!=1:self.request_thumbnail(photo)
    def render(self):
        for i,pane in enumerate(self.photos):
            p=self.queue[i] if len(self.queue)>i else None;pid=p['id'] if p else None
            if p and pid in self.cache:self.cache.move_to_end(pid)
            if not pane.photo or not p or pane.photo['id']!=pid or (pid in self.cache and not pane.pixmap):pane.show_photo(p,self.cache.get(pid),self.image_errors.get(pid,''))
            self.captions[i].setText((p['relative']+'\n'+('已保留' if p['status']==1 else '未筛选')) if p else ('当前队列已处理完' if not self.fetching else '正在读取队列…'))
        scroll=self.strip.horizontalScrollBar().value();self.strip.clear()
        for p in [p for p in self.queue[:16] if p['status']!=1]:
            item=QListWidgetItem(p['name']);item.setData(Qt.ItemDataRole.UserRole,p['id']);item.setToolTip(p['relative']);item.setData(Qt.ItemDataRole.UserRole+1,p['relative']);item.setData(Qt.ItemDataRole.UserRole+2,p['name']);self.strip.addItem(item)
            if p['id'] in self.thumb_cache:item.setIcon(QIcon(self.thumb_cache[p['id']]))
            elif p['id'] in self.cache:item.setIcon(QIcon(self.cache[p['id']]))
            else:
                placeholder=QPixmap(124,80);placeholder.fill(QColor('#223247'));painter=QPainter(placeholder);painter.setPen(QColor('#a5b4ca'));painter.drawText(placeholder.rect(),Qt.AlignmentFlag.AlignCenter,'读取中…');painter.end();item.setIcon(QIcon(placeholder))
            if p['id'] in self.thumb_errors:self.update_thumbnail(p['id'])
        self.strip.horizontalScrollBar().setValue(scroll)
        if self.queue:
            self.cursor_id=self.queue[0]['id'];self.cursor_timer.start(250)
        self.update_actions()
    def update_actions(self):
        running=bool(self.state_data and self.state_data['job']['running']);current=self.queue[0]['id'] if self.queue else None
        ready=current in self.cache or current in self.image_errors
        allowed=bool(self.queue) and not self.busy and not self.fetching and not running and self.view!='deleted' and ready
        self.keep_button.setEnabled(allowed);self.delete_button.setEnabled(allowed)
        self.next_button.setEnabled(bool(self.queue) and not self.busy and self.view!='deleted');self.prev_button.setEnabled(not self.busy and self.view!='deleted')
        self.scan_button.setEnabled(not self.busy and not running)
    def act(self,action):
        if (action=='keep' and not self.keep_button.isEnabled()) or (action=='delete' and not self.delete_button.isEnabled()):return
        photo=self.queue[0];self.busy=True;self.update_actions();generation=self.generation
        def done(result):
            self.busy=False
            if generation==self.generation and self.queue and self.queue[0]['id']==photo['id']:self.queue.pop(0)
            if action=='delete':self.last_deleted.setText(f"已删除：{photo['name']}\n实际暂存：{result['backup']}")
            self.message.setText(('已保留：' if action=='keep' else '已删除，可按 ← 恢复：')+photo['name'])
            self.render();self.prefetch();self.refill();self.poll()
        def failed(message):self.busy=False;self.update_actions();self.error(message)
        self.submit((lambda:self.library.mark(photo['id'],1)) if action=='keep' else (lambda:self.library.trash(photo['id'])),done,failed)
    def next_photo(self):
        if self.busy or self.view=='deleted' or not self.queue:return
        self.queue.pop(0);self.render();self.prefetch();self.refill()
    def previous_photo(self):
        if self.busy or self.fetching or self.view=='deleted':return
        anchor=self.queue[0]['id'] if self.queue else 2**63-1;view=self.view
        def done(rows):
            if rows:self.load(rows[-1]['id']-1)
        self.submit(lambda:self.library.list_photos(view,anchor,'prev',1),done)
    def strip_select(self,item):
        if self.busy:return
        pid=item.data(Qt.ItemDataRole.UserRole);idx=next((i for i,p in enumerate(self.queue) if p['id']==pid),None)
        if idx is not None:self.queue=self.queue[idx:];self.render();self.prefetch();self.refill()
    def restore(self,photo_id=None,general=False):
        if self.busy:return
        self.busy=True;self.update_actions()
        def done(result):
            self.busy=False;self.message.setText('已恢复到原位置' if result.get('restored') else '已撤回筛选')
            if self.view=='deleted':self.load()
            else:
                if result.get('status')==1 and self.view=='pending':self.switch('keep')
                self.load(result['id']-1)
            self.poll()
        def failed(message):self.busy=False;self.update_actions();self.error(message)
        self.submit(lambda:self.library.undo(only_delete=not general,photo_id=photo_id),done,failed)
    def reset_pane_zoom(self,pane):
        if pane is self.deleted_focus:
            pane.show_photo(None);pane.hide();return
        if pane.photo:
            p=pane.photo;pane.show_photo(p,self.cache.get(p['id']),self.image_errors.get(p['id'],''))
            index=self.photos.index(pane);self.captions[index].setText(p['relative']+'\n'+('已保留' if p['status']==1 else '未筛选'))
    def reset_zoom(self):
        for pane in self.photos+[self.deleted_focus]:
            if pane.detail:self.reset_pane_zoom(pane)
    def focus(self,photo,point=None):
        sender=self.sender()
        pane=sender if isinstance(sender,PhotoView) else next((v for v in self.photos if v.photo and v.photo['id']==photo['id']),None)
        if pane is None:
            pane=self.deleted_focus;pane.show();pane.show_photo(photo,self.cache.get(photo['id']))
        if pane.detail:self.reset_pane_zoom(pane);return
        pane.zoom_at(point)
        if pane in self.photos:
            index=self.photos.index(pane);self.captions[index].setText(photo['relative']+'\n已就地放大；正在读取原始分辨率… 再点一次或 Esc 恢复')
        self.load_focus(photo)
    def pump_focus(self):
        for pane in self.photos+[self.deleted_focus]:
            if pane.detail and pane.photo and not pane.full_resolution:self.load_focus(pane.photo)
    def load_focus(self,photo):
        pid=photo['id'];library=self.library
        if pid in self.focus_loading or len(self.focus_loading)>=2:return
        self.focus_loading.add(pid)
        def loaded(image):
            if library is not self.library:return
            self.focus_loading.discard(pid)
            if image.isNull():failed('原图解码失败');return
            pixmap=None
            for target in self.photos+[self.deleted_focus]:
                if target.detail and target.photo and target.photo['id']==pid:
                    if pixmap is None:pixmap=QPixmap.fromImage(image)
                    target.install_full_image(pixmap)
                    if target in self.photos:
                        index=self.photos.index(target);self.captions[index].setText(photo['relative']+f'\n100% · {image.width()} × {image.height()} · 拖动查看，再点一次或 Esc 恢复')
            self.pump_focus()
        def failed(message):
            if library is not self.library:return
            self.focus_loading.discard(pid);self.statusBar().showMessage('原始分辨率读取失败：'+message,15000)
            for pane in self.photos+[self.deleted_focus]:
                if pane.detail and pane.photo and pane.photo['id']==pid:self.reset_pane_zoom(pane)
            self.pump_focus()
        self.submit(lambda:QImage.fromData(library.preview(pid,0)),loaded,failed,self.detail_pool)
    def focus_left(self):
        if self.queue:self.focus(self.queue[0])
    def save_cursor(self):
        pid=self.cursor_id;library=self.library
        if pid:self.submit(lambda:library.remember('cursor',pid))
    def choose_source(self):
        folder=QFileDialog.getExistingDirectory(self,'选择照片文件夹',self.folder.text())
        if folder:self.folder.setText(folder)
    def scan(self):
        folder=self.folder.text().strip()
        if not folder:self.choose_source();folder=self.folder.text().strip()
        if not folder:return
        current=self.library.meta('root')
        if current and Path(current).resolve()!=Path(folder).resolve():
            import hashlib
            newdir=Path.home()/'.photo-sieve'/'libraries'/hashlib.sha256(str(Path(folder).resolve()).encode()).hexdigest()[:16]
            self.busy=True;self.update_actions();self.scan_button.setEnabled(False)
            self.message.setText('正在打开此目录的筛选记录…')
            def changed(library):
                QSettings('PhotoSieve','PhotoSieve').setValue('library',str(library.directory))
                self.library=library;self.generation+=1;self.cache.clear();self.loading.clear();self.image_errors.clear();self.thumb_cache.clear();self.thumb_loading.clear();self.thumb_errors.clear();self.focus_loading.clear();self.reset_zoom()
                self.state_data=None;self.state_pending=False;self.busy=False;self.cursor_timer.stop();self.cursor_id=0
                self.view='pending';self.stack.setCurrentIndex(0);self.heading.setText('未筛选')
                for name,(button,_) in self.nav.items():button.setChecked(name=='pending')
                self.queue=[];self.render();library.scan(folder);self.poll()
            self.submit(lambda:Library(newdir),changed,lambda message:(setattr(self,'busy',False),self.error(message)))
            return
        self.submit(lambda:self.library.scan(folder),lambda _:self.poll())
    def open_folder(self,path):
        if not Path(path).is_dir():self.error('文件夹不存在，或硬盘未连接：'+path);return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))
    def open_last_trash(self):
        last=self.state_data.get('last_deleted') if self.state_data else None
        if last:self.open_folder(str(Path(last['backup']).parent))
    def add_deleted(self,rows):
        self.deleted_rows.extend(rows);self.more_button.setEnabled(len(rows)==40)
        for photo in rows:
            frame=QFrame();frame.setObjectName('card');layout=QHBoxLayout(frame);image=QLabel('预览读取中…');image.setFixedSize(220,150);image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.deleted_cards[photo['id']]=image;layout.addWidget(image);text=QVBoxLayout();name=QLabel(photo['name']);text.addWidget(name)
            paths=QLabel('原位置：'+photo['path']+'\n实际暂存：'+str(photo['backup']));paths.setWordWrap(True);paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);text.addWidget(paths)
            buttons=QHBoxLayout();buttons.addWidget(self.button('恢复到原位置',lambda checked=False,p=photo:self.restore(photo_id=p['id'])));buttons.addWidget(self.button('打开暂存位置',lambda checked=False,p=photo:self.open_folder(str(Path(p['backup']).parent))));buttons.addWidget(self.button('放大检查',lambda checked=False,p=photo:self.focus(p)));text.addLayout(buttons);layout.addLayout(text,1);self.deleted_layout.addWidget(frame)
            if photo['id'] in self.cache:image.setPixmap(self.cache[photo['id']].scaled(220,140,Qt.AspectRatioMode.KeepAspectRatio))
            else:
                # Thumbnail-only task for long deleted lists; no full-size memory accumulation.
                generation=self.generation;library=self.library
                def loaded(im,pid=photo['id'],generation=generation):
                    if generation!=self.generation:return
                    label=self.deleted_cards.get(pid)
                    if label:label.setPixmap(QPixmap.fromImage(im).scaled(220,140,Qt.AspectRatioMode.KeepAspectRatio))
                self.submit(lambda pid=photo['id'],library=library:QImage.fromData(library.preview(pid,320)),loaded,lambda message:None,self.image_pool,priority=-1)
        if not self.deleted_rows:self.deleted_layout.addWidget(QLabel('暂无删除记录'))
    def load_more_deleted(self):
        if self.fetching:return
        self.fetching=True;anchor=self.deleted_rows[-1]['id'] if self.deleted_rows else 0;generation=self.generation
        def done(rows):
            self.fetching=False
            if generation==self.generation:self.add_deleted(rows)
        self.submit(lambda:self.library.list_photos('deleted',anchor,'next',40),done,lambda message:setattr(self,'fetching',False))
    def export_dialog(self):
        dialog=QDialog(self);dialog.setWindowTitle('保存全部保留照片');dialog.resize(700,440);layout=QVBoxLayout(dialog)
        count=self.state_data['counts']['keep'] if self.state_data else 0;layout.addWidget(QLabel(f'将本次任务开始时已保留的 {count:,} 张照片保存到目标目录。'))
        path=QLineEdit();summary=self.library.export_summary();path.setText(summary['destination'] if summary else '')
        row=QHBoxLayout();row.addWidget(path,1)
        def choose():
            folder=QFileDialog.getExistingDirectory(dialog,'选择输出目录',path.text())
            if folder:path.setText(folder)
        row.addWidget(self.button('选择目录…',choose));layout.addLayout(row)
        label=QLabel('保留原文件名和子目录；遇到同名文件会跳过并报告。\n复制：原片仍在原位置。移动：原片转到目标目录，不能用 ← 撤回。');label.setWordWrap(True);layout.addWidget(label)
        buttons=QHBoxLayout()
        def start(mode):
            destination=path.text().strip()
            if not destination:self.error('请先选择目标目录');return
            self.submit(lambda:self.library.export_kept(destination,mode),lambda _:(dialog.accept(),self.poll()))
        buttons.addWidget(self.button('复制全部保留照片',lambda:start('copy'),True));buttons.addWidget(self.button('移动全部保留照片',lambda:start('move')));layout.addLayout(buttons)
        if summary:
            result=QLabel(f"上次任务：成功 {summary['done']} / {summary['total']}，失败 {summary['failed']}\n目标：{summary['destination']}");result.setWordWrap(True);result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);layout.addWidget(result)
            layout.addWidget(self.button('打开输出文件夹',lambda:self.open_folder(summary['destination'])))
            if summary['errors']:
                failures=QLabel('\n'.join((e['original']+'：'+e['error']) for e in summary['errors'][:20]));failures.setWordWrap(True);failures.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(failures);layout.addWidget(scroll)
        layout.addStretch();layout.addWidget(self.button('关闭',dialog.reject));dialog.exec()
    def closeEvent(self,event):
        if self.busy or self.library.job['running']:
            QMessageBox.information(self,'任务正在进行','请等待文件操作或扫描完成后再退出。');event.ignore();return
        self.updates.timer.stop();self.timer.stop();self.cursor_timer.stop();self.closed=True
        for pool in (self.pool,self.image_pool,self.thumb_pool,self.detail_pool):pool.clear();pool.waitForDone()
        if self.cursor_id:self.library.remember('cursor',self.cursor_id)
        event.accept()

def main():
    if len(sys.argv)==3 and sys.argv[1]=='--apply-update':
        from updater import run_helper
        try:return run_helper(sys.argv[2])
        except Exception:
            Path(sys.argv[2]).with_name('helper-error.txt').write_text(traceback.format_exc(),encoding='utf-8');return 1
    parser=argparse.ArgumentParser(description='照片快筛 · 原生 Qt 桌面版')
    parser.add_argument('--folder');parser.add_argument('--data-dir',type=Path);parser.add_argument('--cache-mb',type=int,default=2048)
    parser.add_argument('--self-test-output',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args();application=QApplication(sys.argv[:1]);application.setStyleSheet(STYLE)
    if args.self_test_output:
        from diagnostics import run
        return run(application,args.self_test_output)
    if args.cache_mb<64:parser.error('缓存至少 64 MB')
    default_directory=Path.home()/'.photo-sieve'
    settings=QSettings('PhotoSieve','PhotoSieve')
    directory=args.data_dir or (default_directory if args.folder else Path(settings.value('library',str(default_directory))))
    try:
        lockpath=Path.home()/'.photo-sieve';lockpath.mkdir(parents=True,exist_ok=True)
        application.library_lock=QLockFile(str(lockpath/'desktop.lock'));application.library_lock.setStaleLockTime(0)
        if not application.library_lock.tryLock(100):
            QMessageBox.information(None,'照片快筛已在运行','请使用已经打开的窗口。');return 1
        if args.folder and not args.data_dir:
            directory=default_directory
            existing=Library(directory,args.cache_mb).meta('root')
            if existing and Path(existing).resolve()!=Path(args.folder).resolve():
                import hashlib
                directory=directory/'libraries'/hashlib.sha256(str(Path(args.folder).resolve()).encode()).hexdigest()[:16]
        library=Library(directory,args.cache_mb);settings.setValue('library',str(library.directory));window=Window(library)
        screen=application.primaryScreen().availableGeometry();window.resize(min(1480,screen.width()-40),min(960,screen.height()-60));window.show()
        if args.folder:window.folder.setText(args.folder);QTimer.singleShot(100,window.scan)
    except Exception:
        QMessageBox.critical(None,'无法启动照片快筛',traceback.format_exc());return 1
    return application.exec()

if __name__=='__main__':sys.exit(main())
