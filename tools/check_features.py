"""Focused GUI checks for albums, faces, resume, compact settings, and camera metadata.

Only temporary generated photos are used. Network/update checks are forbidden.
Run under the normal Windows plugin or QT_QPA_PLATFORM=offscreen on Linux.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractButton, QLabel, QCheckBox, QSpinBox
from app import Library
from desktop import Window, STYLE
import keys
import updater


def run(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setStyleSheet(STYLE)
    application.setQuitOnLastWindowClosed(False)
    checks, screenshots = [], []

    def until(predicate, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            application.processEvents()
            if predicate():
                return
            QTest.qWait(10)
        raise AssertionError('Timed out waiting for affected UI')

    def check(name, condition):
        assert condition, name
        checks.append(name)

    def capture(name, widget):
        QTest.qWait(100)
        path = output / (name + '.png')
        assert widget.grab().save(str(path)), name
        screenshots.append(str(path))

    def click(widget):
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())
        application.processEvents()

    def snapshot(library):
        with library.connect() as db:
            return (tuple(tuple(row) for row in db.execute('SELECT id,status,present FROM photos ORDER BY id')),
                    tuple(tuple(row) for row in db.execute('SELECT seq,photo_id,action,before,after FROM history ORDER BY seq')))

    def test_readonly(window, label):
        before = snapshot(window.library)
        check(label + '_culling_shortcuts_disabled', all(not item.isEnabled() for item in window.shortcuts))
        for key, modifier in ((Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier),
                              (Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier),
                              (Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier),
                              (Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)):
            QTest.keyClick(window, key, modifier)
        # Defense in depth: action entry point must also ignore album mode.
        window.act('keep')
        window.act('delete')
        QTest.qWait(160)
        check(label + '_keyboard_and_actions_do_not_mutate', snapshot(window.library) == before)
        check(label + '_dock_hidden', not window.actionbar.isVisible())
        check(label + '_export_hidden', not window.export_button.isVisible())
        check(label + '_faces_hidden', not window.face_panel.isVisible())
        check(label + '_pending_decision_toast_hidden', not window.toast.isVisible())

    with tempfile.TemporaryDirectory(prefix='photosieve-features-') as temporary:
        base = Path(temporary).resolve()
        photos = base / 'generated-photos'
        photos.mkdir()
        settings = QSettings(str(base / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('face_preview', False)
        settings.setValue('face_ahead', 0)
        settings.setValue('warm_count', 0)
        settings.setValue('last_folder', str(photos))
        originals = {}
        for index in range(8):
            image = Image.new('RGB', (1200, 800) if index % 2 else (800, 1200), (45 + index * 12, 72, 102))
            draw = ImageDraw.Draw(image)
            draw.rectangle((80, 100, 650, 650), outline='ivory', width=5)
            draw.text((110, 130), 'PHOTO %02d' % index, fill='ivory', font_size=52)
            exif = Image.Exif()
            exif[34665] = {33437: 2.8 + index, 33434: 1 / (100 + index * 100),
                           34855: 100 + index * 100, 36867: '2026:10:14 12:%02d:30' % index}
            path = photos / ('photo_%02d.jpg' % index)
            if index == 0 and os.environ.get('PHOTOSIEVE_FACE_SAMPLE'):
                with Image.open(os.environ['PHOTOSIEVE_FACE_SAMPLE']) as sample:
                    sample.convert('RGB').save(path, exif=exif, quality=98)
            else:
                image.save(path, exif=exif)
            originals[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()

        with patch('desktop.QSettings', return_value=settings), \
             patch('face_service.QSettings', return_value=settings), \
             patch('update_ui.QSettings', return_value=settings), \
             patch.object(updater, 'check_release', side_effect=AssertionError('No network in feature GUI check')), \
             patch.object(updater, 'download_release', side_effect=AssertionError('No downloads in feature GUI check')):
            window = Window(Library(base / 'workspace'), check_updates=False, start_empty=True)
            try:
                window.faces.timer.stop()  # Scheduling/inference is covered by the isolated service checks.
                window.resize(1280, 900)
                chosen = keys.defaults()
                chosen['keep'], chosen['next'] = ['Right', ''], ['N', '']
                window.apply_shortcuts(chosen)
                window.show()
                window.activateWindow()
                until(lambda: window.state_data is not None)
                capture('01-empty-explicit-resume', window)
                check('startup_stays_empty_despite_last_folder', window.stack.currentIndex() == 2 and not window.queue)
                check('resume_visible_and_enabled', window.resume_button.isVisible() and window.resume_button.isEnabled())
                check('welcome_resume_enabled', window.welcome_resume.isVisible() and window.welcome_resume.isEnabled())
                click(window.resume_button)
                capture('02-folder-import', window)
                until(lambda: not window.importing and not window.fetching and len(window.queue) == 8 and window.keep_button.isEnabled())
                until(lambda: window.queue[0]['id'] in window.cache and window.queue[1]['id'] in window.cache)
                until(lambda: 'ISO 200' in window.details[1].text())
                check('resume_explicitly_imports_last_folder', window.session_active and len(window.queue) == 8)
                check('right_metadata_matches_right_photo', window.queue[1]['name'] == 'photo_01.jpg' and
                      'f/3.8' in window.details[1].text() and '1/200s' in window.details[1].text() and
                      'ISO 200' in window.details[1].text() and '2026-10-14 12:01:30' in window.details[1].text())
                check('metadata_is_not_left_photo', 'ISO 100' not in window.details[1].text())
                capture('03-pending-right-camera-parameters', window)

                # Deterministic GUI crop: native-pixel and event routing checks are independent
                # of model inference quality, covered by test_faces/test_face_service.
                crop = Image.new('RGB', (320, 240), '#cdad83')
                drawing = ImageDraw.Draw(crop)
                drawing.ellipse((82, 50, 112, 78), fill='#25364b')
                drawing.ellipse((203, 50, 233, 78), fill='#25364b')
                png = io.BytesIO()
                crop.save(png, 'PNG')
                result = {'imagebytes': png.getvalue(), 'faces': [{'box': [.1, .1, .3, .4]}],
                    'face': {'landmarks': [[.12125,.08],[.2725,.08]]}, 'original_size': [800,1200],
                    'crop_box': [0,32,320,272], 'is_partial': False, 'ratio': 1}
                window.faces.enabled = True
                current = window.queue[0]['id']
                window.face_changed(current, result)
                check('face_panel_belongs_to_left_photo_viewport', window.face_panel.parentWidget() is window.photos[0].viewport())
                check('face_starts_at_native_100_percent', window.face_panel.isVisible() and not window.face_panel.fit and window.face_panel.scale == 1)
                check('face_png_keeps_original_pixel_dimensions', window.face_panel.picture.width() == 320 and window.face_panel.picture.height() == 240)
                capture('04-native-face-crop', window)
                before = snapshot(window.library)
                click(window.face_panel)
                check('face_click_toggles_fit_without_zooming_main', window.face_panel.fit and not window.photos[0].detail)
                event = QWheelEvent(QPointF(110,110), QPointF(window.face_panel.mapToGlobal(QPoint(110,110))),
                    QPoint(), QPoint(0,120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                    Qt.ScrollPhase.NoScrollPhase, False)
                application.sendEvent(window.face_panel, event)
                check('face_wheel_only_zooms_face', not window.face_panel.fit and window.face_panel.scale > 1 and not window.photos[0].detail)
                QTest.mouseClick(window.face_panel, Qt.MouseButton.RightButton, pos=QPoint(110,110))
                check('face_click_and_wheel_do_not_advance_or_mutate', window.queue[0]['id'] == current and snapshot(window.library) == before)
                window.face_changed(window.queue[1]['id'], {'faces': []})
                check('right_face_result_cannot_replace_left_face', window.face_panel.result is result)
                window.face_changed(current, {'faces': []})
                check('no_face_hides_preview', not window.face_panel.isVisible())
                window.faces.enabled = False
                if os.environ.get('PHOTOSIEVE_FACE_SAMPLE'):
                    window.faces.configure(True, 0)
                    until(lambda: window.faces.result_for(window.queue[0]) is not None, 45)
                    actual = window.faces.result_for(window.queue[0])
                    check('real_offline_model_finds_public_fixture_face', bool(actual.get('faces')) and bool(actual.get('imagebytes')))
                    check('real_worker_crop_is_native_lossless_png', actual['ratio'] == 1 and actual['native_size'] == actual['output_size'] and actual['imagebytes'].startswith(b'\x89PNG'))
                    window.face_changed(current, actual)
                    capture('04b-real-local-model-native-face', window)
                    window.faces.configure(False, 0)
                    window.face_changed(current, None)

                window.show_menu()
                panel = window.more_panel
                until(panel.isVisible)
                capture('05-compact-categorized-more-menu', window)
                check('menu_is_embedded_not_separate_window', not panel.isWindow())
                check('four_distinct_menu_groups', panel.group_count == 4)
                check('normal_menu_needs_no_vertical_scroll', panel.scroll.verticalScrollBar().maximum() == 0)
                check('compact_controls_fit_34px_rows', all(26 <= control.height() <= 34 for control in panel.action_controls.values()))
                text = '\n'.join(label.text() for label in panel.findChildren(QLabel))
                check('hardware_numeric_summary_removed', '核' not in text and 'GB 内存' not in text and '预读' not in text)
                QTest.keyClick(panel, Qt.Key.Key_Print)
                QTest.qWait(50)
                check('unrelated_shortcut_does_not_dismiss_more_menu', panel.isVisible())
                panel.hide()

                # Open the real settings action and save both supported ahead values.
                for enabled, ahead, suffix in ((True, 100, '100-ahead'), (False, 50, 'disabled-50-ahead')):
                    window.show_menu()
                    action_button = next(control for action, control in window.more_panel.action_controls.items() if action.text() == '人脸预览…')
                    state = {'error': None, 'seen': False}
                    timer = QTimer()
                    timer.setInterval(25)
                    def operate_face_settings():
                        dialog = application.activeModalWidget()
                        if dialog is None:
                            return
                        timer.stop()
                        try:
                            state['seen'] = True
                            assert dialog.windowTitle() == '人脸预览'
                            toggle = dialog.findChild(QCheckBox)
                            spin = dialog.findChild(QSpinBox)
                            toggle.setChecked(enabled)
                            spin.setValue(ahead)
                            capture('05b-face-settings-' + suffix, dialog)
                            save = next(control for control in dialog.findChildren(QAbstractButton) if control.text() == '保存')
                            click(save)
                        except BaseException as error:
                            state['error'] = error
                            dialog.reject()
                    timer.timeout.connect(operate_face_settings)
                    timer.start()
                    click(action_button)
                    timer.stop()
                    if state['error']:
                        raise state['error']
                    check('face_settings_real_dialog_opened_' + suffix, state['seen'])
                    check('face_settings_save_applies_' + suffix, window.faces.enabled == enabled and window.faces.ahead == ahead)
                    check('face_settings_save_persists_' + suffix, settings.value('face_preview',type=bool) == enabled and settings.value('face_ahead',type=int) == ahead)
                window.faces.configure(False, 0)
                window.face_changed(current, None)

                # Actual culling hotkey: Right must mark left, never select the tab above.
                window.activateWindow()
                window.setFocus()
                QTest.qWait(80)
                QTest.keyClick(window, Qt.Key.Key_Right)
                until(lambda: window.pending_ops == 0 and window.queue and window.queue[0]['id'] != current)
                check('custom_right_shortcut_keeps_in_pending_view', window.view == 'pending' and window.library.photo(current)['status'] == 1)
                second = window.queue[0]['id']
                click(window.delete_button)
                until(lambda: window.pending_ops == 0 and window.queue and window.queue[0]['id'] != second)

                for mode, grid_name, image_number in (('keep', 'kept_grid', 6), ('deleted', 'deleted_grid', 8)):
                    window.switch(mode)
                    until(lambda: not window.fetching and getattr(window, grid_name).count() > 0)
                    grid = getattr(window, grid_name)
                    capture('%02d-%s-read-only-grid' % (image_number, mode), window)
                    test_readonly(window, mode + '_grid')
                    model_index = grid.model().index(0,0)
                    QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=grid.visualRect(model_index).center())
                    until(lambda: window.stack.currentWidget() is window.album and window.album.photo is not None)
                    capture('%02d-%s-single-photo-album' % (image_number+1, mode), window)
                    test_readonly(window, mode + '_single_view')
                    check(mode + '_single_view_has_one_photo', len(window.album.findChildren(type(window.photos[0]))) == 1)
                    QTest.keyClick(window.album.pane, Qt.Key.Key_Escape)
                    until(lambda: window.stack.currentIndex() == (4 if mode == 'keep' else 1))
                    window.show_menu()
                    check(mode + '_album_menu_has_no_undo', all('撤回' not in action.text() and '撤销' not in action.text() for action in window.more_panel.action_controls))
                    window.more_panel.hide()

                check('deleted_album_text_describes_only_viewing', '恢复' not in window.deleted_note.text() and '彻底删除' not in window.deleted_note.text())
                # Every source remains identical, including the moved deleted photo.
                for name, digest in originals.items():
                    with window.library.connect() as db:
                        row = db.execute('SELECT id FROM photos WHERE path=?', (str(photos / name),)).fetchone()
                    item = window.library.photo(row['id'], include_deleted=True)
                    source = Path(item['path'] if item['present'] else item['backup'])
                    check('source_pixels_unchanged_' + name, source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == digest)
            finally:
                window.busy = False
                window.prompt_on_close = False
                window.close()
                application.processEvents()
    report = {'ok': True, 'passed': True, 'platform': application.platformName(), 'checks': checks, 'screenshots': screenshots}
    (output / 'feature-check-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'passed': True, 'checks': len(checks), 'screenshots': len(screenshots), 'output': str(output)}))


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('feature-screenshots'))
    run(parser.parse_args().output)
