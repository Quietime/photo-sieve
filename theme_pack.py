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
