"""A bounded, foreground-friendly sweep of every imported, non-deleted photo.

Disk warming is independent of the current view/date range and the RAM horizon.
Each explicit start performs one pass. Capacity eviction never starts another pass.
Qt callbacks run through the host's existing submit/result bridge; no image pixels
or per-library list of completed IDs are retained by this service.
"""
from collections import OrderedDict,deque
from contextlib import nullcontext
import hashlib
import logging
from pathlib import Path
import time
from PIL import Image

from app import PREVIEW_EDGES


def jpeg_valid(path):
    try:
        with Image.open(path) as image:
            if image.format not in ('JPEG','MPO'):return False
            image.verify()
        return True
    except (OSError,ValueError,SyntaxError):return False


def discard_invalid(library,photo,path):
    locks=getattr(library,'file_locks',None);guard=locks[photo['id']%len(locks)] if locks else nullcontext()
    delta=0
    with guard:
        with getattr(library,'cache_lock',None) or nullcontext():
            try:
                if path.is_symlink() or not path.is_file() or jpeg_valid(path):return
                size=path.stat().st_size;path.unlink()
            except OSError:return
            if hasattr(library,'cache_sizes'):
                delta=-library.cache_sizes.pop(path.name,0)
                if hasattr(library,'cache_bytes'):library.cache_bytes=max(0,library.cache_bytes+delta)
            if hasattr(library,'cache_indexed'):library.cache_indexed.discard(path.name)
    changed=getattr(library,'cache_changed',None)
    if delta and changed:changed(delta)


def cache_exists(library,photo,edge,repair=False):
    """Check actual photo JPEG files, never a RAM/set/size-ledger success marker."""
    source=Path(photo['path']);size=photo['size'];mtime=photo['mtime']
    for candidate in PREVIEW_EDGES:
        if candidate<edge:continue
        paths=(library.cache_path(source,size,mtime,candidate),
               Path(library.cache)/(hashlib.sha256(f'{source}|{size}|{mtime}|{candidate}'.encode()).hexdigest()+'.jpg'))
        for path in paths:
            try:
                if path.is_symlink() or not path.is_file():continue
                if path.stat().st_size>0 and jpeg_valid(path):return True
                if repair:discard_invalid(library,photo,path)
            except OSError:continue
    return False


