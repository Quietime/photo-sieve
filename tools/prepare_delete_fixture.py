"""Fetch only the checksum-pinned CC0 Sony sample, outside distributable assets."""
import hashlib
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.prepare_feature_fixtures import FIXTURES

if __name__=='__main__':
    name,url,digest=next(row for row in FIXTURES if row[0]=='sony.ARW')
    payload=urllib.request.urlopen(url,timeout=90).read()
    if hashlib.sha256(payload).hexdigest()!=digest:raise RuntimeError('Sony test fixture checksum mismatch')
    Path(sys.argv[1]).write_bytes(payload)
    print('Verified CC0 Sony ILCE-7S ARW sample')
