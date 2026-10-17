import os,unittest
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QWidget
from xmp_queue import AutoXmpQueue

class XmpQueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.page=QWidget();self.page.pending_count=0;self.jobs=[];self.errors=[];library=object()
        self.host=SimpleNamespace(library=library,closed=False,closing=False,pending_ops=0,op_pool=object(),update_actions=lambda:None,refresh_state=lambda _:None,error=self.errors.append,submit=lambda fn,done,fail,pool:self.jobs.append((fn,done,fail)))
        self.tools=SimpleNamespace(host=self.host,page=self.page,records=SimpleNamespace(library=library),syncing=set(),xmp_errors={});self.queue=AutoXmpQueue(self.tools)
    def tearDown(self):self.queue.timer.stop();self.page.deleteLater()
    def drain(self):
        while self.jobs:
            fn,done,fail=self.jobs.pop(0)
            try:result=fn()
            except Exception as exc:fail(str(exc))
            else:done(result)
    def test_coalesces_fields_and_waits_for_native(self):
        self.page.pending_count=32
        for i in range(500):self.queue.schedule(i%32,stars=i%6)
        self.queue.schedule(0,label='');self.queue.schedule(0,stars=0);self.queue.flush()
        self.assertFalse(self.jobs);self.assertEqual(self.host.pending_ops,1)
        calls=[];self.page.pending_count=0
        with patch('xmp_queue.sync_out',side_effect=lambda records,pid,**fields:calls.append((pid,fields))):self.queue.flush();self.drain()
        self.assertEqual(len(calls),32);self.assertIn((0,{'stars':0,'label':''}),calls);self.assertEqual(self.host.pending_ops,0);self.assertFalse(self.tools.syncing)
    def test_close_flushes_and_failure_releases_reservation(self):
        self.host.closing=True;self.queue.schedule(1,stars=4)
        with patch('xmp_queue.sync_out',side_effect=OSError('disk full')):self.queue.flush();self.drain()
        self.assertEqual(self.host.pending_ops,0);self.assertIn(1,self.tools.xmp_errors);self.assertTrue(self.errors)
    def test_edit_during_batch_is_not_lost(self):
        self.queue.schedule(1,stars=1);self.queue.flush();self.queue.schedule(1,stars=5)
        calls=[]
        with patch('xmp_queue.sync_out',side_effect=lambda records,pid,**fields:calls.append(fields)):self.drain()
        self.assertEqual(calls,[{'stars':1},{'stars':5}]);self.assertEqual(self.host.pending_ops,0)
