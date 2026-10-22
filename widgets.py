"""Custom widgets: photo pane with animated in-place zoom, transition ghosts, toast, segmented control."""
from __future__ import annotations
import math

from PySide6.QtCore import Qt, Signal, QPointF, QRect, QRectF, QSize, QTimer, QEvent
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QTransform
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QLabel, QSizePolicy, QWidget, QVBoxLayout, QProgressBar

import theme
from theme import animate
from apple_ui import GlassSurface
from pathlib import Path
from PySide6.QtGui import QPixmap

class DetailLabel(QLabel):
    def set_text(self,value):self.setText(str(value))

class WelcomePage(QWidget):
    """Static branded welcome image; no slideshow, original-photo load or animation."""
    def __init__(self):
        super().__init__(); self.background=QPixmap(str(Path(__file__).parent/'assets'/'welcome-background.png'))
        # Separate drawing anchors keep circle, title and the two fine-print lines on one exact axis.
        self.parts=[QRectF(667,235,338,339),QRectF(641,781,391,30),QRectF(693,817,283,8),QRectF(636,829,400,8)]
        import splash
        self.art=splash.load_art()   # the optional decoration pack turns this page into the opening scene's last frame
    def paintEvent(self,event):
        painter=QPainter(self); painter.fillRect(self.rect(),QColor('#000000'))
        if self.art:
            import splash
            # Leave the bottom band for the buttons and the hint the page lays out below.
            splash.paint_scene(painter,QRectF(0,0,self.width(),max(200,self.height()-120)),10.,self.art)
            painter.fillRect(QRectF(0,max(200,self.height()-120),self.width(),self.height()),QColor('#1E2026'))
        elif not self.background.isNull():
            scale=min(self.width()/self.background.width(),max(1,self.height()-100)/self.background.height())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            for part in self.parts:
                margin_y=6 if part.height()>100 else 2
                source=part.adjusted(-6,-margin_y,6,margin_y)
                target=QRectF(self.width()/2-source.width()*scale/2,source.top()*scale,source.width()*scale,source.height()*scale)
                painter.drawPixmap(target,self.background,source)


class CapsuleProgress(QProgressBar):
    """Accessible QProgressBar values with one fully rounded, clipped moving capsule."""
    def __init__(self):
        super().__init__();self.phase=0.0;self.timer=QTimer(self);self.timer.setInterval(33);self.timer.timeout.connect(self.tick)
        self.setTextVisible(False);self.setStyleSheet('QProgressBar {background:transparent;border:0;}')
    def busy(self):return self.minimum()==self.maximum()==0
    def sync_animation(self):
        active=self.busy() and self.isVisible() and not theme.REDUCED_MOTION
        if active and not self.timer.isActive():self.timer.start()
        elif not active:self.timer.stop()
    def setRange(self,minimum,maximum):
        super().setRange(minimum,maximum)
        if hasattr(self,'timer'):self.sync_animation();self.update()
    def showEvent(self,event):super().showEvent(event);self.sync_animation()
    def hideEvent(self,event):self.timer.stop();super().hideEvent(event)
    def tick(self):
        self.sync_animation();self.phase=(self.phase+33/2200)%1;self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setPen(Qt.PenStyle.NoPen)
        track=QRectF(self.rect()).adjusted(.5,.5,-.5,-.5);radius=track.height()/2
        clip=QPainterPath();clip.addRoundedRect(track,radius,radius)
        painter.fillPath(clip,QColor('#292A2E'));painter.setClipPath(clip)
        if self.busy():
            width=min(track.width(),max(track.height(),track.width()*.5))
            position=.5 if theme.REDUCED_MOTION else .5-.5*math.cos(2*math.pi*self.phase)
            fill=QRectF(track.left()+(track.width()-width)*position,track.top(),width,track.height())
        else:
            fraction=max(0,min(1,(self.value()-self.minimum())/max(1,self.maximum()-self.minimum())))
            if not fraction:return
            fill=QRectF(track.left(),track.top(),min(track.width(),max(track.height(),track.width()*fraction)),track.height())
        painter.setBrush(QColor(theme.LABEL if theme.HIGH_CONTRAST else theme.BLUE));painter.drawRoundedRect(fill,radius,radius)

