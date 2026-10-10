"""Build a distributable ZIP and authenticated-update metadata for GitHub Releases."""
from pathlib import Path
import hashlib
import json
import sys
from zipfile import ZipFile, ZIP_DEFLATED
from version import APP_VERSION, UPDATE_REPOSITORY, UPDATE_ASSET

ROOT=Path(__file__).resolve().parent
EXCLUDE={'build','dist','release','__pycache__','.venv','.build-venv','.git','third_party_licenses'}
def sources():
    for p in sorted(ROOT.rglob('*')):
        parts=p.relative_to(ROOT).parts
        if any(x in EXCLUDE for x in parts) or not p.is_file():continue
        if p.suffix in {'.log','.pyc','.zip','.sqlite3','.png'} or p.name.startswith('frozen-diagnostics') or p.name in {'key_probe.py','update.json','SHA256SUMS.txt'}:continue
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
        assert not any('webengine' in n.lower() or 'chromium' in n.lower() for n in names)
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    metadata={'version':APP_VERSION,'repository':UPDATE_REPOSITORY,'asset':destination.name,'size':destination.stat().st_size,'sha256':digest}
    destination.with_name('update.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    destination.with_name('SHA256SUMS.txt').write_text(digest+'  '+destination.name+'\n',encoding='utf-8')
    print(f'{destination}: {destination.stat().st_size/1024/1024:.1f} MB, {len(names)} files, SHA256 {digest}')
if __name__=='__main__':package(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'release'/UPDATE_ASSET)
