"""Small, read-only camera metadata lookup; never render pixels or substitute file dates."""
from pathlib import Path
from PIL import Image
import json

def shooting_info(path):
    try:
        if Path(path).suffix.lower() in {'.arw','.cr2','.cr3','.nef','.nrw','.dng','.raf','.rw2','.orf','.pef','.srw'}:raise OSError('RAW metadata uses ExifTool')
        with Image.open(Path(path)) as image:
            main=image.getexif();exif=main.get_ifd(34665)
            get=lambda key:exif.get(key,main.get(key))
            aperture=get(33437);exposure=get(33434);iso=get(34855);date=get(36867)
            fields=[]
            if aperture:fields.append(f'f/{float(aperture):g}')
            if exposure:
                seconds=float(exposure)
                fields.append(f'1/{round(1/seconds)}s' if 0<seconds<1 else f'{seconds:g}s')
            if iso:fields.append('ISO '+str(iso[0] if isinstance(iso,tuple) else iso))
            if isinstance(date,bytes):date=date.decode('utf-8',errors='replace')
            return ' · '.join(fields)+ ('\n'+str(date).replace(':','-',2) if date else '')
    except (OSError,ValueError,TypeError,ZeroDivisionError):
        try:
            from embedded_rating import _run
            rows=json.loads(_run(['-j','-n','-FNumber','-ExposureTime','-ISO','-DateTimeOriginal','--',path],4))
            row=rows[0];fields=[]
            if row.get('FNumber'):fields.append(f"f/{float(row['FNumber']):g}")
            seconds=float(row.get('ExposureTime',0))
            if seconds:fields.append(f'1/{round(1/seconds)}s' if 0<seconds<1 else f'{seconds:g}s')
            if row.get('ISO'):fields.append('ISO '+str(row['ISO']))
            date=row.get('DateTimeOriginal')
            return ' · '.join(fields)+ ('\n'+str(date).replace(':','-',2) if date else '')
        except Exception:return ''

class MetadataReader:
    """One cancellable metadata process: slow removable storage never blocks closing."""
    def __init__(self):
        import threading
        self.lock=threading.Lock();self.executor=None;self.closed=False
    def read(self,path):
        import multiprocessing,time
        from concurrent.futures import ProcessPoolExecutor,TimeoutError
        with self.lock:
            if self.closed:return ''
            if self.executor is None:self.executor=ProcessPoolExecutor(1,mp_context=multiprocessing.get_context('spawn'))
            executor=self.executor;future=executor.submit(shooting_info,path)
        deadline=time.monotonic()+7
        try:
            while not self.closed and time.monotonic()<deadline:
                try:return future.result(timeout=.1)
                except TimeoutError:pass
        except Exception:pass
        self.reset(executor)
        return ''
    def reset(self,executor):
        with self.lock:
            if self.executor is not executor:return
            self.executor=None
            processes=list((executor._processes or {}).values())
            for process in processes:
                if process.is_alive():process.terminate()
            executor.shutdown(wait=False,cancel_futures=True)
            for process in processes:
                process.join(.05)
                if process.is_alive():process.kill()
    def close(self):
        self.closed=True
        if self.executor:self.reset(self.executor)
