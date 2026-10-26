"""Phone remote: pairing key, command hand-off to the interface thread, photo access and the QR code."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import qr
import remote_control

application = QApplication.instance() or QApplication([])


def enabled(value=True):
    return SimpleNamespace(isEnabled=lambda: value)


class FakePage:
    def __init__(self):
        self.ids = [11, 12, 13, 14]; self.index = 0; self.images = {}; self.info = {11: {'stars': 2}}
        self.thumb_photos = {pid: {'id': pid, 'name': f'G{pid}.JPG', 'path': f'/x/G{pid}.JPG', 'size': 1, 'mtime': 1} for pid in self.ids}
        self.tools = SimpleNamespace(annotations={11: {'label': 'Red'}}, label=Mock())
        self.undoing = False; self.listing = False; self.undo_button = enabled(True)
        self.grade = Mock(); self.undo = Mock()
    def pid(self): return self.ids[self.index] if self.ids else None
    def can_grade(self): return True
    def move(self, delta): self.index = max(0, min(len(self.ids) - 1, self.index + delta))


class FakeWindow:
    def __init__(self, library):
        self.library = library; self.rating_active = False; self.busy = False; self.pending_ops = 0
        self.session_active = True; self.state_data = {'root': '/photos/火星演唱会', 'can_undo': True}
        self.counts = {'all': 3, 'pending': 3, 'keep': 0, 'deleted': 0}; self.view = 'pending'; self.fetching = False
        self.queue = [{'id': pid, 'name': f'P{pid}.JPG', 'path': f'/x/P{pid}.JPG', 'size': 1, 'mtime': 1, 'status': 0} for pid in (1, 2, 3, 4)]
        self.stack = SimpleNamespace(currentIndex=lambda: 0)
        self.keep_button = self.delete_button = self.next_button = self.restore_button = enabled(True)
        self.closing = False; self.rating_page = FakePage(); self.acts = []
    def job_running(self): return False
    def act(self, action): self.acts.append(action); self.queue.pop(0); self.counts['pending'] -= 1
    def next_photo(self): self.acts.append('next')
    def restore(self, general=False): self.acts.append('undo' if general else 'restore')
    def switch(self, view): self.acts.append('switch:' + view); self.view = view
    def toggle_rating(self): self.rating_active = not self.rating_active


class FakeLibrary:
    def __init__(self): self.calls = []
    def preview_image(self, pid, edge):
        self.calls.append((pid, edge)); return Image.new('RGB', (1600, 1067), (pid * 20 % 255, 80, 120))


class RemoteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / 'p.ini'), QSettings.Format.IniFormat)
        self.settings.setValue('remote/port', 0)
        self.library = FakeLibrary(); self.window = FakeWindow(self.library)
        self.remote = remote_control.RemoteControl(None, self.settings); self.remote.window = self.window
        self.remote.start(); self.base = f'http://127.0.0.1:{self.remote.port}'

    def tearDown(self):
        self.remote.stop(); application.processEvents(); time.sleep(0.05); self.temp.cleanup()

    def call(self, path, key=None, body=None, method=None):
        """Run one request on a thread while this (interface) thread keeps the Qt event loop turning."""
        result = {}
        def go():
            headers = {} if key is None else {'X-Remote-Key': key}
            data = None
            if body is not None:
                data = body if isinstance(body, bytes) else json.dumps(body).encode(); headers['Content-Type'] = 'application/json'
            request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    result['status'] = response.status; result['body'] = response.read(); result['headers'] = response.headers
            except urllib.error.HTTPError as error:
                result['status'] = error.code; result['body'] = error.read(); result['headers'] = error.headers
        thread = threading.Thread(target=go); thread.start()
        deadline = time.monotonic() + 12
        while thread.is_alive() and time.monotonic() < deadline:
            application.processEvents(); time.sleep(0.005)
        thread.join(1)
        return result

    def json(self, path, **kw):
        r = self.call(path, **kw); return r['status'], json.loads(r['body'] or b'null')

    def test_page_is_public_but_every_api_call_needs_the_key(self):
        page = self.call('/')
        self.assertEqual(page['status'], 200); self.assertIn(b'app.js', page['body'])
        self.assertIn("script-src 'self'", page['headers']['Content-Security-Policy'])
        self.assertEqual(self.call('/app.js')['status'], 200)
        for key in (None, '', 'wrong-key-wrong-key-wrong'):
            self.assertEqual(self.call('/api/state', key=key)['status'], 403)
            self.assertEqual(self.call('/api/act', key=key, body={'action': 'keep'})['status'], 403)
        self.assertEqual(self.window.acts, [])
        status, state = self.json('/api/state', key=self.remote.key)
        self.assertEqual(status, 200); self.assertEqual(state['mode'], 'screen'); self.assertEqual(state['folder'], '火星演唱会')
        self.assertEqual(state['screen']['photo']['id'], 1); self.assertEqual(state['screen']['next'], [2, 3])

    def test_no_path_escapes_and_no_arbitrary_photo(self):
        for path in ('/art/../remote_control.py', '/../remote_control.py', '/assets/remote/app.js', '/art/x.py', '/%2e%2e/qr.py'):
            self.assertEqual(self.call(path)['status'], 404, path)
        key = self.remote.key
        self.assertEqual(self.call('/api/photo?id=99', key=key)['status'], 404)
        self.assertEqual(self.call('/api/photo?id=abc', key=key)['status'], 400)
        self.assertEqual(self.library.calls, [])

    def test_photo_is_a_resized_jpeg_of_an_allowed_photo(self):
        r = self.call('/api/photo?id=2&w=800', key=self.remote.key)
        self.assertEqual(r['status'], 200); self.assertEqual(r['headers']['Content-Type'], 'image/jpeg')
        import io
        image = Image.open(io.BytesIO(r['body'])); self.assertEqual(max(image.size), 800)
        self.call('/api/photo?id=2&w=800', key=self.remote.key)
        self.assertEqual(self.library.calls, [(2, remote_control.PHOTO_EDGE)])   # second request served from memory

    def test_screen_commands_run_through_the_window_handlers(self):
        key = self.remote.key
        status, reply = self.json('/api/act', key=key, body={'action': 'keep'})
        self.assertEqual(status, 200); self.assertTrue(reply['ok'])
        self.assertEqual(reply['state']['screen']['photo']['id'], 2)        # the reply already carries the new state
        for action in ('delete', 'next', 'restore', 'undo'):
            self.assertTrue(self.json('/api/act', key=key, body={'action': action})[1]['ok'])
        self.assertEqual(self.window.acts, ['keep', 'delete', 'next', 'restore', 'undo'])
        status, reply = self.json('/api/act', key=key, body={'action': 'star', 'value': 3})
        self.assertFalse(reply['ok']); self.assertEqual(len(self.window.acts), 5)

    def test_screen_commands_refuse_when_the_desktop_left_the_pending_view(self):
        self.window.view = 'keep'; self.remote.refresh()
        reply = self.json('/api/act', key=self.remote.key, body={'action': 'delete'})[1]
        self.assertFalse(reply['ok']); self.assertEqual(self.window.acts, [])
        self.assertTrue(self.json('/api/act', key=self.remote.key, body={'action': 'pending'})[1]['ok'])
        self.assertEqual(self.window.acts, ['switch:pending'])

    def test_grading_commands_and_validation(self):
        key = self.remote.key
        reply = self.json('/api/act', key=key, body={'action': 'mode', 'value': 'grade'})[1]
        self.assertTrue(reply['ok']); self.assertEqual(reply['state']['mode'], 'grade')
        grade = reply['state']['grade']
        self.assertEqual(grade['photo']['stars'], 2); self.assertEqual(grade['photo']['label'], 'Red'); self.assertEqual(grade['total'], 4)
        self.assertTrue(self.json('/api/act', key=key, body={'action': 'star', 'value': 4})[1]['ok'])
        self.window.rating_page.grade.assert_called_once_with(4)
        for bad in (6, -1, '3', True, None, 2.5):
            self.assertFalse(self.json('/api/act', key=key, body={'action': 'star', 'value': bad})[1]['ok'], bad)
        self.assertEqual(self.window.rating_page.grade.call_count, 1)
        self.assertTrue(self.json('/api/act', key=key, body={'action': 'label', 'value': 'Blue'})[1]['ok'])
        self.assertFalse(self.json('/api/act', key=key, body={'action': 'label', 'value': '../x'})[1]['ok'])
        self.window.rating_page.tools.label.assert_called_once_with('Blue')
        reply = self.json('/api/act', key=key, body={'action': 'next'})[1]
        self.assertEqual(reply['state']['grade']['index'], 1)
        self.assertFalse(self.json('/api/act', key=key, body={'action': 'keep'})[1]['ok'])
        self.assertEqual(self.window.acts, [])

    def test_blocked_while_a_settings_menu_is_open(self):
        self.window.more_panel = SimpleNamespace(isVisible=lambda: True)
        reply = self.json('/api/act', key=self.remote.key, body={'action': 'keep'})[1]
        self.assertFalse(reply['ok']); self.assertIn('设置菜单', reply['reason']); self.assertEqual(self.window.acts, [])

    def test_disabled_or_busy_commands_report_rejection(self):
        self.window.keep_button = enabled(False)
        self.assertFalse(self.remote.execute('keep', None)[0])
        self.assertEqual(self.window.acts, [])
        self.window.busy = True
        self.assertFalse(self.remote.execute('next', None)[0])
        self.window.busy = False
        self.window.rating_active = True
        self.window.rating_page.listing = True
        self.assertFalse(self.remote.execute('star', 3)[0])
        self.window.rating_page.grade.assert_not_called()

    def test_usb_links_and_local_motion_asset(self):
        from urllib.parse import urlsplit
        link = urlsplit(self.remote.urls('usb')[0])
        self.assertEqual(link.query, 'via=usb')
        self.assertEqual(link.fragment, 'k=' + self.remote.key)
        self.assertEqual(urlsplit(self.remote.urls()[0]).query, '')
        self.assertEqual(self.call('/vendor/gsap.min.js')['status'], 200)
        self.assertEqual(self.call('/vendor/../remote_control.py')['status'], 404)

    def test_old_server_revision_reconnects_without_long_poll_delay(self):
        before = time.monotonic()
        result = self.remote.wait_state(self.remote.rev + 100, 2)
        self.assertLess(time.monotonic() - before, .1)
        self.assertEqual(result['instance'], self.remote.instance)

    def test_expired_queued_command_cannot_run_late(self):
        command = remote_control._Command('delete', None)
        command.deadline = time.monotonic() - 1
        self.remote.run(command)
        self.assertTrue(command.done.is_set())
        self.assertFalse(command.result[0])
        self.assertEqual(self.window.acts, [])

    def test_bad_bodies_are_rejected(self):
        key = self.remote.key
        for body in (b'', b'not json', b'{"value": 1}', b'{"action": 5}', json.dumps({'action': 'keep', 'pad': 'x' * 3000}).encode()):
            self.assertEqual(self.call('/api/act', key=key, body=body, method='POST')['status'], 400, body[:20])
        self.assertEqual(self.window.acts, [])

    def test_long_poll_wakes_on_change_and_times_out_otherwise(self):
        key = self.remote.key; rev = self.json('/api/state', key=key)[1]['rev']
        old = remote_control.POLL_SECONDS; remote_control.POLL_SECONDS = 0.6
        try:
            started = time.monotonic(); same = self.json(f'/api/state?since={rev}', key=key)[1]
            self.assertEqual(same['rev'], rev); self.assertGreater(time.monotonic() - started, 0.5)
            remote_control.POLL_SECONDS = 5
            def change():
                self.window.counts['keep'] = 9
            threading.Timer(0.3, lambda: None).start()
            started = time.monotonic()
            result = {}
            def poll():
                with urllib.request.urlopen(urllib.request.Request(f'{self.base}/api/state?since={rev}', headers={'X-Remote-Key': key}), timeout=10) as r:
                    result['state'] = json.loads(r.read())
            thread = threading.Thread(target=poll); thread.start()
            changed = False
            while thread.is_alive() and time.monotonic() - started < 8:
                if not changed and time.monotonic() - started > 0.3:
                    change(); changed = True
                application.processEvents(); time.sleep(0.01)
            thread.join(1)
            self.assertGreater(result['state']['rev'], rev); self.assertEqual(result['state']['screen']['counts']['keep'], 9)
            self.assertLess(time.monotonic() - started, 3)
        finally:
            remote_control.POLL_SECONDS = old

    def test_reset_key_locks_out_the_old_one(self):
        old = self.remote.key; self.remote.reset_key()
        self.assertNotEqual(old, self.remote.key); self.assertGreaterEqual(len(self.remote.key), 20)
        self.assertEqual(self.call('/api/state', key=old)['status'], 403)
        self.assertEqual(self.call('/api/state', key=self.remote.key)['status'], 200)
        self.assertEqual(self.settings.value('remote/key'), self.remote.key)

    def test_stop_releases_the_port_and_remembers_the_choice(self):
        port = self.remote.port; self.remote.stop()
        self.assertFalse(self.settings.value('remote/enabled', True, type=bool))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=0.5)
            except (urllib.error.URLError, ConnectionError, OSError):
                break
            time.sleep(0.05)
        else:
            self.fail('server still answering after stop')
        self.assertTrue(self.remote.urls()[0].endswith('#k=' + self.remote.key))

    def test_pairing_link_qr_decodes(self):
        import cv2
        import numpy as np
        for url in self.remote.urls() + ['http://192.168.100.200:8765/#k=' + 'A' * 22]:
            m = np.array(qr.matrix(url), dtype=np.uint8)
            img = cv2.resize((np.pad(1 - m, 4, constant_values=1) * 255).astype(np.uint8), None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST)
            self.assertEqual(cv2.QRCodeDetectorAruco().detectAndDecode(img)[0], url)


if __name__ == '__main__':
    unittest.main()