class ImportPage(QWidget):
    """Import messages have their own page, separate from welcome controls."""
    def __init__(self):
        super().__init__()
        column=QVBoxLayout(self);column.setContentsMargins(48,32,48,48);column.setSpacing(20);column.addStretch()
        self.title=QLabel('正在打开照片文件夹');self.title.setObjectName('heading');self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.folder=QLabel();self.folder.setObjectName('caption');self.folder.setWordWrap(True);self.folder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bar=CapsuleProgress();self.bar.setAccessibleName('照片导入进度');self.bar.setFixedHeight(18);self.bar.setTextVisible(False)
        self.count=QLabel();self.count.setObjectName('lead');self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint=QLabel('导入完成后开始显示照片');hint.setObjectName('caption');hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for widget in (self.title,self.folder,self.bar,self.count,hint):
            widget.setMinimumWidth(360);widget.setMaximumWidth(720);column.addWidget(widget,0,Qt.AlignmentFlag.AlignHCenter)
        self.folder.setMinimumHeight(44)
        self.bar.setMinimumWidth(360);column.addStretch()
    def set_progress(self,phase,done=0,total=0,folder='',found=0):
        self.title.setText(phase)
        if folder:self.folder.setText(folder)
        if total:
            self.bar.setRange(0,total);self.bar.setValue(min(done,total))
            self.count.setText(f'{done:,} / {total:,} 张 · {min(100,done/total*100):.0f}%')
        else:
            self.bar.setRange(0,0)
            self.count.setText(f'已找到 {found:,} 张照片' if found else '正在准备…')

