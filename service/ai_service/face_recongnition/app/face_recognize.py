"""캡처된 사진 1장에서 환자 ID 또는 상태 코드를 반환한다."""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from common import get_model, load_image, save_json, single_face, unit

REGISTRY = Path('/app/data/registry/faces.npz')
DEFAULT_IMAGE = Path('/app/data/input/capture.jpg')
DEFAULT_OUTPUT = Path('/app/data/output/result.json')
DEFAULT_THRESHOLD = 0.5  # FEI 실험에서 사용한 초기 임계값. 실제 환경 검증 후 조정


def recognize(model, image_path, threshold):
    image = load_image(image_path)
    face, status = single_face(model, image)
    if status != 'OK':
        return {'status': status, 'patient_id': None, 'similarity': None}

    if not REGISTRY.is_file():
        raise FileNotFoundError('등록 벡터가 없습니다. 먼저 face_register.py를 실행하세요.')
    with np.load(REGISTRY, allow_pickle=False) as gallery:
        ids = [str(x) for x in gallery['ids']]
        matrix = np.asarray(gallery['embeddings'], dtype=np.float32)
    if not ids or len(ids) != len(matrix):
        raise RuntimeError('등록 벡터 데이터가 잘못되었습니다.')
    sims = matrix @ unit(face.normed_embedding)
    idx = int(np.argmax(sims))
    score = float(sims[idx])
    matched = score >= threshold
    return {
        'status': 'Recognized' if matched else 'Unknown',
        'patient_id': ids[idx] if matched else None,
        'similarity': round(score, 5),
    }


def main():
    parser = argparse.ArgumentParser(description='캡처 사진 기반 환자 얼굴 식별')
    parser.add_argument('--image', type=Path, default=DEFAULT_IMAGE)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()
    if not -1 <= args.threshold <= 1:
        parser.error('threshold 값은 -1 이상 1 이하여야 합니다.')
    start = time.perf_counter()
    try:
        model = get_model()
        result = recognize(model, args.image, args.threshold)
    except Exception as exc:
        result = {'status': 'Error', 'patient_id': None, 'similarity': None, 'error': str(exc)}
    result['image'] = args.image.name
    result['elapsed_ms'] = round((time.perf_counter() - start) * 1000, 2)
    save_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result['status'] == 'Error':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
