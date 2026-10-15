"""Independent photo grading; displayed stars come from verified photo metadata."""
from collections import OrderedDict
from pathlib import Path
from PySide6.QtCore import Qt,QSettings,QThreadPool,QTimer,QSize
from PySide6.QtGui import QIcon,QPixmap,QShortcut,QKeySequence
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QCheckBox,QStackedWidget,QScrollArea
from photo_grid import PhotoGrid
from filmstrip import MixedFilmstrip
from apple_ui import GlassDock
from widgets import PhotoView
from rating_records import RatingRecords
from rating_engine import RatingEngine
from rating_combo import RoundedCombo
import keys

ACTIONS=[(f'star{i}',f'{i} 星',(str(i),),True) for i in range(1,6)]+[
 ('clear','清除评级',('0',),True),('previous','上一张',('Left',),True),
 ('next','下一张',('Right',),True),('undo','撤销评级',('Ctrl+Z',),True),
 ('zoom','放大到 100%',('Z',),False),('unzoom','还原缩放',('Esc',),False)]

class RatingPage(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host;self.library=None;self.records=None
        self.engine=RatingEngine(host.data_root/'logs');self.pool=QThreadPool(self);self.pool.setMaxThreadCount(2)
        self.generation=0;self.read_revision=0;self.active=False;self.writing=False;self.ids=[];self.index=0
        self.info={};self.source_widths={};self.image_sizes={};self.fingerprints={};self.preview_errors=set();self.reading=set();self.thumbs=OrderedDict();self.images=OrderedDict();self.loading=set();self.scanning=False;self.remaining=[];self.shortcut_objects=[]
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout=QVBoxLayout(self);layout.setContentsMargins(24,12,24,16);layout.setSpacing(14)
        row=QHBoxLayout();heading=QLabel('照片分级');heading.setObjectName('heading');row.addWidget(heading);row.addStretch()
        options=QHBoxLayout();self.scope=RoundedCombo();self.scope.addItems(['全部照片','保留照片']);options.addWidget(self.scope)
        self.filter=RoundedCombo();self.filter.addItems(['全部星级','未评级（0 星）']+[f'{i} 星' for i in range(1,6)]);options.addWidget(self.filter)
        self.auto=QCheckBox('评级后下一张');self.auto.setChecked(QSettings('PhotoSieve','PhotoSieve').value('rating_auto_next',True,type=bool));options.addWidget(self.auto)
        self.auto.toggled.connect(lambda value:QSettings('PhotoSieve','PhotoSieve').setValue('rating_auto_next',value))
        self.read_button=host.button('读取全部照片评级',self.toggle_read);row.addWidget(self.read_button);layout.addLayout(row);layout.addLayout(options)
        self.note=QLabel('星级写入照片文件。ARW 支持标准原图星级；Windows 是否能显示会单独提示。保留与删除标记独立。');self.note.setObjectName('caption');self.note.setWordWrap(True);layout.addWidget(self.note)
        self.gallery_button=host.button('缩略图库',self.toggle_gallery);options.addWidget(self.gallery_button);options.addStretch()
        self.body=QStackedWidget();layout.addWidget(self.body,1);review=QWidget();pair=QHBoxLayout(review);pair.setContentsMargins(0,0,0,0);pair.setSpacing(16)
        self.panes=[];self.names=[]
        for index in range(2):
            column=QVBoxLayout();column.setSpacing(4);role=QLabel('左图 · 当前评级（星级写入此图）' if index==0 else '右图 · 下一张对比');role.setObjectName('caption');role.setAlignment(Qt.AlignmentFlag.AlignCenter);column.addWidget(role)
            pane=PhotoView();column.addWidget(pane,1);name=QLabel('请选择照片文件夹');name.setAlignment(Qt.AlignmentFlag.AlignCenter);column.addWidget(name);pair.addLayout(column,1);self.panes.append(pane);self.names.append(name)
            pane.clicked.connect(lambda photo,point,pane=pane:self.zoom(point,pane));pane.escape_requested.connect(self.unzoom);pane.next_requested.connect(lambda:self.move(1))
        self.pane=self.panes[0];self.name=self.names[0];self.body.addWidget(review)
        self.grid=PhotoGrid();self.grid.tile_height=180;self.grid.setGridSize(QSize(196,180));self.grid.setWordWrap(True);self.body.addWidget(self.grid)
        self.status=QLabel('尚未读取 Windows 系统评级');self.status.setWordWrap(True);self.status.setAlignment(Qt.AlignmentFlag.AlignCenter);self.status.setObjectName('caption');layout.addWidget(self.status)
        self.action_row=QScrollArea();self.action_row.setWidgetResizable(True);self.action_row.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded);self.action_row.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.action_row.setFixedHeight(52);self.action_row.setStyleSheet('QScrollArea {background:transparent;border:0;}');action_content=QWidget();self.action_row.setWidget(action_content);outer_controls=QHBoxLayout(action_content);outer_controls.setContentsMargins(0,0,0,0);outer_controls.addStretch();self.dock=GlassDock();controls=QHBoxLayout(self.dock);controls.setContentsMargins(6,2,6,2);controls.setSpacing(4);self.previous=host.button('上一张',lambda:self.move(-1));controls.addWidget(self.previous)
        self.star_buttons=[]
        for star in range(1,6):
            button=host.button('★'*star,lambda _,star=star:self.grade(star));button.setAccessibleName(f'设置 {star} 星');controls.addWidget(button);self.star_buttons.append(button)
        self.clear_button=host.button('清除评级',lambda:self.grade(0));controls.addWidget(self.clear_button)
        self.undo_button=host.button('撤销评级',self.undo);controls.addWidget(self.undo_button)
        self.next=host.button('下一张',lambda:self.move(1));controls.addWidget(self.next);outer_controls.addWidget(self.dock);outer_controls.addStretch();layout.addWidget(self.action_row);self.action_row.setVisible(host.rating_actionbar_visible)
        self.dock.register([self.previous]+self.star_buttons+[self.clear_button,self.undo_button,self.next])
        self.strip=MixedFilmstrip();self.strip.icon_for=lambda pid:self.thumbs.get(pid,host.placeholder);self.strip.itemClicked.connect(lambda item:self.select(item.row));self.strip.horizontalScrollBar().valueChanged.connect(self.visible);layout.addWidget(self.strip)
        self.scope.currentIndexChanged.connect(self.reload);self.filter.currentIndexChanged.connect(self.reload)
        self.grid.itemClicked.connect(lambda item:self.open_gallery_item(item));self.grid.verticalScrollBar().valueChanged.connect(self.visible)
        self.grid.icon_for=lambda pid:self.thumbs.get(pid,host.tile)
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
                shortcut=QShortcut(QKeySequence(key),self);shortcut.setContext(Qt.ShortcutContext.WindowShortcut);shortcut.setAutoRepeat(repeat);shortcut.activated.connect(handlers[action]);self.shortcut_objects.append(shortcut)
        for button,action in [(self.previous,'previous'),(self.next,'next'),(self.clear_button,'clear'),(self.undo_button,'undo')]+[(b,f'star{i}') for i,b in enumerate(self.star_buttons,1)]:
            button.variant='dock';button.setFixedHeight(32);button.set_shortcut(keys.first(chosen,action),' / '.join(keys.label(k) for k in chosen[action] if k) or '未设置快捷键')
    def edit_keys(self):
        dialog=keys.ShortcutDialog(self.host,self.chosen,ACTIONS,'照片分级快捷键','分级与照片筛选使用独立快捷键。每项允许留空，仅用按钮操作。')
        def saved(chosen):keys.save(QSettings('PhotoSieve','PhotoSieve'),chosen,'rating_shortcuts/');self.apply_keys(chosen);self.host.sync_shortcut_mode()
        dialog.saved.connect(saved);dialog.exec()
    def activate(self):
        self.active=True;self.setFocus()
        if not self.host.session_active:return
        if self.library is not self.host.library:
            self.library=self.host.library;self.records=RatingRecords(self.library);self.info={};self.clear_previews()
        self.reload()
    def clear_previews(self):
        self.images.clear();self.thumbs.clear();self.image_sizes.clear();self.source_widths.clear();self.fingerprints.clear();self.preview_errors.clear()
    def deactivate(self):
        self.active=False;self.generation+=1;self.scanning=False;self.resize_timer.stop();self.reading.clear();self.loading.clear()
    def reload(self,*_):
        if not self.active or self.writing or not self.records:return
        self.generation+=1;gen=self.generation;self.scanning=False;self.remaining=[];self.reading.clear();self.loading.clear();self.read_button.setText('读取全部照片评级')
        records=self.records;scope='keep' if self.scope.currentIndex() else 'all';stars=self.filter.currentIndex()-1 if self.filter.currentIndex() else None
        self.status.setText('正在读取分级列表…')
        cached=list(self.fingerprints);library=self.library
        def work():
            ids=records.ids(scope,stars);fingerprints={}
            with library.connect() as db:
                for start in range(0,len(cached),500):
                    part=cached[start:start+500]
                    rows=db.execute('SELECT id,size,mtime FROM photos WHERE present=1 AND id IN ('+','.join('?' for _ in part)+')',part)
                    fingerprints.update((row['id'],(row['size'],row['mtime'])) for row in rows)
            return ids,fingerprints
        def done(value):
            if not self.valid(gen):return
            ids,fingerprints=value
            for pid in cached:
                if self.fingerprints.get(pid)!=fingerprints.get(pid):
                    self.images.pop(pid,None);self.thumbs.pop(pid,None);self.image_sizes.pop(pid,None);self.source_widths.pop(pid,None);self.fingerprints.pop(pid,None)
            self.preview_errors.clear()
            self.ids=ids;self.index=0;self.info={};self.grid.captions.clear();self.grid.tips.clear();self.grid.set_ids(ids);self.strip.set_ids(ids);self.show_current();self.visible()
            if stars is not None:self.note.setText('仅显示已读取的 Windows 星级。首次筛选或在资源管理器中改过星级后，请点击“读取全部照片评级”刷新。')
            else:self.note.setText(f'{len(ids):,} 张照片 · 复用整库磁盘缓存，附近照片提前载入内存；星级与保留 / 删除标记独立。')
        self.host.submit(work,done,self.host.error)
    def valid(self,gen):return self.active and gen==self.generation and self.library is self.host.library and not self.host.closed
    def pid(self):return self.ids[self.index] if self.ids and 0<=self.index<len(self.ids) else None
    def select(self,index):
        if self.writing:return
        self.index=index
        for pane in self.panes:
            if pane.detail:pane.show_photo(None)
        self.show_current();self.setFocus()
    def move(self,delta):
        if self.writing or not self.ids:return
        self.select(max(0,min(len(self.ids)-1,self.index+delta)))
    def open_gallery_item(self,item):
        self.body.setCurrentIndex(0);self.gallery_button.setText('缩略图库');self.select(item.row)
    def toggle_gallery(self):
        self.body.setCurrentIndex(1-self.body.currentIndex());self.gallery_button.setText('返回双图' if self.body.currentIndex() else '缩略图库');self.visible();self.setFocus()
    def show_current(self):
        if not self.active:return
        pid=self.pid()
        if pid is None:
            for pane,name in zip(self.panes,self.names):pane.show_photo(None);name.setText('没有符合条件的照片')
            self.status.setText('可更换照片范围，或读取全部照片评级后再按星级筛选');self.update_controls();return
        self.grid.setCurrentRow(self.index);self.grid.scrollTo(self.grid.photo_model.index(self.index));self.strip.setCurrentRow(self.index);self.strip.scrollTo(self.strip.photo_model.index(self.index))
        for offset,pane in enumerate(self.panes):
            if self.index+offset<len(self.ids):
                target=self.ids[self.index+offset]
                if not pane.detail:self.preview(target,large=True)
            else:pane.show_photo(None);self.names[offset].setText('没有下一张')
        self.read(pid);self.update_controls();self.prefetch()
    def install_preview(self,pid,photo,pix):
        for offset,pane in enumerate(self.panes):
            if self.index+offset<len(self.ids) and self.ids[self.index+offset]==pid and not pane.detail:
                pane.show_photo(photo,pix);pane.source_width=self.source_widths.get(pid,pix.width());self.names[offset].setText(f'{photo["name"]}  ·  {self.index+offset+1:,} / {len(self.ids):,}')
    def visible(self,*_):
        if not self.active or not self.records:return
        for pid in (self.grid.visible_ids() if self.body.currentIndex() else self.strip.visible_ids()):self.preview(pid);self.read(pid)
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
    def preview(self,pid,large=False):
        key=(pid,large)
        if self.host.cache_clearing or key in self.preview_errors:return
        if key in self.loading:return
        size=self.preview_size()
        if large and pid in self.images and self.image_sizes.get(pid)==size:
            photo,pix=self.images[pid];self.install_preview(pid,photo,pix);return
        if not large and pid in self.thumbs:return
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
            photo=library.public(library.photo(pid));result=display_image(library.preview_image(pid,edge if large else 320),width if large else round(176*dpr),height if large else round(118*dpr),(8,8));return photo,result
        def done(value):
            if not self.valid(gen):return
            self.loading.discard(key);photo,result=value;pix=QPixmap.fromImage(result[0][0]);pix.setDevicePixelRatio(dpr)
            self.fingerprints[pid]=(photo['size'],photo['mtime'])
            if large:
                self.source_widths[pid]=result[2][0];self.images[pid]=(photo,pix);self.image_sizes[pid]=size
                self.install_preview(pid,photo,pix)
            else:
                self.thumbs[pid]=QIcon(pix);self.strip.set_preview_width(pid,pix)
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
            if not large:self.thumbs[pid]=self.host.tile
            self.prefetch();self.visible()
        current=pid in self.ids[self.index:self.index+2]
        self.host.submit(work,done,failed,self.host.image_pool if large else self.host.thumb_pool,100 if large and current else -5 if large else 0)
    @staticmethod
    def star_text(info):
        if not info:return '尚未读取'
        if info['state']!='ready':return 'Windows 评级不可用'
        return '★'*info['stars']+'☆'*(5-info['stars']) if info['stars'] else '未评级'
    def read(self,pid,force=False):
        if pid in self.reading or len(self.reading)>=2 or self.writing:return
        if pid in self.info and not force:return
        self.reading.add(pid);gen=self.generation;read_revision=self.read_revision;records=self.records
        def work():
            cached={} if force else records.cached([pid])
            return cached.get(pid) or records.read(pid,self.engine)
        def done(info):
            if not self.valid(gen):return
            self.reading.discard(pid)
            if read_revision!=self.read_revision:
                if not self.writing:self.visible()
                return
            self.info[pid]=info;self.update_caption(pid);self.update_controls();self.pump_reads();self.visible()
        def failed(message):done({'state':'error','stars':None,'writable':False,'error':message})
        self.host.submit(work,done,failed,self.pool,100 if pid==self.pid() else 0)
    def update_caption(self,pid):
        text=self.grid.captions.get(pid,'照片 #'+str(pid)).split('\n')[0];self.grid.captions[pid]=text+'\n'+self.star_text(self.info.get(pid));row=self.grid.index_of(pid)
        if row>=0:self.grid.photo_model.changed(row)
    def update_controls(self):
        info=self.info.get(self.pid());writable=bool(info and info['state']=='ready' and info['writable']);allowed=writable and not self.writing and not self.host.busy and not self.host.job_running()
        for button in self.star_buttons+[self.clear_button]:button.setEnabled(allowed)
        self.previous.setEnabled(bool(self.ids) and self.index>0 and not self.writing);self.next.setEnabled(bool(self.ids) and self.index+1<len(self.ids) and not self.writing)
        self.scope.setEnabled(not self.writing);self.filter.setEnabled(not self.writing);self.read_button.setEnabled(bool(self.records) and not self.writing)
        undo=False
        if self.records:
            with self.library.connect() as db:undo=bool(db.execute('SELECT 1 FROM rating_history h JOIN photos p ON p.id=h.photo_id WHERE p.present=1 LIMIT 1').fetchone())
        self.undo_button.setEnabled(undo and not self.writing and not self.host.busy and not self.host.job_running())
        if self.writing:self.status.setText('正在安全写入并验证照片星级…')
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
    def grade(self,stars):
        info=self.info.get(self.pid())
        if self.host.busy or self.host.job_running() or self.writing or not info or info['state']!='ready' or not info['writable']:return
        if info['stars']==stars:
            if self.auto.isChecked():self.move(1)
            return
        pid=self.pid();records=self.records;self.change(lambda:records.write(pid,stars,self.engine))
    def undo(self):
        if self.writing or not self.records or not self.undo_button.isEnabled():return
        records=self.records;self.change(lambda:records.undo(self.engine),advance=False)
    def change(self,work,advance=True):
        self.writing=True;self.read_revision+=1;self.scanning=False;self.remaining=[];self.update_controls();self.host.update_actions()
        def done(result):
            self.writing=False;pid=result['photo_id'];self.info[pid]=result;self.update_caption(pid)
            if advance and self.auto.isChecked():self.move(1)
            elif not advance and pid in self.ids:self.select(self.ids.index(pid))
            self.update_controls();self.visible();self.host.update_actions();self.note.setText('已写入 Windows 系统评级：'+self.star_text(result)+' · 保留 / 删除标记不变')
            if self.filter.currentIndex():self.reload()
        def failed(message):self.writing=False;self.update_controls();self.host.update_actions();self.host.error(message)
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
    def shutdown(self):self.deactivate();self.engine.close();self.pool.clear();self.pool.waitForDone()
