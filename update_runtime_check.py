"""Release-only check of an actual GUI, decoders, exit and production updater controller."""
import json
from pathlib import Path
import time
from PySide6.QtCore import QTimer

def install(window,plan_path):
    plan=json.loads(Path(plan_path).read_text('utf-8'));window.prompt_on_close=False
    window.updates.timer.stop();window.warm_count=0;window.scan(plan['folder'])
    timer=QTimer(window);timer.setInterval(50);started=time.monotonic()
    def ready():
        if time.monotonic()-started>90:raise TimeoutError('Update check photos did not load')
        if window.busy or window.importing or any(p.pixmap is None for p in window.photos):return
        timer.stop();window.updates.staged=Path(plan['source'])
        window.updates.idle_seconds=lambda:10
        window.updates.tick()   # The real three-second-idle installation branch.
    timer.timeout.connect(ready);timer.start();window.update_test_timer=timer