class PhotoView(QGraphicsView):
    clicked = Signal(object, object)
    background_clicked = Signal()
    next_requested = Signal()
    escape_requested = Signal()
    resized = Signal()
    zoom_changed = Signal()

    def __init__(self, fit_factor=1.0, fit_height_factor=1.0):
        super().__init__()
        self.fit_factor = fit_factor
        self.fit_height_factor = fit_height_factor
        self.setScene(QGraphicsScene(self)); self.item = None; self.text = None
        self.photo = None; self.pixmap = None; self.press = None; self.press_on_image=False; self.dragged=False
        self.click_anchor=None
        self.detail = False; self.full_resolution = False; self.source_width = 0
        self.zoom_animation = None; self.pending_full = None; self.wheel_adjusted = False
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus); self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)   # the photo sits on its caption

    def stop_zoom(self):
        if self.zoom_animation:
            animation, self.zoom_animation = self.zoom_animation, None
            animation.stop(); animation.deleteLater()

    def show_photo(self, photo, pixmap=None, error='', message=''):
        self.stop_zoom(); self.pending_full = None; self.wheel_adjusted = False
        self.click_anchor=None; self.press=None; self.press_on_image=False; self.dragged=False
        self.detail = False; self.full_resolution = False; self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.photo = photo; self.pixmap = pixmap; self.scene().clear(); self.item = self.text = None; self.resetTransform()
        if pixmap:
            self.item = QGraphicsPixmapItem(pixmap); self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self.scene().addItem(self.item); self.scene().setSceneRect(self.item.boundingRect()); self.fit()
        else:
            self.text = self.scene().addSimpleText(error or message or ('正在读取…' if photo else ''))
            font = QFont(self.font()); font.setPixelSize(15); self.text.setFont(font)
            self.text.setBrush(QColor(theme.ERROR_TEXT if error else theme.SECONDARY))
            rect = self.text.boundingRect()
            art = empty_art() if message and not photo and not error else None
            if art is not None:
                # With the decoration pack, an empty pane shows the mascot above its message.
                mascot = self.scene().addPixmap(art); mascot.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
                mascot.setPos(rect.center().x() - art.width() / 2, rect.top() - art.height() - 12)
                rect = rect.united(mascot.sceneBoundingRect())
            self.scene().setSceneRect(rect); self.centerOn(rect.center())

    def fit_scale(self):
        rect = self.sceneRect(); view = self.viewport().size()
        if rect.width() <= 0 or rect.height() <= 0: return 1.0
        scale = min(view.width() / rect.width(), view.height() * self.fit_height_factor / rect.height())
        # A preview decoded for this pane size draws as a plain blit, without per-frame resampling.
        scale=(1.0 if 1.0 <= scale < 1.04 else scale) * self.fit_factor
        reference=getattr(self,'comparison_reference',None)
        if reference is not None and reference.pixmap:
            original=reference.sceneRect()
            limit=.6*max(original.width(),original.height())*reference.fit_scale()
            scale=min(scale,limit/max(rect.width(),rect.height()))
        return scale

    def fit(self):
        scale = self.fit_scale(); self.setTransform(QTransform.fromScale(scale, scale)); self.centerOn(self.sceneRect().center())

    def zoom_target(self):
        if self.full_resolution or not self.pixmap: return 1.0
        return max(self.fit_scale(), (self.source_width or self.pixmap.width()) / max(1, self.pixmap.width()))

    def animate_scale(self, target, anchor_scene, anchor_view, duration=200, done=None):
        """Zoom toward target while anchor_scene stays under anchor_view (the clicked point stays under the cursor)."""
        self.stop_zoom(); centre = QPointF(self.viewport().rect().center())
        state=[self.transform().m11(),target,anchor_scene,anchor_view,0.0]
        def step(t):
            # A small same-event response avoids waiting for the first display-clock tick.
            progress=.005+.995*t
            start,target,scene,view,base=state
            part=max(0.0,min(1.0,(progress-base)/max(1e-9,1-base)))
            scale=start*(target/start)**part
            self.setTransform(QTransform.fromScale(scale,scale));self.centerOn(scene-(view-centre)/scale)
            if self.pending_full is not None and self.detail:
                pixmap,self.pending_full=self.pending_full,None
                scene,view=self.swap_full_pixels(pixmap)
                # Retarget this motion's remaining time, rather than waiting 340 ms
                # and starting a second 130 ms animation when sharp pixels are ready.
                state[:]=[self.transform().m11(),1.0,scene,view,progress]
                if progress>=1:
                    self.setTransform(QTransform.fromScale(1.0,1.0));self.centerOn(scene-(view-centre))
        def finished():
            self.zoom_animation = None
            if done: done()
        self.zoom_animation = animate(self, duration, step, theme.EASE, finished)

    def zoom_at(self, point=None):
        if not self.pixmap: return
        self.detail = True; self.wheel_adjusted = False; self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        view = QPointF(point) if point is not None else QPointF(self.viewport().rect().center())
        scene=self.mapToScene(view.toPoint());rect=self.sceneRect()
        self.click_anchor=((scene.x()-rect.left())/max(1,rect.width()),(scene.y()-rect.top())/max(1,rect.height()),view)
        self.animate_scale(self.zoom_target(),scene,view,done=self.finish_clicked_zoom)

    def finish_clicked_zoom(self):
        self.apply_pending();self.zoom_changed.emit()

    def apply_pending(self):
        if self.pending_full is not None:
            pixmap, self.pending_full = self.pending_full, None; self.install_full_image(pixmap)

    def install_full_image(self, pixmap):
        if not self.detail: return
        if self.full_resolution and self.pixmap is not None and self.pixmap.cacheKey()==pixmap.cacheKey():
            self.zoom_changed.emit();return
        if self.zoom_animation: self.pending_full = pixmap; return
        anchor,view=self.swap_full_pixels(pixmap)
        # A manual wheel/pan owns magnification and position while decoding finishes.
        if self.wheel_adjusted:self.zoom_changed.emit()
        else:self.animate_scale(1.0,anchor,view,90,self.zoom_changed.emit)

    def swap_full_pixels(self,pixmap):
        """Install sharp pixels without moving them; return the current click/centre anchor."""
        old_rect = self.sceneRect(); centre = self.mapToScene(self.viewport().rect().center())
        fraction = ((centre.x() - old_rect.left()) / max(1, old_rect.width()), (centre.y() - old_rect.top()) / max(1, old_rect.height()))
        scale = self.transform().m11() * old_rect.width() / max(1e-6, pixmap.deviceIndependentSize().width())
        self.pixmap = pixmap; self.scene().clear(); self.text = None
        self.item = QGraphicsPixmapItem(pixmap); self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation); self.scene().addItem(self.item)
        rect = self.item.boundingRect(); self.scene().setSceneRect(rect); self.full_resolution = True
        target = QPointF(rect.left() + fraction[0] * rect.width(), rect.top() + fraction[1] * rect.height())
        self.setTransform(QTransform.fromScale(scale, scale)); self.centerOn(target)
        if self.click_anchor is not None:
            x,y,view=self.click_anchor;anchor=QPointF(rect.left()+x*rect.width(),rect.top()+y*rect.height())
        else:anchor,view=target,QPointF(self.viewport().rect().center())
        return anchor,view

    def unzoom(self, done):
        """Leave zoom immediately in state, then animate back to the fitted photo."""
        # Keep full_resolution truthful until show_photo actually replaces those
        # pixels: an interrupted shrink may be reversed before the callback runs.
        self.detail = False; self.pending_full = None
        self.click_anchor=None
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        if not self.pixmap: done(); return
        rect = self.sceneRect(); target = self.fit_scale(); start = self.transform().m11()
        begin = self.mapToScene(self.viewport().rect().center()); end = rect.center(); self.stop_zoom()
        def step(t):
            progress=.005+.995*t
            scale = start*(target/start)**progress; self.setTransform(QTransform.fromScale(scale, scale))
            self.centerOn(begin + (end - begin) * progress)
        def finished():
            self.zoom_animation = None; done()
        self.zoom_animation = animate(self, 230, step, theme.EASE, finished)

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
        self.press = event.position() if event.button()==Qt.MouseButton.LeftButton else None
        self.press_on_image=self.image_at(event.position());self.dragged=False
        if event.button() == Qt.MouseButton.RightButton:
            self.next_requested.emit(); event.accept(); return
        super().mousePressEvent(event)

    def image_at(self,point):
        if self.item is None or self.pixmap is None:return False
        scene=self.mapToScene(QPointF(point).toPoint())
        return self.item.boundingRect().contains(self.item.mapFromScene(scene))

    def mouseMoveEvent(self,event):
        if self.press is not None and (event.position()-self.press).manhattanLength()>=5:
            self.dragged=True;self.click_anchor=None
            if self.detail:
                self.stop_zoom();self.wheel_adjusted=True;self.apply_pending()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self,event):
        # Qt replaces the second press within its double-click interval with this
        # event. Treat it as another ordinary click so zoom reversals are never lost.
        self.mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        # Finish Qt's drag before toggling; a drag must not count as a click.
        click = event.button() == Qt.MouseButton.LeftButton and self.press is not None and not self.dragged and (event.position() - self.press).manhattanLength() < 5
        started_on_image=self.press_on_image
        point = event.position(); super().mouseReleaseEvent(event)
        self.press=None;self.press_on_image=False;self.dragged=False
        if click:
            on_image=self.image_at(point)
            if started_on_image and on_image and self.photo:self.clicked.emit(self.photo,point)
            elif not started_on_image and not on_image:self.background_clicked.emit()

    def wheelEvent(self, event):
        if not self.detail or not self.pixmap:
            event.ignore(); return
        delta = event.angleDelta().y()
        steps = delta / 120 if delta else event.pixelDelta().y() / 100
        if not steps:
            event.accept(); return
        view = event.position(); anchor = self.mapToScene(view.toPoint())
        self.stop_zoom(); self.wheel_adjusted = True;self.click_anchor=None
        # Smooth trackpad deltas and high-resolution wheels use fractional steps. Limits prevent huge render sizes.
        native = self.zoom_target(); current = self.transform().m11()
        scale = max(self.fit_scale(), min(native * 8, current * 1.2 ** max(-20, min(20, steps))))
        self.setTransform(QTransform.fromScale(scale, scale))
        centre = QPointF(self.viewport().rect().center())
        self.centerOn(anchor - (view - centre) / scale)
        self.apply_pending(); self.zoom_changed.emit(); event.accept()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)


