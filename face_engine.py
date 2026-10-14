"""Offline face locations and native-pixel crops; no Qt or network dependencies.

Both inputs must already have their EXIF/RAW orientation corrected. Detect on the
small preview, then pass the metadata to an isolated worker that decodes the
original and crops it there. Only the crop needs to return to the interface.
"""
from pathlib import Path
import math
import threading

from PIL import Image

ENGINE_VERSION = 'yunet-2023mar-native-v1'
MODEL_PATH = Path(__file__).resolve().parent / 'assets' / 'face_detection_yunet_2023mar.onnx'
_detector = None
_detector_key = None
_lock = threading.Lock()


class FaceDetectionUnavailable(ValueError):
    """An unavailable local engine is distinct from a successfully detected empty scene."""


def _box(face):
    """A finite, visible normalized rectangle; invalid cached detections are ignored."""
    try:
        if not isinstance(face, dict):
            return None
        values = face['box']
        if not isinstance(values, (list, tuple)) or len(values) != 4:
            return None
        x, y, width, height = (float(value) for value in values)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in (x, y, width, height)) or width <= 0 or height <= 0:
        return None
    left, top = max(0.0, x), max(0.0, y)
    right, bottom = min(1.0, x + width), min(1.0, y + height)
    if right <= left or bottom <= top:
        return None
    return left, top, right - left, bottom - top


