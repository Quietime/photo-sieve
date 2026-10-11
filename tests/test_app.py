from contextlib import closing
import errno
import io
import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
import importlib.util
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import multiprocessing
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from app import Library, LibraryError, build_preview, lower_priority


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.photos = self.base / '中文照片'
        self.photos.mkdir()
        Image.new('RGB', (1800, 900), 'red').save(self.photos / '01照片.jpg')
        shutil.copy2(self.photos / '01照片.jpg', self.photos / '02副本.jpg')
        Image.new('RGB', (500, 700), 'blue').save(self.photos / '03照片.png')
        (self.photos / '04损坏.jpg').write_bytes(b'broken')
        self.lib = Library(self.base / 'data')
        self.lib.scan(str(self.photos))
        self.wait_job()

    def tearDown(self):
        self.temp.cleanup()

    def assertFails(self, status, fn, *args, **kwargs):
        with self.assertRaises(LibraryError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.status_code, status)

    def wait_job(self):
        deadline = time.monotonic() + 10
        while self.lib.job['running'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.lib.job['running'])
        self.assertEqual(self.lib.job['message'], '已完成')

    def test_review_preview_undo_and_originals_unchanged(self):
        original = {p.name: p.read_bytes() for p in self.photos.iterdir()}
        photos = self.lib.list_photos()
        self.assertEqual(len(photos), 4)
        photo_id = photos[0]['id']
        image = Image.open(io.BytesIO(self.lib.preview(photo_id, 1600)))
        self.assertEqual(image.size, (1600, 800))
        self.lib.mark(photo_id, 1)
        self.assertEqual(self.lib.state()['counts']['keep'], 1)
        self.assertEqual(self.lib.list_photos('pending')[0]['id'], photos[1]['id'])
        self.assertEqual(self.lib.list_photos('keep')[0]['id'], photo_id)
        self.assertEqual(self.lib.undo()['id'], photo_id)
        self.assertEqual(self.lib.state()['counts']['keep'], 0)
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.photos.iterdir()})

    def test_rescan_keeps_marks_but_resets_changed_and_hides_missing(self):
        photos = self.lib.list_photos()
        self.lib.mark(photos[0]['id'], 1)
        self.lib.mark(photos[2]['id'], -1)
        self.lib.scan(str(self.photos))
        self.wait_job()
        self.assertEqual(self.lib.photo(photos[0]['id'])['status'], 1)
        Image.new('RGB', (123, 321), 'green').save(self.photos / '01照片.jpg')
        self.assertFails(409, self.lib.mark, photos[0]['id'], -1)
        (self.photos / '03照片.png').unlink()
        self.lib.scan(str(self.photos))
        self.wait_job()
        self.assertEqual(self.lib.photo(photos[0]['id'])['status'], 0)
        self.assertEqual(len(self.lib.list_photos()), 3)

    def test_broken_photo_reports_why_it_cannot_be_previewed(self):
        broken = self.lib.list_photos()[-1]
        self.assertFails(422, self.lib.preview, broken['id'], 1600)
        self.assertEqual(len(self.lib.list_photos()), 4)

    def test_input_validation(self):
        self.assertFails(400, self.lib.list_photos, 'invalid')
        self.assertFails(400, self.lib.list_photos, 'pending', 0, 'sideways')
        self.assertFails(404, self.lib.preview, 999, 1600)
        self.assertFails(409, self.lib.scan, str(self.base))
        self.assertFails(400, self.lib.scan, str(self.base / 'missing'))

    def test_restart_cursor_and_marks(self):
        photo = self.lib.list_photos()[0]
        self.lib.mark(photo['id'], 1)
        reopened = Library(self.lib.directory)
        self.assertEqual(reopened.meta('cursor'), str(photo['id']))
        self.assertEqual(reopened.photo(photo['id'])['status'], 1)

    def test_delete_restore_bytes_and_restart(self):
        photo = self.lib.list_photos()[0]
        source = Path(photo['path'])
        original = source.read_bytes()
        self.assertTrue(self.lib.trash(photo['id'])['staged'])
        self.assertFalse(source.exists())
        backups = list((source.parent / '.photo-sieve-trash').glob('*.jpg'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(len(self.lib.list_photos()), 3)
        reopened = Library(self.lib.directory)
        result = reopened.undo()
        self.assertTrue(result['restored'])
        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(backups[0].exists())
        self.assertEqual(len(reopened.list_photos()), 4)

    def test_deleted_files_not_reimported_and_multiple_undo(self):
        photos = self.lib.list_photos()
        self.lib.trash(photos[0]['id'])
        self.lib.trash(photos[1]['id'])
        self.lib.scan(str(self.photos))
        self.wait_job()
        self.assertEqual(len(self.lib.list_photos()), 2)
        self.assertEqual(self.lib.undo()['id'], photos[1]['id'])
        self.assertEqual(self.lib.undo()['id'], photos[0]['id'])
        self.assertEqual(len(self.lib.list_photos()), 4)

    def test_undo_does_not_overwrite_conflicting_file(self):
        photo = self.lib.list_photos()[0]
        source = Path(photo['path'])
        self.lib.trash(photo['id'])
        source.write_bytes(b'new unrelated photo')
        self.assertFails(409, self.lib.undo)
        self.assertEqual(source.read_bytes(), b'new unrelated photo')
        source.unlink()
        self.assertTrue(self.lib.undo()['restored'])

    def test_failed_delete_preserves_photo_and_scan_blocks_delete(self):
        photo = self.lib.list_photos()[0]
        with patch('app.os.rename', side_effect=PermissionError('readonly disk')):
            self.assertFails(409, self.lib.trash, photo['id'])
        self.assertTrue(Path(photo['path']).exists())
        self.assertEqual(len(self.lib.list_photos()), 4)
        self.lib.job['running'] = True
        self.assertFails(409, self.lib.trash, photo['id'])
        self.lib.job['running'] = False

    def test_crash_after_move_recovers_undo_record(self):
        photo = self.lib.list_photos()[0]
        source = Path(photo['path'])
        stash = source.parent / '.photo-sieve-trash'
        stash.mkdir()
        backup = stash / 'crash.jpg'
        with self.lib.connect() as db:
            db.execute('INSERT INTO trash_log(photo_id,path,deleted_at,result,backup) VALUES (?,?,?,?,?)',
                       (photo['id'], str(source), time.time(), 'started', str(backup)))
        source.rename(backup)
        reopened = Library(self.lib.directory)
        self.assertEqual(len(reopened.list_photos()), 3)
        self.assertTrue(reopened.undo()['restored'])
        self.assertTrue(source.exists())

    def test_restore_delete_preserves_later_keep_and_supports_multiple_restores(self):
        photos = self.lib.list_photos()
        self.lib.trash(photos[0]['id'])
        self.lib.mark(photos[1]['id'], 1)
        self.lib.trash(photos[2]['id'])
        self.assertEqual(self.lib.undo(only_delete=True)['id'], photos[2]['id'])
        self.assertEqual(self.lib.undo(only_delete=True)['id'], photos[0]['id'])
        self.assertEqual(self.lib.photo(photos[1]['id'])['status'], 1)
        self.assertFails(404, self.lib.undo, only_delete=True)
        self.assertEqual(self.lib.undo()['id'], photos[1]['id'])

    def test_deleted_queue_previews_original_location_and_specific_restore(self):
        photos = self.lib.list_photos()
        self.lib.preview(photos[0]['id'], 1600)
        self.lib.trash(photos[0]['id'])
        self.lib.trash(photos[1]['id'])
        self.assertEqual(self.lib.state()['counts']['deleted'], 2)
        rows = self.lib.list_photos('deleted')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['path'], photos[0]['path'])
        self.assertTrue(Path(rows[0]['backup']).is_file())
        self.assertTrue(rows[0]['deleted'])
        self.assertTrue(self.lib.preview(photos[0]['id'], 1600))
        self.assertTrue(self.lib.undo(only_delete=True, photo_id=photos[0]['id'])['restored'])
        self.assertTrue(Path(photos[0]['path']).is_file())
        self.assertFalse(Path(photos[1]['path']).is_file())
        reopened = Library(self.lib.directory)
        self.assertEqual(len(reopened.list_photos('deleted')), 1)
        self.assertEqual(reopened.undo(only_delete=True)['id'], photos[1]['id'])

    def test_copy_kept_preserves_tree_originals_and_excludes_output_from_scan(self):
        source = self.lib.list_photos()[0]
        nested = self.photos / '子目录'
        nested.mkdir()
        shutil.copy2(source['path'], nested / Path(source['path']).name)
        self.lib.scan(str(self.photos))
        self.wait_job()
        selected = [p for p in self.lib.list_photos() if p['name'] == source['name']]
        for p in selected:
            self.lib.mark(p['id'], 1)
        destination = self.photos / '精选输出'
        self.lib.export_kept(str(destination), 'copy')
        self.wait_job()
        self.assertEqual(self.lib.export_summary()['done'], 2)
        self.assertEqual((destination / source['name']).read_bytes(), Path(source['path']).read_bytes())
        self.assertEqual((destination / '子目录' / source['name']).read_bytes(), Path(source['path']).read_bytes())
        self.assertTrue(Path(source['path']).exists())
        self.lib.scan(str(self.photos))
        self.wait_job()
        self.assertEqual(len(self.lib.list_photos()), 5)
        self.lib.export_kept(str(destination), 'copy')
        self.wait_job()
        summary = self.lib.export_summary()
        self.assertEqual(summary['failed'], 2)
        self.assertEqual(summary['done'], 0)
        self.assertIn('未覆盖', summary['errors'][0]['error'])

    def test_move_kept_cross_volume_preserves_bytes_and_reports_destination(self):
        photos = self.lib.list_photos()
        selected = [photos[0], photos[2]]
        originals = {p['id']: Path(p['path']).read_bytes() for p in selected}
        for p in selected:
            self.lib.mark(p['id'], 1)
        destination = self.base / '第二块磁盘'
        with patch('app.os.rename' if os.name=='nt' else 'app.os.link', side_effect=OSError(errno.EXDEV, 'different volume')):
            self.lib.export_kept(str(destination), 'move')
            self.wait_job()
        self.assertEqual(self.lib.export_summary()['done'], 2)
        for p in selected:
            self.assertFalse(Path(p['path']).exists())
            self.assertEqual((destination / p['name']).read_bytes(), originals[p['id']])
        self.assertEqual(len(self.lib.list_photos()), 2)
        self.assertEqual(len(self.lib.list_photos('deleted')), 0)
        reopened = Library(self.lib.directory)
        self.assertEqual(reopened.export_summary()['destination'], str(destination))
        self.assertEqual(reopened.export_summary()['done'], 2)

    def test_move_conflict_never_overwrites_or_removes_original(self):
        photo = self.lib.list_photos()[0]
        self.lib.mark(photo['id'], 1)
        target = self.base / '已有文件夹'
        target.mkdir()
        (target / photo['name']).write_bytes(b'other important file')
        self.lib.export_kept(str(target), 'move')
        self.wait_job()
        self.assertEqual(self.lib.export_summary()['failed'], 1)
        self.assertEqual((target / photo['name']).read_bytes(), b'other important file')
        self.assertTrue(Path(photo['path']).is_file())

    def test_copy_failure_cleans_partial_file_and_preserves_source(self):
        photo = self.lib.list_photos()[0]
        self.lib.mark(photo['id'], 1)
        target = self.base / '磁盘已满'
        def fail_copy(reader, writer, length):
            writer.write(b'partial')
            raise OSError('disk full')
        with patch('app.shutil.copyfileobj', side_effect=fail_copy):
            self.lib.export_kept(str(target), 'copy')
            self.wait_job()
        self.assertEqual(self.lib.export_summary()['failed'], 1)
        self.assertFalse((target / photo['name']).exists())
        self.assertTrue(Path(photo['path']).exists())

    def test_export_validation(self):
        self.assertFails(400, self.lib.export_kept, str(self.base / 'out'))
        photo = self.lib.list_photos()[0]
        self.lib.mark(photo['id'], 1)
        self.assertFails(400, self.lib.export_kept, str(self.photos))
        self.assertFails(400, self.lib.export_kept, str(self.lib.directory / 'inside'))
        self.assertFails(400, self.lib.export_kept, str(self.base / 'out'), 'invalid')

    def test_upgrade_first_release_preserves_keep_deleted_and_restore(self):
        old_dir=self.base/'旧版资料库'
        old_dir.mkdir()
        photo=self.lib.list_photos()[0]
        original=Path(photo['path'])
        backup=original.parent/'.photo-sieve-trash'/'old.jpg'
        backup.parent.mkdir()
        original.rename(backup)
        other=self.lib.list_photos()[1]
        with closing(sqlite3.connect(old_dir/'library.sqlite3')) as db, db:
            db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE photos(id INTEGER PRIMARY KEY,path TEXT UNIQUE,size INTEGER,mtime INTEGER,status INTEGER,present INTEGER,seen TEXT,digest TEXT,error TEXT);
                CREATE TABLE history(seq INTEGER PRIMARY KEY,photo_id INTEGER,before INTEGER,after INTEGER,size INTEGER,mtime INTEGER,action TEXT,backup TEXT);
                CREATE TABLE trash_log(id INTEGER PRIMARY KEY,photo_id INTEGER,path TEXT,deleted_at REAL,result TEXT,backup TEXT);''')
            db.execute('INSERT INTO meta VALUES (?,?)', ('root',str(self.photos)))
            db.execute('INSERT INTO photos(id,path,size,mtime,status,present) VALUES (?,?,?,?,0,0)', (1,str(original),photo['size'],photo['mtime']))
            db.execute('INSERT INTO photos(id,path,size,mtime,status,present) VALUES (?,?,?,?,1,1)', (2,other['path'],other['size'],other['mtime']))
            db.execute("INSERT INTO history VALUES (1,1,0,0,?,?,'trash',?)", (photo['size'],photo['mtime'],str(backup)))
            db.execute("INSERT INTO trash_log VALUES (1,1,?,?,'staged',?)", (str(original),time.time(),str(backup)))
        upgraded=Library(old_dir)
        self.assertEqual(upgraded.photo(2)['status'],1)
        self.assertEqual(upgraded.list_photos('deleted')[0]['backup'],str(backup))
        self.assertEqual(upgraded.undo(only_delete=True)['id'],1)
        self.assertTrue(original.is_file())

    def test_upgrade_reuses_first_release_preview_cache(self):
        photo=self.lib.list_photos()[0]
        old_key=hashlib.sha256(f"{photo['path']}|{photo['size']}|{photo['mtime']}|1600".encode()).hexdigest()
        image=self.lib.decode(Path(photo['path']),1600)
        image.save(self.lib.cache/f'{old_key}.jpg',format='JPEG')
        with patch.object(self.lib,'decode',side_effect=AssertionError('must reuse old cache')):
            self.assertTrue(self.lib.preview(photo['id'],1600))

    def test_full_detail_has_native_dimensions_and_thumbnails_reuse_decode(self):
        photo = self.lib.list_photos()[0]
        with patch.object(self.lib, 'decode', wraps=self.lib.decode) as decode:
            self.lib.preview(photo['id'], 320)
            self.lib.preview(photo['id'], 1600)
            self.lib.preview(photo['id'], 320)
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(Image.open(io.BytesIO(self.lib.preview(photo['id'], 0))).size, (1800, 900))

    def test_cached_preview_does_not_wait_for_other_decodes(self):
        photo = self.lib.list_photos()[0]
        expected = self.lib.preview(photo['id'], 1600)
        self.lib.decode_slots.acquire()
        self.lib.decode_slots.acquire()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(self.lib.preview, photo['id'], 1600)
                try:
                    self.assertEqual(result.result(timeout=2), expected)
                finally:
                    self.lib.decode_slots.release()
                    self.lib.decode_slots.release()
        except BaseException:
            raise

    def test_pending_omits_kept_and_works_after_restart(self):
        photos = self.lib.list_photos()
        self.lib.mark(photos[1]['id'], 1)
        self.assertNotIn(photos[1]['id'], [p['id'] for p in self.lib.list_photos('pending', photos[0]['id'])])
        reopened = Library(self.lib.directory)
        self.assertNotIn(photos[1]['id'], [p['id'] for p in reopened.list_photos('pending', photos[0]['id'])])

    def test_preview_image_returns_decoded_preview_without_second_decode(self):
        photo = self.lib.list_photos()[0]
        image = self.lib.preview_image(photo['id'], 1600)
        self.assertEqual(image.size, (1600, 800))
        with patch.object(self.lib, 'decode', side_effect=AssertionError('cached preview must be reused')):
            self.assertEqual(self.lib.preview_image(photo['id'], 1600).size, (1600, 800))
            self.assertEqual(Image.open(io.BytesIO(self.lib.preview(photo['id'], 320))).size, (320, 160))

    def test_nearly_sized_preview_is_not_resampled(self):
        source = self.base / 'embedded.jpg'
        Image.new('RGB', (1616, 1080), 'white').save(source)
        self.assertEqual(Library.decode(source, 1600).size, (1616, 1080))

    def mpo(self, name, preview_size, orientation=1):
        main = Image.new('RGB', (3000, 2000), 'red'); exif = Image.Exif(); exif[274] = orientation
        path = self.base / name
        main.save(path, format='MPO', save_all=True, append_images=[Image.new('RGB', preview_size, 'blue')], exif=exif)
        return path

    def test_camera_jpeg_preview_is_decoded_from_its_mpf_preview(self):
        path = self.mpo('sony.jpg', (1616, 1077), orientation=6)
        image = Library.decode(path, 1600)
        self.assertEqual(image.size, (1077, 1616))                       # main image's orientation applied
        self.assertEqual(image.getpixel((500, 500))[2] > 200, True)      # pixels come from the embedded preview
        # Letterboxed (different shape) or small previews are ignored; the photo itself is decoded instead.
        for name, size in (('letterbox.jpg', (1920, 1080)), ('small.jpg', (640, 427))):
            image = Library.decode(self.mpo(name, size), 1600)
            self.assertEqual(image.size, (1600, 1067)); self.assertGreater(image.getpixel((500, 500))[0], 200)
        # 100% zoom still decodes the original at full size.
        self.assertEqual(Library.decode(path, 0).size, (2000, 3000))

    def test_spinning_disk_reads_happen_inside_the_gate_and_decode_after(self):
        events = []
        class Gate:
            def __enter__(self): events.append('enter')
            def __exit__(self, *args): events.append('exit')
        source = Path(self.lib.list_photos()[0]['path'])
        original_read = Path.read_bytes
        def read_bytes(path):
            events.append('read'); return original_read(path)
        with patch.object(Path, 'read_bytes', read_bytes):
            gated = Library.decode(source, 1600, Gate())
        self.assertEqual(events, ['enter', 'read', 'exit'])
        self.assertEqual(gated.tobytes(), Library.decode(source, 1600).tobytes())
        self.lib.rotational = True
        self.assertIs(self.lib.gate(), self.lib.read_gate)
        self.lib.rotational = False
        self.assertIsNone(self.lib.gate())

    def test_decoder_process_builds_previews_and_reports_failures(self):
        photos = self.lib.list_photos()
        with ProcessPoolExecutor(1, multiprocessing.get_context('spawn'), lower_priority) as pool:
            self.lib.offload = lambda *job: pool.submit(build_preview, *job).result() or True
            with patch.object(self.lib, 'decode', side_effect=AssertionError('decoding must happen in the decoder process')):
                self.assertEqual(self.lib.preview_image(photos[0]['id'], 1600).size, (1600, 800))
                self.assertEqual(Image.open(io.BytesIO(self.lib.preview(photos[2]['id'], 320))).size, (229, 320))
            self.assertFails(422, self.lib.preview, photos[3]['id'], 1600)
        self.lib.offload = lambda *job: False   # decoder processes unavailable: decode in this thread instead
        self.assertEqual(self.lib.preview_image(photos[1]['id'], 1600).size, (1600, 800))
        self.lib.offload = None

    @unittest.skipUnless(importlib.util.find_spec('rawpy'), 'optional RAW dependency absent')
    def test_raw_focus_detail_uses_full_sensor_instead_of_embedded_preview(self):
        import numpy as np
        from unittest.mock import MagicMock
        raw = MagicMock()
        raw.postprocess.return_value = np.zeros((20, 30, 3), dtype=np.uint8)
        raw.__enter__.return_value = raw
        with patch('rawpy.imread', return_value=raw):
            image = self.lib.decode(Path('camera.nef'), 0)
        self.assertEqual(image.size, (30, 20))
        raw.extract_thumb.assert_not_called()
        self.assertNotIn('half_size', raw.postprocess.call_args.kwargs)

    def test_delete_waits_for_active_preview_file_handle(self):
        photo = self.lib.list_photos()[0]
        entered, release = threading.Event(), threading.Event()
        original_decode = self.lib.decode
        def slow_decode(path, edge, gate=None):
            entered.set()
            release.wait(3)
            self.assertTrue(path.exists())
            return original_decode(path, edge, gate)
        with patch.object(self.lib, 'decode', side_effect=slow_decode), ThreadPoolExecutor(max_workers=2) as pool:
            preview = pool.submit(self.lib.preview, photo['id'], 320)
            self.assertTrue(entered.wait(3))
            delete = pool.submit(self.lib.trash, photo['id'])
            self.assertTrue(Path(photo['path']).exists())
            release.set()
            self.assertTrue(preview.result(timeout=5))
            self.assertTrue(delete.result(timeout=5)['staged'])
        self.assertFalse(Path(photo['path']).exists())

    @unittest.skipUnless(importlib.util.find_spec('pillow_heif'), 'optional HEIC dependency absent')
    def test_heic_preview_delete_and_restore(self):
        source = self.photos / '05手机照片.heic'
        Image.new('RGB', (640, 360), 'green').save(source, format='HEIF')
        original = source.read_bytes()
        self.lib.scan(str(self.photos))
        self.wait_job()
        photo = next(p for p in self.lib.list_photos() if p['path'] == str(source))
        payload = self.lib.preview(photo['id'], 1600)
        self.assertEqual(Image.open(io.BytesIO(payload)).size, (640, 360))
        self.lib.trash(photo['id'])
        self.lib.undo()
        self.assertEqual(source.read_bytes(), original)

    def test_100000_index_pagination(self):
        start = time.perf_counter()
        with self.lib.connect() as db:
            db.executemany('INSERT INTO photos(path,size,mtime) VALUES (?,?,?)',
                           ((str(self.photos / f'synthetic-{i:06d}.jpg'), i, 1) for i in range(100000)))
        indexed = time.perf_counter() - start
        start = time.perf_counter()
        rows = self.lib.list_photos(anchor=99000, limit=60)
        query = time.perf_counter() - start
        self.assertEqual(len(rows), 60)
        self.assertGreater(rows[0]['id'], 99000)
        previous = self.lib.list_photos(anchor=rows[0]['id'], direction='prev', limit=60)
        self.assertEqual(len(previous), 60)
        self.assertLess(previous[-1]['id'], rows[0]['id'])
        print(f'\n100000 synthetic entries: insert={indexed:.3f}s; page={query:.3f}s')


if __name__ == '__main__':
    unittest.main()
