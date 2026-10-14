"""A calm close confirmation: keeping progress is the default, records always survive."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from apple_ui import AppleButton
from ui_shell import QDialog, TitleBar
import theme


class CloseOfferDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.choice = None
        self.setWindowTitle('关闭当前工作文件夹')
        self.setFixedWidth(568)
        self.setSizeGripEnabled(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 24)
        outer.setSpacing(18)
        self.chrome = TitleBar(self, '退出照片快筛')
        self.chrome.label.setStyleSheet(f'color:{theme.SECONDARY};font-size:12px;')
        outer.addWidget(self.chrome)

        title = QLabel('关闭当前工作文件夹')
        title.setStyleSheet(f'color:{theme.LABEL};font-size:26px;font-weight:600;')
        outer.addWidget(title)
        lead = QLabel('下次打开应用时，仍由你手动选择照片文件夹。')
        lead.setWordWrap(True)
        lead.setStyleSheet(f'color:{theme.SECONDARY};font-size:14px;')
        outer.addWidget(lead)

        card = QFrame()
        card.setObjectName('closeSummary')
        card.setStyleSheet(f'QFrame#closeSummary {{background:{theme.BG};border-radius:14px;}}')
        content = QVBoxLayout(card)
        content.setContentsMargins(18, 16, 18, 16)
        content.setSpacing(12)
        caption = QLabel('照片和筛选记录始终保留')
        caption.setStyleSheet(f'color:{theme.LABEL};font-size:14px;font-weight:600;')
        content.addWidget(caption)
        explanation = QLabel('原照片、保留标记和可恢复的删除记录都不会清除。\n不保留位置只会重置当前浏览位置。')
        explanation.setWordWrap(True)
        explanation.setStyleSheet(f'color:{theme.SECONDARY};font-size:13px;')
        content.addWidget(explanation)
        outer.addWidget(card)

        outer.addSpacing(4)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch()
        self.cancel_button = AppleButton('取消')
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setFixedSize(100, 44)
        self.cancel_button.clicked.connect(self.reject)
        self.keep_button = AppleButton('保留进度并关闭', variant='primary')
        self.keep_button.setFixedSize(180, 44)
        self.keep_button.setDefault(True)
        self.keep_button.clicked.connect(lambda: self.choose('keep'))
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.keep_button)
        outer.addLayout(buttons)

        secondary = QHBoxLayout()
        secondary.setContentsMargins(0, 8, 0, 0)
        self.forget_button = QPushButton('直接关闭，不保留位置')
        self.forget_button.setObjectName('forgetBrowsePosition')
        self.forget_button.setAutoDefault(False)
        self.forget_button.setDefault(False)
        self.forget_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forget_button.setStyleSheet(
            f'QPushButton#forgetBrowsePosition {{background:transparent;color:{theme.SECONDARY};'
            'border:0;padding:8px 0;font-size:12px;font-weight:400;text-align:left;}'
            f'QPushButton#forgetBrowsePosition:hover {{color:{theme.LABEL};text-decoration:underline;}}'
            f'QPushButton#forgetBrowsePosition:focus {{border:1px solid {theme.SEPARATOR};border-radius:6px;}}')
        self.forget_button.setAccessibleDescription('只重置浏览位置，照片和所有筛选记录仍保留。')
        self.forget_button.clicked.connect(lambda: self.choose('forget'))
        secondary.addWidget(self.forget_button, 0, Qt.AlignmentFlag.AlignLeft)
        secondary.addStretch()
        outer.addLayout(secondary)

    def choose(self, choice):
        self.choice = choice
        self.accept()

    def reject(self):
        self.choice = None
        super().reject()

    def showEvent(self, event):
        self.choice = None
        super().showEvent(event)
        self.keep_button.setFocus(Qt.FocusReason.OtherFocusReason)
