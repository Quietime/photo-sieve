"""Bounded scheduling, durable metadata reuse, original-file safety and isolated face jobs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from concurrent.futures import Future, TimeoutError
from concurrent.futures.process import BrokenProcessPool
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch
import unittest

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QSettings, Signal, Slot, Qt
from PySide6.QtWidgets import QApplication

from app import Library
import face_service

application = QApplication.instance() or QApplication([])


def until(condition, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError('Face service did not reach the expected state')


class Signals(QObject):
    finished = Signal(object)


class Task(QRunnable):
    def __init__(self, host, work):
        super().__init__()
        self.host = host
        self.work = work
        self.signals = Signals()

    def run(self):
        with self.host.guard:
            self.host.workers += 1
            self.host.max_workers = max(self.host.max_workers, self.host.workers)
        try:
            payload = True, self.work()
        except Exception as error:
            payload = False, str(error)
        finally:
            with self.host.guard:
                self.host.workers -= 1
        self.signals.finished.emit(payload)


class Completion(QObject):
    def __init__(self, host, task, ok, fail):
        super().__init__(host)
        self.host, self.task, self.ok, self.fail = host, task, ok, fail

    @Slot(object)
    def finish(self, payload):
        try:
            (self.ok if payload[0] else self.fail)(payload[1])
        finally:
            self.host.tasks.remove(self)
            self.task = None
            self.deleteLater()


class Stack:
    index = 0
    def currentIndex(self):
        return self.index


class Host(QObject):
    def __init__(self, library):
        super().__init__()
        self.library = library
        self.queue = library.list_photos('pending', limit=100)
        self.generation = 0
        self.stack = Stack()
        self.view = 'pending'
        self.session_active = True
        self.importing = self.busy = self.closed = self.closing = self.cache_clearing = self.rating_active = False
        self.tasks = []
        self.workers = self.max_workers = self.submissions = 0
        self.guard = threading.Lock()

    def submit(self, fn, ok, fail, pool):
        self.submissions += 1
        task = Task(self, fn)
        completion = Completion(self, task, ok, fail)
        self.tasks.append(completion)
        task.signals.finished.connect(completion.finish, Qt.ConnectionType.QueuedConnection)
        pool.start(task)


def crop_result(colour='blue'):
    image = Image.new('RGB', (128, 96), colour)
    output = io.BytesIO()
    image.save(output, 'PNG')
    return {'imagebytes': output.getvalue(), 'faces': [{'box': [.2, .2, .3, .3], 'score': .95}],
            'face_count': 1, 'engine_version': face_service.ENGINE_VERSION, 'is_partial': False,
            'ratio': 1, 'ratio_x': 1, 'ratio_y': 1, 'crop_box': [10, 20, 138, 116],
            'native_size': [128, 96], 'output_size': [128, 96], 'original_size': [400, 300]}


class FaceServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.photos = self.base / '原照片'
        self.photos.mkdir()
        for index in range(8):
            Image.new('RGB', (160, 120), 'grey').save(self.photos / f'{index:03}.jpg')
        self.library = Library(self.base / 'data')
        self.library.scan(self.photos)
        until(lambda: not self.library.job['running'])
        self.host = Host(self.library)
        self.settings = QSettings(str(self.base / 'preferences.ini'), QSettings.Format.IniFormat)
        self.settings.setValue('face_preview', True)
        self.settings.setValue('face_ahead', 0)
        self.settings_patch = patch('face_service.QSettings', return_value=self.settings)
        self.settings_patch.start()
        self.service = face_service.FaceService(self.host)
        self.service.timer.stop()

    def tearDown(self):
        self.service.shutdown()
        self.assertTrue(self.service.pool.waitForDone(1500), 'Shutdown must release the background thread promptly')
        application.processEvents()
        self.service.deleteLater()
        self.host.deleteLater()
        application.processEvents()
        self.settings_patch.stop()
        self.temp.cleanup()

    def test_first_current_result_is_lossless_png_with_registered_cache_and_json_metadata(self):
        first = self.host.queue[0]
        with patch.object(self.service, '_isolated', return_value=crop_result()) as compute:
            self.service.update()
            until(lambda: self.service.result_for(first) is not None)
        result = self.service.result_for(first)
        self.assertEqual(compute.call_count, 1)
        self.assertEqual(result['photo_id'], first['id'])
        self.assertIsInstance(result['imagebytes'], bytes)
        with Image.open(io.BytesIO(result['imagebytes'])) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.size, (128, 96))
            self.assertGreater(image.getpixel((64, 48))[2], 245)
        filename = face_service.cache_path(self.library, first).name
        with self.library.connect() as db:
            row = db.execute('SELECT * FROM cache_index WHERE filename=?', (filename,)).fetchone()
            metadata = db.execute('SELECT faces_json,metadata_json FROM face_metadata').fetchone()
        self.assertEqual((row['photo_id'], row['edge'], row['source_size'], row['source_mtime']),
                         (first['id'], 9001, first['size'], first['mtime']))
        self.assertEqual(json.loads(metadata['metadata_json'])['ratio'], 1)
        self.assertEqual(len(json.loads(metadata['faces_json'])), 1)
        self.assertEqual(self.library.cache_sizes[filename], len(result['imagebytes']))

    def test_existing_disk_crop_reopens_without_any_original_decode(self):
        first = self.host.queue[0]
        with patch.object(self.service, '_isolated', return_value=crop_result()):
            self.service.update()
            until(lambda: self.service.result_for(first) is not None)
        self.service.shutdown()
        self.service.pool.waitForDone(1000)
        self.service = face_service.FaceService(self.host)
        self.service.timer.stop()
        with patch.object(self.service, '_isolated', side_effect=AssertionError('Valid cached PNG must not decode or infer')):
            self.service.update()
            until(lambda: self.service.result_for(first) is not None)
        self.assertEqual(self.service.result_for(first)['face_count'], 1)

    def test_evicted_png_reuses_face_metadata_without_detection(self):
        first = self.host.queue[0]
        with patch.object(self.service, '_isolated', return_value=crop_result()):
            result = self.service._read_or_compute(self.library, first)
        face_service.cache_path(self.library, first).unlink()
        with patch.object(self.service, '_isolated', return_value=crop_result('green')) as regenerate:
            recovered = self.service._read_or_compute(self.library, first)
        self.assertEqual(regenerate.call_args.args[1], result['faces'])
        self.assertTrue(face_service.cache_path(self.library, first).is_file())
        self.assertEqual(recovered['ratio'], 1)

    def test_corrupt_face_png_is_regenerated_from_cached_locations(self):
        first = self.host.queue[0]
        with patch.object(self.service, '_isolated', return_value=crop_result()):
            result = self.service._read_or_compute(self.library, first)
        face_service.cache_path(self.library, first).write_bytes(b'interrupted or corrupt cache')
        with patch.object(self.service, '_isolated', return_value=crop_result('green')) as regenerate:
            recovered = self.service._read_or_compute(self.library, first)
        self.assertEqual(regenerate.call_args.args[1], result['faces'])
        with Image.open(io.BytesIO(recovered['imagebytes'])) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.getpixel((64, 48)), (0, 128, 0))

    def test_negative_result_is_persistent_and_never_decodes_full_original(self):
        first = self.host.queue[0]
        empty = {'imagebytes': None, 'faces': [], 'face_count': 0, 'engine_version': face_service.ENGINE_VERSION}
        with patch.object(self.service, '_isolated', return_value=empty) as compute:
            first_result = self.service._read_or_compute(self.library, first)
            second_result = self.service._read_or_compute(self.library, first)
        self.assertEqual(compute.call_count, 1)
        self.assertEqual(first_result['face_count'], second_result['face_count'])
        self.assertFalse(face_service.cache_path(self.library, first).exists())

    def test_prefetch_is_current_first_single_inflight_and_ram_is_bounded(self):
        self.service.ahead = 500
        order = []
        gate = threading.Event()
        started = threading.Event()
        def compute(photo, known, *args):
            order.append(photo['id'])
            if len(order) == 1:
                started.set()
                gate.wait(3)
            return crop_result()
        with patch.object(self.service, '_isolated', side_effect=compute):
            self.service.update()
            until(started.is_set)
            for _ in range(100):
                self.service.update()
            self.assertEqual(self.host.submissions, 1, 'The service must not enqueue 500 jobs')
            gate.set()
            until(lambda: len(self.service._done) == 8 and not self.service._working)
        self.assertEqual(order, [photo['id'] for photo in self.host.queue])
        self.assertEqual(self.host.max_workers, 1)
        self.assertLessEqual(len(self.service._results), face_service.RAM_RESULTS)
        self.assertIsNotNone(self.service.result_for(self.host.queue[0]), 'Prefetch must not evict the visible current face')

    def test_next_fifty_crops_remain_immediate_ram_hits_without_disk_or_new_jobs(self):
        template = self.host.queue[0]
        self.host.queue = [dict(template, id=index + 1, path=str(self.photos / f'fake-{index}.jpg')) for index in range(51)]
        self.service.ahead = 50
        self.service._synchronize()
        for photo in self.host.queue:
            self.service._remember(face_service.cache_key(photo), dict(crop_result(), photo_id=photo['id'], quality='preview'))
        self.host.queue = self.host.queue[1:]
        with patch.object(self.library, 'read_cache', side_effect=AssertionError('Switch must not read disk')), \
             patch.object(self.service, 'update', side_effect=AssertionError('RAM hit must not schedule work')):
            result = self.service.result_for(self.host.queue[0])
        self.assertEqual(result['photo_id'], self.host.queue[0]['id'])
        self.assertEqual(result['quality'], 'preview')
        self.assertEqual(len(self.service._results), 51)

    def test_ram_byte_budget_retains_nearest_upcoming_before_distant_crops(self):
        self.service.ahead = 7
        self.service._synchronize()
        with patch('face_service.RAM_BYTES', 9):
            for photo in self.host.queue:
                self.service._remember(face_service.cache_key(photo), {'imagebytes': b'123', 'photo_id': photo['id']})
        self.assertEqual(sum(len(result['imagebytes']) for result in self.service._results.values()), 9)
        self.assertEqual(set(self.service._results), {face_service.cache_key(photo) for photo in self.host.queue[:3]})

    def test_current_preview_is_refined_only_after_idle_and_original_cache_replaces_preview(self):
        first = self.host.queue[0]
        calls = []
        def compute(photo, faces, preview_bytes=None, quality='original'):
            calls.append(quality)
            return dict(crop_result(), quality=quality)
        with patch.object(self.service, '_isolated', side_effect=compute):
            self.service.update()
            until(lambda: self.service.result_for(first) is not None)
            self.assertEqual(self.service.result_for(first)['quality'], 'preview')
            self.assertEqual(calls, ['preview'])
            self.service._current_since = time.monotonic() - face_service.REFINE_IDLE / 2
            self.service.update()
            self.assertEqual(calls, ['preview'], 'Active switching must not start original refinement')
            self.service._current_since -= face_service.REFINE_IDLE
            self.service.update()
            until(lambda: self.service.result_for(first).get('quality') == 'original')
        self.assertEqual(calls, ['preview', 'original'])
        with self.library.connect() as db:
            metadata = json.loads(db.execute('SELECT metadata_json FROM face_metadata').fetchone()[0])
        self.assertEqual(metadata['quality'], 'original')

    def test_cancelled_refinement_keeps_preview_and_does_not_emit_stale_original(self):
        first, second = self.host.queue[:2]
        self.service._synchronize()
        for photo in (first, second):
            self.service._remember(face_service.cache_key(photo), dict(crop_result(), quality='preview'))
            self.service._done.add(face_service.cache_key(photo))
        self.service._current_key = face_service.cache_key(first)
        self.service._current_since = time.monotonic() - 1
        started, release = threading.Event(), threading.Event()
        emitted = []
        self.service.changed.connect(lambda pid, result: emitted.append((pid, result.get('quality'))))
        def refine(*args, **kwargs):
            started.set()
            release.wait(2)
            return dict(crop_result(), quality='original')
        with patch.object(self.service, '_read_or_compute', side_effect=refine), \
             patch.object(self.service, '_discard_executor') as terminate:
            self.service.update()
            until(started.is_set)
            self.host.queue = self.host.queue[1:2]
            self.service.update()
            release.set()
            until(lambda: not self.service._working)
            self.assertTrue(terminate.called)
        self.assertEqual(self.service.result_for(first)['quality'], 'preview')
        self.assertNotIn((first['id'], 'original'), emitted)
        self.assertNotIn(face_service.cache_key(first), self.service._refine_failed)

    def test_scanning_samples_pending_rows_and_waits_for_gui_queue(self):
        self.host.queue = []
        self.host.importing = self.host.busy = True
        self.host.stack.index = 3
        self.service.ahead = 2
        seen = []
        with patch.object(self.service, '_isolated', side_effect=lambda photo, faces, *args: (seen.append(photo['id']) or crop_result())):
            self.service.update()
            until(lambda: len(self.service._done) == 3 and not self.service._working)
        self.assertEqual(seen, [photo['id'] for photo in self.library.list_photos('pending', limit=3)])

    def test_bad_photo_during_import_does_not_stall_other_face_jobs(self):
        self.host.queue = []
        self.host.importing = self.host.busy = True
        self.host.stack.index = 3
        self.service.ahead = 2
        first = self.library.list_photos('pending', limit=1)[0]['id']
        seen = []
        def compute(photo, faces, *args):
            seen.append(photo['id'])
            if photo['id'] == first:
                raise ValueError('模拟：损坏照片')
            return crop_result()
        with patch.object(self.service, '_isolated', side_effect=compute):
            self.service.update()
            until(lambda: len(self.service._done) == 3 and not self.service._working)
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[0], first)

    def test_gallery_and_rating_modes_do_not_schedule_new_jobs(self):
        for index in (1, 2, 4, 5):
            self.host.stack.index = index
            self.service.update()
        self.host.stack.index = 0
        self.host.rating_active = True
        self.service.update()
        self.assertEqual(self.host.submissions, 0)

    def test_replaced_file_is_rejected_before_original_or_cached_crop_is_used(self):
        first = self.host.queue[0]
        Path(first['path']).write_bytes(b'new file with a different fingerprint')
        self.service.update()
        until(lambda: self.service.result_for(first) is not None)
        self.assertIn('已改变', self.service.result_for(first)['error'])
        self.assertFalse(face_service.cache_path(self.library, first).exists())

    def test_results_for_changed_row_fingerprint_are_not_reused(self):
        first = self.host.queue[0]
        with patch.object(self.service, '_isolated', return_value=crop_result()):
            self.service.update()
            until(lambda: self.service.result_for(first) is not None)
        self.service.enabled = False
        changed = dict(first, mtime=first['mtime'] + 1)
        self.service.enabled = True
        self.host.stack.index = 4
        self.assertIsNone(self.service.result_for(changed))

    def test_switching_workspace_discards_an_old_inflight_result(self):
        gate = threading.Event()
        started = threading.Event()
        first = self.host.queue[0]
        emitted = []
        self.service.changed.connect(lambda pid, result: emitted.append((pid, result)))
        def compute(photo, faces, *args):
            started.set()
            gate.wait(3)
            return crop_result()
        with patch.object(self.service, '_isolated', side_effect=compute):
            self.service.update()
            until(started.is_set)
            self.host.library = Library(self.base / 'other-data')
            self.host.generation += 1
            self.host.queue = []
            self.service.update()
            gate.set()
            until(lambda: not self.service._working)
        self.assertEqual(emitted, [])
        self.assertEqual(len(self.service._results), 0)
        self.assertTrue(face_service.cache_path(self.library, first).is_file(), 'Completed old-workspace cache remains useful')

    def test_file_lock_keeps_delete_from_moving_an_original_during_face_read(self):
        first = self.host.queue[0]
        started = threading.Event()
        release = threading.Event()
        def compute(photo, known, *args):
            started.set()
            release.wait(3)
            self.assertTrue(Path(photo['path']).is_file())
            return crop_result()
        with patch.object(self.service, '_isolated', side_effect=compute):
            self.service.update()
            until(started.is_set)
            deleted = threading.Event()
            thread = threading.Thread(target=lambda: (self.library.trash(first['id']), deleted.set()))
            thread.start()
            self.assertFalse(deleted.wait(.05))
            self.assertTrue(Path(first['path']).is_file())
            release.set()
            until(deleted.is_set)
            thread.join(1)
        self.assertFalse(Path(first['path']).exists())
        self.assertEqual(self.library.state()['counts']['deleted'], 1)

    def test_crop_cache_is_cleaned_without_erasing_face_metadata_or_originals(self):
        first = self.host.queue[0]
        with patch.object(self.service, '_isolated', return_value=crop_result()):
            self.service._read_or_compute(self.library, first)
        self.library.clear_preview_cache()
        self.assertFalse(face_service.cache_path(self.library, first).exists())
        with self.library.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM face_metadata').fetchone()[0], 1)
        self.assertTrue(Path(first['path']).is_file())

    def test_configuration_clamps_and_persists_without_starting_when_disabled(self):
        self.service.configure(False, 999)
        self.assertFalse(self.service.enabled)
        self.assertEqual(self.service.ahead, 500)
        self.assertEqual(self.host.submissions, 0)
        self.assertFalse(self.settings.value('face_preview', True, type=bool))
        self.assertEqual(self.settings.value('face_ahead', 0, type=int), 500)

    def test_timeout_terminates_isolated_process_without_blocking_gui(self):
        class Process:
            alive = True
            terminated = False
            killed = False
            def is_alive(self): return self.alive
            def terminate(self): self.terminated = True
            def join(self, timeout): pass
            def kill(self): self.killed = True; self.alive = False
        class Executor:
            def __init__(self):
                self.process = Process()
                self._processes = {1: self.process}
                self.calls = []
                self.shutdown_calls = []
            def submit(self, fn, *arguments):
                self.calls.append((fn, arguments))
                return Future()
            def shutdown(self, **arguments): self.shutdown_calls.append(arguments)
        executor = Executor()
        first = self.host.queue[0]
        ticks = []
        from PySide6.QtCore import QTimer
        with patch('face_service.ProcessPoolExecutor', return_value=executor), patch('face_service.JOB_TIMEOUT', .12):
            self.service.update()
            until(lambda: executor.calls)
            QTimer.singleShot(20, lambda: ticks.append(self.service._working))
            until(lambda: self.service.result_for(first) is not None)
        self.assertEqual(ticks, [True], 'The GUI must keep processing events while a decoder is stuck')
        self.assertIn('超过', self.service.result_for(first)['error'])
        self.assertTrue(executor.process.terminated)
        self.assertTrue(executor.process.killed)
        self.assertEqual(executor.shutdown_calls, [{'wait': False, 'cancel_futures': True}])
        self.assertIsNone(self.service._executor)

    def test_shutdown_cancels_hung_worker_and_releases_photo_lock_promptly(self):
        class Process:
            alive = True
            def is_alive(self): return self.alive
            def terminate(self): self.alive = False
            def join(self, timeout): pass
        class Executor:
            def __init__(self): self._processes = {1: Process()}; self.called = False
            def submit(self, *_): self.called = True; return Future()
            def shutdown(self, **_): pass
        executor = Executor()
        first = self.host.queue[0]
        with patch('face_service.ProcessPoolExecutor', return_value=executor):
            self.service.update()
            until(lambda: executor.called)
            started = time.monotonic()
            self.service.shutdown()
            self.assertLess(time.monotonic() - started, .3)
            self.assertTrue(self.service.pool.waitForDone(500))
        lock = self.library.file_locks[first['id'] % len(self.library.file_locks)]
        self.assertTrue(lock.acquire(blocking=False), 'Shutdown must not strand a lock that blocks deletion')
        lock.release()

    def test_crashed_native_worker_does_not_poison_the_next_face_job(self):
        class Executor:
            _processes = {}
            def __init__(self, crash): self.crash = crash; self.stopped = False
            def submit(self, *_):
                future = Future()
                if self.crash: future.set_exception(BrokenProcessPool('simulated native crash'))
                else: future.set_result(crop_result())
                return future
            def shutdown(self, **_): self.stopped = True
        old, new = Executor(True), Executor(False)
        first, second = self.host.queue[:2]
        with patch('face_service.ProcessPoolExecutor', side_effect=[old, new]):
            with self.assertRaisesRegex(ValueError, '已隔离'):
                self.service._read_or_compute(self.library, first)
            self.assertTrue(old.stopped)
            result = self.service._read_or_compute(self.library, second)
            self.assertEqual(result['face_count'], 1)
            self.assertTrue(face_service.cache_path(self.library, second).exists())

    def test_processed_face_png_is_expired_but_metadata_and_original_survive(self):
        first = self.host.queue[0]
        with patch.object(self.service, '_isolated', return_value=crop_result()):
            self.service._read_or_compute(self.library, first)
        path = face_service.cache_path(self.library, first)
        self.library.mark(first['id'], 1)
        os.utime(path, (1, 1))
        self.assertEqual(self.library.cleanup_processed_cache(), 1)
        self.assertFalse(path.exists())
        with self.library.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM face_metadata').fetchone()[0], 1)
        self.assertEqual(self.library.photo(first['id'])['status'], 1)
        self.assertTrue(Path(first['path']).is_file())

    @unittest.skipUnless(face_service.Path(__file__).parents[1].joinpath('assets/face_detection_yunet_2023mar.onnx').is_file(), 'YuNet model required')
    def test_real_spawn_worker_returns_empty_scene_without_full_image_transfer(self):
        first = self.host.queue[0]
        result = self.service._isolated(first, None)
        self.assertEqual(result['face_count'], 0)
        self.assertIsNone(result['imagebytes'])
        self.assertNotIn('image', result)
        self.assertEqual(len(self.service._executor._processes), 1)
        self.assertEqual(self.service._executor._max_workers, 1)

    @unittest.skipUnless(os.environ.get('PHOTOSIEVE_FACE_SAMPLE'), 'Set PHOTOSIEVE_FACE_SAMPLE to an external portrait fixture')
    def test_real_spawn_portrait_crop_preserves_all_native_pixels_losslessly(self):
        path = Path(os.environ['PHOTOSIEVE_FACE_SAMPLE']).resolve()
        information = path.stat()
        photo = {'path': str(path), 'size': information.st_size, 'mtime': information.st_mtime_ns}
        result = self.service._isolated(photo, None)
        self.assertGreaterEqual(result['face_count'], 1)
        self.assertEqual(result['ratio'], 1)
        self.assertNotIn('image', result)
        original = Library.decode(path, 0)
        try:
            with Image.open(io.BytesIO(result['imagebytes'])) as image:
                self.assertEqual(image.format, 'PNG')
                self.assertLessEqual(max(image.size), 1024)
                self.assertEqual(image.tobytes(), original.crop(tuple(result['crop_box'])).tobytes())
        finally:
            original.close()


class FaceWorkerTest(unittest.TestCase):
    def test_cached_display_jpeg_produces_preview_without_opening_raw_decoder(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'photo.ARW'
            path.write_bytes(b'not decoded: source fingerprint only')
            information = path.stat()
            photo = {'path': str(path), 'size': information.st_size, 'mtime': information.st_mtime_ns}
            image = Image.new('RGB', (1600, 1200), 'blue')
            payload = io.BytesIO()
            image.save(payload, 'JPEG')
            faces = [{'box': [.2, .2, .15, .15]}]
            with patch('face_service.Library.decode', side_effect=AssertionError('Display JPEG avoids all RAW decoding')), \
                 patch('face_service.detect_faces', return_value=faces) as detect:
                result = face_service._compute_faces(photo, None, payload.getvalue(), 'preview')
            self.assertEqual(detect.call_count, 1)
            self.assertEqual(result['quality'], 'preview')
            self.assertEqual(result['face_count'], 1)
            self.assertEqual(result['original_size'], [1600, 1200])
            self.assertTrue(result['imagebytes'])

    def test_known_faces_skip_detection_and_decode_only_original_in_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'photo.jpg'
            Image.new('RGB', (2000, 1500), 'blue').save(path)
            stat = path.stat()
            photo = {'path': str(path), 'size': stat.st_size, 'mtime': stat.st_mtime_ns}
            faces = [{'box': [.2, .2, .6, .6]}]
            decode = Library.decode
            with patch('face_service.detect_faces', side_effect=AssertionError('Metadata avoids repeated inference')), \
                 patch('face_service.Library.decode', wraps=decode) as decoding:
                result = face_service._compute_faces(photo, faces)
            self.assertEqual([call.args[1] for call in decoding.call_args_list], [0])
            self.assertTrue(result['is_partial'])
            self.assertEqual(result['ratio'], 1)
            with Image.open(io.BytesIO(result['imagebytes'])) as image:
                self.assertEqual(image.format, 'PNG')
                self.assertLessEqual(max(image.size), 1024)
                self.assertGreater(image.getpixel((100, 100))[2], 245)
            self.assertNotIn('image', result)


if __name__ == '__main__':
    unittest.main()
