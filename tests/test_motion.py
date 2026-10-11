import os
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication

import theme

application = QGuiApplication.instance() or QGuiApplication([])


def pump(condition, seconds=3):
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        application.processEvents()
        if condition():
            return True
        time.sleep(.001)
    return False


class FrameClockTest(unittest.TestCase):
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

    def test_destroyed_owner_ends_its_motion(self):
        owner = QObject(); values = []
        theme.animate(owner, 300, values.append)
        owner.deleteLater(); pump(lambda: False, .05)
        pump(lambda: not theme.clock().motions)
        self.assertEqual(theme.clock().motions, [])


if __name__ == '__main__':
    unittest.main()
