"""A quiet, readable update offer; full release notes stay behind an explicit toggle."""
import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel,QVBoxLayout,QHBoxLayout,QFrame,QPlainTextEdit
from apple_ui import AppleButton
from ui_shell import QDialog,TitleBar,app_icon
from version import APP_VERSION
import theme

def highlights(notes):
    result=[]
    for raw in str(notes).splitlines():
        line=raw.strip()
        if not re.match(r'^[-*•]\s+',line):continue
        line=re.sub(r'^[-*•]\s+','',line).strip()
        if any(word in line for word in ('发布测试','单元测试','验证流程','Windows 发布测试')):continue
        if '任务栏' in line and '图标' in line:line='改善 Windows 任务栏图标显示。'
        elif '更新' in line and '重启' in line and any(word in line for word in ('助手','运行环境','存活','启动失败')):
            line='修复更新后未能自动重新打开应用的问题。'
        elif '启动' in line and '检查更新' in line:line='打开应用时，有新版本会直接提醒。'
        if line and line not in result:result.append(line)
        if len(result)==3:break
    return result

class UpdateOfferDialog(QDialog):
    def __init__(self,release,parent=None,current_version=APP_VERSION):
        super().__init__(parent);self.setWindowTitle('软件更新');self.setFixedWidth(552);self.setSizeGripEnabled(False)
        self.setStyleSheet(f'QLabel {{color:{theme.LABEL};font-size:14px;}} QPlainTextEdit {{background:{theme.BG};color:{theme.SECONDARY};border:1px solid {theme.SEPARATOR};border-radius:12px;padding:12px;font-size:12px;}}')
        outer=QVBoxLayout(self);outer.setContentsMargins(24,16,24,24);outer.setSpacing(18)
        self.chrome=TitleBar(self,'软件更新');self.chrome.label.setStyleSheet(f'color:{theme.SECONDARY};font-size:12px;');outer.addWidget(self.chrome)
        hero=QHBoxLayout();hero.setSpacing(16)
        logo=QLabel();logo.setPixmap(app_icon().pixmap(52,52));logo.setFixedSize(52,52);hero.addWidget(logo)
        heading=QVBoxLayout();heading.setSpacing(4)
        title=QLabel('发现新版本');title.setStyleSheet('font-size:26px;font-weight:600;');heading.addWidget(title)
        version=QLabel(f"照片快筛 {release['version']}  ·  当前 {current_version}");version.setStyleSheet(f'color:{theme.SECONDARY};font-size:13px;');heading.addWidget(version)
        hero.addLayout(heading,1);outer.addLayout(hero);outer.addSpacing(2)
        card=QFrame();card.setStyleSheet(f'QFrame {{background:{theme.BG};border-radius:14px;}}');items=QVBoxLayout(card);items.setContentsMargins(18,16,18,16);items.setSpacing(12)
        caption=QLabel('本次更新');caption.setStyleSheet(f'color:{theme.SECONDARY};font-size:12px;');items.addWidget(caption)
        summaries=highlights(release.get('notes',''))
        for text in summaries or ['新版已发布，可查看完整更新说明。']:
            row=QHBoxLayout();row.setSpacing(10)
            dot=QLabel('•');dot.setStyleSheet(f'color:{theme.BLUE_HOVER};font-size:18px;');dot.setFixedWidth(10);row.addWidget(dot,0,Qt.AlignmentFlag.AlignTop)
            label=QLabel(text);label.setTextFormat(Qt.TextFormat.PlainText);label.setWordWrap(True);row.addWidget(label,1);items.addLayout(row)
        outer.addWidget(card)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setPlainText(str(release.get('notes','')));self.details.setFixedHeight(180);self.details.hide()
        self.more=AppleButton('查看完整更新说明');self.more.setAutoDefault(False);self.more.setFixedHeight(30);self.more.clicked.connect(self.toggle_details)
        if str(release.get('notes','')).strip():outer.addWidget(self.more,0,Qt.AlignmentFlag.AlignLeft)
        else:self.more.hide()
        outer.addWidget(self.details)
        note=QLabel('照片、筛选记录和设置会保留。\n下载完成后，停止操作 3 秒将自动安装并重新打开应用。');note.setWordWrap(True);note.setStyleSheet(f'color:{theme.SECONDARY};font-size:12px;');outer.addWidget(note)
        buttons=QHBoxLayout();buttons.setSpacing(10);buttons.addStretch()
        self.later=AppleButton('以后再说');self.later.setAutoDefault(False);self.later.setFixedSize(112,44);self.later.clicked.connect(self.reject)
        self.accept_button=AppleButton('下载并更新',variant='primary');self.accept_button.setFixedSize(148,44);self.accept_button.setDefault(True);self.accept_button.clicked.connect(self.accept)
        buttons.addWidget(self.later);buttons.addWidget(self.accept_button);outer.addLayout(buttons)
    def toggle_details(self):
        expanded=not self.details.isVisible();self.details.setVisible(expanded)
        if expanded:
            available=self.screen().availableGeometry().height()-48
            self.details.setFixedHeight(max(80,min(180,available-self.height()-18)))
        self.more.setText('收起更新说明' if expanded else '查看完整更新说明')
        self.layout().activate();self.adjustSize()
