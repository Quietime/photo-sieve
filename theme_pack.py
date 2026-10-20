"""Optional decoration pack (artwork for the welcome page, the opening animation and the phone remote).

The supplied Mars artwork is bundled for offline first use. User-installed or cloud-updated
packs override it; all artwork remains separate from photos, ratings and screening records.
"""
from __future__ import annotations
import os
from pathlib import Path

BUNDLED = Path(__file__).resolve().parent / 'assets' / 'theme'


def user_folder():
    override = os.environ.get('PHOTOSIEVE_THEME')
    return Path(override) if override else Path.home() / '.photo-sieve' / 'theme'


def folders():
    return [user_folder(), BUNDLED]


def find(relative):
    """Path of a pack file such as 'welcome/flower-01.png', or None. `relative` must be a plain relative path."""
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        return None
    for folder in folders():
        path = folder / relative
        if path.is_file():
            return path
    return None


def available():
    return any((folder / 'manifest.json').is_file() for folder in folders())


def install(archive):
    """Unpack a decoration pack (.zip with manifest.json and PNG files) into the user's folder.
    Only plain relative PNG/JSON members are taken; the previous pack is replaced. Returns the file count."""
    import shutil, tempfile, zipfile
    from pathlib import PurePosixPath
    with zipfile.ZipFile(archive) as pack:
        if len(pack.infolist()) > 512 or sum(i.file_size for i in pack.infolist()) > 128 * 1024 ** 2:
            raise ValueError('装饰包过大')
        members = []
        for info in pack.infolist():
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename)
            parts = name.parts[1:] if name.parts and name.parts[0] == 'theme' else name.parts
            if '\\' in info.filename or ':' in info.filename or not parts or name.is_absolute() or '..' in parts or name.suffix.lower() not in ('.png', '.json') or info.file_size > 20 * 1024 ** 2:
                continue
            members.append((info, PurePosixPath(*parts)))
        if not any(str(target) == 'manifest.json' for _, target in members):
            raise ValueError('这不是照片快筛的装饰包（缺少 manifest.json）')
        target_root = user_folder(); target_root.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='theme-', dir=target_root.parent))
        try:
            for info, target in members:
                path = staging / target; path.parent.mkdir(parents=True, exist_ok=True)
                with pack.open(info) as source, open(path, 'wb') as out:
                    shutil.copyfileobj(source, out)
            backup = staging.with_name(staging.name + '-previous')
            if target_root.exists():target_root.rename(backup)
            try:staging.rename(target_root)
            except Exception:
                if backup.exists():backup.rename(target_root)
                raise
            if backup.exists():shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True); raise
    return len(members)


def remove():
    import shutil
    if user_folder().exists():
        shutil.rmtree(user_folder())


CLOUD_ASSET = 'PhotoSieve-Theme-Mars.zip'
MAX_ARCHIVE = 32 * 1024 ** 2


def build_archive(destination):
    """Publish the same embedded artwork separately for one-click cloud imports."""
    import hashlib, json, zipfile
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=True)
    archive = destination / CLOUD_ASSET
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as pack:
        for path in sorted(BUNDLED.rglob('*')):
            if path.is_file() and path.suffix.lower() in ('.png', '.json'):
                info = zipfile.ZipInfo('theme/' + path.relative_to(BUNDLED).as_posix())
                info.compress_type = zipfile.ZIP_DEFLATED
                pack.writestr(info, path.read_bytes())
    meta = {'asset': CLOUD_ASSET, 'size': archive.stat().st_size,
            'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    (destination / 'theme.json').write_text(json.dumps(meta), encoding='utf8')
    return meta


def download_default():
    """Worker-only: discover the published pack, verify its hash, then install it."""
    import hashlib, json, tempfile
    from updater import read_json, open_api
    from version import UPDATE_REPOSITORY
    release = read_json(f'https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest', '')
    assets = {a['name']: a for a in release.get('assets', [])}
    if 'theme.json' not in assets or CLOUD_ASSET not in assets:
        raise ValueError('当前云端版本还没有独立装饰包，已继续使用应用内置装饰')
    with open_api(assets['theme.json']['url'], '', True) as response:
        meta = json.loads(response.read(8193))
    size = meta.get('size'); digest = meta.get('sha256', '')
    if meta.get('asset') != CLOUD_ASSET or type(size) is not int or not 0 < size <= MAX_ARCHIVE or len(digest) != 64:
        raise ValueError('云端装饰包清单无效')
    if assets[CLOUD_ASSET]['size'] != size:raise ValueError('云端装饰包大小不一致')
    with tempfile.TemporaryDirectory(prefix='photosieve-theme-') as folder:
        path = Path(folder) / 'theme.zip'; hashed = hashlib.sha256(); downloaded = 0
        with open_api(assets[CLOUD_ASSET]['url'], '', True) as response, path.open('wb') as output:
            while block := response.read(128 * 1024):
                downloaded += len(block)
                if downloaded > size:raise ValueError('云端装饰包超过声明大小')
                output.write(block); hashed.update(block)
        if downloaded != size or hashed.hexdigest() != digest:raise ValueError('云端装饰包校验失败，已保留当前装饰')
        return install(path)
