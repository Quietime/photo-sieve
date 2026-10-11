"""Custom widgets: photo pane with animated in-place zoom, transition ghosts, toast, segmented control."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPointF, QRect, QRectF, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QTransform
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QLabel, QSizePolicy, QWidget

import theme
from theme import animate


class PhotoView(QGraphicsView):
    clicked = Signal(object, object)
    next_requested = Signal()
    escape_requested = Signal()
    resized = Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self)); self.item = None; self.text = None
        self.photo = None; self.pixmap = None; self.press = None
        self.detail = False; self.full_resolution = False; self.source_width = 0
        self.zoom_animation = None; self.pending_full = None
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus); self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)   # the photo sits on its caption

    def stop_zoom(self):
        if self.zoom_animation:
            animation, self.zoom_animation = self.zoom_animation, None
            animation.stop(); animation.deleteLater()

    def show_photo(self, photo, pixmap=None, error='', message=''):
        self.stop_zoom(); self.pending_full = None
        self.detail = False; self.full_resolution = False; self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.photo = photo; self.pixmap = pixmap; self.scene().clear(); self.item = self.text = None; self.resetTransform()
        if pixmap:
            self.item = QGraphicsPixmapItem(pixmap); self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self.scene().addItem(self.item); self.scene().setSceneRect(self.item.boundingRect()); self.fit()
        else:
            self.text = self.scene().addSimpleText(error or message or ('正在读取…' if photo else ''))
            font = QFont(self.font()); font.setPixelSize(15); self.text.setFont(font)
            self.text.setBrush(QColor(theme.RED if error else theme.SECONDARY))
            self.scene().setSceneRect(self.text.boundingRect()); self.centerOn(self.text)

    def fit_scale(self):
        rect = self.sceneRect(); view = self.viewport().size()
        if rect.width() <= 0 or rect.height() <= 0: return 1.0
        scale = min(view.width() / rect.width(), view.height() / rect.height())
        # A preview decoded for this pane size draws as a plain blit, without per-frame resampling.
        return 1.0 if 1.0 <= scale < 1.04 else scale

    def fit(self):
        scale = self.fit_scale(); self.setTransform(QTransform.fromScale(scale, scale)); self.centerOn(self.sceneRect().center())

    def zoom_target(self):
        if self.full_resolution or not self.pixmap: return 1.0
        return max(self.fit_scale(), (self.source_width or self.pixmap.width()) / max(1, self.pixmap.width()))

    def animate_scale(self, target, anchor_scene, anchor_view, duration=340, done=None):
        """Zoom toward target while anchor_scene stays under anchor_view (the clicked point stays under the cursor)."""
        self.stop_zoom(); start = self.transform().m11(); centre = QPointF(self.viewport().rect().center())
        def step(t):
            scale = start + (target - start) * t; self.setTransform(QTransform.fromScale(scale, scale))
            self.centerOn(anchor_scene - (anchor_view - centre) / scale)
        def finished():
            self.zoom_animation = None
            if done: done()
        self.zoom_animation = animate(self, duration, step, theme.SNAPPY, finished)

    def zoom_at(self, point=None):
        if not self.pixmap: return
        self.detail = True; self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        view = QPointF(point) if point is not None else QPointF(self.viewport().rect().center())
        self.animate_scale(self.zoom_target(), self.mapToScene(view.toPoint()), view, done=self.apply_pending)

    def apply_pending(self):
        if self.pending_full is not None:
            pixmap, self.pending_full = self.pending_full, None; self.install_full_image(pixmap)

    def install_full_image(self, pixmap):
        if not self.detail: return
        if self.zoom_animation: self.pending_full = pixmap; return
        # Swap in the original at the same on-screen size and position, then settle at 100%.
        old_rect = self.sceneRect(); centre = self.mapToScene(self.viewport().rect().center())
        fraction = ((centre.x() - old_rect.left()) / max(1, old_rect.width()), (centre.y() - old_rect.top()) / max(1, old_rect.height()))
        scale = self.transform().m11() * old_rect.width() / max(1e-6, pixmap.deviceIndependentSize().width())
        self.pixmap = pixmap; self.scene().clear(); self.text = None
        self.item = QGraphicsPixmapItem(pixmap); self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation); self.scene().addItem(self.item)
        rect = self.item.boundingRect(); self.scene().setSceneRect(rect); self.full_resolution = True
        target = QPointF(rect.left() + fraction[0] * rect.width(), rect.top() + fraction[1] * rect.height())
        self.setTransform(QTransform.fromScale(scale, scale)); self.centerOn(target)
        self.animate_scale(1.0, target, QPointF(self.viewport().rect().center()), 260)

    def unzoom(self, done):
        """Leave zoom immediately in state, then animate back to the fitted photo."""
        self.detail = False; self.full_resolution = False; self.pending_full = None
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        if not self.pixmap: done(); return
        rect = self.sceneRect(); target = self.fit_scale(); start = self.transform().m11()
        begin = self.mapToScene(self.viewport().rect().center()); end = rect.center(); self.stop_zoom()
        def step(t):
            scale = start + (target - start) * t; self.setTransform(QTransform.fromScale(scale, scale))
            self.centerOn(begin + (end - begin) * t)
        def finished():
            self.zoom_animation = None; done()
        self.zoom_animation = animate(self, 260, step, theme.SNAPPY, finished)

    def content_rect(self):
        """Viewport rectangle currently covered by the photo, for transition snapshots."""
        if not self.item: return QRect()
        return self.mapFromScene(self.item.sceneBoundingRect()).boundingRect() & self.viewport().rect()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.pixmap and not self.detail and not self.zoom_animation: self.fit()
        elif self.text: self.centerOn(self.text)
        self.resized.emit()

    def mousePressEvent(self, event):
        self.press = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            self.next_requested.emit(); event.accept(); return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        # Finish Qt's drag before toggling; a drag must not count as a click.
        click = event.button() == Qt.MouseButton.LeftButton and self.press is not None and (event.position() - self.press).manhattanLength() < 5
        point = event.position(); super().mouseReleaseEvent(event)
        if click and self.photo: self.clicked.emit(self.photo, point)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape: self.escape_requested.emit(); event.accept(); return
        super().keyPressEvent(event)


class Elided(QLabel):
    """Single-line label that keeps its full text and re-elides whenever it is resized."""
    def __init__(self, name='', mode=Qt.TextElideMode.ElideMiddle):
        super().__init__(); self.full = ''; self.mode = mode; self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_text(self, text):
        if text != self.full: self.full = text; self.refresh()

    def refresh(self):
        shown = self.fontMetrics().elidedText(self.full, self.mode, max(0, self.width()))
        super().setText(shown); self.setToolTip(self.full if shown != self.full else '')

    def resizeEvent(self, event):
        super().resizeEvent(event); self.refresh()


class Ghost(QWidget):
    """A snapshot drawn with painter opacity; far cheaper per frame than QGraphicsOpacityEffect."""
    def __init__(self, parent, pixmap, rect):
        super().__init__(parent); self.pixmap = pixmap; self.opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); self.setGeometry(rect); self.show(); self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self); painter.setOpacity(self.opacity); painter.drawPixmap(self.rect(), self.pixmap)


class Toast(QWidget):
    COLORS = {'keep': theme.GREEN, 'delete': theme.RED, 'error': '#FF9F0A', 'info': theme.BLUE}

    def __init__(self, parent):
        super().__init__(parent); self.message = ''; self.kind = 'info'; self.opacity = 0.0; self.offset = 0.0; self.bottom = 0
        self.animation = None; self.leaving = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); self.hide()
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.leave)
        font = QFont(self.font()); font.setPixelSize(13); font.setWeight(QFont.Weight.Medium); self.setFont(font)

    def place(self, bottom):
        self.bottom = bottom; self.reposition()

    def reposition(self):
        width = self.fontMetrics().horizontalAdvance(self.message) + 52; height = 36; parent = self.parentWidget()
        rect = QRect((parent.width() - width) // 2, int(self.bottom - height + self.offset), width, height)
        if rect != self.geometry(): self.setGeometry(rect)

    def show_message(self, message, kind='info', duration=1400):
        changed = (message, kind) != (self.message, self.kind); self.message = message; self.kind = kind
        if changed: self.reposition(); self.update()
        self.raise_()
        if not self.isVisible() or self.leaving:
            self.leaving = False; self.show(); self.run(self.opacity, 1.0, 200)
        self.timer.start(duration)

    def leave(self):
        self.leaving = True; self.run(self.opacity, 0.0, 250, self.hide_now)

    def hide_now(self):
        if self.leaving: self.hide(); self.leaving = False

    def run(self, start, end, duration, done=None):
        if self.animation: self.animation.stop()
        def step(value):
            self.opacity = value; self.offset = (1 - value) * 8; self.reposition(); self.update()
        def finished():
            self.animation = None
            if done: done()
        self.animation = animate(self, duration, step, theme.EASE, finished, start, end)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing); painter.setOpacity(self.opacity)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5); path = QPainterPath(); path.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.fillPath(path, QColor(44, 44, 46, 240)); painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.COLORS.get(self.kind, theme.BLUE))); painter.drawEllipse(QRectF(16, rect.height() / 2 - 3.5, 7, 7))
        painter.setPen(QColor(theme.LABEL)); painter.drawText(QRectF(30, 0, rect.width() - 44, rect.height()), Qt.AlignmentFlag.AlignVCenter, self.message)


class Segmented(QWidget):
    """iOS-style segmented control with a sliding selection thumb and live counts."""
    changed = Signal(str)

    def __init__(self, items):
        super().__init__(); self.items = items; self.counts = {}; self.index = 0; self.position = 0.0; self.animation = None; self.width_cache = 0
        self.setFixedHeight(32); self.setCursor(Qt.CursorShape.PointingHandCursor)
        font = QFont(self.font()); font.setPixelSize(13); self.setFont(font)

    def label(self, i):
        name, text = self.items[i]; count = self.counts.get(name)
        return text, ('' if count is None else f'{count:,}')

    def segment_width(self):
        return self.width_cache

    def sizeHint(self):
        return QSize(self.width_cache * len(self.items) + 4, 32)

    def set_counts(self, counts):
        if counts == self.counts and self.width_cache: return
        self.counts = dict(counts); bold = QFont(self.font()); bold.setWeight(QFont.Weight.DemiBold); metrics = QFontMetrics(bold)
        self.width_cache = max(metrics.horizontalAdvance(a + '  ' + b) for a, b in (self.label(i) for i in range(len(self.items)))) + 32
        if self.width() != self.sizeHint().width(): self.setFixedWidth(self.sizeHint().width())
        self.update()

    def set_current(self, name, animated=True):
        index = next(i for i, (n, _) in enumerate(self.items) if n == name)
        if index == self.index and not self.animation: return
        self.index = index
        if self.animation: self.animation.stop()
        if not animated: self.position = index; self.update(); return
        def step(value): self.position = value; self.update()
        def finished(): self.animation = None
        self.animation = animate(self, 250, step, theme.EASE, finished, self.position, index)

    def mousePressEvent(self, event):
        index = int((event.position().x() - 2) // self.segment_width())
        if 0 <= index < len(self.items) and index != self.index: self.changed.emit(self.items[index][0])

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing); painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ELEVATED)); painter.drawRoundedRect(QRectF(self.rect()), 9, 9)
        width = self.segment_width(); painter.setBrush(QColor('#48484A'))
        painter.drawRoundedRect(QRectF(2 + self.position * width, 2, width, self.height() - 4), 7, 7)
        metrics = self.fontMetrics()
        for i in range(len(self.items)):
            text, count = self.label(i); font = QFont(self.font()); font.setWeight(QFont.Weight.DemiBold if i == self.index else QFont.Weight.Normal)
            painter.setFont(font); metrics = painter.fontMetrics()
            total = metrics.horizontalAdvance(text) + (metrics.horizontalAdvance('  ' + count) if count else 0)
            x = 2 + i * width + (width - total) / 2; box = QRectF(x, 0, total + 4, self.height())
            painter.setPen(QColor(theme.LABEL)); painter.drawText(box, Qt.AlignmentFlag.AlignVCenter, text)
            if count:
                painter.setPen(QColor(theme.SECONDARY)); painter.drawText(box.adjusted(metrics.horizontalAdvance(text + '  '), 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, count)


class ProgressLine(QWidget):
    """Two-pixel progress line under the toolbar, like Safari's loading bar."""
    def __init__(self):
        super().__init__(); self.setFixedHeight(2); self.active = False; self.value = 0.0; self.target = 0.0; self.phase = 0.0
        self.animation = None; self.timer = QTimer(self); self.timer.setInterval(16); self.timer.timeout.connect(self.tick)

    def set_progress(self, done, total, active=True):
        self.active = active
        if not active: self.timer.stop(); self.value = 0.0; self.update(); return
        if not self.timer.isActive(): self.timer.start()
        self.target = done / total if total else -1.0

    def tick(self):
        if self.target < 0: self.phase = (self.phase + .012) % 1.3
        else: self.value += (self.target - self.value) * .18
        self.update()

    def paintEvent(self, event):
        if not self.active: return
        painter = QPainter(self); color = QColor(theme.BLUE)
        if self.target < 0:
            x = (self.phase - .3) * self.width(); painter.fillRect(QRectF(x, 0, self.width() * .3, 2), color)
        else:
            painter.fillRect(QRectF(0, 0, self.width() * self.value, 2), color)
