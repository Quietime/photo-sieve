"""Damped springs for interface feedback, stepped on the shared display-rate FrameClock.

A Spring keeps position and velocity, so a new target mid-motion continues from the current speed
instead of restarting a curve: a quick hover-out-and-in, or a press during a bounce, stays continuous.
Each spring is integrated with fixed 1/480 s substeps (stable for stiff springs at any refresh rate)
and leaves the clock as soon as it settles, so idle windows never animate.
"""
from __future__ import annotations
import math
import time
from PySide6.QtCore import QObject
import theme


class Spring(QObject):
    STEP = 1 / 480

    def __init__(self, owner, stiffness=320.0, damping=26.0, value=0.0, changed=None):
        super().__init__(owner)
        self.stiffness = stiffness; self.damping = damping
        self.value = self.target = float(value); self.velocity = 0.0
        self.changed = changed; self.running = False; self.last = 0.0

    @staticmethod
    def damping_for(stiffness, ratio):
        """Damping coefficient for a damping ratio: 1 = no overshoot, 0.5 = one visible bounce."""
        return 2 * ratio * math.sqrt(stiffness)

    def set_target(self, target, kick=0.0):
        """Move toward target; kick adds velocity (units per second), e.g. for a release bounce."""
        self.target = float(target); self.velocity += kick
        if theme.REDUCED_MOTION:
            self.snap(target); return
        if not self.running and (abs(self.value - self.target) > 1e-4 or abs(self.velocity) > 1e-3):
            self.running = True; self.last = time.perf_counter(); theme.clock().add(self, self.parent())

    def snap(self, value):
        self.value = self.target = float(value); self.velocity = 0.0
        self.stop(); self.notify()

    def notify(self):
        if self.changed:
            self.changed()

    # FrameClock protocol ---------------------------------------------------------------------
    def advance(self, now):
        dt = min(0.05, max(0.0, now - self.last)); self.last = now
        while dt > 1e-9:
            h = min(dt, self.STEP); dt -= h
            force = -self.stiffness * (self.value - self.target) - self.damping * self.velocity
            self.velocity += force * h; self.value += self.velocity * h
        if abs(self.value - self.target) < 1e-3 and abs(self.velocity) < 1e-2:
            self.value = self.target; self.velocity = 0.0; self.stop()
        self.notify()

    def stop(self):
        if self.running:
            self.running = False; theme.clock().remove(self)

    complete = stop          # FrameClock ends a motion whose step raised with complete()