_EMPTY_ART = []


def empty_art():
    """The decoration pack's mascot at 120 px high, or None without a pack (looked up once)."""
    if not _EMPTY_ART:
        import theme_pack
        path = theme_pack.find('splash/mascot.png'); pixmap = QPixmap(str(path)) if path else QPixmap()
        _EMPTY_ART.append(None if pixmap.isNull() else pixmap.scaledToHeight(120, Qt.TransformationMode.SmoothTransformation))
    return _EMPTY_ART[0]


class Elided(QLabel):
    """Single-line label that keeps its full text and re-elides whenever it is resized."""
    def __init__(self, name='', mode=Qt.TextElideMode.ElideMiddle):
        super().__init__(); self.full = ''; self.mode = mode; self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    # It asks for its full text and may shrink to nothing. (An Ignored policy would also drop explicit
    # minimum and fixed widths, letting neighbours in a row draw over it.)
    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.full) + 4, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def text(self):
        return self.full      # the full text; the elided form is only what is painted

    def set_text(self, text):
        if text != self.full: self.full = text; self.refresh()

    def refresh(self):
        shown = self.fontMetrics().elidedText(self.full.replace('\n', ' · '), self.mode, max(0, self.width()))   # always one line
        super().setText(shown); self.setToolTip(self.full if shown != self.full else '')

    def resizeEvent(self, event):
        super().resizeEvent(event); self.refresh()


class Ghost(QWidget):
    """A snapshot drawn with painter opacity; far cheaper per frame than QGraphicsOpacityEffect."""
    def __init__(self, parent, pixmap, rect):
        super().__init__(parent); self.pixmap = pixmap; self.opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); self.setGeometry(rect); self.show(); self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self); painter.setOpacity(self.opacity)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)   # a 1:1 blit unless the pane is scaling its preview
        painter.drawPixmap(self.rect(), self.pixmap)


