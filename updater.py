"""GitHub releases, optional Windows credentials and transactional updates.

No credentials are shipped with the application. GitHub tokens are used only
against api.github.com; asset redirects are followed without Authorization.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, urlopen
from zipfile import ZipFile
from version import APP_VERSION, UPDATE_REPOSITORY, UPDATE_ASSET

MAX_DOWNLOAD = 512 * 1024 * 1024
MAX_UNPACKED = 2 * 1024 * 1024 * 1024
CREDENTIAL = 'PhotoSieve:GitHub:' + UPDATE_REPOSITORY
STAGING = threading.Lock()   # a download and a cleanup of the update folder never run at once
# The old version's files are moved aside inside the APP folder: a move cannot cross drives, and the APP may live on
# D: or an external disk while %LOCALAPPDATA% is on C:.
BACKUP = '.photo-sieve-update-backup'


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('更新版本格式无效')
    return tuple(map(int, value.split('.')))


def credential_store(token=None, delete=False):
    """Read/write a generic credential in the current Windows user vault."""
    if os.name != 'nt':
        if token is not None or delete:raise RuntimeError('授权设置仅在 Windows APP 中可用')
        return ''
    from ctypes import wintypes as w
    class Credential(ctypes.Structure):
        _fields_ = [('Flags', w.DWORD), ('Type', w.DWORD), ('TargetName', w.LPWSTR),
                    ('Comment', w.LPWSTR), ('LastWritten', w.FILETIME),
                    ('CredentialBlobSize', w.DWORD), ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
                    ('Persist', w.DWORD), ('AttributeCount', w.DWORD), ('Attributes', ctypes.c_void_p),
                    ('TargetAlias', w.LPWSTR), ('UserName', w.LPWSTR)]
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    api.CredReadW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredWriteW.argtypes=[ctypes.POINTER(Credential),w.DWORD]
    api.CredDeleteW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD]
    api.CredFree.argtypes=[ctypes.c_void_p]
    if delete:
        if not api.CredDeleteW(CREDENTIAL,1,0) and ctypes.get_last_error()!=1168:raise ctypes.WinError(ctypes.get_last_error())
        return ''
    if token is not None:
        blob=token.strip().encode('utf-8')
        if not blob or len(blob)>2500:raise ValueError('请填写有效的 GitHub 授权令牌')
        data=(ctypes.c_ubyte*len(blob)).from_buffer_copy(blob)
        value=Credential(Type=1,TargetName=CREDENTIAL,CredentialBlobSize=len(blob),CredentialBlob=data,Persist=2,UserName='GitHub')
        if not api.CredWriteW(ctypes.byref(value),0):raise ctypes.WinError(ctypes.get_last_error())
        return token.strip()
    pointer=ctypes.POINTER(Credential)()
    if not api.CredReadW(CREDENTIAL,1,0,ctypes.byref(pointer)):
        if ctypes.get_last_error()==1168:return ''
        raise ctypes.WinError(ctypes.get_last_error())
    try:return ctypes.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode('utf-8')
    finally:api.CredFree(pointer)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):return None


def open_api(url, token, binary=False):
    prefix=f'https://api.github.com/repos/{UPDATE_REPOSITORY}/'
    if not url.startswith(prefix):raise ValueError('更新地址不属于配置的 GitHub 仓库')
    headers={'User-Agent':'PhotoSieve/'+APP_VERSION,'Accept':'application/octet-stream' if binary else 'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
    if token:headers['Authorization']='Bearer '+token
    try:return build_opener(NoRedirect()).open(Request(url,headers=headers),timeout=30)
    except HTTPError as error:
        if error.code in (301,302,303,307,308) and binary:
            location=error.headers.get('Location','');host=urlparse(location).hostname or ''
            if urlparse(location).scheme!='https' or not (host=='github.com' or host.endswith('.githubusercontent.com') or host.endswith('.github.com')):
                raise ValueError('GitHub 下载重定向地址无效') from None
            # Deliberately discard the authorization header when leaving the API.
            return urlopen(Request(location,headers={'User-Agent':'PhotoSieve/'+APP_VERSION}),timeout=45)
        if error.code in (401,403,404):raise RuntimeError('无法获取 GitHub 更新：仓库可能尚未发布正式版本、没有访问权限，或已达到请求次数限制；请稍后重试。') from None
        raise RuntimeError(f'GitHub 请求失败（HTTP {error.code}）') from None


def read_json(url, token):
    with open_api(url,token) as response:
        data=response.read(2*1024*1024+1)
        if len(data)>2*1024*1024:raise ValueError('更新信息过大')
        return json.loads(data)


def check_release(token):
    release=read_json(f'https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest',token)
    if release.get('draft') or release.get('prerelease'):return None
    version=release['tag_name'].removeprefix('v')
    if version_tuple(version)<=version_tuple(APP_VERSION):return None
    assets={asset['name']:asset for asset in release['assets']}
    if UPDATE_ASSET not in assets or 'update.json' not in assets:raise ValueError('新版本缺少更新包，请稍后重试')
    metadata_asset=assets['update.json']
    with open_api(metadata_asset['url'],token,True) as response:
        raw=response.read(65537)
        if len(raw)>65536:raise ValueError('更新清单过大')
        manifest=json.loads(raw)
    asset=assets[UPDATE_ASSET]
    if manifest.get('version')!=version or manifest.get('asset')!=UPDATE_ASSET or manifest.get('repository')!=UPDATE_REPOSITORY:
        raise ValueError('更新清单与发布版本不一致')
    if not re.fullmatch('[0-9a-f]{64}',str(manifest.get('sha256',''))):raise ValueError('缺少更新包校验值')
    if not isinstance(manifest.get('size'),int) or not 0<manifest['size']<=MAX_DOWNLOAD or asset['size']!=manifest['size']:
        raise ValueError('更新包大小无效')
    return {**manifest,'url':asset['url'],'page':release['html_url'],'notes':str(release.get('body') or '')[:4000]}


def safe_relative(name):
    value=PurePosixPath(name)
    if '\\' in name or ':' in name or '\x00' in name or value.is_absolute() or '..' in value.parts or not value.parts:
        raise ValueError('更新包包含不安全的路径')
    # Windows ignores trailing dots/spaces and has reserved device filenames.
    if any(p.rstrip(' .')!=p or p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for p in value.parts):
        raise ValueError('更新包包含无效的 Windows 文件名')
    return value


def unpack(archive, destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    with ZipFile(archive) as bundle:
        entries=bundle.infolist();seen=set();total=0
        if len(entries)>15000:raise ValueError('更新包文件数量异常')
        for item in entries:
            relative=safe_relative(item.filename)
            if relative.parts[0]!='PhotoSieve':raise ValueError('更新包目录结构无效')
            key=str(relative).casefold()
            if key in seen:raise ValueError('更新包包含重复路径')
            seen.add(key);total+=item.file_size
            if total>MAX_UNPACKED or stat.S_ISLNK(item.external_attr>>16):raise ValueError('更新包内容无效')
        bundle.extractall(destination)
    root=destination/'PhotoSieve'
    validate_install(root)
    return root


def validate_install(root):
    root=Path(root)
    manifest=json.loads((root/'installed-files.json').read_text('utf-8'))
    version_tuple(manifest['version']);files=manifest['files']
    if not isinstance(files,dict) or not 0<len(files)<=15000 or 'PhotoSieve.exe' not in files:raise ValueError('安装文件清单无效')
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name!='installed-files.json'}
    if actual!=set(files):raise ValueError('安装包文件清单不完整')
    for name,digest in files.items():
        safe_relative(name);path=root/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('安装文件校验失败：'+name)
    return manifest


def updates_dir():
    return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.photo-sieve')))/'PhotoSieve'/'updates'


def cleanup_updates(*keep):
    """Delete downloaded packages and unpacked new versions, except the stage folders in keep.
    True when nothing is left; a file still in use (the installer exiting) stays for a later try,
    and so does everything while a download is running."""
    if not STAGING.acquire(blocking=False):return False
    try:
        base=updates_dir();keep={Path(path).resolve() for path in keep if path};clean=True
        if not base.is_dir():return True
        for child in base.iterdir():
            if child.resolve() in keep:continue
            if child.is_dir() and not child.is_symlink():shutil.rmtree(child,ignore_errors=True)
            else:
                try:child.unlink()
                except OSError:pass
            clean=clean and not child.exists()
        return clean
    finally:STAGING.release()


def intact(root):
    """Every file the installation's manifest lists is present and unchanged; other files may sit beside them."""
    root=Path(root)
    try:
        files=json.loads((root/'installed-files.json').read_text('utf-8'))['files']
        return all((root/name).is_file() and hashlib.sha256((root/name).read_bytes()).hexdigest()==digest for name,digest in files.items())
    except (OSError,ValueError,KeyError,TypeError,AttributeError):return False


