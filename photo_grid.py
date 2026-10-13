"""A virtual kept-photo gallery: full ID inventory, thumbnails only near the viewport."""
from PySide6.QtCore import Qt,QSize
from PySide6.QtWidgets import QListView,QSizePolicy
from filmstrip import Filmstrip
import theme

class PhotoGrid(Filmstrip):
    def __init__(self):
        super().__init__();self.tile_width=196;self.tile_height=158;self.captions={}
        self.setMinimumHeight(0);self.setMaximumHeight(16777215)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.setWrapping(True);self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setIconSize(QSize(176,118));self.setGridSize(QSize(196,158))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setStyleSheet('QListView { background:transparent; border:0; outline:0; } QListView::item { border:2px solid transparent; border-radius:8px; } QListView::item:selected { border:2px solid '+theme.LABEL+'; background:'+theme.ELEVATED+'; }')
    def visible_rows(self):
        columns=max(1,self.viewport().width()//self.tile_width)
        first=self.verticalScrollBar().value()//self.tile_height*columns
        start=max(0,first-columns)
        return range(start,min(self.count(),first+(self.viewport().height()//self.tile_height+3)*columns))
    def set_ids(self,ids):
        tips=self.tips;super().set_ids(ids)
        if ids:self.tips=tips
        else:self.captions.clear()
