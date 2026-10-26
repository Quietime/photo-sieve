"""Phone remote: a small page on the local network that drives screening and grading.

It is off until the user turns it on (更多选项 → 手机遥控…). The desktop then serves one static page
and a JSON API. Every API call must carry the pairing key, which the page reads from the link's
#fragment: browsers never send a fragment to the server, so the key stays out of request lines, logs
and Referer headers, and another site can neither read it nor forge a call with it.

Commands are queued to the interface thread and run through the same handlers as the keyboard
shortcuts, so the phone can do exactly what the keys can and nothing more. Photos are served only for
the current and next couple of photos of the active mode, never by path.
"""
from __future__ import annotations

import hmac
import io
import json
import re
import secrets
import socket
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal

import qr

PAGE_DIR = Path(__file__).resolve().parent / 'assets' / 'remote'
DEFAULT_PORT = 8765
POLL_SECONDS = 20            # one long poll waits at most this long for a change
IDLE_SECONDS = 60            # snapshots stop when no phone has asked for this long
MAX_HANDLERS = 16            # concurrent requests; more get 503 instead of more threads
PHOTO_EDGE = 1600            # the smallest preview edge the desktop already caches
SCREEN_ACTIONS = {'keep', 'delete', 'next', 'restore', 'undo', 'pending'}
GRADE_ACTIONS = {'star', 'previous', 'next', 'undo', 'label'}
STATIC = {'': ('index.html', 'text/html; charset=utf-8'), 'index.html': ('index.html', 'text/html; charset=utf-8'),
          'vendor/gsap.min.js': ('vendor/gsap.min.js', 'text/javascript; charset=utf-8'),
          'app.js': ('app.js', 'text/javascript; charset=utf-8'), 'style.css': ('style.css', 'text/css; charset=utf-8'),
          'manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json')}
