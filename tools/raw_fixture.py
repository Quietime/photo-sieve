"""Generated CFA sensor data in a valid DNG TIFF; no user photos or downloaded sample."""
def write_raw(path):
    from PIL import Image,TiffImagePlugin
    import numpy as np
    y,x=np.mgrid[:400,:640]
    sensor=((x*53+y*79)%60000+2000).astype(np.uint16)
    tags=TiffImagePlugin.ImageFileDirectory_v2()
    values={271:'PhotoSieve',272:'Synthetic RAW',262:32803,33421:(2,2),
        33422:bytes((0,1,1,2)),50706:bytes((1,4,0,0)),50707:bytes((1,1,0,0)),
        50708:'PhotoSieve Synthetic RAW',50714:0,50717:65535,
        50721:tuple(TiffImagePlugin.IFDRational(v) for v in (1,0,0,0,1,0,0,0,1)),
        50728:tuple(TiffImagePlugin.IFDRational(1) for _ in range(3)),50778:21}
    for key,value in values.items():tags[key]=value
    for key in (33422,50706,50707):tags.tagtype[key]=1
    tags.tagtype[50721]=10
    Image.fromarray(sensor).save(path,format='TIFF',tiffinfo=tags)
