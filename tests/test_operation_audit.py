"""User-facing operation regressions, using only disposable photographs and records."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from contextlib import contextmanager
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSettings, QTimer, Qt, QPoint, QEvent
from PySide6.QtGui import QAction, QKeySequence, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from app import Library
from desktop import Window
import desktop
import keys
from widgets import PhotoView

application = QApplication.instance() or QApplication([])


def until(condition, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError('Operation did not complete before timeout')


class OperationAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.photos = self.base / '照片'
        self.photos.mkdir()
        for i in range(4):
            Image.new('RGB', (120, 90), (i * 40, 25, 150)).save(self.photos / f'{i:03}.jpg')
        self.library = Library(self.base / 'workspaces')
        self.library.scan(self.photos)
        until(lambda: not self.library.job['running'])
        self.settings = QSettings(str(self.base / 'preferences.ini'), QSettings.Format.IniFormat)
        self.settings.setValue('preload_count', 16)
        self.settings.setValue('warm_count', 0)
        self.settings_patch = patch('desktop.QSettings', return_value=self.settings)
        self.settings_patch.start()
        self.window = Window(self.library, check_updates=False, data_root=self.library.directory)
        self.window.apply_shortcuts(keys.defaults())
        self.window.resize(1100, 720)
        self.window.show()
        until(lambda: self.window.keep_button.isEnabled())

    def tearDown(self):
        self.window.prompt_on_close = False
        self.window.uninstall_requested = False
        self.window.close()
        application.processEvents()
        self.window.deleteLater()
        application.processEvents()
        self.settings_patch.stop()
        self.temp.cleanup()

    def settle(self):
        until(lambda: not self.window.pending_ops and not self.window.inflight)

    def deleted_pages(self):
        for i in range(4, 70):
            Image.new('RGB', (30, 20), 'blue').save(self.photos / f'{i:03}.jpg')
        self.library.scan(self.photos)
        until(lambda: not self.library.job['running'])
        for photo in self.library.list_photos(limit=100):
            self.library.trash(photo['id'])
        self.window.poll()
        until(lambda: self.window.counts.get('deleted') == 70)
        self.window.switch('deleted')
        until(lambda: not self.window.fetching and self.window.deleted_grid.count() == 60)

    def test_held_control_z_restores_multiple_and_stops_after_release(self):
        rows=self.library.list_photos(limit=10)
        for photo in rows:self.library.trash(photo['id'])
        self.window.poll();until(lambda:self.window.counts.get('deleted')==4)
        self.window.activateWindow();self.window.setFocus()
        QTest.keyPress(self.window,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        until(lambda:self.library.state()['counts']['deleted']==3 and not self.window.restore_running)
        for remaining in (2,1):
            application.sendEvent(self.window,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier,'z',True))
            until(lambda:self.library.state()['counts']['deleted']==remaining and not self.window.restore_running)
        QTest.keyRelease(self.window,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.settle();QTest.qWait(180)
        self.assertEqual(self.library.state()['counts']['deleted'],1)
        self.assertFalse(Path(rows[0]['path']).exists())
        self.assertTrue(all(Path(photo['path']).is_file() for photo in rows[1:]))

    def test_repeated_undo_does_not_build_a_backlog(self):
        import threading
        left=self.window.queue[0]['id'];self.library.trash(left)
        self.window.poll();until(lambda:self.window.counts.get('deleted')==1)
        gate=threading.Event();entered=threading.Event();original=self.library.undo
        def slow(*args,**kwargs):
            entered.set();gate.wait(3);return original(*args,**kwargs)
        with patch.object(self.library,'undo',side_effect=slow) as undo:
            self.window.restore(general=True);until(entered.is_set)
            for _ in range(20):self.window.restore(general=True)
            self.assertEqual(self.window.pending_ops,1)
            gate.set();self.settle();self.assertEqual(undo.call_count,1)
        self.assertFalse(self.window.restore_running)

    def test_right_keep_after_clicking_categories_never_changes_category(self):
        chosen=keys.defaults();chosen['keep']=['Right',''];chosen['next']=['','']
        self.window.apply_shortcuts(chosen)
        # Reproduce a recent category click before using the custom key.
        for index in (1,0):
            QTest.mouseClick(self.window.segments,Qt.MouseButton.LeftButton,
                pos=QPoint(2+round(self.window.segments.segment_width()*(index+.5)),16))
            until(lambda:self.window.view==('keep' if index else 'pending') and not self.window.fetching)
        until(lambda:self.window.keep_button.isEnabled())
        left=self.window.queue[0]['id']
        QTest.keyClick(self.window,Qt.Key.Key_Right)
        until(lambda:self.library.photo(left)['status']==1)
        self.settle()
        self.assertEqual(self.window.view,'pending')
        remaining=self.window.queue[0]['id']
        QTest.keyClick(self.window,Qt.Key.Key_Down)
        until(lambda:self.library.state()['counts']['deleted']==1)
        self.assertEqual(self.library.photo(remaining,include_deleted=True)['present'],0)
        self.settle()
        self.assertEqual(self.window.view,'pending')

    def test_hidden_dock_keeps_shortcuts_and_persists_preference(self):
        self.window.show_menu()
        panel=self.window.more_panel
        texts=[control.text() for control in panel.action_controls.values()]
        self.assertEqual(len(texts),len(set(texts)))
        action=next(a for a in panel.action_controls if a.text()=='显示底部操作栏')
        self.assertFalse(action.isChecked());self.assertTrue(self.window.action_row.isHidden())   # opt-in since 5.0
        action.setChecked(True);self.assertFalse(self.window.action_row.isHidden())
        action.setChecked(False)
        self.assertTrue(self.window.action_row.isHidden())
        self.assertFalse(self.settings.value('show_actionbar',True,type=bool))
        count=len(panel.action_controls)
        panel.addAction(action)
        self.assertEqual(len(panel.action_controls),count)
        panel.hide()
        self.assertTrue(all(shortcut.isEnabled() for shortcut in self.window.shortcuts))
        left=self.window.queue[0]['id']
        self.window.activateWindow();self.window.setFocus()
        QTest.keyClick(self.window,Qt.Key.Key_Up)
        until(lambda:self.library.photo(left)['status']==1)
        self.settle()
        self.window.show_menu()
        action=next(a for a in self.window.more_panel.action_controls if a.text()=='显示底部操作栏')
        self.assertFalse(action.isChecked())
        action.setChecked(True)
        self.assertFalse(self.window.action_row.isHidden())
        self.assertTrue(self.settings.value('show_actionbar',False,type=bool))
        self.window.more_panel.hide()

    def test_delete_targets_left_and_latest_delete_restore_ignores_later_keep(self):
        left, right = self.window.queue[:2]
        original = Path(left['path']).read_bytes()
        self.window.delete_button.click()
        self.assertEqual(self.window.queue[0]['id'], right['id'])
        self.settle()
        until(lambda: self.window.keep_button.isEnabled())
        self.window.keep_button.click()
        self.settle()
        self.window.restore()
        self.settle()
        self.assertEqual(Path(left['path']).read_bytes(), original)
        self.assertEqual(self.library.photo(right['id'])['status'], 1)
        self.assertEqual(self.library.state()['counts']['deleted'], 0)
        self.assertEqual(self.window.queue[0]['id'], left['id'])
        self.window.restore(general=True)
        self.settle()
        self.assertEqual(self.library.photo(right['id'])['status'], 0)

    def test_all_shortcuts_can_be_unbound_and_buttons_still_work(self):
        chosen = {action: ['', ''] for action, _, _, _ in keys.ACTIONS}
        keys.save(self.settings, chosen)
        self.window.apply_shortcuts(keys.load(self.settings))
        self.assertEqual(self.window.shortcuts, [])
        left = self.window.queue[0]['id']
        self.window.activateWindow()
        self.window.setFocus()
        for key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            QTest.keyClick(self.window, key)
        self.assertEqual(self.window.queue[0]['id'], left)
        self.assertEqual(self.library.state()['counts']['keep'], 0)
        self.window.keep_button.click()
        self.settle()
        self.assertEqual(self.library.photo(left)['status'], 1)
        self.assertEqual(self.window.keep_button.shortcut, '')
        self.assertTrue(all(self.window.strip.item(i).data(Qt.ItemDataRole.UserRole) != left
                            for i in range(self.window.strip.count())))

    def test_undo_menu_removed_and_keyboard_undo_preserved(self):
        kept=self.window.queue[0]['id']
        self.window.keep_button.click();self.settle();self.window.show_menu()
        panel=self.window.more_panel
        self.assertFalse([action for action in panel.findChildren(QAction) if '撤回上一步' in action.text()])
        panel.close();self.window.activateWindow();self.window.setFocus();application.processEvents()
        QTest.keyClick(self.window,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.settle()
        self.assertEqual(self.library.photo(kept)['status'],0)

    def test_restore_all_deleted_includes_photos_beyond_loaded_page(self):
        self.deleted_pages()
        self.window.select_deleted()
        self.window.restore_selection()
        self.settle()
        until(lambda: not self.library.job['running'])
        self.assertEqual(self.library.state()['counts']['deleted'], 0,
                         'Select all means all 70 deleted photos, including the unloaded second page')
        self.assertEqual(len(list(self.photos.glob('*.jpg'))), 70)

    def test_deselect_after_select_all_is_preserved_when_next_page_loads(self):
        self.deleted_pages()
        self.window.select_deleted()
        deselected = self.window.deleted_grid.item(0)
        deselected.setSelected(False)
        self.window.load_more_deleted()
        until(lambda: not self.window.fetching and self.window.deleted_grid.count() == 70)
        self.assertFalse(deselected.isSelected(),
                         'Loading more deleted photos must not reselect a photo explicitly deselected')
        self.assertFalse(getattr(self.window, 'all_deleted_selected', False))
        self.assertEqual(len(self.window.deleted_grid.selectedItems()), 59)

    def test_restore_selected_restores_only_selected_photos(self):
        selected = self.window.queue[:2]
        others = self.window.queue[2:]
        for photo in selected + others:
            self.library.trash(photo['id'])
        self.window.poll()
        until(lambda: self.window.counts.get('deleted') == 4)
        self.window.switch('deleted')
        until(lambda: not self.window.fetching and self.window.deleted_grid.count() == 4)
        for photo in selected:
            self.window.deleted_items[photo['id']].setSelected(True)
        self.window.restore_selection()
        self.settle()
        self.assertEqual(self.library.state()['counts']['deleted'], 2)
        for photo in selected:
            self.assertTrue(Path(photo['path']).is_file())
        for photo in others:
            self.assertFalse(Path(photo['path']).exists())

    def test_bulk_restore_reports_conflict_and_preserves_conflicting_files(self):
        rows = self.library.list_photos(limit=10)
        for photo in rows:
            self.library.trash(photo['id'])
        conflict = rows[0]
        backup = Path(self.library.photo(conflict['id'], include_deleted=True)['backup'])
        Path(conflict['path']).write_bytes(b'new external file; never overwrite')
        self.window.poll()
        until(lambda: self.window.counts.get('deleted') == 4)
        self.window.switch('deleted')
        until(lambda: not self.window.fetching and self.window.deleted_grid.count() == 4)
        self.window.select_deleted()
        self.window.restore_selection()
        self.settle()
        until(lambda: not self.window.busy and not self.library.job['running'])
        self.assertEqual(self.library.state()['counts']['deleted'], 1)
        self.assertTrue(backup.is_file())
        self.assertEqual(Path(conflict['path']).read_bytes(), b'new external file; never overwrite')
        self.assertEqual(len(self.library.job['errors']), 1)
        self.assertIn('已有文件', str(self.library.job['errors']))
        for photo in rows[1:]:
            self.assertTrue(Path(photo['path']).is_file())

    def test_manual_cache_clear_completes_and_preserves_selected_records_and_backups(self):
        kept, deleted = self.window.queue[:2]
        self.window.keep_button.click()
        self.settle()
        until(lambda: self.window.delete_button.isEnabled())
        self.window.delete_button.click()
        self.settle()
        backup = Path(self.library.photo(deleted['id'], include_deleted=True)['backup'])
        self.window.clear_cache()
        until(lambda: not self.window.cache_clearing and not self.window.busy)
        self.assertTrue(backup.is_file())
        self.assertTrue(Path(kept['path']).is_file())
        self.assertEqual(self.library.photo(kept['id'])['status'], 1)
        self.assertEqual(self.library.state()['counts']['deleted'], 1)
        until(lambda: self.window.keep_button.isEnabled())

    def test_kept_gallery_displays_all_rows_and_reloads_after_return_to_culling_undo(self):
        for photo in self.library.list_photos(limit=10):self.library.mark(photo['id'],1)
        self.window.poll();until(lambda:self.window.counts.get('keep')==4)
        self.window.switch('keep')
        until(lambda:self.window.kept_grid.count()==4 and all(pid in self.window.kept_thumb_cache for pid in self.window.kept_grid.visible_ids()))
        self.assertEqual(self.window.stack.currentIndex(),4)
        self.assertEqual(self.window.strip.count(),0)
        self.assertFalse(hasattr(self.window,'export_button'))
        self.assertTrue(all(self.window.kept_grid.captions.get(pid) for pid in self.window.kept_grid.visible_ids()))
        self.window.open_kept_item(self.window.kept_grid.item(2))
        until(lambda:self.window.album.photo and self.window.album.photo['id']==3)
        self.assertIs(self.window.stack.currentWidget(),self.window.album)
        self.assertFalse(self.window.action_row.isVisible())
        self.assertTrue(Path(self.window.album.photo['path']).is_file())
        self.window.back_to_kept()
        until(lambda:self.window.kept_grid.count()==4)
        self.window.switch('pending');self.window.restore(general=True);self.settle()
        self.window.switch('keep');until(lambda:self.window.kept_grid.count()==3)
        self.assertEqual(self.window.stack.currentIndex(),4)
        self.assertTrue(all(self.window.kept_grid.captions.get(pid) for pid in self.window.kept_grid.visible_ids()))

    def test_keep_category_is_a_read_only_album_and_preserves_files(self):
        first=self.window.queue[0]['id'];self.window.keep_button.click();self.settle();self.window.poll()
        until(lambda:self.window.counts.get('keep')==1);self.window.switch('keep')
        until(lambda:not self.window.fetching and self.window.kept_grid.count()==1)
        self.window.open_kept_item(self.window.kept_grid.item(0))
        until(lambda:self.window.album.photo and self.window.album.photo['id']==first)
        self.assertIs(self.window.stack.currentWidget(),self.window.album)
        self.assertFalse(self.window.action_row.isVisible())
        self.assertTrue(all(not shortcut.isEnabled() for shortcut in self.window.shortcuts))
        self.assertEqual(self.library.photo(first)['status'],1)
        self.assertTrue(Path(self.library.photo(first)['path']).is_file())
        self.window.album.back();until(lambda:self.window.stack.currentIndex()==4)
        self.assertEqual(self.window.kept_grid.count(),1)
        self.assertFalse(hasattr(self.window,'export_button'))

    @contextmanager
    def installed_uninstaller(self):
        installation = self.base / '安装目录有空格'
        installation.mkdir()
        executable = installation / 'PhotoSieve.exe'
        executable.write_bytes(b'disposable test fixture, never executed')
        uninstaller = installation / 'unins000.exe'
        uninstaller.write_bytes(b'disposable test fixture, never executed')
        with patch.object(desktop.sys, 'frozen', True, create=True), \
             patch.object(desktop.sys, 'executable', str(executable)), \
             patch('desktop.subprocess.Popen') as launch:
            self.assertEqual(self.window.uninstaller_path(), uninstaller)
            yield uninstaller, launch

    def choose_modal(self, text):
        box = application.activeModalWidget()
        self.assertIsNotNone(box)
        next(button for button in box.findChildren(QPushButton) if button.text() == text).click()

    def test_uninstall_cancel_does_not_close_or_launch(self):
        with self.installed_uninstaller() as (_, launch):
            QTimer.singleShot(60, lambda: self.choose_modal('取消'))
            self.window.uninstall_app()
            launch.assert_not_called()
            self.assertTrue(self.window.isVisible())
            self.assertFalse(self.window.closed)
            self.assertFalse(self.window.uninstall_requested)

    def test_uninstall_confirm_then_close_cancel_does_not_launch(self):
        self.window.prompt_on_close = True
        def accept_uninstall():
            self.choose_modal('打开卸载向导')
            QTimer.singleShot(60, lambda: self.choose_modal('取消'))
        with self.installed_uninstaller() as (_, launch):
            QTimer.singleShot(60, accept_uninstall)
            self.window.uninstall_app()
            launch.assert_not_called()
            self.assertTrue(self.window.isVisible())
            self.assertFalse(self.window.closed)
            self.assertFalse(self.window.uninstall_requested)

    def test_uninstall_confirm_then_save_close_launches_install_local_path(self):
        self.window.prompt_on_close = True
        first = self.window.queue[0]['id']
        self.window.keep_button.click()
        self.settle()
        def accept_uninstall():
            self.choose_modal('打开卸载向导')
            QTimer.singleShot(60, lambda: self.choose_modal('保留进度并关闭'))
        with self.installed_uninstaller() as (uninstaller, launch):
            QTimer.singleShot(60, accept_uninstall)
            self.window.uninstall_app()
            launch.assert_called_once_with([str(uninstaller)], cwd=uninstaller.parent)
            self.assertTrue(self.window.closed)
            self.assertEqual(self.library.photo(first)['status'], 1)
            self.assertTrue(Path(self.library.photo(first)['path']).is_file())
            self.assertTrue(self.library.database.is_file())

    def test_uninstall_missing_installer_leaves_application_open(self):
        with patch('desktop.subprocess.Popen') as launch:
            self.window.uninstall_app()
            launch.assert_not_called()
            self.assertFalse(self.window.closed)
            self.assertFalse(self.window.uninstall_requested)
            self.assertIn('便携版', self.window.toast.message)

    def test_uninstall_launch_failure_leaves_application_available(self):
        with self.installed_uninstaller() as (_, launch):
            launch.side_effect = OSError('测试：卸载程序不可执行')
            QTimer.singleShot(60, lambda: self.choose_modal('打开卸载向导'))
            self.window.uninstall_app()
            self.assertEqual(launch.call_count, 1)
            self.assertFalse(self.window.closed)
            self.assertFalse(self.window.uninstall_requested)
            self.assertTrue(self.window.isVisible())
            self.assertTrue(self.window.keep_button.isEnabled())
            self.assertIn('无法打开卸载向导', self.window.toast.message)

    def test_deleted_thumbnails_reload_after_scroll_eviction_and_show_photo_pixels(self):
        for i in range(4, 240):
            Image.new('RGB', (120, 90), (10, 20, 210)).save(self.photos / f'{i:03}.jpg')
        self.library.scan(self.photos)
        until(lambda: not self.library.job['running'])
        for photo in self.library.list_photos(limit=300):
            self.library.trash(photo['id'])
        self.window.poll()
        until(lambda: self.window.counts.get('deleted') == 240)
        self.window.switch('deleted')
        until(lambda: not self.window.fetching and self.window.deleted_grid.count() == 60)
        grid = self.window.deleted_grid
        first = grid.item(0)
        first_pid = first.data(Qt.ItemDataRole.UserRole)
        until(lambda: first_pid in self.window.deleted_thumb_cache)
        initial_key = first.icon().cacheKey()
        colour = first.icon().pixmap(176, 118).toImage().pixelColor(88, 59)
        self.assertGreater(colour.blue(), 130)
        self.assertLess(colour.red(), 10)
        self.assertNotEqual(initial_key, self.window.tile.cacheKey())
        while self.window.deleted_more:
            self.window.load_more_deleted()
            until(lambda: not self.window.fetching)
        self.assertEqual(grid.count(), 240)
        # Visit every row to decode more than 128 different previews through the real lazy-loading pipeline.
        for index in range(0, 240, 15):
            item = grid.item(index)
            grid.scrollToItem(item, grid.ScrollHint.PositionAtTop)
            self.window.queue_deleted_thumbnails()
            pid = item.data(Qt.ItemDataRole.UserRole)
            until(lambda: pid in self.window.deleted_thumb_cache and not self.window.thumb_loading and not self.window.thumb_wanted)
            self.assertLessEqual(len(self.window.deleted_thumb_cache), 128)
            pixels = item.icon().pixmap(176, 118).toImage().pixelColor(88, 59)
            self.assertGreater(pixels.blue(), 130, 'A loaded tile must show photograph pixels, not the grey placeholder')
        self.assertNotIn(first_pid, self.window.deleted_thumb_cache)
        self.assertEqual(first.icon().cacheKey(), self.window.tile.cacheKey())
        grid.scrollToItem(first, grid.ScrollHint.PositionAtTop)
        self.window.queue_deleted_thumbnails()
        until(lambda: first_pid in self.window.deleted_thumb_cache)
        self.assertNotEqual(first.icon().cacheKey(), self.window.tile.cacheKey())
        restored = first.icon().pixmap(176, 118).toImage().pixelColor(88, 59)
        self.assertGreater(restored.blue(), 130)
        self.assertLess(restored.red(), 10)
        self.assertLessEqual(len(self.window.deleted_thumb_cache), 128)
        live_icons = [grid.item(i) for i in range(grid.count()) if grid.item(i).icon().cacheKey() != self.window.tile.cacheKey()]
        self.assertLessEqual(len(live_icons), 128, 'Evicted pixmaps must also be released from QListWidgetItem icons')

    def test_large_deleted_grid_keeps_every_visible_photo_populated(self):
        for i in range(4, 240):
            Image.new('RGB', (120, 90), (10, 20, 210)).save(self.photos / f'{i:03}.jpg')
        self.library.scan(self.photos)
        until(lambda: not self.library.job['running'])
        for photo in self.library.list_photos(limit=300):
            self.library.trash(photo['id'])
        self.window.poll()
        until(lambda: self.window.counts.get('deleted') == 240)
        self.window.switch('deleted')
        until(lambda: not self.window.fetching and self.window.deleted_grid.count() == 60)
        while self.window.deleted_more:
            self.window.load_more_deleted()
            until(lambda: not self.window.fetching)
        self.window.resize(3840, 2160)
        application.processEvents()
        self.window.queue_deleted_thumbnails()
        until(lambda: not self.window.thumb_loading and not self.window.thumb_wanted,seconds=60)
        grid = self.window.deleted_grid
        visible = [grid.item(i) for i in range(grid.count())
                   if grid.visualItemRect(grid.item(i)).intersects(grid.viewport().rect())]
        self.assertGreater(len(visible), 128, 'This fixture exercises a screen with more than 128 visible photos')
        for item in visible:
            self.assertNotEqual(item.icon().cacheKey(), self.window.tile.cacheKey(),
                                'A fixed 128 thumbnail limit must not evict a photo that is still visible on a large screen')
            colour = item.icon().pixmap(176, 118).toImage().pixelColor(88, 59)
            self.assertGreater(colour.blue(), 130)

    def test_blank_start_preserves_history_and_does_not_decode(self):
        first = self.window.queue[0]['id']
        self.window.delete_button.click()
        self.settle()
        self.window.close()
        self.window = Window(self.library, check_updates=False, start_empty=True,
                             data_root=self.library.directory)
        self.window.show()
        until(lambda: self.window.state_data is not None)
        self.assertEqual(self.window.stack.currentIndex(), 2)
        self.assertFalse(self.window.queue)
        self.assertFalse(self.window.loading)
        self.assertTrue(all(pane.pixmap is None for pane in self.window.photos))
        self.assertEqual(self.library.state()['counts']['deleted'], 1)
        self.assertTrue(self.library.undo(only_delete=True)['restored'])
        self.assertTrue(Path(self.library.photo(first)['path']).exists())

    def test_close_cancel_then_forget_position_keeps_screening_records(self):
        first = self.window.queue[0]['id']
        self.window.keep_button.click()
        self.settle()
        until(lambda: self.window.queue and self.window.keep_button.isEnabled())
        self.window.prompt_on_close = True
        def close_choice(text):
            box = application.activeModalWidget()
            self.assertIsNotNone(box)
            next(button for button in box.findChildren(QPushButton) if button.text() == text).click()
        QTimer.singleShot(60, lambda: close_choice('取消'))
        self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.closed)
        QTimer.singleShot(60, lambda: close_choice('直接关闭，不保留位置'))
        self.window.close()
        self.assertTrue(self.window.closed)
        self.assertEqual(self.library.meta('cursor'), '0')
        self.assertEqual(self.library.photo(first)['status'], 1)


class ShortcutOwnershipTest(unittest.TestCase):
    def test_shortcut_conflict_marks_the_actual_secondary_field(self):
        chosen = keys.defaults()
        chosen['keep'] = ['', 'Down']
        dialog = keys.ShortcutDialog(None, chosen)
        try:
            self.assertFalse(dialog.save_button.isEnabled())
            self.assertNotIn('#FF453A', dialog.fields['keep', 0].styleSheet())
            self.assertIn('#FF453A', dialog.fields['keep', 1].styleSheet())
            dialog.fields['keep', 1].setKeySequence(QKeySequence('P'))
            self.assertTrue(dialog.save_button.isEnabled())
        finally:
            dialog.close()
            dialog.deleteLater()
            application.processEvents()

    def test_picture_widget_does_not_reintroduce_disabled_escape_shortcut(self):
        view = PhotoView()
        requested = []
        view.escape_requested.connect(lambda: requested.append('reset'))
        view.show()
        application.processEvents()
        try:
            # Window's configurable QShortcut is the owner of zoom-reset keys.
            QTest.keyClick(view, Qt.Key.Key_Escape)
            self.assertEqual(requested, [],
                             'A built-in Esc handler bypasses custom or unbound zoom-reset shortcuts')
        finally:
            view.close()
            view.deleteLater()
            application.processEvents()


if __name__ == '__main__':
    unittest.main()
