"""Local, fingerprinted workbench data. User annotations never live in image caches."""
import json, math, time,secrets
from datetime import datetime
from pathlib import Path
from PIL import Image,ImageOps,ImageFilter,ImageStat

LABELS={'':'无标签','Red':'待精修','Yellow':'待确认','Green':'待发布','Blue':'重点照片','Purple':'备选'}
COLORS={'Red':'#BD6967','Yellow':'#D1B477','Green':'#85A88C','Blue':'#7DA7CA','Purple':'#AD93BD'}
SCHEMA='''
CREATE INDEX IF NOT EXISTS work_path_nocase ON photos(path COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS work_annotations(photo_id INTEGER PRIMARY KEY,label TEXT NOT NULL DEFAULT '',segment TEXT NOT NULL DEFAULT '',split_before INTEGER NOT NULL DEFAULT 0,merge_key TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS work_features(photo_id INTEGER PRIMARY KEY,size INTEGER NOT NULL,mtime INTEGER NOT NULL,signature TEXT NOT NULL,captured REAL NOT NULL,time_source TEXT NOT NULL,quality TEXT NOT NULL,version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS work_xmp(photo_id INTEGER PRIMARY KEY,digest TEXT NOT NULL,stars INTEGER,label TEXT NOT NULL,synced_at REAL NOT NULL,error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS work_history(seq INTEGER PRIMARY KEY,photo_id INTEGER NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,created REAL NOT NULL,batch TEXT NOT NULL DEFAULT '');
'''
VERSION=1

def signature(image):
    gray=ImageOps.grayscale(image).resize((9,8),Image.Resampling.BILINEAR);v=list(gray.getdata());bits=0
    for y in range(8):
        for x in range(8):bits=(bits<<1)|int(v[y*9+x]>v[y*9+x+1])
    small=image.convert('RGB').resize((24,24),Image.Resampling.BILINEAR)
    # Coarse spatial colours distinguish similarly lit stages without expensive embeddings.
    colors=[]
    for y in (0,12):
        for x in (0,12):colors.extend(round(n/255,4) for n in ImageStat.Stat(small.crop((x,y,x+12,y+12))).mean)
    return {'hash':str(bits),'colors':colors,'aspect':round(image.width/max(1,image.height),3)}

def similar(a,b):
    if not a or not b:return False
    return (abs(math.log(max(.01,a['aspect'])/max(.01,b['aspect'])))<.3 and
            (int(a['hash'])^int(b['hash'])).bit_count()<=12 and
            sum((x-y)**2 for x,y in zip(a['colors'],b['colors']))/12<.018)

def captured_time(path,fallback):
    try:
        with Image.open(path) as image:
            e=image.getexif();value=e.get_ifd(34665).get(36867,e.get(36867))
            if value:return datetime.strptime(str(value).strip(),' %Y:%m:%d %H:%M:%S'.strip()).timestamp(),'拍摄时间'
    except (OSError,ValueError,TypeError):pass
    # RAW metadata is batch-read separately by the analysis worker.
    return fallback/1e9,'文件时间'

