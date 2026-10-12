"""Design tokens, stylesheet and motion curves (Apple dark system colours)."""
import time
import os
import ctypes
import traceback

from PySide6.QtCore import QEasingCurve, QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from shiboken6 import isValid

BG = '#000000'
ELEVATED = '#1C1C1E'
ELEVATED2 = '#2C2C2E'
ELEVATED3 = '#3A3A3C'
SEPARATOR = '#38383A'
LABEL = '#F5F5F7'
SECONDARY = '#98989D'
TERTIARY = '#636366'
BLUE = '#0A84FF'
BLUE_HOVER = '#409CFF'
BLUE_PRESSED = '#0066CC'
GREEN = '#30D158'
RED = '#FF453A'
FONT = '"SF Pro Text","PingFang SC","Segoe UI Variable Text","Segoe UI","Microsoft YaHei UI","Noto Sans CJK SC",sans-serif'

# Shared interaction tokens, with native Windows accessibility preferences.
MOTION_FAST = 160
MOTION_NORMAL = 240
HOVER_SCALE = .025
PRESS_SCALE = .98
REDUCED_MOTION = False
REDUCED_TRANSPARENCY = False
HIGH_CONTRAST = False


def accessibility_defaults():
    motion = transparency = contrast = False
    if os.name == 'nt':
        try:
            enabled = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(0x1042,0,ctypes.byref(enabled),0):motion = not bool(enabled.value)
            class HighContrast(ctypes.Structure):
                _fields_ = [('cbSize',ctypes.c_uint),('dwFlags',ctypes.c_uint),('scheme',ctypes.c_wchar_p)]
            value = HighContrast(); value.cbSize = ctypes.sizeof(value)
            if ctypes.windll.user32.SystemParametersInfoW(0x42,value.cbSize,ctypes.byref(value),0):contrast = bool(value.dwFlags & 1)
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                transparency = not bool(winreg.QueryValueEx(key,'EnableTransparency')[0])
        except (OSError,AttributeError):pass
    return motion, transparency, contrast


def configure_accessibility(settings):
    global REDUCED_MOTION, REDUCED_TRANSPARENCY, HIGH_CONTRAST
    motion, transparency, HIGH_CONTRAST = accessibility_defaults()
    REDUCED_MOTION = motion or settings.value('reduced_motion',False,type=bool)
    REDUCED_TRANSPARENCY = transparency or HIGH_CONTRAST or settings.value('reduced_transparency',False,type=bool)


STYLE = f'''
QWidget {{ background:{BG}; color:{LABEL}; font-family:{FONT}; font-size:14px; }}
QLabel {{ background:transparent; }}
QLabel#title {{ font-size:17px; font-weight:600; }}
QLabel#subtitle, QLabel#caption {{ color:{SECONDARY}; font-size:12px; }}
QLabel#name {{ font-size:13px; font-weight:500; }}
QLabel#hero {{ font-size:34px; font-weight:600; }}
QLabel#lead {{ color:{SECONDARY}; font-size:17px; }}
QLabel#heading {{ font-size:22px; font-weight:600; }}
QPushButton {{ background:{ELEVATED2}; color:{LABEL}; border:0; border-radius:15px; padding:6px 16px; font-size:13px; min-height:18px; }}
QPushButton:hover {{ background:{ELEVATED3}; }}
QPushButton:pressed {{ background:#48484A; }}
QPushButton:disabled {{ color:{TERTIARY}; background:{ELEVATED}; }}
QPushButton#primary {{ background:{BLUE}; color:#FFFFFF; }}
QPushButton#primary:hover {{ background:{BLUE_HOVER}; }}
QPushButton#primary:pressed {{ background:{BLUE_PRESSED}; }}
QPushButton#primary:disabled {{ background:#0A3D73; color:#8FA9C8; }}
QPushButton[large="true"] {{ border-radius:22px; padding:12px 28px; font-size:17px; }}
QPushButton#icon {{ padding:6px 0; min-width:30px; max-width:30px; font-size:15px; }}
QPushButton#plain {{ background:transparent; border-radius:17px; padding:8px 18px; font-size:14px; }}
QPushButton#plain:hover {{ background:{ELEVATED3}; }}
QPushButton#plain:disabled {{ color:{TERTIARY}; background:transparent; }}
QPushButton#keep {{ background:transparent; color:{GREEN}; border-radius:17px; padding:8px 18px; font-size:14px; font-weight:600; }}
QPushButton#delete {{ background:transparent; color:{RED}; border-radius:17px; padding:8px 18px; font-size:14px; font-weight:600; }}
QPushButton#keep:hover, QPushButton#delete:hover {{ background:{ELEVATED3}; }}
QPushButton#keep:disabled, QPushButton#delete:disabled {{ color:{TERTIARY}; background:transparent; }}
QFrame#actionbar {{ background:transparent; border-radius:22px; }}
QFrame#actionbar QWidget {{ background:transparent; }}
QFrame#divider {{ background:{SEPARATOR}; }}
QFrame#sheet {{ background:{ELEVATED}; border-radius:18px; }}
QGraphicsView {{ background:{BG}; border:0; }}
QListWidget {{ background:transparent; border:0; outline:0; color:{SECONDARY}; font-size:12px; }}
QListWidget::item {{ border-radius:8px; padding:2px; border:2px solid transparent; }}
QListWidget::item:hover {{ background:{ELEVATED}; }}
QListWidget::item:selected {{ background:{ELEVATED2}; border:2px solid {LABEL}; color:{LABEL}; }}
QScrollBar:horizontal {{ height:8px; background:transparent; margin:0; }}
QScrollBar:vertical {{ width:8px; background:transparent; margin:0; }}
QScrollBar::handle {{ background:#48484A; border-radius:4px; min-width:30px; min-height:30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ width:0; height:0; background:transparent; }}
QMenu {{ background:{ELEVATED2}; border:1px solid {SEPARATOR}; border-radius:12px; padding:6px; }}
QMenu::item {{ padding:7px 28px 7px 14px; border-radius:7px; color:{LABEL}; }}
QMenu::item:selected {{ background:{BLUE}; color:#FFFFFF; }}
QMenu::item:disabled {{ color:{SECONDARY}; background:transparent; }}
QMenu::separator {{ height:1px; background:{SEPARATOR}; margin:5px 10px; }}
QMenu::indicator {{ width:14px; height:14px; left:6px; }}
QDialog {{ background:{ELEVATED}; }}
QDialog QWidget {{ background:transparent; }}
QDialog QPushButton {{ background:{ELEVATED3}; }}
QDialog QPushButton#primary {{ background:{BLUE}; }}
QScrollArea {{ border:0; }}
QToolTip {{ background:{ELEVATED2}; color:{LABEL}; border:1px solid {SEPARATOR}; border-radius:6px; padding:4px 8px; }}
QMessageBox {{ background:{ELEVATED}; }}
'''


