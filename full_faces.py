"""Whole-folder background face recognition; foreground navigation only reads results."""
from full_cache import FullCache
from face_service import cache_key

class FullFaces(FullCache):
    view='pending'
    def __init__(self,host,**kwargs):
        super().__init__(host,max_jobs=1,**kwargs)
    def _foreground(self):
        return (not self.host.faces.enabled or
                any(getattr(self.host,name,False) for name in ('closed','importing','cache_clearing','busy','pending_ops')) or
                bool(getattr(self.library,'job',{}).get('running')))
    def prepare(self,library,current,edge):
        if current.get('status') not in ((0,1) if self.view=='all' else (0,)) or not current.get('present',1):return 'skipped'
        # Native detector and original crops execute in the existing isolated
        # process. Only compact results/PNGs persist, never every full-size RAW.
        result=self.host.faces._read_or_compute(library,current,'original')
        return {'face_result':result,'photo':current}
    def consume(self,photo,result):
        if not isinstance(result,dict):return result
        photo=result['photo'];value=result['face_result'];faces=self.host.faces
        faces._done.add(cache_key(photo));faces._remember(cache_key(photo),value)
        faces.changed.emit(photo['id'],dict(value))
        return 'generated'
