"""One oldest-first 10 GiB disk-preview budget across the installation's workspaces."""
from pathlib import Path
from contextlib import nullcontext, closing, contextmanager
import hashlib
import sqlite3
import time
import math
MAX_BYTES=10*1024**3

INDEX_SCHEMA='''CREATE TABLE IF NOT EXISTS cache_index(
    filename TEXT PRIMARY KEY, photo_id INTEGER NOT NULL,
    source_size INTEGER NOT NULL, source_mtime INTEGER NOT NULL, edge INTEGER NOT NULL)'''

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
    """Expire only registered, processed-photo previews, keeping undo grace and live decodes safe."""
    cache=Path(cache); cutoff=time.time()-grace; removed=0; forgotten=[]
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
                        processed=status==1 or present!=1 or (current_size,current_mtime)!=(size,mtime)
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
    """Closed projects have no decoder jobs; expire their processed-photo previews too."""
    removed=0
    projects=[Path(root),*Path(root).glob('libraries/*')]
    for project in projects:
        if project.resolve()==Path(active_directory).resolve() or project.is_symlink(): continue
        database=project/'library.sqlite3'; cache=project/'cache'
        if not database.is_file() or cache.is_symlink(): continue
        try:
            removed+=cleanup_indexed_cache(database,cache,grace)
        except (sqlite3.Error,OSError): continue
    return removed

def prune_workspaces(root, extra=None, limit=MAX_BYTES, cache_lock=None, include_projects=True):
    root=Path(root)
    folders={root/'cache'}
    if include_projects: folders.update(root.glob('libraries/*/cache'))
    if extra is not None: folders.add(Path(extra))
    files=[]
    for folder in folders:
        if folder.is_symlink(): continue
        for path in folder.iterdir() if folder.exists() else ():
            if path.suffix.lower() not in ('.jpg','.png'):continue
            try:
                if not path.is_symlink():
                    stat=path.stat(); files.append((stat.st_mtime,stat.st_size,path))
            except FileNotFoundError: pass
    total=sum(size for _,size,_ in files); removed=0
    if total>=limit and files:
        count=max(1,math.ceil(len(files)*.70))
        for _,size,path in sorted(files)[:count]:
            try:
                with cache_lock if cache_lock is not None and path.parent==Path(extra) else nullcontext():
                    path.unlink(missing_ok=True)
            except OSError: continue
            total-=size; removed+=1
    return removed,total
