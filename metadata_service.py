"""Current-first camera information, with one bounded background request."""
from collections import OrderedDict
import threading,time
import crashlog
from PySide6.QtCore import QObject,QThreadPool,QThread,QTimer

class MetadataService(QObject):
    def __init__(self,host):
        super().__init__(host);self.host=host;self.cache=OrderedDict();self.retries={};self.working=False;self.stopped=threading.Event();self.revision=0
        self.pool=QThreadPool(self);self.pool.setMaxThreadCount(1);self.pool.setThreadPriority(QThread.Priority.LowPriority)
        self.timer=QTimer(self);self.timer.setInterval(80);self.timer.timeout.connect(self.update);self.timer.start()

    def key(self,library,photo):return (str(library.database),photo['id'],photo['path'],photo['size'],photo['mtime'])

    def active(self):
        h=self.host
        return not self.stopped.is_set() and not h.closed and h.view=='pending' and h.stack.currentIndex()==0 and bool(h.queue)

    def update(self):
        if not self.active():return
        h=self.host;library=h.library;current=self.key(library,h.queue[0])
        text=self.cache.get(current)
        if text is None:text='拍摄参数暂未读到，稍后重试' if current in self.retries else '正在读取拍摄参数…'
        elif not text:text='未找到拍摄参数'
        h.details[1].set_text(text)
        if self.working:return
        # Snapshot only the nearby future. No FIFO of obsolete photo requests.
        horizon=max(1,min(64,getattr(h,'ahead_target',9)))
        wanted=[dict(p) for p in h.queue[:horizon] if p['id'] not in h.inflight and self.key(library,p) not in self.cache and time.monotonic()>=self.retries.get(self.key(library,p),(0,0))[1]][:9]
        if not wanted:return
        self.working=True;revision=self.revision
        def work():
            if self.stopped.is_set() or library is not h.library:return []
            wanted_ids={p['id'] for p in h.queue[:horizon]}-h.inflight
            photos=[p for p in wanted if p['id'] in wanted_ids]
            reader=h.metadata_reader
            if not hasattr(reader,'read_many'):photos=photos[:1]
            return library.read_metadata_batch(photos,reader,self.stopped)
        def done(rows):
            self.working=False
            if self.stopped.is_set():return
            if revision!=self.revision or library is not h.library:self.update();return
            for photo,text in rows:
                key=self.key(library,photo)
                if text is None:self.retry(key,'未能读取此照片的拍摄参数')
                else:self.cache[key]=text;self.retries.pop(key,None)
            while len(self.cache)>256:self.cache.popitem(last=False)
            self.update()
        def failed(message):
            self.working=False
            if self.stopped.is_set():return
            if revision!=self.revision or library is not h.library:self.update();return
            for photo in wanted:self.retry(self.key(library,photo),message)
            self.update()
        h.submit(work,done,failed,self.pool,-5)

    def retry(self,key,message):
        count=self.retries.get(key,(0,0))[0]+1;delay=min(30,2**min(count,5))
        self.retries[key]=(count,time.monotonic()+delay)
        while len(self.retries)>256:self.retries.pop(next(iter(self.retries)))
        crashlog.event('metadata_read_retry',photo_id=key[1],attempt=count,retry_in=delay,error=str(message)[:500])

    def clear(self):
        self.revision+=1;self.cache.clear();self.retries.clear()

    def shutdown(self):
        self.stopped.set();self.timer.stop();self.host.metadata_reader.close();self.pool.waitForDone()
