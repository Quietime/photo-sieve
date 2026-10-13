"""Virtual horizontal photo inventory: IDs for all photos, icons for visible cells only."""
from PySide6.QtCore import QAbstractListModel,QModelIndex,Qt,Signal,QSize,QTimer,QPoint,QRect
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QListView,QStyledItemDelegate,QStyleOptionViewItem,QStyle,QApplication
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
        background=QStyleOptionViewItem(option);self.initStyleOption(background,index);background.icon=QIcon();background.text=''
        QApplication.style().drawControl(QStyle.ControlElement.CE_ItemViewItem,background,painter)
        if icon and not icon.isNull():
            available=option.rect.adjusted(6,6,-6,-6)
            picture=icon.pixmap(QSize(104,68));logical=picture.size()/picture.devicePixelRatioF()
            size=logical.scaled(available.size(),Qt.AspectRatioMode.KeepAspectRatio)
            target=QRect(available.center().x()-size.width()//2,available.center().y()-size.height()//2,size.width(),size.height())
            painter.drawPixmap(target,picture)

class Filmstrip(QListView):
    itemClicked=Signal(object)
    def __init__(self):
        super().__init__();self.mixed_layout=False;self.widths={};self.layout_timer=QTimer(self);self.layout_timer.setSingleShot(True);self.layout_timer.timeout.connect(self.doItemsLayout);self.setItemDelegate(MixedDelegate(self));self.tile_width=116;self.tile_height=80;self.photo_model=Model(self);self.setModel(self.photo_model);self.tips={};self.icon_for=lambda _:QIcon()
        self.setViewMode(QListView.ViewMode.IconMode);self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False);self.setMovement(QListView.Movement.Static);self.setUniformItemSizes(True)
        self.setIconSize(QSize(104,68));self.setGridSize(QSize(116,80));self.setFixedHeight(100)
        self.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clicked.connect(lambda index:self.itemClicked.emit(Item(self,index.row())))
        self.setStyleSheet('QListView { background:transparent; border:0; outline:0; } QListView::item { border:2px solid transparent; border-radius:8px; } QListView::item:selected { border:2px solid '+theme.LABEL+'; background:transparent; }')
    def enable_mixed_layout(self):
        self.mixed_layout=True;self.setUniformItemSizes(False);self.setGridSize(QSize());self.setIconSize(QSize(104,68))

    def set_preview_width(self,pid,preview):
        if not self.mixed_layout or preview is None or preview.isNull():return
        width=max(44,min(116,round(68*preview.width()/preview.height())+12))
        if self.widths.get(pid)==width:return
        self.widths[pid]=width;self.layout_timer.start(60)

    def adapt_to_preview(self,preview):
        # A uniform virtual grid stays fast even with 100,000 IDs. Match its
        # density to the current photo, without opening the rest of the files.
        if preview is None or preview.isNull() or not preview.height():return
        width=max(44,min(116,round(68*preview.width()/preview.height())+12))
        if width==self.tile_width:return
        bar=self.horizontalScrollBar();old=self.tile_width;at_end=bar.maximum()>0 and bar.value()==bar.maximum();first,offset=divmod(bar.value(),old)
        self.tile_width=width
        if self.mixed_layout:self.setGridSize(QSize());self.setIconSize(QSize(104,68))
        else:self.setGridSize(QSize(width,80));self.setIconSize(QSize(width-12,68))
        self.doItemsLayout();bar.setValue(bar.maximum() if at_end else round(first*width+offset*width/old))

    def set_ids(self,ids):
        self.photo_model.beginResetModel();self.photo_model.ids=list(ids);self.tips.clear();self.photo_model.endResetModel()
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
        self.tips.pop(pid,None)
    def visible_rows(self):
        if self.mixed_layout and self.count():
            # QListView already indexes item geometry; binary search handles gaps and the ends.
            offset=self.horizontalScrollBar().value();low=0;high=self.count()
            while low<high:
                middle=(low+high)//2;rect=self.visualRect(self.photo_model.index(middle))
                if rect.right()<0:low=middle+1
                else:high=middle
            first=max(0,low-1);end=first
            while end<self.count() and self.visualRect(self.photo_model.index(end)).left()<=self.viewport().width():end+=1
            return range(first,min(self.count(),end+2))
        first=max(0,self.horizontalScrollBar().value()//self.tile_width-1)
        return range(first,min(self.count(),first+self.viewport().width()//self.tile_width+4))
    def visible_ids(self):return [self.photo_model.ids[i] for i in self.visible_rows()]
    def index_of(self,pid):
        try:return self.photo_model.ids.index(pid)
        except ValueError:return -1
