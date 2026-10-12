"""Native, frameless window chrome shared by the app and its sheets."""
from pathlib import Path
from PySide6.QtCore import Qt, QEvent, QPoint, QRectF
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QColor, QPen, QRegion
from PySide6.QtWidgets import (QDialog as QtDialog, QMessageBox as QtMessageBox,
    QFileDialog as QtFileDialog, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QCheckBox, QSizeGrip, QFrame, QScrollArea, QApplication)

def app_icon():
    return QIcon(str(Path(__file__).parent / 'assets' / 'photosieve.ico'))

class TitleBar(QWidget):
    def __init__(self, window, title='', main=False):
        super().__init__(window); self.window=window; self.setFixedHeight(36)
        row=QHBoxLayout(self); row.setContentsMargins(12,0,4,0); row.setSpacing(6)
        icon=QLabel(); icon.setPixmap(app_icon().pixmap(22,22)); row.addWidget(icon)
        if not main: icon.hide(); self.setFixedHeight(44)
        self.label=QLabel(title or window.windowTitle()); row.addWidget(self.label); row.addStretch()
        if not main: self.label.setObjectName('heading')
        if main:
            self.add_button(row,'−','最小化',window.showMinimized)
            self.max_button=self.add_button(row,'□','最大化 / 还原',self.toggle_max)
        self.add_button(row,'×','关闭',window.close)

    def add_button(self,row,text,hint,fn):
        from apple_ui import AppleButton
        button=AppleButton('',symbol={'−':'minimize','□':'maximize','×':'close'}[text]); button.setFixedSize(40,30); button.setToolTip(hint)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAccessibleName(hint); button.clicked.connect(fn); row.addWidget(button); return button

    def toggle_max(self):
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            handle=self.window.windowHandle()
            if handle: handle.startSystemMove()

    def mouseDoubleClickEvent(self,event):
        if hasattr(self,'max_button') and event.button()==Qt.MouseButton.LeftButton: self.toggle_max()

def rounded_paint(widget):
    import theme
    painter=QPainter(widget); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(theme.SEPARATOR),1)); painter.setBrush(QColor(theme.ELEVATED))
    painter.drawRoundedRect(QRectF(widget.rect()).adjusted(.5,.5,-.5,-.5),20,20)


def rounded_init(widget):
    widget.setWindowFlag(Qt.WindowType.FramelessWindowHint); widget.setWindowIcon(app_icon())
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.setObjectName('roundedSheet')
    widget.setStyleSheet('#roundedSheet { background:transparent; }')


def rounded_resize(widget):
    path=QPainterPath(); path.addRoundedRect(QRectF(widget.rect()),20,20)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


class QDialog(QtDialog):
    def __init__(self,parent=None):
        super().__init__(parent); rounded_init(self); self.setSizeGripEnabled(True)

    def showEvent(self,event):
        if not hasattr(self,'chrome') and self.layout():
            for label in self.findChildren(QLabel):
                if label.objectName()=='heading' and label.text()==self.windowTitle(): self.layout().removeWidget(label); label.hide()
            self.layout().setContentsMargins(24,20,24,24); self.layout().setSpacing(16)
            self.chrome=TitleBar(self); self.layout().insertWidget(0,self.chrome)
        super().showEvent(event)

    def paintEvent(self,event): rounded_paint(self)
    def resizeEvent(self,event): super().resizeEvent(event); rounded_resize(self)


class QMessageBox(QtMessageBox):
    def __init__(self,parent=None):
        super().__init__(parent); rounded_init(self)
        self.setStyleSheet('#roundedSheet { background:transparent; } QLabel { padding:8px; }')

    @classmethod
    def information(cls,parent,title,text):
        box=cls(parent); box.setWindowTitle(title); box.setText(text); box.setStandardButtons(cls.StandardButton.Ok)
        box.button(cls.StandardButton.Ok).setText('确定'); return box.exec()

    critical=information
    def paintEvent(self,event): rounded_paint(self)
    def resizeEvent(self,event): super().resizeEvent(event); rounded_resize(self)


class RoundedFileDialog(QtFileDialog):
    def __init__(self,parent,title,start):
        super().__init__(parent,title,start); self.setOption(QtFileDialog.Option.DontUseNativeDialog); rounded_init(self)
        for key,text in [(QtFileDialog.DialogLabel.LookIn,'位置：'),(QtFileDialog.DialogLabel.FileName,'文件夹：'),
                         (QtFileDialog.DialogLabel.FileType,'文件类型：'),(QtFileDialog.DialogLabel.Accept,'选择'),(QtFileDialog.DialogLabel.Reject,'取消')]: self.setLabelText(key,text)
        self.chrome=TitleBar(self,title)
        self.layout().setContentsMargins(24,64,24,24); self.layout().setSpacing(12)
    def paintEvent(self,event): rounded_paint(self)
    def resizeEvent(self,event):
        super().resizeEvent(event); rounded_resize(self)
        if hasattr(self,'chrome'): self.chrome.setGeometry(16,12,self.width()-32,44)

