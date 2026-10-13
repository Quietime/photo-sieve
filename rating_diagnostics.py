"""Frozen-app native handler check, including isolated worker imports."""
import hashlib,json,tempfile
from pathlib import Path
from PIL import Image
from rating_engine import RatingEngine

def run(output):
    report={'ok':False,'formats':{}}
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);engine=RatingEngine(root/'logs')
        try:
            for extension,format in [('jpg','JPEG'),('tif','TIFF'),('png','PNG')]:
                path=root/('photo.'+extension);Image.new('RGB',(80,60),(40,80,140)).save(path,format=format)
                digest=hashlib.sha256(Image.open(path).tobytes()).hexdigest();values=[]
                for stars in [1,2,3,4,5,0]:
                    engine.run(path,stars);result=engine.run(path);assert result['stars']==stars
                    assert hashlib.sha256(Image.open(path).tobytes()).hexdigest()==digest;values.append(stars)
                report['formats'][extension]=values
            report['ok']=True
        finally:engine.close()
    Path(output).write_text(json.dumps(report,indent=2),encoding='utf8');return 0
