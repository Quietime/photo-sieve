"""Regressions found in the October 2026 review: each test names the defect it guards against."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import shutil,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from app import Library,LibraryError,TRASH_DIRECTORY
from cache_policy import MAX_BYTES
from photo_metadata import format_info
from rating_records import RatingRecords
from native_rating import RatingError,VALUES
from workflow_data import WorkRecords
from xmp_sync import sync_out,inspect,serialize
import desktop
from desktop import Window

application=QApplication.instance() or QApplication([])

def wait(library):
    end=time.monotonic()+10
    while library.job['running'] and time.monotonic()<end:time.sleep(.005)
    assert not library.job['running']

class Engine:
    def __init__(self):self.stars=0
    def run(self,path,stars=None,expected=None):
        before=self.stars
        if stars is not None:
            with open(path,'ab') as handle:handle.write(b'm')
            self.stars=stars
        return dict(stars=self.stars,value=VALUES[self.stars],before=before,writable=True,reason='')

class LibraryCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.photos=self.base/'photos';self.photos.mkdir()
        for name,color in (('a','red'),('b','green'),('c','blue')):Image.new('RGB',(40,30),color).save(self.photos/f'{name}.jpg')
        self.library=Library(self.base/'library');self.library.scan(self.photos);wait(self.library)
        self.ids={Path(row['path']).name:row['id'] for row in self.library.list_photos('all')}
    def tearDown(self):self.temp.cleanup()
    def rescan(self):self.library.scan(self.photos);wait(self.library)
    def trash_files(self):return sorted(p.name for p in (self.photos/TRASH_DIRECTORY).iterdir())

class RecopiedDeletionTest(LibraryCase):
    """A deleted photo copied back to its original path used to orphan its staged backup and block every older restore."""
    def recopy(self,name):
        copy=self.base/('copy-'+name);shutil.copy2(self.photos/name,copy)
        self.library.trash(self.ids[name]);shutil.copy2(copy,self.photos/name);self.rescan()

    def test_backup_stays_in_deleted_album_and_purge_all_removes_it(self):
        self.recopy('a.jpg')
        self.assertEqual([p['id'] for p in self.library.list_photos('deleted')],[self.ids['a.jpg']])
        self.assertEqual(self.library.state()['counts']['deleted'],1)
        self.library.purge_deleted(None);wait(self.library)
        self.assertEqual(self.library.job['errors'],[]);self.assertEqual(self.trash_files(),[])
        self.assertEqual(self.library.state()['counts']['deleted'],0)
        self.assertTrue((self.photos/'a.jpg').is_file(),'the copied-back file is not the staged backup')
        self.assertEqual(self.library.photo(self.ids['a.jpg'])['present'],1)

    def test_restore_latest_skips_the_conflict_and_restores_older_deletions(self):
        self.library.trash(self.ids['b.jpg']);self.recopy('a.jpg');self.library.trash(self.ids['c.jpg'])
        self.assertEqual(self.library.undo(only_delete=True)['id'],self.ids['c.jpg'])
        self.assertEqual(self.library.undo(only_delete=True)['id'],self.ids['b.jpg'])
        with self.assertRaises(LibraryError) as raised:self.library.undo(only_delete=True)
        self.assertEqual(raised.exception.status_code,404)
        # Restoring it on purpose still refuses to overwrite the file now at its path.
        with self.assertRaises(LibraryError) as raised:self.library.undo(only_delete=True,photo_id=self.ids['a.jpg'])
        self.assertEqual(raised.exception.status_code,409)
        self.assertEqual(len(self.trash_files()),1)

    def test_general_undo_and_can_undo_ignore_the_conflict(self):
        self.library.mark(self.ids['b.jpg'],1);self.recopy('a.jpg')
        self.assertTrue(self.library.state()['can_undo'])
        self.assertEqual(self.library.undo()['id'],self.ids['b.jpg'])
        self.assertFalse(self.library.state()['can_undo'])

    def test_rating_the_copied_back_file_does_not_retarget_the_staged_backup(self):
        self.recopy('a.jpg');records=RatingRecords(self.library)
        records.write(self.ids['a.jpg'],3,Engine())
        self.library.purge_deleted([self.ids['a.jpg']]);wait(self.library)
        self.assertEqual(self.library.job['errors'],[]);self.assertEqual(self.trash_files(),[])

class SessionLeaseTest(LibraryCase):
    def test_schema_setup_commits_inside_a_session(self):
        self.library.start_session()
        try:
            RatingRecords(self.library);WorkRecords(self.library)
            with self.library.connect() as db:
                db.executescript('CREATE TABLE IF NOT EXISTS probe(x)');db.execute('INSERT INTO probe VALUES (1)')
            with self.library.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM probe').fetchone()[0],1)
        finally:self.library.stop_session()

    def test_first_write_on_each_new_pooled_connection_commits(self):
        """A new pooled connection leased its unread journal_mode cursor, so its first write could not commit."""
        self.library.start_session();barrier=threading.Barrier(12);errors=[]
        def write(index):
            try:
                with self.library.connect() as db:
                    barrier.wait(5)   # all twelve leases at once: each one is a brand-new connection
                    db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(f'probe{index}',str(index)))
            except Exception as error:errors.append(repr(error))
        try:
            threads=[threading.Thread(target=write,args=(i,)) for i in range(12)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(10)
            self.assertEqual(errors,[])
            self.assertEqual([self.library.meta(f'probe{i}') for i in range(12)],[str(i) for i in range(12)])
            # The delete path: rename, then a second lease commits the record.
            row=self.library.list_photos('all')[0];self.library.trash(row['id'])
            self.assertEqual(self.library.state()['counts']['deleted'],1)
        finally:self.library.stop_session()

class LockOrderTest(LibraryCase):
    def test_trash_and_rating_writes_on_one_photo_never_deadlock(self):
        """trash() took the photo lock before the job lock; rating writes take them the other way round."""
        pid=self.ids['a.jpg'];records=RatingRecords(self.library);engine=Engine();stop=time.monotonic()+4;errors=[]
        def rate():
            stars=0
            while time.monotonic()<stop:
                stars=stars%5+1
                try:records.write(pid,stars,engine)
                except (LibraryError,RatingError,OSError):pass
                except Exception as error:errors.append(error)
        def trash():
            while time.monotonic()<stop:
                try:self.library.trash(pid);self.library.undo(only_delete=True,photo_id=pid)
                except LibraryError:pass
                except Exception as error:errors.append(error)
        threads=[threading.Thread(target=rate,daemon=True),threading.Thread(target=trash,daemon=True)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(20)
        self.assertFalse(any(thread.is_alive() for thread in threads),'trash and rating writes deadlocked')
        self.assertEqual(errors,[])
        self.assertEqual(self.library.photo(pid,include_deleted=True)['id'],pid)

class XmpPrefixTest(LibraryCase):
    SIDECAR='''<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" crs:Exposure2012="+0.50" photoshop:City="Singapore"/>
</rdf:RDF></x:xmpmeta>
<?xpacket end="w"?>'''
    def test_rewrite_keeps_prefixes_packet_and_every_property(self):
        pid=self.ids['a.jpg'];path=self.photos/'a.xmp';path.write_text(self.SIDECAR,encoding='utf-8')
        sync_out(WorkRecords(self.library),pid,stars=4,force=True)
        text=path.read_text('utf-8')
        self.assertIn('crs:Exposure2012="+0.50"',text);self.assertIn('photoshop:City="Singapore"',text)
        self.assertNotRegex(text,r'\bns\d+:')
        self.assertTrue(text.startswith('<?xpacket begin="﻿"'));self.assertTrue(text.rstrip().endswith('<?xpacket end="w"?>'))
        self.assertEqual(inspect(path)['stars'],4)
        sync_out(WorkRecords(self.library),pid,stars=2)
        self.assertEqual(inspect(path)['stars'],2);self.assertIn('crs:Exposure2012',path.read_text('utf-8'))

    def test_prefix_table_is_restored_and_concurrent_serialization_is_consistent(self):
        before=dict(ET._namespace_map);failures=[]
        def work(index):
            data=f'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:p{index}="urn:test:{index}"><p{index}:v/></x:xmpmeta>'.encode()
            for _ in range(200):
                text=serialize(ET.fromstring(data),data).decode()
                if f'p{index}:v' not in text or f'xmlns:p{index}="urn:test:{index}"' not in text:failures.append(text)
        threads=[threading.Thread(target=work,args=(i,)) for i in range(6)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(failures,[]);self.assertEqual(dict(ET._namespace_map),before)

    def test_new_sidecar_without_packet_is_plain_xml(self):
        pid=self.ids['b.jpg'];sync_out(WorkRecords(self.library),pid,stars=1)
        text=(self.photos/'b.xmp').read_text('utf-8')
        self.assertTrue(text.startswith("<?xml version='1.0' encoding='utf-8'?>"));self.assertEqual(inspect(self.photos/'b.xmp')['stars'],1)

class XmpFuzzTest(LibraryCase):
    def facts(self,data):
        """Every attribute and leaf text by namespace URI, independent of prefixes and layout."""
        root=ET.fromstring(data);found=set()
        for node in root.iter():
            for key,value in node.attrib.items():
                if key!='{http://ns.adobe.com/xap/1.0/}Rating':found.add(('@',node.tag,key,value))
            if node.text and node.text.strip():found.add(('#',node.tag,node.text.strip()))
        return found
    def test_random_sidecars_keep_every_property_and_prefix(self):
        import random
        rng=random.Random(1018);pid=self.ids['a.jpg'];path=self.photos/'a.xmp';records=WorkRecords(self.library)
        for case in range(150):
            spaces=[(f'{rng.choice("abcdefgh")}{rng.choice("xyz")}{i}',f'http://example.com/{case}/{i}/') for i in range(rng.randint(1,5))]
            if rng.random()<.5:spaces.append(('crs','http://ns.adobe.com/camera-raw-settings/1.0/'))
            declarations=' '.join(f'xmlns:{prefix}="{uri}"' for prefix,uri in spaces)
            attributes=' '.join(f'{prefix}:v{k}="{rng.choice(["1","+0.5","中文","a&amp;b","&lt;x&gt;"])}"' for prefix,_ in spaces for k in range(rng.randint(0,2)))
            children=''.join(f'<{prefix}:item>{rng.randint(0,99)}</{prefix}:item>' for prefix,_ in spaces if rng.random()<.6)
            rating=f' xmp:Rating="{rng.randint(0,5)}" xmlns:xmp="http://ns.adobe.com/xap/1.0/"' if rng.random()<.4 else ''
            body=(f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                  f'<rdf:Description rdf:about="" {declarations} {attributes}{rating}>{children}</rdf:Description></rdf:RDF></x:xmpmeta>')
            packet=rng.random()<.5
            text=('\ufeff' if packet and rng.random()<.3 else '')+(('<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'+body+'\n<?xpacket end="w"?>') if packet else body)
            path.write_bytes(text.encode('utf-8'));before=self.facts(path.read_bytes().lstrip(b'\xef\xbb\xbf'))
            stars=rng.randint(0,5);sync_out(records,pid,stars=stars,force=True)
            after=path.read_bytes()
            with self.subTest(case=case):
                self.assertEqual(self.facts(after),before);self.assertEqual(inspect(path)['stars'],stars)
                # A declaration nothing uses carries no data, and ElementTree drops it; used prefixes must survive.
                for prefix,uri in spaces:
                    if f'{prefix}:' in body.replace(f'xmlns:{prefix}=',''):self.assertIn(f'xmlns:{prefix}="{uri}"'.encode(),after)
                self.assertEqual(after.startswith(b'<?xpacket'),packet)

class ExposureTest(unittest.TestCase):
    def test_every_exposure_reads_back_close_to_the_real_value(self):
        from fractions import Fraction
        seconds=[1/8000*1.07**i for i in range(220)]+[1/n for n in range(1,8001,7)]
        for value in seconds:
            text=format_info(exposure=value)[:-1]
            shown=float(Fraction(text)) if '/' in text else float(text)
            with self.subTest(seconds=value,text=text):self.assertLess(abs(shown-value)/value,.15)

    def test_decimal_and_fractional_exposures_read_like_the_camera(self):
        cases={0.8:'0.8s',0.6:'0.6s',0.4:'0.4s',0.5:'1/2s',1/3:'1/3s',0.3:'1/3s',0.25:'1/4s',1/1.6:'0.6s',
               1/250:'1/250s',1/8000:'1/8000s',1:'1s',2.5:'2.5s',30:'30s'}
        for seconds,text in cases.items():
            with self.subTest(seconds=seconds):self.assertEqual(format_info(exposure=seconds),text)

class WindowCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.settings=QSettings(str(self.base/'preferences.ini'),QSettings.Format.IniFormat)
        self.settings.setValue('face_preview',False)
        self.patches=[patch(module+'.QSettings',return_value=self.settings) for module in ('desktop','rating_page','face_service','update_ui')]
        self.patches.append(patch('theme.accessibility_defaults',return_value=(False,False,False)))
        for p in self.patches:p.start()
        self.windows=[]
    def make_window(self,**options):
        window=Window(Library(self.base/('records'+str(len(self.windows)))),check_updates=False,start_empty=True,data_root=self.base/'workspaces',**options)
        window.resize(1280,900);window.show();self.windows.append(window);application.processEvents();return window
    def tearDown(self):
        for window in self.windows:
            window.prompt_on_close=False;window.uninstall_requested=False;window.close();application.processEvents();window.deleteLater();application.processEvents()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

class WindowRegressionTest(WindowCase):
    def test_refused_scan_leaves_the_rating_page_active(self):
        window=self.make_window();folder=self.base/'photos';folder.mkdir()
        window.toggle_rating();self.assertTrue(window.rating_page.active)
        window.pending_ops=1
        try:window.scan(str(folder))
        finally:window.pending_ops=0
        self.assertTrue(window.rating_page.active);self.assertFalse(window.importing)

    def test_uninstaller_failure_keeps_every_service_running(self):
        window=self.make_window();window.uninstall_requested=True
        with patch.object(desktop.subprocess,'Popen',side_effect=OSError('blocked')):window.close()
        application.processEvents()
        self.assertTrue(window.isVisible());self.assertFalse(window.closed);self.assertFalse(window.uninstall_requested)
        self.assertFalse(window.faces._closed);self.assertFalse(window.rating_page.engine.closed)
        self.assertFalse(window.metadata_service.stopped.is_set());self.assertFalse(window.analysis.stopped)
        self.assertTrue(window.timer is not None and window.maintenance_timer.isActive())

    def test_uninstaller_starts_before_shutdown_and_window_closes(self):
        window=self.make_window();window.uninstall_requested=True;order=[]
        real=window.faces.shutdown
        window.faces.shutdown=lambda:(order.append('shutdown'),real())
        with patch.object(desktop.subprocess,'Popen',side_effect=lambda *a,**k:order.append('uninstaller')):window.close()
        self.assertEqual(order,['uninstaller','shutdown']);self.assertTrue(window.closed)

    def test_first_rating_page_opens_in_a_live_session(self):
        """Session leases kept every cursor until after the commit; the first RatingRecords on a workspace failed."""
        folder=self.base/'photos';folder.mkdir()
        for i in range(3):Image.new('RGB',(40,30),'teal').save(folder/f'{i}.jpg')
        window=self.make_window();window.scan(str(folder))
        end=time.monotonic()+20
        while (window.importing or window.library.job['running']) and time.monotonic()<end:application.processEvents();time.sleep(.01)
        self.assertFalse(window.importing)
        window.toggle_rating();application.processEvents()
        self.assertTrue(window.rating_active);self.assertIsNotNone(window.rating_page.records)
        self.assertIsNotNone(window.rating_page.tools.records)

    def test_hidden_grading_page_does_not_size_other_pages(self):
        """The hidden grading page (689 px) used to force every page to a 790 px tall window."""
        window=self.make_window()
        for index in range(window.stack.count()):
            if window.stack.widget(index) is window.rating_page:continue
            window.stack.setCurrentIndex(index);application.processEvents()
            with self.subTest(page=type(window.stack.currentWidget()).__name__):
                self.assertLessEqual(window.minimumSizeHint().height(),720)

    def test_cache_budget_follows_the_launch_option(self):
        window=self.make_window(cache_mb=512)
        self.assertEqual(window.library.cache_limit,512*1024**2);self.assertEqual(window.cache_bytes_limit,512*1024**2)
        self.assertEqual(self.make_window(cache_mb=10**6).cache_bytes_limit,MAX_BYTES)
        self.assertEqual(self.make_window().library.cache_limit,MAX_BYTES)

if __name__=='__main__':unittest.main()
