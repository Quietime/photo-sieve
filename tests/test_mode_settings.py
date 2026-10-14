"""Separate review/grading preferences and mode-specific menus on actual Qt windows."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from PySide6.QtCore import QSettings,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QLabel
from app import Library
from desktop import Window
import keys,theme
from rating_page import ACTIONS

application=QApplication.instance() or QApplication([])

class ModeSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.settings=QSettings(str(self.base/'preferences.ini'),QSettings.Format.IniFormat)
        self.settings.setValue('face_preview',False);self.settings.setValue('warm_count',0)
        self.patches=[patch(module+'.QSettings',return_value=self.settings) for module in ('desktop','rating_page','face_service','update_ui')]
        self.patches.append(patch('theme.accessibility_defaults',return_value=(False,False,False)))
        for p in self.patches:p.start()
        self.windows=[]
    def make_window(self):
        window=Window(Library(self.base/('records'+str(len(self.windows)))),check_updates=False,start_empty=True,data_root=self.base/'workspaces')
        window.resize(1280,900);window.show();self.windows.append(window);application.processEvents();return window
    def tearDown(self):
        for window in self.windows:
            window.prompt_on_close=False;window.close();application.processEvents();window.deleteLater();application.processEvents()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def action(self,window,text):return next(a for a in window.more_panel.action_controls if a.text()==text)
    def menu_names(self,window):return [a.text() for a in window.more_panel.action_controls]

    def test_legacy_hidden_dock_migrates_review_only_and_persists_independently(self):
        self.settings.setValue('show_actionbar',False)
        window=self.make_window()
        self.assertFalse(window.actionbar_visible);self.assertTrue(window.rating_actionbar_visible)
        self.assertTrue(window.action_row.isHidden());self.assertFalse(window.rating_page.action_row.isHidden())
        window.toggle_rating();window.set_actionbar_visible(False)
        self.assertFalse(self.settings.value('rating/show_actionbar',True,type=bool))
        window.toggle_rating();window.set_actionbar_visible(True)
        self.assertTrue(self.settings.value('review/show_actionbar',False,type=bool))
        self.assertFalse(self.settings.value('rating/show_actionbar',True,type=bool))
        second=self.make_window()
        self.assertTrue(second.actionbar_visible);self.assertFalse(second.rating_actionbar_visible)

    def test_dock_menu_toggles_only_current_mode_and_restores_after_switch(self):
        window=self.make_window();window.show_menu();self.action(window,'显示底部操作栏').setChecked(False);window.more_panel.hide()
        self.assertTrue(window.action_row.isHidden());self.assertFalse(window.rating_page.action_row.isHidden())
        window.toggle_rating();window.show_menu();self.assertTrue(self.action(window,'显示底部操作栏').isChecked())
        self.action(window,'显示底部操作栏').setChecked(False);window.more_panel.hide()
        window.toggle_rating();window.show_menu();self.assertFalse(self.action(window,'显示底部操作栏').isChecked())
        self.action(window,'显示底部操作栏').setChecked(True)
        self.assertFalse(window.action_row.isHidden());self.assertTrue(window.rating_page.action_row.isHidden())
        window.more_panel.hide();window.toggle_rating()
        self.assertTrue(window.rating_page.action_row.isHidden())

    def test_menus_have_separate_titles_and_actions_while_global_maintenance_is_explicit(self):
        window=self.make_window();window.show_menu()
        self.assertEqual(window.more_panel.windowTitle(),'照片筛选设置')
        review=self.menu_names(window)
        self.assertIn('筛选快捷键…',review);self.assertIn('人脸预览…',review);self.assertIn('预加载与缓存…',review)
        self.assertNotIn('分级快捷键…',review)
        window.more_panel.hide();window.toggle_rating();window.show_menu()
        self.assertEqual(window.more_panel.windowTitle(),'照片分级设置')
        rating=self.menu_names(window)
        self.assertIn('分级快捷键…',rating)
        for name in ('筛选快捷键…','人脸预览…','预加载与缓存…','排序与日期筛选…','重新扫描当前文件夹'):
            self.assertNotIn(name,rating)
        self.assertEqual(len(rating),len(set(rating)))
        titles=[label.text() for label in window.more_panel.findChildren(QLabel) if label.objectName()=='sectionTitle']
        self.assertIn('应用全局',titles)
        for name in ('一键清理全部缓存','检查更新','自动检查更新','打开日志文件夹'):
            self.assertIn(name,review);self.assertIn(name,rating)
        window.more_panel.hide()

    def test_fps_and_accessibility_preferences_are_restored_per_mode_including_restart(self):
        window=self.make_window();window.show_fps(True);window.set_accessibility('reduced_motion',True)
        window.set_accessibility('reduced_transparency',True)
        self.assertIsNotNone(window.fps_label);self.assertTrue(theme.REDUCED_MOTION);self.assertTrue(theme.REDUCED_TRANSPARENCY)
        window.toggle_rating()
        self.assertIsNone(window.fps_label);self.assertFalse(theme.REDUCED_MOTION);self.assertFalse(theme.REDUCED_TRANSPARENCY)
        window.show_fps(True);window.show_fps(False)
        window.toggle_rating()
        self.assertIsNotNone(window.fps_label);self.assertTrue(theme.REDUCED_MOTION)
        second=self.make_window();self.assertIsNotNone(second.fps_label)
        second.toggle_rating();self.assertIsNone(second.fps_label);self.assertFalse(theme.REDUCED_MOTION)

    def test_operating_system_accessibility_is_honored_in_both_modes(self):
        window=self.make_window()
        with patch('theme.accessibility_defaults',return_value=(True,True,False)):
            window.set_accessibility('reduced_motion',False);window.set_accessibility('reduced_transparency',False)
            self.assertTrue(theme.REDUCED_MOTION);self.assertTrue(theme.REDUCED_TRANSPARENCY)
            window.toggle_rating();self.assertTrue(theme.REDUCED_MOTION);self.assertTrue(theme.REDUCED_TRANSPARENCY)

    def test_saved_shortcut_namespaces_reload_without_overwriting_other_mode(self):
        review=keys.defaults();review['keep']=['F7','']
        rating=keys.defaults(ACTIONS);rating['star1']=['F8','']
        keys.save(self.settings,review);keys.save(self.settings,rating,'rating_shortcuts/')
        window=self.make_window()
        self.assertEqual(window.chosen_keys['keep'],['F7',''])
        self.assertEqual(window.rating_page.chosen['star1'],['F8',''])
        self.assertNotEqual(window.chosen_keys['keep'],window.rating_page.chosen['star1'])

    def test_shortcut_editors_and_active_bindings_belong_to_the_current_mode(self):
        window=self.make_window();review_editor=Mock();rating_editor=Mock()
        with patch.object(window,'edit_shortcuts',review_editor),patch.object(window.rating_page,'edit_keys',rating_editor):
            window.show_menu();self.action(window,'筛选快捷键…').trigger();window.more_panel.hide()
            self.assertEqual(review_editor.call_count,1);rating_editor.assert_not_called()
            window.toggle_rating();window.show_menu();self.action(window,'分级快捷键…').trigger();window.more_panel.hide()
            self.assertEqual(rating_editor.call_count,1);self.assertEqual(review_editor.call_count,1)
        window.toggle_rating();window.session_active=True;window.stack.setCurrentIndex(0)
        chosen=keys.defaults();chosen['keep']=['F7',''];review_action=Mock();window.act=review_action;window.apply_shortcuts(chosen)
        grade=Mock();window.rating_page.grade=grade;rating_keys=keys.defaults(ACTIONS);rating_keys['star1']=['F7',''];window.rating_page.apply_keys(rating_keys)
        window.sync_shortcut_mode();window.activateWindow();window.setFocus();application.processEvents();QTest.keyClick(window,Qt.Key.Key_F7)
        self.assertEqual(review_action.call_count,1);grade.assert_not_called()
        with patch.object(window.rating_page,'activate'):
            window.toggle_rating()
        window.setFocus();application.processEvents();QTest.keyClick(window,Qt.Key.Key_F7)
        self.assertEqual(review_action.call_count,1);grade.assert_called_once_with(1)
        self.assertTrue(all(not shortcut.isEnabled() for shortcut in window.shortcuts))
        self.assertTrue(all(shortcut.isEnabled() for shortcut in window.rating_page.shortcut_objects))

if __name__=='__main__':unittest.main()