class FullCache:
    view='all'
    def __init__(self,host,on_change=None,page_size=256,max_jobs=None,auto_timer=True,retry_limit=128):
        self.host=host;self.on_change=on_change;self.page_size=max(1,min(1200,int(page_size)))
        workers=max(1,int(getattr(host,'workers',2)))
        self.max_jobs=max(1,min(12,int(max_jobs))) if max_jobs is not None else max(1,min(4,workers-1))
        self.retry_limit=max(0,min(128,int(retry_limit)))
        self.generation=0;self.library=None;self.edge=1600;self.running=False;self.finished=False
        self.pending=deque();self.retries=deque();self.active={};self.refreshes=OrderedDict();self.active_refresh=set();self.error_details=OrderedDict()
        self.fetching=False;self.exhausted=False;self.anchor=0;self.total=None;self.discovered=0
        self.visited=0;self.ready=0;self.generated=0;self.cached=0;self.errors=0;self.evicted=0;self.skipped=0
        self.phase='idle';self.page_errors=0;self.page_retry_at=0.;self._pumping=False;self._last_notify=0.
        self.refreshed=0;self.refresh_ready=0;self.eviction_start=0;self.retry_count=0
        self.timer=None
        if auto_timer:
            from PySide6.QtCore import QTimer
            self.timer=QTimer(host);self.timer.setInterval(250);self.timer.timeout.connect(self.pump)

    def status(self):
        capacity=max(0,int(getattr(self.library,'cache_evictions',0))-self.eviction_start)
        return {'running':self.running,'finished':self.finished,'sweep_complete':self.finished,
                'complete':self.finished and not self.errors and not self.evicted and not self.page_errors and not capacity,
                'phase':self.phase,'edge':self.edge,'total':self.total,'discovered':self.discovered,
                'visited':self.visited,'ready':self.ready,'generated':self.generated,'cached':self.cached,
                'errors':self.errors,'evicted':self.evicted,'skipped':self.skipped,'in_flight':len(self.active),
                'queued':len(self.pending),'retry_queued':len(self.retries),
                'refresh_queued':len(self.refreshes),'refresh_in_flight':len(self.active_refresh),'refreshed':self.refreshed,'refresh_ready':self.refresh_ready,
                'capacity_evictions':capacity,'listing_errors':self.page_errors,
                'error_details':list(self.error_details.items()),'resident_verified':False}

    def _notify(self,force=False):
        now=time.monotonic()
        if self.on_change and (force or now-self._last_notify>=.2):
            self._last_notify=now
            try:self.on_change(self.status())
            except Exception:logging.getLogger('photosieve').exception('Full-cache progress callback failed')

    def start(self,library,edge,total=None):
        if edge not in PREVIEW_EDGES:raise ValueError('Full-folder cache needs a display preview bucket; full originals are not warmed')
        self.stop(notify=False);self.library=library;self.edge=int(edge);self.running=True;self.finished=False
        self.anchor=0;self.total=max(0,int(total)) if total is not None else None;self.discovered=0
        self.visited=self.ready=self.generated=self.cached=self.errors=self.evicted=self.skipped=0
        self.refreshed=self.refresh_ready=0;self.eviction_start=int(getattr(library,'cache_evictions',0))
        self.retry_count=0
        self.fetching=False;self.exhausted=False;self.page_errors=0;self.page_retry_at=0.;self.phase='listing'
        if self.timer:self.timer.start()
        self._notify(True);self.pump()

    def stop(self,notify=True):
        self.generation+=1;self.running=False;self.finished=False;self.fetching=False
        self.pending.clear();self.retries.clear();self.active.clear();self.active_refresh.clear();self.refreshes.clear();self.error_details.clear();self.phase='stopped'
        if self.timer:self.timer.stop()
        if notify:self._notify(True)

    def _current(self,generation,library):
        return self.running and generation==self.generation and library is self.library and library is getattr(self.host,'library',library) and not getattr(self.host,'closed',False)

    def _foreground(self):
        if any(getattr(self.host,name,False) for name in ('closed','importing','cache_clearing','busy','pending_ops')):return True
        if any(getattr(self.host,name,None) for name in ('loading','focus_loading','strip_loading','thumb_loading','inflight')):return True
        return bool(getattr(self.library,'job',{}).get('running'))

    def _list_page(self):
        if self.fetching or self.exhausted or time.monotonic()<self.page_retry_at:return
        generation=self.generation;library=self.library;anchor=self.anchor;self.fetching=True
        def work():
            if not self._current(generation,library):return None
            return library.list_photos(self.view,anchor=anchor,direction='next',limit=self.page_size,sort='file')
        def done(rows):
            if not self._current(generation,library):return
            self.fetching=False
            if rows is None:self.pump();return
            self.page_errors=0;self.page_retry_at=0.
            if rows:
                self.anchor=rows[-1]['id'];self.discovered+=len(rows)
                self.total=max(self.total or 0,self.discovered);self.pending.extend((photo,0) for photo in rows)
            self.exhausted=len(rows)<self.page_size
            if self.exhausted:self.total=self.discovered
            self.pump()
        def failed(message):
            if not self._current(generation,library):return
            self.fetching=False;self.page_errors+=1
            if self.page_errors>=2:
                self.running=False;self.finished=True;self.phase='error';self._remember_error('listing',message)
                if self.timer:self.timer.stop()
            else:self.page_retry_at=time.monotonic()+1.;self.phase='retrying'
            self._notify(True)
        self.host.submit(work,done,failed)

    def _remember_error(self,pid,message):
        self.error_details[pid]=str(message)
        while len(self.error_details)>128:self.error_details.popitem(last=False)

    def refresh_photo(self,pid):
        """Recheck a restored photo without restarting the all-photo sweep; False means the bounded queue is full."""
        if self.library is None or self.library is not getattr(self.host,'library',self.library) or getattr(self.host,'closed',False):return False
        pid=int(pid)
        if pid not in self.refreshes and len(self.refreshes)>=128:return False
        self.refreshes[pid]=({'id':pid},0)
        if not self.running:
            self.exhausted=True;self.running=True;self.finished=False
            if self.timer:self.timer.start()
        self.pump();return True

    def prepare(self,library,current,edge):
        if cache_exists(library,current,edge,repair=True):return 'cached'
        payload=library.preview(current['id'],edge)
        if cache_exists(library,current,edge):return 'generated'
        if payload:return 'evicted'
        raise ValueError('预览未生成，磁盘中没有匹配分辨率的照片缓存')

    def consume(self,photo,result):
        return result

    def _submit(self,photo,attempt,refresh=False):
        pid=photo['id'];generation=self.generation;library=self.library;edge=self.edge
        self.active[pid]=photo
        if refresh:self.active_refresh.add(pid)
        def work():
            if not self._current(generation,library):return 'cancelled'
            # Native decoding already in progress is not preempted, but queued work
            # gives foreground navigation another chance before opening an original.
            if self._foreground():return 'deferred'
            try:current=dict(library.photo(pid))
            except Exception as error:
                if getattr(error,'status_code',None)==404:return 'skipped'
                raise
            return self.prepare(library,current,edge)
        def done(result):
            if not self._current(generation,library):return
            self.active.pop(pid,None)
            self.active_refresh.discard(pid)
            result=self.consume(photo,result)
            if result=='deferred':
                if refresh:self.refreshes[pid]=(photo,attempt)
                else:self.pending.appendleft((photo,attempt))
            elif result=='cancelled':pass
            else:
                if refresh:
                    self.refreshed+=1
                    if result in ('cached','generated'):self.refresh_ready+=1
                else:
                    self.visited+=1
                    if result in ('cached','generated'):self.ready+=1
                    if result=='cached':self.cached+=1
                    elif result=='generated':self.generated+=1
                if result=='evicted':self.evicted+=1
                elif result=='skipped':self.skipped+=1
                self.error_details.pop(pid,None)
            self.pump()
        def failed(message):
            if not self._current(generation,library):return
            self.active.pop(pid,None)
            self.active_refresh.discard(pid)
            if attempt==0 and self.retry_count<self.retry_limit:
                self.retry_count+=1
                self.retries.append((photo,1,refresh))
            else:
                self.errors+=1
                if refresh:self.refreshed+=1
                else:self.visited+=1
            self._remember_error(pid,message);self.pump()
        self.host.submit(work,done,failed,self.host.image_pool,-30)

    def pump(self):
        if not self.running or self._pumping:return
        if self.library is not getattr(self.host,'library',self.library) or getattr(self.host,'closed',False):self.stop();return
        self._pumping=True
        try:
            if self._foreground():self.phase='paused';self._notify();return
            self.phase='building'
            while len(self.active)<self.max_jobs:
                refresh_pid=next((pid for pid in self.refreshes if pid not in self.active),None)
                if refresh_pid is not None:
                    photo,attempt=self.refreshes.pop(refresh_pid);self._submit(photo,attempt,True)
                elif self.pending:
                    if self.pending[0][0]['id'] in self.active:break
                    photo,attempt=self.pending.popleft();self._submit(photo,attempt)
                elif self.exhausted and self.retries:
                    photo,attempt,refresh=self.retries.popleft()
                    if photo['id'] in self.active:self.retries.appendleft((photo,attempt,refresh));break
                    self._submit(photo,attempt,refresh)
                else:break
            if not self.pending and not self.exhausted:self._list_page()
            if self.exhausted and not self.pending and not self.retries and not self.refreshes and not self.active and not self.fetching:
                self.running=False;self.finished=True
                capacity=self.status()['capacity_evictions']
                self.phase='complete' if not self.errors and not self.evicted and not capacity else 'complete_with_errors' if self.errors else 'capacity_limited'
                if self.timer:self.timer.stop()
                self._notify(True)
            else:self._notify()
        finally:self._pumping=False
