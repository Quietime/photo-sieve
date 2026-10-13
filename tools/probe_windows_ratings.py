"""Probe actual Windows handlers on disposable image files; never touches user photos."""
import hashlib,json,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from native_rating import operate

from tools.raw_fixture import write_raw
import rawpy
results=[]
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary)
    for extension,format in [('jpg','JPEG'),('tif','TIFF'),('png','PNG'),('bmp','BMP'),('webp','WEBP'),('arw','TIFF'),('dng','TIFF')]:
        path=root/('test.'+extension);Image.new('RGB',(80,60),(40,80,140)).save(path,format=format)
        digest=hashlib.sha256(Image.open(path).tobytes()).hexdigest()
        record={'extension':extension,'fixture':format}
        try:
            record['initial']=operate(path)
            if record['initial']['writable']:
                record['roundtrips']=[]
                for stars in [1,2,3,4,5,0]:
                    written=operate(path,stars);fresh=operate(path)
                    assert fresh['stars']==stars,(extension,stars,fresh)
                    assert hashlib.sha256(Image.open(path).tobytes()).hexdigest()==digest,'Pixels changed'
                    record['roundtrips'].append(stars)
        except Exception as exc:record['error']=repr(exc)
        results.append(record)
        print(json.dumps(record,ensure_ascii=False),flush=True)
    for extension in ['dng','arw']:
        path=root/('sensor.'+extension);write_raw(path)
        with rawpy.imread(str(path)) as raw:digest=hashlib.sha256(raw.raw_image_visible.tobytes()).hexdigest()
        record={'extension':extension,'fixture':'generated CFA DNG'}
        try:
            record['initial']=operate(path)
            if record['initial']['writable']:
                record['roundtrips']=[]
                for stars in [1,5,0]:
                    operate(path,stars);fresh=operate(path)
                    assert fresh['stars']==stars
                    with rawpy.imread(str(path)) as raw:assert hashlib.sha256(raw.raw_image_visible.tobytes()).hexdigest()==digest,'RAW sensor pixels changed'
                    record['roundtrips'].append(stars)
        except Exception as exc:record['error']=repr(exc)
        results.append(record);print(json.dumps(record,ensure_ascii=False),flush=True)
Path('windows-rating-probe.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
assert next(record for record in results if record['extension']=='jpg').get('roundtrips')==[1,2,3,4,5,0], 'JPEG must support a real Windows metadata roundtrip'
