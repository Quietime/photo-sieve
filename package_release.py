"""Build a distributable ZIP and authenticated-update metadata for GitHub Releases."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED
from version import APP_VERSION, UPDATE_REPOSITORY, UPDATE_ASSET

ROOT=Path(__file__).resolve().parent
# A public download: only the project's own source files, never whatever else sits in the build folder
# (photos, RAWs, libraries, screenshots, notes). The list comes from git; anything else is refused.
SOURCE_TYPES={'.py','.md','.txt','.bat','.sh','.spec','.iss','.yml','.yaml','.gitattributes','.gitignore',''}
PRIVATE_TYPES={'.jpg','.jpeg','.png','.gif','.bmp','.tif','.tiff','.webp','.heic','.heif','.avif','.arw','.cr2','.cr3','.nef',
               '.nrw','.dng','.raf','.rw2','.orf','.pef','.srw','.sqlite3','.db','.log','.json','.ini','.zip','.pdf','.docx','.xlsx'}

def tracked():
    try:
        out=subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True,check=True).stdout
    except (OSError,subprocess.CalledProcessError):
        raise SystemExit('打包需要在 git 仓库中进行，以确保只包含项目源文件。')
    return [ROOT/name for name in out.decode('utf-8').split('\0') if name]

def public(name):
    path=PurePosixPath(name)
    if path.parts[-2:] in {('assets','photosieve.ico'),('assets','welcome-background.png')}: return True
    return path.suffix.lower() not in PRIVATE_TYPES and (path.suffix.lower() in SOURCE_TYPES or path.name in {'LICENSE'})

def sources():
    for p in sorted(tracked()):
        relative=p.relative_to(ROOT).as_posix()
        if relative.startswith('third_party_licenses/') or not p.is_file():continue
        if not public(relative):raise SystemExit('拒绝打包非源码文件：'+relative)
        yield p

def package(destination):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    binary=ROOT/'dist'/'PhotoSieve'
    if not (binary/'PhotoSieve.exe').is_file():raise SystemExit('请先执行 Windows EXE 构建。')
    entries={p.relative_to(binary).as_posix():p for p in sorted(binary.rglob('*')) if p.is_file() and p.name!='installed-files.json'}
    for name in ['README.md','使用说明.txt','LICENSE','THIRD-PARTY-NOTICES.txt']:entries[name]=ROOT/name
    for p in sources():entries['source/'+p.relative_to(ROOT).as_posix()]=p
    for p in (ROOT/'third_party_licenses').rglob('*'):
        if p.is_file():entries[p.relative_to(ROOT).as_posix()]=p
    installed={'version':APP_VERSION,'files':{name:hashlib.sha256(p.read_bytes()).hexdigest() for name,p in entries.items()}}
    with ZipFile(destination,'w',ZIP_DEFLATED,compresslevel=6) as archive:
        for name,p in sorted(entries.items()):archive.write(p,'PhotoSieve/'+name)
        archive.writestr('PhotoSieve/installed-files.json',json.dumps(installed,ensure_ascii=False,indent=2))
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        names=archive.namelist();assert 'PhotoSieve/PhotoSieve.exe' in names
        own=[n for n in names if not n.startswith(('PhotoSieve/_internal/','PhotoSieve/third_party_licenses/'))]
        assert all(public(n) or n in {'PhotoSieve/PhotoSieve.exe','PhotoSieve/installed-files.json'} for n in own),'安装包含非源码文件'
        assert not any('webengine' in n.lower() or 'chromium' in n.lower() for n in names)
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    metadata={'version':APP_VERSION,'repository':UPDATE_REPOSITORY,'asset':destination.name,'size':destination.stat().st_size,'sha256':digest}
    destination.with_name('update.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    destination.with_name('SHA256SUMS.txt').write_text(digest+'  '+destination.name+'\n',encoding='utf-8')
    print(f'{destination}: {destination.stat().st_size/1024/1024:.1f} MB, {len(names)} files, SHA256 {digest}')
if __name__=='__main__':package(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'release'/UPDATE_ASSET)
