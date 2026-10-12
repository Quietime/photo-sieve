"""Developer packaging check; uses temporary photos, never the user's library."""
import json
from pathlib import Path
import sys
import tempfile
import time

def run(application, output):
    from PIL import Image
    from PySide6.QtCore import qVersion
    from desktop import Window, Decoders
    from app import Library
    import rawpy
    import pillow_heif
    result={'platform':sys.platform,'frozen':bool(getattr(sys,'frozen',False)),'qt':qVersion(),'interface':'Qt Widgets','checks':[]}
    window=None
    def wait(fn):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            application.processEvents()
            if fn():return
            time.sleep(.01)
        raise TimeoutError('packaging check timed out')
    with tempfile.TemporaryDirectory() as temp:
        try:
            base=Path(temp);photos=base/'photos';photos.mkdir()
            for i in range(8):Image.new('RGB',(2400,1400),(i*30,100,50)).save(photos/f'{i:02}.jpg')
            heif=pillow_heif.from_pillow(Image.new('RGB',(900,600),'blue'));heif.save(photos/'heic.heic')
            lib=Library(base/'data');lib.scan(str(photos));wait(lambda:not lib.job['running'])
            rows=lib.list_photos('pending');assert len(rows)==9
            assert lib.decode(photos/'heic.heic',320).size==(320,213)
            window=Window(lib,check_updates=False,decoders=Decoders(2));window.show();wait(lambda:window.keep_button.isEnabled())
            assert not window.windowIcon().isNull()
            result['checks'].append('application icon loaded from packaged resources')
            assert window.photos[0].pixmap and window.photos[1].photo
            result['checks'].append('native dual-image UI, HEIC, decoder processes')
            original=window.queue[0];original_bytes=Path(original['path']).read_bytes()
            window.act('delete');wait(lambda:not window.busy and lib.state()['counts']['deleted']==1)
            assert lib.preview(original['id'],320)
            wait(lambda:window.keep_button.isEnabled());kept=window.queue[0];window.act('keep');wait(lambda:not window.busy)
            window.restore();wait(lambda:not window.busy and lib.state()['counts']['deleted']==0)
            assert Path(original['path']).read_bytes()==original_bytes and lib.photo(kept['id'])['status']==1
            result['checks'].append('delete, keep, deletion-only restore, original bytes')
            # The library records the restore before the window puts the photo back in front; zoom only once it is there.
            wait(lambda:window.queue and window.queue[0]['id']==original['id'] and window.keep_button.isEnabled())
            window.focus(original);wait(lambda:window.photos[0].full_resolution)
            assert window.photos[0].pixmap.width()==2400;window.reset_zoom()
            assert not window.photos[0].detail
            result['checks'].append('in-place full-resolution zoom and restore')
            wait(lambda:all(window.strip.item(i).data(256) in window.thumb_cache for i in window.strip.visible_rows()))
            result['checks'].append('all filmstrip thumbnails, not only first six')
            lib.export_kept(str(base/'copy'),'copy');wait(lambda:not lib.job['running'])
            assert lib.export_summary()['done']==1 and Path(kept['path']).is_file()
            lib.export_kept(str(base/'move'),'move');wait(lambda:not lib.job['running'])
            assert lib.export_summary()['done']==1 and not Path(kept['path']).exists()
            result['checks'].append('copy and Windows file move')
            result['checks'].append('RAW decoder DLL import')
            window.close();window=None;result['ok']=True
        except Exception as exc:
            import traceback
            result.update(ok=False,error=str(exc),traceback=traceback.format_exc())
            if window:   # closed while its temporary library still exists
                window.busy=False;window.library.job['running']=False;window.close()
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['ok'] else 1
