import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

import keys
import package_release

application = QApplication.instance() or QApplication([])


class ShortcutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / 'keys.ini'), QSettings.Format.IniFormat)

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_until_the_user_chooses_and_choices_survive_a_restart(self):
        chosen = keys.load(self.settings)
        self.assertEqual(chosen['keep'], ['Up', '']); self.assertEqual(chosen['undo'], ['Ctrl+Z', ''])
        chosen['keep'] = ['P', 'Up']; chosen['delete'] = ['X', '']
        keys.save(self.settings, chosen)
        again = keys.load(QSettings(self.settings.fileName(), QSettings.Format.IniFormat))
        self.assertEqual((again['keep'], again['delete'], again['next']), (['P', 'Up'], ['X', ''], ['Right', '']))
        self.settings.setValue('shortcuts/next', 'not json')   # a damaged setting falls back to the default
        self.assertEqual(keys.load(self.settings)['next'], ['Right', ''])

    def test_conflicts_and_labels(self):
        chosen = keys.defaults(); self.assertEqual(keys.conflicts(chosen), set())
        chosen['zoom'] = ['Up', '']; self.assertEqual(keys.conflicts(chosen), {'Up'})
        self.assertEqual((keys.label('Up'), keys.label('Left'), keys.label('')), ('↑', '←', ''))
        self.assertEqual(keys.normal('ctrl+z'), 'Ctrl+Z'); self.assertEqual(keys.normal('A, B'), '')

    def test_dialog_refuses_duplicates_and_resets_to_defaults(self):
        chosen = keys.defaults(); dialog = keys.ShortcutDialog(None, chosen); saved = []
        dialog.saved.connect(saved.append)
        dialog.fields['keep', 1].setKeySequence(QKeySequence('P'))
        dialog.fields['delete', 1].setKeySequence(QKeySequence('P'))
        self.assertFalse(dialog.save_button.isEnabled()); self.assertIn('P', dialog.problem.text())
        dialog.fields['delete', 1].setKeySequence(QKeySequence('X')); self.assertTrue(dialog.save_button.isEnabled())
        dialog.fields['keep', 0].clear()   # the second key moves up to become the main one
        dialog.accept_keys()
        self.assertEqual(saved[0]['keep'], ['P', '']); self.assertEqual(saved[0]['delete'], ['Down', 'X'])
        dialog = keys.ShortcutDialog(None, saved[0]); dialog.fields['keep', 0].clear()
        self.assertTrue(dialog.save_button.isEnabled(), 'Actions can be button-only')
        dialog.reset(); self.assertEqual(dialog.chosen(), keys.defaults())


class PublicPackageTest(unittest.TestCase):
    def test_only_project_source_goes_into_the_public_download(self):
        names = [path.relative_to(package_release.ROOT).as_posix() for path in package_release.sources()]
        self.assertIn('app.py', names); self.assertIn('使用说明.txt', names)
        for private in ('photos/DSC0001.ARW', 'shots/screen.png', 'records/library.sqlite3', 'notes/settings.ini', 'export.zip'):
            self.assertFalse(package_release.public(private), private)


if __name__ == '__main__':
    unittest.main()
