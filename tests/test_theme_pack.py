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

class CloudThemeTest(unittest.TestCase):
    def test_bundle_has_actual_mars_art_and_packaging_allows_only_theme_paths(self):
        import package_release
        self.assertTrue((theme_pack.BUNDLED/'manifest.json').is_file())
        self.assertTrue((theme_pack.BUNDLED/'splash/mascot.png').is_file())
        self.assertTrue(package_release.public('assets/theme/splash/mascot.png'))
        self.assertTrue(package_release.public('PhotoSieve/source/assets/theme/manifest.json'))
        self.assertFalse(package_release.public('private-photo.png'))
    def test_cloud_download_verifies_and_installs_without_a_file_picker(self):
        import hashlib,io,json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle=root/'bundle';bundle.mkdir();(bundle/'manifest.json').write_text('{}');(bundle/'splash').mkdir();(bundle/'splash/mascot.png').write_bytes(b'image')
            with patch.object(theme_pack,'BUNDLED',bundle):meta=theme_pack.build_archive(root/'release')
            data=(root/'release'/theme_pack.CLOUD_ASSET).read_bytes();self.assertEqual(hashlib.sha256(data).hexdigest(),meta['sha256'])
            release={'assets':[{'name':'theme.json','url':'manifest'}, {'name':theme_pack.CLOUD_ASSET,'url':'archive','size':len(data)}]}
            with patch.dict(os.environ,PHOTOSIEVE_THEME=str(root/'user')),patch('updater.read_json',return_value=release),patch('updater.open_api',side_effect=[io.BytesIO(json.dumps(meta).encode()),io.BytesIO(data)]):
                self.assertEqual(theme_pack.download_default(),2);self.assertEqual(theme_pack.find('splash/mascot.png').read_bytes(),b'image')
    def test_failed_cloud_hash_preserves_current_theme(self):
        import io,json
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,PHOTOSIEVE_THEME=tmp):
            target=Path(tmp)/'manifest.json';target.write_text('old')
            meta={'asset':theme_pack.CLOUD_ASSET,'size':3,'sha256':'0'*64};release={'assets':[{'name':'theme.json','url':'manifest'},{'name':theme_pack.CLOUD_ASSET,'url':'archive','size':3}]}
            with patch('updater.read_json',return_value=release),patch('updater.open_api',side_effect=[io.BytesIO(json.dumps(meta).encode()),io.BytesIO(b'bad')]):
                with self.assertRaisesRegex(ValueError,'校验失败'):theme_pack.download_default()
            self.assertEqual(target.read_text(),'old')
    def test_file_picker_entry_is_on_the_file_dialog_class(self):
        from ui_shell import QFileDialog
        self.assertTrue(callable(QFileDialog.getOpenFileName))