def bezier(x1, y1, x2, y2):
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1, 1))
    return curve


# apple.com default ease, and the snappy deceleration used by iOS sheets and cards.
EASE = bezier(.25, .1, .25, 1)
SNAPPY = bezier(.32, .72, 0, 1)


class SpringCurve:
    """Critically damped spring: physical deceleration without decorative overshoot."""
    def valueForProgress(self, progress):
        import math
        stiffness = 8.0
        end = 1-(1+stiffness)*math.exp(-stiffness)
        return (1-(1+stiffness*progress)*math.exp(-stiffness*progress))/end


SPRING = SpringCurve()


class Motion(QObject):
    """One animation on the shared FrameClock. It offers the QVariantAnimation calls the app relies on:
    stop() ends it silently, setCurrentTime(duration()) jumps to the end, finished fires on completion."""
    finished = Signal()

    def __init__(self, owner, duration, step, curve, start, end):
        super().__init__(owner)
        self.length = max(1, int(duration)); self.step = step; self.curve = curve
        self.start_value = float(start); self.end_value = float(end); self.began = time.perf_counter(); self.running = True

    def duration(self):
        return self.length

    def apply(self, progress):
        eased = self.curve.valueForProgress(progress)
        self.step(self.start_value + (self.end_value - self.start_value) * eased)

    def advance(self, now):
        progress = min(1.0, (now - self.began) * 1000 / self.length)
        self.apply(progress)
        if progress >= 1.0:
            self.complete()

    def setCurrentTime(self, ms):
        if not self.running:
            return
        if ms >= self.length:
            self.apply(1.0); self.complete()
        else:
            self.began = time.perf_counter() - ms / 1000; self.apply(ms / self.length)

    def stop(self):
        if self.running:
            self.running = False; clock().remove(self); self.deleteLater()   # a stopped motion never restarts

    def complete(self):
        if self.running:
            self.running = False; clock().remove(self); self.finished.emit(); self.deleteLater()


class FrameClock(QObject):
    """One precise timer at the display's refresh rate drives every animation (4 ms on a 240 Hz panel,
    16 ms at 60 Hz), instead of Qt's fixed ~60 Hz animation timer."""
    def __init__(self):
        super().__init__(); self.motions = []
        self.timer = QTimer(self); self.timer.setTimerType(Qt.TimerType.PreciseTimer); self.timer.timeout.connect(self.tick)

    @staticmethod
    def interval(owner=None):
        screen = owner.screen() if hasattr(owner, 'screen') else None
        screen = screen or QGuiApplication.primaryScreen()
        rate = screen.refreshRate() if screen else 60.0
        return max(2, int(1000 / max(30.0, min(rate or 60.0, 500.0))))

    def add(self, motion, owner):
        self.motions.append(motion); interval = self.interval(owner)
        if not self.timer.isActive() or interval < self.timer.interval():
            self.timer.start(interval)

    def remove(self, motion):
        if motion in self.motions:
            self.motions.remove(motion)
        if not self.motions:
            self.timer.stop()

    def tick(self):
        now = time.perf_counter()
        for motion in list(self.motions):
            if not isValid(motion) or not motion.running:
                self.remove(motion); continue
            try:
                motion.advance(now)
            except RuntimeError:   # its widget was destroyed mid-animation
                motion.running = False; self.remove(motion)
            except Exception:   # a failing step ends only its own motion, and finished still lets its owner tidy up
                traceback.print_exc(); motion.complete()


_clock = None


def clock():
    global _clock
    if _clock is None:
        _clock = FrameClock()
    return _clock


def animate(owner, duration, step, curve=SNAPPY, done=None, start=0.0, end=1.0):
    """Drive step(value) from start to end at the display's refresh rate; parented to owner, never collected early."""
    motion = Motion(owner, duration, step, curve, start, end)
    if done:
        motion.finished.connect(done)
    if REDUCED_MOTION:
        motion.apply(1.0)
        QTimer.singleShot(0, motion.complete)
    else:
        motion.apply(0.0)
        clock().add(motion, owner)
    return motion
