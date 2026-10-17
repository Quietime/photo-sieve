"""Bounded background analysis of existing previews; no original decoding."""
import json,threading,time
from pathlib import Path
from PIL import Image
from PySide6.QtCore import QObject,QThreadPool,QThread,QTimer,Signal
from workflow_data import WorkRecords,signature,captured_time,VERSION
from face_quality import assess_face

class WorkAnalysis(QObject):
    changed=Signal()
    def __init__(self,host):
        super().__init__(host);self.host=host;self.pool=QThreadPool(self);self.pool.setMaxThreadCount(1);self.pool.setThreadPriority(QThread.Priority.LowPriority)
        self.library=None;self.records=None;self.generation=0;self.working=False;self.stopped=False;self.anchor=0;self.done=0;self.missing=0;self.total=0;self.errors=0;self.finished=False;self.retry_at=0;self.revisit=set();self.lock=threading.Lock()
        self.timer=QTimer(self);self.timer.setInterval(350);self.timer.timeout.connect(self.pump);self.timer.start()
    def start(self,library,force=False):
        if library is self.library and not self.finished and not force:return
        self.generation+=1;self.library=library;self.records=WorkRecords(library);self.anchor=0;self.done=0;self.missing=0;self.errors=0;self.finished=False;self.revisit.clear();self.retry_at=0
        with library.connect() as db:self.total=db.execute('SELECT count(*) FROM photos WHERE present=1').fetchone()[0]
        self.changed.emit()
    def refresh(self,pid):
        if self.library is self.host.library:
            if len(self.revisit)<128:self.revisit.add(pid)
            self.finished=False
    def clear(self,delete=True):
        self.generation+=1;self.anchor=0;self.done=0;self.missing=0;self.finished=False;self.revisit.clear()
        if delete and self.records:
            with self.library.connect() as db:db.execute('DELETE FROM work_features')
    def status(self):return dict(done=self.done,total=self.total,errors=self.errors,skipped=self.missing,finished=self.finished,running=not self.finished and self.library is not None)
    def pump(self):
        h=self.host
        if self.stopped or self.working or self.finished or not self.library or self.library is not h.library or time.monotonic()<self.retry_at:return
        if h.closed or h.closing or h.busy or h.importing or h.cache_clearing or h.pending_ops or self.library.job['running']:return
        if (h.rating_active and h.rating_page.loading) or (not h.rating_active and (h.loading or h.focus_loading)):return
        self.working=True;library=self.library;records=self.records;gen=self.generation;anchor=self.anchor;revisit=list(self.revisit)[:8]
        for pid in revisit:self.revisit.discard(pid)
        def current():return not self.stopped and gen==self.generation and library is h.library
        def work():
            if not current() or h.pending_ops:return None
            with library.connect() as db:
                rows=[dict(r) for r in db.execute('SELECT * FROM photos WHERE present=1 AND id>? ORDER BY id LIMIT 8',(anchor,))] if not revisit else [dict(r) for r in db.execute('SELECT * FROM photos WHERE present=1 AND id IN ('+','.join('?' for _ in revisit)+')',revisit)]
                faces_exist=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='face_metadata'").fetchone())
            missing=0;failures=0
            raw_rows=[]
            for p in rows:
                if not current() or h.pending_ops:return None
                with library.connect() as db:
                    old=db.execute('SELECT 1 FROM work_features WHERE photo_id=? AND size=? AND mtime=? AND version=?',(p['id'],p['size'],p['mtime'],VERSION)).fetchone()
                if old and not revisit:continue
                path=None
                # The cache index includes migrated preview names; never manufacture an original decode.
                with library.connect() as db:
                    candidates=list(db.execute('SELECT filename FROM cache_index WHERE photo_id=? AND source_size=? AND source_mtime=? AND edge IN (320,1600,2400,3200,4800) ORDER BY edge DESC',(p['id'],p['size'],p['mtime'])))
                for entry in candidates:
                    name=entry[0]
                    if Path(name).name!=name or '/' in name or '\\' in name or Path(name).suffix.lower()!='.jpg':continue
                    candidate=library.cache/name
                    if not candidate.is_symlink() and candidate.is_file():path=candidate;break
                if path is None:missing+=1;continue
                try:
                    with library.file_locks[p['id']%len(library.file_locks)]:
                        if not current():return None
                        st=Path(p['path']).stat()
                        if (st.st_size,st.st_mtime_ns)!=(p['size'],p['mtime']):continue
                        with Image.open(path) as image:feature=dict(signature=signature(image),captured=0,time_source='',quality={'state':'pending','text':'等待后台人脸结果'})
                        captured,source=captured_time(p['path'],p['mtime']);feature.update(captured=captured,time_source=source)
                        if faces_exist:
                            with library.connect() as db:face=db.execute('SELECT faces_json,metadata_json FROM face_metadata WHERE path=? AND source_size=? AND source_mtime=?',(p['path'],p['size'],p['mtime'])).fetchone()
                            if face:
                                boxes=json.loads(face['faces_json'])
                                if not boxes:feature['quality']={'state':'no_face','text':'未检测到人脸'}
                                else:
                                    from face_service import cache_path
                                    crop=cache_path(library,p)
                                    if crop.is_file() and not crop.is_symlink():
                                        with Image.open(crop) as image:feature['quality']=assess_face(image,dict(boxes[0],partial=json.loads(face['metadata_json']).get('is_partial',False)))
                        if current():records.save_feature(p,feature)
                        if Path(p['path']).suffix.lower() in {'.arw','.nef','.cr2','.cr3','.dng','.raf','.rw2','.orf'}:raw_rows.append((p,feature))
                except (OSError,ValueError,TypeError,json.JSONDecodeError):failures+=1
            # One read-only RAW metadata process per small background batch.
            if raw_rows and current():
                try:
                    from photo_metadata import _run_metadata,_path_key
                    from datetime import datetime
                    info={_path_key(r['SourceFile']):r for r in _run_metadata([p['path'] for p,_ in raw_rows]) if r.get('SourceFile')}
                    for p,f in raw_rows:
                        value=info.get(_path_key(p['path']),{}).get('DateTimeOriginal')
                        if value:
                            f.update(captured=datetime.strptime(str(value)[:19],'%Y:%m:%d %H:%M:%S').timestamp(),time_source='拍摄时间')
                            if current():records.save_feature(p,f)
                except Exception:pass
            return rows,missing,failures
        def done(result):
            self.working=False
            if not current():return
            if result is None:
                for pid in revisit:
                    if len(self.revisit)<128:self.revisit.add(pid)
            if result:
                rows,missing,failures=result;self.errors+=failures;self.missing+=missing
                if not revisit:
                    if rows:self.anchor=rows[-1]['id'];self.done+=len(rows)
                    if len(rows)<8:
                        if self.missing and h.full_cache.running:self.anchor=0;self.done=0;self.missing=0;self.retry_at=time.monotonic()+2
                        else:self.finished=not self.revisit
                self.changed.emit()
        def failed(message):self.working=False;self.errors+=1;self.retry_at=time.monotonic()+3;self.changed.emit()
        h.submit(work,done,failed,self.pool,-30)
    def shutdown(self):self.stopped=True;self.generation+=1;self.timer.stop();self.pool.clear();self.pool.waitForDone()
