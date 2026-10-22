"""Native, frameless window chrome shared by the app and its sheets."""
from pathlib import Path
import theme
from PySide6.QtCore import Qt, QEvent, QPoint, QRectF
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QColor, QPen, QRegion
from PySide6.QtWidgets import (QDialog as QtDialog, QMessageBox as QtMessageBox,
    QFileDialog as QtFileDialog, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QCheckBox, QSizeGrip, QFrame, QScrollArea, QApplication,
    QSpinBox, QStyle, QStyleOptionSpinBox, QFileIconProvider)

class FolderIconProvider(QFileIconProvider):
    """Never ask Windows shell/RAW extensions to inspect actual photo files."""
    def __init__(self):
        super().__init__()
        style=QApplication.style()
        self.folder=style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.file=style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self.computer=style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.drive=style.standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon)
    def icon(self,value):
        if isinstance(value,QFileIconProvider.IconType):
            if value==self.IconType.Computer:return self.computer
            if value==self.IconType.Drive:return self.drive
            return self.folder if value in (self.IconType.Folder,self.IconType.Network) else self.file
        if value.isRoot():return self.drive
        return self.folder if value.isDir() else self.file
    def type(self,info):return '文件夹' if info.isDir() else '文件'

def app_icon():
    assets=Path(__file__).parent/'assets'
    icon=QIcon(str(assets/'photosieve.png'))
    return icon if not icon.pixmap(32,32).isNull() else QIcon(str(assets/'photosieve.ico'))