def cleanup_backups(app):
    """Delete the old version's files saved during an update, but only once the running installation is complete:
    after a rollback that could not finish they may be the only copy of some program files."""
    backup=Path(app)/BACKUP
    if not backup.exists():return True
    if linked(backup) or not intact(app):return True   # keep it; nothing to retry
    shutil.rmtree(backup,ignore_errors=True)
    return not backup.exists()


def download_release(release, token, progress=lambda done,total:None):
    with STAGING:
        return _download(release,token,progress)


def _download(release, token, progress):
    base=updates_dir()
    base.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='v'+release['version']+'-',dir=base))
    try:
        archive=stage/'update.zip';digest=hashlib.sha256();size=0
        with open_api(release['url'],token,True) as response,archive.open('xb') as output:
            while chunk:=response.read(1024*1024):
                size+=len(chunk)
                if size>release['size']:raise ValueError('下载包大小超过更新清单')
                output.write(chunk);digest.update(chunk);progress(size,release['size'])
        if size!=release['size'] or digest.hexdigest()!=release['sha256']:raise ValueError('下载校验失败，请重新检查更新')
        root=unpack(archive,stage/'new')
        if validate_install(root)['version']!=release['version']:raise ValueError('安装文件版本不一致')
        archive.unlink()
        return root
    except Exception:
        shutil.rmtree(stage,ignore_errors=True);raise


