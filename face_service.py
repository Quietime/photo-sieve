"""Bounded offline face jobs, isolated native decoding and persistent derived caches."""
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
import hashlib
import io
import json
import multiprocessing
from pathlib import Path
import secrets
import threading
import time

from PySide6.QtCore import QObject, QSettings, QThread, QThreadPool, QTimer, Signal
from PIL import Image

from app import Library, lower_priority
from face_engine import ENGINE_VERSION, crop_faces, detect_faces
import crashlog

JOB_TIMEOUT = 60
FACE_EDGE = 9001
RAM_RESULTS = 4
SCHEMA = '''CREATE TABLE IF NOT EXISTS face_metadata(
    cache_key TEXT PRIMARY KEY, path TEXT NOT NULL, source_size INTEGER NOT NULL,
    source_mtime INTEGER NOT NULL, engine_version TEXT NOT NULL,
    faces_json TEXT NOT NULL, metadata_json TEXT NOT NULL)'''


def cache_key(photo):
    payload = json.dumps([str(photo['path']), int(photo['size']), int(photo['mtime']), ENGINE_VERSION],
                         ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def cache_path(library, photo):
    return library.cache / ('face-' + cache_key(photo) + '.png')


def _verify_source(photo):
    source = Path(photo['path'])
    if source.is_symlink():
        raise ValueError('照片路径已改变，请重新扫描')
    information = source.stat()
    if (information.st_size, information.st_mtime_ns) != (photo['size'], photo['mtime']):
        raise ValueError('照片文件已改变，请重新扫描后查看人脸')
    return source


def _face_initializer():
    lower_priority()
    crashlog.decoder_init()


def _compute_faces(photo, known_faces=None):
    """Spawn-process entry: the full image never leaves this process."""
    source = _verify_source(photo)
    faces = known_faces
    if faces is None:
        preview = Library.decode(source, 1280)
        try:
            faces = detect_faces(preview)
        finally:
            preview.close()
    if not faces:
        result = {'imagebytes': None, 'faces': [], 'face_count': 0, 'engine_version': ENGINE_VERSION,
                  'is_partial': False, 'ratio': None, 'ratio_x': None, 'ratio_y': None,
                  'crop_box': None, 'requested_box': None, 'original_size': None,
                  'native_size': None, 'output_size': None, 'face': None}
    else:
        original = Library.decode(source, 0)
        try:
            result = crop_faces(original, faces)
        finally:
            original.close()
        crop = result.pop('image')
        result['faces'] = faces
        result['imagebytes'] = None
        if crop is not None:
            try:
                output = io.BytesIO()
                # Focus checking requires native pixels and lossless encoding.
                crop.save(output, 'PNG', compress_level=3)
                result['imagebytes'] = output.getvalue()
            finally:
                crop.close()
    _verify_source(photo)
    return result


class FaceService(QObject):
    changed = Signal(int, object)

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.settings = QSettings('PhotoSieve', 'PhotoSieve')
        self.enabled = self.settings.value('face_preview', True, type=bool)
        self.ahead = max(0, min(500, self.settings.value('face_ahead', 50, type=int)))
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.pool.setThreadPriority(QThread.Priority.LowPriority)
        self._executor = None
        self._executor_lock = threading.Lock()
        self._stop = threading.Event()
        self._closed = False
        self._working = False
        self._context = None
        self._results = OrderedDict()
        self._done = set()
        self._schemas = set()
        self._sample_after = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.update)
        self.timer.start()

    def configure(self, enabled, ahead):
        self.enabled = bool(enabled)
        self.ahead = max(0, min(500, int(ahead)))
        self.settings.setValue('face_preview', self.enabled)
        self.settings.setValue('face_ahead', self.ahead)
        self.settings.sync()
        # A repaired component or reconnected drive can be retried after a user
        # toggles the feature. Valid negative face detections remain durable.
        failed = {key for key, result in self._results.items() if result.get('error')}
        for key in failed:
            self._results.pop(key)
        self._done.difference_update(failed)
        self._sample_after = 0.0
        self.update()

    def _token(self):
        return id(self.host.library), getattr(self.host, 'generation', 0)

    def _synchronize(self):
        token = self._token()
        if token != self._context:
            self._context = token
            self._results.clear()
            self._done.clear()
            self._sample_after = 0.0
        return token

    def _active(self):
        if self._closed or not self.enabled or getattr(self.host, 'closed', False) or getattr(self.host, 'closing', False):
            return False
        if getattr(self.host, 'cache_clearing', False) or getattr(self.host, 'rating_active', False):
            return False
        if getattr(self.host, 'importing', False):
            return True
        return (getattr(self.host, 'session_active', True) and getattr(self.host, 'view', 'pending') == 'pending'
                and self.host.stack.currentIndex() == 0 and not getattr(self.host, 'busy', False))

    def result_for(self, photo):
        """Immediate bounded-RAM lookup; durable-cache I/O runs in the background pool."""
        if self._closed or not self.enabled or not photo:
            return None
        self._synchronize()
        key = cache_key(photo)
        result = self._results.get(key)
        if result is not None:
            self._results.move_to_end(key)
            return dict(result)
        self.update()
        return None

    def update(self):
        if self._closed:
            return
        token = self._synchronize()
        if not self._active() or self._working:
            return
        library = self.host.library
        importing = bool(getattr(self.host, 'importing', False))
        rows = [dict(photo) for photo in getattr(self.host, 'queue', ())[:self.ahead + 1]
                if photo.get('present', 1) == 1 and photo.get('status', 0) == 0]
        candidates = {cache_key(photo) for photo in rows}
        if not importing:
            self._done.intersection_update(candidates)
            # Fetch the current cached crop even if prefetching previously evicted
            # its RAM copy. Future completed jobs never repeat their original decode.
            current = rows[0] if rows else None
            photo = current if current is not None and cache_key(current) not in self._results else next(
                (photo for photo in rows if cache_key(photo) not in self._done), None)
            if photo is None:
                return
        else:
            if time.monotonic() < self._sample_after:
                return
            photo = None
        self._working = True
        done_keys = set(self._done)
        count = self.ahead + 1
        selection = {'photo': photo}
        def work():
            chosen = photo
            if importing:
                available = library.list_photos('pending', 0, 'next', count)
                chosen = next((row for row in available if cache_key(row) not in done_keys), None)
            if chosen is None or self._stop.is_set():
                return None
            selection['photo'] = chosen
            return chosen, self._read_or_compute(library, chosen)
        def completed(value):
            self._working = False
            if self._closed:
                return
            if token != self._token():
                self.update()
                return
            if value is None:
                self._sample_after = time.monotonic() + .4
                return
            chosen, result = value
            key = cache_key(chosen)
            self._done.add(key)
            self._remember(key, result)
            self.changed.emit(chosen['id'], dict(result))
            self.update()
        def failed(message):
            self._working = False
            if self._closed:
                return
            if token != self._token():
                self.update()
                return
            chosen = selection['photo']
            if chosen is not None:
                key = cache_key(chosen)
                self._done.add(key)
                result = {'imagebytes': None, 'faces': [], 'face_count': 0, 'error': str(message),
                          'engine_version': ENGINE_VERSION, 'photo_id': chosen['id']}
                self._remember(key, result)
                self.changed.emit(chosen['id'], dict(result))
            else:
                self._sample_after = time.monotonic() + 2
            self.update()
        self.host.submit(work, completed, failed, self.pool)

    def _remember(self, key, result):
        self._results[key] = result
        self._results.move_to_end(key)
        current = getattr(self.host, 'queue', ())
        protected = cache_key(current[0]) if current else None
        for old in list(self._results):
            if len(self._results) <= RAM_RESULTS:
                break
            if old != protected:
                self._results.pop(old)

    def _ensure_schema(self, library):
        database = id(library), str(library.database)
        if database not in self._schemas:
            with library.connect() as db:
                db.execute(SCHEMA)
            self._schemas.add(database)

    def _read_or_compute(self, library, photo):
        key = cache_key(photo)
        lock = library.file_locks[photo['id'] % len(library.file_locks)]
        while not lock.acquire(timeout=.1):
            if self._stop.is_set():
                raise ValueError('人脸任务已停止')
        try:
            if self._stop.is_set():
                raise ValueError('人脸任务已停止')
            # All original-file I/O, including a potentially slow NAS stat,
            # runs in the terminable child. Cached crops match the scanned row.
            self._ensure_schema(library)
            with library.connect() as db:
                entry = db.execute('SELECT faces_json,metadata_json FROM face_metadata WHERE cache_key=?', (key,)).fetchone()
            faces = None
            if entry is not None:
                try:
                    faces = json.loads(entry['faces_json'])
                    metadata = json.loads(entry['metadata_json'])
                    if not isinstance(faces, list) or not isinstance(metadata, dict):
                        faces = None
                    elif not faces:
                        return dict(metadata, imagebytes=None, photo_id=photo['id'], cache_key=key)
                    else:
                        target = cache_path(library, photo)
                        payload = None if target.is_symlink() else library.read_cache(target)
                        if payload is not None:
                            try:
                                with Image.open(io.BytesIO(payload)) as cached:
                                    if cached.format != 'PNG' or list(cached.size) != metadata.get('output_size'):
                                        raise ValueError('人脸缓存格式或尺寸不匹配')
                                    cached.verify()
                            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                                payload = None
                            if payload is not None:
                                return dict(metadata, imagebytes=payload, photo_id=photo['id'], cache_key=key)
                except (ValueError, TypeError):
                    faces = None
            result = self._isolated(photo, faces)
            metadata = {name: value for name, value in result.items() if name != 'imagebytes'}
            with library.connect() as db:
                db.execute('INSERT OR REPLACE INTO face_metadata VALUES (?,?,?,?,?,?,?)',
                           (key, photo['path'], photo['size'], photo['mtime'], ENGINE_VERSION,
                            json.dumps(result['faces'], ensure_ascii=False), json.dumps(metadata, ensure_ascii=False)))
            if result.get('imagebytes'):
                self._save_crop(library, photo, result['imagebytes'])
            return dict(result, photo_id=photo['id'], cache_key=key)
        finally:
            lock.release()

    def _save_crop(self, library, photo, payload):
        target = cache_path(library, photo)
        temporary = target.with_suffix('.' + secrets.token_hex(6) + '.tmp')
        with library.cache_lock:
            try:
                temporary.write_bytes(payload)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            with library.connect() as db:
                db.execute('INSERT OR REPLACE INTO cache_index VALUES (?,?,?,?,?)',
                           (target.name, photo['id'], photo['size'], photo['mtime'], FACE_EDGE))
            library.cache_indexed.add(target.name)
            delta = len(payload) - library.cache_sizes.get(target.name, 0)
            library.cache_sizes[target.name] = len(payload)
            library.cache_bytes += delta
        if delta and library.cache_changed:
            library.cache_changed(delta)
        library.schedule_prune()

    def _isolated(self, photo, faces):
        with self._executor_lock:
            if self._closed or self._stop.is_set():
                raise ValueError('人脸任务已停止')
            if self._executor is None:
                self._executor = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context('spawn'), initializer=_face_initializer)
            executor = self._executor
        future = executor.submit(_compute_faces, photo, faces)
        deadline = time.monotonic() + JOB_TIMEOUT
        try:
            while not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._discard_executor(.1)
                    raise ValueError('人脸原图读取超过 60 秒，已停止独立解码进程')
                try:
                    return future.result(timeout=min(.1, remaining))
                except FutureTimeout:
                    continue
            raise ValueError('人脸任务已停止')
        except BrokenProcessPool as error:
            self._discard_executor(.1)
            raise ValueError('独立人脸解码进程意外退出，已隔离此任务') from error

    def _discard_executor(self, join_timeout=0):
        with self._executor_lock:
            executor, self._executor = self._executor, None
        if executor is None:
            return
        processes = list((getattr(executor, '_processes', None) or {}).values())
        for process in processes:
            try:
                if process.is_alive():
                    process.terminate()
            except (OSError, ValueError):
                pass  # The process manager may have reaped it between checks.
        executor.shutdown(wait=False, cancel_futures=True)
        for process in processes:
            try:
                if join_timeout:
                    process.join(join_timeout)
                if process.is_alive():
                    process.kill()
                    if join_timeout:
                        process.join(join_timeout)
            except (OSError, ValueError):
                pass

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self.timer.stop()
        self._discard_executor(.05)

    def clear_memory(self):
        """Use after manual cache clearing; face locations remain in SQLite."""
        self._results.clear()
        self._done.clear()
        self._sample_after = 0.0
        self.update()