class QFileDialog:
    @staticmethod
    def getExistingDirectory(parent,title,start=''):
        dialog=RoundedFileDialog(parent,title,start); dialog.setOption(QtFileDialog.Option.DontUseNativeDialog)
        dialog.setFileMode(QtFileDialog.FileMode.Directory); dialog.setOption(QtFileDialog.Option.ShowDirsOnly)
        dialog.setWindowFlag(Qt.WindowType.FramelessWindowHint); dialog.setWindowIcon(app_icon())
        if dialog.exec(): return dialog.selectedFiles()[0]
        return ''

class SettingsPanel(QFrame):
    """An anchored child panel: no top-level window and no focus-loss auto-dismiss."""
    def __init__(self,window,anchor):
        super().__init__(window.centralWidget()); self.host=window; self.anchor=anchor
        self.setObjectName('morePanel'); self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("""
            #morePanel { background:#1C1C1E; border:1px solid #38383A; border-radius:16px; }
            #morePanel QWidget { background:transparent; }
            #morePanel QPushButton#menuItem { background:transparent; border:0; border-radius:8px; text-align:left; padding:9px 12px; }
            #morePanel QPushButton#menuItem:hover { background:#2C2C2E; }
            #morePanel QPushButton#menuItem:disabled { color:#636366; }
            #morePanel QCheckBox { padding:8px 12px; spacing:9px; }
            #morePanel QLabel#panelStatus { color:#98989D; font-size:12px; padding:6px 12px; }
        """)
        outer=QVBoxLayout(self); outer.setContentsMargins(12,12,12,12); outer.setSpacing(8)
        header=QHBoxLayout(); title=QLabel('更多选项'); header.addWidget(title); header.addStretch()
        from apple_ui import AppleButton
        close=AppleButton('',symbol='close'); close.setFixedSize(28,28); close.setAccessibleName('关闭更多选项'); close.clicked.connect(self.hide); header.addWidget(close); outer.addLayout(header)
        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content=QWidget(); self.column=QVBoxLayout(content); self.column.setContentsMargins(0,0,0,0); self.column.setSpacing(6)
        self.scroll.setWidget(content); outer.addWidget(self.scroll); self.hide()

    def addAction(self,action):
        from PySide6.QtGui import QAction
        if isinstance(action,str): action=QAction(action,self)
        control=QCheckBox(action.text()) if action.isCheckable() else QPushButton(action.text())
        control.setObjectName('menuItem'); control.setEnabled(action.isEnabled())
        if action.isCheckable():
            control.setChecked(action.isChecked()); control.toggled.connect(action.setChecked)
        else:
            def clicked(): self.hide(); action.trigger()
            control.clicked.connect(clicked)
        self.column.addWidget(control)
        action.changed.connect(lambda:control.setEnabled(action.isEnabled())); return action

    def addStatus(self,text):
        label=QLabel(text); label.setObjectName('panelStatus'); label.setWordWrap(True); self.column.addWidget(label)

    def addSeparator(self):
        line=QFrame(); line.setFrameShape(QFrame.Shape.HLine); line.setStyleSheet('background:#38383A; min-height:1px; max-height:1px;'); self.column.addWidget(line)

    def place(self):
        parent=self.parentWidget(); width=min(380,max(160,parent.width()-24))
        anchor=self.anchor.mapTo(parent,QPoint(self.anchor.width(),self.anchor.height()+8))
        below=max(120,parent.height()-anchor.y()-12)
        self.resize(width,min(self.column.sizeHint().height()+76,below))
        self.move(max(12,min(anchor.x()-width,parent.width()-width-12)),max(12,min(anchor.y(),parent.height()-self.height()-12)))
        self.raise_()

    def exec(self,position=None): self.place(); self.show(); self.raise_(); self.setFocus()
    def showEvent(self,event):
        QApplication.instance().installEventFilter(self)
        for shortcut in getattr(self.host,'shortcuts',[]): shortcut.setEnabled(False)
        super().showEvent(event)
    def hideEvent(self,event):
        QApplication.instance().removeEventFilter(self)
        for shortcut in getattr(self.host,'shortcuts',[]): shortcut.setEnabled(True)
        super().hideEvent(event)
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
            point=event.globalPosition().toPoint()
            inside=self.rect().contains(self.mapFromGlobal(point))
            anchor=self.anchor.rect().contains(self.anchor.mapFromGlobal(point))
            if not inside and not anchor: self.hide()
        return False
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape: self.hide(); event.accept()
        else: super().keyPressEvent(event)
