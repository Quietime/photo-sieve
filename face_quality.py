"""Conservative local quality hints, never an automatic deletion decision."""
from pathlib import Path
import threading
from PIL import Image,ImageOps,ImageFilter,ImageStat
_detector=None;_detector_key=None;_lock=threading.Lock()
EYE_MODEL=Path(__file__).parent/'assets/haarcascade_eye_tree_eyeglasses.xml'

def assess_face(image,face=None):
    if min(image.size)<64:return dict(state='uncertain',text='人脸较小，清晰度待核对',sharpness=None,eyes='unknown')
    gray=ImageOps.grayscale(image);gray.thumbnail((256,256),Image.Resampling.LANCZOS)
    # Normalized face size makes scores comparable; edge energy remains only a hint.
    detail=gray.filter(ImageFilter.FIND_EDGES).crop((3,3,gray.width-3,gray.height-3));sharpness=round(ImageStat.Stat(detail).var[0],2)
    eyes='unknown';warnings=[]
    if sharpness<55:warnings.append('人脸可能模糊')
    if EYE_MODEL.is_file() and sharpness>=55 and not (face or {}).get('partial'):
        try:
            import cv2,numpy as np
            cv2.setNumThreads(1)
            global _detector,_detector_key
            stat=EYE_MODEL.stat();key=(str(EYE_MODEL),stat.st_size,stat.st_mtime_ns);pixels=np.array(gray)
            with _lock:
                if _detector is None or _detector_key!=key:_detector=cv2.CascadeClassifier(str(EYE_MODEL));_detector_key=key
                detected=_detector.detectMultiScale(pixels[:round(gray.height*.68)],scaleFactor=1.08,minNeighbors=5,minSize=(12,8))
            # A missing eye detection is not proof of closed eyes (profile, glasses, lights).
            eyes='visible' if len(detected)>=2 else 'review'
            if eyes=='review':warnings.append('眼部待核对：可能闭眼、侧脸或遮挡')
        except Exception:eyes='unknown'
    return dict(state='review' if warnings else 'clear',text='；'.join(warnings) or '人脸细节较清楚',sharpness=sharpness,eyes=eyes,notice='本地辅助判断，舞台灯光和侧脸可能影响结果')
