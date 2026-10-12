"""Keep project records in the install folder; import existing SQLite records without copying photos."""
import hashlib
from pathlib import Path
import sqlite3
from contextlib import closing
from app import Library

def workspace_key(folder):
    return hashlib.sha256(str(Path(folder).resolve()).encode()).hexdigest()[:16]

def open_workspace(root, folder, previous=None, cache_mb=None):
    target=Path(root)/'libraries'/workspace_key(folder)
    database=target/'library.sqlite3'
    if not database.exists():
        legacy=Path.home()/'.photo-sieve'
        candidates=[]
        if previous:
            old=Path(previous).resolve(); candidates += [old,old/'libraries'/workspace_key(folder)]
            if old.parent.name=='libraries': candidates.append(old.parent/workspace_key(folder))
        candidates += [legacy,legacy/'libraries'/workspace_key(folder),Path(root)]
        for candidate in candidates:
            source=candidate/'library.sqlite3'
            if not source.is_file() or source.resolve()==database.resolve(): continue
            try:
                with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as old:
                    row=old.execute("SELECT value FROM meta WHERE key='root'").fetchone()
                    if not row or Path(row[0]).resolve()!=Path(folder).resolve(): continue
                    target.mkdir(parents=True,exist_ok=True)
                    temporary=target/'migration.sqlite3'
                    with closing(sqlite3.connect(temporary)) as new: old.backup(new)
                    temporary.replace(database)
                break
            except sqlite3.Error:
                if database.exists(): break
                (target/'migration.sqlite3').unlink(missing_ok=True)
    return Library(target,cache_mb)
