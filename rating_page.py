"""Independent photo grading; displayed stars come from verified photo metadata."""
from collections import OrderedDict
from pathlib import Path
import time
from PySide6.QtCore import Qt,QSettings,QThreadPool,QTimer,QSize,QPoint,QRect,Signal
from PySide6.QtGui import QIcon,QPixmap,QShortcut,QKeySequence,QColor,QPen,QPainter
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QCheckBox,QSplitter,QScrollArea,QSlider,QStyledItemDelegate,QStyle,QSizePolicy
from photo_grid import PhotoGrid
from apple_ui import GlassDock
from widgets import PhotoView
from face_panel import FacePanel
from rating_records import RatingRecords
from rating_engine import RatingEngine
from rating_combo import RoundedCombo
import keys
import theme

ACTIONS=[(f'star{i}',f'{i} 星',(str(i),),True) for i in range(1,6)]+[
 ('clear','清除评级',('0',),True),('previous','上一张',('Left',),True),
 ('next','下一张',('Right',),True),('undo','撤销评级',('Ctrl+Z',),True),
 *[(f'label{i}',f'颜色标签 {i}',(f'Alt+{i}',),False) for i in range(6)],
 ('zoom','放大到 100%',('Z',),False),('unzoom','还原缩放',('Esc',),False)]

class RatingDelegate(QStyledItemDelegate):
    """Keep two compact caption lines visible, independently of photo orientation."""
    def regions(self,rect):
        view=self.parent();line=view.fontMetrics().height()
        picture=rect.adjusted(8,5,-8,-(line*2+8))
        return picture,QRect(rect.x()+8,picture.bottom()+3,rect.width()-16,line),QRect(rect.x()+8,picture.bottom()+3+line,rect.width()-16,line)
    def paint(self,painter,option,index):
        painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(theme.ELEVATED));painter.setPen(QPen(QColor(theme.BLUE_HOVER),2) if option.state & QStyle.StateFlag.State_Selected else Qt.PenStyle.NoPen)
        painter.drawRoundedRect(option.rect.adjusted(2,2,-2,-2),6,6)
        picture,name,stars=self.regions(option.rect)
        icon=index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull():icon.paint(painter,picture,Qt.AlignmentFlag.AlignCenter)
        lines=(index.data(Qt.ItemDataRole.DisplayRole) or '').split('\n',1)
        painter.setFont(option.font)
        for rect,text in zip((name,stars),lines):
            painter.setPen(QColor(theme.LABEL if rect==name else theme.SECONDARY))
            painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,option.fontMetrics.elidedText(text,Qt.TextElideMode.ElideMiddle,rect.width()))
        color=getattr(self.parent(),'label_colors',{}).get(index.data(Qt.ItemDataRole.UserRole))
        if color:
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(color));painter.drawEllipse(option.rect.right()-14,option.rect.top()+8,7,7)
        painter.restore()

class RatingGrid(PhotoGrid):
    zoom_requested=Signal(int,QPoint)
    def __init__(self):
        self.wheel_remainder=0;self.scroll_motion=None;self.scroll_target=0;self.scroll_direction=0;self.scroll_applying=False;super().__init__()
        self.setItemDelegate(RatingDelegate(self));self.verticalScrollBar().setSingleStep(24)
        self.verticalScrollBar().valueChanged.connect(self.external_scroll)
        self.verticalScrollBar().sliderPressed.connect(self.stop_scroll)
        self.verticalScrollBar().rangeChanged.connect(self.stop_scroll)
    def stop_scroll(self,*_):
        motion=self.scroll_motion;self.scroll_motion=None
        if motion:motion.stop()
        self.scroll_target=self.verticalScrollBar().value();self.scroll_direction=0
    def external_scroll(self,*_):
        if not self.scroll_applying:self.stop_scroll()
    def scrollTo(self,*args):
        self.stop_scroll();super().scrollTo(*args)
    def hideEvent(self,event):
        self.stop_scroll();super().hideEvent(event)
    def resizeEvent(self,event):
        self.stop_scroll();super().resizeEvent(event)
    def set_tile_size(self,value):
        self.stop_scroll();self.tile_width=value
        self.tile_height=max(118,round(value*.56)+self.fontMetrics().height()*2+16)
        self.setGridSize(QSize(value,self.tile_height))
        self.setIconSize(QSize(value-16,self.tile_height-self.fontMetrics().height()*2-13))
    def wheelEvent(self,event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.stop_scroll();delta=event.angleDelta().y()
            self.wheel_remainder+=delta if delta else event.pixelDelta().y()*3
            steps=int(self.wheel_remainder/120);self.wheel_remainder-=steps*120
            if steps:self.zoom_requested.emit(steps,event.position().toPoint())
            event.accept();return
        self.wheel_remainder=0;bar=self.verticalScrollBar()
        if event.pixelDelta().y():
            self.stop_scroll();bar.setValue(bar.value()-event.pixelDelta().y());event.accept();return
        distance=-event.angleDelta().y()*48/120
        if distance:
            direction=1 if distance>0 else -1
            target=self.scroll_target if self.scroll_motion and direction==self.scroll_direction else bar.value()
            target=max(bar.minimum(),min(bar.maximum(),round(target+distance)))
            self.stop_scroll();self.scroll_target=target;self.scroll_direction=direction
            def step(value):
                self.scroll_applying=True
                try:bar.setValue(round(value))
                finally:self.scroll_applying=False
            def done():self.scroll_motion=None;self.scroll_direction=0
            self.scroll_motion=theme.animate(self,110,step,curve=theme.EASE,done=done,start=bar.value(),end=target)
        event.accept()

class InfoLabel(QLabel):
    """One line of the information row: it elides instead of wrapping, and text() keeps the full text."""
    def __init__(self,text=''):
        super().__init__();self.full='';self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Preferred);self.setText(text)
    # Small hints let the row shrink it; the layout's stretch then shares the width (an Ignored policy
    # would also drop an explicit fixed width, and neighbours would draw over it).
    def sizeHint(self):return QSize(40,super().sizeHint().height())
    def minimumSizeHint(self):return QSize(0,super().minimumSizeHint().height())
    def setText(self,text):
        self.full=str(text);self.refresh()
    def text(self):return self.full
    def refresh(self):
        shown=self.fontMetrics().elidedText(self.full,Qt.TextElideMode.ElideRight,max(0,self.width()))
        QLabel.setText(self,shown);self.setToolTip(self.full if shown!=self.full else self.toolTip() if self.toolTip()!=self.full else '')
    def resizeEvent(self,event):super().resizeEvent(event);self.refresh()


