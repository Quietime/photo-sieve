"""Current-first camera information, with one bounded background request."""
from collections import OrderedDict
import threading
from PySide6.QtCore import QObject,QThreadPool,QThread,QTimer

class MetadataService(QObject):
    def __init__(self,host):
        super().__init__(host);self.host=host;self.cache=OrderedDict();self.working=False;self.stopped=threading.Event()
        self.pool=QThreadPool(self);self.pool.setMaxThreadCount(1);self.pool.setThreadPriority(QThread.Priority.LowPriority)
        self.timer=QTimer(self);self.timer.setInterval(80);self.timer.timeout.connect(self.update);self.timer.start()

    def key(self,library,photo):return (str(library.database),photo['id'],photo['path'],photo['size'],photo['mtime'])

    def active(self):
        h=self.host
        return not self.stopped.is_set() and not h.closed and h.view=='pending' and h.stack.currentIndex()==0 and bool(h.queue)

    def update(self):
        if not self.active():return
        h=self.host;library=h.library;current=self.key(library,h.queue[0])
        h.details[1].set_text(self.cache.get(current,''))
        if self.working:return
        # Snapshot only the nearby future. No FIFO of obsolete photo requests.
        wanted=[dict(p) for p in h.queue[:9] if p['id'] not in h.inflight and self.key(library,p) not in self.cache]
        if not wanted:return
        self.working=True
        def work():
            if self.stopped.is_set() or library is not h.library:return []
            wanted_ids={p['id'] for p in h.queue[:9]}-h.inflight
            photos=[p for p in wanted if p['id'] in wanted_ids]
            reader=h.metadata_reader
            if not hasattr(reader,'read_many'):photos=photos[:1]
            return library.read_metadata_batch(photos,reader,self.stopped)
        def done(rows):
            self.working=False
            if self.stopped.is_set():return
            for photo,text in rows:self.cache[self.key(library,photo)]=text
            while len(self.cache)>256:self.cache.popitem(last=False)
            self.update()
        def failed(_):
            self.working=False
            # A transient read failure must not flood the UI task queue.
        h.submit(work,done,failed,self.pool,-5)

    def shutdown(self):
        self.stopped.set();self.timer.stop();self.host.metadata_reader.close();self.pool.clear();self.pool.waitForDone()
