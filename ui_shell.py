"""Native, frameless window chrome shared by the app and its sheets."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QDialog as QtDialog, QMessageBox as QtMessageBox,
    QFileDialog as QtFileDialog, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QCheckBox, QSizeGrip)

def app_icon():
    return QIcon(str(Path(__file__).parent / 'assets' / 'photosieve.ico'))

class TitleBar(QWidget):
    def __init__(self, window, title='', main=False):
        super().__init__(window); self.window=window; self.setFixedHeight(36)
        row=QHBoxLayout(self); row.setContentsMargins(12,0,4,0); row.setSpacing(6)
        icon=QLabel(); icon.setPixmap(app_icon().pixmap(22,22)); row.addWidget(icon)
        self.label=QLabel(title or window.windowTitle()); row.addWidget(self.label); row.addStretch()
        if main:
            self.add_button(row,'−','最小化',window.showMinimized)
            self.max_button=self.add_button(row,'□','最大化 / 还原',self.toggle_max)
        self.add_button(row,'×','关闭',window.close)

    def add_button(self,row,text,hint,fn):
        from apple_ui import AppleButton
        button=AppleButton('',symbol={'−':'minimize','□':'maximize','×':'close'}[text]); button.setFixedSize(40,30); button.setToolTip(hint)
        button.setAccessibleName(hint); button.clicked.connect(fn); row.addWidget(button); return button

    def toggle_max(self):
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            handle=self.window.windowHandle()
            if handle: handle.startSystemMove()

    def mouseDoubleClickEvent(self,event):
        if hasattr(self,'max_button') and event.button()==Qt.MouseButton.LeftButton: self.toggle_max()

class QDialog(QtDialog):
    def __init__(self,parent=None):
        super().__init__(parent); self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowIcon(app_icon()); self.setSizeGripEnabled(True)

    def showEvent(self,event):
        if not hasattr(self,'chrome') and self.layout():
            self.chrome=TitleBar(self); self.layout().insertWidget(0,self.chrome)
        super().showEvent(event)

class QMessageBox(QtMessageBox):
    def __init__(self,parent=None):
        super().__init__(parent); self.setWindowFlag(Qt.WindowType.FramelessWindowHint); self.setWindowIcon(app_icon())

    @classmethod
    def information(cls,parent,title,text):
        box=cls(parent); box.setWindowTitle(title); box.setText(text); box.setStandardButtons(cls.StandardButton.Ok); return box.exec()

    critical=information

class QFileDialog:
    @staticmethod
    def getExistingDirectory(parent,title,start=''):
        dialog=QtFileDialog(parent,title,start); dialog.setOption(QtFileDialog.Option.DontUseNativeDialog)
        dialog.setFileMode(QtFileDialog.FileMode.Directory); dialog.setOption(QtFileDialog.Option.ShowDirsOnly)
        dialog.setWindowFlag(Qt.WindowType.FramelessWindowHint); dialog.setWindowIcon(app_icon())
        if dialog.exec(): return dialog.selectedFiles()[0]
        return ''

class SettingsPanel(QDialog):
    """A persistent sheet: screenshot shortcuts and focus loss do not dismiss a popup menu."""
    def __init__(self,parent):
        super().__init__(parent); self.setWindowTitle('更多选项'); self.setFixedWidth(480)
        self.column=QVBoxLayout(self); self.column.setContentsMargins(16,12,16,16); self.column.setSpacing(4)

    def addAction(self,action):
        from PySide6.QtGui import QAction
        if isinstance(action,str): action=QAction(action,self)
        control=QCheckBox(action.text()) if action.isCheckable() else QPushButton(action.text())
        control.setEnabled(action.isEnabled())
        if action.isCheckable():
            control.setChecked(action.isChecked()); control.toggled.connect(action.setChecked)
        else:
            def clicked():
                self.close(); action.trigger()
            control.clicked.connect(clicked)
        self.column.addWidget(control); action.changed.connect(lambda:control.setEnabled(action.isEnabled())); return action

    def addSeparator(self): self.column.addSpacing(8)

    def exec(self,position=None):
        self.show()
        if position is not None:
            screen=self.screen().availableGeometry()
            x=max(screen.left(),min(position.x(),screen.right()-self.width()+1))
            y=max(screen.top(),min(position.y(),screen.bottom()-self.height()+1)); self.move(x,y)
        self.raise_(); self.activateWindow()
