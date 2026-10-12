"""Local persistent diagnostics, including native faults and unexpected process exits.

No Qt dependency: installed before Qt imports and inside isolated photo decoders.
"""
import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time

LOGGER=logging.getLogger('photosieve')
LOGGER.addHandler(logging.NullHandler())
SESSION=None
_files=[]
_qt_handler=None

def _write_state(path,state):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)

def _alive(pid):
    if os.name!='nt':
        try: os.kill(pid,0); return True
        except OSError: return False
    import ctypes
    from ctypes import wintypes as W
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];api.OpenProcess.restype=W.HANDLE
    api.CloseHandle.argtypes=[W.HANDLE]
    handle=api.OpenProcess(0x1000,False,pid)
    if not handle:return False
    try:
        code=W.DWORD();api.GetExitCodeProcess.argtypes=[W.HANDLE,ctypes.POINTER(W.DWORD)]
        return bool(api.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
    finally:api.CloseHandle(handle)

def record_exit(path,code=None):
    """The independent monitor can record termination even when no Python hook ran."""
    path=Path(path)
    try:
        state=json.loads(path.read_text('utf-8'))
        if code in (None,0) and state.get('status')=='finished':return
        state.update(status='abnormal',ended=time.time(),exit_code=code,
            exit_hex=f'0x{code&0xffffffff:08X}' if code is not None else 'unknown')
        _write_state(path,state)
        (path.parent/'unexpected-exit.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    except (OSError,ValueError):pass

def watch_process(pid,path):
    def clean_shutdown():
        try:return json.loads(Path(path).read_text('utf-8')).get('status')=='finished'
        except (OSError,ValueError):return False
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes as W
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];api.OpenProcess.restype=W.HANDLE
        api.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]
        api.GetExitCodeProcess.argtypes=[W.HANDLE,ctypes.POINTER(W.DWORD)]
        api.CloseHandle.argtypes=[W.HANDLE]
        handle=api.OpenProcess(0x00100000|0x1000,False,pid)
        if not handle:record_exit(path);return 0
        try:
            while api.WaitForSingleObject(handle,100)==258:
                if clean_shutdown():return 0
            code=W.DWORD();record_exit(path,code.value if api.GetExitCodeProcess(handle,ctypes.byref(code)) else None)
        finally:api.CloseHandle(handle)
    elif hasattr(os,'pidfd_open'):
        import select
        try:descriptor=os.pidfd_open(pid)
        except OSError:record_exit(path);return 0
        try:
            poll=select.poll();poll.register(descriptor,select.POLLIN)
            while not poll.poll(100):
                if clean_shutdown():return 0
            record_exit(path)
        finally:os.close(descriptor)
    else:
        while _alive(pid):
            if clean_shutdown():return 0
            time.sleep(.1)
        record_exit(path)
    return 0

