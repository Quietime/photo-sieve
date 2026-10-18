"""Small, read-only camera metadata lookup; never render pixels or substitute file dates."""
from pathlib import Path
from PIL import Image
import json
import os
import ntpath
import subprocess

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
        # Cameras write 1/2 s as a fraction but 0.8 s or 0.4 s as decimals; 1/round(1/0.8) would read "1/1s".
        reciprocal=1/seconds if seconds>0 else 0
        fraction=0<seconds<1 and (reciprocal>=3 or abs(reciprocal-round(reciprocal))<.05)
        exposure_fields.append(f'1/{round(reciprocal)}s' if fraction else f'{round(seconds,1) if seconds<1 else seconds:g}s')
    if exposure_fields:lines.append(' · '.join(exposure_fields))
    sensitivity=[]
    if focal:sensitivity.append(f'{float(focal):g} mm')
    if iso:sensitivity.append('ISO '+str(iso[0] if isinstance(iso,tuple) else iso))
    if sensitivity:lines.append(' · '.join(sensitivity))
    if date:lines.append(_text(date).replace(':','-',2))
    return '\n'.join(lines)

RAW_SUFFIXES={'.arw','.cr2','.cr3','.nef','.nrw','.dng','.raf','.rw2','.orf','.pef','.srw'}
CAMERA_TAGS=('FNumber','ExposureTime','ISO','DateTimeOriginal','FocalLength','Make','Model','LensModel')

def _path_key(path):
    text=str(path).replace('\\','/')
    if text.startswith('//?/UNC/'):text='//'+text[8:]
    elif text.startswith('//?/'):text=text[4:]
    if ntpath.splitdrive(text)[0]:return ntpath.normcase(ntpath.normpath(text)).replace('\\','/')
    return os.path.normcase(os.path.abspath(text))

class MetadataReadError(Exception):
    """A retryable read failure, distinct from a readable photo without EXIF."""


def _run_metadata(paths):
    """Read-only ExifTool runner; keep good JSON rows on partial batch errors.

    Rating writes deliberately retain their stricter runner. A metadata batch
    can contain a moved/missing photo while other rows are perfectly readable.
    """
    from embedded_rating import executable
    # Windows ExifTool's Perl launcher can lose Unicode on the command line
    # before -charset is interpreted. UTF-8 argument-file input avoids that
    # conversion entirely; do not shell-quote argument-file lines.
    filenames=[os.path.abspath(path) for path in paths]
    if any('\n' in path or '\r' in path for path in filenames):
        raise MetadataReadError('拍摄参数文件路径包含换行符，无法读取')
    arguments=['-charset','filename=UTF8','-j','-n',*('-'+tag for tag in CAMERA_TAGS),'-@','-']
    options={'capture_output':True,'timeout':4,'check':False,'input':('\n'.join(filenames)+'\n').encode('utf8')}
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
    try:
        result=subprocess.run([executable(),*arguments],**options)
    except subprocess.TimeoutExpired as error:
        raise MetadataReadError('拍摄参数读取超时，请稍后重试') from error
    except Exception as error:
        raise MetadataReadError('无法启动拍摄参数读取组件：'+str(error)) from error
    try:
        rows=json.loads(result.stdout)
        if not isinstance(rows,list):raise ValueError('JSON rows required')
    except (ValueError,TypeError) as error:
        raise MetadataReadError('拍摄参数读取结果无效：'+result.stderr.decode('utf8','replace').strip()[:300]) from error
    if result.returncode and not rows:
        raise MetadataReadError('拍摄参数读取失败：'+result.stderr.decode('utf8','replace').strip()[:300])
    return rows


def shooting_infos(paths,strict=False):
    """Aligned read-only results; one ExifTool startup for the entire RAW batch.

    SourceFile is matched explicitly because damaged or unsupported files can be
    omitted from ExifTool JSON. Never shift another photo's parameters into an
    empty slot. Ordinary JPEG headers continue to use Pillow without a subprocess.
    In strict mode, '' means readable without EXIF, None means one failed row,
    and command-wide failures raise MetadataReadError so callers can retry.
    """
    paths=list(paths);results=[None if strict else '']*len(paths);fallback=[]
    for index,path in enumerate(paths):
        try:
            if Path(path).suffix.lower() in RAW_SUFFIXES:raise OSError('RAW metadata uses ExifTool')
            with Image.open(Path(path)) as image:
                main=image.getexif();exif=main.get_ifd(34665)
                get=lambda key:exif.get(key,main.get(key))
                results[index]=format_info(get(33437),get(33434),get(34855),get(36867),get(37386),main.get(271),main.get(272),get(42036))
        except (OSError,ValueError,TypeError,ZeroDivisionError):
            fallback.append(index)
    if not fallback:return results
    try:
        rows=_run_metadata([paths[index] for index in fallback])
        if not isinstance(rows,list):raise MetadataReadError('拍摄参数读取结果无效')
        identified={_path_key(row['SourceFile']):row for row in rows if isinstance(row,dict) and row.get('SourceFile')}
        # ExifTool always provides SourceFile. Positional fallback only supports
        # legacy callers/tests that supply a complete response without this field.
        positional=len(rows)==len(fallback) and not identified
        for offset,index in enumerate(fallback):
            row=rows[offset] if positional else identified.get(_path_key(paths[index]))
            if not isinstance(row,dict) or row.get('Error'):continue
            try:results[index]=format_info(*(row.get(key) for key in CAMERA_TAGS))
            except (ValueError,TypeError,ZeroDivisionError,OverflowError):pass
    except Exception as error:
        if strict:
            if isinstance(error,MetadataReadError):raise
            raise MetadataReadError('拍摄参数读取失败：'+str(error)) from error
    return results

def shooting_infos_strict(paths):
    return shooting_infos(paths,strict=True)

def shooting_info(path):
    return shooting_infos([path])[0]

class MetadataReader:
    """One cancellable metadata process: slow removable storage never blocks closing."""
    def __init__(self):
        import threading
        self.lock=threading.Lock();self.executor=None;self.closed=False
    def read(self,path):
        return self._read(shooting_info,path,'')
    def read_many(self,paths):
        paths=list(paths)
        if not paths:return []
        return self._read(shooting_infos_strict,paths,[None]*len(paths),strict=True)
    def _read(self,function,argument,empty,strict=False):
        import multiprocessing,time
        from concurrent.futures import ProcessPoolExecutor,TimeoutError
        executor=None
        try:
            with self.lock:
                if self.closed:return empty
                if self.executor is None:self.executor=ProcessPoolExecutor(1,mp_context=multiprocessing.get_context('spawn'))
                executor=self.executor;future=executor.submit(function,argument)
        except Exception as error:
            if executor is not None:self.reset(executor)
            if strict and not self.closed:raise MetadataReadError('无法启动拍摄参数后台任务：'+str(error)) from error
            return empty
        deadline=time.monotonic()+7
        try:
            while not self.closed and time.monotonic()<deadline:
                try:return future.result(timeout=.1)
                except TimeoutError:pass
        except Exception as error:
            self.reset(executor)
            if strict and not self.closed:
                if isinstance(error,MetadataReadError):raise
                raise MetadataReadError('拍摄参数后台读取失败：'+str(error)) from error
            return empty
        self.reset(executor)
        if strict and not self.closed:raise MetadataReadError('拍摄参数后台读取超时，请稍后重试')
        return empty
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
                if process.is_alive():
                    process.kill();process.join(.05)
    def close(self):
        self.closed=True
        if self.executor:self.reset(self.executor)
