"""Design tokens, stylesheet and motion curves (Apple dark system colours)."""
from PySide6.QtCore import QEasingCurve, QPointF, QVariantAnimation

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
QFrame#actionbar {{ background:{ELEVATED}; border-radius:22px; }}
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


def animate(owner, duration, step, curve=SNAPPY, done=None, start=0.0, end=1.0):
    """Drive step(value) from start to end; the animation is parented to owner so it is never collected early."""
    animation = QVariantAnimation(owner)
    animation.setStartValue(float(start))
    animation.setEndValue(float(end))
    animation.setDuration(duration)
    animation.setEasingCurve(curve)
    animation.valueChanged.connect(step)
    if done:
        animation.finished.connect(done)
    animation.finished.connect(animation.deleteLater)
    animation.start()
    return animation
