from contextlib import closing
import errno
import io
import hashlib
import os
import shutil
import sqlite3
import struct
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

    def mpo(self, name, preview_size, orientation=1, kind=0x010002, mode='RGB'):
        main = Image.new('RGB', (3000, 2000), 'red'); exif = Image.Exif(); exif[274] = orientation
        path = self.base / name
        second = Image.new(mode, preview_size, 'blue' if mode == 'RGB' else 128)
        main.save(path, format='MPO', save_all=True, append_images=[second], exif=exif)
        # Pillow marks extra frames 'Undefined'; cameras mark theirs 'Large Thumbnail (Full HD Equivalent)'.
        with Image.open(path) as im:
            entry = im.mpinfo[0xB002][1]
        data = path.read_bytes(); written = struct.pack('<LLL', 0, entry['Size'], entry['DataOffset'])
        self.assertEqual(data.count(written), 1)
        path.write_bytes(data.replace(written, struct.pack('<LLL', kind, entry['Size'], entry['DataOffset'])))
        return path

    def tiff_raw(self, name, previews):
        """A minimal TIFF-based RAW whose IFD0 and next IFDs each point at one embedded JPEG, as a Sony ARW does."""
        blobs = []
        for size, color in previews:
            out = io.BytesIO(); Image.new('RGB', size, color).save(out, 'JPEG'); blobs.append(out.getvalue())
        step = 2 + 2 * 12 + 4; position = 8 + step * len(blobs); data = b'II*\x00' + struct.pack('<I', 8)
        for index, blob in enumerate(blobs):
            following = 8 + step * (index + 1) if index + 1 < len(blobs) else 0
            data += struct.pack('<HHHIIHHIII', 2, 0x201, 4, 1, position, 0x202, 4, 1, len(blob), following)
            position += len(blob)
        path = self.base / name; path.write_bytes(data + b''.join(blobs))
        return path

    def test_camera_jpeg_preview_is_decoded_from_its_mpf_preview(self):
        path = self.mpo('sony.jpg', (1616, 1077), orientation=6)
        image = Library.decode(path, 1600)
        self.assertEqual(image.size, (1077, 1616))                       # main image's orientation applied
        self.assertEqual(image.getpixel((500, 500))[2] > 200, True)      # pixels come from the embedded preview
        # Letterboxed (different shape) or small previews are ignored; the photo itself is decoded instead.
        # So is a phone's HDR gain map: grey, half size, the same shape, and typed 'Undefined' rather than a thumbnail.
        for name, size, kind, mode in (('letterbox.jpg', (1920, 1080), 0x010002, 'RGB'), ('small.jpg', (640, 427), 0x010002, 'RGB'),
                                       ('iphone.jpg', (1500, 1000), 0, 'L'), ('grey.jpg', (1500, 1000), 0x010002, 'L')):
            image = Library.decode(self.mpo(name, size, kind=kind, mode=mode), 1600)
            self.assertEqual(image.size, (1600, 1067), name); self.assertGreater(image.getpixel((500, 500))[0], 200, name)
        # 100% zoom still decodes the original at full size.
        self.assertEqual(Library.decode(path, 0).size, (2000, 3000))

    def test_draft_box_follows_the_long_side(self):
        path = self.base / 'wide.jpg'; Image.new('RGB', (3600, 2400), 'red').save(path)
        with Image.open(path) as im:
            self.assertEqual(Library.box(im, 800), (800, 533))
            im.draft('RGB', Library.box(im, 800))
            self.assertEqual(im.size, (900, 600))   # a square 800 box would decode 1800 x 1200, four times the pixels

    def test_raw_preview_picks_the_small_embedded_jpeg_of_the_same_shape(self):
        path = self.tiff_raw('a7iv.arw', [((1616, 1080), 'blue'), ((160, 120), 'green'), ((6000, 4000), 'red')])
        data = Library.raw_preview(path, 1600)
        self.assertEqual(Image.open(io.BytesIO(data)).size, (1616, 1080))
        # Letterboxed or too small previews are passed over for the full-size one, which is what LibRaw would return.
        for name, small in (('wide.arw', (1616, 910)), ('old.arw', (640, 427))):
            data = Library.raw_preview(self.tiff_raw(name, [(small, 'blue'), ((6000, 4000), 'red')]), 1600)
            self.assertEqual(Image.open(io.BytesIO(data)).size, (6000, 4000), name)
        # Not a TIFF at all (CR3 and RAF use other containers), or unreadable: LibRaw decides as before.
        other = self.base / 'camera.cr3'; other.write_bytes(b'\x00\x00\x00\x18ftypcrx ' + bytes(64))
        self.assertIsNone(Library.raw_preview(other, 1600))
        self.assertIsNone(Library.raw_preview(self.base / 'missing.arw', 1600))

    @unittest.skipUnless(importlib.util.find_spec('rawpy'), 'optional RAW dependency absent')
    def test_raw_preview_is_decoded_without_libraw_reading_the_full_size_jpeg(self):
        from unittest.mock import MagicMock
        import rawpy
        path = self.tiff_raw('portrait.arw', [((1616, 1080), 'blue'), ((6000, 4000), 'red')])
        raw = MagicMock(); raw.__enter__.return_value = raw; raw.sizes.flip = 6
        with patch('rawpy.imread', return_value=raw):
            image = Library.decode(path, 1600)
        raw.extract_thumb.assert_not_called()
        self.assertEqual(image.size, (1080, 1616)); self.assertGreater(image.getpixel((500, 500))[2], 200)   # LibRaw's flip applied
        # Without a suitable preview LibRaw's largest one is used, decoded at the smallest scale that covers 1600 px.
        out = io.BytesIO(); Image.new('RGB', (6000, 4000), 'red').save(out, 'JPEG')
        raw.extract_thumb.return_value = MagicMock(format=rawpy.ThumbFormat.JPEG, data=out.getvalue()); raw.sizes.flip = 0
        with patch('rawpy.imread', return_value=raw), patch.object(Library, 'raw_preview', return_value=None):
            image = Library.decode(path, 1600)
        self.assertEqual(image.size, (1600, 1067)); self.assertGreater(image.getpixel((500, 500))[0], 200)

    def test_zooming_a_jpeg_does_not_write_a_full_size_copy(self):
        photo = self.lib.list_photos()[0]
        self.assertEqual(self.lib.preview_image(photo['id'], 0).size, (1800, 900))
        self.assertFalse(self.lib.cache_path(Path(photo['path']), photo['size'], photo['mtime'], 0).exists())

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

    def test_a_crashed_decoder_pool_starts_over_with_a_fresh_read_gate(self):
        from concurrent.futures.process import BrokenProcessPool
        from desktop import Decoders
        decoders = Decoders(1); photo = self.lib.photo(self.lib.list_photos()[0]['id'])
        job = (photo['path'], photo['path'], photo['size'], photo['mtime'], str(self.lib.cache), True)   # on a spinning disk
        caller = ThreadPoolExecutor(1); old = None
        try:
            self.assertTrue(caller.submit(decoders, *job).result(30)); old = decoders.gate
            # A decoder dies inside the gate and the broken pool terminates the other: neither slot comes back.
            old.acquire(); old.acquire()
            with self.assertRaises(BrokenProcessPool): decoders.pool.submit(os._exit, 1).result(30)
            with self.assertRaises(ValueError): caller.submit(decoders, *job).result(30)
            self.assertTrue(caller.submit(decoders, *job).result(30))   # the next pool does not wait on the old gate
            self.assertIsNot(decoders.gate, old)
        finally:
            if old is not None and decoders.gate is old: old.release(); old.release()   # let a failed run still shut down
            decoders.close(); caller.shutdown()

    def camera_raw(self, thumb_size=None, flip=6, damaged=False):
        """A mocked LibRaw file with a 3000 x 2000 sensor, holding an embedded JPEG of thumb_size (None: no thumbnail).
        damaged keeps the JPEG's header but cuts its picture data short, as a bad disk sector would."""
        import numpy as np
        from unittest.mock import MagicMock
        import rawpy
        raw = MagicMock(); raw.__enter__.return_value = raw
        raw.sizes.width, raw.sizes.height, raw.sizes.flip = 3000, 2000, flip
        # Like LibRaw, postprocess returns the sensor image already turned by flip.
        raw.postprocess.return_value = np.zeros((3000, 2000, 3) if flip in (5, 6) else (2000, 3000, 3), dtype=np.uint8)
        if thumb_size:
            out = io.BytesIO(); Image.effect_noise(thumb_size, 40).convert('RGB').save(out, 'JPEG')
            data = out.getvalue()[:len(out.getvalue()) // 2] if damaged else out.getvalue()
            raw.extract_thumb.return_value = MagicMock(format=rawpy.ThumbFormat.JPEG, data=data)
        else:
            raw.extract_thumb.side_effect = rawpy.LibRawNoThumbnailError
        return raw

    @unittest.skipUnless(importlib.util.find_spec('rawpy'), 'optional RAW dependency absent')
    def test_raw_focus_detail_uses_the_camera_full_size_jpeg_or_the_sensor(self):
        # A7 IV style: the embedded JPEG covers the sensor, so it is shown, turned by LibRaw's flip.
        raw = self.camera_raw((2990, 1990))
        with patch('rawpy.imread', return_value=raw):
            image = Library.decode(Path('camera.arw'), 0)
        self.assertEqual(image.size, (1990, 2990))
        raw.postprocess.assert_not_called(); self.assertFalse(image.info.get('sensor'))
        # A7 III style (only a 1616 px preview), no thumbnail at all, or a damaged camera JPEG: the full sensor image,
        # marked for the cache.
        for raw in (self.camera_raw((1616, 1077)), self.camera_raw(None), self.camera_raw((2990, 1990), damaged=True)):
            with patch('rawpy.imread', return_value=raw):
                image = Library.decode(Path('camera.arw'), 0)
            self.assertEqual(image.size, (2000, 3000)); self.assertIs(image.info['sensor'], True)
            self.assertNotIn('half_size', raw.postprocess.call_args.kwargs)

    @unittest.skipUnless(importlib.util.find_spec('rawpy'), 'optional RAW dependency absent')
    def test_only_sensor_decodes_are_cached_for_zoom(self):
        (self.photos / 'camera.arw').write_bytes(b'raw')
        self.lib.scan(str(self.photos)); self.wait_job()
        photo = next(p for p in self.lib.list_photos() if p['name'] == 'camera.arw')
        cached = self.lib.cache_path(Path(photo['path']), photo['size'], photo['mtime'], 0)
        # A sensor render cached by an earlier version is not shown in place of the camera JPEG.
        before = hashlib.sha256(f"v2|{photo['path']}|{photo['size']}|{photo['mtime']}|0".encode()).hexdigest()
        Image.new('RGB', (3000, 2000), 'red').save(self.lib.cache / f'{before}.jpg')
        with patch('rawpy.imread', return_value=self.camera_raw((2990, 1990))):
            self.assertEqual(self.lib.preview_image(photo['id'], 0).size, (1990, 2990))
        self.assertFalse(cached.exists())   # decoding the camera JPEG again is as quick as reading a cached copy
        with patch('rawpy.imread', return_value=self.camera_raw((1616, 1077))):
            self.assertEqual(self.lib.preview_image(photo['id'], 0).size, (2000, 3000))
        self.assertTrue(cached.exists())
        # A PNG whose text happens to say 'sensor' is still not copied.
        from PIL.PngImagePlugin import PngInfo
        text = PngInfo(); text.add_text('sensor', 'True')
        Image.new('RGB', (900, 600), 'green').save(self.photos / 'notes.png', pnginfo=text)
        self.lib.scan(str(self.photos)); self.wait_job()
        png = next(p for p in self.lib.list_photos() if p['name'] == 'notes.png')
        self.assertEqual(self.lib.preview_image(png['id'], 0).size, (900, 600))
        self.assertFalse(self.lib.cache_path(Path(png['path']), png['size'], png['mtime'], 0).exists())

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
            # This verifies coordination and intact files, not filesystem latency.
            # Shared Windows runners can spend several seconds creating WAL files.
            self.assertTrue(preview.result(timeout=30))
            self.assertTrue(delete.result(timeout=30)['staged'])
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