def managed_files(target):
    path=target/'installed-files.json'
    if not path.is_file():raise ValueError('当前安装缺少文件清单，请手动解压新版一次')
    files=json.loads(path.read_text('utf-8'))['files']
    for name in files:safe_relative(name)
    if 'PhotoSieve.exe' not in files:raise ValueError('当前安装文件清单无效')
    return set(files)|{'installed-files.json'}


def linked(path):
    try:return path.is_symlink() or bool(getattr(path.lstat(),'st_file_attributes',0)&0x400)
    except FileNotFoundError:return False


def install_transaction(source, target, backup):
    """Replace only files owned by the app; preserve user files. Roll back errors."""
    source,target,backup=map(Path,(source,target,backup))
    manifest=validate_install(source);old=managed_files(target);new=set(manifest['files'])|{'installed-files.json'}
    if not target.is_dir() or linked(target) or source.resolve().is_relative_to(target.resolve()):raise ValueError('安装位置无效')
    # Preflight ALL destination paths before changing any file (avoid symlink directories).
    for name in old|new:
        for component in [target/name,*(target/name).parents]:
            if component==target.parent:break
            if linked(component):raise ValueError('安装目录含符号链接：'+name)
        if name in new-old and (target/name).exists():raise ValueError('新版文件与用户文件重名：'+name)
    backup.mkdir(parents=True,exist_ok=False);saved=[];written=[]
    try:
        # Preserve a durable backup until the next launch confirms success.
        for name in sorted(old,key=lambda n:(n=='PhotoSieve.exe',n)):
            path=target/name
            if path.is_file():
                copy=backup/name;copy.parent.mkdir(parents=True,exist_ok=True);os.replace(path,copy);saved.append(name)
        for name in sorted(new,key=lambda n:(n=='PhotoSieve.exe',n)):
            path=target/name;path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_name(path.name+'.photo-sieve-update')
            if temporary.exists():raise ValueError('临时安装文件已存在：'+str(temporary))
            try:
                shutil.copyfile(source/name,temporary);os.replace(temporary,path);written.append(name)
            finally:temporary.unlink(missing_ok=True)
    except Exception:
        for name in reversed(written):(target/name).unlink(missing_ok=True)
        for name in reversed(saved):
            path=target/name;path.parent.mkdir(parents=True,exist_ok=True);os.replace(backup/name,path)
        raise


def wait_for_parent(pid, ready, timeout=180):
    if os.name!='nt':raise RuntimeError('自动安装仅支持 Windows')
    from ctypes import wintypes as w
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];api.OpenProcess.restype=w.HANDLE
    api.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];api.CloseHandle.argtypes=[w.HANDLE]
    handle=api.OpenProcess(0x100000,False,pid)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    try:
        Path(ready).write_text('ready',encoding='utf-8')
        if api.WaitForSingleObject(handle,timeout*1000)!=0:raise RuntimeError('旧程序未退出，已取消替换')
    finally:api.CloseHandle(handle)


def independent_process(arguments, cwd):
    """A new frozen application, never a worker inheriting its parent's PyInstaller runtime."""
    environment=os.environ.copy();environment['PYINSTALLER_RESET_ENVIRONMENT']='1'
    flags=0
    if os.name=='nt':
        flags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS
    return subprocess.Popen(arguments,cwd=cwd,env=environment,creationflags=flags,close_fds=True)


