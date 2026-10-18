"""Concurrent, randomized library stress with an on-disk invariant check after every round.

QT_QPA_PLATFORM=offscreen python tools/check_library_stress.py --photos 3000 --seconds 60 --rounds 3
--raw adds copies of real camera RAW files (NEF, CR2, ARW…) so previews go through LibRaw.

Threads play every part the APP gives them at once: the serialized operation queue (keep, delete,
undo, ratings, XMP, colour labels), library jobs (rescan, batch restore, permanent deletion),
preview decoding in isolated processes, cache maintenance and eviction, and an outside "user" who
copies deleted photos back, removes staged files by hand and edits originals. Only the refusals the
APP shows to users are tolerated. After each round every photo must still be exactly where the
database says it is: no lost originals, no orphaned staged files, no stuck undo, no deadlock.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile
import threading
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from app import Library, LibraryError, TRASH_DIRECTORY, EXTENSIONS
from native_rating import RatingError, VALUES
from rating_records import RatingRecords
from workflow_data import WorkRecords, LABELS
from xmp_sync import sync_out, sync_in, inspect, XmpError

EXPECTED = (LibraryError, RatingError, XmpError)


class Engine:
    """Stands in for the Windows property handler: rewrites metadata bytes in place, like a JPEG handler."""
    def __init__(self):
        self.stars = {}; self.lock = threading.Lock()

    def run(self, path, stars=None, expected=None):
        key = os.path.normcase(str(path))
        with self.lock:
            before = self.stars.get(key, 0)
            if expected is not None and expected != before: raise RatingError('external change')
            if stars is not None:
                with open(path, 'ab') as handle: handle.write(b'\0rating%d' % stars)
                self.stars[key] = stars
            return dict(stars=self.stars.get(key, 0), value=VALUES[self.stars.get(key, 0)], before=before, writable=True, reason='')


def make_photos(root, count, raws, rng):
    formats = [('jpg', 'JPEG'), ('jpg', 'JPEG'), ('jpg', 'JPEG'), ('png', 'PNG'), ('tif', 'TIFF'), ('webp', 'WEBP'), ('bmp', 'BMP'), ('gif', 'GIF')]
    try:
        from pillow_heif import register_heif_opener; register_heif_opener()
        Image.new('RGB', (8, 8)).save(root / 'probe.heic'); (root / 'probe.heic').unlink(); formats.append(('heic', 'HEIF'))
    except Exception:
        pass
    names = []
    for index in range(count):
        folder = root / f'card{index % 7}' / f'day{index % 3}'; folder.mkdir(parents=True, exist_ok=True)
        suffix, kind = formats[index % len(formats)]
        size = (rng.randint(24, 96), rng.randint(24, 96))
        image = Image.new('RGB', size, (index * 37 % 256, index * 91 % 256, index * 13 % 256))
        path = folder / f'IMG_{index:05d}.{suffix}'; image.save(path, kind); names.append(path)
    for index, source in enumerate(raws):
        for copy in range(2):
            path = root / 'raw' / f'RAW_{index}_{copy}{source.suffix}'; path.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, path); names.append(path)
    # A file that is not a photo and a damaged photo must never disturb the others.
    (root / 'card0' / 'notes.txt').write_text('not a photo')
    (root / 'card0' / 'broken.jpg').write_bytes(b'\xff\xd8\xff\xe0 damaged')
    return names


class Stress:
    def __init__(self, base, photos, seconds, seed, decoders):
        self.base = base; self.root = base / 'photos'; self.seconds = seconds; self.rng = random.Random(seed)
        self.library = Library(base / 'workspace', cache_mb=64)
        self.library.start_session()
        self.decoders = decoders; self.library.offload = decoders
        self.records = RatingRecords(self.library); self.work = WorkRecords(self.library); self.engine = Engine()
        self.failures = []; self.counts = {}; self.lock = threading.Lock(); self.op_lock = threading.Lock()
        self.removed_by_hand = set(); self.id_cache = {}
        # A rescan refuses every other action while it runs; space jobs out by library size.
        self.job_pause = (.2, .8) if photos <= 2000 else (2., 6.)

    def count(self, name, outcome='ok'):
        with self.lock: self.counts[name + ':' + outcome] = self.counts.get(name + ':' + outcome, 0) + 1

    def wait_job(self, limit=300):
        end = time.monotonic() + limit
        while self.library.job['running']:
            if time.monotonic() > end: raise TimeoutError('library job did not finish: ' + json.dumps(self.library.job, ensure_ascii=False, default=str))
            time.sleep(.01)

    def ids(self, view='all'):
        """Photo ids by view, re-read at most every 0.3 s: at 20,000 photos a query per action would dominate."""
        now = time.monotonic(); cached = self.id_cache.get(view)
        if cached and now - cached[0] < .3: return cached[1]
        with self.library.connect() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM photos WHERE ' + self.library.query(view))]
        self.id_cache[view] = (now, ids); return ids

    def guarded(self, name, action, expected=EXPECTED):
        try:
            action(); self.count(name)
        except expected:
            self.count(name, 'refused')
        except Exception:
            self.failures.append(f'{name}: {traceback.format_exc()}'); self.count(name, 'FAILED')

    # ---- roles ---------------------------------------------------------------------------------------
    def operations(self, stop):
        """The APP's single operation thread."""
        rng = random.Random(self.rng.random())
        while time.monotonic() < stop:
            pending = self.ids('pending'); every = self.ids('all'); deleted = self.ids('deleted')
            choice = rng.random()
            with self.op_lock:
                if choice < .25 and pending: self.guarded('keep', lambda: self.library.mark(rng.choice(pending), 1))
                elif choice < .50 and every: self.guarded('delete', lambda: self.library.trash(rng.choice(every)))
                elif choice < .60: self.guarded('restore_latest', lambda: self.library.undo(only_delete=True))
                elif choice < .68: self.guarded('undo', lambda: self.library.undo())
                elif choice < .72 and deleted: self.guarded('restore_one', lambda: self.library.undo(only_delete=True, photo_id=rng.choice(deleted)))
                elif choice < .82 and every: self.guarded('rate', lambda: self.records.write(rng.choice(every), rng.randint(0, 5), self.engine))
                elif choice < .85: self.guarded('rating_undo', lambda: self.records.undo(self.engine))
                elif choice < .92 and every: self.guarded('xmp_out', lambda: sync_out(self.work, rng.choice(every), rng.randint(0, 5), rng.choice(list(LABELS))))
                elif choice < .94 and every: self.guarded('xmp_in', lambda: sync_in(self.work, rng.choice(every), self.records, self.engine))
                # Colour labels refuse with a plain ValueError (shown as a toast) while a scan runs.
                elif every: self.guarded('label', lambda: self.work.annotate(rng.sample(every, min(5, len(every))), label=rng.choice(list(LABELS))), (ValueError,))

    def jobs(self, stop):
        """Library jobs: what rescans, batch restore and permanent deletion do while ops are queued."""
        rng = random.Random(self.rng.random())
        while time.monotonic() < stop:
            time.sleep(rng.uniform(*self.job_pause))
            choice = rng.random(); deleted = self.ids('deleted')
            def start(kind, function):
                try:
                    function(); self.wait_job(); self.count(kind)
                    if self.library.job['message'] != '已完成': self.failures.append(f'{kind}: job ended {self.library.job["message"]}')
                except LibraryError: self.count(kind, 'refused')
                except Exception: self.failures.append(f'{kind}: {traceback.format_exc()}')
            if choice < .3: start('rescan', lambda: self.library.scan(self.root))
            elif choice < .75 and deleted: start('batch_restore', lambda: self.library.restore_deleted(rng.sample(deleted, min(len(deleted), rng.randint(1, 20)))))
            elif deleted: start('purge', lambda: self.library.purge_deleted(rng.sample(deleted, min(len(deleted), rng.randint(1, 10)))))

    def previews(self, stop):
        rng = random.Random(self.rng.random())
        while time.monotonic() < stop:
            every = self.ids('all') + self.ids('deleted')
            if not every: time.sleep(.01); continue
            pid = rng.choice(every); edge = rng.choice((320, 1600, 2400))
            def preview():
                image = self.library.preview_image(pid, edge)
                image.load()
                if max(image.size) > edge * 1.06: raise AssertionError(f'preview {pid} {image.size} larger than {edge}')
            try: preview(); self.count('preview')
            except LibraryError as error:
                # A damaged photo, a file changed outside, or one being moved right now.
                self.count('preview', 'refused')
            except Exception: self.failures.append('preview: ' + traceback.format_exc()); self.count('preview', 'FAILED')

    def maintenance(self, stop):
        rng = random.Random(self.rng.random())
        while time.monotonic() < stop:
            time.sleep(rng.uniform(.05, .3)); choice = rng.random()
            if choice < .5: self.guarded('cleanup', lambda: self.library.cleanup_processed_cache(grace=0))
            elif choice < .9: self.guarded('prune', self.library.prune_cache)
            else: self.guarded('clear_cache', lambda: self.library.clear_preview_cache(include_recognition=True))

    def outsider(self, stop):
        """A person working in Explorer while the APP runs."""
        rng = random.Random(self.rng.random())
        while time.monotonic() < stop:
            time.sleep(rng.uniform(.3, 1.2))
            with self.library.connect() as db:
                trashed = db.execute("SELECT p.path,h.backup FROM history h JOIN photos p ON p.id=h.photo_id WHERE h.action='trash'").fetchall()
                present = [row[0] for row in db.execute('SELECT path FROM photos WHERE present=1')]
            choice = rng.random()
            try:
                if choice < .4 and trashed:
                    original, backup = map(Path, rng.choice(trashed))
                    if backup.is_file() and not original.exists(): shutil.copy2(backup, original); self.count('copy_back')
                elif choice < .6 and trashed:
                    _, backup = rng.choice(trashed); backup = Path(backup)
                    if backup.is_file(): backup.unlink(); self.removed_by_hand.add(str(backup)); self.count('remove_staged')
                elif present:
                    path = Path(rng.choice(present))
                    if path.is_file() and path.suffix.lower() == '.jpg':
                        with open(path, 'ab') as handle: handle.write(b'edited')
                        self.count('edit_original')
            except OSError:
                self.count('outsider', 'raced')

    # ---- invariants ----------------------------------------------------------------------------------
    def check(self, label):
        library = self.library; problems = []
        library.scan(self.root); self.wait_job()
        if library.job['message'] != '已完成': problems.append('final rescan: ' + library.job['message'])
        with library.connect() as db:
            photos = {row['id']: dict(row) for row in db.execute('SELECT * FROM photos')}
            trash = db.execute("SELECT photo_id,backup,size,mtime FROM history WHERE action='trash'").fetchall()
            counts = {row[0]: row[1] for row in db.execute('SELECT status,count(*) FROM photos WHERE present=1 GROUP BY status')}
        state = library.state()
        if state['counts']['all'] != sum(counts.values()) or state['counts']['pending'] != counts.get(0, 0) or state['counts']['keep'] != counts.get(1, 0):
            problems.append(f'counts {state["counts"]} != {counts}')
        if state['counts']['deleted'] != len({row['photo_id'] for row in trash}): problems.append('deleted count disagrees with trash history')
        for pid, row in photos.items():
            if row['present'] and not Path(row['path']).is_file(): problems.append(f'present photo {pid} missing at {row["path"]}')
            if not row['present'] and Path(row['path']).is_file(): problems.append(f'absent photo {pid} still on disk at {row["path"]}')
        known = set()
        for row in trash:
            known.add(os.path.normcase(row['backup']))
            if not Path(row['backup']).is_file(): problems.append(f'trash history {row["photo_id"]} has no staged file {row["backup"]}')
        on_disk = set()
        for folder in self.root.rglob(TRASH_DIRECTORY):
            for path in folder.iterdir(): on_disk.add(os.path.normcase(str(path)))
        for orphan in on_disk - known: problems.append('orphaned staged file ' + orphan)
        # Every photo file in the folder tree is in the library.
        paths = {os.path.normcase(row['path']) for row in photos.values() if row['present']}
        for path in self.root.rglob('*'):
            if path.is_file() and TRASH_DIRECTORY not in path.parts and path.suffix.lower() in EXTENSIONS and os.path.normcase(str(path)) not in paths:
                problems.append('photo on disk not in library ' + str(path))
        leftovers = list((library.directory / 'rating-backups').glob('*')) if (library.directory / 'rating-backups').exists() else []
        if leftovers: problems.append(f'rating backups left behind: {leftovers[:3]}')
        # Undo must always terminate, and "restore latest" must drain every restorable deletion.
        drained = 0
        while True:
            try: library.undo(only_delete=True); drained += 1
            except LibraryError as error:
                if error.status_code != 404: problems.append('restore-latest stuck: ' + error.detail)
                break
            if drained > 100000: problems.append('restore-latest never ends'); break
        with library.connect() as db:
            stuck = db.execute(f"SELECT count(*) FROM history h JOIN photos p ON p.id=h.photo_id WHERE h.action='trash' AND p.present=0").fetchone()[0]
        if stuck: problems.append(f'{stuck} restorable deletions left after draining restore-latest')
        for pid, row in photos.items():
            path = Path(row['path'])
            if path.is_file() and path.suffix.lower() in ('.png', '.gif', '.bmp', '.webp', '.tif'):
                try:
                    with Image.open(path) as image: image.load()
                except Exception as error: problems.append(f'photo {pid} no longer decodes: {error}')
        for xmp in self.root.rglob('*.xmp'):
            try: inspect(xmp)
            except XmpError as error: problems.append(f'{xmp} unreadable after sync: {error}')
        self.failures.extend(f'[{label}] {problem}' for problem in problems)
        return dict(round=label, photos=len(photos), deleted=state['counts']['deleted'], restored_by_drain=drained, problems=len(problems))

    def round(self, label):
        stop = time.monotonic() + self.seconds
        roles = [self.operations, self.jobs, self.maintenance, self.outsider] + [self.previews] * 4
        threads = [threading.Thread(target=role, args=(stop,), daemon=True, name=role.__name__) for role in roles]
        for thread in threads: thread.start()
        for thread in threads:
            thread.join(self.seconds + 600)
            if thread.is_alive():
                frames = sys._current_frames()
                self.failures.append(f'[{label}] {thread.name} deadlocked:\n' + ''.join(traceback.format_stack(frames[thread.ident])))
        self.wait_job()
        return self.check(label)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--photos', type=int, default=3000); parser.add_argument('--seconds', type=float, default=45)
    parser.add_argument('--rounds', type=int, default=2); parser.add_argument('--seed', type=int, default=20261018)
    parser.add_argument('--raw', type=Path, nargs='*', default=[]); parser.add_argument('--decoders', type=int, default=4)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    from desktop import Decoders
    decoders = Decoders(args.decoders)
    with tempfile.TemporaryDirectory(prefix='photosieve-stress-') as temporary:
        base = Path(temporary); (base / 'photos').mkdir()
        started = time.monotonic(); make_photos(base / 'photos', args.photos, args.raw, random.Random(args.seed))
        stress = Stress(base, args.photos, args.seconds, args.seed, decoders)
        stress.library.scan(stress.root); stress.wait_job(); imported = time.monotonic() - started
        results = [stress.round(f'round {index + 1}') for index in range(args.rounds)]
        decoders.close(); stress.library.stop_session()
        report = dict(ok=not stress.failures, photos=args.photos, raw=[str(p) for p in args.raw], setup_seconds=round(imported, 1),
                      rounds=results, operations=dict(sorted(stress.counts.items())), failures=stress.failures[:50], failure_count=len(stress.failures))
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.output: args.output.write_text(text, encoding='utf-8')
    print(text)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
