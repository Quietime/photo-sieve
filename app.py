"""Photo Sieve: local dual-photo review with persistent undoable deletion."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

RAW = {'.cr2', '.cr3', '.nef', '.nrw', '.arw', '.dng', '.raf', '.rw2', '.orf', '.pef', '.srw'}
EXTENSIONS = RAW | {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp', '.gif', '.heic', '.heif', '.avif'}
STATIC = Path(__file__).parent / 'static'
TRASH_DIRECTORY = '.photo-sieve-trash'


class Library:
    def __init__(self, directory: Path, cache_mb=2048):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cache = self.directory / 'cache'
        self.cache.mkdir(exist_ok=True)
        self.database = self.directory / 'library.sqlite3'
        self.cache_limit = cache_mb * 1024 * 1024
        self.cache_lock = threading.Lock()
        self.decode_slots = threading.Semaphore(2)
        self.file_locks = [threading.RLock() for _ in range(64)]
        self.job_lock = threading.Lock()
        self.prune_lock = threading.Lock()
        self.last_prune = time.monotonic()
        self.job = {'revision': secrets.token_hex(8), 'running': False, 'kind': '', 'done': 0, 'total': 0, 'errors': [], 'message': ''}
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS photos(
                    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                    size INTEGER NOT NULL, mtime INTEGER NOT NULL,
                    status INTEGER NOT NULL DEFAULT 0, present INTEGER NOT NULL DEFAULT 1,
                    seen TEXT, digest TEXT, error TEXT);
                CREATE INDEX IF NOT EXISTS photo_status ON photos(present,status,id);
                CREATE INDEX IF NOT EXISTS photo_size ON photos(present,size);
                CREATE INDEX IF NOT EXISTS photo_digest ON photos(present,digest);
                CREATE TABLE IF NOT EXISTS history(
                    seq INTEGER PRIMARY KEY, photo_id INTEGER, before INTEGER, after INTEGER,
                    size INTEGER, mtime INTEGER, action TEXT DEFAULT 'mark', backup TEXT);
                CREATE TABLE IF NOT EXISTS trash_log(
                    id INTEGER PRIMARY KEY, photo_id INTEGER, path TEXT,
                    deleted_at REAL, result TEXT, backup TEXT);
                CREATE TABLE IF NOT EXISTS export_batches(
                    id TEXT PRIMARY KEY, destination TEXT, mode TEXT, created_at REAL, completed_at REAL);
                CREATE TABLE IF NOT EXISTS export_files(
                    id INTEGER PRIMARY KEY, batch_id TEXT, photo_id INTEGER, original TEXT,
                    target TEXT, size INTEGER, mtime INTEGER, result TEXT DEFAULT 'pending', error TEXT);
                CREATE INDEX IF NOT EXISTS export_batch_files ON export_files(batch_id,id);
            ''')
            for table, column, declaration in [('history', 'action', "TEXT DEFAULT 'mark'"),
                                                ('history', 'backup', 'TEXT'), ('trash_log', 'backup', 'TEXT')]:
                if column not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
            db.execute('CREATE INDEX IF NOT EXISTS history_actions ON history(action,photo_id,seq)')
            # Reconcile a crash between a filesystem move and committing the review record.
            for row in db.execute("SELECT * FROM trash_log WHERE result='started'").fetchall():
                backup = Path(row['backup']) if row['backup'] else None
                photo = db.execute('SELECT * FROM photos WHERE id=?', (row['photo_id'],)).fetchone()
                if backup and backup.is_file() and not Path(row['path']).exists() and photo:
                    db.execute('UPDATE photos SET present=0 WHERE id=?', (photo['id'],))
                    db.execute("INSERT INTO history(photo_id,before,after,size,mtime,action,backup) VALUES (?,?,?,?,?,'trash',?)",
                               (photo['id'], photo['status'], photo['status'], photo['size'], photo['mtime'], str(backup)))
                    db.execute("UPDATE trash_log SET result='staged' WHERE id=?", (row['id'],))
            # A completed filesystem move remains discoverable after a process interruption.
            for row in db.execute("SELECT export_files.* FROM export_files JOIN export_batches ON export_batches.id=batch_id WHERE mode='move' AND result='working'").fetchall():
                if row['target'] and Path(row['target']).is_file() and not Path(row['original']).exists():
                    st = Path(row['target']).stat()
                    if (st.st_size, st.st_mtime_ns) == (row['size'], row['mtime']):
                        db.execute("UPDATE export_files SET result='done' WHERE id=?", (row['id'],))
                        db.execute('UPDATE photos SET present=0 WHERE id=?', (row['photo_id'],))
            db.execute("UPDATE export_files SET result='failed',error='上次任务被中断，请检查目标文件后再重试' WHERE result IN ('pending','working')")
            db.execute('UPDATE export_batches SET completed_at=? WHERE completed_at IS NULL', (time.time(),))
        self.prune_cache()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=30000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self):
        with self.connect() as db:
            counts = {row['status']: row['n'] for row in db.execute('SELECT status,COUNT(*) n FROM photos WHERE present=1 GROUP BY status')}
            duplicates = db.execute('SELECT COUNT(*) FROM photos WHERE ' + self.query('duplicates')).fetchone()[0]
            deleted = db.execute('SELECT COUNT(*) FROM photos WHERE ' + self.query('deleted')).fetchone()[0]
            last_deleted = db.execute('''SELECT photos.*,trash_log.backup,trash_log.deleted_at FROM trash_log
                JOIN photos ON photos.id=photo_id WHERE result='staged' AND present=0
                AND photos.id IN (SELECT photo_id FROM history WHERE action='trash') ORDER BY trash_log.id DESC LIMIT 1''').fetchone()
        return {'root': self.meta('root'), 'cursor': int(self.meta('cursor') or 0),
                'counts': {'all': sum(counts.values()), 'pending': counts.get(0, 0), 'keep': counts.get(1, 0), 'reject': counts.get(-1, 0), 'duplicates': duplicates, 'deleted': deleted},
                'job': {**self.job, 'errors': list(self.job['errors'])}, 'data_dir': str(self.directory), 'last_export': self.export_summary(),
                'last_deleted': self.public(last_deleted) if last_deleted else None}


    def meta(self, key):
        with self.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return row[0] if row else ''

    def remember(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, str(value)))

    def start_job(self, kind, action):
        with self.job_lock:
            if self.job['running']:
                raise HTTPException(409, '正在执行任务，请等待完成。')
            self.job = {'revision': secrets.token_hex(8), 'running': True, 'kind': kind, 'done': 0, 'total': 0, 'errors': [], 'message': ''}
        def work():
            try:
                action()
                self.job['message'] = '已完成'
            except Exception as exc:
                self.job['message'] = f'任务未完成：{exc}'
            finally:
                self.job['running'] = False
        threading.Thread(target=work, daemon=True).start()

    def warn(self, value):
        if len(self.job['errors']) < 100:
            self.job['errors'].append(str(value))

    def scan(self, folder):
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            raise HTTPException(400, '文件夹不存在，或尚未连接硬盘。')
        old = self.meta('root')
        if old and Path(old) != root:
            raise HTTPException(409, '此资料库已绑定其他文件夹。请用 --data-dir 指定新资料库，以保留原筛选记录。')
        def work():
            self.remember('root', str(root))
            self._scan(root)
        self.start_job('扫描文件夹', work)

    def _scan(self, root):
        generation = secrets.token_hex(12)
        batch = []
        walk_errors = []
        excluded = {Path(p).resolve() for p in json.loads(self.meta('export_destinations') or '[]')}
        def walk_error(exc):
            walk_errors.append(exc)
            self.warn(exc)
        def flush():
            with self.connect() as db:
                db.executemany('''INSERT INTO photos(path,size,mtime,seen) VALUES (?,?,?,?)
                    ON CONFLICT(path) DO UPDATE SET
                    status=CASE WHEN photos.size=excluded.size AND photos.mtime=excluded.mtime THEN photos.status ELSE 0 END,
                    digest=CASE WHEN photos.size=excluded.size AND photos.mtime=excluded.mtime THEN photos.digest ELSE NULL END,
                    error=CASE WHEN photos.size=excluded.size AND photos.mtime=excluded.mtime THEN photos.error ELSE NULL END,
                    size=excluded.size,mtime=excluded.mtime,present=1,seen=excluded.seen''', batch)
            batch.clear()
        for base, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
            dirs[:] = sorted(d for d in dirs if d.casefold() != TRASH_DIRECTORY.casefold() and not (Path(base) / d).is_symlink()
                             and (Path(base) / d).resolve() != self.directory
                             and (Path(base) / d).resolve() not in excluded)
            for name in sorted(names):
                path = Path(base) / name
                if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                    continue
                try:
                    stat = path.stat()
                    batch.append((str(path), stat.st_size, stat.st_mtime_ns, generation))
                    self.job['done'] += 1
                except OSError as exc:
                    walk_errors.append(exc)
                    self.warn(exc)
                if len(batch) >= 500:
                    flush()
        flush()
        # An inaccessible branch must not make its photos disappear from the library.
        if not walk_errors:
            with self.connect() as db:
                db.execute('UPDATE photos SET present=0 WHERE seen IS NULL OR seen<>?', (generation,))

    def query(self, view):
        filters = {'all': 'present=1', 'pending': 'present=1 AND status=0',
                   'keep': 'present=1 AND status=1', 'reject': 'present=1 AND status=-1',
                   'duplicates': 'present=1 AND digest IN (SELECT digest FROM photos WHERE present=1 AND digest IS NOT NULL GROUP BY digest HAVING COUNT(*)>1)',
                   'errors': 'present=1 AND error IS NOT NULL',
                   'deleted': "present=0 AND id IN (SELECT photo_id FROM history WHERE action='trash')"}
        if view not in filters:
            raise HTTPException(400, '未知筛选条件')
        return filters[view]

    def list_photos(self, view='all', anchor=0, direction='next', limit=60):
        where = self.query(view)
        if direction not in ('next', 'prev'):
            raise HTTPException(400, '未知翻页方向')
        relation, order = ('>', 'ASC') if direction == 'next' else ('<', 'DESC')
        columns = '''photos.*,
            (SELECT backup FROM history WHERE photo_id=photos.id AND action='trash' ORDER BY seq DESC LIMIT 1) backup,
            (SELECT deleted_at FROM trash_log WHERE photo_id=photos.id AND result='staged' ORDER BY id DESC LIMIT 1) deleted_at''' if view == 'deleted' else '*'
        with self.connect() as db:
            rows = db.execute(f'SELECT {columns} FROM photos WHERE {where} AND id {relation} ? ORDER BY id {order} LIMIT ?',
                              (anchor, limit)).fetchall()
        if direction == 'prev':
            rows = list(reversed(rows))
        root = self.meta('root') or self.directory
        return [self.public(row, root) for row in rows]

    def public(self, row, root=None):
        data = dict(row)
        data['name'] = Path(data['path']).name
        data['relative'] = os.path.relpath(data['path'], root or self.meta('root') or self.directory)
        data['deleted'] = not data['present'] and bool(data.get('backup'))
        return data

    def context(self, photo_id, view='pending', limit=40):
        return {'current': self.public(self.photo(photo_id)),
                'before': self.list_photos(view, photo_id, 'prev', 4),
                'after': self.list_photos(view, photo_id, 'next', limit)}

    def photo(self, photo_id, include_deleted=False):
        with self.connect() as db:
            row = db.execute('SELECT * FROM photos WHERE id=? AND present=1', (photo_id,)).fetchone()
            if not row and include_deleted:
                row = db.execute('''SELECT photos.*,history.backup FROM photos JOIN history ON photo_id=photos.id
                    WHERE photos.id=? AND present=0 AND action='trash' ORDER BY seq DESC LIMIT 1''', (photo_id,)).fetchone()
        if not row:
            raise HTTPException(404, '照片不在当前资料库中')
        return row

    def mark(self, photo_id, status):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM photos WHERE id=? AND present=1', (photo_id,)).fetchone()
            if not row:
                raise HTTPException(404, '照片不存在')
            try:
                stat = Path(row['path']).stat()
            except OSError:
                raise HTTPException(409, '照片无法访问，请连接硬盘后重新扫描')
            if (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime']):
                raise HTTPException(409, '原文件已改变，请重新扫描后再筛选')
            if row['status'] != status:
                db.execute('INSERT INTO history(photo_id,before,after,size,mtime) VALUES (?,?,?,?,?)',
                           (photo_id, row['status'], status, row['size'], row['mtime']))
                db.execute('UPDATE photos SET status=? WHERE id=?', (status, photo_id))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('cursor',?)", (str(photo_id),))

    def undo(self, only_delete=False, photo_id=None):
        with self.job_lock:
            if self.job['running']:
                raise HTTPException(409, '请等待扫描或检查完成，再撤回操作')
            return self._undo(only_delete, photo_id)

    def _undo(self, only_delete=False, photo_id=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            while True:
                condition = "WHERE action='trash'" if only_delete else ''
                values = ()
                if photo_id is not None:
                    condition = "WHERE action='trash' AND photo_id=?"
                    values = (photo_id,)
                h = db.execute(f'SELECT * FROM history {condition} ORDER BY seq DESC LIMIT 1', values).fetchone()
                if not h:
                    raise HTTPException(404, '没有可以恢复的删除' if only_delete else '没有可以撤回的操作')
                p = db.execute('SELECT * FROM photos WHERE id=?', (h['photo_id'],)).fetchone()
                if h['action'] == 'trash' and p:
                    original, backup = Path(p['path']), Path(h['backup'])
                    if original.exists():
                        raise HTTPException(409, '原路径已有文件，无法撤回；请先移走冲突文件。')
                    if not backup.is_file() or not original.parent.is_dir():
                        raise HTTPException(409, '待删除照片或原目录无法访问，请连接硬盘后重试。')
                    stat = backup.stat()
                    if (stat.st_size, stat.st_mtime_ns) != (h['size'], h['mtime']):
                        raise HTTPException(409, '待删除照片已被外部程序修改，请手动检查。')
                    try:
                        os.rename(backup, original)
                    except OSError as exc:
                        raise HTTPException(409, f'无法恢复照片：{exc}')
                    db.execute('UPDATE photos SET present=1,status=? WHERE id=?', (h['before'], p['id']))
                    db.execute('DELETE FROM history WHERE seq=?', (h['seq'],))
                    db.execute("UPDATE trash_log SET result='restored' WHERE backup=?", (str(backup),))
                    return {'id': p['id'], 'status': h['before'], 'restored': True}
                db.execute('DELETE FROM history WHERE seq=?', (h['seq'],))
                if p and p['present'] and (p['status'], p['size'], p['mtime']) == (h['after'], h['size'], h['mtime']):
                    db.execute('UPDATE photos SET status=? WHERE id=?', (h['before'], h['photo_id']))
                    return {'id': h['photo_id'], 'status': h['before']}

    def duplicates(self):
        self.start_job('检查完全重复文件', self._duplicates)

    def export_kept(self, destination, mode='copy'):
        if mode not in ('copy', 'move'):
            raise HTTPException(400, '请选择复制或移动')
        target = Path(destination).expanduser().resolve()
        root = Path(self.meta('root')).resolve()
        if target == root or target == self.directory or target.is_relative_to(self.directory):
            raise HTTPException(400, '请选择独立的目标文件夹，不能使用原片根目录或程序资料库。')
        if target.exists() and not target.is_dir():
            raise HTTPException(400, '目标路径不是文件夹')
        with self.connect() as db:
            if target.is_relative_to(root):
                prefix = str(target) + os.sep
                if db.execute('SELECT 1 FROM photos WHERE present=1 AND substr(path,1,?)=? COLLATE NOCASE LIMIT 1', (len(prefix), prefix)).fetchone():
                    raise HTTPException(400, '目标文件夹已包含图库原片，请选一个新的输出文件夹。')
            if not db.execute('SELECT COUNT(*) FROM photos WHERE present=1 AND status=1').fetchone()[0]:
                raise HTTPException(400, '还没有已保留的照片')
        self.start_job('复制保留照片' if mode == 'copy' else '移动保留照片', lambda: self._export_kept(target, mode, root))

    def _export_kept(self, destination, mode, root):
        destination.mkdir(parents=True, exist_ok=True)
        batch_id = secrets.token_hex(16)
        with self.connect() as db:
            db.execute('INSERT INTO export_batches(id,destination,mode,created_at) VALUES (?,?,?,?)',
                       (batch_id, str(destination), mode, time.time()))
            db.execute('''INSERT INTO export_files(batch_id,photo_id,original,size,mtime)
                SELECT ?,id,path,size,mtime FROM photos WHERE present=1 AND status=1 ORDER BY id''', (batch_id,))
            self.job['total'] = db.execute('SELECT COUNT(*) FROM export_files WHERE batch_id=?', (batch_id,)).fetchone()[0]
        # Do not reimport copied output if the user chose a subfolder of the source library.
        excluded = json.loads(self.meta('export_destinations') or '[]')
        if str(destination) not in excluded:
            excluded.append(str(destination))
            self.remember('export_destinations', json.dumps(excluded, ensure_ascii=False))
        self.remember('last_export', batch_id)
        anchor = 0
        while True:
            with self.connect() as db:
                rows = db.execute('SELECT * FROM export_files WHERE batch_id=? AND id>? ORDER BY id LIMIT 100',
                                  (batch_id, anchor)).fetchall()
            if not rows:
                break
            for row in rows:
                anchor = row['id']
                source = Path(row['original'])
                transferred = False
                try:
                    relative = source.relative_to(root)
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.resolve().is_relative_to(destination) or target.resolve() == source.resolve():
                        raise ValueError('目标路径与原文件冲突')
                    with self.connect() as db:
                        db.execute("UPDATE export_files SET target=?,result='working' WHERE id=?", (str(target), row['id']))
                    with self.file_locks[row['photo_id'] % len(self.file_locks)]:
                        stat = source.stat()
                        if (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime']):
                            raise ValueError('原文件已改变，请重新扫描后再导出')
                        self.transfer_file(source, target, mode)
                        transferred = True
                    with self.connect() as db:
                        db.execute("UPDATE export_files SET result='done' WHERE id=?", (row['id'],))
                        if mode == 'move':
                            db.execute('UPDATE photos SET present=0 WHERE id=?', (row['photo_id'],))
                except Exception as exc:
                    if transferred:
                        # Preserve the durable 'working' record for restart reconciliation.
                        raise
                    error = '目标已有同名文件，已跳过，未覆盖' if isinstance(exc, FileExistsError) else str(exc)
                    self.warn(f'{source}: {error}')
                    with self.connect() as db:
                        db.execute("UPDATE export_files SET result='failed',error=? WHERE id=?", (error, row['id']))
                self.job['done'] += 1
        with self.connect() as db:
            db.execute('UPDATE export_batches SET completed_at=? WHERE id=?', (time.time(), batch_id))

    @staticmethod
    def transfer_file(source, target, mode):
        if mode == 'move':
            try:
                if os.name == 'nt':
                    # Windows rename refuses an existing destination, unlike POSIX rename.
                    os.rename(source, target)
                else:
                    os.link(source, target)
                    try:
                        source.unlink()
                    except BaseException:
                        target.unlink()
                        raise
                return
            except OSError as exc:
                if exc.errno not in (errno.EXDEV, errno.ENOSYS, errno.EOPNOTSUPP) and getattr(exc, 'winerror', 0) != 17:
                    raise
        # Cross-volume moves and copies reserve a new name with O_EXCL; never overwrite.
        before = source.stat()
        created = False
        inode = None
        try:
            with source.open('rb') as reader, target.open('xb') as writer:
                created = True
                inode = os.fstat(writer.fileno()).st_ino
                shutil.copyfileobj(reader, writer, length=4 * 1024 * 1024)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('复制期间原文件发生变化，已停止该文件')
            shutil.copystat(source, target)
            if mode == 'move':
                source.unlink()
        except BaseException:
            if created and target.exists() and target.stat().st_ino == inode:
                target.unlink()
            raise

    def export_summary(self):
        batch_id = self.meta('last_export')
        if not batch_id:
            return None
        with self.connect() as db:
            batch = db.execute('SELECT * FROM export_batches WHERE id=?', (batch_id,)).fetchone()
            if not batch:
                return None
            result = dict(batch)
            counts = {r['result']: r['n'] for r in db.execute('SELECT result,COUNT(*) n FROM export_files WHERE batch_id=? GROUP BY result', (batch_id,))}
            result.update({'total': sum(counts.values()), 'done': counts.get('done', 0), 'failed': counts.get('failed', 0),
                           'pending': counts.get('pending', 0) + counts.get('working', 0)})
            result['errors'] = [dict(r) for r in db.execute("SELECT original,target,error FROM export_files WHERE batch_id=? AND result='failed' ORDER BY id LIMIT 100", (batch_id,))]
            return result

    def trash(self, photo_id):
        # Windows cannot rename files while a preview decoder still holds them open.
        with self.file_locks[photo_id % len(self.file_locks)]:
            return self._trash(photo_id)

    def _trash(self, photo_id):
        # Serialize with starting scans: scanning could otherwise reinsert a deleted file.
        with self.job_lock:
            if self.job['running']:
                raise HTTPException(409, '请等待当前扫描或检查完成，再删除照片。')
            row = self.photo(photo_id)
            path = Path(row['path'])
            root = Path(self.meta('root'))
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise HTTPException(409, '文件路径已改变，请重新扫描')
                stat = path.stat()
            except OSError:
                raise HTTPException(409, '照片无法访问，请检查硬盘并重新扫描')
            if (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime']):
                raise HTTPException(409, '原照片已改变，请重新扫描后再删除')
            stash = path.parent / TRASH_DIRECTORY
            if stash.is_symlink():
                raise HTTPException(409, '待删除目录是符号链接，请检查后重试。')
            try:
                stash.mkdir(exist_ok=True)
                backup = stash / (secrets.token_hex(16) + path.suffix)
                with self.connect() as db:
                    entry = db.execute('INSERT INTO trash_log(photo_id,path,deleted_at,result,backup) VALUES (?,?,?,?,?)',
                                       (photo_id, str(path), time.time(), 'started', str(backup))).lastrowid
                # Same-directory filesystem move: no decoding or full-size copying.
                os.rename(path, backup)
            except Exception as exc:
                raise HTTPException(409, f'无法移入待删除区：{exc}')
            with self.connect() as db:
                db.execute('UPDATE photos SET present=0 WHERE id=?', (photo_id,))
                db.execute("INSERT INTO history(photo_id,before,after,size,mtime,action,backup) VALUES (?,?,?,?,?,'trash',?)",
                           (photo_id, row['status'], row['status'], row['size'], row['mtime'], str(backup)))
                db.execute("UPDATE trash_log SET result='staged' WHERE id=?", (entry,))
            return {'id': photo_id, 'path': str(path), 'backup': str(backup), 'staged': True}

    def _duplicates(self):
        query = '''present=1 AND size IN
            (SELECT size FROM photos WHERE present=1 GROUP BY size HAVING COUNT(*)>1)'''
        with self.connect() as db:
            self.job['total'] = db.execute(f'SELECT COUNT(*) FROM photos WHERE {query}').fetchone()[0]
        anchor = 0
        while True:
            with self.connect() as db:
                rows = db.execute(f'SELECT * FROM photos WHERE {query} AND id>? ORDER BY id LIMIT 100', (anchor,)).fetchall()
            if not rows:
                break
            for row in rows:
                anchor = row['id']
                try:
                    path = Path(row['path'])
                    before = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (row['size'], row['mtime']):
                        raise ValueError('文件已改变，请重新扫描')
                    if not row['digest']:
                        digest = hashlib.blake2b(digest_size=32)
                        with path.open('rb') as source:
                            while block := source.read(1024 * 1024):
                                digest.update(block)
                        after = path.stat()
                        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                            raise ValueError('检查期间文件发生改变')
                        with self.connect() as db:
                            db.execute('UPDATE photos SET digest=? WHERE id=?', (digest.hexdigest(), row['id']))
                except Exception as exc:
                    self.warn(f"{row['path']}: {exc}")
                    with self.connect() as db:
                        db.execute('UPDATE photos SET digest=NULL WHERE id=?', (row['id'],))
                self.job['done'] += 1

    def prune_cache(self):
        # Enumerating a large cache must not lock out all preview reads.
        files = []
        for path in self.cache.glob('*.jpg'):
            try:
                st = path.stat()
                files.append((st.st_mtime, st.st_size, path))
            except FileNotFoundError:
                pass
        total = sum(item[1] for item in files)
        if total > self.cache_limit:
            for _, size, path in sorted(files):
                with self.cache_lock:
                    path.unlink(missing_ok=True)
                total -= size
                if total <= self.cache_limit * .85:
                    break

    def schedule_prune(self):
        if time.monotonic() - self.last_prune < 60 or not self.prune_lock.acquire(blocking=False):
            return
        self.last_prune = time.monotonic()
        def work():
            try:
                self.prune_cache()
            except OSError:
                pass
            finally:
                self.prune_lock.release()
        threading.Thread(target=work, daemon=True).start()

    def cache_path(self, source, size, mtime, edge):
        key = hashlib.sha256(f'v2|{source}|{size}|{mtime}|{edge}'.encode()).hexdigest()
        return self.cache / f'{key}.jpg'

    def read_cache(self, target):
        with self.cache_lock:
            if target.exists():
                payload = target.read_bytes()
                # Avoid a write on every disk cache hit; occasional touches suffice for eviction.
                if time.time() - target.stat().st_mtime > 60:
                    os.utime(target, None)
                return payload
        return None

    def read_preview_cache(self, original, size, mtime, edge):
        cached = self.read_cache(self.cache_path(original, size, mtime, edge))
        if cached is None and edge:
            # Reuse the first release's previews when upgrading an existing large library.
            legacy = hashlib.sha256(f'{original}|{size}|{mtime}|{edge}'.encode()).hexdigest()
            cached = self.read_cache(self.cache / f'{legacy}.jpg')
        return cached

    @staticmethod
    def save_cache(target, image, quality=90):
        result = io.BytesIO()
        image.save(result, 'JPEG', quality=quality)
        payload = result.getvalue()
        temp = target.with_suffix(f'.{secrets.token_hex(6)}.tmp')
        try:
            temp.write_bytes(payload)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return payload

    def preview(self, photo_id, edge):
        with self.file_locks[photo_id % len(self.file_locks)]:
            return self._preview(photo_id, edge)

    def _preview(self, photo_id, edge):
        row = self.photo(photo_id, include_deleted=True)
        source = Path(row['backup'] if not row['present'] else row['path'])
        # Keep the cache key stable when a photo is moved into the reversible deletion area.
        original = Path(row['path'])
        try:
            stat = source.stat()
            if (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime']):
                raise ValueError('原文件已改变，请重新扫描')
            target = self.cache_path(original, stat.st_size, stat.st_mtime_ns, edge)
            cached = self.read_preview_cache(original, stat.st_size, stat.st_mtime_ns, edge)
            # Cached photos bypass the decode semaphore, even while large RAWs are loading.
            if cached is not None:
                return cached
            with self.decode_slots:
                # Reuse an existing preview for thumbnails rather than reopening the original.
                larger = self.cache_path(original, stat.st_size, stat.st_mtime_ns, 1600)
                existing = self.read_preview_cache(original, stat.st_size, stat.st_mtime_ns, 1600) if edge == 320 else None
                if existing is not None:
                    with Image.open(io.BytesIO(existing)) as im:
                        image = im.convert('RGB')
                        image.thumbnail((320, 320), Image.Resampling.LANCZOS)
                elif edge == 320:
                    # The strip also warms the review preview with one original-file decode.
                    image = self.decode(source, 1600)
                    self.save_cache(larger, image)
                    image.thumbnail((320, 320), Image.Resampling.LANCZOS)
                else:
                    image = self.decode(source, edge)
                payload = self.save_cache(target, image, quality=96 if edge == 0 else 90)
                if edge == 1600:
                    thumbnail = image.copy()
                    thumbnail.thumbnail((320, 320), Image.Resampling.LANCZOS)
                    self.save_cache(self.cache_path(original, stat.st_size, stat.st_mtime_ns, 320), thumbnail)
                with self.connect() as db:
                    db.execute('UPDATE photos SET error=NULL WHERE id=?', (photo_id,))
                self.schedule_prune()
                return payload
        except Exception as exc:
            with self.connect() as db:
                db.execute('UPDATE photos SET error=? WHERE id=?', (str(exc), photo_id))
            raise HTTPException(422, f'无法预览：{exc}。HEIC 需要 pillow-heif；RAW 需要 rawpy。')

    @staticmethod
    def decode(source, edge):
        if source.suffix.lower() in RAW:
            try:
                import rawpy
            except ImportError:
                raise ValueError('请安装 rawpy 才能预览 RAW')
            with rawpy.imread(str(source)) as raw:
                if edge == 0:
                    # Focus inspection uses the sensor image, not the small embedded JPEG.
                    return Image.fromarray(raw.postprocess(use_camera_wb=True, no_auto_bright=True))
                flip = raw.sizes.flip
                needs_rotation = False
                try:
                    thumb = raw.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        with Image.open(io.BytesIO(thumb.data)) as im:
                            needs_rotation = im.getexif().get(274, 1) == 1
                            image = ImageOps.exif_transpose(im).convert('RGB')
                    else:
                        image = Image.fromarray(thumb.data)
                        needs_rotation = True
                except rawpy.LibRawNoThumbnailError:
                    image = Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True))
            # RAW embedded preview orientation varies by camera; rawpy's LibRaw flip describes it.
            if needs_rotation and flip in (3, 5, 6):
                image = image.rotate({3: 180, 5: 90, 6: 270}[flip], expand=True)
        else:
            with Image.open(source) as im:
                if edge:
                    im.draft('RGB', (edge, edge))
                image = ImageOps.exif_transpose(im).convert('RGB')
        if edge:
            image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        return image


class Folder(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class Mark(BaseModel):
    id: int
    status: int = Field(ge=-1, le=1)


class Cursor(BaseModel):
    id: int = Field(ge=0)


class DeletePhoto(BaseModel):
    id: int = Field(ge=1)


class SaveKept(BaseModel):
    destination: str = Field(min_length=1, max_length=4096)
    mode: str = 'copy'


def create_app(library, token=None):
    token = token or secrets.token_urlsafe(32)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def local_access(request: Request, call_next):
        host = request.headers.get('host', '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost', 'testserver'):
            return Response('Local access only', status_code=403)
        if request.url.path.startswith('/api/'):
            supplied = request.headers.get('x-photo-token') or request.query_params.get('token', '')
            if not secrets.compare_digest(supplied.encode(), token.encode()):
                return Response('Invalid session token', status_code=403)
        response = await call_next(request)
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        return response

    @app.get('/')
    def index():
        return HTMLResponse((STATIC / 'index.html').read_text('utf-8'))

    @app.get('/static/{name}')
    def assets(name: str):
        if name not in ('app.js', 'style.css'):
            raise HTTPException(404)
        return FileResponse(STATIC / name)

    @app.get('/api/state')
    def state():
        return library.state()

    @app.post('/api/scan')
    def scan(folder: Folder):
        library.scan(folder.path)
        return {'ok': True}

    @app.get('/api/photos')
    def photos(view: str = 'all', anchor: int = 0, direction: str = 'next', limit: int = 60):
        return library.list_photos(view, anchor, direction, max(1, min(limit, 120)))

    @app.get('/api/photo/{photo_id}')
    def photo(photo_id: int):
        return library.public(library.photo(photo_id, include_deleted=True))

    @app.get('/api/context/{photo_id}')
    def context(photo_id: int, view: str = 'pending'):
        return library.context(photo_id, view)

    @app.get('/api/detail/{photo_id}')
    def detail(photo_id: int):
        return Response(library.preview(photo_id, 0), media_type='image/jpeg',
                        headers={'Cache-Control': 'private, max-age=3600'})

    @app.get('/api/image/{photo_id}')
    def image(photo_id: int, edge: int = 1600):
        if edge not in (320, 1600, 4096):
            raise HTTPException(400, '不支持的预览尺寸')
        return Response(library.preview(photo_id, edge), media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=3600'})

    @app.post('/api/mark')
    def mark(mark: Mark):
        library.mark(mark.id, mark.status)
        return {'ok': True}

    @app.post('/api/undo')
    def undo():
        return library.undo()

    @app.post('/api/undo-delete')
    def undo_delete():
        return library.undo(only_delete=True)

    @app.post('/api/restore/{photo_id}')
    def restore(photo_id: int):
        return library.undo(only_delete=True, photo_id=photo_id)

    @app.post('/api/save-kept')
    def save_kept(request: SaveKept):
        library.export_kept(request.destination, request.mode)
        return {'ok': True}

    @app.get('/api/choose-folder')
    def choose_folder():
        try:
            result = subprocess.run([sys.executable, str(STATIC.parent / 'choose_folder.py')],
                                    capture_output=True, text=True, encoding='utf-8', timeout=300)
            if result.returncode:
                raise ValueError('系统文件夹选择窗口不可用，请粘贴完整路径。')
            return json.loads(result.stdout)
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/open-folder')
    def open_folder(folder: Folder):
        path = Path(folder.path).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(404, '文件夹不存在，或照片盘未连接。')
        try:
            if os.name == 'nt':
                os.startfile(str(path))
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise HTTPException(400, f'无法打开文件夹：{exc}')
        return {'ok': True}

    @app.post('/api/trash')
    def trash(photo: DeletePhoto):
        return library.trash(photo.id)

    @app.post('/api/cursor')
    def cursor(cursor: Cursor):
        library.remember('cursor', cursor.id)
        return {'ok': True}

    @app.post('/api/duplicates')
    def duplicates():
        library.duplicates()
        return {'ok': True}

    @app.get('/api/export')
    def export(view: str = 'keep'):
        # Stream via a disk file, so exporting 100k records does not inflate RAM.
        if view not in ('all', 'keep', 'reject', 'pending'):
            raise HTTPException(400, '不支持的导出条件')
        output = library.directory / f'export-{view}-{secrets.token_hex(6)}.csv'
        with library.connect() as db, output.open('w', encoding='utf-8-sig', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['绝对路径', '筛选结果', '文件字节数'])
            for row in db.execute('SELECT path,status,size FROM photos WHERE ' + library.query(view) + ' ORDER BY id'):
                writer.writerow([row['path'], {0: '未筛选', 1: '保留', -1: '淘汰'}[row['status']], row['size']])
        from starlette.background import BackgroundTask
        return FileResponse(output, filename=f'photos-{view}.csv', background=BackgroundTask(output.unlink, missing_ok=True))

    return app, token


def main():
    parser = argparse.ArgumentParser(description='照片快筛：本机双图筛选与可撤回删除')
    parser.add_argument('--folder', help='照片文件夹，可包含子文件夹')
    parser.add_argument('--data-dir', type=Path, default=Path.home() / '.photo-sieve', help='资料库与缓存目录')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--cache-mb', type=int, default=2048)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if args.cache_mb < 64:
        parser.error('--cache-mb 必须至少为 64')
    library = Library(args.data_dir, args.cache_mb)
    app, token = create_app(library)
    if args.folder:
        library.scan(args.folder)
    url = f'http://127.0.0.1:{args.port}/#token={token}'
    print(f'照片快筛已准备启动。请保留终端窗口。\n本机地址：{url}\n资料库：{library.directory}')
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