class CacheSpinBox(QSpinBox):
    """Native editable number field with clear, theme-independent step arrows."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet('''QSpinBox { padding-right:30px; }
            QSpinBox::up-button { subcontrol-origin:border; subcontrol-position:top right;
                width:28px; height:19px; border:0; background:transparent; }
            QSpinBox::down-button { subcontrol-origin:border; subcontrol-position:bottom right;
                width:28px; height:19px; border:0; background:transparent; }
            QSpinBox::up-arrow, QSpinBox::down-arrow { width:0; height:0; }''')

    def step_rect(self, up):
        option=QStyleOptionSpinBox(); self.initStyleOption(option)
        control=QStyle.SubControl.SC_SpinBoxUp if up else QStyle.SubControl.SC_SpinBoxDown
        return self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,option,control,self)

    def paintEvent(self,event):
        super().paintEvent(event)
        import theme
        painter=QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for up in (True,False):
            rect=self.step_rect(up); center=QRectF(rect).center()
            enabled=self.isEnabled() and (self.value()<self.maximum() if up else self.value()>self.minimum())
            pen=QPen(QColor(theme.LABEL if enabled else theme.TERTIARY),1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap); pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen); path=QPainterPath(); direction=-1 if up else 1
            path.moveTo(center.x()-4,center.y()-direction*2)
            path.lineTo(center.x(),center.y()+direction*2)
            path.lineTo(center.x()+4,center.y()-direction*2); painter.drawPath(path)

class TitleBar(QWidget):
    def __init__(self, window, title='', main=False):
        super().__init__(window); self.window=window; self.setFixedHeight(36)
        row=QHBoxLayout(self); row.setContentsMargins(12,0,4,0); row.setSpacing(6)
        icon=QLabel(); icon.setPixmap(app_icon().pixmap(22,22)); row.addWidget(icon)
        if not main: icon.hide(); self.setFixedHeight(44)
        self.label=QLabel(title or window.windowTitle()); row.addWidget(self.label); row.addStretch()
        if not main: self.label.setObjectName('heading')
        if main:
            self.setObjectName('windowChrome')
            self.min_button=self.add_button(row,'−','最小化',window.showMinimized)
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
        super().__init__(parent); self.setOption(QtFileDialog.Option.DontUseNativeDialog); rounded_init(self)
        self.generic_icons=FolderIconProvider();self.setIconProvider(self.generic_icons)
        self.setWindowTitle(title)
        if start:self.setDirectory(start)
        for key,text in [(QtFileDialog.DialogLabel.LookIn,'位置：'),(QtFileDialog.DialogLabel.FileName,'文件夹：'),
                         (QtFileDialog.DialogLabel.FileType,'文件类型：'),(QtFileDialog.DialogLabel.Accept,'选择'),(QtFileDialog.DialogLabel.Reject,'取消')]: self.setLabelText(key,text)
        self.chrome=TitleBar(self,title)
        self.layout().setContentsMargins(24,64,24,24); self.layout().setSpacing(12)
        screen=parent.screen() if parent else QApplication.primaryScreen()
        area=screen.availableGeometry()
        self.setMinimumSize(min(800,area.width()-32),min(560,area.height()-32))
        self.resize(min(1120,round(area.width()*.8)),min(760,round(area.height()*.8)))
        self.move(area.center()-self.rect().center())
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
        # Dispose on the GUI thread. The dialog and its filesystem-model worker
        # must not remain in a cycle for an indexing thread's GC to destroy.
        try:
            return dialog.selectedFiles()[0] if dialog.exec() else ''
        finally:
            dialog.deleteLater()

    @staticmethod
    def getOpenFileName(parent,title,start='',filter=''):
        dialog=RoundedFileDialog(parent,title,start); dialog.setFileMode(QtFileDialog.FileMode.ExistingFile)
        dialog.setLabelText(QtFileDialog.DialogLabel.FileName,'文件：')
        if filter:dialog.setNameFilter(filter)
        dialog.setWindowFlag(Qt.WindowType.FramelessWindowHint); dialog.setWindowIcon(app_icon())
        try:
            return (dialog.selectedFiles()[0] if dialog.exec() else ''),filter
        finally:
            dialog.deleteLater()


class SettingsToggle(QCheckBox):
    """Native checkbox semantics with a readable label and a trailing switch."""
    def __init__(self,text):
        super().__init__(text);self.setCursor(Qt.CursorShape.PointingHandCursor)
    def hitButton(self,point):return self.rect().contains(point)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.font());painter.setPen(QColor(theme.LABEL if self.isEnabled() else theme.SECONDARY))
        area=QRectF(self.rect()).adjusted(8,0,-60,0)
        painter.drawText(area,Qt.AlignmentFlag.AlignVCenter,self.fontMetrics().elidedText(self.text(),Qt.TextElideMode.ElideRight,int(area.width())))
        track=QRectF(self.width()-44,self.height()/2-9,32,18)
        painter.setPen(QPen(QColor(theme.ICE if self.isChecked() else theme.SECONDARY),1));painter.setBrush(QColor(theme.BLUE if self.isChecked() else theme.SEPARATOR));painter.drawRoundedRect(track,9,9)
        painter.setBrush(QColor(theme.LABEL));painter.setPen(Qt.PenStyle.NoPen);painter.drawEllipse(QRectF(track.x()+(16 if self.isChecked() else 2),track.y()+2,14,14))
        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.ICE),1));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawRoundedRect(QRectF(self.rect()).adjusted(1,1,-1,-1),6,6)


class SettingsPanel(QFrame):
    """An anchored child panel: no top-level window and no focus-loss auto-dismiss."""
    def __init__(self,window,anchor):
        super().__init__(window.centralWidget()); self.host=window; self.anchor=anchor; self.action_controls={}
        self.setObjectName('morePanel'); self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("""
            #morePanel { background:#1C1C1E; border:1px solid #38383A; border-radius:16px; }
            #morePanel QWidget { background:transparent; }
            #morePanel QPushButton#menuItem { background:transparent; border:0; border-radius:8px; text-align:left; padding:4px 8px; }
            #morePanel QPushButton#menuItem:hover { background:#2C2C2E; }
            #morePanel QPushButton#menuItem:disabled { color:#636366; }
            #morePanel QCheckBox { padding:4px 8px; spacing:7px; }
            #morePanel QLabel#sectionTitle { color:#F5F5F7; font-size:14px; font-weight:600; padding:0 4px 4px; }
            #morePanel QFrame#settingsGroup { background:#303236; border:0; border-radius:8px; }
            #morePanel QLabel#sectionDescription { color:#98989D; font-size:12px; padding:0 12px 6px; }
            #morePanel QLabel#panelStatus { color:#98989D; font-size:12px; padding:6px 12px; }
        """.replace('#1C1C1E',theme.ELEVATED).replace('#38383A',theme.SEPARATOR)
            .replace('#2C2C2E',theme.ELEVATED2).replace('#636366',theme.TERTIARY)
            .replace('#98989D',theme.SECONDARY).replace('#F5F5F7',theme.LABEL))
        outer=QVBoxLayout(self); outer.setContentsMargins(24,20,24,24); outer.setSpacing(16)
        header=QHBoxLayout(); title=QLabel('更多选项'); header.addWidget(title); header.addStretch()
        from apple_ui import AppleButton
        close=AppleButton('',symbol='close'); close.setFixedSize(28,28); close.setAccessibleName('关闭更多选项'); close.clicked.connect(self.hide); header.addWidget(close); outer.addLayout(header)
        from PySide6.QtWidgets import QStackedWidget
        body=QHBoxLayout();body.setSpacing(24)
        navigation=QWidget();navigation.setFixedWidth(172);self.navigation=QVBoxLayout(navigation);self.navigation.setContentsMargins(0,0,0,0);self.navigation.setSpacing(8);self.navigation.addStretch()
        self.stack=QStackedWidget();body.addWidget(navigation);body.addWidget(self.stack,1);outer.addLayout(body,1)
        self.pages={};self.category_buttons={};self.group_count=0;self.column=None;self.application_section=False
        self.mode_category='照片分级' if getattr(window,'rating_active',False) else '照片筛选'
        self.hide()

    def category(self,name):
        if name not in self.pages:
            scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);scroll.setStyleSheet('QScrollArea {border:0;}')
            content=QWidget();groups=QVBoxLayout(content);groups.setContentsMargins(0,0,8,0);groups.setSpacing(16);groups.addStretch()
            scroll.setWidget(content);self.stack.addWidget(scroll)
            button=QPushButton(name);button.setCheckable(True);button.setMinimumHeight(36)
            button.setStyleSheet('QPushButton {background:transparent;text-align:left;border-radius:8px;padding:8px 12px;} QPushButton:checked {background:'+theme.ELEVATED+';} QPushButton:hover {background:'+theme.SEPARATOR+';}')
            order=[self.mode_category,'缓存与性能','外观与装饰包','更新与日志']
            position=sum(1 for key in self.category_buttons if order.index(key)<order.index(name));self.navigation.insertWidget(position,button)
            button.clicked.connect(lambda _,name=name:self.select_category(name))
            self.pages[name]=(scroll,content,groups);self.category_buttons[name]=button
            if len(self.pages)==1:self.select_category(name)
        return self.pages[name]

    def select_category(self,name):
        scroll,content,groups=self.pages[name];self.stack.setCurrentWidget(scroll)
        for key,button in self.category_buttons.items():button.setChecked(key==name)
        self.scroll=scroll;self.content=content;self.groups=groups

    def section(self,category,title,description=''):
        scroll,content,groups=self.category(category)
        group=QFrame();group.setObjectName('settingsGroup');column=QVBoxLayout(group);column.setContentsMargins(16,16,16,16);column.setSpacing(6)
        groups.insertWidget(groups.count()-1,group)
        label=QLabel(title);label.setObjectName('sectionTitle');column.addWidget(label)
        if description:
            description_label=QLabel(description);description_label.setObjectName('sectionDescription');description_label.setWordWrap(True);column.addWidget(description_label)
        return column

    def application_column(self,text):
        if any(word in text for word in ('缓存','GPU')):category='缓存与性能'
        elif any(word in text for word in ('装饰包','启动动画')):category='外观与装饰包'
        else:category='更新与日志'
        if category not in self.application_columns:self.application_columns[category]=self.section(category,category,'这些设置适用于整个应用')
        return self.application_columns[category]

    def addAction(self,action):
        from PySide6.QtGui import QAction
        if isinstance(action,str): action=QAction(action,self)
        if action in self.action_controls:return action
        from apple_ui import MenuRow
        control=SettingsToggle(action.text()) if action.isCheckable() else MenuRow(action.text())
        control.setObjectName('menuItem'); control.setEnabled(action.isEnabled())
        control.setFixedHeight(34);control.setToolTip(action.toolTip())
        if action.isCheckable():
            control.setChecked(action.isChecked()); control.toggled.connect(action.setChecked)
        else:
            def clicked(): self.hide(); action.trigger()
            control.clicked.connect(clicked)
        self.action_controls[action]=control
        column=self.application_column(action.text()) if self.application_section else self.column
        column.addWidget(control)
        def synchronize():
            control.setEnabled(action.isEnabled())
            if action.isCheckable():
                previous=control.blockSignals(True);control.setChecked(action.isChecked());control.blockSignals(previous)
        action.changed.connect(synchronize);return action

    def addSection(self,title,description=''):
        self.group_count+=1
        if title=='应用全局':
            self.application_section=True;self.application_columns={}
            self.column=self.section('缓存与性能',title,description)
            self.application_columns['缓存与性能']=self.column
        else:
            self.application_section=False;self.column=self.section(self.mode_category,title,description)

    def addStatus(self,text):
        label=QLabel(text); label.setObjectName('panelStatus'); label.setWordWrap(True); (self.application_column('检查更新') if self.application_section else self.column).addWidget(label)

    def addSeparator(self):
        line=QFrame(); line.setFrameShape(QFrame.Shape.HLine); line.setStyleSheet('background:'+theme.SEPARATOR+'; min-height:1px; max-height:1px;'); self.column.addWidget(line)

    def place(self):
        parent=self.parentWidget();width=min(920,max(280,parent.width()-48));height=min(680,max(240,parent.height()-48))
        self.resize(width,height);self.move(max(12,(parent.width()-width)//2),max(12,(parent.height()-height)//2));self.raise_()

    def exec(self,position=None): self.place(); self.show(); self.raise_(); self.setFocus()
    def showEvent(self,event):
        QApplication.instance().installEventFilter(self)
        for shortcut in getattr(self.host,'shortcuts',[]): shortcut.setEnabled(False)
        for shortcut in getattr(getattr(self.host,'rating_page',None),'shortcut_objects',[]):shortcut.setEnabled(False)
        super().showEvent(event)
    def hideEvent(self,event):
        QApplication.instance().removeEventFilter(self)
        if hasattr(self.host,'sync_shortcut_mode'):self.host.sync_shortcut_mode()
        else:
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
