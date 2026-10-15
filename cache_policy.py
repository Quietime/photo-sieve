"""One oldest-first 10 GiB disk-preview budget across the installation's workspaces."""
from pathlib import Path
from contextlib import nullcontext, closing, contextmanager
import hashlib
import sqlite3
import time
import math
import stat
MAX_BYTES=10*1024**3

INDEX_SCHEMA='''CREATE TABLE IF NOT EXISTS cache_index(
    filename TEXT PRIMARY KEY, photo_id INTEGER NOT NULL,
    source_size INTEGER NOT NULL, source_mtime INTEGER NOT NULL, edge INTEGER NOT NULL)'''

def linked_directory(path):
    """Never traverse a substituted derived-cache directory or Windows junction."""
    path=Path(path)
    try:return path.is_symlink() or bool(getattr(path.lstat(),'st_file_attributes',0)&0x400)
    except FileNotFoundError:return False

def workspace_directories(root):
    root=Path(root)
    if linked_directory(root):return
    yield root
    projects=root/'libraries'
    if projects.is_dir() and not linked_directory(projects):
        for project in projects.iterdir():
            if project.is_dir() and not linked_directory(project):yield project

def ensure_cache_index(db, cache):
    """Migrate pre-index previews once; later maintenance never hashes all photo rows."""
    from app import cache_file
    db.execute(INDEX_SCHEMA)
    if db.execute("SELECT 1 FROM meta WHERE key='cache_index_version' AND value='1'").fetchone(): return
    existing={p.name for p in Path(cache).glob('*.jpg') if not p.is_symlink()}
    found=[]
    if existing:
        # Stream metadata only for this one upgrade pass. WAL readers do not block review writes.
        for photo_id,source,size,mtime in db.execute('SELECT id,path,size,mtime FROM photos'):
            for edge in (0,320,1600):
                names=[cache_file(cache,source,size,mtime,edge).name]
                names.append(hashlib.sha256(f'{source}|{size}|{mtime}|{edge}'.encode()).hexdigest()+'.jpg')
                if edge==0: names.append(hashlib.sha256(f'v2|{source}|{size}|{mtime}|0'.encode()).hexdigest()+'.jpg')
                for name in names:
                    if name in existing: found.append((name,photo_id,size,mtime,edge))
    db.executemany('INSERT OR IGNORE INTO cache_index VALUES (?,?,?,?,?)',found)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('cache_index_version','1')")
    db.commit()

@contextmanager
def available_lock(lock):
    acquired=lock is None or lock.acquire(blocking=False)
    try:yield acquired
    finally:
        if lock is not None and acquired:lock.release()

def cleanup_indexed_cache(database, cache, grace=120, file_locks=None, cache_lock=None, known=None):
    """Retain all present originals; expire only deleted/missing or changed-source derivatives."""
    cache=Path(cache); cutoff=time.time()-grace; removed=0; forgotten=[]
    if linked_directory(cache):return 0
    with closing(sqlite3.connect(database,timeout=30)) as db:
        db.execute('PRAGMA busy_timeout=30000'); ensure_cache_index(db,cache)
        # Scales with cached previews, not all 100,000 reviewed originals.
        rows=db.execute('''SELECT i.filename,i.photo_id,i.source_size,i.source_mtime,
                    p.status,p.present,p.size,p.mtime FROM cache_index i
                    LEFT JOIN photos p ON p.id=i.photo_id''').fetchall()
        for name,photo_id,size,mtime,status,present,current_size,current_mtime in rows:
            # Filenames are hashes we generate; never follow a forged path out of cache.
            if Path(name).name!=name or '/' in name or '\\' in name:
                forgotten.append((name,)); continue
            path=cache/name
            with available_lock(file_locks[photo_id % len(file_locks)] if file_locks else None) as available:
                if not available:continue
                with available_lock(cache_lock) as available:
                    if not available:continue
                    try:
                        if path.is_symlink(): forgotten.append((name,)); continue
                        stat=path.stat()
                        processed=present!=1 or (current_size,current_mtime)!=(size,mtime)
                        if processed and stat.st_mtime<=cutoff:
                            path.unlink(); removed+=1; forgotten.append((name,))
                    except FileNotFoundError: forgotten.append((name,))
        with db:
            db.executemany('DELETE FROM cache_index WHERE filename=?',forgotten)
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='face_metadata'").fetchone():
                db.execute('''DELETE FROM face_metadata WHERE NOT EXISTS
                    (SELECT 1 FROM photos p WHERE p.path=face_metadata.path
                     AND p.size=face_metadata.source_size AND p.mtime=face_metadata.source_mtime)''')
        if known is not None: known.difference_update(name for name, in forgotten)
    return removed

def expire_inactive_workspaces(root, active_directory, grace=120):
    """Keep closed projects' pending/kept previews; expire deleted or outdated derivatives."""
    removed=0
    for project in workspace_directories(root):
        if project.resolve()==Path(active_directory).resolve(): continue
        database=project/'library.sqlite3'; cache=project/'cache'
        if not database.is_file() or linked_directory(cache): continue
        try:
            removed+=cleanup_indexed_cache(database,cache,grace)
        except (sqlite3.Error,OSError): continue
    return removed

def prune_workspaces(root, extra=None, limit=MAX_BYTES, cache_lock=None, include_projects=True):
    root=Path(root)
    folders=set() if linked_directory(root) else {root/'cache'}
    if include_projects: folders.update(project/'cache' for project in workspace_directories(root))
    if extra is not None: folders.add(Path(extra))
    active_cache=Path(extra) if extra is not None else None
    files=[]
    for folder in folders:
        if linked_directory(folder): continue
        for path in folder.iterdir() if folder.exists() else ():
            if path.suffix.lower() not in ('.jpg','.png'):continue
            try:
                if not path.is_symlink():
                    information=path.stat()
                    if stat.S_ISREG(information.st_mode):files.append((information.st_mtime,information.st_size,path))
            except FileNotFoundError: pass
    total=sum(size for _,size,_ in files); removed=0
    if total>limit and files:
        count=max(1,math.ceil(len(files)*.70))
        for _,size,path in sorted(files)[:count]:
            try:
                with cache_lock if cache_lock is not None and path.parent==active_cache else nullcontext():
                    path.unlink(missing_ok=True)
            except OSError: continue
            total-=size; removed+=1
    return removed,total


def clear_workspace_caches(root,active):
    """Explicit user cleanup: only derived cache directories, never source/trash/ratings."""
    removed=active.clear_preview_cache(include_recognition=True)
    root=Path(root)
    for project in workspace_directories(root):
        cache=project/'cache';database=project/'library.sqlite3'
        if project.resolve()==active.directory.resolve() or linked_directory(cache):continue
        if not cache.is_dir():continue
        for path in cache.iterdir():
            if path.suffix.lower() not in ('.jpg','.png') or path.is_symlink() or not path.is_file():continue
            try:path.unlink();removed+=1
            except FileNotFoundError:pass
        if database.is_file():
            with closing(sqlite3.connect(database,timeout=30)) as db:
                with db:
                    for table in ('cache_index','face_metadata'):
                        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():db.execute('DELETE FROM '+table)
    return removed
