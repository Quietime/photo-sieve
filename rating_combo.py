"""The rating page's rounded selectors, with an entirely painted arrow and standard Qt behavior."""
from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QComboBox, QFrame, QListView, QStyledItemDelegate, QWidget
import theme


class _PopupBackground(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet('background:transparent;')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.SEPARATOR), 1))
        painter.setBrush(QColor(theme.ELEVATED))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 12, 12)


class RoundedCombo(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ratingRoundedCombo')
        self.setMinimumHeight(38)
        self.setMinimumWidth(148)
        self.setMaxVisibleItems(8)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        view = QListView(self)
        view.setItemDelegate(QStyledItemDelegate(view))
        self.setView(view)
        self.setStyleSheet('''
            QComboBox#ratingRoundedCombo {background:transparent;border:0;padding:0;min-height:38px;}
            QComboBox#ratingRoundedCombo::drop-down {background:transparent;border:0;width:0;}
            QComboBox#ratingRoundedCombo::down-arrow {image:none;width:0;height:0;}
        ''')
        self._popup = None
        self._popup_background = None

    def sizeHint(self):
        width = max((self.fontMetrics().horizontalAdvance(self.itemText(i))
            + (24 if not self.itemIcon(i).isNull() else 0) for i in range(self.count())), default=90)
        base = super().sizeHint()
        return QSize(max(148, width + 44, base.width()), max(38, base.height()))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        high_contrast = theme.HIGH_CONTRAST
        # ELEVATED2 is the app's blue action colour; selectors remain neutral until an option is selected.
        fill = '#000000' if high_contrast else '#4A4F56' if self.underMouse() and self.isEnabled() else '#3F434A' if self.isEnabled() else '#34383E'
        edge = '#FFFFFF' if high_contrast else theme.BLUE if self.hasFocus() else theme.SEPARATOR
        color = '#FFFFFF' if high_contrast else theme.LABEL if self.isEnabled() else theme.TERTIARY
        painter.setPen(QPen(QColor(edge), 1.5 if self.hasFocus() or high_contrast else 1))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.75, .75, -.75, -.75), 9, 9)

        right_to_left = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        text = QRect(12, 0, max(0, self.width() - 44), self.height())
        if right_to_left:
            text.moveLeft(32)
        item_icon = self.itemIcon(self.currentIndex())
        if not item_icon.isNull():
            icon_x = text.right() - 17 if right_to_left else text.left()
            item_icon.paint(painter, QRect(icon_x, (self.height() - 18) // 2, 18, 18))
            if right_to_left:
                text.adjust(0, 0, -24, 0)
            else:
                text.adjust(24, 0, 0, 0)
        if not self.isEditable():
            painter.setPen(QColor(color))
            painter.setClipRect(text)
            alignment = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if right_to_left else Qt.AlignmentFlag.AlignLeft)
            painter.drawText(text, alignment, self.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideRight, text.width()))
            painter.setClipping(False)

        x = 18 if right_to_left else self.width() - 18
        y = self.height() / 2
        raised = bool(self._popup is not None and self._popup.isVisible())
        arrow = QPainterPath()
        arrow.moveTo(x - 4.5, y + (2 if raised else -2))
        arrow.lineTo(x, y + (-2.5 if raised else 2.5))
        arrow.lineTo(x + 4.5, y + (2 if raised else -2))
        painter.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(arrow)

    def _prepare_popup(self):
        view = self.view()
        popup = view.window()
        if popup is self.window() or not popup.isWindow():
            return
        if self._popup is not popup:
            self._popup = popup
            popup.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            popup.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
            popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            popup.setObjectName('ratingComboPopup')
            popup.setStyleSheet(f'QFrame#ratingComboPopup {{background:{theme.ELEVATED};border:1px solid {theme.SEPARATOR};border-radius:12px;}}')
            self._popup_background = _PopupBackground(popup)
            self._popup_background.show()
            self._popup_background.lower()
            popup.installEventFilter(self)
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.viewport().setStyleSheet('background:transparent;')
        view.viewport().setAutoFillBackground(False)
        view.setStyleSheet(f'''
            QListView {{background:transparent;color:{theme.LABEL};border:0;padding:6px;outline:0;}}
            QListView::item {{min-height:28px;padding:6px 10px;border:0;border-radius:7px;}}
            QListView::item:selected {{background:{theme.BLUE};color:#FFFFFF;}}
            QListView::item:hover {{background:#41464D;}}
        ''')

    def _round_popup(self):
        popup = self._popup
        if popup is not None and popup.width() > 0 and popup.height() > 0:
            if self._popup_background is not None:
                self._popup_background.setGeometry(popup.rect())
                self._popup_background.lower()
            outline = QPainterPath()
            outline.addRoundedRect(QRectF(popup.rect()), 12, 12)
            popup.setMask(QRegion(outline.toFillPolygon().toPolygon()))

    def showPopup(self):
        self._prepare_popup()
        super().showPopup()
        self._round_popup()
        self.update()

    def hidePopup(self):
        super().hidePopup()
        self.update()

    def eventFilter(self, watched, event):
        if watched is self._popup and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._round_popup()
        return super().eventFilter(watched, event)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)
