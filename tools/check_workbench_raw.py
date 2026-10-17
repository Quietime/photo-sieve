"""Real ARW/XMP/cache/culling integration. All source pixels must remain unchanged."""
import hashlib,io,json,os,shutil,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import rawpy
from app import Library
from workflow_data import WorkRecords,signature
from rating_records import RatingRecords
from native_rating import value_from_stars
from xmp_sync import sync_out,sync_in,inspect
class LocalArwEngine:
    def run(self,path,stars=None,expected=None):
        from embedded_rating import read,write
        value=read(path) if stars is None else write(path,stars,expected)
        return dict(value,value=value_from_stars(value['stars']),writable=True,backend='embedded',windows_visible=False)
    def close(self):pass

def verify(source,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);checks=[];source=Path(source);original=hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);folder=root/'原片';folder.mkdir();path=folder/'Sony.ARW';shutil.copy2(source,path);lib=Library(root/'data');lib.scan(folder)
        deadline=time.monotonic()+30
        while lib.job['running']:
            if time.monotonic()>deadline:raise RuntimeError('ARW scan timeout')
            time.sleep(.01)
        records=WorkRecords(lib);rating=RatingRecords(lib);pid=lib.list_photos('all')[0]['id'];before=dict(lib.photo(pid));mtime=path.stat().st_mtime_ns
        from rating_engine import RatingEngine
        engine=RatingEngine(root/'logs') if os.name=='nt' else LocalArwEngine()
        try:
            with rawpy.imread(str(path)) as raw:pixels=hashlib.sha256(raw.raw_image.tobytes()).hexdigest()
            lib.mark(pid,1);records.annotate([pid],label='Blue',segment='encore')
            im=lib.preview_image(pid,1600);feature=dict(signature=signature(im),captured=100,time_source='拍摄时间',quality={'text':'清楚'});im.close();records.save_feature(before,feature)
            # Seed an actual indexed derived face crop, then verify native metadata changes retain it.
            from face_service import SCHEMA,FaceService,cache_key,cache_path
            with lib.connect() as db:db.execute(SCHEMA);db.execute('INSERT INTO face_metadata VALUES(?,?,?,?,?,?,?)',(cache_key(before),before['path'],before['size'],before['mtime'],'yunet-2023mar-native-v1','[]','{}'))
            buffer=io.BytesIO();Image.new('RGB',(128,128),'blue').save(buffer,'PNG');FaceService._save_crop(None,lib,before,buffer.getvalue());old_preview=lib.read_preview_cache(path,before['size'],before['mtime'],1600)
            result=rating.write(pid,5,engine);sync_out(records,pid,5,'Blue');assert result['stars']==5 and inspect(path.with_suffix('.xmp'))['stars']==5
            assert records.summary()['features']==1 and records.annotations()[pid]['segment']=='encore'
            current=dict(lib.photo(pid));assert cache_path(lib,current).is_file()
            with patch.object(lib,'decode',side_effect=AssertionError('Metadata-only rating must retain pixels cache')):
                im=lib.preview_image(pid,1600);im.close()
            assert lib.read_preview_cache(path,current['size'],current['mtime'],1600)==old_preview
            checks.append('real_arw_native_rating_xmp_and_pixel_face_feature_caches_remain_consistent')
            xmp=path.with_suffix('.xmp');xmp.write_bytes(xmp.read_bytes().replace(b'Rating="5"',b'Rating="2"'));value=sync_in(records,pid,rating,engine);assert value['rating']['stars']==2
            assert lib.photo(pid)['status']==1 and path.stat().st_mtime_ns==mtime
            checks.append('external_xmp_import_updates_real_arw_stars_without_changing_keep_or_file_date')
            undone=rating.undo(engine);sync_out(records,pid,undone['stars']);assert engine.run(path)['stars']==5
            lib.trash(pid);assert not path.exists();lib.undo();assert path.exists() and engine.run(path)['stars']==5 and records.annotations()[pid]['label']=='Blue'
            checks.append('rating_undo_and_trash_restore_preserve_real_arw_stars_and_user_annotations')
            with rawpy.imread(str(path)) as raw:assert hashlib.sha256(raw.raw_image.tobytes()).hexdigest()==pixels
            assert hashlib.sha256(source.read_bytes()).hexdigest()==original
            checks.append('all_real_raw_sensor_pixels_and_external_fixture_remain_unchanged')
        finally:engine.close()
    report=dict(ok=True,native_windows=os.name=='nt',checks=checks);(output/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
if __name__=='__main__':verify(sys.argv[1],sys.argv[2])
