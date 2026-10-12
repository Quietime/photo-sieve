"""Virtual horizontal photo inventory: IDs for all photos, icons for visible cells only."""
from PySide6.QtCore import QAbstractListModel,QModelIndex,Qt,Signal,QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QListView

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
        if role==Qt.ItemDataRole.DecorationRole:return self.view.icon_for(pid)
        if role==Qt.ItemDataRole.ToolTipRole:return self.view.tips.get(pid,f'照片 #{pid}')
        if role==Qt.ItemDataRole.SizeHintRole:return QSize(116,80)
        return None
    def changed(self,row):
        index=self.index(row);self.dataChanged.emit(index,index,[Qt.ItemDataRole.DecorationRole])

class Filmstrip(QListView):
    itemClicked=Signal(object)
    def __init__(self):
        super().__init__();self.photo_model=Model(self);self.setModel(self.photo_model);self.tips={};self.icon_for=lambda _:QIcon()
        self.setViewMode(QListView.ViewMode.IconMode);self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False);self.setMovement(QListView.Movement.Static);self.setUniformItemSizes(True)
        self.setIconSize(QSize(104,68));self.setGridSize(QSize(116,80));self.setFixedHeight(100)
        self.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clicked.connect(lambda index:self.itemClicked.emit(Item(self,index.row())))
        self.setStyleSheet('QListView { background:transparent; border:0; outline:0; } QListView::item { border:2px solid transparent; border-radius:8px; } QListView::item:selected { border:2px solid #F5F5F7; background:transparent; }')
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
        first=max(0,self.horizontalScrollBar().value()//116-1)
        return range(first,min(self.count(),first+self.viewport().width()//116+4))
    def visible_ids(self):return [self.photo_model.ids[i] for i in self.visible_rows()]
    def index_of(self,pid):
        try:return self.photo_model.ids.index(pid)
        except ValueError:return -1