class Toast(QWidget):
    COLORS = {'keep': theme.GREEN, 'delete': theme.RED, 'error': '#FF9F0A', 'info': theme.BLUE}

    def __init__(self, parent):
        super().__init__(parent); self.message = ''; self.kind = 'info'; self.opacity = 0.0; self.offset = 0.0; self.bottom = 0
        self.animation = None; self.leaving = False; self.pending = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); self.hide()
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.leave)
        font = QFont(self.font()); font.setPixelSize(13); font.setWeight(QFont.Weight.Medium); self.setFont(font)

    def place(self, bottom):
        self.bottom = bottom; self.reposition()

    def reposition(self):
        width = self.fontMetrics().horizontalAdvance(self.message) + 52; height = 36; parent = self.parentWidget()
        rect = QRect((parent.width() - width) // 2, int(self.bottom - height + self.offset), width, height)
        if rect != self.geometry(): self.setGeometry(rect)

    def defer(self, message, kind):
        """Hold a message for show_pending (held keys coalesce into one repaint); any message shown before then wins."""
        self.pending = (message, kind)

    def show_pending(self):
        if self.pending: self.show_message(*self.pending)

    def active(self):
        return self.isVisible() or self.pending is not None

    def show_message(self, message, kind='info', duration=1400):
        positioner=getattr(self,'positioner',None)
        if positioner:positioner()
        self.pending = None
        changed = (message, kind) != (self.message, self.kind); self.message = message; self.kind = kind
        if changed: self.reposition(); self.update()
        self.raise_()
        if not self.isVisible() or self.leaving:
            self.leaving = False; self.show(); self.run(self.opacity, 1.0, 80)
        self.timer.start(duration)

    def leave(self):
        self.leaving = True; self.run(self.opacity, 0.0, 100, self.hide_now)

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


class Segmented(GlassSurface):
    title_bar_control = True   # clicks select a view, never drag the window
    """iOS-style segmented control with a sliding selection thumb and live counts."""
    changed = Signal(str)

    def __init__(self, items):
        super().__init__(); self.items = items; self.counts = {}; self.index = 0; self.position = 0.0; self.animation = None; self.width_cache = 0
        self.setFixedHeight(32); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus);self.setAccessibleName('筛选视图：未筛选、保留、已删除')
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
        self.animation = animate(self, 100, step, theme.EASE, finished, self.position, index)

    def mousePressEvent(self, event):
        index = int((event.position().x() - 2) // self.segment_width())
        if 0 <= index < len(self.items) and index != self.index: self.changed.emit(self.items[index][0])

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing); painter.setPen(Qt.PenStyle.NoPen)
        painter.end();super().paintEvent(event);painter.begin(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setPen(Qt.PenStyle.NoPen)
        width = self.segment_width(); painter.setBrush(QColor(theme.BLUE if not theme.HIGH_CONTRAST else '#FFFFFF'))   # screening's lava accent
        painter.drawRoundedRect(QRectF(2 + self.position * width, 2, width, self.height() - 4), 14, 14)
        metrics = self.fontMetrics()
        for i in range(len(self.items)):
            text, count = self.label(i); font = QFont(self.font()); font.setWeight(QFont.Weight.DemiBold if i == self.index else QFont.Weight.Normal)
            painter.setFont(font); metrics = painter.fontMetrics()
            total = metrics.horizontalAdvance(text) + (metrics.horizontalAdvance('  ' + count) if count else 0)
            x = 2 + i * width + (width - total) / 2; box = QRectF(x, 0, total + 4, self.height())
            painter.setPen(QColor('#000000' if theme.HIGH_CONTRAST and i==self.index else theme.LABEL)); painter.drawText(box, Qt.AlignmentFlag.AlignVCenter, text)
            if count:
                painter.setPen(QColor('#000000' if theme.HIGH_CONTRAST and i==self.index else '#FFFFFF' if theme.HIGH_CONTRAST else theme.SECONDARY)); painter.drawText(box.adjusted(metrics.horizontalAdvance(text + '  '), 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, count)
        if self.hasFocus() and theme.HIGH_CONTRAST:
            painter.setBrush(Qt.BrushStyle.NoBrush);painter.setPen(QColor('#FFFFFF'));painter.drawRoundedRect(QRectF(self.rect()).adjusted(1,1,-1,-1),15,15)


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
