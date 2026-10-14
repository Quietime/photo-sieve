"""Real Sony ARW regression: all star tags change, original RAW pixels do not."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import rawpy
from embedded_rating import read,write
from native_rating import operate


def verify(source,output):
    source=Path(source).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    original_digest=hashlib.sha256(source.read_bytes()).hexdigest()
    with rawpy.imread(str(source)) as raw:
        raw_shape=list(raw.raw_image.shape)
        pixel_digest=hashlib.sha256(raw.raw_image.tobytes()).hexdigest()
    operations=[]
    with tempfile.TemporaryDirectory(prefix='arw-regression-',dir=output) as directory:
        candidate=Path(directory)/'Sony.ARW';shutil.copy2(source,candidate)
        for stars in (1,3,5,0):
            result=operate(candidate,stars) if os.name=='nt' else write(candidate,stars)
            embedded=read(candidate)
            assert embedded['stars']==stars,(stars,embedded)
            assert result['before']==(0 if stars==1 else 1 if stars==3 else 3 if stars==5 else 5),result
            with rawpy.imread(str(candidate)) as raw:
                assert hashlib.sha256(raw.raw_image.tobytes()).hexdigest()==pixel_digest,'RAW sensor data changed'
            if os.name=='nt':
                assert result['backend']=='embedded',result
                assert result['windows_visible'] in (True,False,None),result
            operations.append({'stars':stars,'tags':embedded['tags'],'backend':result.get('backend','embedded'),'windows_visible':result.get('windows_visible')})
        # Undo must detect external changes rather than overwrite them.
        try:write(candidate,2,expected=3)
        except Exception as exc:assert '外部修改' in str(exc),str(exc)
        else:raise AssertionError('Conflicting undo was accepted')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original_digest,'Source fixture changed'
    report={'source_sha256':original_digest,'sensor_shape':raw_shape,'sensor_sha256':pixel_digest,'operations':operations,'checks':10}
    (output/'arw-rating-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('PASS real ARW stars 1/3/5/0; sensor pixels unchanged; external-star undo protected')
    return report


if __name__=='__main__':verify(sys.argv[1],sys.argv[2])
