"""Adobe-compatible sidecars: atomic updates, preservation and explicit conflicts."""
import hashlib,os,tempfile,time
from pathlib import Path
import xml.etree.ElementTree as ET
from workflow_data import LABELS
NS={'x':'adobe:ns:meta/','rdf':'http://www.w3.org/1999/02/22-rdf-syntax-ns#','xmp':'http://ns.adobe.com/xap/1.0/'}
for prefix,uri in NS.items():ET.register_namespace(prefix,uri)
class XmpConflict(ValueError):pass
class XmpError(ValueError):pass

def inspect(path):
    path=Path(path)
    if path.is_symlink():raise XmpError('XMP 是链接，不能安全同步')
    try:
        if path.stat().st_size>8*1024**2:raise XmpError('XMP 文件过大')
        data=path.read_bytes()
    except FileNotFoundError:data=b''
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise XmpError('XMP 含不支持的实体定义')
    try:root=ET.fromstring(data) if data else ET.Element('{'+NS['x']+'}xmpmeta')
    except ET.ParseError as exc:raise XmpError('XMP 格式损坏，未覆盖原文件') from exc
    def get(name):
        key='{'+NS['xmp']+'}'+name;values=[]
        for node in root.iter():
            if key in node.attrib:values.append(node.attrib[key])
            if node.tag==key and node.text is not None:values.append(node.text)
        if len(set(values))>1:raise XmpConflict('XMP 内存在多个不一致的'+name)
        return values[0] if values else None
    rating=get('Rating');label=get('Label')
    if rating is not None:
        try:rating=int(rating)
        except (TypeError,ValueError):raise XmpError('XMP 星级无效')
        if not -1<=rating<=5:raise XmpError('XMP 星级超出范围')
    return dict(root=root,digest=hashlib.sha256(data).hexdigest(),stars=rating,label=label,data=data)

def sidecar(library,pid):
    photo=dict(library.photo(pid));source=Path(photo['path'])
    if source.is_symlink():raise XmpError('原图是链接，请重新扫描')
    st=source.stat()
    if (st.st_size,st.st_mtime_ns)!=(photo['size'],photo['mtime']):raise XmpError('原图已改变，请重新扫描')
    target=source.with_suffix('.xmp')
    with library.connect() as db:
        prefix=str(source.with_suffix(''))+'.'
        for row in db.execute('SELECT id,path FROM photos WHERE present=1 AND id!=? AND path COLLATE NOCASE>=? AND path COLLATE NOCASE<?',(pid,prefix,prefix+'\U0010ffff')):
            if os.path.normcase(str(Path(row['path']).with_suffix('.xmp')))==os.path.normcase(str(target)):raise XmpConflict('同名 RAW/JPEG 共用 XMP 路径，请先区分文件名再同步')
    return photo,target

def _sync_out(records,pid,stars=None,label=None,force=False):
    library=records.library
    with library.job_lock:
        if library.job['running']:raise XmpError('请等待扫描完成')
        with library.file_locks[pid%len(library.file_locks)]:
            photo,path=sidecar(library,pid);current=inspect(path)
            with library.connect() as db:baseline=db.execute('SELECT * FROM work_xmp WHERE photo_id=?',(pid,)).fetchone()
            if stars is not None and not 0<=stars<=5:raise XmpError('星级必须是0～5')
            if label is not None and label not in LABELS:raise XmpError('未知标签')
            collision=(baseline and baseline['digest'] and baseline['digest']!=current['digest']) or ((not baseline or not baseline['digest']) and current['data'] and ((stars is not None and current['stars'] not in (None,stars)) or (label is not None and current['label'] not in (None,label))))
            if collision and not force:raise XmpConflict('外部 XMP 已改变，请选择从 XMP 读取，或明确使用应用内容覆盖')
            root=current['root'];rdf=root.find('.//rdf:RDF',NS)
            if rdf is None:rdf=ET.SubElement(root,'{'+NS['rdf']+'}RDF')
            desc=rdf.find('rdf:Description',NS)
            if desc is None:desc=ET.SubElement(rdf,'{'+NS['rdf']+'}Description',{'{'+NS['rdf']+'}about':''})
            for name,value in (('Rating',stars),('Label',label)):
                if value is None:continue
                key='{'+NS['xmp']+'}'+name
                # Normalize duplicate representations while retaining every unrelated property.
                for node in root.iter():
                    node.attrib.pop(key,None)
                    for child in list(node):
                        if child.tag==key:node.remove(child)
                desc.set(key,str(value))
            data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'-',suffix='.tmp',dir=path.parent)
            try:
                with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
                if inspect(path)['digest']!=current['digest']:raise XmpConflict('写入前 XMP 再次改变，已取消同步')
                os.replace(tmp,path)
            finally:Path(tmp).unlink(missing_ok=True)
            check=inspect(path)
            if stars is not None and check['stars']!=stars or label is not None and check['label']!=label:raise XmpError('XMP 写入验证失败')
            with library.connect() as db:db.execute('INSERT OR REPLACE INTO work_xmp VALUES(?,?,?,?,?,?)',(pid,check['digest'],check['stars'],check['label'] or '',time.time(),''))
            return dict(photo_id=pid,path=str(path),stars=check['stars'],label=check['label'])

def sync_out(records,pid,stars=None,label=None,force=False):
    try:return _sync_out(records,pid,stars,label,force)
    except Exception as error:
        try:
            with records.library.connect() as db:
                db.execute("INSERT INTO work_xmp(photo_id,digest,stars,label,synced_at,error) VALUES(?,'',NULL,'',?,?) ON CONFLICT(photo_id) DO UPDATE SET error=excluded.error",(pid,time.time(),str(error)))
        except Exception:pass
        raise

def sync_in(records,pid,rating_records,engine):
    library=records.library
    with library.file_locks[pid%len(library.file_locks)]:
        photo,path=sidecar(library,pid);value=inspect(path)
    if not value['data']:raise XmpError('没有对应的 XMP 文件')
    if value['stars']==-1:raise XmpConflict('XMP 的拒绝标记不会自动删除照片，请在筛选页人工确认')
    if value['label'] not in (None,*LABELS):raise XmpConflict('XMP 标签未映射到应用颜色，请先在外部修改或仅保留外部文件')
    # Native write performs its own original backup and pixel/metadata verification.
    result=rating_records.write(pid,value['stars'],engine) if value['stars'] is not None else None
    if value['label'] is not None:records.annotate([pid],label=value['label'])
    if inspect(path)['digest']!=value['digest']:raise XmpConflict('读取过程中外部 XMP 再次改变，请重新读取')
    with library.connect() as db:db.execute('INSERT OR REPLACE INTO work_xmp VALUES(?,?,?,?,?,?)',(pid,value['digest'],value['stars'],value['label'] or '',time.time(),''))
    return dict(photo_id=pid,rating=result,label=value['label'])