class RatingPage(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host;self.library=None;self.records=None
        self.engine=RatingEngine(host.data_root/'logs');self.pool=QThreadPool(self);self.pool.setMaxThreadCount(2)
        self.generation=0;self.read_revision=0;self.active=False;self.writing=False;self.listing=False;self.list_context=None;self.ids=[];self.index=0
        self.rating_pending={};self.rating_failures={};self.rating_confirmed={};self.rating_versions={};self.rating_serial=0;self.pending_count=0;self.undoing=False;self.warm_started=False
        self.info={};self.source_widths={};self.image_sizes={};self.thumb_sizes={};self.fingerprints={};self.thumb_photos={};self.preview_errors=set();self.reading=set();self.thumbs=OrderedDict();self.images=OrderedDict();self.loading=set();self.scanning=False;self.remaining=[];self.shortcut_objects=[]
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout=QVBoxLayout(self);layout.setContentsMargins(6,4,6,4);layout.setSpacing(4)
        # No page title row: the top bar already says 照片分级. Its two buttons sit at the end of the one filter row.
        row=None
        # One row for every filter and tool, so the row uses the page's width instead of stacking on the left.
        options=QHBoxLayout();options.setSpacing(6);self.options=options;self.scope=RoundedCombo();self.scope.addItems(['全部照片','保留照片']);options.addWidget(self.scope)
        self.filter=RoundedCombo();self.filter.addItems(['全部星级','未评级（0 星）']+[f'{i} 星' for i in range(1,6)]);options.addWidget(self.filter)
        self.options_slot=options.count()   # the workbench puts colour, grouping and its two buttons here
        options.addStretch()
        self.auto=QCheckBox('评级后下一张');self.auto.setChecked(QSettings('PhotoSieve','PhotoSieve').value('rating_auto_next',True,type=bool));options.addWidget(self.auto)
        self.auto.toggled.connect(lambda value:QSettings('PhotoSieve','PhotoSieve').setValue('rating_auto_next',value))
        self.read_button=host.button('读取全部照片评级',self.toggle_read);options.addWidget(self.read_button);layout.addLayout(options)
        self.note=InfoLabel('星级写入照片文件。ARW 支持标准原图星级；Windows 是否能显示会单独提示。保留与删除标记独立。');self.note.setObjectName('caption');self.note.setObjectName('hint')
        self.splitter=QSplitter(Qt.Orientation.Horizontal);self.splitter.setObjectName('ratingSplit');self.splitter.setChildrenCollapsible(False);self.splitter.setHandleWidth(9)
        self.splitter.setStyleSheet('QSplitter#ratingSplit::handle:horizontal {background:'+theme.SEPARATOR+';margin:0 3px;} QSplitter#ratingSplit::handle:horizontal:hover {background:'+theme.BLUE_HOVER+';}')
        self.split_ratio=max(.05,min(.95,QSettings('PhotoSieve','PhotoSieve').value('rating_split_ratio_v5',.3,type=float)));self.split_restored=False
        gallery=QWidget();gallery.setMinimumWidth(230);gallery_layout=QVBoxLayout(gallery);gallery_layout.setContentsMargins(0,0,8,0);gallery_layout.setSpacing(2)
        gallery_heading=QHBoxLayout();gallery_heading.setSpacing(4);gallery_heading.addStretch()
        self.gallery_smaller=host.button('−',lambda:self.gallery_size.setValue(self.gallery_size.value()-20));self.gallery_smaller.setFixedSize(24,24);self.gallery_smaller.setToolTip('缩小缩略图');gallery_heading.addWidget(self.gallery_smaller)
        self.gallery_size=QSlider(Qt.Orientation.Horizontal);self.gallery_size.setRange(120,360);self.gallery_size.setSingleStep(20);self.gallery_size.setPageStep(40);self.gallery_size.setMinimumWidth(70);self.gallery_size.setMaximumWidth(150);self.gallery_size.setFocusPolicy(Qt.FocusPolicy.NoFocus);self.gallery_size.setAccessibleName('图库缩略图大小')
        self.gallery_size.setValue(max(120,min(360,QSettings('PhotoSieve','PhotoSieve').value('rating_gallery_size',160,type=int))));gallery_heading.addWidget(self.gallery_size)
        self.gallery_larger=host.button('+',lambda:self.gallery_size.setValue(self.gallery_size.value()+20));self.gallery_larger.setFixedSize(24,24);self.gallery_larger.setToolTip('放大缩略图');gallery_heading.addWidget(self.gallery_larger);gallery_layout.addLayout(gallery_heading)
        self.gallery_smaller.setEnabled(self.gallery_size.value()>120);self.gallery_larger.setEnabled(self.gallery_size.value()<360)
        self.gallery_size.setStyleSheet('QSlider::groove:horizontal {height:4px;background:'+theme.SEPARATOR+';border-radius:2px;} QSlider::sub-page:horizontal {background:'+theme.BLUE_HOVER+';border-radius:2px;} QSlider::handle:horizontal {width:12px;margin:-4px 0;background:'+theme.LABEL+';border-radius:6px;}')
        self.grid=RatingGrid();self.grid.set_tile_size(self.gallery_size.value());self.grid.setWordWrap(False)
        self.gallery_size.setToolTip('缩略图大小 · 鼠标在图库内按 Ctrl + 滚轮也可调整');self.gallery_size.valueChanged.connect(self.resize_gallery);self.grid.zoom_requested.connect(self.wheel_gallery_zoom)
        self.grid.setStyleSheet('QListView {background:transparent;border:0;outline:0;} QListView::item {background:'+theme.ELEVATED+';border:2px solid transparent;border-radius:6px;margin:4px;} QListView::item:selected {border:2px solid '+theme.BLUE_HOVER+';background:'+theme.ELEVATED+';}')
        gallery_layout.addWidget(self.grid,1);self.splitter.addWidget(gallery)
        preview=QWidget();preview.setMinimumWidth(250);column=QHBoxLayout(preview);column.setContentsMargins(6,0,0,0);column.setSpacing(6)
        self.pane=PhotoView();column.addWidget(self.pane,1)
        # Beside the photo, not under it: the face crop and the quality hint take width, never height.
        side=QWidget();side.setFixedWidth(168);side_layout=QVBoxLayout(side);side_layout.setContentsMargins(0,0,0,0);side_layout.setSpacing(4)
        self.aux=QWidget();self.aux.setFixedHeight(184);aux_layout=QVBoxLayout(self.aux);aux_layout.setContentsMargins(0,0,0,0);aux_layout.setSpacing(0)
        self.face_panel=FacePanel(self.aux);aux_layout.addWidget(self.face_panel,0,Qt.AlignmentFlag.AlignCenter)
        side_layout.addWidget(self.aux);side_layout.addStretch();column.addWidget(side)
        self.panes=[self.pane];self.splitter.addWidget(preview);self.splitter.setStretchFactor(0,1);self.splitter.setStretchFactor(1,1);self.splitter.setSizes([600,400]);layout.addWidget(self.splitter,1)
        self.splitter.handle(1).setToolTip('左右拖动，调整图库和预览的宽度');self.splitter.splitterMoved.connect(self.split_moved)
        self.pane.clicked.connect(lambda photo,point:self.zoom(point,self.pane));self.pane.escape_requested.connect(self.unzoom);self.pane.next_requested.connect(lambda:self.move(1))
        # One 24 px information line: stars, name, camera settings, rating state and keys.
        self.info_line=QWidget();self.info_line.setFixedHeight(24);info=QHBoxLayout(self.info_line);info.setContentsMargins(4,0,4,0);info.setSpacing(12)
        self.stars=QLabel('☆☆☆☆☆');self.stars.setFixedWidth(92);self.stars.setStyleSheet('font-size:15px;letter-spacing:1px;color:'+theme.ICE+';');info.addWidget(self.stars)
        self.name=InfoLabel('请选择照片文件夹');self.name.setObjectName('name');self.name.setFixedWidth(210);info.addWidget(self.name)
        self.names=[self.name]
        self.camera_info=InfoLabel('请选择照片');self.camera_info.setObjectName('caption');info.addWidget(self.camera_info,2)
        self.status=InfoLabel('尚未读取 Windows 系统评级');self.status.setObjectName('caption');info.addWidget(self.status,2)
        info.addWidget(self.note,1)
        self.key_hint=QLabel();self.key_hint.setObjectName('hint');info.addWidget(self.key_hint)
        layout.addWidget(self.info_line)
        self.action_row=QScrollArea();self.action_row.setWidgetResizable(True);self.action_row.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded);self.action_row.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.action_row.setFixedHeight(36);self.action_row.setStyleSheet('QScrollArea {background:transparent;border:0;}');action_content=QWidget();self.action_row.setWidget(action_content);outer_controls=QHBoxLayout(action_content);outer_controls.setContentsMargins(0,0,0,0);outer_controls.addStretch();self.dock=GlassDock();controls=QHBoxLayout(self.dock);controls.setContentsMargins(4,1,4,1);controls.setSpacing(3);self.previous=host.button('上一张',lambda:self.move(-1));controls.addWidget(self.previous)
        self.star_buttons=[]
        for star in range(1,6):
            button=host.button('★'*star,lambda _,star=star:self.grade(star));button.setAccessibleName(f'设置 {star} 星');controls.addWidget(button);self.star_buttons.append(button)
        self.clear_button=host.button('清除',lambda:self.grade(0));controls.addWidget(self.clear_button)
        self.undo_button=host.button('撤销',self.undo);controls.addWidget(self.undo_button)
        self.next=host.button('下一张',lambda:self.move(1));controls.addWidget(self.next);outer_controls.addWidget(self.dock);outer_controls.addStretch();layout.addWidget(self.action_row);self.action_row.setVisible(host.rating_actionbar_visible)
        self.dock.register([self.previous]+self.star_buttons+[self.clear_button,self.undo_button,self.next])
        self.scope.currentIndexChanged.connect(self.reload);self.filter.currentIndexChanged.connect(self.reload)
        self.grid.itemClicked.connect(lambda item:self.select(item.row));self.grid.verticalScrollBar().valueChanged.connect(self.visible)
        self.grid.icon_for=lambda pid:self.thumbs.get(pid,host.tile)
        self.resize_timer=QTimer(self);self.resize_timer.setSingleShot(True);self.resize_timer.timeout.connect(self.show_current);self.pane.resized.connect(lambda:self.resize_timer.start(150))
        from workbench import Workbench
        self.tools=Workbench(self,info,self.options)
        self.apply_keys(keys.load(QSettings('PhotoSieve','PhotoSieve'),ACTIONS,'rating_shortcuts/'))
    def apply_keys(self,chosen):
        self.chosen=chosen
        for shortcut in self.shortcut_objects:shortcut.setEnabled(False);shortcut.deleteLater()
        self.shortcut_objects=[]
        handlers={f'star{i}':lambda i=i:self.grade(i) for i in range(1,6)}
        from workflow_data import LABELS
        handlers.update({f'label{i}':lambda key=key:self.tools.label(key) for i,key in enumerate(LABELS)})
        handlers.update(clear=lambda:self.grade(0),previous=lambda:self.move(-1),next=lambda:self.move(1),undo=self.undo,zoom=self.zoom,unzoom=self.unzoom)
        for action,_,_,repeat in ACTIONS:
            for key in chosen[action]:
                if not key:continue
                shortcut=QShortcut(QKeySequence(key),self);shortcut.setContext(Qt.ShortcutContext.WindowShortcut);shortcut.setAutoRepeat(repeat);shortcut.activated.connect(handlers[action]);self.shortcut_objects.append(shortcut)
        first=lambda action:keys.first(chosen,action)
        self.key_hint.setText('　'.join(x for x in ((first('star1') and first('star5')) and f"{first('star1')}–{first('star5')} 评级",
            first('clear') and f"{first('clear')} 清除",(first('previous') or first('next')) and f"{first('previous')}/{first('next')} 浏览",
            first('undo') and f"{first('undo')} 撤销") if x))
        for button,action in [(self.previous,'previous'),(self.next,'next'),(self.clear_button,'clear'),(self.undo_button,'undo')]+[(b,f'star{i}') for i,b in enumerate(self.star_buttons,1)]:
            # Grading is done from the keyboard: the dock stays slim, without key badges; keys are in the tooltip.
            button.variant='dock';button.compact=True;button.setFixedHeight(26);button.setStyleSheet('padding:0 8px;min-height:0;font-size:12px;')
            font=button.font();font.setPixelSize(12);button.setFont(font)
            button.set_shortcut('',' / '.join(keys.label(k) for k in chosen[action] if k) or '未设置快捷键')
    def edit_keys(self):
        dialog=keys.ShortcutDialog(self.host,self.chosen,ACTIONS,'照片分级快捷键','分级与照片筛选使用独立快捷键。每项允许留空，仅用按钮操作。')
        def saved(chosen):keys.save(QSettings('PhotoSieve','PhotoSieve'),chosen,'rating_shortcuts/');self.apply_keys(chosen);self.host.sync_shortcut_mode()
        dialog.saved.connect(saved);dialog.exec()
    def activate(self):
        self.active=True;self.setFocus()
        if not self.host.session_active:return
        if self.library is not self.host.library:
            self.library=self.host.library;self.records=RatingRecords(self.library);self.tools.attach(self.library);self.info={};self.rating_failures.clear();self.rating_versions.clear();self.rating_confirmed.clear();self.clear_previews()
        if not self.warm_started and hasattr(self.engine,'warm_writes'):
            self.warm_started=True;self.host.submit(self.engine.warm_writes,pool=self.pool,priority=-10)
        self.reload()
    def clear_previews(self):
        self.images.clear();self.thumbs.clear();self.image_sizes.clear();self.thumb_sizes.clear();self.source_widths.clear();self.fingerprints.clear();self.thumb_photos.clear();self.preview_errors.clear()
        self.face_panel.show_result(None);self.camera_info.setText('请选择照片')
    def deactivate(self):
        self.active=False;self.tools.hide();self.generation+=1;self.scanning=False;self.listing=False;self.resize_timer.stop();self.reading.clear();self.loading.clear()
    def reload(self,*_):
        if not self.active or self.writing or not self.records:return
        self.generation+=1;gen=self.generation;self.scanning=False;self.listing=True;self.remaining=[];self.reading.clear();self.loading.clear();self.read_button.setText('读取全部照片评级')
        extra_records=self.tools.records;extra_filter=self.tools.snapshot();records=self.records;scope='keep' if self.scope.currentIndex() else 'all';stars=self.filter.currentIndex()-1 if self.filter.currentIndex() else None
        context=(id(self.library),scope,stars,extra_filter['label'],extra_filter['mode'],extra_filter['group']);anchor=self.pid() if context==self.list_context else None;self.list_context=context
        self.status.setText('正在读取分级列表…')
        cached=list(self.fingerprints);library=self.library
        def work():
            ids,groups,annotations=self.tools.filter_ids(records.ids(scope,stars),extra_filter,extra_records);fingerprints={}
            with library.connect() as db:
                for start in range(0,len(cached),500):
                    part=cached[start:start+500]
                    rows=db.execute('SELECT id,size,mtime FROM photos WHERE present=1 AND id IN ('+','.join('?' for _ in part)+')',part)
                    fingerprints.update((row['id'],(row['size'],row['mtime'])) for row in rows)
            return ids,fingerprints,groups,annotations
        def done(value):
            if not self.valid(gen):return
            self.listing=False
            ids,fingerprints,groups,annotations=value;self.tools.reloaded(groups,annotations)
            for pid in cached:
                if self.fingerprints.get(pid)!=fingerprints.get(pid):
                    self.images.pop(pid,None);self.thumbs.pop(pid,None);self.thumb_photos.pop(pid,None);self.thumb_sizes.pop(pid,None);self.image_sizes.pop(pid,None);self.source_widths.pop(pid,None);self.fingerprints.pop(pid,None)
            self.preview_errors.clear()
            self.ids=ids;self.index=ids.index(anchor) if anchor in ids else 0;self.info={};self.grid.captions.clear();self.grid.tips.clear()
            for pid,photo in self.thumb_photos.items():
                self.grid.captions[pid]=photo['name']+'\n'+self.star_text(None);self.grid.tips[pid]=photo['path']
            self.grid.set_ids(ids);self.show_current(ensure_visible=True);self.visible()
            if stars is not None:self.note.setText('仅显示已读取的 Windows 星级。首次筛选或在资源管理器中改过星级后，请点击“读取全部照片评级”刷新。')
            else:self.note.setText(f'{len(ids):,} 张照片 · 复用整库磁盘缓存，附近照片提前载入内存；星级与保留 / 删除标记独立。')
        def failed(message):
            if not self.valid(gen):return
            self.listing=False;self.update_controls();self.host.error(message)
        self.update_controls();self.host.submit(work,done,failed)
    def valid(self,gen):return self.active and gen==self.generation and self.library is self.host.library and not self.host.closed
    def pid(self):return self.ids[self.index] if self.ids and 0<=self.index<len(self.ids) else None
    def nearby_photos(self,limit=64):
        rows=[]
        for pid in self.ids[self.index:self.index+limit]:
            photo=self.images[pid][0] if pid in self.images else self.thumb_photos.get(pid)
            # Keep current-first identity; never substitute the next photo's data.
            if photo is None:break
            rows.append(photo)
        return rows
    def show_aux(self):
        rows=self.nearby_photos(1);photo=rows[0] if rows else None
        self.face_panel.show_result(self.host.faces.result_for(photo) if photo else None)
        if photo:self.host.metadata_service.update()
        else:self.camera_info.setText('正在读取拍摄参数…' if self.pid() else '没有符合条件的照片')
        self.tools.show_quality()
    def select(self,index):
        self.tools.last_interaction=time.monotonic()
        if self.undoing or self.host.closing:return
        self.index=index
        self.show_aux()
        for pane in self.panes:
            if pane.detail:pane.show_photo(None)
        self.show_current(ensure_visible=True);self.setFocus()
    def move(self,delta):
        if self.undoing or self.host.closing or not self.ids:return
        self.select(max(0,min(len(self.ids)-1,self.index+delta)))
    def showEvent(self,event):
        super().showEvent(event)
        if not self.split_restored:QTimer.singleShot(0,self.restore_split)
    def restore_split(self):
        if self.split_restored:return
        self.split_restored=True;total=max(1,self.splitter.width()-self.splitter.handleWidth())
        self.splitter.setSizes([round(total*self.split_ratio),round(total*(1-self.split_ratio))]);self.visible()
    def split_moved(self,*_):
        left,right=self.splitter.sizes();total=left+right
        if total:
            self.split_ratio=left/total;QSettings('PhotoSieve','PhotoSieve').setValue('rating_split_ratio_v5',self.split_ratio)
        if self.pane.pixmap and not self.pane.detail:self.pane.fit();self.pane.viewport().update()
        self.visible();self.resize_timer.start(150)
    def wheel_gallery_zoom(self,steps,point):
        self.gallery_zoom_point=point
        self.gallery_size.setValue(self.gallery_size.value()+steps*20)
        self.gallery_zoom_point=None
    def resize_gallery(self,value):
        point=getattr(self,'gallery_zoom_point',None)
        if point is None:point=self.grid.viewport().rect().center()
        anchor=self.grid.indexAt(point)
        if not anchor.isValid():
            visible=self.grid.visible_ids();anchor=self.grid.photo_model.index(self.grid.index_of(visible[0])) if visible else self.grid.currentIndex()
        old=self.grid.visualRect(anchor);fraction=(point.y()-old.y())/max(1,old.height())
        self.grid.set_tile_size(value);self.grid.doItemsLayout()
        if anchor.isValid():
            rect=self.grid.visualRect(anchor);bar=self.grid.verticalScrollBar();bar.setValue(round(bar.value()+rect.y()+fraction*rect.height()-point.y()))
        self.gallery_smaller.setEnabled(value>120);self.gallery_larger.setEnabled(value<360)
        QSettings('PhotoSieve','PhotoSieve').setValue('rating_gallery_size',value);self.visible()
    def show_current(self,ensure_visible=False):
        if not self.active:return
        pid=self.pid()
        if pid is None:
            for pane,name in zip(self.panes,self.names):pane.show_photo(None);name.setText('没有符合条件的照片')
            self.status.setText('可更换照片范围，或读取全部照片评级后再按星级筛选');self.show_aux();self.update_controls();return
        if self.grid.currentRow()!=self.index:self.grid.setCurrentRow(self.index)
        if ensure_visible:self.grid.scrollTo(self.grid.photo_model.index(self.index))
        for offset,pane in enumerate(self.panes):
            if self.index+offset<len(self.ids):
                target=self.ids[self.index+offset]
                if not pane.detail:self.preview(target,large=True)
            else:pane.show_photo(None);self.names[offset].setText('没有下一张')
        self.read(pid);self.update_controls();self.prefetch();self.show_aux()
    def install_preview(self,pid,photo,pix):
        for offset,pane in enumerate(self.panes):
            if self.index+offset<len(self.ids) and self.ids[self.index+offset]==pid and not pane.detail:
                changed=not pane.photo or pane.photo['id']!=pid or pane.pixmap is not pix
                if changed:pane.show_photo(photo,pix)
                pane.source_width=self.source_widths.get(pid,pix.width())
                text=f'{photo["name"]}  ·  {self.index+offset+1:,} / {len(self.ids):,}'
                if self.names[offset].text()!=text:self.names[offset].setText(text)
                if changed:self.show_aux()
    def visible(self,*_):
        if not self.active or not self.records or self.host.closing:return
        for pid in self.grid.visible_ids():self.preview(pid);self.read(pid)
        for pid in self.ids[self.index:self.index+min(64,max(16,self.cache_plan()[1]))]:self.read(pid)
    def preview_size(self):
        dpr=self.devicePixelRatioF()
        return QSize(max(300,round(self.pane.width()*dpr)),max(200,round(self.pane.height()*dpr)))
    def cache_plan(self):
        import machine
        size=self.preview_size();pixel_bytes=size.width()*size.height()*4+176*118*4
        requested=self.host.preload_count or machine.PROFILE.buffer(pixel_bytes)
        # Account for both grading and screening pages sharing process memory.
        capacity=machine.PROFILE.preview_capacity(max(16,requested),pixel_bytes*2,True)
        behind=min(4,self.index);start=self.index-behind
        return self.ids[start:start+capacity],capacity
    def prefetch(self):
        if not self.active or self.host.cache_clearing:return
        wanted,capacity=self.cache_plan();keep=set(wanted)
        for pid in list(self.images):
            if pid not in keep:
                self.images.pop(pid,None);self.image_sizes.pop(pid,None);self.source_widths.pop(pid,None)
        # Current pair first, then nearby future frames, then the short back buffer.
        order=self.ids[self.index:self.index+2]+wanted
        for pid in dict.fromkeys(order):self.preview(pid,True)
        while len(self.thumbs)>max(128,min(512,capacity*4)):self.thumbs.popitem(last=False)
        for pid in list(self.fingerprints):
            if pid not in self.images and pid not in self.thumbs:self.fingerprints.pop(pid,None)
        for pid in list(self.thumb_photos):
            if pid not in self.thumbs:self.thumb_photos.pop(pid,None)
        for pid in list(self.thumb_sizes):
            if pid not in self.thumbs:self.thumb_sizes.pop(pid,None)
    def preview(self,pid,large=False):
        key=(pid,large)
        if self.host.cache_clearing or key in self.preview_errors:return
        if key in self.loading:return
        size=self.preview_size()
        if large and pid in self.images and self.image_sizes.get(pid)==size:
            photo,pix=self.images[pid];self.install_preview(pid,photo,pix);return
        dpr=self.devicePixelRatioF();thumb_size=self.grid.iconSize()*dpr
        if not large and pid in self.thumbs and self.thumb_sizes.get(pid)==thumb_size:return
        if sum(flag==large for _,flag in self.loading)>=(2 if large else 4):return
        self.loading.add(key);gen=self.generation;library=self.library
        if large:
            for offset,pane in enumerate(self.panes):
                if self.index+offset<len(self.ids) and self.ids[self.index+offset]==pid:
                    if pid in self.images:self.install_preview(pid,*self.images[pid])
                    else:pane.show_photo({'id':pid},message='正在读取照片…')
        dpr=self.devicePixelRatioF();width=size.width();height=size.height();edge=self.host.preview_edge(size)
        def work():
            from desktop import display_image
            photo=library.public(library.photo(pid))
            with library.connect() as db:
                quality=db.execute('SELECT quality FROM work_features WHERE photo_id=? AND size=? AND mtime=?',(pid,photo['size'],photo['mtime'])).fetchone()
            if quality:
                import json
                photo['_work_quality']=json.loads(quality[0])
            thumb_edge=320 if max(thumb_size.width(),thumb_size.height())<=320 else 1600
            result=display_image(library.preview_image(pid,edge if large else thumb_edge),width if large else thumb_size.width(),height if large else thumb_size.height(),(8,8));return photo,result
        def done(value):
            if not self.valid(gen):return
            self.loading.discard(key);photo,result=value;pix=QPixmap.fromImage(result[0][0]);pix.setDevicePixelRatio(dpr)
            self.fingerprints[pid]=(photo['size'],photo['mtime'])
            if large:
                self.source_widths[pid]=result[2][0];self.images[pid]=(photo,pix);self.image_sizes[pid]=size
                self.install_preview(pid,photo,pix)
            else:
                self.thumbs[pid]=QIcon(pix);self.thumb_photos[pid]=photo;self.thumb_sizes[pid]=thumb_size
                while len(self.thumbs)>128:self.thumbs.popitem(last=False)
                self.grid.captions[pid]=photo['name']+'\n'+self.star_text(self.info.get(pid));self.grid.tips[pid]=photo['path'];row=self.grid.index_of(pid)
                if row>=0:self.grid.photo_model.changed(row)
            self.prefetch()
            self.visible()
        def failed(message):
            if not self.valid(gen):return
            self.loading.discard(key)
            self.preview_errors.add(key)
            # Do not retry a broken preview in a tight loop.
            if large and pid==self.pid():self.pane.show_photo({'id':pid},error=message)
            if not large:self.thumbs[pid]=self.host.tile;self.thumb_sizes[pid]=thumb_size
            self.prefetch();self.visible()
        current=pid in self.ids[self.index:self.index+2]
        self.host.submit(work,done,failed,self.host.image_pool if large else self.host.thumb_pool,100 if large and current else -5 if large else 0)
    @staticmethod
    def star_text(info):
        if not info or info['state']=='unread':return '尚未读取'
        if info['state']!='ready':return 'Windows 评级不可用'
        text='★'*info['stars']+'☆'*(5-info['stars']) if info['stars'] else '未评级'
        return text+(' · 待保存' if info.get('pending') else ' · 保存失败' if info.get('write_error') else '')
    def read(self,pid,force=False):
        if pid in self.reading or len(self.reading)>=2 or pid in self.rating_pending or self.undoing or self.host.closing:return
        if pid in self.info and not force:return
        self.reading.add(pid);gen=self.generation;read_revision=self.read_revision;version=self.rating_versions.get(pid,0);records=self.records
        def work():
            cached={} if force else records.cached([pid])
            return cached.get(pid) or records.read(pid,self.engine)
        def done(info):
            if not self.valid(gen):return
            self.reading.discard(pid)
            if read_revision!=self.read_revision or version!=self.rating_versions.get(pid,0) or pid in self.rating_pending:
                self.visible()
                return
            if pid in self.rating_failures:info=dict(info,write_error=self.rating_failures[pid])
            self.info[pid]=info;self.update_caption(pid);self.update_controls();self.pump_reads();self.visible()
        def failed(message):done({'state':'error','stars':None,'writable':False,'error':message})
        self.host.submit(work,done,failed,self.pool,100 if pid==self.pid() else 0)
    def update_caption(self,pid):
        text=self.grid.captions.get(pid,'照片 #'+str(pid)).split('\n')[0];self.grid.captions[pid]=text+'\n'+self.star_text(self.info.get(pid));row=self.grid.index_of(pid)
        if row>=0:self.grid.photo_model.changed(row)
    def can_grade(self):
        info=self.info.get(self.pid())
        if info and info['state'] in ('ready','error'):return info['state']=='ready' and bool(info['writable'])
        return bool(self.pane.photo and self.pane.photo['id']==self.pid() and self.pane.pixmap is not None)
    def update_controls(self):
        info=self.info.get(self.pid())
        self.stars.setText('★'*info['stars']+'☆'*(5-info['stars']) if info and info['state']=='ready' else '☆☆☆☆☆')
        self.stars.setToolTip(self.star_text(info))
        writable=self.can_grade();allowed=writable and not self.undoing and self.pending_count<256 and not self.listing and not self.host.closing and not self.host.busy and not self.host.job_running()
        for button in self.star_buttons+[self.clear_button]:button.setEnabled(allowed)
        self.previous.setEnabled(bool(self.ids) and self.index>0 and not self.undoing and not self.host.closing);self.next.setEnabled(bool(self.ids) and self.index+1<len(self.ids) and not self.undoing and not self.host.closing)
        self.scope.setEnabled(not self.writing);self.filter.setEnabled(not self.writing);self.read_button.setEnabled(bool(self.records) and not self.writing)
        undo=False
        if self.records and not self.writing:
            with self.library.connect() as db:undo=bool(db.execute('SELECT 1 FROM rating_history h JOIN photos p ON p.id=h.photo_id WHERE p.present=1 LIMIT 1').fetchone())
        self.undo_button.setEnabled(undo and not self.writing and not self.host.busy and not self.host.job_running())
        if self.pending_count:self.status.setText(f'后台待保存 {self.pending_count:,} 项评级 · '+('保存完毕后退出' if self.host.closing else '队列已满，请稍候' if self.pending_count>=256 else '可以继续选片和打分'))
        elif self.rating_failures:self.status.setText(f'{len(self.rating_failures)} 张照片的评级未保存，请检查后重新打分')
        elif self.writing:self.status.setText('正在安全写入并验证照片星级…')
        elif info:
            embedded=info.get('backend')=='embedded'
            origin='ARW 原图星级' if embedded else 'Windows 系统评级'
            visibility=(' · Windows 可读取' if info.get('windows_visible') else ' · Windows 读取支持待评级后验证' if info.get('windows_visible') is None else ' · 当前 Windows 无法读取原图星级') if embedded else ''
            self.status.setText(origin+'：'+self.star_text(info)+visibility+(' · '+info.get('error','') if info.get('error') and not embedded else ' · 点击星级写入，0 清除'))
            self.status.setToolTip(info.get('error',''))
        elif self.pid():self.status.setText('正在读取 Windows 系统评级…')
    def toggle_read(self):
        if self.scanning:self.scanning=False;self.remaining=[];self.read_button.setText('读取全部照片评级');return
        if not self.records or self.writing:return
        self.scanning=True;self.remaining=None;gen=self.generation;records=self.records;scope='keep' if self.scope.currentIndex() else 'all'
        def done(ids):
            if not self.valid(gen) or not self.scanning:return
            self.remaining=list(reversed(ids));self.total=len(ids);self.pump_reads()
        self.host.submit(lambda:records.ids(scope),done,self.host.error)
    def pump_reads(self):
        if not self.scanning or self.writing or self.remaining is None:return
        while self.remaining and len(self.reading)<2:
            if self.remaining[-1] in self.reading:break
            self.read(self.remaining.pop(),True)
        self.read_button.setText(f'停止读取 · {max(0,self.total-len(self.remaining)-len(self.reading)):,} / {self.total:,}')
        if not self.remaining and not self.reading:self.scanning=False;self.read_button.setText('读取全部照片评级');self.reload()
    def grade(self,stars,advance=True):
        self.tools.last_interaction=time.monotonic()
        pid=self.pid();info=self.info.get(pid)
        if self.host.busy or self.host.job_running() or self.undoing or self.listing or self.host.closing or self.pending_count>=256 or not self.can_grade():return
        info=info or dict(photo_id=pid,state='unread',stars=None,writable=False,error='')
        if info['stars']==stars:
            if advance and self.auto.isChecked():self.move(1)
            return
        records=self.records;library=self.library;self.rating_serial+=1;token=self.rating_serial
        if pid not in self.rating_pending:self.rating_confirmed[pid]=dict(info)
        self.rating_pending.setdefault(pid,OrderedDict())[token]=stars;self.pending_count+=1;self.writing=True
        self.rating_versions[pid]=self.rating_versions.get(pid,0)+1
        self.info[pid]=dict(info,stars=stars,state='ready',writable=True,pending=True,write_error='');self.scanning=False;self.remaining=[];self.read_button.setText('读取全部照片评级')
        self.update_caption(pid)
        # File backup, verified writes and history remain serial, but no longer
        # block the next input or the first visible response.
        self.host.op(lambda:records.write(pid,stars,self.engine),
                     lambda result:self.grade_finished(pid,token,result,None,library),
                     lambda message:self.grade_finished(pid,token,None,message,library))
        if advance and self.auto.isChecked():self.move(1)
        self.host.update_actions()
    def grade_finished(self,pid,token,result,error,library):
        if library is not self.library:return
        pending=self.rating_pending.get(pid)
        if not pending or token not in pending:return
        pending.pop(token);self.pending_count-=1
        if result:self.rating_confirmed[pid]=result;self.rating_failures.pop(pid,None);self.tools.after_rating(result)
        if error:self.rating_failures[pid]=str(error)
        confirmed=self.rating_confirmed[pid]
        if pending:
            self.info[pid]=dict(confirmed,stars=next(reversed(pending.values())),state='ready',writable=True,pending=True)
        else:
            self.rating_pending.pop(pid);self.info[pid]=dict(confirmed,pending=False,write_error=error or '')
            self.rating_confirmed.pop(pid,None)
        self.writing=bool(self.pending_count);self.update_caption(pid)
        if error:
            photo=self.images[pid][0] if pid in self.images else self.thumb_photos.get(pid,{})
            self.host.error('评级未保存：'+photo.get('name',str(pid))+' · '+str(error))
            if pid not in self.rating_pending:self.read(pid,force=True)
        self.update_controls();self.host.update_actions();self.visible()
        if not self.writing:
            if self.rating_failures:self.note.setText(f'{len(self.rating_failures)} 项评级未保存，请检查后重新打分，详情见提示。')
            else:self.note.setText('评级已写入并验证 · 保留 / 删除标记不变')
            if self.active and (self.filter.currentIndex() or self.tools.filter_dirty):self.tools.filter_dirty=False;self.reload()
    def undo(self):
        if self.writing or not self.records or not self.undo_button.isEnabled():return
        records=self.records;self.change(lambda:records.undo(self.engine),advance=False)
    def change(self,work,advance=True):
        self.writing=True;self.undoing=True;self.read_revision+=1;self.scanning=False;self.remaining=[];self.update_controls();self.host.update_actions()
        def done(result):
            self.writing=False;self.undoing=False;pid=result['photo_id'];self.info[pid]=result;self.update_caption(pid);self.tools.after_rating(result)
            if advance and self.auto.isChecked():self.move(1)
            elif not advance and pid in self.ids:self.select(self.ids.index(pid))
            self.update_controls();self.visible();self.host.update_actions();self.note.setText('已写入 Windows 系统评级：'+self.star_text(result)+' · 保留 / 删除标记不变')
            if self.filter.currentIndex():self.reload()
        def failed(message):self.writing=False;self.undoing=False;self.update_controls();self.host.update_actions();self.host.error(message)
        self.host.op(work,done,failed)
    def zoom(self,point=None,pane=None):
        pane=pane or self.pane
        if not pane.pixmap:return
        if pane.detail:pane.unzoom(self.show_current);return
        pane.zoom_at(point);pid=pane.photo['id'];gen=self.generation;library=self.library
        def work():
            from desktop import full_qimage
            return full_qimage(library.preview_image(pid,0))
        def done(image):
            if self.valid(gen) and pane.photo and pid==pane.photo['id'] and pane.detail:
                pixmap=QPixmap.fromImage(image);pixmap.setDevicePixelRatio(self.devicePixelRatioF());pane.install_full_image(pixmap)
        self.host.submit(work,done,self.host.error,self.host.detail_pool)
    def unzoom(self):
        for pane in self.panes:
            if pane.detail:pane.unzoom(self.show_current)
    def shutdown(self):self.tools.shutdown();self.deactivate();self.engine.close();self.pool.clear();self.pool.waitForDone()
