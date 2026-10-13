"""A cache of actual Windows ratings, and atomic bookkeeping for metadata-only changes."""
import os
from pathlib import Path
import shutil
import tempfile
import time
from native_rating import RatingError,value_from_stars

SCHEMA='''
CREATE TABLE IF NOT EXISTS rating_cache(
 photo_id INTEGER PRIMARY KEY,stars INTEGER,value INTEGER,writable INTEGER,size INTEGER,mtime INTEGER,
 state TEXT NOT NULL,error TEXT,checked_at REAL);
CREATE INDEX IF NOT EXISTS native_rating_filter ON rating_cache(state,stars,photo_id);
CREATE TABLE IF NOT EXISTS rating_history(
 seq INTEGER PRIMARY KEY,photo_id INTEGER,before INTEGER,after INTEGER,created_at REAL);
'''

class RatingRecords:
    def __init__(self,library):
        self.library=library
        with library.connect() as db:db.executescript(SCHEMA)
    def ids(self,scope='all',stars=None,unknown=False):
        where=self.library.query(scope)
        if scope not in ('all','keep'):raise RatingError('未知分级范围')
        match='c.photo_id=p.id AND c.size=p.size AND c.mtime=p.mtime'
        condition=' AND (c.state IS NULL)' if unknown else ' AND c.state=\'ready\' AND c.stars=?' if stars is not None else ''
        with self.library.connect() as db:
            return [r[0] for r in db.execute(f'SELECT p.id FROM photos p LEFT JOIN rating_cache c ON {match} WHERE {where}{condition} ORDER BY p.id',() if stars is None or unknown else (stars,))]
    def cached(self,ids):
        result={}
        with self.library.connect() as db:
            for start in range(0,len(ids),400):
                batch=ids[start:start+400]
                if not batch:continue
                for row in db.execute('SELECT c.* FROM rating_cache c JOIN photos p ON p.id=c.photo_id AND p.size=c.size AND p.mtime=c.mtime WHERE c.photo_id IN ('+','.join('?' for _ in batch)+')',batch):result[row['photo_id']]=dict(row)
        return result
    def cache(self,pid,stat,result=None,error=''):
        with self.library.connect() as db:
            db.execute('INSERT OR REPLACE INTO rating_cache VALUES (?,?,?,?,?,?,?,?,?)',(pid,result.get('stars') if result else None,result.get('value') if result else None,int(result.get('writable',False)) if result else 0,stat.st_size,stat.st_mtime_ns,'ready' if result else 'error',result.get('reason','') if result else error,time.time()))
    def read(self,pid,engine):
        with self.library.file_locks[pid%len(self.library.file_locks)]:
            photo=self.library.photo(pid);stat=Path(photo['path']).stat()
            try:result=engine.run(photo['path'])
            except Exception as exc:
                self.cache(pid,stat,error=str(exc));return {'photo_id':pid,'state':'error','stars':None,'writable':False,'error':str(exc)}
            self.cache(pid,stat,result)
            return {'photo_id':pid,'state':'ready',**result,'error':result.get('reason','')}
    def write(self,pid,stars,engine,expected=None,history=True):
        value_from_stars(stars)
        library=self.library
        with library.job_lock:
            if library.job['running']:raise RatingError('请等待扫描或导出完成后再评级')
            with library.file_locks[pid%len(library.file_locks)]:
                row=library.photo(pid);path=Path(row['path']);before=path.stat()
                if (before.st_size,before.st_mtime_ns)!=(row['size'],row['mtime']):raise RatingError('原文件已改变，请重新扫描后再评级')
                backup_root=library.directory/'rating-backups';backup_root.mkdir(exist_ok=True)
                descriptor,backup_name=tempfile.mkstemp(prefix=f'{pid}-',suffix=path.suffix,dir=backup_root);os.close(descriptor)
                backup=Path(backup_name)
                try:shutil.copy2(path,backup)
                except Exception:
                    backup.unlink(missing_ok=True);raise
                try:
                    result=engine.run(path,stars,expected)
                    # Keep file-date sorting stable while changing only its Windows metadata.
                    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns));after=path.stat()
                    with library.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        current=db.execute('SELECT size,mtime,present FROM photos WHERE id=?',(pid,)).fetchone()
                        if not current or not current['present'] or (current['size'],current['mtime'])!=(row['size'],row['mtime']):raise RatingError('资料库已改变，评级写入已撤销')
                        db.execute('UPDATE photos SET size=?,mtime=?,digest=NULL,error=NULL WHERE id=?',(after.st_size,after.st_mtime_ns,pid))
                        db.execute('UPDATE history SET size=?,mtime=? WHERE photo_id=? AND size=? AND mtime=?',(after.st_size,after.st_mtime_ns,pid,row['size'],row['mtime']))
                        if history and result['before']!=stars:db.execute('INSERT INTO rating_history(photo_id,before,after,created_at) VALUES (?,?,?,?)',(pid,result['before'],stars,time.time()))
                        db.execute('INSERT OR REPLACE INTO rating_cache VALUES (?,?,?,?,?,?,?,?,?)',(pid,result['stars'],result['value'],int(result['writable']),after.st_size,after.st_mtime_ns,'ready',result.get('reason',''),time.time()))
                    try:backup.unlink()
                    except OSError:pass
                    return {'photo_id':pid,'state':'ready',**result,'error':result.get('reason','')}
                except Exception:
                    # Property-handler crashes may leave partially written metadata.
                    try:shutil.copy2(backup,path);backup.unlink()
                    except OSError as exc:raise RatingError('评级失败，原文件备份保留在 '+str(backup)+'；请先检查恢复原文件。') from exc
                    raise
    def undo(self,engine):
        with self.library.connect() as db:row=db.execute('SELECT * FROM rating_history ORDER BY seq DESC LIMIT 1').fetchone()
        if not row:raise RatingError('没有可以撤销的评级')
        result=self.write(row['photo_id'],row['before'],engine,expected=row['after'],history=False)
        with self.library.connect() as db:db.execute('DELETE FROM rating_history WHERE seq=?',(row['seq'],))
        return result
