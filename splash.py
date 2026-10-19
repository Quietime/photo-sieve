"""Opening animation: about a second on start, skippable with any click or key.

The intro plays first; the main window is built while the finished frame holds (building blocks the
interface thread, so nothing may be moving then); the splash then fades out over the new window.
Artwork comes from the optional decoration pack (theme_pack, folder 'splash'); without it the same
choreography runs on drawn shapes. Reduced motion or 设置 → 启动动画 off skips it entirely.
"""
from __future__ import annotations
import math
import time
from PySide6.QtCore import QEventLoop, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap, QPolygonF, QGuiApplication
from PySide6.QtWidgets import QWidget
import theme
import theme_pack

INTRO = 0.95      # seconds of motion before the window is built
OUTRO = 0.28      # fade over the window
LAVA, WINE, ICE, DEEP_ICE, SILVER = '#A8423C', '#4A2124', '#A9C4E8', '#4C789B', '#E6EAF0'

# (file in the pack's splash folder, centre x, centre y as fractions, height as a fraction, start s, from-offset px, spin deg)
LAYERS = [
    ('rock-1.png', .10, .26, .26, .10, (-60, -10), -12), ('rock-2.png', .90, .70, .22, .16, (60, 20), 10),
    ('truss-1.png', .95, .25, .48, .05, (40, -30), 6), ('truss-2.png', .05, .78, .40, .12, (-40, 30), -6),
    ('flower-1.png', .16, .80, .30, .22, (0, 30), -20), ('flower-2.png', .84, .20, .26, .28, (0, -24), 18),
    ('flower-3.png', .74, .86, .20, .34, (20, 20), 25), ('flower-4.png', .27, .12, .17, .40, (-16, -16), -25),
    ('pyramid.png', .50, .82, .24, .30, (0, 50), 0), ('filigree.png', .50, .30, .17, .46, (0, 0), 0),
    ('mascot.png', .63, .72, .13, .62, (0, 24), 0),
]


def spring(p, ratio=.55, speed=9.):
    """Step response of a damped spring at time p (s): 0 → 1 with a natural overshoot."""
    if p <= 0:
        return 0.
    w = speed; wd = w * math.sqrt(max(1e-6, 1 - ratio * ratio))
    return 1 - math.exp(-ratio * w * p) * (math.cos(wd * p) + ratio * w / wd * math.sin(wd * p))


def enabled(settings):
    return settings.value('opening_animation', True, type=bool) and not theme.REDUCED_MOTION and not settings.value('review/reduced_motion', False, type=bool)


