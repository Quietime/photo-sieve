"""End-to-end phone remote check: the real desktop Window serves the page, a phone-sized Chromium drives
screening and grading through it, and the library afterwards must hold exactly those decisions.

usage: python tools/check_remote.py [OUTPUT_DIR]   (needs Node with Playwright; screenshots go to OUTPUT_DIR)
"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from unittest.mock import patch
from PIL import Image, ImageDraw
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from app import Library
from desktop import Window, STYLE
from native_rating import VALUES


class FakeEngine:
    """Linux harness only; Windows uses the real property-store engine."""
    def __init__(self): self.values = {}
    def run(self, path, stars=None, expected=None):
        path = str(path); before = self.values.get(path, 0)
        if stars is not None: self.values[path] = stars
        value = self.values.get(path, 0); return dict(stars=value, value=VALUES[value], before=before, writable=True, reason='')
    def close(self): pass


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else 'remote-check').resolve(); output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([]); app.setStyleSheet(STYLE)
    def until(fn, seconds=45):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if fn(): return
            time.sleep(.01)
        raise TimeoutError('remote check condition')
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp); photos = base / 'concert'; photos.mkdir()
        for i in range(30):
            im = Image.new('RGB', (1200, 800) if i % 4 else (800, 1200), (60 + i * 6, 40 + i * 3, 90)); d = ImageDraw.Draw(im)
            d.ellipse((300, 200, 600, 500), fill=(200, 120 + i * 3, 140)); d.text((40, 40), f'#{i:02}', fill='white')
            im.save(photos / f'IMG_{i:04}.jpg', quality=88)
        lib = Library(base / 'workspace'); lib.scan(photos); until(lambda: not lib.job['running'])
        settings = QSettings(str(base / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('warm_count', 0); settings.setValue('preload_count', 16); settings.setValue('remote/port', 0)
        patches = [patch(m + '.QSettings', return_value=settings) for m in ('desktop', 'update_ui', 'rating_page', 'remote_control')]
        for p in patches: p.start()
        try:
            window = Window(lib, check_updates=False, data_root=base / 'workspace'); window.resize(1366, 768); window.show()
            window.rating_page.engine = FakeEngine() if os.name != 'nt' else window.rating_page.engine
            window.rating_page.auto.setChecked(False)   # the check reads back the stars on the same photo
            until(lambda: window.keep_button.isEnabled())
            window.remote.start(); url = window.remote.urls()[0].replace(window.remote.urls()[0].split('/')[2].split(':')[0], '127.0.0.1', 1)
            env = dict(os.environ)
            if Path('/opt/pw-browsers/chromium').exists() and 'PLAYWRIGHT_CHROMIUM' not in env:
                found = sorted(Path('/opt/pw-browsers').glob('chromium-*/chrome-linux/chrome'))
                if found: env['PLAYWRIGHT_CHROMIUM'] = str(found[-1])
            runner = subprocess.Popen(['node', str(Path(__file__).with_name('remote_e2e.cjs')), url, str(output)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            end = time.monotonic() + 240
            while runner.poll() is None and time.monotonic() < end:
                app.processEvents(); time.sleep(.005)
            if runner.poll() is None: runner.kill(); raise TimeoutError('browser run did not finish')
            out, err = runner.communicate()
            lines = [l for l in out.splitlines() if l.startswith('{')]
            if not lines: raise RuntimeError('no report from the browser run:\n' + out[-2000:] + err[-2000:])
            report = json.loads(lines[-1])
            until(lambda: not window.pending_ops and not window.rating_page.writing)
            window.grab().save(str(output / 'desktop-after.png'))
            counts = lib.state()['counts']
            report['library'] = {k: counts.get(k) for k in ('all', 'pending', 'keep', 'deleted')}
            # Screening via the phone: keep (swipe), delete (swipe), keep (button), delete (button), restore,
            # next, then undo of the last decision. Two keeps and one delete stay after restore; the undo may
            # take back the latest keep, so accept both outcomes but require consistency.
            kept, deleted = counts.get('keep', 0), counts.get('deleted', 0)
            ok = not report['errors'] and kept in (1, 2) and deleted == 1 and counts.get('pending', 0) + kept + deleted == 30
            report['ok'] = ok
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False))
            window.prompt_on_close = False; window.close(); app.processEvents()
            return 0 if ok else 1
        finally:
            for p in reversed(patches): p.stop()


if __name__ == '__main__':
    sys.exit(main())
