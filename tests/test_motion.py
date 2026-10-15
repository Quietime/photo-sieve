import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

import theme

application = QGuiApplication.instance() or QApplication([])


def pump(condition, seconds=3):
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        application.processEvents()
        if condition():
            return True
        time.sleep(.001)
    return False


class FrameClockTest(unittest.TestCase):
    def setUp(self):
        # Test the animation clock independently of Windows accessibility and
        # settings loaded by other Window tests in the same interpreter.
        preference=patch('theme.REDUCED_MOTION',False)
        preference.start();self.addCleanup(preference.stop)

    def test_runs_to_the_end_and_finishes_once(self):
        owner = QObject(); values = []; finished = []
        motion = theme.animate(owner, 120, values.append, theme.EASE, lambda: finished.append(1), 10, 20)
        self.assertEqual(values[0], 10)   # the start value is applied at once, as QVariantAnimation does
        self.assertTrue(pump(lambda: finished))
        self.assertEqual(values[-1], 20); self.assertEqual(finished, [1])
        self.assertEqual(values, sorted(values))
        self.assertEqual(motion.duration(), 120)
        self.assertFalse(theme.clock().timer.isActive())

    def test_stop_is_silent_and_jump_finishes(self):
        owner = QObject(); finished = []
        stopped = theme.animate(owner, 500, lambda v: None, done=lambda: finished.append('stopped'))
        stopped.stop(); pump(lambda: False, .1)
        self.assertEqual(finished, [])
        values = []
        jumped = theme.animate(owner, 500, values.append, done=lambda: finished.append('jumped'))
        jumped.setCurrentTime(jumped.duration())
        self.assertEqual(values[-1], 1.0); self.assertEqual(finished, ['jumped'])

    def test_ticks_at_the_display_refresh_rate(self):
        screen = application.primaryScreen()
        self.assertEqual(theme.FrameClock.interval(), max(2, int(1000 / max(30.0, min(screen.refreshRate() or 60.0, 500.0)))))
        owner = QObject(); values = []
        theme.animate(owner, 200, values.append)
        pump(lambda: len(values) > 1 and values[-1] == 1.0)
        # Qt's own animation timer would give about 13 frames in 200 ms; the clock follows the screen instead.
        self.assertGreaterEqual(len(values), 200 // theme.FrameClock.interval() // 2)

    def test_a_failing_step_ends_only_its_own_motion(self):
        owner = QObject(); finished = []; values = []
        def broken(value):
            if value > 0: raise ZeroDivisionError   # fails on a clock tick, after its first frame
        with patch('theme.traceback.print_exc'):
            theme.animate(owner, 60, broken, done=lambda: finished.append('broken'))
            theme.animate(owner, 60, values.append, done=lambda: finished.append('healthy'))
            self.assertTrue(pump(lambda: len(finished) == 2))
        self.assertEqual(sorted(finished), ['broken', 'healthy']); self.assertEqual(values[-1], 1.0)
        self.assertTrue(pump(lambda: not theme.clock().timer.isActive()))

    def test_stopped_motions_are_released(self):
        owner = QObject()
        for _ in range(20): theme.animate(owner, 500, lambda v: None).stop()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(owner.findChildren(theme.Motion), [])

    def test_240hz_and_display_migration_retime_running_animation(self):
        from types import SimpleNamespace
        owner=QObject();rate=[60.0]
        owner.screen=lambda:SimpleNamespace(refreshRate=lambda:rate[0])
        motion=theme.animate(owner,1000,lambda _:None)
        try:
            self.assertEqual(theme.clock().timer.interval(),16)
            rate[0]=240.0;theme.clock().tick()
            self.assertEqual(theme.FrameClock.interval(owner),4)
            self.assertEqual(theme.clock().timer.interval(),4)
            rate[0]=144.0;theme.clock().tick()
            self.assertEqual(theme.clock().timer.interval(),6)
        finally:motion.stop()
        self.assertFalse(theme.clock().timer.isActive())

    def test_destroyed_owner_ends_its_motion(self):
        owner = QObject(); values = []
        theme.animate(owner, 300, values.append)
        owner.deleteLater(); pump(lambda: False, .05)
        pump(lambda: not theme.clock().motions)
        self.assertEqual(theme.clock().motions, [])


if __name__ == '__main__':
    unittest.main()