ASSET = re.compile(r'^art/([a-z0-9][a-z0-9-]{0,60})\.(png|webp|jpg|svg)$')
TYPES = {'png': 'image/png', 'webp': 'image/webp', 'jpg': 'image/jpeg', 'svg': 'image/svg+xml'}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
       "connect-src 'self'; manifest-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def lan_addresses():
    """IPv4 addresses a phone on the same network can reach, the default-route one first."""
    found = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(('10.255.255.255', 1))   # no packet is sent; this only picks the outgoing interface
            found.append(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    # USB tethering adapters may not be the default route or in the hostname DNS.
    # Enumerate all up IPv4 interfaces so Android's USB network address is offered too.
    try:
        from PySide6.QtNetwork import QNetworkInterface
        for interface in QNetworkInterface.allInterfaces():
            if interface.flags() & QNetworkInterface.InterfaceFlag.IsUp:
                for entry in interface.addressEntries():
                    ip = entry.ip().toString()
                    if re.fullmatch(r'\d+\.\d+\.\d+\.\d+', ip): found.append(ip)
    except (ImportError, RuntimeError):
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    usable = []
    for ip in found:
        if ip not in usable and not ip.startswith(('127.', '169.254.', '0.')):
            usable.append(ip)
    return usable or ['127.0.0.1']


class _Command:
    def __init__(self, action, value):
        self.action = action; self.value = value; self.done = threading.Event(); self.result = None; self.deadline = time.monotonic() + 4.0


class _Inbox(QObject):
    """Lives on the interface thread; server threads hand work over through its queued signals."""
    arrived = Signal(object)
    visitor = Signal()


class RemoteControl(QObject):
    changed = Signal()       # server started/stopped, key reset or a phone connected: refresh the dialog

    def __init__(self, window, settings=None):
        super().__init__(window)
        self.window = window; self.settings = settings or QSettings('PhotoSieve', 'PhotoSieve')
        self.key = self.settings.value('remote/key', '') or ''
        if len(self.key) < 20:
            self.reset_key(save_only=True)
        self.port = int(self.settings.value('remote/port', DEFAULT_PORT) or DEFAULT_PORT)
        self.server = None; self.thread = None; self.instance = secrets.token_hex(8)
        self.cond = threading.Condition(); self.state = {'rev': 0, 'running': False}; self.rev = 0; self.allowed = {}
        self.clients = OrderedDict(); self.last_request = 0.0; self.last_json = ''
        self.photos = OrderedDict(); self.photo_lock = threading.Lock()
        self.inbox = _Inbox(self); self.inbox.arrived.connect(self.run, Qt.ConnectionType.QueuedConnection)
        self.inbox.visitor.connect(self.changed.emit, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self); self.timer.setInterval(50); self.timer.timeout.connect(self.tick)

    # ---- lifecycle (interface thread) -------------------------------------------------------
    @property
    def running(self):
        return self.server is not None

    def start(self):
        if self.server:
            return True
        last_error = None
        for port in (self.port, 0):
            try:
                server = _Server(('', port), _Handler)
                break
            except OSError as error:
                last_error = error
        else:
            raise OSError(f'无法开启手机遥控：{last_error}')
        server.remote = self; self.server = server; self.port = server.server_address[1]
        self.instance = secrets.token_hex(8)
        self.thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.3}, name='phone-remote', daemon=True)
        self.thread.start(); self.settings.setValue('remote/enabled', True)
        self.refresh(force=True); self.timer.start(); self.changed.emit()
        return True

    def stop(self, remember=True):
        server, self.server = self.server, None
        self.timer.stop()
        if server:
            threading.Thread(target=lambda: (server.shutdown(), server.server_close()), daemon=True).start()
        with self.cond:
            self.state = {'rev': self.rev + 1, 'running': False, 'instance': self.instance}; self.rev += 1; self.cond.notify_all()
        if remember:
            self.settings.setValue('remote/enabled', False)
        self.changed.emit()

    def reset_key(self, save_only=False):
        self.key = secrets.token_urlsafe(16)
        self.settings.setValue('remote/key', self.key)
        if not save_only:
            with self.cond:
                self.cond.notify_all()     # pollers holding the old key get 403 on their next call
            self.changed.emit()

    def urls(self, transport='wifi'):
        query = '?via=usb' if transport == 'usb' else ''
        return [f'http://{ip}:{self.port}/{query}#k={self.key}' for ip in lan_addresses()]

    def connected(self, within=IDLE_SECONDS):
        now = time.monotonic()
        return [(ip, now - seen) for ip, seen in self.clients.items() if now - seen < within]

    # ---- snapshots (interface thread) --------------------------------------------------------
    def tick(self):
        if time.monotonic() - self.last_request < IDLE_SECONDS:
            self.refresh()

    def refresh(self, force=False):
        state, allowed = self.snapshot()
        text = json.dumps(state, ensure_ascii=False, sort_keys=True)
        if text == self.last_json and not force:
            return
        self.last_json = text
        with self.cond:
            self.rev += 1; state['rev'] = self.rev; state['running'] = True
            self.state = state; self.allowed = allowed; self.cond.notify_all()

    def snapshot(self):
        w = self.window; library = getattr(w, 'library', None)
        state = {'instance': self.instance, 'session': bool(getattr(w, 'session_active', False)), 'mode': 'grade' if w.rating_active else 'screen',
                 'folder': '', 'busy': bool(w.busy or w.pending_ops or w.job_running()), 'blocked': self.blocked()}
        root = (w.state_data or {}).get('root') or ''
        state['folder'] = Path(root).name or root
        allowed = {}
        if w.rating_active:
            state['grade'] = self.grade_state(allowed)
        else:
            state['screen'] = self.screen_state(allowed)
        return state, {pid: library for pid in allowed}

    def blocked(self):
        from PySide6.QtWidgets import QApplication
        w = self.window
        modal = QApplication.activeModalWidget()
        if modal is not None and not getattr(modal, 'remote_dialog', False):   # the pairing dialog itself is fine
            return '电脑上有对话框，请先在电脑上处理'
        if getattr(w, 'more_panel', None) is not None and w.more_panel.isVisible():
            return '电脑上打开了设置菜单'
        if getattr(w, 'closing', False):
            return '应用正在关闭'
        return ''

    def camera(self, photo):
        service = getattr(self.window, 'metadata_service', None)
        if not service or not photo:
            return ''
        try:
            return service.cache.get(service.key(self.window.library, photo)) or ''
        except (KeyError, TypeError):
            return ''

    def screen_state(self, allowed):
        w = self.window; counts = dict(w.counts or {})
        queue = w.queue if w.view == 'pending' and w.stack.currentIndex() == 0 else []
        photo = queue[0] if queue else None
        for p in queue[:3]:
            allowed[p['id']] = True
        can_undo = bool((w.state_data or {}).get('can_undo'))
        return {
            'view': w.view, 'counts': {k: int(counts.get(k, 0)) for k in ('all', 'pending', 'keep', 'deleted')},
            'photo': {'id': photo['id'], 'name': photo['name'], 'camera': self.camera(photo), 'kept': photo.get('status') == 1} if photo else None,
            'next': [p['id'] for p in queue[1:3]],
            'can': {'keep': w.keep_button.isEnabled(), 'delete': w.delete_button.isEnabled(), 'next': w.next_button.isEnabled(),
                    'restore': w.restore_button.isEnabled(), 'undo': can_undo},
            'message': '' if photo else ('正在读取…' if w.fetching else '全部筛选完毕' if w.session_active else '请先在电脑上打开照片文件夹'),
        }

    def grade_state(self, allowed):
        from workflow_data import COLORS, LABELS
        page = self.window.rating_page; pid = page.pid()
        photo = (page.images[pid][0] if pid in page.images else page.thumb_photos.get(pid)) if pid is not None else None
        info = page.info.get(pid) or {}
        for p in page.ids[page.index:page.index + 3]:
            allowed[p] = True
        label = page.tools.annotations.get(pid, {}).get('label', '') if pid is not None else ''
        return {
            'photo': {'id': pid, 'name': photo['name'] if photo else '', 'camera': self.camera(photo), 'stars': info.get('stars'),
                      'pending': bool(info.get('pending')), 'label': label} if pid is not None else None,
            'index': page.index, 'total': len(page.ids), 'next': list(page.ids[page.index + 1:page.index + 3]),
            'can': {'grade': bool(pid is not None and not page.undoing and not page.listing and page.can_grade()),
                    'previous': page.index > 0, 'next': page.index < len(page.ids) - 1, 'undo': page.undo_button.isEnabled()},
            'labels': [{'key': key, 'text': text, 'color': COLORS.get(key, '')} for key, text in LABELS.items()],
            'message': '' if pid is not None else ('正在读取分级列表…' if page.listing else '没有符合条件的照片'),
        }

    # ---- commands (interface thread) ---------------------------------------------------------
    def run(self, command):
        try:
            command.result = (False, '操作已超时，请重试') if time.monotonic() >= command.deadline else self.execute(command.action, command.value)
        except Exception as error:   # a failing command must never take the remote down
            command.result = (False, f'操作失败：{error}')
        self.refresh()
        command.done.set()

    def execute(self, action, value):
        w = self.window
        reason = self.blocked()
        if reason:
            return False, reason
        if not w.session_active or w.busy or w.job_running():
            return False, '请等待电脑打开照片文件夹'
        if action == 'mode':
            if value not in ('screen', 'grade'):
                return False, '未知模式'
            if (value == 'grade') != w.rating_active:
                w.toggle_rating()
            if (value == 'grade') != w.rating_active:
                # A rating or file change is still being written; switch as soon as it is done.
                self.switch_later(value, time.monotonic() + 4)
                return True, '正在保存上一步，稍后自动切换'
            return True, ''
        if w.rating_active:
            if action not in GRADE_ACTIONS:
                return False, '照片分级中没有这个操作'
            page = w.rating_page
            can = self.grade_state({})['can']
            capability = 'grade' if action in ('star', 'label') else action
            if not can.get(capability):
                return False, '当前无法进行此操作，请等待电脑就绪'
            if action == 'star':
                if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 5:
                    return False, '星级应为 0–5'
                page.grade(value)
            elif action == 'label':
                from workflow_data import LABELS
                if value not in LABELS:
                    return False, '未知颜色标签'
                page.tools.label(value)
            elif action == 'previous':
                page.move(-1)
            elif action == 'next':
                page.move(1)
            else:
                page.undo()
            return True, ''
        if action not in SCREEN_ACTIONS:
            return False, '照片筛选中没有这个操作'
        if action == 'pending':
            if w.view != 'pending':
                w.switch('pending')
            return True, ''
        if w.view != 'pending' or w.stack.currentIndex() != 0:
            return False, '电脑没有停在“未筛选”，请先切回'
        if not self.screen_state({})['can'].get(action):
            return False, '当前无法进行此操作，请等待电脑就绪'
        {'keep': lambda: w.act('keep'), 'delete': lambda: w.act('delete'), 'next': w.next_photo,
         'restore': lambda: w.restore(), 'undo': lambda: w.restore(general=True)}[action]()
        return True, ''

    def switch_later(self, mode, deadline):
        w = self.window
        if (mode == 'grade') == w.rating_active or self.server is None:
            return
        if not self.blocked():
            w.toggle_rating()
        if (mode == 'grade') != w.rating_active:
            if time.monotonic() < deadline:
                QTimer.singleShot(100, self, lambda: self.switch_later(mode, deadline))
            return
        self.refresh()

    # ---- server side (any thread) ------------------------------------------------------------
    def authorised(self, key):
        return bool(key) and hmac.compare_digest(key.encode(), self.key.encode())

    def seen(self, ip):
        self.last_request = time.monotonic()
        new = ip not in self.clients
        self.clients[ip] = self.last_request; self.clients.move_to_end(ip)
        while len(self.clients) > 16:
            self.clients.popitem(last=False)
        if new:
            self.inbox.visitor.emit()

    def wait_state(self, since, timeout):
        deadline = time.monotonic() + timeout
        with self.cond:
            while self.server is not None and self.state.get('rev', 0) == since:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(min(left, 1.0))
            return dict(self.state)

    def submit(self, action, value, timeout=4.0):
        command = _Command(action, value); command.deadline = time.monotonic() + timeout
        self.inbox.arrived.emit(command)
        if not command.done.wait(timeout):
            return False, '电脑没有及时响应', self.current()
        ok, reason = command.result
        return ok, reason, self.current()

    def current(self):
        with self.cond:
            return dict(self.state)

    def photo_bytes(self, pid, width):
        with self.cond:
            library = self.allowed.get(pid)
        if library is None:
            return None
        width = max(320, min(2048, int(width)))
        cache_key = (id(library), pid, width)
        with self.photo_lock:
            if cache_key in self.photos:
                self.photos.move_to_end(cache_key); return self.photos[cache_key]
        image = library.preview_image(pid, PHOTO_EDGE)
        image = image.convert('RGB') if image.mode != 'RGB' else image.copy()
        image.thumbnail((width, width))
        out = io.BytesIO(); image.save(out, 'JPEG', quality=84, optimize=True, progressive=True)
        data = out.getvalue()
        with self.photo_lock:
            self.photos[cache_key] = data
            while len(self.photos) > 8:
                self.photos.popitem(last=False)
        return data


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False   # on Windows SO_REUSEADDR would let two copies share one port

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.slots = threading.BoundedSemaphore(MAX_HANDLERS)

    def get_request(self):
        connection, address = super().get_request()
        # Commands and long-poll replies are tiny; avoid Nagle/delayed-ACK stalls.
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return connection, address

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return          # a phone that locked its screen mid-poll is normal, not an error
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    server_version = 'PhotoSieveRemote'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):   # requests carry the key; keep them out of every log
        pass

    def handle_one_request(self):
        if not self.server.slots.acquire(blocking=False):
            self.raw_requestline = self.rfile.readline(65537)
            self.close_connection = True
            try:
                self.send_response(503); self.send_header('Content-Length', '0'); self.send_header('Connection', 'close'); self.end_headers()
            except OSError:
                pass
            return
        try:
            super().handle_one_request()
        finally:
            self.server.slots.release()

    # ---- helpers ----
    def send(self, status, body=b'', content_type='application/json; charset=utf-8', extra=(), cache='no-store'):
        self.send_response(status)
        self.send_header('Content-Type', content_type); self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache); self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer'); self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def send_json(self, status, value):
        self.send(status, json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def remote(self):
        remote = self.server.remote
        return remote if remote.server is self.server else None

    def check_key(self, remote):
        if not remote.authorised(self.headers.get('X-Remote-Key', '')):
            self.send_json(403, {'error': '配对码无效，请在电脑上重新扫码'}); return False
        remote.seen(self.client_address[0])
        return True

    # ---- routes ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        remote = self.remote()
        if remote is None:
            return self.send(503)
        parts = urlsplit(self.path); path = parts.path.lstrip('/'); query = parse_qs(parts.query)
        if path in STATIC:
            name, content_type = STATIC[path]
            return self.serve_file(PAGE_DIR / name, content_type, name.endswith('.html'))
        match = ASSET.match(path)
        if match:
            import theme_pack
            name = f'{match.group(1)}.{match.group(2)}'
            found = theme_pack.find(f'remote/{name}') or PAGE_DIR / 'art' / name
            return self.serve_file(found, TYPES[match.group(2)], False, cache=True)
        if path == 'api/state':
            if not self.check_key(remote):
                return
            try:
                since = int(query.get('since', ['0'])[0])
            except ValueError:
                since = 0
            return self.send_json(200, remote.wait_state(since, POLL_SECONDS))
        if path == 'api/photo':
            if not self.check_key(remote):
                return
            try:
                pid = int(query.get('id', [''])[0]); width = int(query.get('w', ['1280'])[0])
            except ValueError:
                return self.send_json(400, {'error': '参数无效'})
            try:
                data = remote.photo_bytes(pid, width)
            except Exception as error:
                return self.send_json(500, {'error': f'预览读取失败：{error}'})
            if data is None:
                return self.send_json(404, {'error': '这张照片已不在当前位置'})
            return self.send(200, data, 'image/jpeg')
        self.send_json(404, {'error': '没有这个页面'})

    def do_POST(self):
        remote = self.remote()
        if remote is None:
            return self.send(503)
        if urlsplit(self.path).path != '/api/act':
            return self.send_json(404, {'error': '没有这个操作'})
        if not self.check_key(remote):
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = -1
        if not 0 < length <= 2048:
            return self.send_json(400, {'error': '请求无效'})
        try:
            body = json.loads(self.rfile.read(length).decode('utf-8'))
            action = body['action']; value = body.get('value')
            if not isinstance(action, str):
                raise ValueError
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return self.send_json(400, {'error': '请求无效'})
        ok, reason, state = remote.submit(action, value)
        self.send_json(200, {'ok': ok, 'reason': reason, 'state': state})

    def serve_file(self, path, content_type, page, cache=False):
        try:
            data = path.read_bytes()
        except OSError:
            return self.send_json(404, {'error': '没有这个文件'})
        extra = [('Content-Security-Policy', CSP), ('X-Frame-Options', 'DENY')] if page else []
        self.send(200, data, content_type, extra, 'max-age=86400' if cache else 'no-store')


def qr_pixmap(text, size, dark='#141416', light='#F4F0E6'):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QPainter, QPixmap
    grid = qr.matrix(text); count = len(grid) + 8
    pixmap = QPixmap(size, size); pixmap.fill(QColor(light))
    painter = QPainter(pixmap); painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(dark))
    cell = size / count
    for r, row in enumerate(grid):
        for c, on in enumerate(row):
            if on:
                painter.drawRect(QRectF((c + 4) * cell, (r + 4) * cell, cell + .02, cell + .02))
    painter.end()
    return pixmap


