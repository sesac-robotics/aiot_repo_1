"""InsightFace buffalo_l 모델과 공통 얼굴 처리 기능."""
import json
from pathlib import Path

import cv2
import numpy as np

EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}


def load_image(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'이미지 파일 없음: {path}')
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f'이미지 읽기 실패: {path}')
    return image


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError('유효하지 않은 얼굴 특징 벡터')
    return vector / norm


def get_model():
    from insightface.app import FaceAnalysis
    model = FaceAnalysis(
        name='buffalo_l',
        root='/root/.insightface',
        allowed_modules=['detection', 'recognition'],
        providers=['CUDAExecutionProvider', 'CPUExecutionProvider'],
    )
    model.prepare(ctx_id=0, det_size=(640, 640))
    for task in ('detection', 'recognition'):
        providers = model.models[task].session.get_providers()
        if 'CUDAExecutionProvider' not in providers:
            raise RuntimeError(f'{task} 모델 CUDA 실행 제공자 미활성: {providers}')
    return model


def single_face(model, image):
    faces = model.get(image)
    if not faces:
        return None, 'NoFace'
    if len(faces) > 1:
        return None, 'MultipleFaces'
    return faces[0], 'OK'


def save_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
