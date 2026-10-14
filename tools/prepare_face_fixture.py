"""Download a checksum-pinned public NASA portrait outside the source tree.

Eileen Collins portrait, NASA/public domain; scikit-image's bundled astronaut sample.
Used only by optional positive face checks, never included in app assets or releases.
"""
import argparse
import hashlib
from pathlib import Path
import urllib.request

URL = 'https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/astronaut.png'
SHA256 = '88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5'


def prepare(destination):
    destination = destination.resolve()
    source_root = Path(__file__).resolve().parents[1]
    if destination.is_relative_to(source_root):
        raise ValueError('Face sample must stay outside source and application assets')
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == SHA256:
        return destination
    with urllib.request.urlopen(URL, timeout=30) as response:
        data = response.read(2 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Public face fixture checksum mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    print(prepare(parser.parse_args().destination))
