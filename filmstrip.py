"""Virtual horizontal photo inventory: IDs for all photos, icons for visible cells only."""
from bisect import bisect_right
from itertools import accumulate
from PySide6.QtCore import QAbstractListModel,QModelIndex,Qt,Signal,QSize,QTimer,QPoint,QRect
from PySide6.QtGui import QIcon,QColor,QPen,QPainter,QPolygon
from PySide6.QtWidgets import QListView,QStyledItemDelegate,QStyleOptionViewItem,QStyle,QApplication,QAbstractItemView
import theme

ROLE=Qt.ItemDataRole.UserRole
class Item:
    def __init__(self,view,row):self.view=view;self.row=row
    def data(self,role):return self.view.photo_model.data(self.view.photo_model.index(self.row),role)
    def icon(self):return self.data(Qt.ItemDataRole.DecorationRole) or QIcon()
    def setIcon(self,icon):self.view.photo_model.changed(self.row)
    def setText(self,text):self.view.photo_model.changed(self.row)
    def setToolTip(self,text):self.view.tips[self.data(ROLE)]=text

class Model(QAbstractListModel):
    def __init__(self,view):super().__init__(view);self.view=view;self.ids=[]
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.ids)
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row()>=len(self.ids):return None
        pid=self.ids[index.row()]
        if role==ROLE:return pid
        if role==Qt.ItemDataRole.DisplayRole:return getattr(self.view,'captions',{}).get(pid,'')
        if role==Qt.ItemDataRole.DecorationRole:return self.view.icon_for(pid)
        if role==Qt.ItemDataRole.ToolTipRole:return self.view.tips.get(pid,f'照片 #{pid}')
        if role==Qt.ItemDataRole.SizeHintRole:return QSize(self.view.widths.get(pid,self.view.tile_width) if self.view.mixed_layout else self.view.tile_width,self.view.tile_height)
        return None
    def changed(self,row):
        index=self.index(row);self.dataChanged.emit(index,index,[Qt.ItemDataRole.DecorationRole,Qt.ItemDataRole.DisplayRole,Qt.ItemDataRole.ToolTipRole])

class MixedDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        if not self.parent().mixed_layout:return super().paint(painter,option,index)
        icon=index.data(Qt.ItemDataRole.DecorationRole)
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            # The photo the keys act on: a lava ring and a small inverted triangle above it.
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(theme.LAVA),2));painter.setBrush(QColor(theme.ELEVATED));painter.drawRoundedRect(option.rect.adjusted(1,1,-1,-1),8,8)
            cx=option.rect.center().x();top=option.rect.top()
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(theme.LABEL))
            painter.drawPolygon(QPolygon([QPoint(cx-5,top-1),QPoint(cx+5,top-1),QPoint(cx,top+5)]))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(theme.ELEVATED));painter.drawRoundedRect(option.rect.adjusted(1,1,-1,-1),8,8)
        painter.restore()
        if icon and not icon.isNull():
            available=option.rect.adjusted(6,6,-6,-6)
            picture=icon.pixmap(QSize(104,68));logical=picture.size()/picture.devicePixelRatioF()
            size=logical.scaled(available.size(),Qt.AspectRatioMode.KeepAspectRatio)
            target=QRect(available.center().x()-size.width()//2,available.center().y()-size.height()//2,size.width(),size.height())
            painter.drawPixmap(target,picture)

class Filmstrip(QListView):
    itemClicked=Signal(object)
    def __init__(self):
        super().__init__();self.mixed_layout=False;self.widths={};self.offsets=[0];self.layout_timer=QTimer(self);self.layout_timer.setSingleShot(True);self.layout_timer.timeout.connect(self.relayout_mixed);self.setItemDelegate(MixedDelegate(self));self.tile_width=116;self.tile_height=80;self.photo_model=Model(self);self.setModel(self.photo_model);self.tips={};self.icon_for=lambda _:QIcon()
        self.setViewMode(QListView.ViewMode.IconMode);self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False);self.setMovement(QListView.Movement.Static);self.setUniformItemSizes(True)
        self.setIconSize(QSize(104,68));self.setGridSize(QSize(116,80));self.setFixedHeight(100)
        self.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clicked.connect(lambda index:self.itemClicked.emit(Item(self,index.row())))
        self.setStyleSheet('QListView { background:transparent; border:0; outline:0; } QListView::item { border:2px solid transparent; border-radius:8px; } QListView::item:selected { border:2px solid '+theme.LABEL+'; background:transparent; }')
    def enable_mixed_layout(self):
        self.mixed_layout=True;self.setUniformItemSizes(True);self.setGridSize(QSize(116,80));self.setIconSize(QSize(104,68));self.relayout_mixed()

    def set_preview_width(self,pid,preview):
        if not self.mixed_layout or preview is None or preview.isNull():return
        width=max(44,min(116,round(68*preview.width()/preview.height())+12))
        if self.widths.get(pid,116)==width:return
        self.widths[pid]=width;self.layout_timer.start(60)

    def adapt_to_preview(self,preview):
        # A uniform virtual grid stays fast even with 100,000 IDs. Match its
        # density to the current photo, without opening the rest of the files.
        if preview is None or preview.isNull() or not preview.height():return
        if self.mixed_layout:return
        width=max(44,min(116,round(68*preview.width()/preview.height())+12))
        if width==self.tile_width:return
        bar=self.horizontalScrollBar();old=self.tile_width;at_end=bar.maximum()>0 and bar.value()==bar.maximum();first,offset=divmod(bar.value(),old)
        self.tile_width=width
        self.setGridSize(QSize(width,80));self.setIconSize(QSize(width-12,68))
        self.doItemsLayout();bar.setValue(bar.maximum() if at_end else round(first*width+offset*width/old))

    def set_ids(self,ids):
        self.photo_model.beginResetModel();self.photo_model.ids=list(ids);self.tips.clear();self.photo_model.endResetModel();self.relayout_mixed()
    def count(self):return len(self.photo_model.ids)
    def item(self,row):return Item(self,row) if 0<=row<self.count() else None
    def currentRow(self):return self.currentIndex().row()
    def setCurrentRow(self,row):self.setCurrentIndex(self.photo_model.index(row))
    def row(self,item):return item.row
    def visualItemRect(self,item):return self.visualRect(self.photo_model.index(item.row))
    def clear(self):self.set_ids([])
    def remove_id(self,pid):
        try:row=self.photo_model.ids.index(pid)
        except ValueError:return
        self.photo_model.beginRemoveRows(QModelIndex(),row,row);self.photo_model.ids.pop(row);self.photo_model.endRemoveRows()
        self.tips.pop(pid,None);self.relayout_mixed()
    def visible_rows(self):
        if self.mixed_layout and self.count():
            first=max(0,bisect_right(self.offsets,self.horizontalScrollBar().value())-2)
            end=bisect_right(self.offsets,self.horizontalScrollBar().value()+self.viewport().width())+1
            return range(first,min(self.count(),end))
        first=max(0,self.horizontalScrollBar().value()//self.tile_width-1)
        return range(first,min(self.count(),first+self.viewport().width()//self.tile_width+4))
    def visible_ids(self):return [self.photo_model.ids[i] for i in self.visible_rows()]
    def index_of(self,pid):
        try:return self.photo_model.ids.index(pid)
        except ValueError:return -1

    def relayout_mixed(self):
        if not self.mixed_layout:return
        self.offsets=[0]+list(accumulate(self.widths.get(pid,116) for pid in self.photo_model.ids))
        self.updateGeometries();self.viewport().update()

    def updateGeometries(self):
        super().updateGeometries()
        if getattr(self,'mixed_layout',False):
            self.horizontalScrollBar().setPageStep(self.viewport().width())
            self.horizontalScrollBar().setRange(0,max(0,self.offsets[-1]-self.viewport().width()))

    def visualRect(self,index):
        if not self.mixed_layout:return super().visualRect(index)
        row=index.row()
        if row<0 or row+1>=len(self.offsets):return QRect()
        return QRect(self.offsets[row]-self.horizontalScrollBar().value(),0,self.offsets[row+1]-self.offsets[row],80)

    def indexAt(self,point):
        if not self.mixed_layout:return super().indexAt(point)
        if not 0<=point.y()<80:return QModelIndex()
        row=bisect_right(self.offsets,point.x()+self.horizontalScrollBar().value())-1
        return self.photo_model.index(row) if 0<=row<self.count() else QModelIndex()

    def scrollTo(self,index,hint=QListView.ScrollHint.EnsureVisible):
        if not self.mixed_layout:return super().scrollTo(index,hint)
        row=index.row()
        if row<0 or row+1>=len(self.offsets):return
        left=self.offsets[row];right=self.offsets[row+1];bar=self.horizontalScrollBar()
        if left<bar.value():bar.setValue(left)
        elif right>bar.value()+self.viewport().width():bar.setValue(right-self.viewport().width())

    def scrollContentsBy(self,dx,dy):
        if self.mixed_layout:self.viewport().update()
        else:super().scrollContentsBy(dx,dy)

    def paintEvent(self,event):
        if not self.mixed_layout:return super().paintEvent(event)
        from PySide6.QtGui import QPainter
        painter=QPainter(self.viewport())
        for row in self.visible_rows():
            index=self.photo_model.index(row);option=QStyleOptionViewItem();option.initFrom(self.viewport());option.rect=self.visualRect(index);option.widget=self.viewport()
            if row==self.currentRow():option.state|=QStyle.StateFlag.State_Selected
            self.itemDelegate().paint(painter,option,index)
        painter.end()

class MixedFilmstrip(QAbstractItemView):
    """Variable-width strip without QListView's full-inventory layout on every removal."""
    itemClicked=Signal(object)
    def __init__(self):
        super().__init__();self.mixed_layout=True;self.widths={};self.offsets=[0];self.tile_width=116;self.tile_height=80;self.tips={};self.icon_for=lambda _:QIcon()
        self.photo_model=Model(self);self.setModel(self.photo_model);self.setItemDelegate(MixedDelegate(self));self._icon_size=QSize(104,68)
        self.layout_timer=QTimer(self);self.layout_timer.setSingleShot(True);self.layout_timer.timeout.connect(self.relayout_mixed)
        self.setFixedHeight(100);self.setHorizontalScrollMode(self.ScrollMode.ScrollPerPixel);self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn);self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clicked.connect(lambda index:self.itemClicked.emit(Item(self,index.row())))
        self.setStyleSheet('QAbstractItemView { background:transparent; border:0; outline:0; }')
    def enable_mixed_layout(self):pass
    def setViewMode(self,*_):pass
    def setFlow(self,*_):pass
    def setWrapping(self,*_):pass
    def setMovement(self,*_):pass
    def setUniformItemSizes(self,*_):pass
    def setGridSize(self,*_):pass
    def setIconSize(self,size):self._icon_size=size
    def iconSize(self):return self._icon_size
    set_preview_width=Filmstrip.set_preview_width
    adapt_to_preview=Filmstrip.adapt_to_preview
    set_ids=Filmstrip.set_ids
    count=Filmstrip.count
    item=Filmstrip.item
    currentRow=Filmstrip.currentRow
    setCurrentRow=Filmstrip.setCurrentRow
    row=Filmstrip.row
    visualItemRect=Filmstrip.visualItemRect
    clear=Filmstrip.clear
    remove_id=Filmstrip.remove_id
    visible_rows=Filmstrip.visible_rows
    visible_ids=Filmstrip.visible_ids
    index_of=Filmstrip.index_of
    relayout_mixed=Filmstrip.relayout_mixed
    visualRect=Filmstrip.visualRect
    indexAt=Filmstrip.indexAt
    scrollTo=Filmstrip.scrollTo
    scrollContentsBy=Filmstrip.scrollContentsBy
    paintEvent=Filmstrip.paintEvent
    def updateGeometries(self):
        if not hasattr(self,'offsets'):return
        self.horizontalScrollBar().setPageStep(self.viewport().width());self.horizontalScrollBar().setRange(0,max(0,self.offsets[-1]-self.viewport().width()))
    def resizeEvent(self,event):super().resizeEvent(event);self.updateGeometries()
    def horizontalOffset(self):return self.horizontalScrollBar().value()
    def verticalOffset(self):return 0
    def isIndexHidden(self,index):return False
    def moveCursor(self,action,modifiers):return self.currentIndex()
    def setSelection(self,rect,command):
        index=self.indexAt(rect.center())
        if index.isValid():self.selectionModel().select(index,command)
    def visualRegionForSelection(self,selection):
        from PySide6.QtGui import QRegion
        region=QRegion()
        for span in selection:
            for row in range(span.top(),span.bottom()+1):region|=QRegion(self.visualRect(self.photo_model.index(row)))
        return region
