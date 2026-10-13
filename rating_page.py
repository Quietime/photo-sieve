"""Independent photo grading; displayed stars always come from Windows System.Rating."""
from collections import OrderedDict
from pathlib import Path
from PySide6.QtCore import Qt,QSettings,QThreadPool,QTimer,QSize
from PySide6.QtGui import QIcon,QPixmap,QShortcut,QKeySequence
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QCheckBox,QSplitter
from photo_grid import PhotoGrid
from widgets import PhotoView
from rating_records import RatingRecords
from rating_engine import RatingEngine
import keys

ACTIONS=[(f'star{i}',f'{i} 星',(str(i),),True) for i in range(1,6)]+[
 ('clear','清除评级',('0',),True),('previous','上一张',('Left',),True),
 ('next','下一张',('Right',),True),('undo','撤销评级',('Ctrl+Z',),True),
 ('zoom','放大到 100%',('Z',),False),('unzoom','还原缩放',('Esc',),False)]

class RatingPage(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host;self.library=None;self.records=None
        self.engine=RatingEngine(host.data_root/'logs');self.pool=QThreadPool(self);self.pool.setMaxThreadCount(2)
        self.generation=0;self.active=False;self.writing=False;self.ids=[];self.index=0
        self.info={};self.reading=set();self.thumbs=OrderedDict();self.images=OrderedDict();self.loading=set();self.scanning=False;self.remaining=[];self.shortcut_objects=[]
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout=QVBoxLayout(self);layout.setContentsMargins(24,12,24,16);layout.setSpacing(14)
        row=QHBoxLayout();heading=QLabel('照片分级');heading.setObjectName('heading');row.addWidget(heading);row.addStretch()
        self.scope=QComboBox();self.scope.addItems(['全部照片','保留照片']);row.addWidget(self.scope)
        self.filter=QComboBox();self.filter.addItems(['全部星级','未评级（0 星）']+[f'{i} 星' for i in range(1,6)]);row.addWidget(self.filter)
        self.auto=QCheckBox('评级后下一张');self.auto.setChecked(QSettings('PhotoSieve','PhotoSieve').value('rating_auto_next',True,type=bool));row.addWidget(self.auto)
        self.auto.toggled.connect(lambda value:QSettings('PhotoSieve','PhotoSieve').setValue('rating_auto_next',value))
        self.read_button=host.button('读取全部系统评级',self.toggle_read);row.addWidget(self.read_button);layout.addLayout(row)
        self.note=QLabel('星级写入照片的 Windows 属性；保留与删除标记独立。格式支持取决于 Windows 文件属性处理器。');self.note.setObjectName('caption');self.note.setWordWrap(True);layout.addWidget(self.note)
        split=QSplitter();self.pane=PhotoView();self.pane.setMinimumWidth(400);split.addWidget(self.pane)
        self.grid=PhotoGrid();self.grid.setMinimumWidth(280);self.grid.tile_height=180;self.grid.setGridSize(QSize(196,180));self.grid.setWordWrap(True);split.addWidget(self.grid);split.setStretchFactor(0,3);split.setStretchFactor(1,2);split.setSizes([800,500]);layout.addWidget(split,1)
        self.name=QLabel('请选择照片文件夹');self.name.setAlignment(Qt.AlignmentFlag.AlignCenter);layout.addWidget(self.name)
        self.status=QLabel('尚未读取 Windows 系统评级');self.status.setWordWrap(True);self.status.setAlignment(Qt.AlignmentFlag.AlignCenter);self.status.setObjectName('caption');layout.addWidget(self.status)
        controls=QHBoxLayout();controls.addStretch();self.previous=host.button('上一张',lambda:self.move(-1));controls.addWidget(self.previous)
        self.star_buttons=[]
        for star in range(1,6):
            button=host.button('★'*star,lambda _,star=star:self.grade(star));button.setAccessibleName(f'设置 {star} 星');controls.addWidget(button);self.star_buttons.append(button)
        self.clear_button=host.button('清除评级',lambda:self.grade(0));controls.addWidget(self.clear_button)
        self.undo_button=host.button('撤销评级',self.undo);controls.addWidget(self.undo_button)
        self.next=host.button('下一张',lambda:self.move(1));controls.addWidget(self.next);controls.addStretch();layout.addLayout(controls)
        self.scope.currentIndexChanged.connect(self.reload);self.filter.currentIndexChanged.connect(self.reload)
        self.grid.itemClicked.connect(lambda item:self.select(item.row));self.grid.verticalScrollBar().valueChanged.connect(self.visible)
        self.grid.icon_for=lambda pid:self.thumbs.get(pid,host.tile)
        self.pane.clicked.connect(lambda photo,point:self.zoom(point));self.pane.escape_requested.connect(self.unzoom);self.pane.next_requested.connect(lambda:self.move(1))
        self.resize_timer=QTimer(self);self.resize_timer.setSingleShot(True);self.resize_timer.timeout.connect(self.show_current);self.pane.resized.connect(lambda:self.resize_timer.start(150))
        self.apply_keys(keys.load(QSettings('PhotoSieve','PhotoSieve'),ACTIONS,'rating_shortcuts/'))
    def apply_keys(self,chosen):
        self.chosen=chosen
        for shortcut in self.shortcut_objects:shortcut.setEnabled(False);shortcut.deleteLater()
        self.shortcut_objects=[]
        handlers={f'star{i}':lambda i=i:self.grade(i) for i in range(1,6)}
        handlers.update(clear=lambda:self.grade(0),previous=lambda:self.move(-1),next=lambda:self.move(1),undo=self.undo,zoom=self.zoom,unzoom=self.unzoom)
        for action,_,_,repeat in ACTIONS:
            for key in chosen[action]:
                if not key:continue
                shortcut=QShortcut(QKeySequence(key),self);shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut);shortcut.setAutoRepeat(repeat);shortcut.activated.connect(handlers[action]);self.shortcut_objects.append(shortcut)
        for button,action in [(self.previous,'previous'),(self.next,'next'),(self.clear_button,'clear'),(self.undo_button,'undo')]+[(b,f'star{i}') for i,b in enumerate(self.star_buttons,1)]:
            button.setToolTip(' / '.join(keys.label(k) for k in chosen[action] if k) or '未设置快捷键')
    def edit_keys(self):
        dialog=keys.ShortcutDialog(self.host,self.chosen,ACTIONS,'照片分级快捷键','分级与照片筛选使用独立快捷键。每项允许留空，仅用按钮操作。')
        def saved(chosen):keys.save(QSettings('PhotoSieve','PhotoSieve'),chosen,'rating_shortcuts/');self.apply_keys(chosen);self.host.sync_shortcut_mode()
        dialog.saved.connect(saved);dialog.exec()
    def activate(self):
        self.active=True;self.setFocus()
        if not self.host.session_active:return
        if self.library is not self.host.library:
            self.library=self.host.library;self.records=RatingRecords(self.library);self.info={};self.thumbs.clear();self.images.clear()
        self.reload()
    def deactivate(self):
        self.active=False;self.generation+=1;self.scanning=False;self.resize_timer.stop();self.reading.clear();self.loading.clear()
    def reload(self,*_):
        if not self.active or self.writing or not self.records:return
        self.generation+=1;gen=self.generation;self.scanning=False;self.remaining=[];self.reading.clear();self.loading.clear();self.read_button.setText('读取全部系统评级')
        records=self.records;scope='keep' if self.scope.currentIndex() else 'all';stars=self.filter.currentIndex()-1 if self.filter.currentIndex() else None
        self.status.setText('正在读取分级列表…')
        def done(ids):
            if not self.valid(gen):return
            self.ids=ids;self.index=0;self.info={};self.grid.captions.clear();self.grid.tips.clear();self.grid.set_ids(ids);self.show_current();self.visible()
            if stars is not None:self.note.setText('仅显示已读取的 Windows 星级。首次筛选或在资源管理器中改过星级后，请点击“读取全部系统评级”刷新。')
            else:self.note.setText(f'{len(ids):,} 张照片 · 星级写入 Windows 属性，保留与删除标记独立；缩略图按需加载。')
        self.host.submit(lambda:records.ids(scope,stars),done,self.host.error)
    def valid(self,gen):return self.active and gen==self.generation and self.library is self.host.library and not self.host.closed
    def pid(self):return self.ids[self.index] if self.ids and 0<=self.index<len(self.ids) else None
    def select(self,index):
        if self.writing:return
        self.index=index;self.pane.show_photo(None);self.show_current();self.setFocus()
    def move(self,delta):
        if self.writing or not self.ids:return
        self.select(max(0,min(len(self.ids)-1,self.index+delta)))
    def show_current(self):
        if not self.active:return
        if self.pane.detail:return
        pid=self.pid()
        if pid is None:self.pane.show_photo(None);self.name.setText('没有符合条件的照片');self.status.setText('可更换照片范围，或读取全部系统评级后再按星级筛选');self.update_controls();return
        self.grid.setCurrentRow(self.index);self.grid.scrollTo(self.grid.photo_model.index(self.index));self.preview(pid,large=True);self.read(pid);self.update_controls()
    def visible(self,*_):
        if not self.active or not self.records:return
        for pid in self.grid.visible_ids():self.preview(pid);self.read(pid)
    def preview(self,pid,large=False):
        key=(pid,large)
        if key in self.loading:return
        if large and pid in self.images:
            photo,pix=self.images[pid];self.pane.show_photo(photo,pix);self.name.setText(f'{photo["name"]}  ·  {self.index+1:,} / {len(self.ids):,}');return
        if not large and pid in self.thumbs:return
        if len(self.loading)>=6:return
        self.loading.add(key);gen=self.generation;library=self.library
        if large:self.pane.show_photo({'id':pid},message='正在读取照片…')
        width=max(300,min(1600,self.pane.width()));height=max(200,min(1200,self.pane.height()))
        def work():
            from desktop import display_image
            photo=library.public(library.photo(pid));result=display_image(library.preview_image(pid,1600 if large else 320),width if large else 176,height if large else 118,(8,8));return photo,result
        def done(value):
            if not self.valid(gen):return
            self.loading.discard(key);photo,result=value;pix=QPixmap.fromImage(result[0][0])
            if large:
                self.images[pid]=(photo,pix)
                while len(self.images)>3:self.images.popitem(last=False)
                if pid==self.pid():self.pane.show_photo(photo,pix);self.name.setText(f'{photo["name"]}  ·  {self.index+1:,} / {len(self.ids):,}')
            else:
                self.thumbs[pid]=QIcon(pix)
                while len(self.thumbs)>128:self.thumbs.popitem(last=False)
                self.grid.captions[pid]=photo['name']+'\n'+self.star_text(self.info.get(pid));self.grid.tips[pid]=photo['path'];row=self.grid.index_of(pid)
                if row>=0:self.grid.photo_model.changed(row)
            if self.pid() and self.pid() not in self.images:self.preview(self.pid(),True)
            self.visible()
        def failed(message):
            if not self.valid(gen):return
            self.loading.discard(key)
            # Do not retry a broken preview in a tight loop.
            if large and pid==self.pid():self.pane.show_photo({'id':pid},error=message)
            if not large:self.thumbs[pid]=self.host.tile
        self.host.submit(work,done,failed,self.host.image_pool if large else self.host.thumb_pool,100 if large else 0)
    @staticmethod
    def star_text(info):
        if not info:return '尚未读取'
        if info['state']!='ready':return 'Windows 评级不可用'
        return '★'*info['stars']+'☆'*(5-info['stars']) if info['stars'] else '未评级'
    def read(self,pid,force=False):
        if pid in self.reading or len(self.reading)>=2 or self.writing:return
        if pid in self.info and not force:return
        self.reading.add(pid);gen=self.generation;records=self.records
        def work():
            cached={} if force else records.cached([pid])
            return cached.get(pid) or records.read(pid,self.engine)
        def done(info):
            if not self.valid(gen):return
            self.reading.discard(pid);self.info[pid]=info;self.update_caption(pid);self.update_controls();self.pump_reads();self.visible()
        def failed(message):done({'state':'error','stars':None,'writable':False,'error':message})
        self.host.submit(work,done,failed,self.pool,100 if pid==self.pid() else 0)
    def update_caption(self,pid):
        text=self.grid.captions.get(pid,'照片 #'+str(pid)).split('\n')[0];self.grid.captions[pid]=text+'\n'+self.star_text(self.info.get(pid));row=self.grid.index_of(pid)
        if row>=0:self.grid.photo_model.changed(row)
    def update_controls(self):
        info=self.info.get(self.pid());writable=bool(info and info['state']=='ready' and info['writable']);allowed=writable and not self.writing
        for button in self.star_buttons+[self.clear_button]:button.setEnabled(allowed)
        self.previous.setEnabled(bool(self.ids) and self.index>0 and not self.writing);self.next.setEnabled(bool(self.ids) and self.index+1<len(self.ids) and not self.writing)
        self.scope.setEnabled(not self.writing);self.filter.setEnabled(not self.writing);self.read_button.setEnabled(bool(self.records) and not self.writing)
        undo=False
        if self.records:
            with self.library.connect() as db:undo=bool(db.execute('SELECT 1 FROM rating_history LIMIT 1').fetchone())
        self.undo_button.setEnabled(undo and not self.writing)
        if self.writing:self.status.setText('正在安全写入并验证 Windows 星级…')
        elif info:self.status.setText('Windows 系统评级：'+self.star_text(info)+(' · '+info.get('error','') if info.get('error') else ' · 点击星级写入，0 清除'))
        elif self.pid():self.status.setText('正在读取 Windows 系统评级…')
    def toggle_read(self):
        if self.scanning:self.scanning=False;self.remaining=[];self.read_button.setText('读取全部系统评级');return
        if not self.records or self.writing:return
        self.scanning=True;self.remaining=None;gen=self.generation;records=self.records;scope='keep' if self.scope.currentIndex() else 'all'
        def done(ids):
            if not self.valid(gen) or not self.scanning:return
            self.remaining=list(reversed(ids));self.total=len(ids);self.pump_reads()
        self.host.submit(lambda:records.ids(scope),done,self.host.error)
    def pump_reads(self):
        if not self.scanning or self.writing or self.remaining is None:return
        while self.remaining and len(self.reading)<2:self.read(self.remaining.pop(),True)
        self.read_button.setText(f'停止读取 · {self.total-len(self.remaining)-len(self.reading):,} / {self.total:,}')
        if not self.remaining and not self.reading:self.scanning=False;self.read_button.setText('读取全部系统评级');self.reload()
    def grade(self,stars):
        info=self.info.get(self.pid())
        if self.writing or not info or info['state']!='ready' or not info['writable']:return
        if info['stars']==stars:return
        pid=self.pid();records=self.records;self.change(lambda:records.write(pid,stars,self.engine))
    def undo(self):
        if self.writing or not self.records or not self.undo_button.isEnabled():return
        records=self.records;self.change(lambda:records.undo(self.engine),advance=False)
    def change(self,work,advance=True):
        self.writing=True;self.scanning=False;self.remaining=[];self.update_controls();self.host.update_actions()
        def done(result):
            self.writing=False;pid=result['photo_id'];self.info[pid]=result;self.images.pop(pid,None);self.update_caption(pid)
            if advance and self.auto.isChecked():self.move(1)
            elif not advance and pid in self.ids:self.select(self.ids.index(pid))
            self.update_controls();self.host.update_actions();self.host.toast.show_message('已写入 Windows 系统评级：'+self.star_text(result),'info')
            if self.filter.currentIndex():self.reload()
        def failed(message):self.writing=False;self.update_controls();self.host.update_actions();self.host.error(message)
        self.host.op(work,done,failed)
    def zoom(self,point=None):
        if not self.pane.pixmap:return
        if self.pane.detail:self.unzoom();return
        self.pane.zoom_at(point);pid=self.pid();gen=self.generation;library=self.library
        def work():
            from desktop import full_qimage
            return full_qimage(library.preview_image(pid,0))
        def done(image):
            if self.valid(gen) and pid==self.pid() and self.pane.detail:self.pane.install_full_image(QPixmap.fromImage(image))
        self.host.submit(work,done,self.host.error,self.host.detail_pool)
    def unzoom(self):
        if self.pane.detail:self.pane.unzoom(self.show_current)
    def shutdown(self):self.deactivate();self.engine.close();self.pool.clear();self.pool.waitForDone()
