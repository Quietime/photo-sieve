"""Download only public verification fixtures, never distribute them in the APP."""
import hashlib
from pathlib import Path
import urllib.request

FIXTURES=[
 ('astronaut.png','https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/astronaut.png','88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5'),
 ('sony.ARW','https://raw.pixls.us/getfile.php/1582/nice/Sony%20-%20ILCE-7S%20-%2014bit%2014bit%20compressed%20%283%3A2%29.ARW','a35ebb2fbec929daa5beb20d1ce5c15a8aac7b1a7a231455387f3df8a7442e07'),
]

if __name__=='__main__':
    directory=Path('feature-fixtures');directory.mkdir(exist_ok=True)
    for name,url,digest in FIXTURES:
        payload=urllib.request.urlopen(url,timeout=60).read()
        if hashlib.sha256(payload).hexdigest()!=digest:raise RuntimeError('Public test photo checksum mismatch: '+name)
        (directory/name).write_bytes(payload)
        print('Verified public fixture '+name)
