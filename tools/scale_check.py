"""Library scale check: a day of about 100,000 photos.

python tools/scale_check.py [--count 100000]
Measures scanning, rescanning, queue pages, status refresh, and keep / delete / undo latency
on a library that already holds a full day of history. Photos are tiny files; decoding is measured
separately by tools/stress_check.py.
"""
import argparse
import io
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from app import Library


def timed(fn, repeat=1):
    samples = []
    for _ in range(repeat):
        start = time.perf_counter(); result = fn(); samples.append(time.perf_counter() - start)
    return result, samples


def ms(samples):
    samples = sorted(samples)
    return f'median {statistics.median(samples) * 1000:.1f} ms, p95 {samples[int(len(samples) * .95)] * 1000:.1f} ms'


def wait(library):
    while library.job['running']: time.sleep(.02)
    assert library.job['message'] == '已完成', library.job['message']


def run(count):
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary); photos = base / 'photos'; buffer = io.BytesIO()
        Image.new('RGB', (64, 48), 'gray').save(buffer, 'JPEG'); payload = buffer.getvalue()
        start = time.perf_counter()
        for folder in range(100):
            directory = photos / f'card{folder:03}'; directory.mkdir(parents=True)
            for i in range(count // 100): (directory / f'DSC{i:05}.jpg').write_bytes(payload)
        print(f'{count:,} files written in {time.perf_counter() - start:.1f}s')
        library = Library(base / 'data')
        _, t = timed(lambda: (library.scan(str(photos)), wait(library))); print(f'first scan: {t[0]:.1f}s ({count / t[0]:,.0f} files/s)')
        _, t = timed(lambda: (library.scan(str(photos)), wait(library))); print(f'rescan of an indexed library: {t[0]:.1f}s')
        _, t = timed(library.state, 20); print(f'status refresh: {ms(t)}')
        rows, t = timed(lambda: library.list_photos('pending', 0, 'next', 1200), 10); print(f'queue page of 1,200: {ms(t)}')
        middle = rows[600]['id']
        _, t = timed(lambda: library.list_photos('pending', middle + count // 2, 'next', 1200), 10); print(f'queue page deep in the library: {ms(t)}')
        # A day of history: half kept, a third deleted, recorded the way the app records them.
        with library.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM photos ORDER BY id')]
            kept = ids[:count // 2]; deleted = ids[count // 2:count // 2 + count // 3]
            db.executemany('INSERT INTO history(photo_id,before,after,size,mtime) VALUES (?,0,1,0,0)', ((i,) for i in kept))
            db.executemany('UPDATE photos SET status=1 WHERE id=?', ((i,) for i in kept))
            db.executemany("INSERT INTO history(photo_id,before,after,size,mtime,action,backup) VALUES (?,0,0,0,0,'trash','x')", ((i,) for i in deleted))
            db.executemany("INSERT INTO trash_log(photo_id,path,deleted_at,result,backup) VALUES (?,'',0,'staged','x')", ((i,) for i in deleted))
            db.executemany('UPDATE photos SET present=0 WHERE id=?', ((i,) for i in deleted))
        state, t = timed(library.state, 20); print(f'status refresh after a full day ({state["counts"]}): {ms(t)}')
        _, t = timed(lambda: library.list_photos('deleted', 0, 'next', 60), 10); print(f'deleted grid page: {ms(t)}')
        _, t = timed(lambda: library.list_photos('keep', 0, 'next', 1200), 10); print(f'kept queue page: {ms(t)}')
        remaining = library.list_photos('pending', 0, 'next', 1000)
        marks = [timed(lambda p=p: library.mark(p['id'], 1))[1][0] for p in remaining[:500]]
        print(f'keep: {ms(marks)}')
        trashes = [timed(lambda p=p: library.trash(p['id']))[1][0] for p in remaining[500:1000]]
        print(f'delete (move to .photo-sieve-trash): {ms(trashes)}')
        undos = [timed(lambda: library.undo(only_delete=True))[1][0] for _ in range(200)]
        print(f'restore last delete: {ms(undos)}')
        _, t = timed(lambda: Library(base / 'data')); print(f'reopen library: {t[0] * 1000:.0f} ms')
        size = (base / 'data' / 'library.sqlite3').stat().st_size / 1e6; print(f'database size: {size:.1f} MB')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--count', type=int, default=100_000)
    run(parser.parse_args().count)
