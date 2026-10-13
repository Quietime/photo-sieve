"""Original focus/photo mark; build the Windows icon at all required shell sizes."""
from pathlib import Path
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[1]

def build():
    image=Image.new('RGBA',(1024,1024)); d=ImageDraw.Draw(image)
    d.rounded_rectangle((32,32,992,992),radius=220,fill='#1279F4')
    # Two photos, with a clear focus mark on the active one.
    d.rounded_rectangle((408,246,808,762),radius=70,fill='#75B9FF')
    d.rounded_rectangle((216,246,616,762),radius=70,fill='white')
    for points in [(286,422,286,346,362,346),(470,346,546,346,546,422),
                   (286,586,286,662,362,662),(470,662,546,662,546,586)]:
        d.line(points,fill='#1279F4',width=24,joint='curve')
    d.ellipse((376,466,456,546),fill='#1279F4')
    assets=ROOT/'assets'; assets.mkdir(exist_ok=True)
    image.save(assets/'photosieve.png')
    image.save(assets/'photosieve.ico',sizes=[(s,s) for s in (16,24,32,48,64,128,256)])

if __name__=='__main__': build()
