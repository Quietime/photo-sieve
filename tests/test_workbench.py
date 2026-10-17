import hashlib,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageFilter,ImageDraw
from app import Library
from rating_records import RatingRecords
from tools.check_ratings import FakeEngine
from workflow_data import WorkRecords,signature,similar,captured_time
from xmp_sync import sync_out,sync_in,inspect,XmpConflict,XmpError
from face_quality import assess_face

class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.photos=self.root/'演唱会';self.photos.mkdir();self.lib=Library(self.root/'data');self.records=WorkRecords(self.lib);self.rating=RatingRecords(self.lib);self.engine=FakeEngine();self.ids=[]
        for i,color in enumerate(('navy','navy','red','red','white','black')):
            path=self.photos/f'{i:03}.jpg';image=Image.new('RGB',(640,400),color);ImageDraw.Draw(image).rectangle((10,20,130,200),fill='yellow');image.save(path)
            st=path.stat()
            with self.lib.connect() as db:self.ids.append(db.execute('INSERT INTO photos(path,size,mtime) VALUES(?,?,?)',(str(path),st.st_size,st.st_mtime_ns)).lastrowid)
        self.lib.remember('root',str(self.photos))
    def tearDown(self):self.tmp.cleanup()
    def feature(self,pid,when=0,color=None,quality=None):
        p=dict(self.lib.photo(pid))
        with Image.open(p['path']) as image:sig=signature(image)
        f=dict(signature=sig,captured=when,time_source='拍摄时间',quality=quality or {'state':'clear','text':'清楚'})
        self.records.save_feature(p,f);return f
    def test_label_and_annotation_undo_do_not_change_culling_or_ratings(self):
        pid=self.ids[0];self.lib.mark(pid,1);self.rating.write(pid,3,self.engine);self.records.annotate([pid],label='Blue',segment='第一首');self.records.undo()
        self.assertEqual(self.lib.photo(pid)['status'],1);self.assertEqual(self.rating.cached([pid])[pid]['stars'],3);self.assertEqual(self.records.annotations()[pid]['label'],'')
    def test_annotation_batch_rolls_back_if_one_photo_missing(self):
        with self.assertRaises(ValueError):self.records.annotate([self.ids[0],999999],label='Red')
        self.assertFalse(self.records.annotations())
    def test_label_validation_and_segment_length(self):
        for change in ({'label':'Unknown'},{'segment':'a'*121},{'untrusted':'bad'}):
            with self.assertRaises(ValueError):self.records.annotate(self.ids[:1],**change)
    def test_external_photo_change_invalidates_feature_join(self):
        pid=self.ids[0];self.feature(pid)
        with self.lib.connect() as db:db.execute('UPDATE photos SET size=size+1 WHERE id=?',(pid,))
        self.assertIsNone(self.records.inventory({pid})[0]['signature'])
    def test_feature_result_race_does_not_commit_stale_photo(self):
        pid=self.ids[0];p=dict(self.lib.photo(pid));f=self.feature(pid)
        with self.lib.connect() as db:db.execute('UPDATE photos SET present=0 WHERE id=?',(pid,))
        self.assertFalse(self.records.save_feature(p,f));self.assertFalse(self.records.inventory({pid}))
    def test_similar_adjacent_bursts_and_representative(self):
        for i,pid in enumerate(self.ids):self.feature(pid,when=i)
        self.assertEqual([len(g['ids']) for g in self.records.groups('similar')][:2],[2,2])
        a=self.feature(self.ids[0])['signature'];b=dict(a,colors=[min(1,v+.3) for v in a['colors']]);self.assertFalse(similar(a,b))
    def test_color_label_does_not_split_an_existing_time_or_similar_group(self):
        for pid in self.ids[:2]:self.feature(pid,when=1)
        before=self.records.groups('similar',ids=self.ids[:2]);self.records.annotate([self.ids[0]],label='Red')
        self.assertEqual(self.records.groups('similar',ids=self.ids[:2]),before)
    def test_time_gap_and_manual_song_names(self):
        for i,pid in enumerate(self.ids):self.feature(pid,when=i*10 if i<3 else 200+i*10)
        self.assertEqual([len(g['ids']) for g in self.records.groups('time',gap=90)],[3,3])
        self.records.annotate([self.ids[1]],segment='新歌');self.assertGreater(len(self.records.groups('time')),2)
    def test_manual_split_merge_unmerge_and_undo(self):
        for pid in self.ids[:2]:self.feature(pid,when=1)
        self.records.annotate([self.ids[1]],split_before=1);self.assertEqual(len(self.records.groups('similar',ids=self.ids[:2])),2)
        self.records.annotate(self.ids[:2],merge_key='manual');self.assertEqual(len(self.records.groups('similar',ids=self.ids[:2])),1)
        self.records.annotate(self.ids[:2],merge_key='');self.assertEqual(len(self.records.groups('similar',ids=self.ids[:2])),2)
        self.records.undo();self.assertEqual(self.records.annotations()[self.ids[1]]['merge_key'],'manual')
    def test_deleted_photos_not_in_groups_or_summary(self):
        self.feature(self.ids[0]);self.lib.trash(self.ids[0]);self.assertNotIn(self.ids[0],[pid for g in self.records.groups() for pid in g['ids']]);self.assertEqual(self.records.summary()['total'],5)
    def test_restore_preserves_user_labels(self):
        self.records.annotate([self.ids[0]],label='Green');self.lib.trash(self.ids[0]);self.lib.undo();self.assertEqual(self.records.annotations()[self.ids[0]]['label'],'Green')
    def test_bulk_annotation_undo_reverts_the_entire_action(self):
        self.records.annotate(self.ids,label='Purple',segment='song');self.records.undo()
        self.assertTrue(all(a['label']=='' and a['segment']=='' for a in self.records.annotations().values()));self.assertEqual(set(self.records.last_undo_labels),set(self.ids))
    def test_manual_merged_group_split_divides_suffix_without_overlapping(self):
        for pid in self.ids:self.feature(pid,when=1)
        self.records.annotate(self.ids,merge_key='one');self.records.split(self.ids[3],self.ids)
        self.assertEqual([g['ids'] for g in self.records.groups()], [self.ids[:3],self.ids[3:]])
        self.records.undo();self.assertEqual(len(self.records.groups()),1)
    def test_clear_derived_data_keeps_user_annotations(self):
        self.records.annotate([self.ids[0]],label='Red',segment='song');self.feature(self.ids[0])
        with self.lib.connect() as db:db.execute('DELETE FROM work_features')
        self.assertEqual(self.records.annotations()[self.ids[0]]['segment'],'song')
    def test_capture_exif_and_fallback_time(self):
        path=self.photos/'001.jpg';im=Image.open(path);exif=Image.Exif();exif[36867]='2024:06:29 15:45:08';im.save(self.root/'date.jpg',exif=exif);im.close()
        self.assertEqual(captured_time(self.root/'date.jpg',1)[1],'拍摄时间');self.assertEqual(captured_time(path,2_000_000_000),(2.0,'文件时间'))
    def test_standard_xmp_round_trip_preserves_original_image(self):
        pid=self.ids[0];source=Path(self.lib.photo(pid)['path']);old=hashlib.sha256(source.read_bytes()).hexdigest();r=sync_out(self.records,pid,4,'Blue');v=inspect(r['path']);self.assertEqual(v['stars'],4);self.assertEqual(v['label'],'Blue');self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),old)
        result=sync_in(self.records,pid,self.rating,self.engine);self.assertEqual(result['rating']['stars'],4);self.assertEqual(self.records.annotations()[pid]['label'],'Blue')
    def test_xmp_preserves_lightroom_adjustments_and_keywords(self):
        pid=self.ids[0];path=Path(self.lib.photo(pid)['path']).with_suffix('.xmp');path.write_text('<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"><rdf:RDF><rdf:Description crs:Exposure2012="1.25"><keywords>stage</keywords></rdf:Description></rdf:RDF></x:xmpmeta>')
        sync_out(self.records,pid,5,'Red');v=inspect(path);self.assertIn(b'Exposure2012="1.25"',v['data']);self.assertIn(b'<keywords>stage</keywords>',v['data'])
    def test_external_xmp_change_needs_explicit_resolution(self):
        pid=self.ids[0];r=sync_out(self.records,pid,2,'Green');path=Path(r['path']);path.write_bytes(path.read_bytes().replace(b'Rating="2"',b'Rating="5"'));old=path.read_bytes()
        with self.assertRaises(XmpConflict):sync_out(self.records,pid,3,'Green')
        self.assertEqual(path.read_bytes(),old);sync_out(self.records,pid,3,'Green',force=True);self.assertEqual(inspect(path)['stars'],3)
    def test_first_existing_different_xmp_rating_not_silently_overwritten(self):
        pid=self.ids[0];r=sync_out(self.records,pid,5,'Red')
        with self.lib.connect() as db:db.execute('DELETE FROM work_xmp')
        with self.assertRaises(XmpConflict):sync_out(self.records,pid,1,'Blue')
    def test_unknown_stars_does_not_clear_existing_rating(self):
        pid=self.ids[0];r=sync_out(self.records,pid,4,'Red');sync_out(self.records,pid,None,'Blue');self.assertEqual(inspect(r['path'])['stars'],4)
    def test_malformed_xmp_untouched(self):
        pid=self.ids[0];path=Path(self.lib.photo(pid)['path']).with_suffix('.xmp');path.write_bytes(b'broken')
        with self.assertRaises(XmpError):sync_out(self.records,pid,3)
        self.assertEqual(path.read_bytes(),b'broken')
    def test_xml_entities_and_oversize_rejected(self):
        path=self.root/'bad.xmp';path.write_text('<!DOCTYPE x [<!ENTITY x "boom">]><x/>')
        with self.assertRaises(XmpError):inspect(path)
        path.write_bytes('<?xml version="1.0" encoding="utf-16"?><!DOCTYPE x [<!ENTITY x "boom">]><x/>'.encode('utf-16'))
        with self.assertRaises(XmpError):inspect(path)
        path.write_bytes(b' '* (8*1024**2+1))
        with self.assertRaises(XmpError):inspect(path)
    def test_rejected_xmp_rating_does_not_delete_or_change_native_rating(self):
        pid=self.ids[0];r=sync_out(self.records,pid,3,'Red');path=Path(r['path']);path.write_bytes(path.read_bytes().replace(b'Rating="3"',b'Rating="-1"'))
        with self.assertRaises(XmpConflict):sync_in(self.records,pid,self.rating,self.engine)
        self.assertTrue(self.lib.photo(pid)['present']);self.assertEqual(self.engine.run(self.lib.photo(pid)['path'])['stars'],0)
    def test_same_stem_raw_jpeg_sidecar_collision_is_explicit(self):
        source=Path(self.lib.photo(self.ids[0])['path']);path=source.with_suffix('.arw');path.write_bytes(b'raw');st=path.stat()
        with self.lib.connect() as db:db.execute('INSERT INTO photos(path,size,mtime) VALUES(?,?,?)',(str(path),st.st_size,st.st_mtime_ns))
        with self.assertRaises(XmpConflict):sync_out(self.records,self.ids[0],5)
    def test_sync_failures_persist_until_successful_resolution(self):
        pid=self.ids[0]
        with patch('xmp_sync.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):sync_out(self.records,pid,4)
        with self.lib.connect() as db:self.assertIn('disk full',db.execute('SELECT error FROM work_xmp WHERE photo_id=?',(pid,)).fetchone()[0])
        sync_out(self.records,pid,4)
        with self.lib.connect() as db:self.assertEqual(db.execute('SELECT error FROM work_xmp WHERE photo_id=?',(pid,)).fetchone()[0],'')
    def test_source_change_blocks_xmp_write(self):
        path=Path(self.lib.photo(self.ids[0])['path']);path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises(XmpError):sync_out(self.records,self.ids[0],3)
    def test_symlink_sidecar_not_followed(self):
        if os.name=='nt':self.skipTest('Windows runner link privileges vary')
        path=Path(self.lib.photo(self.ids[0])['path']).with_suffix('.xmp');target=self.root/'target';target.write_bytes(b'untouched');path.symlink_to(target)
        with self.assertRaises(XmpError):sync_out(self.records,self.ids[0],5)
        self.assertEqual(target.read_bytes(),b'untouched')
    def test_failed_atomic_replace_preserves_existing_sidecar(self):
        pid=self.ids[0];r=sync_out(self.records,pid,2);path=Path(r['path']);old=path.read_bytes()
        with patch('xmp_sync.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):sync_out(self.records,pid,5)
        self.assertEqual(path.read_bytes(),old);self.assertFalse(list(path.parent.glob('*.tmp')))
    def test_analysis_signatures_bound_and_orientation_aware(self):
        a=signature(Image.new('RGB',(800,400),'red'));b=signature(Image.new('RGB',(400,800),'red'));self.assertFalse(similar(a,b));self.assertEqual(len(a['colors']),12)
    def test_quality_distinguishes_blur_and_declines_tiny_faces(self):
        image=Image.new('RGB',(256,256),'white');d=ImageDraw.Draw(image)
        for x in range(0,256,4):d.line((x,0,x,256),fill='black',width=2)
        sharp=assess_face(image);blur=assess_face(image.filter(ImageFilter.GaussianBlur(9)));self.assertGreater(sharp['sharpness'],blur['sharpness']);self.assertEqual(blur['state'],'review');self.assertEqual(assess_face(Image.new('RGB',(30,30)))['state'],'uncertain')
    def test_missing_eye_classifier_is_unknown_not_closed(self):
        with patch('face_quality.EYE_MODEL',self.root/'missing.xml'):self.assertEqual(assess_face(Image.effect_noise((256,256),100).convert('RGB'))['eyes'],'unknown')
    def test_group_choice_model_keeps_all_16000_groups_without_per_item_widgets(self):
        from workbench import GroupChoiceModel
        from PySide6.QtCore import Qt
        groups=[dict(key=str(i),title='组 '+str(i),ids=[i]) for i in range(16000)];model=GroupChoiceModel(groups)
        self.assertEqual(model.rowCount(),16001);self.assertEqual(model.data(model.index(16000,0),Qt.ItemDataRole.UserRole),'15999');self.assertEqual(model.positions['15999'],16000)
    def test_16000_inventory_grouping_is_bounded_and_complete(self):
        f=self.feature(self.ids[0]);sig=json.dumps(f['signature']);t=time.monotonic()
        with self.lib.connect() as db:
            db.executemany('INSERT INTO photos(id,path,size,mtime) VALUES(?,?,?,?)',((100+i,str(self.photos/f'bulk{i}.arw'),123,1000000000+i) for i in range(16000)))
            db.executemany('INSERT INTO work_features VALUES(?,?,?,?,?,?,?,?)',((100+i,123,1000000000+i,sig,float(i),'拍摄时间','{}',1) for i in range(16000)))
        groups=self.records.groups('similar');self.assertEqual(len({pid for g in groups for pid in g['ids']}),16006);self.assertLess(time.monotonic()-t,12)

if __name__=='__main__':unittest.main()
