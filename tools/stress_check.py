"""Held-key stress check: send ↓ or ↑ at keyboard-repeat speed and measure whether review keeps up.

QT_QPA_PLATFORM=offscreen python tools/stress_check.py --rate 40 --seconds 10 --megapixels 24
Photos are synthetic but detailed (noise texture, so JPEG decoding costs as much as a busy real photo).
--source copies one real photo or RAW file instead, to test a specific camera format.
"""
import argparse
import os
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import Qt, QTimer, QObject, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import Library, DECODE_SLOTS
from desktop import Window, Decoders, STYLE

application = QApplication.instance() or QApplication([]); application.setStyleSheet(STYLE)


def until(fn, seconds=600):
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        application.processEvents()
        if fn(): return
        time.sleep(.002)
    raise TimeoutError('timed out')


def rss_mb():
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS'): return int(line.split()[1]) / 1024
    except OSError: pass
    return 0.0


def make_photos(folder, count, megapixels, source):
    if source:
        for i in range(count): shutil.copyfile(source, folder / f'{i:05}{Path(source).suffix.lower()}')
        return
    width = int((megapixels * 1e6 * 1.5) ** .5); height = int(width / 1.5); bases = []
    for seed in range(3):
        coarse = Image.effect_noise((width // 4, height // 4), 70 + seed * 5).convert('L')
        rgb = Image.merge('RGB', [coarse, coarse.rotate(180), coarse.transpose(Image.Transpose.FLIP_LEFT_RIGHT)]).resize((width, height), Image.Resampling.BICUBIC)
        detail = Image.blend(rgb, Image.effect_noise((width, height), 25).convert('RGB'), .25)
        path = folder / f'base{seed}.jpg'; detail.save(path, quality=92); bases.append(path)
    for i in range(count): shutil.copyfile(bases[i % 3], folder / f'{i:05}.jpg')
    for path in bases: path.unlink()


def run(args):
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary); photos = base / 'photos'; photos.mkdir()
        started = time.monotonic(); make_photos(photos, args.count, args.megapixels, args.source)
        size = sum(p.stat().st_size for p in photos.iterdir()) / args.count / 1e6
        print(f'{args.count} photos, {size:.1f} MB each, prepared in {time.monotonic() - started:.0f}s; decoder threads {DECODE_SLOTS}, CPU cores {os.cpu_count()}')
        library = Library(base / 'data'); library.scan(str(photos)); until(lambda: not library.job['running'])
        window = Window(library, check_updates=False, decoders=None if args.threads else Decoders()); window.resize(1480, 960); window.show()
        until(lambda: window.keep_button.isEnabled()); window.activateWindow()
        if args.idle:
            # The user looks at the first photo for a while; background warming builds disk previews ahead.
            time.sleep(0); end = time.monotonic() + args.idle; until(lambda: time.monotonic() > end, args.idle + 5)
            print(f'after {args.idle:.0f}s idle: {len(window.warmed)} disk previews warmed ahead, {len(window.cache)} in memory')
        painted = set(); left = window.photos[0]
        class Painted(QObject):
            def eventFilter(self, watched, event):
                if event.type() == QEvent.Type.Paint and left.photo and left.pixmap: painted.add(left.photo['id'])
                return False
        watcher = Painted(); left.viewport().installEventFilter(watcher)
        stalls = []; last = [time.perf_counter()]
        def beat():
            now = time.perf_counter(); stalls.append(now - last[0] - .004); last[0] = now
        monitor = QTimer(); monitor.setTimerType(Qt.TimerType.PreciseTimer); monitor.timeout.connect(beat); monitor.start(4)
        key = Qt.Key.Key_Down if args.action == 'delete' else Qt.Key.Key_Up
        interval = 1 / args.rate; sent = accepted = right_shown = 0; handler = []; backlog = 0; peak = rss_mb(); trace = []; sampled = 0.0
        begin = time.perf_counter(); due = begin; deadline = begin + args.seconds
        while time.perf_counter() < deadline and window.queue:
            while time.perf_counter() < due: application.processEvents(); time.sleep(.0005)
            due += interval; before = window.queue[0]['id']
            t = time.perf_counter(); QTest.keyClick(window, key); handler.append(time.perf_counter() - t); sent += 1
            if window.queue and window.queue[0]['id'] != before or not window.queue:
                accepted += 1; right_shown += bool(window.photos[1].pixmap)
            backlog = max(backlog, window.pending_ops)
            if time.perf_counter() - sampled >= 1: sampled = time.perf_counter(); trace.append(rss_mb()); peak = max(peak, trace[-1])
        elapsed = time.perf_counter() - begin; monitor.stop()
        flush = time.perf_counter(); until(lambda: not window.pending_ops); flush = time.perf_counter() - flush
        stalls.sort(); handler.sort()
        state = library.state()['counts']; done = state['deleted'] if args.action == 'delete' else state['keep']
        print(f'held {args.action} at {args.rate}/s for {elapsed:.1f}s: {sent} presses, {accepted} accepted ({accepted / elapsed:.1f} photos/s), '
              f'{sent - accepted} waited for a preview')
        print(f'actually painted on screen: {len(painted)}/{accepted} photos; next photo already shown on the right at {right_shown}/{max(1, accepted)} steps')
        print(f'key handler: median {statistics.median(handler) * 1000:.1f} ms, p95 {handler[int(len(handler) * .95)] * 1000:.1f} ms, max {handler[-1] * 1000:.1f} ms')
        print(f'interface thread stall: p95 {max(0, stalls[int(len(stalls) * .95)]) * 1000:.1f} ms, max {max(0, stalls[-1]) * 1000:.1f} ms')
        print(f'background records: peak backlog {backlog}, all written {flush * 1000:.0f} ms after release; {done} recorded in library; peak memory {peak:.0f} MB')
        if len(trace) >= 6:
            print(f'memory over the run: {" → ".join(f"{m:.0f}" for m in trace[::max(1, len(trace) // 8)])} MB')
        assert done == accepted, 'every accepted press must be recorded exactly once'
        window.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=400); parser.add_argument('--megapixels', type=float, default=24)
    parser.add_argument('--rate', type=float, default=40); parser.add_argument('--seconds', type=float, default=8)
    parser.add_argument('--action', choices=('delete', 'keep'), default='delete')
    parser.add_argument('--idle', type=float, default=0, help='seconds to rest on the first photo before holding the key')
    parser.add_argument('--threads', action='store_true', help='decode originals in threads instead of decoder processes')
    parser.add_argument('--source', type=Path, help='copy this real photo or RAW file instead of synthetic JPEGs')
    run(parser.parse_args())
