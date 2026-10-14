"""Focused real-click/key checks and screenshots for the close offer and rating selectors."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QWidget
from close_offer import CloseOfferDialog
from rating_combo import RoundedCombo
import theme


def run(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setStyleSheet(theme.STYLE)
    application.setQuitOnLastWindowClosed(False)
    screenshots = []
    checks = []

    def capture(name, widget):
        QTest.qWait(100)
        path = output / (name + '.png')
        assert widget.grab().save(str(path))
        screenshots.append(str(path))

    def check(name, condition):
        assert condition, name
        checks.append(name)

    def click(widget):
        QTest.mouseMove(widget, widget.rect().center())
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())
        QTest.qWait(30)

    def offer():
        dialog = CloseOfferDialog()
        dialog.show()
        QTest.qWait(50)
        return dialog

    dialog = offer()
    capture('01-close-offer', dialog)
    check('close_offer_is_rounded', not dialog.mask().contains(QPoint(4, 4)))
    check('keeping_progress_is_primary_default', dialog.keep_button.isDefault() and dialog.keep_button.variant == 'primary')
    check('forget_is_not_default_or_auto_default', not dialog.forget_button.isDefault() and not dialog.forget_button.autoDefault())
    check('forget_text_is_not_bold', not dialog.forget_button.font().bold())
    click(dialog.keep_button)
    check('click_keep_returns_keep', dialog.choice == 'keep' and not dialog.isVisible())

    dialog = offer()
    click(dialog.forget_button)
    check('click_forget_returns_forget', dialog.choice == 'forget' and not dialog.isVisible())
    dialog = offer()
    click(dialog.cancel_button)
    check('click_cancel_returns_none', dialog.choice is None and not dialog.isVisible())
    dialog = offer()
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    check('enter_keeps_progress', dialog.choice == 'keep' and not dialog.isVisible())
    dialog = offer()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    check('escape_cancels_close', dialog.choice is None and not dialog.isVisible())
    dialog = offer()
    close_button = next(button for button in dialog.chrome.findChildren(QPushButton)
        if button.accessibleName() == '关闭')
    click(close_button)
    check('titlebar_close_cancels', dialog.choice is None and not dialog.isVisible())
    dialog = offer()
    dialog.forget_button.setFocus()
    QTest.keyClick(dialog.forget_button, Qt.Key.Key_Return)
    check('forget_does_not_capture_default_enter', dialog.choice != 'forget')
    if dialog.isVisible():
        dialog.reject()

    host = QWidget()
    host.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    host.setWindowTitle('照片分级 · 控件检查')
    host.resize(660, 180)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.addWidget(QLabel('照片分级'))
    row = QHBoxLayout()
    scope = RoundedCombo()
    scope.addItems(['全部照片', '保留照片'])
    scope.setAccessibleName('照片范围')
    stars = RoundedCombo()
    stars.addItems(['全部星级', '未评级（0 星）'] + [f'{i} 星' for i in range(1, 6)])
    stars.setAccessibleName('星级筛选')
    row.addWidget(scope)
    row.addWidget(stars)
    row.addStretch()
    layout.addLayout(row)
    layout.addStretch()
    host.show()
    host.activateWindow()
    capture('02-rating-rounded-selectors', host)
    control_pixel = scope.grab().toImage().pixelColor(100, scope.height() // 2)
    check('selector_surface_is_neutral_gray', max(control_pixel.red(), control_pixel.green(), control_pixel.blue()) - min(control_pixel.red(), control_pixel.green(), control_pixel.blue()) < 20)
    changes = QSignalSpy(scope.currentIndexChanged)
    click(scope)
    check('scope_mouse_opens_popup', scope.view().window().isVisible())
    capture('03-scope-popup', scope.view().window())
    check('scope_popup_has_round_mask', not scope.view().window().mask().contains(QPoint(0, 0)))
    QTest.qWait(250)  # Qt deliberately ignores an immediate release after the opening press.
    index = scope.model().index(1, 0)
    QTest.mouseMove(scope.view().viewport(), scope.view().visualRect(index).center())
    QTest.qWait(30)
    QTest.mouseClick(scope.view().viewport(), Qt.MouseButton.LeftButton,
        pos=scope.view().visualRect(index).center())
    QTest.qWait(50)
    check('scope_popup_click_changes_standard_index', scope.currentIndex() == 1 and changes.count() == 1)
    check('scope_popup_closes_after_selection', not scope.view().window().isVisible())

    stars.setFocus()
    QTest.keyClick(stars, Qt.Key.Key_Down)
    check('closed_combo_keyboard_changes_index', stars.currentIndex() == 1)
    QTest.keyClick(stars, Qt.Key.Key_Space)
    QTest.qWait(50)
    check('keyboard_opens_star_popup', stars.view().window().isVisible())
    capture('04-star-filter-popup', stars.view().window())
    QTest.keyClick(application.focusWidget() or stars.view(), Qt.Key.Key_End)
    QTest.keyClick(application.focusWidget() or stars.view(), Qt.Key.Key_Return)
    QTest.qWait(50)
    check('popup_keyboard_selects_five_stars', stars.currentIndex() == 6)
    check('keyboard_selection_closes_popup', not stars.view().window().isVisible())

    click(stars)
    QTest.keyClick(application.focusWidget() or stars.view(), Qt.Key.Key_Escape)
    QTest.qWait(50)
    check('escape_closes_popup_without_changing_value', stars.currentIndex() == 6 and not stars.view().window().isVisible())
    stars.setItemData(6, 99)
    check('standard_item_data_api_preserved', stars.currentData() == 99)
    scope.addItem('这是一段很长的照片范围名称，用来检查文字不侵入右侧下拉箭头')
    scope.setFixedWidth(184)
    scope.setCurrentIndex(2)
    capture('05-long-label-clipped-before-arrow', host)
    stars.setEnabled(False)
    capture('06-disabled-selector', host)
    host.close()
    report = {'passed': True, 'platform': application.platformName(), 'checks': checks,
        'screenshots': screenshots, 'scope': 'No photos, settings, or filesystem metadata are modified.'}
    (output / 'small-dialogs-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'passed': True, 'checks': len(checks), 'screenshots': len(screenshots), 'output': str(output)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('small-dialog-screenshots'))
    run(parser.parse_args().output)
