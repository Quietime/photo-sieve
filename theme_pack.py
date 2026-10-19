"""Optional decoration pack (artwork for the welcome page, the opening animation and the phone remote).

The artwork is not part of the repository or the installer. The app looks for it in the user's own
folder first, then next to the program, and simply runs without decorations when neither has it.
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
        members = []
        for info in pack.infolist():
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename)
            parts = name.parts[1:] if name.parts and name.parts[0] == 'theme' else name.parts
            if not parts or name.is_absolute() or '..' in parts or name.suffix.lower() not in ('.png', '.json') or info.file_size > 20 * 1024 ** 2:
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
            if target_root.exists():
                shutil.rmtree(target_root)
            staging.rename(target_root)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True); raise
    return len(members)


def remove():
    import shutil
    if user_folder().exists():
        shutil.rmtree(user_folder())