def detect_faces(preview, *, model_path=None, max_edge=1280, score_threshold=.75):
    """Return JSON-compatible face dictionaries, largest visible face first.

    ``box`` is [left, top, width, height] normalized to the upright preview;
    ``landmarks`` contains five normalized [x, y] points. No identifying face
    embeddings, names or other biometric recognition data are created.
    OpenCV is imported lazily and uses one CPU thread with OpenCL disabled.
    """
    if preview.width <= 0 or preview.height <= 0:
        raise ValueError('照片尺寸无效')
    if not isinstance(max_edge, int) or max_edge < 32:
        raise ValueError('检测预览边长至少为 32 像素')
    if not math.isfinite(score_threshold) or not 0 < score_threshold <= 1:
        raise ValueError('人脸检测置信度必须在 0 到 1 之间')
    path = Path(model_path) if model_path is not None else MODEL_PATH
    if not path.is_file():
        raise FaceDetectionUnavailable('缺少本地人脸检测模型：' + str(path))
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise FaceDetectionUnavailable('本地人脸检测组件不可用，请重新安装应用') from error
    image = preview
    if max(preview.size) > max_edge:
        ratio = max_edge / max(preview.size)
        image = preview.resize((max(1, round(preview.width * ratio)), max(1, round(preview.height * ratio))),
                               Image.Resampling.BILINEAR)
    image = image.convert('RGB') if image.mode != 'RGB' else image
    width, height = image.size
    pixels = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
    global _detector, _detector_key
    try:
        information = path.stat()
        key = (str(path.resolve()), information.st_size, information.st_mtime_ns, float(score_threshold))
        # FaceDetectorYN mutates its input size: serialize access when an embedding
        # application happens to call this process-local engine from two threads.
        with _lock:
            if _detector is None or _detector_key != key:
                cv2.setNumThreads(1)
                cv2.ocl.setUseOpenCL(False)
                _detector = cv2.FaceDetectorYN.create(str(path), '', (width, height), float(score_threshold), .3, 5000,
                                                    cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
                _detector_key = key
            _detector.setInputSize((width, height))
            _, rows = _detector.detect(pixels)
    except (OSError, cv2.error) as error:
        raise FaceDetectionUnavailable('本地人脸检测失败：' + str(error)) from error
    faces = []
    if rows is not None:
        for row in rows:
            score = float(row[14])
            if not math.isfinite(score) or score < score_threshold:
                continue
            face = {'box': [float(row[0]) / width, float(row[1]) / height,
                            float(row[2]) / width, float(row[3]) / height], 'score': score}
            box = _box(face)
            if box is None:
                continue
            face['box'] = list(box)
            face['area'] = box[2] * box[3]
            face['landmarks'] = [[max(0.0, min(1.0, float(row[i]) / width)),
                                  max(0.0, min(1.0, float(row[i + 1]) / height))]
                                 for i in range(4, 14, 2)]
            face['preview_size'] = list(preview.size)
            face['engine_version'] = ENGINE_VERSION
            faces.append(face)
    faces.sort(key=lambda face: face['area'], reverse=True)
    return faces


def _eye_center(face, box):
    x, y, width, height = box
    try:
        eyes = face['landmarks'][:2]
        if len(eyes) == 2:
            points = [(float(eye[0]), float(eye[1])) for eye in eyes if len(eye) == 2]
            if len(points) == 2 and all(math.isfinite(px) and math.isfinite(py) and
                                         x <= px <= x + width and y <= py <= y + height for px, py in points):
                return (points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return x + width / 2, y + height / 2


def crop_faces(original, faces, *, padding=.25, max_edge=1024):
    """Crop the largest detected face from the upright original, with context.

    Padding is 25% of the detected face's width/height on EACH side. Pixels are
    never resampled. A large face becomes a bounded original-pixel region around
    the eyes (or face center); ``is_partial`` distinguishes that focus detail.
    ``ratio``, ``ratio_x`` and ``ratio_y`` are always 1 for an available image.
    ``crop_box`` is [left, top, right, bottom] in original-image pixels.
    An empty or invalid detection returns the same metadata shape with image=None.
    """
    if original.width <= 0 or original.height <= 0:
        raise ValueError('原照片尺寸无效')
    if not math.isfinite(padding) or not 0 <= padding <= 2:
        raise ValueError('人脸裁切余量必须在 0 到 2 之间')
    if not isinstance(max_edge, int) or max_edge < 1:
        raise ValueError('人脸裁切边长必须为正整数')
    candidates = [(face, box) for face in (faces or ()) if (box := _box(face)) is not None]
    result = {'image': None, 'face_count': len(candidates), 'original_size': list(original.size),
              'crop_box': None, 'native_size': None, 'output_size': None,
              'ratio': None, 'ratio_x': None, 'ratio_y': None, 'face': None,
              'is_partial': False, 'requested_box': None, 'engine_version': ENGINE_VERSION}
    if not candidates:
        return result
    face, (x, y, width, height) = max(candidates, key=lambda candidate: candidate[1][2] * candidate[1][3])
    # Ignore tiny floating-point residue at exact pixel boundaries.
    left = max(0, math.floor((x - width * padding) * original.width + 1e-8))
    top = max(0, math.floor((y - height * padding) * original.height + 1e-8))
    right = min(original.width, math.ceil((x + width * (1 + padding)) * original.width - 1e-8))
    bottom = min(original.height, math.ceil((y + height * (1 + padding)) * original.height - 1e-8))
    if right <= left or bottom <= top:
        return result
    requested = [left, top, right, bottom]
    partial = right - left > max_edge or bottom - top > max_edge
    if partial:
        center_x, center_y = _eye_center(face, (x, y, width, height))
        if right - left > max_edge:
            left = max(left, min(right - max_edge, round(center_x * original.width - max_edge / 2)))
            right = left + max_edge
        if bottom - top > max_edge:
            top = max(top, min(bottom - max_edge, round(center_y * original.height - max_edge / 2)))
            bottom = top + max_edge
    crop = original.crop((left, top, right, bottom))
    result.update(image=crop, crop_box=[left, top, right, bottom], native_size=list(crop.size),
                  output_size=list(crop.size), ratio=1.0, ratio_x=1.0, ratio_y=1.0,
                  is_partial=partial, requested_box=requested,
                  face={**face, 'box': [x, y, width, height]})
    return result
