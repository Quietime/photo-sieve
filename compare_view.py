"""Two/four cached photos with linked normalized zoom and pan, bounded native detail."""
from pathlib import Path
from PySide6.QtCore import Qt,QPointF,QTimer,Signal
from PySide6.QtGui import QPixmap,QTransform,QShortcut,QKeySequence
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QGridLayout,QWidget,QLabel,QCheckBox,QGraphicsPixmapItem
from widgets import PhotoView
from rating_combo import RoundedCombo
import theme

class ComparePane(PhotoView):
    activated=Signal()
    moved=Signal()
    def mousePressEvent(self,event):self.activated.emit();super().mousePressEvent(event)
    def mouseMoveEvent(self,event):super().mouseMoveEvent(event);self.moved.emit()
    def wheelEvent(self,event):self.activated.emit();super().wheelEvent(event);self.moved.emit()

class CompareDialog(QDialog):
    def __init__(self,page):
        super().__init__(page);self.page=page;self.host=page.host;self.library=page.library;self.generation=0;self.alive=True;self.selected=0;self.loading=False;self.detail_loading=set();self.driver=None;self.syncing=False;self.overlays={};self.detail_wanted={};self.detail_versions={};self.detail_active=False
        self.setWindowTitle('同步对比 · 星级只作用于高亮照片');self.resize(1400,900);self.setMinimumSize(720,520)
        group=page.tools.group_map.get(page.pid());self.ids=list(group['ids'] if group and len(group['ids'])>1 else page.ids);self.start=max(0,self.ids.index(page.pid()) if page.pid() in self.ids else 0)
        layout=QVBoxLayout(self);top=QHBoxLayout();self.count=RoundedCombo();self.count.addItems(['2 张对比','4 张对比']);top.addWidget(self.count);self.link=QCheckBox('同步放大与拖动');self.link.setChecked(True);top.addWidget(self.link)
        for text,fn in [('上一组',lambda:self.move(-1)),('下一组',lambda:self.move(1)),('还原',self.fit_all)]:top.addWidget(self.host.button(text,fn))
        top.addStretch();top.addWidget(self.host.button('完成',self.close));layout.addLayout(top)
        self.content=QWidget();self.grid=QGridLayout(self.content);layout.addWidget(self.content,1);self.panes=[];self.labels=[];self.frames=[]
        for i in range(4):
            frame=QWidget();frame.setObjectName(f'compareFrame{i}');column=QVBoxLayout(frame);column.setContentsMargins(4,4,4,4);pane=ComparePane();column.addWidget(pane,1);label=QLabel();label.setAlignment(Qt.AlignmentFlag.AlignCenter);column.addWidget(label);self.frames.append(frame);self.panes.append(pane);self.labels.append(label)
            pane.activated.connect(lambda i=i:self.activate(i));pane.clicked.connect(lambda photo,point,i=i:self.zoom(i,point));pane.moved.connect(self.sync);pane.zoom_changed.connect(self.sync)
        bottom=QHBoxLayout();bottom.addWidget(QLabel('点击高亮目标，再按 0～5 评级；拖动和滚轮同步对应位置。'))
        for stars in range(6):bottom.addWidget(self.host.button('清除' if not stars else '★'*stars,lambda _,stars=stars:self.grade(stars)))
        layout.addLayout(bottom);self.note=QLabel();layout.addWidget(self.note)
        for stars in range(6):
            key=QShortcut(QKeySequence(str(stars)),self);key.activated.connect(lambda stars=stars:self.grade(stars))
        self.detail_timer=QTimer(self);self.detail_timer.setSingleShot(True);self.detail_timer.timeout.connect(self.pump_detail)
        self.count.currentIndexChanged.connect(self.load)
        self.timer=QTimer(self);self.timer.setInterval(theme.FrameClock.interval(self));self.timer.timeout.connect(self.sync);self.timer.start()
        self.status_timer=QTimer(self);self.status_timer.setInterval(200);self.status_timer.timeout.connect(self.captions);self.status_timer.start();self.load()
    def load(self):
        self.generation+=1;gen=self.generation;self.loading=True;self.detail_loading.clear();self.detail_wanted.clear();self.detail_versions.clear();self.overlays.clear();self.detail_timer.stop();self.driver=None;self.selected=0
        count=4 if self.count.currentIndex() else 2;self.grid.setRowStretch(0,1);self.grid.setRowStretch(1,1 if count==4 else 0);self.grid.setColumnStretch(0,1);self.grid.setColumnStretch(1,1);self.visible_ids=self.ids[self.start:self.start+count]
        for frame in self.frames:self.grid.removeWidget(frame);frame.hide()
        for i in range(count):self.grid.addWidget(self.frames[i],i//2,i%2);self.frames[i].show();self.panes[i].show_photo(None,message='正在读取磁盘缓存…')
        for i in range(count,len(self.panes)):self.panes[i].show_photo(None)
        library=self.library;targets=list(self.visible_ids);size=(max(320,self.width()//2),max(250,self.height()//(2 if count==4 else 1)))
        def work():
            from desktop import display_image
            rows=[]
            for pid in targets:
                if not self.alive or gen!=self.generation:break
                photo=library.public(library.photo(pid))
                # Shared disk preview; original is requested only for active 100% detail.
                image=library.preview_image(pid,1600)
                result=display_image(image,*size,(8,8));rows.append((photo,result[0][0],result[2][0]))
            return rows
        def done(rows):
            if not self.alive or gen!=self.generation:return
            self.loading=False
            for i,(photo,image,width) in enumerate(rows):
                pix=QPixmap.fromImage(image);self.panes[i].show_photo(photo,pix);self.panes[i].source_width=width
            self.activate(0);self.captions()
        self.host.submit(work,done,lambda error:self.failed(error,gen),self.host.thumb_pool,5)
    def failed(self,error,gen):
        if self.alive and gen==self.generation:self.loading=False;self.note.setText(str(error))
    def move(self,direction):
        if not self.ids:return
        count=4 if self.count.currentIndex() else 2;self.start=max(0,min(len(self.ids)-1,self.start+direction*count));self.load()
    def activate(self,index):
        if index>=len(self.visible_ids):return
        self.selected=index;self.driver=self.panes[index]
        for i,frame in enumerate(self.frames):frame.setStyleSheet('QWidget#compareFrame'+str(i)+' {background:transparent;border:1px solid '+(theme.BLUE_HOVER if i==index else 'transparent')+';border-radius:6px;}')
        pid=self.visible_ids[index]
        if pid in self.page.ids and self.page.pid()!=pid:self.page.select(self.page.ids.index(pid))
        self.captions()
    def captions(self):
        if not self.alive:return
        for i,pid in enumerate(self.visible_ids):
            pane=self.panes[i];photo=pane.photo or {};info=self.page.info.get(pid)
            text=photo.get('name',f'照片 {pid}')+' · '+self.page.star_text(info)
            if self.labels[i].text()!=text:self.labels[i].setText(text)
        if not self.loading:self.note.setText(('原图细节 '+str(len(self.overlays))+'/'+str(len(self.visible_ids))+' · ' if any(p.detail for p in self.panes) else '')+'高亮照片可按 0～5 即时评级；后台验证保存。'+(' 已开启同步位置。' if self.link.isChecked() else ''))
    def grade(self,stars):
        if self.loading or not self.visible_ids:return
        self.activate(self.selected);self.page.grade(stars,advance=False);self.captions()
    def zoom(self,index,point):
        self.activate(index);pane=self.panes[index]
        if pane.detail:self.fit_all();return
        pane.zoom_at(point);self.detail(index)
    def detail(self,index):
        targets=range(len(self.visible_ids)) if self.link.isChecked() else (index,)
        for target in targets:
            pane=self.panes[target]
            if not pane.photo or not pane.pixmap:continue
            rect=pane.sceneRect();center=pane.mapToScene(pane.viewport().rect().center())
            point=(max(0,min(1,center.x()/max(1,rect.width()))),max(0,min(1,center.y()/max(1,rect.height()))))
            version=self.detail_versions.get(target,0)+1;self.detail_versions[target]=version
            self.detail_wanted[target]=(pane.photo['id'],point,version)
        self.detail_timer.start(100)
    def pump_detail(self):
        if not self.alive or self.detail_active or not self.detail_wanted:return
        index=next(iter(self.detail_wanted));pid,point,version=self.detail_wanted.pop(index);pane=self.panes[index]
        if not pane.detail:self.pump_detail();return
        gen=self.generation;library=self.library;self.detail_active=True
        def work():
            if not self.alive or gen!=self.generation:return None
            from desktop import full_qimage
            # Exactly one original can be decoded; only a bounded native-pixel
            # crop crosses to the GUI. The fitted cached photo remains underneath.
            image=library.preview_image(pid,0)
            try:
                width,height=image.size;edge=2048;left=max(0,min(width-edge,round(point[0]*width-edge/2)));top=max(0,min(height-edge,round(point[1]*height-edge/2)))
                box=(left,top,min(width,left+edge),min(height,top+edge));crop=image.crop(box)
                try:return full_qimage(crop),box,(width,height)
                finally:crop.close()
            finally:image.close()
        def done(result):
            self.detail_active=False
            if self.alive and gen==self.generation and self.detail_versions.get(index)==version and pane.detail and pane.photo and pane.photo['id']==pid and result:
                image,box,size=result;old=self.overlays.pop(index,None)
                if old:pane.scene().removeItem(old)
                overlay=QGraphicsPixmapItem(QPixmap.fromImage(image));overlay.setTransformationMode(Qt.TransformationMode.SmoothTransformation);rect=pane.sceneRect();scale=rect.width()/size[0]
                overlay.setScale(scale);overlay.setPos(box[0]*scale,box[1]*scale);overlay.setZValue(2);pane.scene().addItem(overlay);self.overlays[index]=overlay
            if self.alive:self.pump_detail()
        def failed(error):
            self.detail_active=False
            if self.alive and gen==self.generation:self.note.setText('原图细节暂未读到：'+str(error));self.pump_detail()
        self.host.submit(work,done,failed,self.host.detail_pool,10)
    def sync(self):
        if not self.alive or self.syncing or not self.link.isChecked() or self.driver is None or not self.driver.pixmap:return
        driver=self.driver;rect=driver.sceneRect();center=driver.mapToScene(driver.viewport().rect().center());fraction=((center.x()-rect.x())/max(1,rect.width()),(center.y()-rect.y())/max(1,rect.height()));ratio=driver.transform().m11()/max(.0001,driver.fit_scale())
        state=(id(driver),round(ratio,5),round(fraction[0],5),round(fraction[1],5))
        if getattr(self,'last_state',None)==state:return
        self.last_state=state;self.syncing=True
        try:
            for pane in self.panes[:len(self.visible_ids)]:
                if pane is driver or not pane.pixmap:continue
                pane.stop_zoom();pane.detail=driver.detail;pane.setDragMode(PhotoView.DragMode.ScrollHandDrag if pane.detail else PhotoView.DragMode.NoDrag)
                scale=pane.fit_scale()*ratio;pane.setTransform(QTransform.fromScale(scale,scale));r=pane.sceneRect();pane.centerOn(QPointF(r.x()+fraction[0]*r.width(),r.y()+fraction[1]*r.height()))
        finally:self.syncing=False
        if driver.detail:self.detail(self.selected)
    def fit_all(self):
        self.driver=None;self.last_state=None;self.detail_wanted.clear();self.detail_versions.clear();self.detail_timer.stop()
        for index,item in list(self.overlays.items()):self.panes[index].scene().removeItem(item)
        self.overlays.clear()
        for pane in self.panes:
            if pane.detail:pane.unzoom(lambda:None)
    def closeEvent(self,event):
        self.alive=False;self.generation+=1;self.timer.stop();self.status_timer.stop();self.detail_timer.stop();self.detail_wanted.clear();self.overlays.clear()
        for pane in self.panes:pane.stop_zoom();pane.show_photo(None)
        self.page.show_current();super().closeEvent(event)
