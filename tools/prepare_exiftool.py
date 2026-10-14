"""Fetch the pinned official Windows metadata runtime for packaging; verify before extracting."""
import hashlib
import io
from pathlib import Path
import shutil
import urllib.request
from zipfile import ZipFile

VERSION='13.59'
SHA256='44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec'
URL=f'https://sourceforge.net/projects/exiftool/files/exiftool-{VERSION}_64.zip/download'

def prepare():
    payload=urllib.request.urlopen(URL,timeout=90).read()
    if hashlib.sha256(payload).hexdigest()!=SHA256:raise RuntimeError('ExifTool download checksum mismatch')
    destination=Path(__file__).resolve().parents[1]/'assets/exiftool'
    if destination.exists():shutil.rmtree(destination)
    destination.mkdir(parents=True)
    prefix=f'exiftool-{VERSION}_64/'
    with ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            if member.is_dir():continue
            if not member.filename.startswith(prefix):raise RuntimeError('Unexpected ExifTool archive entry')
            relative=Path(member.filename[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts:raise RuntimeError('Unsafe ExifTool archive entry')
            target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.read(member))
    (destination/'exiftool(-k).exe').rename(destination/'exiftool.exe')
    print(f'Verified ExifTool {VERSION}; runtime and licenses prepared')

if __name__=='__main__':prepare()
