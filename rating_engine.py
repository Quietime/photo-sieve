"""Isolated Windows property handlers; their crashes/timeouts never run in the GUI process."""
from concurrent.futures import ProcessPoolExecutor,TimeoutError
from concurrent.futures.process import BrokenProcessPool
import faulthandler
import multiprocessing
import os
from pathlib import Path
import threading
import time
from native_rating import operate,RatingError

FAULT_FILE=None

def initialize(logs):
    global FAULT_FILE
    Path(logs).mkdir(parents=True,exist_ok=True)
    FAULT_FILE=open(Path(logs)/f'rating-worker-{os.getpid()}.fault.log','a',encoding='utf8')
    faulthandler.enable(file=FAULT_FILE,all_threads=True)

def job(path,stars,expected):
    faulthandler.dump_traceback_later(4 if stars is None else 10,repeat=True,file=FAULT_FILE)
    try:
        # The store itself checks expected stars before committing an undo.
        return operate(path,stars,expected)
    finally:faulthandler.cancel_dump_traceback_later()

class RatingEngine:
    def __init__(self,logs):
        self.logs=Path(logs);self.pool=None;self.write_pool=None;self.lock=threading.Lock();self.closed=False
    def run(self,path,stars=None,expected=None):
        if os.name!='nt':raise RatingError('Windows 系统评级仅在 Windows 上可用')
        with self.lock:
            if self.closed:raise RatingError('评级后台已关闭')
            attribute='pool' if stars is None else 'write_pool'
            if getattr(self,attribute) is None:
                setattr(self,attribute,ProcessPoolExecutor(2 if stars is None else 1,mp_context=multiprocessing.get_context('spawn'),initializer=initialize,initargs=(str(self.logs),)))
            pool=getattr(self,attribute);future=pool.submit(job,str(path),stars,expected)
        try:return future.result(timeout=8 if stars is None else 60)
        except (TimeoutError,BrokenProcessPool) as exc:
            self.reset(pool)
            raise RatingError('Windows 评级处理超时或异常退出，请检查属性处理器及评级后台日志') from exc
    def reset(self,pool):
        with self.lock:
            attribute='pool' if self.pool is pool else 'write_pool' if self.write_pool is pool else None
            if attribute is None:return
            processes=list((pool._processes or {}).values());setattr(self,attribute,None)
            for process in processes:
                if process.is_alive():process.terminate()
            for process in processes:
                process.join(timeout=2)
                if process.is_alive():process.kill();process.join(timeout=2)
            pool.shutdown(wait=False,cancel_futures=True)
    def close(self):
        with self.lock:pools=[self.pool,self.write_pool];self.closed=True
        for pool in pools:
            if pool:self.reset(pool)
