"""Preserve verified pixel derivatives after a metadata-only native rating change."""
import hashlib,json,logging
from pathlib import Path

def preserve(library,photo,size,mtime):
    if (photo['size'],photo['mtime'])==(size,mtime):return
    try:
        with library.cache_lock:
            with library.connect() as db:
                rows=list(db.execute('SELECT filename,edge FROM cache_index WHERE photo_id=? AND source_size=? AND source_mtime=?',(photo['id'],photo['size'],photo['mtime'])))
                face_rows=list(db.execute('SELECT * FROM face_metadata WHERE path=? AND source_size=? AND source_mtime=?',(photo['path'],photo['size'],photo['mtime']))) if db.execute("SELECT 1 FROM sqlite_master WHERE name='face_metadata'").fetchone() else []
                face_targets={}
                for face in face_rows:
                    key=hashlib.sha256(json.dumps([str(photo['path']),int(size),int(mtime),face['engine_version']],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
                    face_targets['face-'+face['cache_key']+'.png']='face-'+key+'.png'
                    db.execute('INSERT OR REPLACE INTO face_metadata VALUES(?,?,?,?,?,?,?)',(key,face['path'],size,mtime,face['engine_version'],face['faces_json'],face['metadata_json']))
                for name,edge in rows:
                    if Path(name).name!=name or '/' in name or '\\' in name:continue
                    old=library.cache/name;target=library.cache/face_targets[name] if name in face_targets else library.cache_path(photo['path'],size,mtime,edge) if edge!=9001 else None
                    if not target or old.is_symlink() or not old.is_file() or target.is_symlink():continue
                    old.replace(target);db.execute('DELETE FROM cache_index WHERE filename=?',(name,));db.execute('INSERT OR REPLACE INTO cache_index VALUES(?,?,?,?,?)',(target.name,photo['id'],size,mtime,edge))
                    length=library.cache_sizes.pop(name,0);previous=library.cache_sizes.get(target.name,0);library.cache_sizes[target.name]=length or target.stat().st_size;library.cache_bytes=max(0,library.cache_bytes-previous)
                    library.cache_indexed.discard(name);library.cache_indexed.add(target.name)
    except Exception:logging.getLogger('photosieve').exception('Metadata saved, derived cache rekey deferred')