def helper_entry(plan_path):
    try:return run_helper(plan_path)
    except Exception:
        Path(plan_path).with_name('helper-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        return 1


def write_update_result(stage,result):
    temporary=stage/'result.tmp'
    temporary.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    temporary.replace(stage/'result.json')


def run_helper(plan_path):
    plan_path=Path(plan_path).resolve();plan=json.loads(plan_path.read_text('utf-8'))
    source=Path(sys.executable).resolve().parent;target=Path(plan['target']).resolve()
    if source.parent.parent!=plan_path.parent or source==target or target==Path.home() or target==Path(target.anchor):raise ValueError('更新任务目录无效')
    logs=target/'workspaces'/'logs'/'updates';logs.mkdir(parents=True,exist_ok=True)
    logger=logging.getLogger('photosieve.update');logger.setLevel(logging.INFO)
    handler=logging.FileHandler(logs/(plan_path.parent.name+'.log'),encoding='utf-8');logger.addHandler(handler)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.info('Helper started pid=%s source=%s target=%s parent=%s',os.getpid(),source,target,plan['pid'])
    manifest=validate_install(source)
    wait_for_parent(plan['pid'],plan_path.parent/'ready')
    logger.info('Old application exited; replacing installed files')
    result={'version':APP_VERSION,'ok':False}
    try:
        install_transaction(source,target,target/BACKUP/plan_path.parent.name);result['ok']=True
    except Exception as error:result['error']=str(error);logger.exception('Installation failed; transaction rolled back')
    arguments=[str(target/'PhotoSieve.exe'),*plan.get('arguments',[])]
    marker=plan_path.parent/'restart-ready.json'
    # Existing installed versions do not include this field in their update plans.
    verify=bool(plan.get('verify_restart','--self-test-output' not in plan.get('arguments',[])) and result['ok'])
    if verify:arguments.extend(['--update-restart-marker',str(marker)])
    try:
        # Old DLL handles and the single-instance lock may settle just after process exit.
        for attempt in range(3):
            marker.unlink(missing_ok=True)
            child=independent_process(arguments,target);logger.info('Restart attempt %s pid=%s',attempt+1,child.pid)
            if not verify:break
            deadline=time.monotonic()+60
            while not marker.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(.1)
            if marker.exists():
                confirmation=json.loads(marker.read_text('utf-8'))
                if not confirmation.get('visible') or confirmation['version']!=manifest['version'] or confirmation['pid']!=child.pid:
                    raise RuntimeError('更新后的窗口启动确认不匹配')
                if Path(confirmation['executable']).resolve()!=target/'PhotoSieve.exe':raise RuntimeError('重启应用的安装位置不匹配')
                try:child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    result['restart']=confirmation;logger.info('Restarted window confirmed: %s',confirmation);break
                logger.warning('Window exited immediately after startup: %s',child.returncode)
            if child.poll() is None:raise RuntimeError('更新后的应用未在 60 秒内确认启动，请查看应用日志')
            logger.warning('Restart exited before window ready: %s',child.returncode)
            if attempt==2:raise RuntimeError('更新后的应用启动失败，退出码 '+str(child.returncode))
            time.sleep(1)
    except Exception as error:
        result['restart_error']=str(error);logger.exception('Restart failed')
        result['log']=str(logs/(plan_path.parent.name+'.log'))
        write_update_result(plan_path.parent,result)
        if os.name=='nt':ctypes.windll.user32.MessageBoxW(None,'更新启动失败：'+str(error)+'\n可以从安装目录重新打开应用。日志：'+str(logs),'照片快筛更新',0x10)
    result['log']=str(logs/(plan_path.parent.name+'.log'))
    write_update_result(plan_path.parent,result)
    handler.flush();logger.removeHandler(handler);handler.close()
    if result.get('restart_error'):return 1
    return 0 if result['ok'] else 1


def launch_helper(source, target, arguments):
    source,target=Path(source),Path(target);managed_files(target)
    stage=source.parent.parent
    plan=stage/'plan.json'
    plan.write_text(json.dumps({'target':str(target),'pid':os.getpid(),'arguments':arguments,'verify_restart':True}),encoding='utf-8')
    # Launch from the staged NEW installation, so it can replace every old DLL.
    process=independent_process([str(source/'PhotoSieve.exe'),'--apply-update',str(plan)],source)
    start=time.monotonic()
    while not (stage/'ready').is_file():
        if process.poll() is not None:raise RuntimeError('更新安装器启动失败，原程序未修改')
        if time.monotonic()-start>45:
            process.terminate();raise RuntimeError('更新安装器启动超时')
        time.sleep(.1)
    return stage
