"""Small, read-only camera metadata lookup; never render pixels or substitute file dates."""
from pathlib import Path
from PIL import Image
import json

def _text(value):
    if isinstance(value,bytes):value=value.decode('utf8',errors='replace')
    text=str(value).strip('\x00 ') if value is not None else ''
    return '' if not text or set(text)<=set('-? ') or text.casefold() in ('unknown','n/a') else text

def format_info(aperture=None,exposure=None,iso=None,date=None,focal=None,make=None,model=None,lens=None):
    lines=[];make=_text(make);model=_text(model);lens=_text(lens)
    camera=(make+' '+model).strip() if make and make.casefold() not in model.casefold() else model or make
    if camera:lines.append(camera)
    if lens:lines.append(lens)
    exposure_fields=[]
    if aperture:exposure_fields.append(f'f/{float(aperture):g}')
    if exposure:
        seconds=float(exposure)
        exposure_fields.append(f'1/{round(1/seconds)}s' if 0<seconds<1 else f'{seconds:g}s')
    if exposure_fields:lines.append(' · '.join(exposure_fields))
    sensitivity=[]
    if focal:sensitivity.append(f'{float(focal):g} mm')
    if iso:sensitivity.append('ISO '+str(iso[0] if isinstance(iso,tuple) else iso))
    if sensitivity:lines.append(' · '.join(sensitivity))
    if date:lines.append(_text(date).replace(':','-',2))
    return '\n'.join(lines)

def shooting_info(path):
    try:
        if Path(path).suffix.lower() in {'.arw','.cr2','.cr3','.nef','.nrw','.dng','.raf','.rw2','.orf','.pef','.srw'}:raise OSError('RAW metadata uses ExifTool')
        with Image.open(Path(path)) as image:
            main=image.getexif();exif=main.get_ifd(34665)
            get=lambda key:exif.get(key,main.get(key))
            return format_info(get(33437),get(33434),get(34855),get(36867),get(37386),main.get(271),main.get(272),get(42036))
    except (OSError,ValueError,TypeError,ZeroDivisionError):
        try:
            from embedded_rating import _run
            rows=json.loads(_run(['-j','-n','-FNumber','-ExposureTime','-ISO','-DateTimeOriginal','-FocalLength','-Make','-Model','-LensModel','--',path],4))
            row=rows[0]
            return format_info(*(row.get(key) for key in ('FNumber','ExposureTime','ISO','DateTimeOriginal','FocalLength','Make','Model','LensModel')))
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