class WorkRecords:
    def __init__(self,library):
        self.library=library
        with library.connect() as db:
            db.executescript(SCHEMA)
            if 'batch' not in {r[1] for r in db.execute('PRAGMA table_info(work_history)')}:db.execute("ALTER TABLE work_history ADD COLUMN batch TEXT NOT NULL DEFAULT ''")
    def annotations(self,ids=None):
        with self.library.connect() as db:
            rows=db.execute('SELECT a.* FROM work_annotations a JOIN photos p ON p.id=a.photo_id WHERE p.present=1')
            return {r['photo_id']:dict(r) for r in rows if ids is None or r['photo_id'] in ids}
    def annotate(self,ids,**changes):return self.annotate_map({pid:changes for pid in dict.fromkeys(ids)})
    def annotate_map(self,mapping):
        for changes in mapping.values():
            if not set(changes)<= {'label','segment','split_before','merge_key'}:raise ValueError('未知标记')
            if 'label' in changes and changes['label'] not in LABELS:raise ValueError('未知颜色标签')
            if 'segment' in changes and len(changes['segment'])>120:raise ValueError('分段名称不能超过120字')
        batch=secrets.token_hex(12)
        with self.library.job_lock:
            if self.library.job['running']:raise ValueError('请等待扫描结束')
            with self.library.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for pid,changes in mapping.items():
                    if not db.execute('SELECT 1 FROM photos WHERE id=? AND present=1',(pid,)).fetchone():raise ValueError('照片已移出当前图库')
                    old=dict(db.execute('SELECT * FROM work_annotations WHERE photo_id=?',(pid,)).fetchone() or dict(photo_id=pid,label='',segment='',split_before=0,merge_key=''))
                    new=dict(old,**changes)
                    if old==new:continue
                    db.execute('INSERT INTO work_history(photo_id,before_json,after_json,created,batch) VALUES(?,?,?,?,?)',(pid,json.dumps(old),json.dumps(new),time.time(),batch))
                    db.execute('INSERT OR REPLACE INTO work_annotations VALUES(?,?,?,?,?)',tuple(new[k] for k in ('photo_id','label','segment','split_before','merge_key')))
    def split(self,pid,group_ids):
        annotation=self.annotations({pid}).get(pid,{})
        changes={pid:dict(split_before=1,merge_key='')}
        if annotation.get('merge_key') and pid in group_ids:
            key=secrets.token_hex(12);changes={item:dict(merge_key=key) for item in group_ids[group_ids.index(pid):]};changes[pid]['split_before']=1
        return self.annotate_map(changes)
    def undo(self):
        with self.library.job_lock:
            if self.library.job['running']:raise ValueError('请等待扫描结束')
            with self.library.connect() as db:
                db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM work_history ORDER BY seq DESC LIMIT 1').fetchone()
                if not row:return None
                rows=list(db.execute('SELECT * FROM work_history WHERE batch=? ORDER BY seq DESC',(row['batch'],))) if row['batch'] else [row]
                self.last_undo_labels={}
                for entry in rows:
                    old=json.loads(entry['before_json'])
                    if old['label']!=json.loads(entry['after_json'])['label']:self.last_undo_labels[old['photo_id']]=old['label']
                    db.execute('INSERT OR REPLACE INTO work_annotations VALUES(?,?,?,?,?)',tuple(old[k] for k in ('photo_id','label','segment','split_before','merge_key')));db.execute('DELETE FROM work_history WHERE seq=?',(entry['seq'],))
                return row['photo_id']
    def save_feature(self,photo,feature):
        with self.library.connect() as db:
            current=db.execute('SELECT size,mtime,present FROM photos WHERE id=?',(photo['id'],)).fetchone()
            if not current or not current['present'] or (current['size'],current['mtime'])!=(photo['size'],photo['mtime']):return False
            db.execute('INSERT OR REPLACE INTO work_features VALUES(?,?,?,?,?,?,?,?)',(photo['id'],photo['size'],photo['mtime'],json.dumps(feature['signature']),feature['captured'],feature['time_source'],json.dumps(feature['quality']),VERSION));return True
    def inventory(self,ids=None):
        with self.library.connect() as db:
            rows=db.execute('''SELECT p.id,p.path,p.size,p.mtime,p.status,coalesce(a.label,'') label,coalesce(a.segment,'') segment,coalesce(a.split_before,0) split_before,coalesce(a.merge_key,'') merge_key,f.signature,f.captured,f.time_source,f.quality
FROM photos p LEFT JOIN work_annotations a ON a.photo_id=p.id LEFT JOIN work_features f ON f.photo_id=p.id AND f.size=p.size AND f.mtime=p.mtime AND f.version=? WHERE p.present=1 ORDER BY p.id''',(VERSION,))
            return [dict(r) for r in rows if ids is None or r['id'] in ids]
    def groups(self,mode='similar',gap=90,ids=None):
        rows=self.inventory(None if ids is None else set(ids));rows.sort(key=lambda r:(r['captured'] if r['captured'] is not None else r['mtime']/1e9,r['id']))
        groups=[];last=None;representative=None;manual={}
        for row in rows:
            timestamp=row['captured'] if row['captured'] is not None else row['mtime']/1e9
            sig=json.loads(row['signature']) if row['signature'] else None
            split=not last or row['split_before'] or row['segment']!=last['segment'] or timestamp-last['when']>(gap if mode=='time' else min(gap,30))
            if mode=='similar' and (not similar(sig,last['sig'] if last else None) or not similar(sig,representative)):split=True
            key=row['merge_key']
            if key:
                group=manual.get(key)
                if group is None:group={'key':f'manual:{key}','ids':[],'title':row['segment'] or '手动合并','when':timestamp};groups.append(group);manual[key]=group
            else:
                if last and last.get('merge_key'):split=True
                if split:groups.append({'key':f'{mode}:{row["id"]}','ids':[],'title':row['segment'] or datetime.fromtimestamp(timestamp).strftime('%m-%d %H:%M:%S'),'when':timestamp});representative=sig
                group=groups[-1]
            group['ids'].append(row['id']);last=dict(row,when=timestamp,sig=sig)
        return groups
    def summary(self):
        with self.library.connect() as db:
            counts={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM photos WHERE present=1 GROUP BY status')}
            ratings={r[0]:r[1] for r in db.execute("SELECT c.stars,count(*) FROM rating_cache c JOIN photos p ON p.id=c.photo_id AND p.size=c.size AND p.mtime=c.mtime WHERE p.present=1 AND c.state='ready' GROUP BY c.stars")} if db.execute("SELECT 1 FROM sqlite_master WHERE name='rating_cache'").fetchone() else {}
            deleted=db.execute('SELECT count(*) FROM photos WHERE '+self.library.query('deleted')).fetchone()[0]
            features=db.execute('SELECT count(*) FROM work_features f JOIN photos p ON p.id=f.photo_id AND p.size=f.size AND p.mtime=f.mtime WHERE p.present=1 AND f.version=?',(VERSION,)).fetchone()[0]
            labels={r[0]:r[1] for r in db.execute('SELECT label,count(*) FROM work_annotations a JOIN photos p ON p.id=a.photo_id WHERE p.present=1 GROUP BY label')}
            session=db.execute("SELECT count(*) FROM history WHERE rowid>?",(getattr(self.library,'work_session_seq',0),)).fetchone()[0]
            total=sum(counts.values());return dict(total=total,pending=counts.get(0,0),keep=counts.get(1,0),deleted=deleted,ratings=ratings,unknown=max(0,total-sum(ratings.values())),labels=labels,features=features,session_actions=session)
