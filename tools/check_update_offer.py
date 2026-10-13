"""Only the changed update offer: layout, notes, details, accept/decline and keyboard."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
if not getattr(sys,'frozen',False):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt,QTimer,QPoint
from PySide6.QtWidgets import QApplication,QWidget,QLabel
from PySide6.QtTest import QTest
from update_offer import UpdateOfferDialog,highlights
from update_ui import UpdateController
from theme import STYLE

def run(output):
    application=QApplication.instance() or QApplication([]);application.setStyleSheet(STYLE)
    output=Path(output);output.mkdir(parents=True,exist_ok=True);checks=[]
    notes='照片快筛 4.1.4\n- 补齐 Windows 任务栏窗口身份和图标资源。\n- 更新助手独立运行，重启使用全新运行环境，确认窗口持续存活。\n- Windows 发布测试覆盖真实 GUI 加载 RAW。\n- 启动时自动检查更新，发现最新版立即提醒。'
    release={'version':'4.1.5','notes':notes}
    dialog=UpdateOfferDialog(release,current_version='4.1.4');dialog.show();application.processEvents()
    assert len(highlights(notes))==3 and not any('GUI' in line for line in highlights(notes));checks.append('user_facing_highlights')
    assert dialog.windowFlags()&Qt.WindowType.FramelessWindowHint and not dialog.mask().contains(QPoint(0,0));checks.append('rounded_frameless')
    assert not dialog.details.isVisible();checks.append('details_collapsed_by_default')
    dialog.grab().save(str(output/'01_update_offer.png'))
    QTest.mouseClick(dialog.more,Qt.MouseButton.LeftButton);application.processEvents()
    assert dialog.details.isVisible() and dialog.details.toPlainText()==notes;checks.append('full_notes_expand')
    dialog.grab().save(str(output/'02_update_details.png'))
    QTest.mouseClick(dialog.more,Qt.MouseButton.LeftButton);application.processEvents()
    assert not dialog.details.isVisible();checks.append('full_notes_collapse');dialog.close()
    long=UpdateOfferDialog({'version':'99.0.0','notes':'\n'.join('- '+('测试长更新内容，保持完整换行。'*8) for _ in range(8))});long.show();application.processEvents()
    assert long.accept_button.isVisible() and long.height()<application.primaryScreen().availableGeometry().height();checks.append('long_content_keeps_actions_visible')
    long.grab().save(str(output/'03_long_notes.png'));long.close()
    parent=QWidget();parent.show();controller=SimpleNamespace(window=parent)
    for name,action,accepted in [('later',lambda d:QTest.mouseClick(d.later,Qt.MouseButton.LeftButton),False),
                                  ('accept',lambda d:QTest.mouseClick(d.accept_button,Qt.MouseButton.LeftButton),True),
                                  ('escape',lambda d:QTest.keyClick(d,Qt.Key.Key_Escape),False),
                                  ('enter',lambda d:QTest.keyClick(d,Qt.Key.Key_Return),True),
                                  ('close',lambda d:d.close(),False)]:
        QTimer.singleShot(80,lambda action=action:action(application.activeModalWidget()))
        assert UpdateController.ask(controller,release)==accepted,name;checks.append(name+'_result')
    parent.close();(output/'report.json').write_text(json.dumps({'ok':True,'scope':'update offer only','checks':checks},ensure_ascii=False,indent=2))
    print('PASS: update offer only:',len(checks),'checks and 3 screenshots')
    return 0

if __name__=='__main__':raise SystemExit(run(sys.argv[1] if len(sys.argv)>1 else 'update-offer-ci'))
