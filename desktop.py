"""Native Qt desktop photo review. No webview, browser, HTTP server or ports."""
from __future__ import annotations
import argparse
from collections import OrderedDict, deque
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import hashlib
import math
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import time
import traceback
import subprocess

from PIL import Image

# Worker threads run some Python between C calls; a short switch interval keeps the interface thread's
# waits for the GIL near 1 ms instead of 5 ms while previews decode in the background.
sys.setswitchinterval(.001)

from PySide6.QtCore import Qt, Signal, Slot, QEvent, QObject, QRunnable, QThread, QThreadPool, QTimer, QSize, QUrl, QLockFile, QSettings, QPoint, QRect, QDate
from PySide6.QtGui import QImage, QPainter, QPixmap, QIcon, QShortcut, QKeySequence, QDesktopServices, QColor, QAction
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QListWidget, QListWidgetItem, QAbstractItemView, QStackedWidget, QDialog, QScrollArea,
    QFrame, QMenu, QSizePolicy, QSpinBox, QFormLayout, QCheckBox, QSizeGrip, QComboBox, QDateEdit)
from app import Library, DECODE_SLOTS, build_preview, build_full_image, decoder_init
import keys
import crashlog
from ui_shell import TitleBar, QDialog, QMessageBox, QFileDialog, SettingsPanel, app_icon, CacheSpinBox
import machine
from cache_policy import prune_workspaces, expire_inactive_workspaces
from workspaces import open_workspace
from filmstrip import Filmstrip
from photo_grid import PhotoGrid
from rating_page import RatingPage
from window_frame import WindowFrame
from version import APP_VERSION
from update_ui import UpdateController
import theme
from apple_ui import AppleButton, GlassDock, GlassPopover
from theme import STYLE, animate
from widgets import PhotoView, Elided, Ghost, Toast, Segmented, ProgressLine, WelcomePage, ImportPage

PAGE = 1200         # rows per queue page; ids are indexed, so a page costs a few milliseconds
REFILL = 800        # fetch the next page while this many photos remain
WARM = 1200         # further photos whose disk preview is prepared in the background, within the cache budget
BEHIND = 20         # previews kept for photos already passed, for ← and Ctrl+Z
# Decoded, pane-sized previews kept ready ahead: 100 to 200 depending on pane size and memory (machine.Profile.buffer).
BURST = .22         # actions closer together than this skip the transition animation
STRIP = 16
ROLE = Qt.ItemDataRole.UserRole


def qimage(im):
    """An opaque QImage over a pane-sized image's pixels, converted by Pillow with the GIL released.
    RGB32 needs an opaque 0xFF fourth byte (Pillow's BGRX pads with 0, which composites as transparent).
    No copy here: the bytes travel with the image and QPixmap.fromImage copies once on the interface thread."""
    data = im.convert('RGBA').tobytes('raw', 'BGRA')
    return QImage(data, im.width, im.height, im.width * 4, QImage.Format.Format_RGB32), data


def full_qimage(im, rows=128):
    """The original for 100% zoom. QImage.fromData, or converting the whole image at once, would hold the GIL for over
    half a second at 61 MP and freeze the interface. Pillow decodes with the GIL released; then each strip is copied
    out in Pillow's own RGBX layout and painted in by Qt, so the interface thread runs in between (a few ms at most)."""
    if im.mode != 'RGB': im = im.convert('RGB')
    width, height = im.size
    image = QImage(width, height, QImage.Format.Format_RGB32); painter = QPainter(image)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    for top in range(0, height, rows):
        strip = im.crop((0, top, width, min(height, top + rows))); pixels = strip.tobytes('raw', 'RGBX')
        painter.drawImage(0, top, QImage(pixels, width, strip.height, width * 4, QImage.Format.Format_RGBX8888))
    painter.end()
    return image


def display_image(image, width, height, thumb):
    """Worker-side preview sized for the pane. Pillow releases the GIL while it decodes and resamples,
    so the interface thread keeps running; Qt's scaler would hold the GIL throughout."""
    source = image.size; image.draft('RGB', (width, height))
    if image.mode != 'RGB': image = image.convert('RGB')
    def fit(im, w, h):
        scale = min(w / im.width, h / im.height)
        return im if scale >= 1 else im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.Resampling.BILINEAR, reducing_gap=2.0)
    shown = fit(image, width, height)
    return qimage(shown), qimage(fit(shown, *thumb)), source


class Decoders:
    """Originals decode in separate processes. They share no GIL with the interface, so it stays smooth while
    keys are held, and a decoder crash on a damaged file ends only that process, not the app.
    If processes cannot start, the app shows an error and never retries native decoding in its own process."""
    def __init__(self, workers=DECODE_SLOTS):
        self.workers = workers; self.pool = self.gate = None; self.lock = threading.Lock(); self.working = False; self.unavailable = False
        self.context = multiprocessing.get_context('spawn')

    def __call__(self, *job):
        return self.run(build_preview,*job)

    def full_image(self,source,rotational=False):
        return self.run(build_full_image,source,rotational)

    def run(self,function,*job):
        if self.unavailable:raise ValueError('安全解码进程不可用，请查看运行日志后重新启动应用')
        pool = None
        try:
            with self.lock:
                if self.pool is None:
                    # Shared by every decoder process: at most two read a spinning disk at once. A process that dies
                    # inside it never gives its slot back (and a broken pool terminates the rest), so each pool gets
                    # a fresh one; otherwise the next pool would wait on the old one forever.
                    self.gate = self.context.BoundedSemaphore(2)
                    self.pool = ProcessPoolExecutor(self.workers, self.context, decoder_init, (self.gate,))
                pool = self.pool
            result=pool.submit(function, *job).result(); self.working = True
            return result if function is build_full_image else True
        except ValueError:
            raise   # this photo could not be decoded; the reason is shown on it
        except BrokenProcessPool:
            crashlog.LOGGER.exception('Isolated decoder exited: %s',job[0] if job else '')
            with self.lock:
                if self.pool is pool: self.pool = None
            # Retrying a native failure in the APP process defeats the isolation,
            # especially when the very first RAW crashes before working becomes True.
            raise ValueError('照片解码进程意外退出，已隔离此照片；请查看日志') from None
        except Exception as exc:
            crashlog.LOGGER.exception('Could not run isolated decoder: %s',job[0] if job else '')
            self.unavailable = True
            raise ValueError(f'无法运行安全解码进程：{exc}') from None

    def close(self):
        with self.lock:
            pool, self.pool = self.pool, None
        # Wait for the processes to exit, so an update never finds program files still in use.
        if pool: pool.shutdown(wait=True, cancel_futures=True)


class Result(QObject):
    ok = Signal(object)
    fail = Signal(str)


class Completion(QObject):
    """An explicit GUI-thread receiver; workers never call UI closures directly."""
    def __init__(self,window,ok,fail):
        super().__init__(window);self.window=window;self.ok=ok;self.fail=fail;self.task=None

    @Slot(object)
    def succeeded(self,value):
        try:
            if not self.window.closed:self.ok(value)
        finally:self.finish()

    @Slot(str)
    def failed(self,message):
        try:
            if not self.window.closed:(self.fail or self.window.error)(message)
        finally:self.finish()

    def finish(self):
        self.window.tasks.discard(self.task)
        self.task=None;self.ok=self.fail=None
        self.deleteLater()


class Work(QRunnable):
    def __init__(self, fn):
        super().__init__(); self.fn = fn; self.signals = Result()

    def run(self):
        try: self.signals.ok.emit(self.fn())
        except Exception as e:
            crashlog.LOGGER.exception('Background task failed')
            self.signals.fail.emit(str(getattr(e, 'detail', str(e))))