def open_dialog(window):
    """更多选项 → 手机遥控…: turn the remote on or off and show the pairing QR code."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout
    from ui_shell import QDialog
    remote = window.remote
    dialog = QDialog(window); dialog.remote_dialog = True; dialog.setWindowTitle('手机遥控'); dialog.setMinimumSize(560, 470)
    layout = QVBoxLayout(dialog); layout.setContentsMargins(24, 20, 24, 24); layout.setSpacing(14)
    switch = QCheckBox('开启手机遥控（同一 Wi‑Fi 下用手机代替键盘）'); layout.addWidget(switch)
    transport = QComboBox(); transport.addItem('Wi-Fi 无线连接', 'wifi'); transport.addItem('安卓 USB 有线连接', 'usb')
    transport.setAccessibleName('连接方式'); transport.setCurrentIndex(1 if remote.settings.value('remote/transport','wifi') == 'usb' else 0); layout.addWidget(transport)
    body = QHBoxLayout(); body.setSpacing(20); layout.addLayout(body, 1)
    code = QLabel(); code.setFixedSize(220, 220); code.setAccessibleName('配对二维码'); body.addWidget(code, 0, Qt.AlignmentFlag.AlignTop)
    side = QVBoxLayout(); side.setSpacing(10); body.addLayout(side, 1)
    steps = QLabel('1. 手机连上与电脑相同的 Wi‑Fi\n2. 用相机或微信扫描左侧二维码\n3. 默认大按键控制器，看电脑即可操作；点“预览”查看照片\n也可预览照片：上滑保留、下滑删除；分级时点星级打分')
    steps.setWordWrap(True); side.addWidget(steps)
    address = QComboBox(); address.setAccessibleName('电脑地址'); side.addWidget(address)
    link = QLabel(); link.setObjectName('caption'); link.setWordWrap(True); link.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); side.addWidget(link)
    clients = QLabel(); clients.setWordWrap(True); side.addWidget(clients)
    side.addStretch()
    note = QLabel('首次开启时 Windows 可能询问防火墙，请允许“专用网络”。配对码只保存在本机；更换后旧手机需要重新扫码。跨网络使用可配合 Tailscale 等虚拟局域网。')
    note.setObjectName('caption'); note.setWordWrap(True); layout.addWidget(note)
    buttons = QHBoxLayout(); layout.addLayout(buttons)
    copy = window.button('复制链接', lambda: QGuiApplication.clipboard().setText(current_url()))
    reset = window.button('更换配对码', lambda: (remote.reset_key(), refresh()))
    buttons.addWidget(copy); buttons.addWidget(reset); buttons.addStretch(); buttons.addWidget(window.button('完成', dialog.accept, 'primary'))

    def current_url():
        urls = remote.urls(transport.currentData()); index = max(0, min(address.currentIndex(), len(urls) - 1))
        return urls[index] if remote.running else ''

    def refresh():
        on = remote.running
        switch.blockSignals(True); switch.setChecked(on); switch.blockSignals(False)
        usb = transport.currentData() == 'usb'
        steps.setText('1. 安卓手机用数据线连接电脑\n2. 手机设置 → 个人热点／网络共享 → 开启 USB 网络共享\n3. 在下方选择 USB 网卡的电脑地址，扫码打开控制器\n无需 USB 调试；若地址未出现，稍候会自动刷新。' if usb else '1. 手机与电脑连接同一 Wi-Fi\n2. 手机相机扫描二维码\n3. 默认大按键控制器，也可点预览查看照片')
        ips = [u.split('/')[2] for u in remote.urls(transport.currentData())]
        if [address.itemText(i) for i in range(address.count())] != ips:
            selected = address.currentText()
            address.blockSignals(True); address.clear(); address.addItems(ips)
            if selected in ips: address.setCurrentText(selected)
            address.blockSignals(False)
        address.setEnabled(on and len(ips) > 1); copy.setEnabled(on); reset.setEnabled(True)
        url = current_url()
        if on:
            code.setPixmap(qr_pixmap(url, 220)); link.setText(url.split('#')[0] + ' （链接含配对码，请勿分享）')
        else:
            code.clear(); code.setText('未开启'); code.setAlignment(Qt.AlignmentFlag.AlignCenter); link.setText('')
        seen = remote.connected()
        clients.setText('已连接：' + '，'.join(f'{ip}（{int(age)} 秒前）' for ip, age in seen) if seen else ('等待手机扫码…' if on else ''))

    def toggled(enabled):
        try:
            remote.start() if enabled else remote.stop()
        except OSError as error:
            window.error(str(error))
        refresh()

    transport.currentIndexChanged.connect(lambda _: (remote.settings.setValue('remote/transport',transport.currentData()), refresh()))
    switch.toggled.connect(toggled); address.currentIndexChanged.connect(lambda _: refresh())
    remote.changed.connect(refresh)
    timer = QTimer(dialog); timer.timeout.connect(refresh); timer.start(2000)
    refresh(); dialog.exec(); timer.stop()
    try:
        remote.changed.disconnect(refresh)
    except (RuntimeError, TypeError):
        pass
