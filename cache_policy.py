"""One oldest-first 10 GiB disk-preview budget across the installation's workspaces."""
from pathlib import Path
from contextlib import nullcontext, closing
import hashlib
import sqlite3
import time
MAX_BYTES=10*1024**3

def expire_inactive_workspaces(root, active_directory, grace=120):
    """Closed projects have no decoder jobs; expire their processed-photo previews too."""
    from app import cache_file
    cutoff=time.time()-grace; removed=0
    projects=[Path(root),*Path(root).glob('libraries/*')]
    for project in projects:
        if project.resolve()==Path(active_directory).resolve() or project.is_symlink(): continue
        database=project/'library.sqlite3'; cache=project/'cache'
        if not database.is_file() or cache.is_symlink(): continue
        existing={p.name for p in cache.glob('*.jpg')}
        if not existing: continue
        try:
            with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                rows=db.execute('SELECT path,size,mtime FROM photos WHERE status=1 OR present=0').fetchall()
            for source,size,mtime in rows:
                names=[cache_file(cache,source,size,mtime,edge).name for edge in (0,320,1600)]
                names += [hashlib.sha256(f'{source}|{size}|{mtime}|{edge}'.encode()).hexdigest()+'.jpg' for edge in (320,1600)]
                for name in names:
                    if name not in existing: continue
                    path=cache/name
                    if not path.is_symlink() and path.exists() and path.stat().st_mtime<=cutoff:
                        path.unlink(); removed+=1
        except (sqlite3.Error,OSError): continue
    return removed

def prune_workspaces(root, extra=None, limit=MAX_BYTES, cache_lock=None):
    root=Path(root)
    folders={root/'cache',*root.glob('libraries/*/cache')}
    if extra is not None: folders.add(Path(extra))
    files=[]
    for folder in folders:
        if folder.is_symlink(): continue
        for path in folder.glob('*.jpg'):
            try:
                if not path.is_symlink():
                    stat=path.stat(); files.append((stat.st_mtime,stat.st_size,path))
            except FileNotFoundError: pass
    total=sum(size for _,size,_ in files); removed=0
    if total>limit:
        # Finish the whole eviction pass, rather than deleting one file per timer tick.
        for _,size,path in sorted(files):
            try:
                with cache_lock if cache_lock is not None and path.parent==Path(extra) else nullcontext():
                    path.unlink(missing_ok=True)
            except OSError: continue
            total-=size; removed+=1
            if total<=limit: break
    return removed,total
