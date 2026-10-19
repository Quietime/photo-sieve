"""Decoration pack: optional, user-installed, and never able to write outside its own folder."""
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import theme_pack


class ThemePackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.patch = patch.dict(os.environ, {'PHOTOSIEVE_THEME': str(self.base / 'user' / 'theme')}); self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.temp.cleanup()

    def pack(self, members):
        path = self.base / 'pack.zip'
        with zipfile.ZipFile(path, 'w') as z:
            for name, data in members.items():
                z.writestr(name, data)
        return path

    def test_install_keeps_only_safe_png_and_json(self):
        path = self.pack({'theme/manifest.json': '{}', 'theme/splash/rock-1.png': b'png', '../evil.png': b'x', 'theme/../../evil2.png': b'x',
                          '/abs.png': b'x', 'theme/run.py': 'print(1)', 'theme/splash/notes.txt': 'x'})
        self.assertEqual(theme_pack.install(path), 2)
        self.assertEqual(theme_pack.find('splash/rock-1.png').read_bytes(), b'png')
        self.assertTrue(theme_pack.available())
        self.assertFalse((self.base / 'evil.png').exists()); self.assertFalse((self.base / 'user' / 'evil2.png').exists())
        self.assertIsNone(theme_pack.find('run.py')); self.assertIsNone(theme_pack.find('../pack.zip'))

    def test_a_zip_without_manifest_is_refused_and_the_old_pack_stays(self):
        theme_pack.install(self.pack({'manifest.json': '{}', 'splash/a.png': b'1'}))
        with self.assertRaises(ValueError):
            theme_pack.install(self.pack({'splash/b.png': b'2'}))
        self.assertIsNotNone(theme_pack.find('splash/a.png'))

    def test_app_runs_without_a_pack(self):
        self.assertIsNone(theme_pack.find('splash/rock-1.png') if not theme_pack.BUNDLED.exists() else None)
        theme_pack.remove(); self.assertFalse(theme_pack.user_folder().exists())


if __name__ == '__main__':
    unittest.main()
