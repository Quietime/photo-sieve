import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile,time,unittest
from pathlib import Path
from app import Library

class NavigationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.lib=Library(Path(self.temp.name))
        with self.lib.connect() as db:
            db.executemany('INSERT INTO photos(id,path,size,mtime,status) VALUES (?,?,1,?,?)',[(pid,str(self.lib.directory/name),stamp,status) for pid,name,stamp,status in [(1,'a',300,0),(2,'b',100,0),(3,'c',200,0),(4,'d',200,0),(5,'e',400,1)]])
    def tearDown(self):self.temp.cleanup()
    def test_date_sort_with_equal_timestamps_is_stable_across_pages(self):
        for sort,want in [('oldest',[2,3,4,1]),('newest',[1,4,3,2])]:
            self.assertEqual(self.lib.list_ids('pending',sort),want)
            page=self.lib.list_photos('pending',sort=sort,limit=2)
            nextpage=self.lib.list_photos('pending',page[-1]['id'],limit=2,sort=sort)
            self.assertEqual([p['id'] for p in page+nextpage],want)
            jumped=self.lib.list_photos('pending',want[2],sort=sort,inclusive=True)
            self.assertEqual(jumped[0]['id'],want[2])
    def test_date_range_and_keep_exclusion_apply_to_the_entire_inventory(self):
        self.assertEqual(self.lib.list_ids('pending','oldest',100,300),[2,3,4])
        self.assertEqual(self.lib.list_ids('keep'),[])
        self.assertEqual([p['id'] for p in self.lib.list_photos('keep',start_ns=300,end_ns=500)],[5])

    def test_virtual_strip_can_jump_to_last_of_100000_rows_without_all_thumbnails(self):
        from PySide6.QtWidgets import QApplication
        from filmstrip import Filmstrip
        application=QApplication.instance() or QApplication([])
        strip=Filmstrip();lookups=[]
        def icon_for(pid):
            from PySide6.QtGui import QIcon
            lookups.append(pid);return QIcon()
        strip.icon_for=icon_for;strip.resize(1000,100);strip.set_ids(list(range(100000)));strip.show();application.processEvents()
        self.assertEqual(strip.count(),100000)
        bar=strip.horizontalScrollBar();self.assertGreater(bar.maximum(),0)
        bar.setValue(bar.maximum());application.processEvents()
        self.assertIn(99999,strip.visible_ids());self.assertLess(len(strip.visible_rows()),20)
        clicked=[];strip.itemClicked.connect(lambda item:clicked.append(item.data(256)))
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        QTest.mouseClick(strip.viewport(),Qt.MouseButton.LeftButton,pos=strip.visualItemRect(strip.item(99999)).center())
        self.assertEqual(clicked,[99999]);self.assertLess(len(lookups),500);strip.close();strip.deleteLater();application.processEvents()