class Session:
    def __init__(self,root,monitor=True):
        from version import APP_VERSION
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.previous=[]
        for marker in self.root.glob('session-*/state.json'):
            try:
                old=json.loads(marker.read_text('utf-8'))
                if old.get('status')=='running' and not _alive(old['pid']):record_exit(marker)
                old=json.loads(marker.read_text('utf-8'))
                if old.get('status')=='abnormal':self.previous.append(old)
            except (OSError,ValueError,KeyError):pass
        name=time.strftime('session-%Y%m%d-%H%M%S',time.gmtime())+f'-{os.getpid()}'
        self.directory=self.root/name;self.directory.mkdir(exist_ok=True)
        self.path=self.directory/'state.json';self.lock=threading.Lock();self.last_write=0
        self.state={'version':APP_VERSION,'pid':os.getpid(),'started':time.time(),'status':'running',
                    'platform':platform.platform(),'stage':'startup'}
        self.update(force=True)
        handler=RotatingFileHandler(self.directory/'application.log',maxBytes=2*1024**2,backupCount=3,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s [%(threadName)s] %(message)s'))
        LOGGER.addHandler(handler);LOGGER.setLevel(logging.INFO);self.handler=handler
        fault=(self.directory/'native-fault.log').open('a',encoding='utf-8',buffering=1)
        _files.append(fault);faulthandler.enable(fault,all_threads=True)
        self.original_hooks=(sys.excepthook,threading.excepthook)
        def exception(kind,value,tb):
            LOGGER.critical('Unhandled Python exception',exc_info=(kind,value,tb))
            self.update(force=True,last_exception=str(value))
            self.original_hooks[0](kind,value,tb)
        def thread_exception(args):
            LOGGER.error('Unhandled thread exception',exc_info=(args.exc_type,args.exc_value,args.exc_traceback))
            self.update(force=True,last_exception=str(args.exc_value))
        sys.excepthook=exception;threading.excepthook=thread_exception
        if sys.stderr is None:
            stream=(self.directory/'console.log').open('a',encoding='utf-8',buffering=1)
            sys.stderr=stream;sys.stdout=stream;_files.append(stream)
        os.environ['PHOTOSIEVE_LOG_SESSION']=str(self.directory)
        LOGGER.info('Session started: %s',json.dumps(self.state,ensure_ascii=False))
        self.monitor=None
        if monitor:
            command=[sys.executable]
            if not getattr(sys,'frozen',False):command.append(str(Path(__file__).with_name('photosieve.py')))
            command+=['--crash-watch',str(os.getpid()),str(self.path)]
            try:self.monitor=subprocess.Popen(command,creationflags=0x08000000 if os.name=='nt' else 0)
            except OSError:LOGGER.exception('Could not start process-exit monitor')
        # Bound logs independently of image caches. Never remove an active session.
        directories=sorted(self.root.glob('session-*'),key=lambda p:p.stat().st_mtime,reverse=True)
        for directory in directories[20:]:
            try:
                marker=json.loads((directory/'state.json').read_text('utf-8'))
                if marker.get('status')!='running':
                    import shutil
                    shutil.rmtree(directory)
            except (OSError,ValueError):pass

    def update(self,force=False,**fields):
        with self.lock:
            self.state.update(fields)
            now=time.monotonic()
            if force or now-self.last_write>=1:
                try:_write_state(self.path,self.state)
                except OSError:LOGGER.warning('Could not persist diagnostic state',exc_info=True)
                self.last_write=now

    def finish(self,code=0):
        self.update(force=True,status='finished' if code==0 else 'abnormal',ended=time.time(),exit_code=code)
        LOGGER.info('Session exited: %s',code)
        for handler in LOGGER.handlers:handler.flush()
        # The watcher also runs the frozen EXE. Release its executable handle
        # before an automatic updater starts replacing installed program files.
        if code==0 and self.monitor:
            try:self.monitor.wait(timeout=3)
            except subprocess.TimeoutExpired:LOGGER.warning('Exit monitor did not stop promptly')

def install(root,monitor=True):
    global SESSION
    SESSION=Session(root,monitor)
    return SESSION

def boot():
    directory=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
    try:return install(directory/'workspaces'/'logs')
    except OSError:return None

def event(name,**fields):
    LOGGER.info('%s %s',name,json.dumps(fields,ensure_ascii=False,default=str))
    if SESSION:SESSION.update(force=True,stage=name,**fields)

def progress(**fields):
    if SESSION:SESSION.update(**fields)

def decoder_init():
    directory=os.environ.get('PHOTOSIEVE_LOG_SESSION')
    if not directory:return
    path=Path(directory)/f'decoder-{os.getpid()}'
    try:
        fault=path.with_suffix('.fault.log').open('a',encoding='utf-8',buffering=1)
        _files.append(fault);faulthandler.enable(fault,all_threads=True)
        handler=RotatingFileHandler(path.with_suffix('.log'),maxBytes=1024**2,backupCount=1,encoding='utf-8')
        LOGGER.handlers=[handler];LOGGER.setLevel(logging.INFO)
        LOGGER.info('Decoder ready pid=%s',os.getpid())
    except OSError:pass

def install_qt_handler():
    global _qt_handler
    if not SESSION:return
    from PySide6.QtCore import qInstallMessageHandler,QtMsgType
    def handle(kind,context,message):
        if kind in (QtMsgType.QtWarningMsg,QtMsgType.QtCriticalMsg,QtMsgType.QtFatalMsg):
            LOGGER.warning('Qt [%s] %s',context.category,message)
            if kind==QtMsgType.QtFatalMsg:SESSION.update(force=True,last_qt_fatal=message)
    _qt_handler=handle;qInstallMessageHandler(handle)

def finish(code=0):
    if SESSION:SESSION.finish(code)