class Window(QMainWindow):
    VIEWS = [('pending', '未筛选'), ('keep', '保留'), ('deleted', '已删除')]
    cache_changed=Signal(object)
    fps_label = None   # Qt sends events before __init__ finishes

    def __init__(self, library, check_updates=True, decoders=None, start_empty=False, prompt_on_close=False, data_root=None):
        import gui_runtime
        gui_runtime.install()
        super().__init__(); self.library = library; self.decoders = decoders; library.offload = decoders; self.setWindowTitle('照片快筛'); self.resize(1480, 960); self.setAcceptDrops(True)
        self.session_active = not start_empty; self.prompt_on_close = prompt_on_close; self.close_choice = None
        library.start_session()
        self.data_root=Path(data_root) if data_root else Path.home()/'.photo-sieve'
        self.managed_workspaces=data_root is not None
        self.previous_library=QSettings('PhotoSieve','PhotoSieve').value('library','')
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint); self.setWindowIcon(app_icon())
        settings=QSettings('PhotoSieve','PhotoSieve')
        self.rating_active=False
        self.actionbar_visible=settings.value('show_actionbar',True,type=bool)
        self.preload_count=max(0,min(5000,settings.value('preload_count',0,type=int)))
        self.warm_count=max(0,min(50000,settings.value('warm_count',1200,type=int)))
        self.cache_clearing=False;self.importing=False;self.import_revision=None
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(2)
        self.op_pool = QThreadPool(self); self.op_pool.setMaxThreadCount(1)   # file and record changes run strictly in order
        # Image threads mostly wait for decoder processes; only a few at a time do interface-side decoding.
        self.workers = decoders.workers if decoders else DECODE_SLOTS; self.in_flight = self.workers + 4
        self.image_pool = QThreadPool(self); self.image_pool.setMaxThreadCount(self.in_flight)
        self.display_gate = threading.Semaphore(machine.PROFILE.interface_threads())
        self.ahead_target = 24; self.cache_limit = 24 + BEHIND; self.frames = 0
        self.thumb_pool = QThreadPool(self); self.thumb_pool.setMaxThreadCount(2)
        self.detail_pool = QThreadPool(self); self.detail_pool.setMaxThreadCount(1)
        for pool in (self.image_pool, self.thumb_pool, self.detail_pool): pool.setThreadPriority(QThread.Priority.LowPriority)   # the interface thread wins any CPU contention
        self.tasks = set(); self.cache = OrderedDict(); self.cache_size = {}; self.sources = {}; self.loading = set(); self.image_errors = {}
        self.thumb_cache = OrderedDict(); self.warming = set(); self.warmed = set(); self.thumb_wanted = deque(); self.thumb_loading = set()
        self.sort_order='file';self.date_start=None;self.date_end=None;self.strip_revision=0;self.strip_loading=set()
        self.queue = []; self.view = 'pending'; self.busy = False; self.fetching = False; self.state_pending = False; self.state_data = None
        self.counts = {}; self.inflight = set(); self.pending_ops = 0; self.restore_running=False; self.queue_end = False; self.generation = 0; self.focus_loading = set()
        self.was_running = False; self.closed = False; self.closing = False; self.display_size = None; self.motions = []; self.last_motion = 0.0
        self.last_nudge = 0.0; self.delete_streak = 0; self.deleted_rows = []; self.deleted_items = {}; self.deleted_more = False
        self.deleted_thumb_cache=OrderedDict(); self.deleted_errors=set()
        self.kept_thumb_cache=OrderedDict();self.kept_loading={};self.kept_revision=0
        self.cursor_id = 0; self.cursor_timer = QTimer(self); self.cursor_timer.setSingleShot(True); self.cursor_timer.timeout.connect(self.save_cursor)
        self.state_timer = QTimer(self); self.state_timer.setSingleShot(True); self.state_timer.timeout.connect(self.poll)
        self.chrome_timer = QTimer(self); self.chrome_timer.setSingleShot(True); self.chrome_timer.timeout.connect(self.paint_chrome); self.burst = False
        self.resize_timer = QTimer(self); self.resize_timer.setSingleShot(True); self.resize_timer.timeout.connect(self.resized)
        theme.configure_accessibility(QSettings('PhotoSieve', 'PhotoSieve'))
        self.uninstall_requested=False
        self.build(); self.window_frame=WindowFrame(self,self.window_chrome); self.update_actions()
        self.updates = UpdateController(self, check_on_start=check_updates); self.updates.report_previous()
        self.shortcuts = []; self.apply_shortcuts(keys.load(QSettings('PhotoSieve', 'PhotoSieve')))
        self.timer = QTimer(self); self.timer.timeout.connect(self.poll); self.poll()
        self.maintenance_running=False; self.maintenance_timer=QTimer(self)
        self.maintenance_timer.timeout.connect(self.maintain_cache); self.maintenance_timer.start(30000)
        self.cache_total=0; self.cache_changed.connect(self.cache_growth)
        self.library.cache_changed=self.cache_changed.emit; QTimer.singleShot(0,self.maintain_cache)

    def cache_growth(self,delta):
        self.cache_total=max(0,self.cache_total+delta)
        if self.cache_total>=10*1024**3: self.maintain_cache()

    def maintain_cache(self):
        if self.maintenance_running or self.cache_clearing or self.closed: return
        self.maintenance_running=True; library=self.library
        def work():
            result=prune_workspaces(self.data_root,library.cache,cache_lock=library.cache_lock)
            library.recount_cache(); return result
        def finished(result):
            self.maintenance_running=False; self.cache_total=result[1]
            visible={p['id'] for p in self.queue[:self.ahead_target]}
            for pid in list(self.cache):
                if pid not in visible and pid not in self.loading:
                    self.cache.pop(pid,None); self.cache_size.pop(pid,None); self.sources.pop(pid,None); self.thumb_cache.pop(pid,None)
            for pid in list(self.thumb_cache):
                if pid not in visible: self.thumb_cache.pop(pid,None)
        self.submit(work,finished,lambda _:setattr(self,'maintenance_running',False))

    def apply_shortcuts(self, chosen):
        """Bind the user's keys (see keys.ACTIONS); held keys follow the keyboard repeat rate."""
        for shortcut in self.shortcuts: shortcut.setEnabled(False); shortcut.deleteLater()
        self.shortcuts = []; self.chosen_keys = chosen
        handlers = {'keep': lambda: self.act('keep'), 'delete': lambda: self.act('delete'), 'next': self.next_photo,
                    'restore': lambda: self.restore(), 'undo': lambda: self.restore(general=True), 'zoom': self.focus_left, 'unzoom': self.reset_zoom}
        for action, _, _, repeat in keys.ACTIONS:
            for key in chosen[action]:
                if not key: continue
                shortcut = QShortcut(QKeySequence(key), self); shortcut.setAutoRepeat(repeat); shortcut.activated.connect(handlers[action]); self.shortcuts.append(shortcut)
        for button, action, text in ((self.keep_button, 'keep', '保留'), (self.delete_button, 'delete', '删除'),
                                     (self.restore_button, 'restore', '恢复'), (self.next_button, 'next', '下一张')):
            button.setText(text); button.symbol=action; button.variant='dock'
            button.set_shortcut(keys.first(chosen, action), ' / '.join(keys.label(key) for key in chosen[action] if key))
        self.sync_shortcut_mode()
        hint = ('或按 ' + keys.first(chosen, 'restore') + ' ') if keys.first(chosen, 'restore') else ''
        self.deleted_note.setText('照片暂存在 .photo-sieve-trash 中。双击' + hint + '恢复；彻底删除后无法撤回。')

    def key(self, action):
        return keys.first(self.chosen_keys, action) or ('再次单击图片' if action=='unzoom' else '恢复按钮')

    def edit_shortcuts(self):
        if self.rating_active:self.rating_page.edit_keys();return
        self.updates.touch()
        dialog = keys.ShortcutDialog(self, self.chosen_keys)
        def saved(chosen):
            keys.save(QSettings('PhotoSieve', 'PhotoSieve'), chosen); self.apply_shortcuts(chosen); self.toast.show_message('快捷键已更新', 'info')
        dialog.saved.connect(saved); dialog.exec()

    def sync_shortcut_mode(self):
        menu_open=bool(getattr(self,'more_panel',None) and self.more_panel.isVisible())
        for shortcut in getattr(self,'shortcuts',[]):shortcut.setEnabled(not self.rating_active and not menu_open)
        for shortcut in self.rating_page.shortcut_objects:shortcut.setEnabled(self.rating_active and not menu_open)

    def toggle_rating(self):
        if self.busy or self.pending_ops or self.job_running():return
        if getattr(self,'more_panel',None):self.more_panel.hide()
        self.rating_active=not self.rating_active;self.segments.setVisible(not self.rating_active)
        self.rating_button.setText('返回照片筛选' if self.rating_active else '照片分级')
        if self.rating_active:
            self.generation+=1;self.fetching=False;self.reset_zoom();self.stack.setCurrentWidget(self.rating_page);self.rating_page.activate()
        else:
            self.rating_page.deactivate();self.stack.setCurrentIndex(2 if not self.session_active else 1 if self.view=='deleted' else 4 if self.view=='keep' else 0)
            if self.session_active:self.load(anchor=max(0,self.cursor_id-1))
        self.sync_shortcut_mode();self.update_actions();self.place_overlays()

    # ---- infrastructure -------------------------------------------------
    def submit(self, fn, ok=lambda _: None, fail=None, pool=None, priority=0):
        if self.closed: return   # late timers must not start work after the pools were drained
        task = Work(fn); self.tasks.add(task)
        completion=Completion(self,ok,fail);completion.task=task
        task.signals.ok.connect(completion.succeeded,Qt.ConnectionType.QueuedConnection)
        task.signals.fail.connect(completion.failed,Qt.ConnectionType.QueuedConnection)
        (pool or self.pool).start(task, priority)

    def op(self, fn, done, failed):
        self.pending_ops += 1
        def ok(value):
            self.pending_ops -= 1; done(value); self.refresh_state(400)
        def bad(message):
            self.pending_ops -= 1; failed(message); self.refresh_state(0)
        self.submit(fn, ok, bad, self.op_pool)

    def error(self, message):
        self.toast.show_message(message, 'error', 4200)

    def button(self, text, fn, name=None):
        b = AppleButton(text, variant='primary' if name=='primary' else 'neutral'); b.clicked.connect(fn)
        if name: b.setObjectName(name)
        return b

    # ---- layout ---------------------------------------------------------
    def build(self):
        root = QWidget(); self.setCentralWidget(root); outer = QVBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        self.window_chrome=TitleBar(self,'照片快筛',main=True); outer.addWidget(self.window_chrome)
        bar = QWidget(); bar.setObjectName('workspaceToolbar'); bar.setFixedHeight(64); top = QHBoxLayout(bar); top.setContentsMargins(24, 16, 24, 16); top.setSpacing(16)
        left = QWidget(); titles = QVBoxLayout(left); titles.setContentsMargins(0, 0, 0, 0); titles.setSpacing(1)
        left.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title = Elided('title', Qt.TextElideMode.ElideRight); self.title.set_text('照片快筛'); self.subtitle = Elided('subtitle')
        titles.addWidget(self.title); titles.addWidget(self.subtitle); top.addWidget(left, 1)
        self.segments = Segmented(self.VIEWS); self.segments.changed.connect(self.switch); self.segments.set_counts({}); top.addWidget(self.segments)
        right = QWidget(); right.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        tools = QHBoxLayout(right); tools.setContentsMargins(0, 0, 0, 0); tools.setSpacing(8); tools.addStretch()
        self.rating_button=self.button('照片分级',self.toggle_rating);tools.addWidget(self.rating_button)
        self.open_button = self.button('打开文件夹…', self.choose_source); self.open_button.symbol='folder'; tools.addWidget(self.open_button)
        self.export_button = self.button('导出', self.export_dialog, 'primary'); self.export_button.symbol='export'; tools.addWidget(self.export_button)
        self.more_button = self.button('', self.show_menu, 'icon'); self.more_button.symbol='more'; self.more_button.setAccessibleName('更多选项'); tools.addWidget(self.more_button)
        for control in (self.open_button, self.export_button, self.more_button): control.setFixedHeight(32)
        self.badge = QLabel(self.more_button); self.badge.setFixedSize(8, 8); self.badge.move(22, 0)
        self.badge.setStyleSheet(f'background:{theme.BLUE}; border-radius:4px;'); self.badge.hide()
        top.addWidget(right, 1); outer.addWidget(bar)
        self.progress = ProgressLine(); outer.addWidget(self.progress)
        self.stack = QStackedWidget(); outer.addWidget(self.stack, 1)

        review = QWidget(); column = QVBoxLayout(review); column.setContentsMargins(24, 8, 24, 0); column.setSpacing(0)
        self.keep_back_button=self.button('← 返回保留列表',self.back_to_kept);self.keep_back_button.hide();column.addWidget(self.keep_back_button,0,Qt.AlignmentFlag.AlignLeft)
        pair = QHBoxLayout(); pair.setSpacing(16); self.photos = []; self.names = []; self.details = []
        for index in range(2):
            pane = QVBoxLayout(); pane.setSpacing(4); pane.setContentsMargins(0, 0, 0, 0)
            role=QLabel('左图 · 当前筛选（保留 / 删除操作此图）' if index==0 else '右图 · 下一张对比')
            role.setObjectName('caption'); role.setAlignment(Qt.AlignmentFlag.AlignCenter); role.setFixedHeight(22); pane.addWidget(role)
            photo = PhotoView()
            photo.clicked.connect(self.focus); photo.next_requested.connect(self.next_photo); photo.escape_requested.connect(self.reset_zoom)
            photo.resized.connect(lambda: self.resize_timer.start(160)); photo.zoom_changed.connect(lambda index=index: self.zoom_caption(index)); pane.addWidget(photo, 1)
            name = Elided('name'); name.setFixedHeight(24); name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            detail = Elided('caption'); detail.setFixedHeight(20); detail.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            pane.addWidget(name); pane.addWidget(detail); pair.addLayout(pane, 1)
            self.photos.append(photo); self.names.append(name); self.details.append(detail)
        column.addLayout(pair, 1)
        self.action_row=QWidget(); actions = QHBoxLayout(self.action_row); actions.setContentsMargins(0, 10, 0, 8); actions.addStretch()
        self.actionbar = GlassDock(); self.actionbar.setObjectName('actionbar'); row = QHBoxLayout(self.actionbar); row.setContentsMargins(6, 2, 6, 2); row.setSpacing(4)
        self.keep_button = self.button('↑  保留', lambda: self.act('keep'), 'keep'); self.delete_button = self.button('↓  删除', lambda: self.act('delete'), 'delete')
        self.restore_button = self.button('←  恢复', lambda: self.restore(), 'plain'); self.next_button = self.button('→  下一张', self.next_photo, 'plain')
        divider = QFrame(); divider.setObjectName('divider'); divider.setFixedSize(1, 20)
        for widget in (self.keep_button, self.delete_button, divider, self.restore_button, self.next_button): row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        for control in (self.keep_button, self.delete_button, self.restore_button, self.next_button): control.setFixedHeight(30)
        self.actionbar.register((self.keep_button,self.delete_button,self.restore_button,self.next_button))
        actions.addWidget(self.actionbar); actions.addStretch(); column.addWidget(self.action_row); self.action_row.setVisible(self.actionbar_visible)
        self.strip = Filmstrip(); self.strip.setViewMode(QListWidget.ViewMode.IconMode); self.strip.setFlow(QListWidget.Flow.LeftToRight)
        self.strip.setWrapping(False); self.strip.setMovement(QListWidget.Movement.Static); self.strip.setUniformItemSizes(True)
        self.strip.setIconSize(QSize(104, 68)); self.strip.setGridSize(QSize(116, 80)); self.strip.setFixedHeight(100)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn); self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.strip.enable_mixed_layout()
        self.strip.setFocusPolicy(Qt.FocusPolicy.NoFocus); self.strip.itemClicked.connect(self.strip_select); column.addWidget(self.strip)
        self.strip.icon_for=lambda pid:QIcon(self.thumbnail(pid)) if self.thumbnail(pid) is not None else self.placeholder
        self.strip.horizontalScrollBar().valueChanged.connect(self.load_strip_thumbnails)
        self.strip_note=QLabel('全部未筛选照片 · 拖动下方滚动条浏览');self.strip_note.setObjectName('caption');column.addWidget(self.strip_note)
        column.addSpacing(8); self.stack.addWidget(review)
        placeholder = QPixmap(104, 68); placeholder.fill(QColor(theme.ELEVATED)); self.placeholder = QIcon(placeholder)
        # The grid sizes every cell from its first item, so its placeholder must be full size.
        tile = QPixmap(176, 118); tile.fill(QColor(theme.ELEVATED)); self.tile = QIcon(tile)

        deleted = QWidget(); grid_layout = QVBoxLayout(deleted); grid_layout.setContentsMargins(24, 8, 24, 16); grid_layout.setSpacing(16)
        self.deleted_grid = QListWidget(); self.deleted_grid.setViewMode(QListWidget.ViewMode.IconMode); self.deleted_grid.setWrapping(True)
        self.deleted_grid.setResizeMode(QListWidget.ResizeMode.Adjust); self.deleted_grid.setMovement(QListWidget.Movement.Static)
        self.deleted_grid.setUniformItemSizes(True); self.deleted_grid.setIconSize(QSize(176, 118)); self.deleted_grid.setGridSize(QSize(196, 158))
        self.deleted_grid.setTextElideMode(Qt.TextElideMode.ElideMiddle); self.deleted_grid.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.deleted_grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.deleted_grid.itemDoubleClicked.connect(lambda item: self.restore(photo_id=item.data(ROLE)))
        self.deleted_grid.itemSelectionChanged.connect(self.deleted_selection_changed)
        self.deleted_grid.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.deleted_grid.verticalScrollBar().valueChanged.connect(self.maybe_more_deleted); grid_layout.addWidget(self.deleted_grid, 1)
        footer = QHBoxLayout(); self.deleted_note = QLabel('删除的照片在原文件夹的 .photo-sieve-trash 中，仍占用空间。双击或按 ← 恢复。')
        self.deleted_note.setObjectName('caption'); footer.addWidget(self.deleted_note, 1)
        self.select_all_deleted = self.button('全选', self.select_deleted); footer.addWidget(self.select_all_deleted)
        self.purge_selected = self.button('彻底删除所选', self.purge_selection, 'delete'); footer.addWidget(self.purge_selected)
        self.restore_selected = self.button('恢复所选', self.restore_selection, 'primary'); footer.addWidget(self.restore_selected); grid_layout.addLayout(footer)
        self.stack.addWidget(deleted)

        empty = WelcomePage(); hero = QVBoxLayout(empty); hero.setContentsMargins(24,24,24,24); hero.setSpacing(12); hero.addStretch()
        lead = QLabel('选择照片文件夹开始筛选 · 也可以拖入文件夹'); lead.setObjectName('caption')
        start = self.button('打开文件夹…', self.choose_source, 'primary'); start.setProperty('large', True)
        lead.setAlignment(Qt.AlignmentFlag.AlignHCenter); hero.addWidget(lead)
        hero.addWidget(start, 0, Qt.AlignmentFlag.AlignHCenter); self.stack.addWidget(empty)
        self.import_page=ImportPage();self.stack.addWidget(self.import_page)
        kept=QWidget();kept_layout=QVBoxLayout(kept);kept_layout.setContentsMargins(24,8,24,16);kept_layout.setSpacing(16)
        self.kept_grid=PhotoGrid();self.kept_grid.icon_for=lambda pid:QIcon(self.kept_thumb_cache[pid]) if pid in self.kept_thumb_cache else self.tile
        self.kept_grid.itemClicked.connect(self.open_kept_item);self.kept_grid.verticalScrollBar().valueChanged.connect(self.queue_kept_thumbnails)
        kept_layout.addWidget(self.kept_grid,1)
        self.kept_note=QLabel('已保留照片 · 单击缩略图查看大图，右上角可导出全部保留照片');self.kept_note.setObjectName('caption');kept_layout.addWidget(self.kept_note)
        self.stack.addWidget(kept)

        self.rating_page=RatingPage(self);self.stack.addWidget(self.rating_page)

        self.toast = Toast(root)
        self.toast.positioner=self.place_overlays
        grip_row=QHBoxLayout(); grip_row.setContentsMargins(0,0,2,2); grip_row.addStretch(); grip_row.addWidget(QSizeGrip(self)); outer.addLayout(grip_row)

    def show_menu(self):
        self.updates.touch()
        if getattr(self,'more_panel',None) and self.more_panel.isVisible(): self.more_panel.close(); return
        if getattr(self,'more_panel',None): self.more_panel.deleteLater()
        menu = self.more_panel = SettingsPanel(self,self.more_button)
        menu.addSection('照片管理', '整理当前文件夹中的照片')
        ordering=menu.addAction('排序与日期筛选…');ordering.setEnabled(self.session_active and not self.busy and not self.rating_active);ordering.triggered.connect(self.edit_ordering)
        rescan=menu.addAction('重新扫描当前文件夹');root=self.state_data and self.state_data.get('root')
        rescan.setEnabled(bool(root) and not self.busy and not self.pending_ops);rescan.triggered.connect(lambda:self.scan(root))
        undo=menu.addAction('撤销评级' if self.rating_active else '撤回上一步');undo.setEnabled(self.rating_page.undo_button.isEnabled() if self.rating_active else self.session_active and (bool(self.state_data and self.state_data.get('can_undo')) or self.pending_ops>0))
        undo.triggered.connect(lambda:self.rating_page.undo() if self.rating_active else self.restore(general=True))
        menu.addSection('界面与操作', '调整显示方式和键盘操作')
        dock=QAction('显示底部操作栏',menu);dock.setCheckable(True);dock.setChecked(self.actionbar_visible)
        dock.toggled.connect(self.set_actionbar_visible);dock.setEnabled(not self.rating_active);menu.addAction(dock)
        menu.addAction('快捷键…').triggered.connect(self.edit_shortcuts)
        fps=QAction('显示帧率',menu);fps.setCheckable(True);fps.setChecked(self.fps_label is not None);fps.toggled.connect(self.show_fps);menu.addAction(fps)
        reduced=QAction('减少动态效果',menu);reduced.setCheckable(True);reduced.setChecked(theme.REDUCED_MOTION)
        reduced.toggled.connect(lambda enabled:self.set_accessibility('reduced_motion',enabled));menu.addAction(reduced)
        menu.addSection('性能与缓存', '管理预加载、缓存空间和渲染方式')
        menu.addAction('预加载与缓存…').triggered.connect(self.cache_dialog)
        gpu=QAction('GPU 合成（重启后生效）',menu);gpu.setCheckable(True);gpu.setEnabled(os.name=='nt')
        gpu.setChecked(QSettings('PhotoSieve','PhotoSieve').value('gpu_compositing',False,type=bool));gpu.toggled.connect(self.set_gpu);menu.addAction(gpu)
        menu.addStatus(self.machine_status())
        menu.addSection('更新与诊断', '获取新版本或查看运行日志')
        menu.addAction('检查更新').triggered.connect(lambda:self.updates.check())
        automatic=QAction('启动时检查更新',menu);automatic.setCheckable(True);automatic.setChecked(self.updates.auto_enabled())
        automatic.toggled.connect(self.updates.set_automatic);menu.addAction(automatic)
        if self.updates.staged:menu.addAction('立即安装更新并重启').triggered.connect(lambda:self.updates.install())
        menu.addStatus(self.updates.status or '版本 '+APP_VERSION)
        menu.addAction('打开日志文件夹').triggered.connect(self.open_logs)
        menu.exec()

    def set_actionbar_visible(self, enabled):
        self.actionbar_visible=bool(enabled)
        settings=QSettings('PhotoSieve','PhotoSieve');settings.setValue('show_actionbar',self.actionbar_visible);settings.sync()
        self.action_row.setVisible(self.actionbar_visible)
        self.centralWidget().layout().activate()
        self.place_overlays()
        QTimer.singleShot(0,self.update_display_size)

    def set_accessibility(self, name, enabled):
        settings=QSettings('PhotoSieve','PhotoSieve');settings.setValue(name,bool(enabled));settings.sync()
        theme.configure_accessibility(settings);self.finish_motion();self.actionbar.update();self.segments.update()
        for control in self.actionbar.controls:control.set_response(0.);control.update()

    def cache_dialog(self):
        dialog=QDialog(self); dialog.setWindowTitle('预加载与缓存'); dialog.setMinimumWidth(510)
        layout=QVBoxLayout(dialog); layout.setContentsMargins(24,20,24,24); layout.setSpacing(18)
        form=QFormLayout(); form.setVerticalSpacing(16); form.setHorizontalSpacing(20)
        ahead=CacheSpinBox(); ahead.setRange(0,5000); ahead.setSpecialValueText('自动（按内存调整）'); ahead.setValue(self.preload_count)
        warm=CacheSpinBox(); warm.setRange(0,50000); warm.setValue(self.warm_count)
        disk=CacheSpinBox(); disk.setRange(64,10240); disk.setSuffix(' MB'); disk.setValue(self.library.cache_limit//(1024*1024))
        for field in (ahead,warm,disk): field.setMinimumHeight(38); field.setMinimumWidth(240)
        form.addRow('内存预加载照片数',ahead); form.addRow('后台磁盘预读照片数',warm); form.addRow('磁盘缓存上限',disk)
        form_box=QWidget();form_box.setLayout(form);form_box.setMinimumHeight(3*38+2*16);layout.addWidget(form_box)
        note=QLabel(); note.setObjectName('caption'); note.setWordWrap(True); layout.addWidget(note)
        def estimate():
            size=self.display_size or QSize(800,600); count=ahead.value() or machine.PROFILE.buffer(size.width()*size.height()*4)
            effective=machine.PROFILE.preview_capacity(max(16,count),size.width()*size.height()*4+160*110*4,COMPOSITING['gpu'])
            note.setText(f'当前窗口预计实际预加载 {effective:,} 张，预览内存约 {(effective+BEHIND)*size.width()*size.height()*4/1024**3:.2f} GB。大照片会按安全内存预算调整，避免内存过量。\n达到设置的缓存限额后，立即按生成时间从旧到新删除最旧的 70% 预览文件。全部工作文件夹共用 10 GB 总限额。读取照片不会改变缓存生成时间。照片和筛选记录不会被清理。')
        ahead.valueChanged.connect(estimate); estimate()
        layout.addWidget(self.button('清理当前文件夹的预览缓存',lambda:(dialog.accept(),self.clear_cache())))
        buttons=QHBoxLayout(); buttons.addStretch(); buttons.addWidget(self.button('取消',dialog.reject))
        def save():
            self.preload_count=max(STRIP,ahead.value()) if ahead.value() else 0; self.warm_count=warm.value(); self.library.cache_limit=disk.value()*1024*1024
            settings=QSettings('PhotoSieve','PhotoSieve')
            for key,value in [('preload_count',self.preload_count),('warm_count',self.warm_count),('cache_mb',disk.value())]: settings.setValue(key,value)
            settings.sync(); self.display_size=None; self.update_display_size(); self.trim_previews(); self.library.schedule_prune(); self.prefetch(); dialog.accept()
        buttons.addWidget(self.button('保存',save,'primary')); layout.addLayout(buttons); dialog.exec()

    def clear_cache(self):
        if self.cache_clearing or self.busy or self.job_running(): return
        self.cache_clearing=True; library=self.library; self.busy=True; self.update_actions()
        self.toast.show_message('正在清理预览缓存…','info')
        def work():
            for pool in (self.image_pool,self.detail_pool,self.thumb_pool): pool.waitForDone()
            return library.clear_preview_cache()
        def done(count):
            self.cache_clearing=False; self.busy=False
            for store in (self.cache,self.cache_size,self.sources,self.thumb_cache,self.kept_thumb_cache,self.image_errors,self.warmed): store.clear()
            self.render(); self.prefetch(); self.queue_kept_thumbnails(); self.toast.show_message(f'已清理 {count:,} 个预览缓存，正在重新加载当前照片','info')
        def failed(message):
            self.cache_clearing=False; self.busy=False; self.update_actions(); self.prefetch(); self.error(message)
        self.submit(work,done,failed)

    def material_sources(self):
        # The tiny thumbnail LRU can evict visible photos while 200 previews fill; the pane cache stays ready.
        sources=[self.cache.get(p['id']) for p in self.queue[:2]]
        self.actionbar.set_sources(sources);self.segments.set_sources(sources)

    def machine_status(self):
        return (f'{machine.PROFILE.describe()} · 解码 {self.workers} · 预读 {self.ahead_target} · '
                + ('GPU 合成' if COMPOSITING['gpu'] else 'CPU 合成'))

    def set_gpu(self, enabled):
        QSettings('PhotoSieve', 'PhotoSieve').setValue('gpu_compositing', bool(enabled))
        self.toast.show_message(('GPU 合成' if enabled else 'CPU 合成') + '将在下次启动时生效', 'info', 2600)

    def show_fps(self, enabled):
        if not enabled:
            if self.fps_label: self.removeEventFilter(self); self.fps_timer.stop(); self.fps_label.deleteLater(); self.fps_label = None
            return
        if self.fps_label is not None:return
        self.installEventFilter(self)   # only while the meter is shown: every window event passes through Python
        self.fps_label = QLabel(self.window_chrome); self.fps_label.setObjectName('caption')
        self.fps_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fps_label.setContentsMargins(8,0,8,0)
        row=self.window_chrome.layout()
        row.insertWidget(row.indexOf(self.window_chrome.min_button),self.fps_label)
        self.fps_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); self.frames = 0
        self.fps_timer = QTimer(self.fps_label); self.fps_timer.timeout.connect(self.tick_fps); self.fps_timer.start(1000); self.tick_fps()
        self.fps_label.show()

    def tick_fps(self):
        # Window frames actually composed in the last second (one UpdateRequest per frame).
        if self.fps_label is None:return
        self.fps_label.setText(f'{self.frames} 帧/秒'); self.frames = 0

    def eventFilter(self, watched, event):
        if watched is self and event.type() == QEvent.Type.UpdateRequest: self.frames += 1
        return False

    def update_ready(self):
        self.badge.show(); self.toast.show_message('新版已下载 · 停止操作 3 秒后自动重启完成更新', 'info', 3600)

    def place_overlays(self):
        root = self.centralWidget()
        root.layout().activate()
        if self.stack.currentIndex() == 0: bottom = (self.action_row if self.actionbar_visible else self.strip).mapTo(root, QPoint(0, 0)).y() - 14
        elif self.stack.currentIndex()==2:
            bottom=self.stack.mapTo(root,QPoint(0,0)).y()+round(max(0,self.stack.height()-100)*.74)
        elif self.stack.currentIndex()==3:bottom=self.stack.mapTo(root,QPoint(0,0)).y()+60
        else: bottom = root.height() - 70
        self.toast.place(bottom)

    def resizeEvent(self, event):
        super().resizeEvent(event); self.place_overlays()
        panel=getattr(self,'more_panel',None)
        if panel and panel.isVisible(): panel.place()

    def nativeEvent(self,event_type,message):
        frame=getattr(self,'window_frame',None)
        result=frame.native_event(message) if frame else None
        return result if result is not None else super().nativeEvent(event_type,message)

    def showEvent(self, event):
        super().showEvent(event); QTimer.singleShot(0, self.place_overlays)

    # ---- state ------------------------------------------------------------
    def refresh_state(self, delay=0):
        if not self.timer.isActive(): self.state_timer.start(delay)

    def job_running(self):
        return bool(self.state_data and self.state_data['job']['running']) or self.library.job['running']

    def poll(self):
        if self.state_pending or self.closed: return
        self.state_pending = True; library = self.library
        def done(state):
            self.state_pending = False
            if library is not self.library: self.poll(); return
            if not self.session_active:
                self.state_data = dict(state, root='', cursor=0, counts={}); self.counts={}; self.stack.setCurrentIndex(5 if self.rating_active else 2); self.show_counts(); self.update_actions(); return
            first = self.state_data is None; old = self.state_data; self.state_data = state; job = state['job']; running = job['running']
            if not self.pending_ops: self.counts = dict(state['counts'])
            self.show_counts()
            if self.importing:
                if self.import_revision is None or job['revision']!=self.import_revision:
                    self.timer.start(180);self.update_actions();return
                self.import_page.set_progress(job.get('phase','正在打开照片文件夹'),job['done'],job['total'],state['root'],job.get('found',0))
                if running:
                    self.timer.start(180);self.progress.set_progress(job['done'],job['total']);self.was_running=True;self.update_actions();return
                self.importing=False;self.busy=False;self.stack.setCurrentIndex(1 if self.view=='deleted' else 4 if self.view=='keep' else 0);self.keep_back_button.hide();self.place_overlays()
                if self.rating_active:
                    self.stack.setCurrentWidget(self.rating_page);self.rating_page.activate();self.timer.stop();self.progress.set_progress(0,0,False);self.was_running=False;self.update_actions();return
                self.toast.hide();self.load(anchor=max(0,state['cursor']-1))
                self.timer.stop();self.progress.set_progress(0,0,False)
                self.was_running=False;self.update_actions()
                if job['errors'] or job['message']!='已完成':self.error(job['message']+f' · {len(job["errors"])} 项失败')
                return
            if running:
                if not self.timer.isActive(): self.timer.start(900)
                self.progress.set_progress(job['done'], job['total'])
            else:
                self.timer.stop(); self.progress.set_progress(0, 0, False)
            if self.rating_active:
                self.was_running=running;self.update_actions();return
            if first:
                self.stack.setCurrentIndex(2 if not state['root'] and not running else (1 if self.view == 'deleted' else 4 if self.view=='keep' else 0)); self.place_overlays()
                if state['root'] or running: self.load(anchor=max(0, state['cursor'] - 1))
            elif not running and (self.was_running or job.get('revision') != old['job'].get('revision')):
                errors = job.get('errors', []); message = job['kind'] + '：' + job['message'] + (f'，{len(errors)} 项失败' if errors else '')
                self.toast.show_message(message, 'error' if errors or job['message'] != '已完成' else 'info', 3600)
                if self.stack.currentIndex() == 2: self.stack.setCurrentIndex(0); self.place_overlays()
                self.load(anchor=0 if self.view == 'deleted' or not self.queue else self.queue[0]['id'] - 1)
            self.was_running = running; self.update_actions()
        def failed(message):
            self.state_pending = False; self.error(message)
        self.submit(library.state, done, failed)

    def show_counts(self):
        state = self.state_data or {}; counts = self.counts; self.segments.set_counts(counts if counts else {})
        root = state.get('root') or ''; job = state.get('job') or {}
        self.title.set_text(Path(root).name or root or '照片快筛')
        if job.get('running'):
            text = f"{job['kind']} · {job['done']:,}" + (f" / {job['total']:,}" if job.get('total') else '')
        elif root:
            text = f"{counts.get('all', 0):,} 张照片 · {root}"
        else:
            text = '原生桌面版 ' + APP_VERSION
        if self.date_start is not None and self.session_active:text+=' · 日期筛选已开启'
        self.subtitle.set_text(text)

    def refresh_chrome(self):
        """Counts and the delete toast follow every press, but while a key is held they repaint at most every 80 ms."""
        if not self.chrome_timer.isActive(): self.chrome_timer.start(80 if self.burst else 0)

    def paint_chrome(self):
        self.show_counts()
        self.toast.show_pending()

    def adjust_counts(self, action, status):
        if not self.counts: return
        source = 'keep' if status == 1 else 'pending'
        if action == 'delete':
            self.counts[source] = max(0, self.counts.get(source, 0) - 1); self.counts['deleted'] = self.counts.get('deleted', 0) + 1
            self.counts['all'] = max(0, self.counts.get('all', 0) - 1)
        elif action == 'keep' and status != 1:
            self.counts['pending'] = max(0, self.counts.get('pending', 0) - 1); self.counts['keep'] = self.counts.get('keep', 0) + 1
        self.refresh_chrome()

    # ---- queue ------------------------------------------------------------
    def switch(self, view):
        if self.rating_active:return
        self.updates.touch()
        if self.busy or view == self.view and not self.fetching: self.segments.set_current(self.view); return
        self.reset_zoom(); self.view = view; self.segments.set_current(view)
        if self.stack.currentIndex() != 2: self.stack.setCurrentIndex(1 if view == 'deleted' else 4 if view=='keep' else 0)
        self.keep_back_button.hide()
        self.place_overlays(); self.load(); self.refresh_state(0)

    def load(self, anchor=0, target_id=None):
        if self.rating_active:return
        self.generation += 1; generation = self.generation; view = self.view; library = self.library
        # Previews that failed (a disconnected drive, say) are retried whenever the queue reloads.
        self.fetching = True; self.queue = []; self.image_errors.clear(); self.update_actions(); self.update_display_size()
        if view == 'deleted':
            self.all_deleted_selected=False
            self.deleted_thumb_cache.clear(); self.deleted_errors.clear()
            self.deleted_rows = []; self.deleted_items = {}; self.deleted_grid.clear(); self.thumb_wanted.clear()
        def done(rows):
            if generation != self.generation: return
            self.fetching = False
            if view == 'deleted': self.add_deleted(rows); self.update_actions(); return
            self.queue = [p for p in rows if p['id'] not in self.inflight]; self.queue_end = len(rows) < PAGE
            if not self.queue and anchor: self.load(); return
            self.warmed.clear(); self.render(); self.prefetch()
        def failed(message):
            self.fetching = False; self.error(message)
        if target_id is None:
            self.refresh_strip()
            if view=='keep' and self.stack.currentIndex()==4:self.load_kept_grid()
        options=self.navigation_options() if view!='deleted' else {}
        target=target_id if target_id is not None else anchor+1 if anchor else 0
        self.submit(lambda: library.list_photos(view,target,'next',60 if view=='deleted' else PAGE,inclusive=bool(anchor) or target_id is not None,**options),done,failed)

    def refill(self):
        horizon=min(self.warm_count,max(self.ahead_target,int(self.library.cache_limit*.4/400_000)))
        needed=max(REFILL,self.ahead_target,horizon)
        if self.fetching or self.queue_end or len(self.queue) >= needed or not self.queue or self.view == 'deleted': return
        self.fetching = True; anchor = self.queue[-1]['id']; generation = self.generation; view = self.view; library = self.library
        def done(rows):
            if generation != self.generation: return   # a newer load owns the fetching flag now
            self.fetching = False; self.queue_end = len(rows) < PAGE
            ids = {p['id'] for p in self.queue}; self.queue.extend(p for p in rows if p['id'] not in ids and p['id'] not in self.inflight)
            self.render(); self.prefetch()
        def failed(message):
            if generation == self.generation: self.fetching = False
        options=self.navigation_options()
        self.submit(lambda: library.list_photos(view, anchor, 'next', PAGE,**options), done, failed)

    # ---- previews -----------------------------------------------------------
    def update_display_size(self):
        dpr = self.devicePixelRatioF(); view = self.photos[0].viewport().size()
        size = QSize(max(320, round(view.width() * dpr)), max(240, round(view.height() * dpr)))
        old = self.display_size
        if old is None or abs(size.width() - old.width()) > old.width() * .06 or abs(size.height() - old.height()) > old.height() * .06:
            self.display_size = size
            # Pane-sized RGB32 preview plus its strip thumbnail.
            pixel_bytes=size.width()*size.height()*4+160*110*4
            requested=max(STRIP,self.preload_count) if self.preload_count else machine.PROFILE.buffer(pixel_bytes)
            self.ahead_target=machine.PROFILE.preview_capacity(requested,pixel_bytes,COMPOSITING['gpu'])
            self.cache_limit = self.ahead_target + BEHIND
            return old is not None
        return False

    def resized(self):
        # Previews keep showing (scaled) while sharper ones are decoded for the new pane size.
        if self.update_display_size(): self.prefetch()
        self.queue_kept_thumbnails()

    def request_image(self, photo, priority):
        pid = photo['id']; library = self.library; size = self.display_size; dpr = self.devicePixelRatioF()
        if pid in self.loading or pid in self.image_errors or (pid in self.cache and self.cache_size.get(pid) == size): return
        self.loading.add(pid); thumb = (round(104 * dpr), round(68 * dpr))
        def done(result):
            if library is not self.library: return
            self.loading.discard(pid); (image, _), (small, _), source = result
            pixmap = QPixmap.fromImage(image); pixmap.setDevicePixelRatio(dpr)
            self.cache[pid] = pixmap; self.cache_size[pid] = size; self.sources[pid] = source[0]; self.cache.move_to_end(pid)
            self.trim_previews()
            icon = QPixmap.fromImage(small); icon.setDevicePixelRatio(dpr); self.store_thumbnail(pid, icon)
            for pane in self.photos:
                if pane.photo and pane.photo['id'] == pid and not pane.detail and not pane.zoom_animation:
                    fresh = pane.pixmap is None; pane.show_photo(pane.photo, pixmap); pane.source_width = source[0]
                    if fresh and pane.item and time.monotonic() - self.last_motion > BURST: self.fade(pane, 0, 1, 160)
            self.update_thumbnail(pid); self.material_sources(); self.update_actions(); self.prefetch()
        def failed(message):
            if library is not self.library: return
            self.loading.discard(pid); self.image_errors[pid] = message
            for pane in self.photos:
                if pane.photo and pane.photo['id'] == pid and not pane.detail: pane.show_photo(pane.photo, error=message)
            self.update_thumbnail(pid); self.update_actions(); self.prefetch()
        gate = self.display_gate
        def work():
            image = library.preview_image(pid, 1600)   # may wait for a decoder process, without holding the gate
            with gate: return display_image(image, size.width(), size.height(), thumb)
        self.submit(work, done, failed, self.image_pool, priority)

    def trim_previews(self):
        if len(self.cache)<=self.cache_limit: return
        ahead={p['id'] for p in self.queue[:self.ahead_target]}
        for old in [k for k in self.cache if k not in ahead][:len(self.cache)-self.cache_limit]:
            self.cache.pop(old); self.cache_size.pop(old,None); self.sources.pop(old,None)

    def prefetch(self):
        if self.rating_active:return
        if not self.session_active or self.importing or self.cache_clearing or self.view == 'deleted' or self.view=='keep' and self.stack.currentIndex()==4 or self.display_size is None: return
        self.refill()  # custom horizons can exceed the first 1,200 metadata rows
        # Nearest photos first; a bounded number in flight keeps priorities meaningful while the key is held.
        for index, photo in enumerate(self.queue[:self.ahead_target]):
            if len(self.loading) >= self.in_flight: break
            self.request_image(photo, 1000 - index)
        # Beyond that, build disk previews ahead with every decoder, but only once the nearest photos are ready.
        library = self.library; limit = 0 if self.loading else self.workers
        if len(self.warming) >= limit: return
        # Previews average roughly 0.4 MB; never warm further ahead than 40% of the disk cache holds.
        horizon = min(self.warm_count, max(self.ahead_target, int(library.cache_limit * .4 / 400_000)))
        for photo in self.queue[self.ahead_target:horizon]:
            if len(self.warming) >= limit: break
            pid = photo['id']
            if pid in self.warming or pid in self.warmed or pid in self.cache: continue
            self.warming.add(pid)
            def settled(_, pid=pid):
                if library is not self.library:return
                self.warming.discard(pid); self.warmed.add(pid); self.prefetch()
            self.submit(lambda pid=pid: library.preview(pid, 1600) and None, settled, settled, self.image_pool, -10)

    def ready(self):
        current = self.queue[0]['id'] if self.queue else None
        return current in self.cache or current in self.image_errors

    # ---- rendering ----------------------------------------------------------
    def render(self):
        for i, pane in enumerate(self.photos):
            p = self.queue[i] if len(self.queue) > i else None; pid = p['id'] if p else None
            if p and pid in self.cache: self.cache.move_to_end(pid)
            pixmap = self.cache.get(pid); error = self.image_errors.get(pid, '')
            same = bool(p and pane.photo and pane.photo['id'] == pid)
            if same and (pane.detail or pane.zoom_animation): continue
            if not same or not p or pane.pixmap is not pixmap or (error and not pixmap and not pane.text):
                message = '' if p or i else ('正在读取…' if self.fetching else ('所选日期范围内没有照片' if self.date_start is not None else '全部筛选完毕' if self.view == 'pending' else '没有照片'))
                pane.show_photo(p, pixmap, error, message); pane.source_width = self.sources.get(pid, 0)
            self.caption(i, p)
        self.material_sources(); self.update_strip()
        if self.queue: self.cursor_id = self.queue[0]['id']; self.cursor_timer.start(400)
        self.update_actions()

    def caption(self, index, photo, detail=None):
        if not photo: self.names[index].set_text(''); self.details[index].set_text(''); return
        self.names[index].set_text(photo['name'])
        folder = os.path.dirname(photo['relative'])
        text = detail or ' · '.join(x for x in (folder, '已保留' if photo['status'] == 1 else '') if x)
        self.details[index].set_text(text)

    def navigation_options(self):
        return dict(sort=self.sort_order,start_ns=self.date_start,end_ns=self.date_end)

    def refresh_strip(self):
        self.strip_revision+=1;revision=self.strip_revision;library=self.library;view=self.view
        options=self.navigation_options()
        def done(ids):
            if revision!=self.strip_revision or library is not self.library:return
            self.strip.set_ids([pid for pid in ids if pid not in self.inflight]);self.update_strip()
        self.submit(lambda:library.list_ids(view,**options),done)

    def update_strip(self):
        if self.view!='pending':
            if self.strip.count():self.strip.clear()
            return
        if self.queue:
            row=self.strip.index_of(self.queue[0]['id'])
            if row>=0 and self.strip.currentRow()!=row:self.strip.setCurrentRow(row)
        if self.queue:self.strip.adapt_to_preview(self.thumbnail(self.queue[0]['id']))
        for row in self.strip.visible_rows():self.update_thumbnail(self.strip.item(row).data(ROLE))
        self.strip_note.setText(f'未筛选 {self.strip.count():,} 张 · 拖动滚动条浏览全部照片 · '+{'file':'文件顺序','oldest':'文件日期：从旧到新','newest':'文件日期：从新到旧'}[self.sort_order])
        self.load_strip_thumbnails()

    def load_strip_thumbnails(self,*_):
        if self.closed or self.importing or self.view!='pending':return
        ids=[pid for pid in self.strip.visible_ids() if pid not in self.thumb_cache and pid not in self.cache and pid not in self.strip_loading and pid not in self.image_errors]
        available=max(0,8-len(self.strip_loading));ids=ids[:available]
        library=self.library
        for pid in ids:
            self.strip_loading.add(pid)
            def loaded(result,pid=pid):
                if library is not self.library:return
                self.strip_loading.discard(pid);(image,data),tip=result
                pixmap=QPixmap.fromImage(image);pixmap.setDevicePixelRatio(self.devicePixelRatioF());self.store_thumbnail(pid,pixmap)
                self.strip.tips[pid]=tip;self.update_thumbnail(pid);self.load_strip_thumbnails()
            def failed(message,pid=pid):
                if library is not self.library:return
                self.strip_loading.discard(pid);self.image_errors[pid]=message
            def work(pid=pid):
                photo=library.public(library.photo(pid));im=library.preview_image(pid,320)
                im.thumbnail((104,68));return qimage(im),photo['relative']
            self.submit(work,loaded,failed,self.thumb_pool)

    def store_thumbnail(self, pid, icon):
        self.thumb_cache[pid]=icon; self.thumb_cache.move_to_end(pid)
        visible=set(self.strip.visible_ids())
        for old in list(self.thumb_cache):
            if len(self.thumb_cache)<=64: break
            if old not in visible: self.thumb_cache.pop(old)

    def thumbnail(self, pid):
        icon=self.thumb_cache.get(pid)
        if icon is None and pid in self.cache:
            pixmap=self.cache[pid]; dpr=pixmap.devicePixelRatioF()
            icon=pixmap.scaled(round(104*dpr),round(68*dpr),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            icon.setDevicePixelRatio(dpr); self.store_thumbnail(pid,icon)
        if icon is not None: self.thumb_cache.move_to_end(pid)
        return icon

    def update_thumbnail(self,pid):
        row=self.strip.index_of(pid)
        if row>=0:
            preview=self.thumbnail(pid)
            self.strip.set_preview_width(pid,preview)
            if self.queue and pid==self.queue[0]['id']:self.strip.adapt_to_preview(preview)
            self.strip.photo_model.changed(row)
        self.refresh_chrome()

    def update_actions(self):
        self.rating_button.setEnabled(not self.busy and not self.pending_ops and not self.job_running())
        if self.rating_active and self.rating_page.writing:
            self.open_button.setEnabled(False);self.export_button.setEnabled(False);return
        running = self.job_running(); reviewing = self.view != 'deleted' and not (self.view=='keep' and self.stack.currentIndex()==4)
        allowed = bool(self.queue) and self.ready() and not self.busy and not running and reviewing
        for button, enabled in [(self.keep_button, allowed and self.queue[0]['status']!=1), (self.delete_button, allowed),
                                (self.next_button, bool(self.queue) and not self.busy and reviewing),
                                (self.restore_button, not self.busy and not running and (self.counts.get('deleted', 1) > 0 or self.pending_ops > 0)),
                                (self.purge_selected, bool(self.deleted_grid.selectedItems()) and not self.busy and not running),
                                (self.select_all_deleted, bool(self.counts.get('deleted')) and not self.busy and not running),
                                (self.restore_selected, bool(self.deleted_grid.selectedItems()) and not self.busy and not running),
                                (self.export_button, not self.busy and not running and bool(self.counts.get('keep'))),
                                (self.open_button, not self.busy and not running)]:
            if button.isEnabled() != enabled: button.setEnabled(enabled)

    # ---- motion -------------------------------------------------------------
    def track(self, animation):
        self.motions.append(animation); animation.finished.connect(lambda: animation in self.motions and self.motions.remove(animation))
        return animation

    def finish_motion(self):
        for animation in list(self.motions): animation.setCurrentTime(animation.duration())

    def fade(self, pane, start, end, duration):
        item = pane.item
        if item is None: return
        def step(value):
            if pane.item is item: item.setOpacity(value)
        item.setOpacity(start); self.track(animate(self, duration, step, theme.EASE, None, start, end))

    def ghost(self, pane):
        # The pane's own pixmap, not a viewport grab: no re-render on the keypress frame, whatever composites the window.
        if pane.detail or not pane.item or not pane.pixmap: return None
        rect = pane.content_rect()
        if rect.isEmpty(): return None
        # Still easing back from a zoom, the pane shows only part of the photo: take exactly what is on screen.
        picture = pane.viewport().grab(rect) if pane.zoom_animation else pane.pixmap
        return Ghost(self.centralWidget(), picture, QRect(pane.viewport().mapTo(self.centralWidget(), rect.topLeft()), rect.size()))

    def begin_transition(self):
        """Snapshot both panes before the queue moves; held keys get instant swaps instead."""
        now = time.monotonic(); burst = self.burst = now - self.last_motion < BURST; self.last_motion = now; self.finish_motion()
        if burst or theme.REDUCED_MOTION or not self.isVisible(): return None
        return self.ghost(self.photos[0]), self.ghost(self.photos[1])

    def play_transition(self, kind, ghosts):
        if not ghosts: return
        left, right = ghosts; offset = {'keep': QPoint(0, -48), 'delete': QPoint(0, 56), 'next': QPoint(-72, 0)}[kind]
        if left:
            start = left.pos()
            def lift(t, g=left):
                g.move(start + QPoint(round(offset.x() * t), round(offset.y() * t))); g.opacity = max(0.0, 1 - t * 1.5); g.update()
            self.track(animate(self, 300, lift, theme.SNAPPY, left.deleteLater))
        target_pane = self.photos[0]
        if right and target_pane.item and target_pane.photo and self.queue and target_pane.photo['id'] == self.queue[0]['id']:
            origin = right.pos(); rect = right.geometry().translated(-self.photos[1].viewport().mapTo(self.centralWidget(), QPoint(0, 0)))
            destination = self.photos[0].viewport().mapTo(self.centralWidget(), rect.topLeft()); item = target_pane.item; item.setOpacity(0)
            def glide(t, g=right):
                g.move(origin + (destination - origin) * t)
            def landed(g=right, item=item):
                if target_pane.item is item: item.setOpacity(1)
                g.deleteLater()
            right.raise_(); self.track(animate(self, 320, glide, theme.SNAPPY, landed))
        elif right: right.deleteLater()
        if self.photos[1].item: self.fade(self.photos[1], 0, 1, 320)
        self.toast.raise_()

    def nudge(self):
        """iOS-style refusal shake when the current preview is not ready yet."""
        if theme.REDUCED_MOTION: return
        now = time.monotonic()
        if now - self.last_nudge < .6: return
        self.last_nudge = now; pane = self.photos[0]; item = pane.item or pane.text
        if item is None: return
        def step(t):
            if (pane.item or pane.text) is item: item.setX(8 * (1 - t) * math.sin(t * 6 * math.pi))
        self.track(animate(self, 320, step, theme.EASE, lambda: (pane.item or pane.text) is item and item.setX(0)))

    # ---- actions ------------------------------------------------------------
    def act(self, action):
        if self.rating_active:return
        self.updates.touch()
        if action not in ('keep', 'delete') or self.closing or self.view=='keep' and self.stack.currentIndex()==4: return
        if not (self.keep_button if action=='keep' else self.delete_button).isEnabled():
            if self.queue and self.view != 'deleted' and not self.busy and not self.job_running(): self.nudge()
            return
        photo = self.queue[0]; pid = photo['id']; generation = self.generation; view = self.view; library = self.library
        ghosts = self.begin_transition(); self.queue.pop(0); self.inflight.add(pid); self.strip.remove_id(pid); self.adjust_counts(action, photo['status'])
        self.render(); self.play_transition(action, ghosts); self.prefetch(); self.refill()
        if action == 'delete':
            self.delete_streak += 1
            back = f'{self.key("restore")} 恢复'
            self.toast.defer(f'已移到暂存 · {back}' if self.delete_streak == 1 else f'已删除 {self.delete_streak} 张 · {back}', 'delete'); self.refresh_chrome()
        elif self.delete_streak and not self.toast.active():
            self.delete_streak = 0
        def done(result):
            self.inflight.discard(pid)
            if library is self.library and self.state_data is not None: self.state_data['can_undo']=True
            if not self.toast.active(): self.delete_streak = 0
        def failed(message):
            self.inflight.discard(pid);self.refresh_strip()
            if generation == self.generation and self.view == view and all(p['id'] != pid for p in self.queue):
                self.queue.insert(0, photo); self.render(); self.prefetch()
            self.delete_streak = 0; self.error(message)
        self.op((lambda: library.mark(pid, 1)) if action == 'keep' else (lambda: library.trash(pid)), done, failed)

    def next_photo(self):
        if self.rating_active:return
        self.updates.touch()
        if self.busy or self.view == 'deleted' or self.view=='keep' and self.stack.currentIndex()==4 or not self.queue: return
        ghosts = self.begin_transition(); self.queue.pop(0); self.render(); self.play_transition('next', ghosts); self.prefetch(); self.refill()

    def strip_select(self, item):
        self.updates.touch()
        if self.busy: return
        pid = item.data(ROLE); index = next((i for i, p in enumerate(self.queue) if p['id'] == pid), None)
        if index: self.finish_motion(); self.queue = self.queue[index:]; self.render(); self.prefetch(); self.refill()
        elif index is None:self.finish_motion();self.load(target_id=pid)

    def restore(self, photo_id=None, general=False):
        self.updates.touch()
        if not self.session_active or self.busy or self.closing or self.restore_running: return
        if photo_id is None and not self.pending_ops:
            if general and self.state_data and not self.state_data.get('can_undo'):return
            if not general and self.counts.get('deleted')==0:return
        self.restore_running=True
        library = self.library; generation = self.generation
        def work():
            result = library.undo(only_delete=not general, photo_id=photo_id)
            try: result['photo'] = library.public(library.photo(result['id']))
            except Exception: result['photo'] = None
            with library.connect() as db:result['can_undo']=bool(db.execute('SELECT 1 FROM history LIMIT 1').fetchone())
            return result
        def done(result):
            self.restore_running=False
            if generation != self.generation: return
            pid = result['id']; photo = result['photo']; self.delete_streak = 0;self.refresh_strip()
            if self.state_data is not None:self.state_data['can_undo']=result['can_undo']
            if self.counts and photo:
                destination='keep' if photo['status']==1 else 'pending'
                if result.get('restored'):
                    self.counts['deleted']=max(0,self.counts.get('deleted',0)-1);self.counts['all']=self.counts.get('all',0)+1
                else:
                    source='pending' if destination=='keep' else 'keep';self.counts[source]=max(0,self.counts.get(source,0)-1)
                self.counts[destination]=self.counts.get(destination,0)+1;self.refresh_chrome()

            if pid in self.deleted_items:
                self.deleted_grid.takeItem(self.deleted_grid.row(self.deleted_items.pop(pid)))
                self.deleted_rows = [p for p in self.deleted_rows if p['id'] != pid]
            if self.view != 'deleted':
                self.queue = [p for p in self.queue if p['id'] != pid]; wanted = 1 if self.view == 'keep' else 0
                within_dates=photo and (self.date_start is None or self.date_start<=photo['mtime']<self.date_end)
                if photo and photo['status'] == wanted and within_dates:
                    self.finish_motion(); self.queue.insert(0, photo); self.render(); self.prefetch()
                    pane = self.photos[0]
                    if pane.item:
                        item = pane.item; item.setTransformOriginPoint(item.boundingRect().center())
                        def settle(t):
                            if pane.item is item: item.setOpacity(t); item.setScale(.96 + .04 * t)
                        self.track(animate(self, 320, settle, theme.SNAPPY))
                else: self.render()
            kept = photo and photo['status'] == 1 and self.view == 'pending'
            self.toast.show_message(('已恢复到原位置' + (' · 在「保留」中' if kept else '')) if result.get('restored') else '已撤回', 'keep')
            if self.view=='keep' and self.stack.currentIndex()==4:self.load_kept_grid()
            self.update_actions()
        def failed(message):
            self.restore_running=False;self.error(message)
        self.op(work, done, failed)

    def restore_selection(self):
        items = self.deleted_grid.selectedItems()
        if not items: return
        ids=[item.data(ROLE) for item in items]
        all_selected=getattr(self,'all_deleted_selected',False) and len(ids)==self.deleted_grid.count()
        if len(ids)==1 and not all_selected: self.restore(photo_id=ids[0]); return
        library=self.library
        self.busy=True; self.update_actions(); self.timer.start(900)
        def work():
            library.restore_deleted(None if all_selected else ids)
            while library.job['running']: time.sleep(.01)
            return library.job
        def done(_): self.busy=False; self.poll(); self.timer.start(900)
        def failed(message): self.busy=False; self.update_actions(); self.error(message)
        self.op(work,done,failed)

    def uninstaller_path(self):
        return Path(sys.executable).resolve().parent/'unins000.exe' if getattr(sys,'frozen',False) else Path(__file__).resolve().parent/'unins000.exe'

    def uninstall_app(self):
        if not self.uninstaller_path().is_file(): self.toast.show_message('当前为便携版，请通过安装版使用卸载功能','info'); return
        box=QMessageBox(self); box.setWindowTitle('卸载照片快筛')
        box.setText('关闭应用并打开卸载向导？'); box.setInformativeText('照片和 workspaces 筛选记录会保留。卸载向导中仍可取消。')
        proceed=box.addButton('打开卸载向导',QMessageBox.ButtonRole.AcceptRole); box.addButton('取消',QMessageBox.ButtonRole.RejectRole); box.exec()
        if box.clickedButton() is proceed: self.uninstall_requested=True; self.close()

    # ---- zoom -----------------------------------------------------------------
    def zoom_caption(self, index):
        pane = self.photos[index]
        if not pane.detail or not pane.photo: return
        percent = pane.transform().m11() / max(.0001, pane.zoom_target()) * 100
        size = f'{pane.pixmap.width()} × {pane.pixmap.height()}' if pane.full_resolution else '正在读取原始分辨率…'
        self.caption(index, pane.photo, f'{percent:.0f}% · {size} · 滚轮缩放 / 拖动查看 · {self.key("unzoom")} 还原')

    def reset_pane(self, pane):
        if pane.detail or not pane.photo: return
        pid = pane.photo['id']; pane.show_photo(pane.photo, self.cache.get(pid), self.image_errors.get(pid, '')); pane.source_width = self.sources.get(pid, 0)
        self.caption(self.photos.index(pane), pane.photo)

    def reset_zoom(self):
        for pane in self.photos:
            if pane.detail:
                photo = pane.photo; pane.unzoom(lambda pane=pane: self.reset_pane(pane)); self.caption(self.photos.index(pane), photo)

    def focus(self, photo, point=None):
        self.updates.touch()
        sender = self.sender()
        pane = sender if isinstance(sender, PhotoView) else next((v for v in self.photos if v.photo and v.photo['id'] == photo['id']), None)
        if pane is None or not pane.pixmap: return
        if pane.detail:
            pane.unzoom(lambda: self.reset_pane(pane)); self.caption(self.photos.index(pane), photo); return
        self.finish_motion(); pane.zoom_at(point)
        self.caption(self.photos.index(pane), photo, f'正在读取原始分辨率… 再点一次或 {self.key("unzoom")} 还原'); self.load_focus(photo)

    def pump_focus(self):
        for pane in self.photos:
            if pane.detail and pane.photo and not pane.full_resolution and not pane.pending_full: self.load_focus(pane.photo)

    def load_focus(self, photo):
        pid = photo['id']; library = self.library
        if pid in self.focus_loading or len(self.focus_loading) >= 2: return
        self.focus_loading.add(pid)
        def loaded(image):
            if library is not self.library: return
            self.focus_loading.discard(pid)
            if image.isNull(): failed('原图解码失败'); return
            pixmap = None
            for index, pane in enumerate(self.photos):
                if pane.detail and pane.photo and pane.photo['id'] == pid:
                    if pixmap is None: pixmap = QPixmap.fromImage(image); pixmap.setDevicePixelRatio(self.devicePixelRatioF())
                    pane.install_full_image(pixmap)
                    self.zoom_caption(index)
            self.pump_focus()
        def failed(message):
            if library is not self.library: return
            self.focus_loading.discard(pid); self.error('原始分辨率读取失败：' + message)
            for pane in self.photos:
                if pane.detail and pane.photo and pane.photo['id'] == pid: pane.unzoom(lambda pane=pane: self.reset_pane(pane))
            self.pump_focus()
        self.submit(lambda: full_qimage(library.preview_image(pid, 0)), loaded, failed, self.detail_pool)

    def focus_left(self):
        if self.rating_active:return
        if self.queue and self.view != 'deleted' and not (self.view=='keep' and self.stack.currentIndex()==4): self.focus(self.queue[0])

    def save_cursor(self):
        pid = self.cursor_id; library = self.library
        if pid: self.submit(lambda: library.remember('cursor', pid))

    def edit_ordering(self):
        if not self.session_active or self.busy:return
        dialog=QDialog(self);dialog.setWindowTitle('排序与日期筛选');dialog.setMinimumWidth(500)
        layout=QVBoxLayout(dialog);form=QFormLayout();form.setVerticalSpacing(16)
        sort=QComboBox();sort.addItem('文件顺序','file');sort.addItem('文件日期：从旧到新','oldest');sort.addItem('文件日期：从新到旧','newest');sort.setCurrentIndex(sort.findData(self.sort_order))
        form.addRow('排序方式',sort)
        enabled=QCheckBox('只显示指定日期范围');enabled.setChecked(self.date_start is not None);form.addRow(enabled)
        from datetime import datetime
        start=QDateEdit();end=QDateEdit()
        for field in (start,end):field.setCalendarPopup(True);field.setDisplayFormat('yyyy-MM-dd');field.setMinimumHeight(36)
        first=QDate.currentDate().addYears(-1);last=QDate.currentDate()
        if self.date_start is not None:
            date=datetime.fromtimestamp(self.date_start/1e9);first=QDate(date.year,date.month,date.day)
            date=datetime.fromtimestamp((self.date_end-1)/1e9);last=QDate(date.year,date.month,date.day)
        start.setDate(first);end.setDate(last);form.addRow('开始日期',start);form.addRow('结束日期',end)
        def changed(value):start.setEnabled(value);end.setEnabled(value)
        changed(enabled.isChecked());enabled.toggled.connect(changed)
        layout.addLayout(form)
        hint=QLabel('日期使用照片文件的修改时间。范围只影响未筛选／保留视图，不影响已删除列表和导出。');hint.setObjectName('caption');hint.setWordWrap(True);layout.addWidget(hint)
        error=QLabel();error.setWordWrap(True);error.setStyleSheet('color:'+theme.ERROR_TEXT);layout.addWidget(error)
        row=QHBoxLayout();row.addStretch();row.addWidget(self.button('取消',dialog.reject))
        def apply():
            if enabled.isChecked() and start.date()>end.date():error.setText('开始日期不能晚于结束日期。');return
            self.sort_order=sort.currentData();self.date_start=self.date_end=None
            if enabled.isChecked():
                def timestamp(date):return int(datetime(date.year(),date.month(),date.day()).timestamp()*1e9)
                self.date_start=timestamp(start.date());self.date_end=timestamp(end.date().addDays(1))
            dialog.accept();self.reset_zoom();self.load()
        row.addWidget(self.button('应用',apply,'primary'));layout.addLayout(row)
        dialog.exec();dialog.deleteLater()

    # ---- folders --------------------------------------------------------------
    def choose_source(self):
        self.updates.touch()
        crashlog.event('choosing_folder')
        start = (self.state_data or {}).get('root') or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, '选择照片文件夹', start)
        crashlog.event('folder_chosen' if folder else 'folder_choice_cancelled',folder=folder)
        if folder: self.scan(str(Path(folder)))   # Qt hands back forward slashes on Windows

    def scan(self, folder=None):
        if self.rating_page.writing:return
        self.rating_page.deactivate()
        if folder is None: self.choose_source(); return
        if self.busy: return
        folder = str(folder); self.session_active=True; self.state_data=None; current = self.library.meta('root')
        self.busy=True;self.importing=True;self.import_revision=None;self.update_actions();self.toast.hide()
        self.import_page.set_progress('正在打开筛选记录',folder=folder);self.stack.setCurrentIndex(3);self.place_overlays()
        crashlog.event('opening_folder',folder=folder,preload_requested=self.preload_count,done=0,total=0,found=0,file='')
        if (current and Path(current).resolve() != Path(folder).resolve()) or (self.managed_workspaces and (not current or not self.library.directory.is_relative_to(self.data_root.resolve()))):
            newdir = self.data_root / 'libraries' / hashlib.sha256(str(Path(folder).resolve()).encode()).hexdigest()[:16]
            def changed(library):
                QSettings('PhotoSieve', 'PhotoSieve').setValue('library', str(library.directory)); library.offload = self.decoders
                self.library.stop_session(); library.start_session()
                self.library = library; library.cache_changed=self.cache_changed.emit; self.generation += 1
                self.strip_revision+=1;self.strip.clear();self.kept_revision+=1;self.kept_grid.clear();self.kept_thumb_cache.clear();self.kept_loading.clear()
                crashlog.event('workspace_ready',library=str(library.directory))
                for store in (self.cache, self.cache_size, self.sources, self.loading, self.image_errors, self.thumb_cache, self.focus_loading, self.warming, self.warmed,self.strip_loading): store.clear()
                self.reset_zoom(); self.state_data = None; self.state_pending = False; self.counts = {}
                self.cursor_timer.stop(); self.cursor_id = 0; self.view = 'pending'; self.segments.set_current('pending', False)
                self.queue = []; self.render(); self.start_scan(library, folder)
            def failed(message):
                self.busy = False;self.importing=False;self.stack.setCurrentIndex(2);self.update_actions();self.error(message)
            cache_mb=QSettings('PhotoSieve','PhotoSieve').value('cache_mb',self.library.cache_limit//1024**2,type=int)
            self.submit(lambda: open_workspace(self.data_root,folder,self.previous_library,cache_mb) if self.managed_workspaces else Library(newdir,cache_mb), changed, failed)
            return
        self.start_scan(self.library, folder)

    def start_scan(self, library, folder):
        crashlog.event('starting_scan',folder=str(folder))
        def started(_):
            crashlog.event('scan_notified',folder=str(folder))
            self.import_revision=library.job['revision']
            self.poll(); self.timer.start(180)
        def failed(message):
            self.importing=False;self.busy=False;self.stack.setCurrentIndex(2);self.update_actions();self.error(message)
        self.submit(lambda: library.scan(folder), started,failed)

    def open_logs(self):
        directory=crashlog.SESSION.root if crashlog.SESSION else self.data_root/'logs'
        directory.mkdir(parents=True,exist_ok=True);self.open_folder(directory)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).is_dir(): event.acceptProposedAction()

    def dropEvent(self, event):
        self.scan(str(Path(event.mimeData().urls()[0].toLocalFile()))); event.acceptProposedAction()

    def open_folder(self, path):
        if not Path(path).is_dir(): self.error('文件夹不存在，或硬盘未连接：' + path); return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    # ---- kept gallery ----------------------------------------------------------
    def load_kept_grid(self):
        self.kept_revision+=1;revision=self.kept_revision;library=self.library
        self.kept_loading.clear();options=self.navigation_options()
        self.kept_note.setText('正在读取已保留照片…')
        def done(ids):
            if revision!=self.kept_revision or library is not self.library:return
            self.kept_grid.set_ids(ids)
            self.kept_note.setText(f'已保留 {len(ids):,} 张 · 单击缩略图查看大图，右上角可导出全部保留照片' if ids else '没有已保留照片' if self.date_start is None else '所选日期范围内没有已保留照片')
            QTimer.singleShot(0,self.queue_kept_thumbnails)
        self.submit(lambda:library.list_ids('keep',include_kept=True,**options),done,self.error)

    def queue_kept_thumbnails(self,*_):
        if self.closed or self.view!='keep' or self.stack.currentIndex()!=4 or self.cache_clearing:return
        visible=self.kept_grid.visible_ids();protected=set(visible)
        for old in list(self.kept_thumb_cache):
            if len(self.kept_thumb_cache)<=max(128,len(protected)):break
            if old not in protected:
                self.kept_thumb_cache.pop(old);self.kept_grid.captions.pop(old,None);self.kept_grid.tips.pop(old,None)
        candidates=[pid for pid in visible if pid not in self.kept_thumb_cache and pid not in self.kept_loading and pid not in self.image_errors]
        revision=self.kept_revision;library=self.library;dpr=self.devicePixelRatioF();size=(round(176*dpr),round(118*dpr))
        for pid in candidates[:max(0,3-len(self.kept_loading))]:
            self.kept_loading[pid]=revision
            def work(pid=pid):
                photo=library.public(library.photo(pid))
                return display_image(library.preview_image(pid,320),*size,(8,8)),photo['name'],photo['path']
            def done(result,pid=pid):
                if revision!=self.kept_revision or library is not self.library:return
                self.kept_loading.pop(pid,None)
                image,name,path=result;pixmap=QPixmap.fromImage(image[0][0]);pixmap.setDevicePixelRatio(dpr)
                self.kept_thumb_cache[pid]=pixmap;self.kept_thumb_cache.move_to_end(pid)
                self.kept_grid.captions[pid]=name;self.kept_grid.tips[pid]=path
                row=self.kept_grid.index_of(pid)
                if row>=0:self.kept_grid.photo_model.changed(row)
                self.queue_kept_thumbnails()
            def failed(message,pid=pid):
                if revision!=self.kept_revision or library is not self.library:return
                self.kept_loading.pop(pid,None);self.image_errors[pid]=message
                self.kept_grid.captions[pid]='无法预览';self.kept_grid.tips[pid]=message
                row=self.kept_grid.index_of(pid)
                if row>=0:self.kept_grid.photo_model.changed(row)
                self.queue_kept_thumbnails()
            self.submit(work,done,failed,self.thumb_pool)

    def open_kept_item(self,item):
        if self.busy or self.view!='keep':return
        self.stack.setCurrentIndex(0);self.keep_back_button.show();self.place_overlays()
        self.load(target_id=item.data(ROLE))

    def back_to_kept(self):
        if self.view!='keep' or self.busy:return
        self.reset_zoom();self.stack.setCurrentIndex(4);self.keep_back_button.hide();self.place_overlays();self.update_actions();self.load_kept_grid()

    # ---- deleted grid ----------------------------------------------------------
    def select_deleted(self):
        self.all_deleted_selected=True; self.deleted_grid.selectAll()
        self.toast.show_message(f'已选中全部 {self.counts.get("deleted",0):,} 张已删除照片（包含尚未加载的照片）','info')

    def deleted_selection_changed(self):
        if getattr(self,'all_deleted_selected',False) and len(self.deleted_grid.selectedItems())!=self.deleted_grid.count():
            self.all_deleted_selected=False
        self.update_actions()

    def purge_selection(self):
        ids=[item.data(ROLE) for item in self.deleted_grid.selectedItems()]
        if not ids: return
        all_selected=getattr(self,'all_deleted_selected',False) and len(ids)==self.deleted_grid.count()
        count=self.counts.get('deleted',len(ids)) if all_selected else len(ids)
        box=QMessageBox(self); box.setWindowTitle('彻底删除照片')
        box.setText(f'彻底删除 {count:,} 张暂存照片？'); box.setInformativeText('文件将永久删除，不进入 Windows 回收站，无法通过左键或撤回恢复。')
        confirm=box.addButton('彻底删除',QMessageBox.ButtonRole.DestructiveRole); box.addButton('取消',QMessageBox.ButtonRole.RejectRole); box.exec()
        if box.clickedButton() is confirm:
            self.submit(lambda:self.library.purge_deleted(None if all_selected else ids),lambda _:(self.poll(),self.timer.start(900)))

    def add_deleted(self, rows):
        previous=self.deleted_grid.blockSignals(True)
        self.deleted_rows.extend(rows); self.deleted_more = len(rows) == 60
        for photo in rows:
            item = QListWidgetItem(self.tile, photo['name']); item.setData(ROLE, photo['id'])
            item.setToolTip('原位置：' + photo['path'] + '\n暂存：' + str(photo['backup'])); self.deleted_grid.addItem(item)
            self.deleted_items[photo['id']] = item
        if getattr(self,'all_deleted_selected',False): self.deleted_grid.selectAll()
        self.deleted_grid.blockSignals(previous); self.update_actions()
        QTimer.singleShot(0,self.queue_deleted_thumbnails)

    def queue_deleted_thumbnails(self):
        if self.view!='deleted' or self.cache_clearing: return
        grid=self.deleted_grid; view=grid.viewport(); cell=grid.gridSize()
        columns=max(1,view.width()//cell.width()); samples=[]
        for y in range(1,max(2,view.height()),max(1,cell.height()//2)):
            index=grid.indexAt(QPoint(min(cell.width()//2,max(0,view.width()-1)),y))
            if index.isValid(): samples.append(index.row())
        start=max(0,(min(samples) if samples else 0)-columns)
        end=min(grid.count(),start+(view.height()//cell.height()+4)*columns)
        wanted=[grid.item(i).data(ROLE) for i in range(start,end)]
        self.deleted_thumb_protected=set(wanted); self.trim_deleted_thumbnails()
        self.thumb_wanted=deque(pid for pid in wanted if pid not in self.deleted_thumb_cache and pid not in self.thumb_loading and pid not in self.deleted_errors)
        self.pump_thumbnails()

    def pump_thumbnails(self):
        library = self.library; generation = self.generation; dpr = self.devicePixelRatioF(); size = (round(176 * dpr), round(118 * dpr))
        while self.thumb_wanted and len(self.thumb_loading) < 3:
            pid = self.thumb_wanted.popleft()
            if pid not in self.deleted_items: continue
            self.thumb_loading.add(pid)
            def done(result, pid=pid):
                self.thumb_loading.discard(pid)
                item = self.deleted_items.get(pid)
                if item and generation == self.generation:
                    pixmap = QPixmap.fromImage(result[0][0]); pixmap.setDevicePixelRatio(dpr); item.setIcon(QIcon(pixmap))
                    self.deleted_thumb_cache[pid]=pixmap; self.deleted_thumb_cache.move_to_end(pid)
                    self.trim_deleted_thumbnails()
                self.pump_thumbnails()
            def failed(message, pid=pid):
                self.thumb_loading.discard(pid); item = self.deleted_items.get(pid)
                if item and generation==self.generation:
                    self.deleted_errors.add(pid); item.setText('无法预览'); item.setToolTip(item.toolTip() + '\n' + message)
                self.pump_thumbnails()
            self.submit(lambda pid=pid: display_image(library.preview_image(pid, 320), *size, (8, 8)), done, failed, self.thumb_pool)

    def maybe_more_deleted(self, value):
        self.queue_deleted_thumbnails()
        bar = self.deleted_grid.verticalScrollBar()
        if self.deleted_more and not self.fetching and value >= bar.maximum() - 240: self.load_more_deleted()

    def trim_deleted_thumbnails(self):
        protected=getattr(self,'deleted_thumb_protected',set()); limit=max(128,len(protected))
        for old in list(self.deleted_thumb_cache):
            if len(self.deleted_thumb_cache)<=limit: break
            if old not in protected:
                self.deleted_thumb_cache.pop(old)
                if old in self.deleted_items: self.deleted_items[old].setIcon(self.tile)

    def load_more_deleted(self):
        if self.fetching or not self.deleted_more: return
        self.fetching = True; anchor = self.deleted_rows[-1]['id'] if self.deleted_rows else 0; generation = self.generation; library = self.library
        def done(rows):
            self.fetching = False
            if generation == self.generation: self.add_deleted(rows)
        self.submit(lambda: library.list_photos('deleted', anchor, 'next', 60), done, lambda message: setattr(self, 'fetching', False))

    # ---- export -------------------------------------------------------------------
    def export_dialog(self):
        self.updates.touch()
        dialog = QDialog(self); dialog.setWindowTitle('导出保留的照片'); dialog.setFixedWidth(560)
        layout = QVBoxLayout(dialog); layout.setContentsMargins(28, 24, 28, 22); layout.setSpacing(10)
        count = self.counts.get('keep', 0); heading = QLabel('导出保留的照片'); heading.setObjectName('heading'); layout.addWidget(heading)
        lead = QLabel(f'{count:,} 张已保留的照片，保留原文件名和子文件夹。'); lead.setObjectName('lead'); lead.setWordWrap(True); layout.addWidget(lead)
        layout.addSpacing(6); summary = self.library.export_summary(); chosen = {'path': summary['destination'] if summary else ''}
        row = QHBoxLayout(); target = QLabel(); target.setObjectName('name'); target.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        def show_target():
            target.setText(target.fontMetrics().elidedText(chosen['path'] or '尚未选择目标文件夹', Qt.TextElideMode.ElideMiddle, 380))
        def choose():
            folder = QFileDialog.getExistingDirectory(dialog, '选择目标文件夹', chosen['path'])
            if folder: chosen['path'] = folder; show_target()
        row.addWidget(target, 1); row.addWidget(self.button('选择…', choose)); layout.addLayout(row); show_target()
        note = QLabel(f'复制：原片仍在原处。移动：原片转到目标文件夹，不能用 {self.key("restore")} 撤回。\n目标中已有的同名文件会跳过，不会覆盖。'); note.setObjectName('caption'); note.setWordWrap(True); layout.addWidget(note)
        problem = QLabel(); problem.setStyleSheet(f'color:{theme.RED}; font-size:12px;'); problem.setWordWrap(True); problem.hide(); layout.addWidget(problem)
        if summary:
            result = QLabel(f"上次：成功 {summary['done']:,} / {summary['total']:,}，失败 {summary['failed']:,}"); result.setObjectName('caption'); layout.addWidget(result)
            if summary['errors']:
                failures = QLabel('\n'.join((e['original'] + '：' + e['error']) for e in summary['errors'][:20])); failures.setObjectName('caption')
                failures.setWordWrap(True); failures.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(failures); scroll.setMaximumHeight(140); layout.addWidget(scroll)
            reveal = self.button('在资源管理器中显示上次导出', lambda: self.open_folder(summary['destination']), 'plain'); layout.addWidget(reveal, 0, Qt.AlignmentFlag.AlignLeft)
        def start(mode):
            if not chosen['path']: problem.setText('请先选择目标文件夹。'); problem.show(); return
            if self.busy: problem.setText('正在准备更新或切换文件夹，请稍后再导出。'); problem.show(); return
            def started(_):
                dialog.accept(); self.poll(); self.timer.start(900)
            def failed(message):
                problem.setText(message); problem.show()
            self.submit(lambda: self.library.export_kept(chosen['path'], mode), started, failed)
        layout.addSpacing(8); buttons = QHBoxLayout(); buttons.addStretch()
        buttons.addWidget(self.button('取消', dialog.reject)); buttons.addWidget(self.button('移动', lambda: start('move')))
        copy = self.button('复制', lambda: start('copy'), 'primary'); copy.setDefault(True); buttons.addWidget(copy); layout.addLayout(buttons)
        dialog.exec()

    def closeEvent(self, event):
        if self.updates.installing and self.library.job['running']:
            event.ignore(); QTimer.singleShot(1000, self.close); return   # the update closes the APP once the job is done
        if self.busy or self.library.job['running']:
            self.uninstall_requested=False
            QMessageBox.information(self, '任务正在进行', '请等待扫描、导出或文件夹切换完成后再退出。'); event.ignore(); return
        if self.pending_ops:
            # Queued keeps and deletions finish first; the window stays responsive and closes by itself.
            self.closing = True; self.toast.show_message(f'正在完成 {self.pending_ops} 项筛选记录…', 'info', 2000)
            event.ignore(); QTimer.singleShot(120, self.close); return
        if self.prompt_on_close and self.session_active and not self.updates.installing and self.close_choice is None:
            box=QMessageBox(self); box.setWindowTitle('关闭工作文件夹')
            box.setText('是否保留当前文件夹的筛选进度？下次启动仍由你选择文件夹。')
            box.setInformativeText('不保留仅重置浏览位置。照片、保留标记和可恢复的删除记录都会保留。')
            keep=box.addButton('保留进度并关闭',QMessageBox.ButtonRole.AcceptRole)
            forget=box.addButton('直接关闭，不保留位置',QMessageBox.ButtonRole.DestructiveRole)
            cancel=box.addButton('取消',QMessageBox.ButtonRole.RejectRole); box.exec()
            if box.clickedButton() not in (keep,forget): event.ignore(); self.closing=False; self.uninstall_requested=False; return
            self.close_choice='keep' if box.clickedButton() is keep else 'forget'
        for timer in (self.updates.timer, self.timer, self.cursor_timer, self.state_timer, self.resize_timer, self.chrome_timer, self.maintenance_timer): timer.stop()
        self.rating_page.shutdown()
        self.closed = True
        self.op_pool.waitForDone()
        for pool in (self.pool, self.image_pool, self.thumb_pool, self.detail_pool): pool.clear(); pool.waitForDone()
        if self.decoders: self.decoders.close()
        if self.cursor_id: self.library.remember('cursor', self.cursor_id if self.close_choice!='forget' else 0)
        self.library.stop_session()
        if self.uninstall_requested:
            try: subprocess.Popen([str(self.uninstaller_path())],cwd=self.uninstaller_path().parent)
            except OSError as exc:
                self.closed=False; self.closing=False; self.uninstall_requested=False
                self.library.start_session()
                self.updates.timer.start(1000); self.maintenance_timer.start(60000); self.poll(); self.prefetch()
                self.error('无法打开卸载向导：'+str(exc)); event.ignore(); return
        event.accept()

COMPOSITING = {'gpu': False}


def configure_compositing(settings, windows=os.name == 'nt'):
    """GPU compositing on Windows: the window is composed with Direct3D 11 through Qt's RHI, and Qt itself falls back
    to WARP software rendering when no GPU device can be created. A launch that never reached a healthy window
    turns it off for the next start. Must run before QApplication exists."""
    COMPOSITING['gpu'] = False
    if 'QT_WIDGETS_RHI' in os.environ and not os.environ.get('PHOTOSIEVE_RHI_AUTO'):
        COMPOSITING['gpu'] = os.environ['QT_WIDGETS_RHI'] == '1'; return   # set by hand for an A/B test
    for name in ('QT_WIDGETS_RHI', 'QT_WIDGETS_RHI_BACKEND', 'PHOTOSIEVE_RHI_AUTO'): os.environ.pop(name, None)
    if not windows or os.environ.get('QT_QPA_PLATFORM', '').startswith(('offscreen', 'minimal')): return
    if settings.value('gpu_pending', False, type=bool):
        settings.setValue('gpu_compositing', False); settings.setValue('gpu_fell_back', True)
    settings.setValue('gpu_pending', False)
    if settings.value('gpu_compositing', False, type=bool):
        os.environ.update(QT_WIDGETS_RHI='1', QT_WIDGETS_RHI_BACKEND='d3d11', PHOTOSIEVE_RHI_AUTO='1')
        settings.setValue('gpu_pending', True); COMPOSITING['gpu'] = True
    settings.sync()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--apply-update':
        from updater import run_helper
        try: return run_helper(sys.argv[2])
        except Exception:
            Path(sys.argv[2]).with_name('helper-error.txt').write_text(traceback.format_exc(), encoding='utf-8'); return 1
    parser = argparse.ArgumentParser(description='照片快筛 · 原生 Qt 桌面版')
    parser.add_argument('--folder'); parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--cache-mb', type=int, help='磁盘预览缓存（MB）；默认取缓存盘可用空间的 5%%，2 到 20 GB')
    parser.add_argument('--self-test-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--runtime-check-output',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--runtime-check-folders',nargs='+',help=argparse.SUPPRESS)
    args = parser.parse_args(); settings = QSettings('PhotoSieve', 'PhotoSieve')
    if os.name=='nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Quietime.PhotoSieve')
    if not args.self_test_output: configure_compositing(settings)
    machine.interface_qos(); machine.fine_timer();crashlog.install_qt_handler()
    crashlog.event('starting_interface',gpu=COMPOSITING['gpu'],profile=machine.PROFILE.describe())
    application = QApplication(sys.argv[:1]); application.setStyleSheet(STYLE); application.setWindowIcon(app_icon())
    if args.self_test_output:
        from diagnostics import run
        return run(application, args.self_test_output)
    # Only a crash skips the finally, so the next start blames the GPU for nothing else (a locked database, say).
    try:
        if args.cache_mb is not None and args.cache_mb < 64: parser.error('缓存至少 64 MB')
        install_directory = Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
        default_directory = install_directory / 'workspaces'
        directory = args.data_dir or default_directory
        try:
            lockpath = default_directory; lockpath.mkdir(parents=True, exist_ok=True)
            application.library_lock = QLockFile(str(lockpath / 'desktop.lock')); application.library_lock.setStaleLockTime(0)
            if not application.library_lock.tryLock(100):
                QMessageBox.information(None, '照片快筛已在运行', '请使用已经打开的窗口。'); return 1
            if args.folder and not args.data_dir:
                directory = default_directory
                existing = Library(directory, args.cache_mb).meta('root')
                if existing and Path(existing).resolve() != Path(args.folder).resolve():
                    directory = directory / 'libraries' / hashlib.sha256(str(Path(args.folder).resolve()).encode()).hexdigest()[:16]
            library = Library(directory, args.cache_mb or settings.value('cache_mb',0,type=int)); window = Window(library, decoders=Decoders(), start_empty=True, prompt_on_close=True, data_root=default_directory)
            screen = application.primaryScreen().availableGeometry(); window.resize(min(1480, screen.width() - 40), min(960, screen.height() - 60))
            window.move(screen.center()-window.rect().center());window.show()
            crashlog.event('waiting_for_folder',gpu=COMPOSITING['gpu'])
            crashlog.install_gui_watchdog(application)
            if args.runtime_check_output:
                from runtime_check import install
                install(window,args.runtime_check_output,args.runtime_check_folders or [])
            elif args.folder: QTimer.singleShot(100, lambda: window.scan(args.folder))
            # Keep gpu_pending until normal exit: an import/render crash can occur
            # well after the first three seconds of an apparently healthy startup.
            if settings.value('gpu_fell_back', False, type=bool):
                settings.setValue('gpu_fell_back', False)
                QTimer.singleShot(800, lambda: window.toast.show_message('上次应用异常退出，已使用 CPU 合成；可在 ⋯ 中重新开启 GPU', 'error', 5000))
        except Exception:
            QMessageBox.critical(None, '无法启动照片快筛', traceback.format_exc()); return 1
        return application.exec()
    finally:
        settings.setValue('gpu_pending', False)


if __name__ == '__main__':
    multiprocessing.freeze_support()   # decoder processes of the packaged app start here
    sys.exit(main())
