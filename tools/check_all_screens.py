"""Open every app surface through its controls and retain screenshots and a UI inventory.

Run with QT_QPA_PLATFORM=offscreen on Linux, or the normal Windows platform plugin.
Only generated photos and temporary records are touched. Update downloads are forbidden.
The --output directory is retained for visual review; no user library is opened.
"""
import argparse
import html
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from PySide6.QtCore import QEvent, QPoint, QRectF, QSettings, Qt, QTimer, QDate
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractButton, QApplication, QCheckBox,
    QFileDialog as NativeFileDialog, QLabel, QLineEdit, QSpinBox, QComboBox, QDateEdit, QScrollArea)
from app import Library
from desktop import STYLE, Window
import keys
import updater


class ScreenCheck:
    def __init__(self, application, output):
        self.application = application
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.screens = []
        self.checks = []
        self.findings = []

    def until(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.application.processEvents()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError('UI operation timed out')

    def check(self, name, predicate, detail=''):
        result = bool(predicate)
        self.checks.append({'name': name, 'passed': result, 'detail': detail})
        if not result:
            self.findings.append(name + (': ' + detail if detail else ''))
        return result

    def screenshot(self, name, widget):
        QTest.qWait(90)
        self.application.processEvents()
        path = self.output / (name + '.png')
        if not widget.grab().save(str(path)):
            raise AssertionError('Could not save ' + str(path))
        controls = []
        for control in widget.findChildren(QAbstractButton):
            if not control.isVisibleTo(widget):
                continue
            position = control.mapTo(widget, QPoint(0, 0))
            controls.append({'label': control.text() or control.accessibleName(),
                'type': type(control).__name__, 'enabled': control.isEnabled(),
                'geometry': [position.x(), position.y(), control.width(), control.height()]})
        self.screens.append({'name': name, 'file': str(path), 'title': widget.windowTitle(),
            'type': type(widget).__name__, 'size': [widget.width(), widget.height()],
            'top_level': widget.isWindow(), 'frameless': bool(widget.windowFlags() & Qt.WindowType.FramelessWindowHint),
            'controls': controls})

    @staticmethod
    def button(widget, text):
        # Buttons in an inner settings panel must not accidentally match hidden older panels.
        candidates = [b for b in widget.findChildren(QAbstractButton)
            if b.isVisibleTo(widget) and (b.text() == text or b.accessibleName() == text)]
        if not candidates:
            raise AssertionError('Visible button not found: ' + text)
        return candidates[-1]

    def click(self, button):
        assert button.isEnabled(), 'Disabled button: ' + button.text()
        parent=button.parentWidget()
        while parent:
            if isinstance(parent,QScrollArea):parent.ensureWidgetVisible(button);self.application.processEvents()
            parent=parent.parentWidget()
        position = QPoint(20, button.height() // 2) if isinstance(button, QCheckBox) else button.rect().center()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=position)
        self.application.processEvents()

    def modal(self, name, trigger, operate=None, expected_title=None):
        """A real click enters QDialog.exec; a timer captures and operates that real dialog."""
        state = {'dialog': None, 'error': None}
        started = time.monotonic()
        timer = QTimer()
        timer.setInterval(25)

        def capture():
            dialog = self.application.activeModalWidget()
            if dialog is None:
                if time.monotonic() - started > 8:
                    state['error'] = AssertionError('No modal opened for ' + name)
                    timer.stop()
                return
            timer.stop()
            state['dialog'] = dialog
            try:
                if expected_title:
                    assert dialog.windowTitle() == expected_title, dialog.windowTitle()
                self.screenshot(name, dialog)
                self.check(name + '_frameless', bool(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint))
                mask = dialog.mask()
                self.check(name + '_rounded_corners', not mask.isEmpty() and
                    not mask.contains(QPoint(0, 0)) and not mask.contains(QPoint(4, 4)) and mask.contains(QPoint(20, 20)))
                if operate:
                    operate(dialog)
                if dialog.isVisible():
                    # Do not leave a nested event loop alive if an operation only inspected fields.
                    dialog.reject()
            except BaseException as error:
                state['error'] = error
                dialog.reject()

        timer.timeout.connect(capture)
        timer.start()
        trigger()
        timer.stop()
        if state['error']:
            raise state['error']
        assert state['dialog'] is not None, 'No modal captured: ' + name
        return state['dialog']

    def open_menu(self, window, name='more_options'):
        self.click(window.more_button)
        panel = window.more_panel
        self.until(panel.isVisible)
        self.screenshot(name, panel)
        self.screenshot(name + '_in_window', window)
        self.check(name + '_inside_app', not panel.isWindow())
        return panel

    def segment(self, window, index):
        size = window.segments.segment_width()
        QTest.mouseClick(window.segments, Qt.MouseButton.LeftButton,
            pos=QPoint(round(2 + size * (index + .5)), window.segments.height() // 2))
        self.until(lambda: not window.fetching and window.view == window.VIEWS[index][0])


def fixture(directory):
    directory.mkdir()
    extensions = ['jpg', 'png', 'bmp', 'tiff', 'webp']
    for i in range(30):
        image = Image.new('RGB', (800,1200) if i>=28 else (1200,800), (20 + i * 3, 35 + i * 2, 72 + i * 4))
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((60, 90, 1140, 680), outline=(220, 230, 240), width=5)
        drawing.ellipse((100 + i * 20, 200, 500 + i * 20, 600), fill=(50, 160, 210))
        drawing.polygon([(700, 650), (920, 170), (1120, 650)], fill=(215, 160, 95))
        drawing.text((80, 110), 'Photo Sieve UI test: %02d' % i, fill='white', font_size=40)
        image.save(directory / ('photo_%02d.%s' % (i, extensions[i % len(extensions)])))
    (directory / 'voice.wav').write_bytes(b'NOT A PHOTO: TEST FIXTURE')
    (directory / 'notes.txt').write_text('This non-photo must not be imported.', encoding='utf-8')


def run(output):
    application = QApplication.instance() or QApplication([])
    application.setStyleSheet(STYLE)
    application.setQuitOnLastWindowClosed(False)
    audit = ScreenCheck(application, output)
    window = None
    with tempfile.TemporaryDirectory(prefix='photosieve-ui-') as temporary:
        base = Path(temporary).resolve()
        # Explicit organization/application constructors can retain NativeFormat (the Windows
        # registry), so setDefaultFormat/setPath alone do not isolate the application's settings.
        preferences = QSettings(str(base / 'preferences.ini'), QSettings.Format.IniFormat)
        fixture(base / 'test-photos')
        with patch.object(updater, 'check_release', side_effect=AssertionError('UI checks must not access real releases')), \
             patch.object(updater, 'download_release', side_effect=AssertionError('UI checks must not download updates')), \
             patch('desktop.QSettings', return_value=preferences), patch('update_ui.QSettings', return_value=preferences):
            try:
                window = Window(Library(base / 'workspaces' / 'empty'), check_updates=False,
                    start_empty=True, prompt_on_close=True, data_root=base / 'workspaces')
                window.resize(1280, 900)
                window.apply_shortcuts(keys.defaults())
                window.show()
                audit.until(lambda: window.state_data is not None)
                audit.screenshot('01_start_empty', window)
                audit.screenshot('01a_window_chrome_icons', window.window_chrome)
                audit.check('startup_empty', window.stack.currentIndex() == 2 and not window.queue)
                audit.check('main_icon_present', not window.windowIcon().pixmap(32,32).isNull())
                window.toast.show_message('启动状态提示 · 不覆盖选择文件夹控件','info')
                audit.screenshot('01c_welcome_toast_layout',window)
                welcome=window.stack.widget(2)
                for control in welcome.findChildren(QAbstractButton)+welcome.findChildren(QLabel):
                    position=control.mapTo(window.centralWidget(),QPoint(0,0))
                    rect=control.rect().translated(position)
                    audit.check('welcome_status_clear_of_'+control.text(),not rect.intersects(window.toast.geometry()))
                scale=min(welcome.width()/welcome.background.width(),max(1,welcome.height()-100)/welcome.background.height())
                origin=welcome.mapTo(window.centralWidget(),QPoint(0,0))
                for index,part in enumerate(welcome.parts):
                    visible=QRectF(welcome.width()/2-part.width()*scale/2+origin.x(),part.top()*scale+origin.y(),part.width()*scale,part.height()*scale)
                    audit.check('welcome_status_clear_of_brand_'+str(index),not visible.intersects(QRectF(window.toast.geometry())))
                window.toast.hide()
                audit.click(audit.button(window.window_chrome, '最小化'))
                audit.check('minimize_button_operates', window.isMinimized())
                window.showNormal()
                QTest.qWait(100)
                audit.click(window.window_chrome.max_button)
                audit.until(window.isMaximized)
                audit.check('maximize_button_operates', window.isMaximized())
                audit.screenshot('01b_maximized_start', window)
                # These are two separate clicks, not a native double click. Windows
                # otherwise coalesces the second press within its double-click interval.
                QTest.qWait(application.doubleClickInterval() + 50)
                audit.click(window.window_chrome.max_button)
                audit.until(lambda: not window.isMaximized())
                audit.check('maximize_button_restores_window', not window.isMaximized())
                window.resize(1280, 900)

                audit.modal('02_source_folder_picker', lambda: audit.click(window.open_button),
                    expected_title='选择照片文件夹')
                audit.check('cancel_picker_keeps_empty', window.stack.currentIndex() == 2)

                def import_folder(dialog):
                    assert isinstance(dialog, NativeFileDialog)
                    dialog.setDirectory(str(base / 'test-photos'))
                    dialog.accept()
                audit.modal('03_source_folder_selected', lambda: audit.click(window.open_button), import_folder)
                audit.until(lambda: window.keep_button.isEnabled() and len(window.queue) == 30)
                audit.until(lambda: all(window.strip.item(i).data(Qt.ItemDataRole.UserRole) in window.thumb_cache
                    for i in window.strip.visible_rows()))
                audit.screenshot('04_pending_review', window)
                audit.check('only_photos_imported', len(window.queue) == 30)
                audit.check('all_visible_thumbnails_loaded', window.strip.count() == 30 and
                    all(window.strip.item(i).data(Qt.ItemDataRole.UserRole) in window.thumb_cache for i in window.strip.visible_rows()))

                bar=window.strip.horizontalScrollBar()
                audit.check('filmstrip_indexes_all_photos_and_has_scrollbar',window.strip.count()==30 and bar.maximum()>0)
                bar.setValue(bar.maximum());application.processEvents()
                audit.until(lambda:all(pid in window.thumb_cache or pid in window.cache for pid in window.strip.visible_ids()))
                audit.screenshot('04a_filmstrip_scrolled_to_end',window)
                last=window.strip.item(window.strip.count()-1)
                QTest.mouseClick(window.strip.viewport(),Qt.MouseButton.LeftButton,pos=window.strip.visualItemRect(last).center())
                audit.until(lambda:window.queue and window.queue[0]['id']==last.data(Qt.ItemDataRole.UserRole))
                audit.check('filmstrip_click_jumps_to_distant_photo',True)
                portrait=window.strip.item(28)
                QTest.mouseClick(window.strip.viewport(),Qt.MouseButton.LeftButton,pos=window.strip.visualItemRect(portrait).center())
                audit.until(lambda:len(window.queue)==2 and all(pane.pixmap is not None for pane in window.photos) and window.strip.widths.get(window.queue[0]['id'],116)<=60)
                audit.screenshot('04p_portrait_compact_filmstrip',window)
                audit.check('portrait_filmstrip_removes_landscape_sized_gaps',window.strip.widths.get(window.queue[0]['id'],116)<=60 and window.strip.count()==30)
                window.load();audit.until(lambda:len(window.queue)==30 and window.keep_button.isEnabled())
                def ordering(dialog):
                    combo=dialog.findChild(QComboBox);dates=dialog.findChildren(QDateEdit);enabled=dialog.findChild(QCheckBox)
                    enabled.setChecked(True);dates[0].setDate(QDate.currentDate().addDays(2));dates[1].setDate(QDate.currentDate())
                    audit.click(audit.button(dialog,'应用'))
                    audit.check('invalid_date_range_keeps_dialog_open',dialog.isVisible())
                    audit.screenshot('04c_ordering_invalid_dates',dialog)
                    dates[0].setDate(QDate.currentDate().addDays(-1));dates[1].setDate(QDate.currentDate().addDays(1))
                    QTest.mouseClick(dates[0],Qt.MouseButton.LeftButton,pos=QPoint(dates[0].width()-10,dates[0].height()//2))
                    application.processEvents()
                    popup=QApplication.activePopupWidget()
                    if popup:
                        audit.screenshot('04d_date_calendar',popup);QTest.keyClick(popup,Qt.Key.Key_Escape)
                    combo.setCurrentIndex(combo.findData('newest'));audit.click(audit.button(dialog,'应用'))
                audit.modal('04b_ordering_and_date_filter',window.edit_ordering,ordering)
                audit.until(lambda:len(window.queue)==30 and window.keep_button.isEnabled())
                audit.check('date_order_applies_to_full_strip',window.sort_order=='newest' and window.strip.count()==30 and window.date_start is not None)
                audit.screenshot('04e_date_sorted_review',window)
                def reset_order(dialog):
                    dialog.findChild(QCheckBox).setChecked(False);dialog.findChild(QComboBox).setCurrentIndex(0)
                    audit.click(audit.button(dialog,'应用'))
                audit.modal('04f_clear_date_filter',window.edit_ordering,reset_order)
                audit.until(lambda:len(window.queue)==30 and window.keep_button.isEnabled())
                audit.check('date_filter_can_be_cleared',window.date_start is None and window.sort_order=='file')
                first = window.queue[0]['id']
                audit.click(window.keep_button)
                audit.until(lambda: not window.pending_ops and not window.inflight)
                audit.click(window.delete_button)
                audit.until(lambda: not window.pending_ops and not window.inflight)
                window.poll()
                audit.until(lambda: window.counts.get('keep') == 1 and window.counts.get('deleted') == 1)
                audit.check('buttons_act_on_left_photo', window.library.photo(first)['status'] == 1)

                audit.segment(window, 1)
                audit.until(lambda: window.kept_grid.count()==1 and all(pid in window.kept_thumb_cache for pid in window.kept_grid.visible_ids()))
                audit.screenshot('05_kept_review', window)
                audit.check('kept_hidden_from_filmstrip', window.strip.count() == 0)
                audit.check('kept_category_is_a_thumbnail_gallery',window.stack.currentIndex()==4 and window.kept_grid.count()==1)
                item=window.kept_grid.item(0)
                QTest.mouseClick(window.kept_grid.viewport(),Qt.MouseButton.LeftButton,pos=window.kept_grid.visualItemRect(item).center())
                audit.until(lambda:window.stack.currentIndex()==0 and window.delete_button.isEnabled())
                audit.screenshot('05c_kept_photo_large_view',window)
                audit.click(window.keep_back_button)
                audit.until(lambda:window.stack.currentIndex()==4 and window.kept_grid.count()==1)
                audit.screenshot('05d_kept_gallery_returned',window)
                audit.check('kept_photo_opens_inside_app_and_returns_to_gallery',window.stack.currentIndex()==4)


                audit.segment(window, 2)
                audit.until(lambda: window.deleted_grid.count() == 1)
                audit.screenshot('08_deleted_grid', window)
                audit.click(window.select_all_deleted)
                audit.screenshot('09_deleted_selected_all', window)
                audit.check('deleted_select_all', len(window.deleted_grid.selectedItems()) == 1)
                audit.modal('10_permanent_delete_confirmation', lambda: audit.click(window.purge_selected),
                    lambda dialog: audit.click(audit.button(dialog, '取消')))
                audit.check('cancel_purge_preserves_deleted_file', window.library.state()['counts']['deleted'] == 1)
                audit.click(window.restore_selected)
                audit.until(lambda: not window.pending_ops and not window.inflight and window.deleted_grid.count() == 0)
                audit.screenshot('11_deleted_empty', window)

                panel = audit.open_menu(window, '12_more_options')
                titles=[label.text() for label in panel.findChildren(QLabel) if label.objectName()=='sectionTitle']
                audit.check('settings_have_mode_and_global_categories',panel.windowTitle()=='照片筛选设置' and all(title in titles for title in ['照片管理','筛选界面与操作','筛选预加载','应用全局']))
                names=[control.text() for control in panel.findChildren(QAbstractButton) if control.objectName()=='menuItem']
                audit.check('settings_have_no_duplicate_controls',len(names)==len(set(names)))
                audit.click(audit.button(panel,'显示底部操作栏'))
                audit.check('bottom_action_row_hidden',window.action_row.isHidden())
                panel.hide();audit.segment(window,0)
                audit.until(lambda:bool(window.queue) and window.photos[0].pixmap is not None)
                audit.screenshot('12c_bottom_actions_hidden',window)
                panel.exec();audit.screenshot('12d_settings_dock_disabled',panel)
                audit.click(audit.button(panel,'显示底部操作栏'))
                audit.check('bottom_action_row_restored',not window.action_row.isHidden())
                panel.scroll.verticalScrollBar().setValue(0)
                panel_size = panel.size()
                application.sendEvent(panel, QEvent(QEvent.Type.WindowDeactivate))
                audit.check('system_focus_loss_keeps_menu', panel.isVisible() and panel.size() == panel_size)
                QTest.keyClick(panel, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
                audit.check('unrelated_shortcut_keeps_menu', panel.isVisible() and panel.size() == panel_size)
                audit.click(window.more_button)
                audit.check('more_button_toggles_menu_closed', not panel.isVisible())
                panel = audit.open_menu(window, '12b_more_options_before_outside_click')
                # Click an inert area inside the app. Screenshot shortcuts / focus loss above must not close it.
                QTest.mouseClick(window.subtitle, Qt.MouseButton.LeftButton)
                audit.check('left_click_outside_closes_menu', not panel.isVisible())
                panel.close()

                def shortcuts(dialog):
                    for field in dialog.fields.values():
                        field.clear()
                    audit.screenshot('13b_shortcuts_all_unbound', dialog)
                    audit.check('unbound_shortcuts_use_chinese_placeholder', all(
                        field.findChild(QLineEdit).placeholderText() == '未设置' for field in dialog.fields.values()))
                    audit.check('all_shortcuts_can_be_unbound', dialog.save_button.isEnabled())
                    dialog.fields['keep', 0].setKeySequence(QKeySequence('P'))
                    dialog.fields['delete', 0].setKeySequence(QKeySequence('P'))
                    audit.screenshot('13c_shortcuts_conflict', dialog)
                    audit.check('duplicate_shortcuts_block_save', not dialog.save_button.isEnabled())
                    audit.click(audit.button(dialog, '恢复默认'))
                    audit.check('restore_shortcut_defaults', dialog.chosen() == keys.defaults())
                    audit.click(audit.button(dialog, '保存'))
                panel = audit.open_menu(window, '13a_menu_before_shortcuts')
                audit.modal('13_shortcuts', lambda: audit.click(audit.button(panel, '筛选快捷键…')), shortcuts)

                def cache(dialog):
                    fields = dialog.findChildren(QSpinBox)
                    assert len(fields) == 3
                    audit.check('cache_inputs_have_touch_spacing', all(field.height() >= 32 for field in fields))
                    ordered = sorted(fields, key=lambda field: field.mapTo(dialog, QPoint(0, 0)).y())
                    audit.check('cache_form_rows_have_vertical_spacing', all(
                        ordered[i+1].mapTo(dialog, QPoint(0, 0)).y() - ordered[i].mapTo(dialog, QPoint(0, 0)).y() - ordered[i].height() >= 8
                        for i in range(len(ordered)-1)))
                    for field_index,field in enumerate(fields):
                        original=field.value()
                        field.setValue(max(field.minimum()+1,original))
                        initial=field.value()
                        QTest.mouseClick(field,Qt.MouseButton.LeftButton,pos=field.step_rect(True).center())
                        audit.check('cache_step_up_'+str(field_index),field.value()==initial+1)
                        QTest.mouseClick(field,Qt.MouseButton.LeftButton,pos=field.step_rect(False).center())
                        audit.check('cache_step_down_'+str(field_index),field.value()==initial)
                        field.setValue(original)
                    fields[0].setValue(350)
                    fields[1].setValue(0)
                    fields[2].setValue(128)
                    audit.screenshot('14b_cache_custom_values', dialog)
                    audit.click(audit.button(dialog, '保存'))
                panel = audit.open_menu(window, '14a_menu_before_cache')
                audit.modal('14_preload_and_cache', lambda: audit.click(audit.button(panel, '预加载与缓存…')), cache)
                audit.check('preload_settings_apply', window.preload_count == 350 and window.warm_count == 0 and
                    window.library.cache_limit == 128 * 1024 * 1024)

                panel = audit.open_menu(window, '14c_menu_before_clear_cache')
                audit.click(audit.button(panel,'一键清理全部缓存'))
                audit.until(lambda: not window.cache_clearing and not window.busy)
                audit.screenshot('14e_cache_cleared_feedback', window)
                audit.check('cache_clear_preserves_originals_and_marks',
                    len(list((base / 'test-photos').glob('photo_*'))) == 30 and window.library.state()['counts']['keep'] == 1)

                panel = audit.open_menu(window, '15_more_options_preferences')
                audit.click(audit.button(panel, '显示帧率'))
                audit.check('fps_toggle_operates', window.fps_label is not None)
                panel.hide()
                original_size=window.size()
                def verify_fps(tag):
                    application.processEvents()
                    fps=window.fps_label
                    rect=QRectF(fps.mapTo(window,QPoint(0,0)),fps.size())
                    controls=[window.open_button,window.more_button,
                        *window.window_chrome.findChildren(QAbstractButton)]
                    audit.check(tag+'_no_button_overlap',all(not rect.intersects(
                        QRectF(button.mapTo(window,QPoint(0,0)),button.size())) for button in controls))
                    audit.check(tag+'_inside_title_bar',fps.parentWidget() is window.window_chrome
                        and window.window_chrome.rect().contains(fps.geometry()))
                    audit.screenshot(tag,window)
                verify_fps('15a_fps_enabled')
                fps=window.fps_label;window.show_fps(True)
                audit.check('fps_repeated_enable_reuses_label',window.fps_label is fps)
                window.frames=9999;window.tick_fps();window.resize(900,640)
                verify_fps('15a2_fps_narrow_large_value')
                window.showMaximized();verify_fps('15a3_fps_maximized')
                window.showNormal();window.resize(original_size);panel.exec()

                audit.click(audit.button(panel, '显示帧率'))
                audit.check('fps_toggle_off_operates', window.fps_label is None)
                audit.click(audit.button(panel, '减少动态效果'))
                audit.check('obsolete_transparency_option_removed',not any(button.text()=='减少透明效果' for button in panel.findChildren(QAbstractButton)))
                audit.screenshot('15b_more_options_accessibility', panel)
                panel.close()

                release = {'version': '99.0.0', 'notes': '界面测试专用版本。\n不会下载或安装。'}
                result = []
                audit.modal('16_update_offer', lambda: result.append(window.updates.ask(release)),
                    lambda dialog: audit.click(audit.button(dialog, '以后再说')))
                audit.check('decline_update_downloads_nothing', result == [False])

                # The staged-only menu entry is covered without launching its installer.
                window.updates.staged = base / 'fake-staged-update'
                panel = audit.open_menu(window, '16b_staged_update_menu')
                audit.check('staged_update_control_present', audit.button(panel, '立即安装更新并重启').isEnabled())
                panel.close()
                window.updates.staged = None

                panel = audit.open_menu(window, '16c_logs_menu')
                audit.check('uninstall_not_in_app_menu',not any('卸载' in button.text() for button in panel.findChildren(QAbstractButton)))
                with patch.object(window,'open_folder') as opened:
                    audit.click(audit.button(panel,'打开日志文件夹'))
                    audit.check('logs_folder_accessible',opened.called)

                audit.segment(window, 0)
                audit.until(lambda: window.keep_button.isEnabled() and window.photos[0].pixmap is not None)
                QTest.mouseClick(window.photos[0].viewport(), Qt.MouseButton.LeftButton,
                    pos=window.photos[0].viewport().rect().center())
                audit.until(lambda: window.photos[0].full_resolution and not window.photos[0].zoom_animation)
                audit.screenshot('17_photo_in_place_zoom', window)
                window.reset_zoom()
                window.resize(960, 720)
                QTest.qWait(300)
                audit.screenshot('18_small_window_review', window)
                panel = audit.open_menu(window, '19_small_window_menu')
                top_left = panel.mapTo(window, QPoint(0, 0))
                audit.check('small_window_menu_inside_bounds', not panel.isWindow() and
                    top_left.x() >= 0 and top_left.y() >= 0 and
                    top_left.x() + panel.width() <= window.width() and top_left.y() + panel.height() <= window.height())
                panel.close()
                window.busy = True
                try:
                    audit.modal('19b_busy_close_guard', lambda: audit.click(audit.button(window.window_chrome, '关闭')))
                finally:
                    window.busy = False
                audit.check('busy_close_keeps_app_open', window.isVisible() and not window.closed)
                audit.modal('20_close_folder_prompt', lambda: audit.click(audit.button(window.window_chrome, '关闭')),
                    lambda dialog: audit.click(audit.button(dialog, '取消')))
                audit.check('cancel_close_keeps_app_open', window.isVisible() and not window.closed)
            except BaseException as error:
                if window:
                    audit.screenshot('failure_window', window)
                    audit.findings.append('Window state: ' + json.dumps({
                        'maximized': window.isMaximized(), 'geometry': window.geometry().getRect(),
                        'busy': window.busy, 'fetching': window.fetching,
                        'counts': window.counts, 'job': window.library.job,
                        'state_job': (window.state_data or {}).get('job'),
                        'export_removed': not hasattr(window,'export_button')}, default=str))
                audit.findings.append(type(error).__name__ + ': ' + str(error))
                audit.checks.append({'name': 'complete_surface_walkthrough', 'passed': False, 'detail': str(error)})
            finally:
                if window:
                    window.prompt_on_close = False
                    # Temporary worker activity is allowed to finish before closing the app.
                    try:
                        audit.until(lambda: not window.busy and not window.library.job['running'] and not window.pending_ops)
                    except AssertionError:
                        pass
                    window.close()
                report = {'platform': application.platformName(), 'screens': audit.screens,
                    'checks': audit.checks, 'findings': audit.findings,
                    'passed': not audit.findings,
                    'scope': 'Generated photos only; updates mocked; permanent deletion canceled.'}
                (audit.output / 'ui-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                cards = ''.join('<figure><a href="{0}"><img loading="lazy" src="{0}" alt="{1}"></a><figcaption>{1}</figcaption></figure>'.format(
                    html.escape(Path(screen['file']).name), html.escape(screen['name'])) for screen in audit.screens)
                gallery = '<!doctype html><meta charset="utf-8"><title>Photo Sieve UI audit</title><style>body{background:#111;color:#eee;font:16px system-ui;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:24px}figure{margin:0;padding:16px;background:#222;border-radius:16px}img{display:block;width:100%;height:300px;object-fit:contain}figcaption{margin-top:14px}</style><h1>Photo Sieve · 界面测试截图</h1><p>所有截图使用自动生成的临时照片。点击可查看原图。</p><main>' + cards + '</main>'
                (audit.output / 'index.html').write_text(gallery, encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'screenshots': len(audit.screens),
        'checks': len(audit.checks), 'findings': audit.findings, 'report': str(audit.output / 'ui-report.json')}, ensure_ascii=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('ui-screenshots'))
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.output))
