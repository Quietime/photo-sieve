"""Deterministic open-app discovery: no network, real waits or global mouse-idle gate."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from PySide6.QtCore import QObject,QSettings
from PySide6.QtWidgets import QApplication
import update_ui

application=QApplication.instance() or QApplication([])

class Window(QObject):
    def __init__(self):
        super().__init__();self.closed=self.busy=self.importing=self.closing=False;self.pending_ops=0
        self.library=SimpleNamespace(job={'running':False});self.more_panel=None;self.messages=[];self.jobs=[];self.deferred=False
        self.toast=SimpleNamespace(show_message=lambda *args:self.messages.append(args))
    def submit(self,fn,ok=lambda _:None,fail=None,**kwargs):
        if self.deferred:self.jobs.append((fn,ok,fail));return
        try:value=fn()
        except Exception as exc:
            if fail:fail(str(exc))
            else:raise
        else:ok(value)

class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.clock=100.;self.wall=1000.
        self.patches=[patch('update_ui.time.monotonic',side_effect=lambda:self.clock),patch('update_ui.time.time',side_effect=lambda:self.wall),
                      patch('update_ui.sys',SimpleNamespace(frozen=True,executable=__import__('sys').executable)),patch('update_ui.QTimer.singleShot'),
                      patch('update_ui.os',SimpleNamespace(name='posix')),patch('update_ui.updater.check_release',return_value=None)]
        self.started=[p.start() for p in self.patches];self.check=self.started[-1]
        self.window=Window();self.controller=update_ui.UpdateController(self.window);self.controller.timer.stop()
        self.controller.settings=QSettings(str(Path(self.temp.name)/'settings.ini'),QSettings.Format.IniFormat)
        self.controller.settings.setValue('automatic_updates',True)
        self.asked=[];self.controller.ask=lambda release:(self.asked.append(release['version']),False)[1]
    def tearDown(self):
        self.controller.timer.stop();self.window.deleteLater()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def advance(self,seconds):self.clock+=seconds;self.wall+=seconds
    def release(self,version='99.0.0'):return {'version':version,'page':'https://example.invalid','notes':'test'}

    def test_open_app_discovers_later_release_every_two_minutes_without_global_idle(self):
        self.controller.tick();self.assertEqual(self.check.call_count,0)
        self.advance(3);self.controller.tick();self.assertEqual(self.check.call_count,1)
        self.check.return_value=self.release();self.controller.idle_seconds=lambda:0
        self.advance(update_ui.CHECK_INTERVAL-1);self.controller.tick();self.assertEqual(self.check.call_count,1)
        self.advance(1);self.controller.tick()
        self.assertEqual(self.check.call_count,2);self.assertEqual(self.asked,['99.0.0'])

    def test_disabled_automatic_updates_never_request_after_due(self):
        self.controller.settings.setValue('automatic_updates',False)
        self.advance(3600);self.controller.startup_check();self.controller.tick()
        self.check.assert_not_called()

    def test_busy_scan_file_operations_and_held_actions_wait_then_offer_without_new_request(self):
        self.check.return_value=self.release();self.window.busy=True
        self.advance(3);self.controller.tick();self.assertIsNotNone(self.controller.offer)
        self.window.busy=False;self.window.pending_ops=2;self.controller.tick();self.assertEqual(self.asked,[])
        self.window.pending_ops=0;self.window.library.job['running']=True;self.controller.tick();self.assertEqual(self.asked,[])
        self.window.library.job['running']=False;self.controller.touch();self.controller.tick();self.assertEqual(self.asked,[])
        self.advance(.2);self.controller.tick();self.assertEqual(self.asked,['99.0.0']);self.check.assert_called_once()

    def test_pending_offer_does_not_duplicate_checks_or_interrupt_modal(self):
        self.check.return_value=self.release();self.advance(3)
        with patch('update_ui.QApplication.activeModalWidget',return_value=object()):
            self.controller.tick();self.advance(600);self.controller.tick()
            self.assertEqual(self.asked,[]);self.check.assert_called_once()
        self.advance(.2);self.controller.tick();self.assertEqual(self.asked,['99.0.0'])

    def test_asking_downloading_and_staged_states_do_not_recheck(self):
        self.advance(600)
        self.controller.asking=True;self.controller.tick();self.controller.asking=False
        self.controller.running=True;self.controller.tick();self.controller.running=False
        self.controller.staged=Path('stage');self.controller.last_activity=self.clock;self.controller.tick()
        self.check.assert_not_called()

    def test_later_defers_same_version_but_newer_release_and_manual_check_are_not_suppressed(self):
        self.check.return_value=self.release();self.advance(3);self.controller.tick();self.assertEqual(self.asked,['99.0.0'])
        self.advance(update_ui.CHECK_INTERVAL);self.controller.tick();self.assertEqual(self.asked,['99.0.0'])
        self.check.return_value=self.release('99.0.1');self.advance(update_ui.CHECK_INTERVAL);self.controller.tick()
        self.assertEqual(self.asked,['99.0.0','99.0.1'])
        self.controller.check();self.assertEqual(self.asked,['99.0.0','99.0.1','99.0.1'])

    def test_same_release_can_be_offered_after_thirty_minute_defer_expires(self):
        self.check.return_value=self.release();self.advance(3);self.controller.tick()
        self.advance(update_ui.DEFER_SECONDS);self.controller.tick()
        self.assertEqual(self.asked,['99.0.0','99.0.0'])

    def test_failures_back_off_and_success_restores_normal_interval(self):
        self.check.side_effect=OSError('offline');self.advance(3)
        self.controller.tick();self.assertEqual(self.controller.next_check,self.clock+60)
        self.advance(59);self.controller.tick();self.assertEqual(self.check.call_count,1)
        self.advance(1);self.controller.tick();self.assertEqual(self.controller.next_check,self.clock+120)
        self.check.side_effect=None;self.advance(120);self.controller.tick()
        self.assertEqual(self.controller.next_check,self.clock+update_ui.CHECK_INTERVAL);self.assertEqual(self.controller.check_failures,0)

    def test_failed_install_version_stays_skipped_automatically_but_manual_can_retry(self):
        self.check.return_value=self.release();self.controller.failed_version='99.0.0';self.advance(3);self.controller.tick()
        self.assertEqual(self.asked,[])
        self.controller.check();self.assertEqual(self.asked,['99.0.0'])

    def test_disabling_inflight_automatic_check_discards_its_offer(self):
        self.window.deferred=True;self.check.return_value=self.release();self.advance(3);self.controller.tick()
        self.controller.set_automatic(False);fn,ok,fail=self.window.jobs.pop();ok(fn())
        self.assertIsNone(self.controller.offer);self.assertEqual(self.asked,[])

    def test_mouse_drag_waits_for_release_but_plain_movement_does_not(self):
        self.check.return_value=self.release();self.advance(3)
        with patch('update_ui.QApplication.mouseButtons',return_value=True):
            self.controller.tick();self.assertEqual(self.asked,[])
        self.controller.idle_seconds=lambda:0;self.controller.tick()
        self.assertEqual(self.asked,['99.0.0'])

    def test_held_windows_key_defers_only_until_release(self):
        self.check.return_value=self.release();self.advance(3)
        keyboard=SimpleNamespace(GetAsyncKeyState=lambda key:0x8000 if key==40 else 0)
        with patch('update_ui.os',SimpleNamespace(name='nt')),patch.object(update_ui.ctypes,'windll',SimpleNamespace(user32=keyboard),create=True):
            self.controller.tick();self.assertEqual(self.asked,[])
            keyboard.GetAsyncKeyState=lambda key:0;self.controller.tick()
        self.assertEqual(self.asked,['99.0.0'])

if __name__=='__main__':unittest.main()
