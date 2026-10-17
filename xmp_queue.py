"""Coalesce automatic sidecar updates while retaining every verified native write."""
from collections import OrderedDict
from PySide6.QtCore import QTimer
from xmp_sync import sync_out

class AutoXmpQueue:
    def __init__(self,tools):
        self.tools=tools;self.pending=OrderedDict();self.reserved=False;self.running=False
        self.timer=QTimer(tools.page);self.timer.setInterval(150);self.timer.timeout.connect(self.flush)
    def schedule(self,pid,stars=None,label=None):
        t=self.tools;h=t.host
        if h.closed or not t.records or t.records.library is not h.library:return
        if not self.reserved:
            self.records=t.records;self.reserved=True;h.pending_ops+=1
        fields=self.pending.setdefault(pid,{})
        if stars is not None:fields['stars']=stars
        if label is not None:fields['label']=label
        t.syncing.add(pid);self.timer.start()
    def flush(self):
        t=self.tools;h=t.host
        if self.running or t.page.pending_count:return
        if not self.pending:
            self.timer.stop()
            if self.reserved:
                self.reserved=False;h.pending_ops-=1;h.update_actions();h.refresh_state(0)
            return
        batch=[self.pending.popitem(last=False) for _ in range(min(4,len(self.pending)))]
        records=self.records;self.running=True
        def work():
            results=[]
            for pid,fields in batch:
                try:sync_out(records,pid,**fields);results.append((pid,None))
                except Exception as exc:results.append((pid,str(exc)))
            return results
        def done(results):
            self.running=False
            for pid,error in results:
                if pid not in self.pending:t.syncing.discard(pid)
                if records is not t.records:continue
                if error:t.xmp_errors[pid]=error
                else:t.xmp_errors.pop(pid,None)
            failures=[error for _,error in results if error]
            if failures:h.error('XMP 未同步（原图评级已保留）：'+failures[0])
            self.flush()
        def failed(message):done([(pid,message) for pid,_ in batch])
        h.submit(work,done,failed,pool=h.op_pool)
