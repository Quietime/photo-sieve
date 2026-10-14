"""ExifTool metadata-only ARW stars; subprocesses may touch only a disposable copy."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

VALUES=(0,1,25,50,75,99)
TAGS=('EXIF:Rating','EXIF:RatingPercent','XMP-xmp:Rating','XMP-microsoft:RatingPercent')
class EmbeddedRatingError(Exception):pass

def executable():
    configured=os.environ.get('PHOTOSIEVE_EXIFTOOL')
    if configured:
        path=Path(configured)
        if path.is_file():return str(path)
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    for path in (base/'assets/exiftool/exiftool.exe',base/'assets/exiftool/exiftool'):
        if path.is_file():return str(path)
    found=shutil.which('exiftool')
    if found:return found
    raise EmbeddedRatingError('ARW 原图星级组件未安装，请使用完整安装包')

def _run(arguments,timeout):
    options={'capture_output':True,'timeout':timeout,'check':False}
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
    try:result=subprocess.run([executable(),*map(str,arguments)],**options)
    except subprocess.TimeoutExpired as exc:raise EmbeddedRatingError('ARW 元数据处理超时，原照片未由元数据子进程修改') from exc
    except OSError as exc:raise EmbeddedRatingError('无法启动 ARW 星级组件：'+str(exc)) from exc
    if result.returncode:raise EmbeddedRatingError('ARW 星级处理失败：'+result.stderr.decode('utf8','replace').strip()[:500])
    return result.stdout

def read(path):
    try:
        rows=json.loads(_run(['-j','-G1','-n',*('-'+tag for tag in TAGS),'--',path],4))
        row=rows[0]
        # XMP is the standard interoperable star value; EXIF is a compatibility copy.
        for key in ('XMP-xmp:Rating','IFD0:Rating','ExifIFD:Rating'):
            if key in row:
                value=int(row[key])
                if 0<=value<=5:return {'stars':value,'tags':row}
                raise EmbeddedRatingError('ARW 内嵌星级超出 0～5 范围')
        for key in ('IFD0:RatingPercent','XMP-microsoft:RatingPercent'):
            if key in row:
                value=int(row[key])
                if value in VALUES:return {'stars':VALUES.index(value),'tags':row}
                raise EmbeddedRatingError('ARW 内嵌百分制星级无法转换')
        return {'stars':0,'tags':row}
    except (ValueError,TypeError,IndexError,KeyError) as exc:raise EmbeddedRatingError('ARW 星级元数据无法解析') from exc

def _fingerprint(path):
    stat=Path(path).stat();return stat.st_size,stat.st_mtime_ns

def write(path,stars,expected=None):
    if isinstance(stars,bool) or not isinstance(stars,int) or not 0<=stars<=5:raise EmbeddedRatingError('星级必须为 0～5')
    path=Path(path).resolve()
    fingerprint=_fingerprint(path)
    before=read(path)['stars']
    if _fingerprint(path)!=fingerprint:raise EmbeddedRatingError('读取期间原照片已改变，已取消 ARW 星级修改')
    if expected is not None and before!=expected:raise EmbeddedRatingError('原图星级已被改变，不能覆盖外部修改来撤销评级')
    installed=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
    candidates=Path(os.environ.get('PHOTOSIEVE_RATING_CANDIDATES',installed/'workspaces/rating-candidates'))
    candidates.mkdir(parents=True,exist_ok=True)
    # ExifTool is never given the original path for a write. If our parent kills
    # this worker, an orphan ExifTool process can only finish editing this copy.
    with tempfile.TemporaryDirectory(prefix='arw-',dir=candidates) as directory:
        candidate=Path(directory)/path.name
        shutil.copy2(path,candidate)
        _run(['-overwrite_original','-P',f'-EXIF:Rating={stars}',f'-EXIF:RatingPercent={VALUES[stars]}',f'-XMP-xmp:Rating={stars}',f'-XMP-microsoft:RatingPercent={VALUES[stars]}','--',candidate],45)
        verified=read(candidate)
        row=verified['tags']
        expected_tags={'IFD0:Rating':stars,'IFD0:RatingPercent':VALUES[stars],'XMP-xmp:Rating':stars,'XMP-microsoft:RatingPercent':VALUES[stars]}
        if any(int(row.get(key,-1))!=value for key,value in expected_tags.items()):raise EmbeddedRatingError('ARW 写入后的四项星级验证未通过，原照片未修改')
        if _fingerprint(path)!=fingerprint:raise EmbeddedRatingError('写入期间原照片已改变，已取消 ARW 星级修改')
        # Child has exited and the candidate is verified. RatingRecords retains
        # a full original backup until this copy and database bookkeeping commit.
        shutil.copyfile(candidate,path)
    return {'stars':stars,'before':before}
