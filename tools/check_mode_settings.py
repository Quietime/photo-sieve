"""Capture distinct review/grading menus and test independent dock switches."""
import json,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from desktop import Window,STYLE
from app import Library

def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE)
    def until(test):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            application.processEvents()
            if test():return
            time.sleep(.005)
        raise AssertionError('Mode settings did not finish')
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);photos=base/'photos';photos.mkdir();settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat)
        settings.setValue('face_preview',False);settings.setValue('warm_count',0)
        for i in range(3):Image.new('RGB',(600,900),'#668092').save(photos/f'{i}.jpg')
        with patch('desktop.QSettings',return_value=settings),patch('rating_page.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            window=Window(Library(base/'workspace'),check_updates=False,start_empty=True,data_root=base)
            try:
                window.resize(1280,900);window.show();window.scan(photos);until(lambda:len(window.queue)==3 and window.ready() and not window.busy)
                def menu(name):
                    window.show_menu();application.processEvents();window.more_panel.grab().save(str(output/(name+'.png')))
                    return list(window.more_panel.action_controls)
                def dock(actions):return next(a for a in actions if a.text()=='显示底部操作栏')
                review=menu('review-settings');assert any(a.text()=='筛选快捷键…' for a in review)
                assert not dock(review).isChecked() and window.action_row.isHidden()   # the dock is opt-in since 5.0
                dock(review).setChecked(True);dock(review).setChecked(False);window.more_panel.hide()
                window.toggle_rating();until(lambda:window.rating_active and bool(window.rating_page.ids))
                assert not window.rating_actionbar_visible and window.rating_page.action_row.isHidden()
                rating=menu('rating-settings-default');dock(rating).setChecked(True);window.more_panel.hide()
                assert window.rating_actionbar_visible and not window.rating_page.action_row.isHidden()
                rating=menu('rating-settings');names=[a.text() for a in rating]
                assert '分级快捷键…' in names and '人脸预览…' not in names and '预加载与缓存…' not in names
                dock(rating).setChecked(False);window.more_panel.hide();window.toggle_rating();until(lambda:not window.rating_active and not window.fetching)
                review=menu('review-settings-saved');assert not dock(review).isChecked();dock(review).setChecked(True);window.more_panel.hide()
                assert window.actionbar_visible and not window.rating_actionbar_visible
                assert settings.value('review/show_actionbar',type=bool) and not settings.value('rating/show_actionbar',type=bool)
                report={'ok':True,'checks':['review_and_rating_menus_are_mode_specific','review_dock_change_does_not_hide_rating_dock','rating_dock_change_does_not_change_review_preference','mode_preferences_are_saved_separately']}
            finally:window.prompt_on_close=False;window.close();application.processEvents()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print('PASS independent mode settings')

if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support();run(sys.argv[1])