class Splash(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground); self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('照片快筛'); self.resize(760, 460); self.t = 0.; self.fade = 1.; self.skipped = False
        screen = QGuiApplication.primaryScreen().availableGeometry(); self.move(screen.center() - self.rect().center())
        self.art = {}
        for name, *_ in LAYERS:
            path = theme_pack.find('splash/' + name)
            if path:
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    self.art[name] = pixmap

    def mousePressEvent(self, event): self.skipped = True
    def keyPressEvent(self, event): self.skipped = True

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(self.fade)
        w, h, t = self.width(), self.height(), self.t
        clip = QPainterPath(); clip.addRoundedRect(QRectF(self.rect()), 22, 22); painter.setClipPath(clip)
        # Two grounds, lava and ice, meeting on a diagonal that sweeps into place.
        split = .56 - .12 * (1 - spring(t, .9, 7))
        painter.fillRect(self.rect(), QColor('#1E2A39'))
        lava = QPolygonF([QPointF(0, 0), QPointF(w * split, 0), QPointF(w * (split - .12), h), QPointF(0, h)])
        gradient = QLinearGradient(0, 0, w * .6, h); gradient.setColorAt(0, QColor('#3A1D20')); gradient.setColorAt(1, QColor('#22171A'))
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(gradient); painter.drawPolygon(lava)
        for name, cx, cy, size, start, offset, spin in LAYERS:
            local = t - start
            if local <= 0:
                continue
            k = spring(local, .5 if 'flower' in name else .7, 10 if 'flower' in name else 8)
            opacity = min(1., local / .18)
            x = cx * w + offset[0] * (1 - k); y = cy * h + offset[1] * (1 - k)
            painter.save(); painter.setOpacity(self.fade * opacity); painter.translate(x, y)
            painter.rotate(spin * (1 - k)); scale = .6 + .4 * k if 'flower' in name or name == 'mascot.png' else 1.
            painter.scale(scale, scale)
            pixmap = self.art.get(name)
            if pixmap:
                height = size * h; width = height * pixmap.width() / max(1, pixmap.height())
                if name == 'filigree.png':      # the scrollwork unrolls from the centre outward
                    reveal = min(1., local / .45); painter.setClipRect(QRectF(-width * reveal / 2, -height, width * reveal, height * 2))
                painter.drawPixmap(QRectF(-width / 2, -height / 2, width, height), pixmap, QRectF(pixmap.rect()))
            elif name == 'pyramid.png':
                self.draw_pyramid(painter, size * h * 1.6)
            elif name == 'filigree.png':
                self.draw_star(painter, size * h * .5)
            painter.restore()
        # Title: letters settle from wide spacing.
        local = t - .5
        if local > 0:
            k = spring(local, .8, 9)
            painter.setOpacity(self.fade * min(1., local / .25))
            font = QFont(['Noto Serif SC', 'Source Han Serif SC', 'Songti SC', 'SimSun', 'serif']); font.setPixelSize(58); font.setWeight(QFont.Weight.Black)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6 + 26 * (1 - k)); painter.setFont(font); painter.setPen(QColor(SILVER))
            painter.drawText(QRectF(0, h * .40, w, 76), Qt.AlignmentFlag.AlignCenter, '照片快筛')
            small = QFont(['Grenze Gotisch', 'Georgia', 'serif']); small.setPixelSize(22); small.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
            painter.setFont(small); painter.setPen(QColor(ICE)); painter.drawText(QRectF(0, h * .40 + 70, w, 30), Qt.AlignmentFlag.AlignCenter, 'Photo Sieve')

    def draw_pyramid(self, painter, width):
        s = width / 240
        top = QPolygonF([QPointF(-100 * s, -40 * s), QPointF(20 * s, -60 * s), QPointF(100 * s, -36 * s), QPointF(-20 * s, -16 * s)])
        left = QPolygonF([QPointF(-100 * s, -40 * s), QPointF(-20 * s, -16 * s), QPointF(-2 * s, 70 * s)])
        right = QPolygonF([QPointF(-20 * s, -16 * s), QPointF(100 * s, -36 * s), QPointF(-2 * s, 70 * s)])
        for poly, colour in ((top, '#D9DEE6'), (left, LAVA), (right, WINE)):
            painter.setBrush(QColor(colour)); painter.drawPolygon(poly)

    def draw_star(self, painter, r):
        path = QPainterPath(QPointF(0, -r))
        for a, b in (((r * .09, -r * .09), (r, 0)), ((r * .09, r * .09), (0, r)), ((-r * .09, r * .09), (-r, 0)), ((-r * .09, -r * .09), (0, -r))):
            path.quadTo(QPointF(*a), QPointF(*b))
        painter.setBrush(QColor(SILVER)); painter.drawPath(path)


def play(application, settings, build):
    """Run build() (which returns the shown main window) under the opening animation."""
    if not enabled(settings):
        return build()
    splash = Splash(); splash.show(); splash.raise_()
    started = time.perf_counter()
    while not splash.skipped:
        splash.t = time.perf_counter() - started
        splash.update(); application.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 12)
        if splash.t >= INTRO:
            break
        time.sleep(.004)
    splash.t = max(splash.t, INTRO + .4); splash.repaint()     # the finished frame holds while the window is built
    window = build()
    splash.raise_(); fade_from = time.perf_counter()
    while not splash.skipped:
        p = (time.perf_counter() - fade_from) / OUTRO
        if p >= 1:
            break
        splash.fade = 1 - p * p; splash.update(); application.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 12); time.sleep(.004)
    splash.close()
    return window
