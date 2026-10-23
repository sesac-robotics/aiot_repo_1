"""DB 등록용 사진에서 InsightFace buffalo_l 얼굴 특징 벡터를 추출해 JSON으로 저장.

먼저 GUI/서버에서 사람을 등록하고 발급된 person_id(P0001/S0001/A0001 등)를
사진 폴더 이름으로 사용한다. JSON의 patient_id는 그 서버 person_id와 같은 값이다.
입력: /app/data/db_registered/<person_id>/*.jpg|png|jpeg|bmp|webp
출력: /app/data/db_embeddings/<patient_id>.json

실행: python3 face_db_export.py
재생성: python3 face_db_export.py --overwrite
"""

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from common import EXTENSIONS, get_model, load_image, single_face, unit

INPUT_DIR = Path('/app/data/db_registered')
OUTPUT_DIR = Path('/app/data/db_embeddings')
MODEL_NAME = 'buffalo_l'


def save_json_atomic(path, value):
    """JSON을 임시 파일로 먼저 기록하고 완료 후 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.writing-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser(description='서버 person_id별 등록 사진 -> 512차원 특징 벡터 JSON')
    parser.add_argument('--input', type=Path, default=INPUT_DIR, help='서버 person_id별 하위 폴더가 있는 등록 사진 경로')
    parser.add_argument('--output', type=Path, default=OUTPUT_DIR, help='person_id별 JSON 저장 폴더')
    parser.add_argument('--overwrite', action='store_true', help='기존 JSON을 다시 추출해 갱신')
    args = parser.parse_args()

    if not args.input.is_dir():
        parser.error(f'입력 폴더가 없습니다: {args.input}')

    patient_dirs = sorted(p for p in args.input.iterdir() if p.is_dir())
    if not patient_dirs:
        parser.error(f'환자별 하위 폴더가 없습니다: {args.input}')

    args.output.mkdir(parents=True, exist_ok=True)
    model = None
    exported = skipped = failed = 0

    for folder in patient_dirs:
        patient_id = folder.name
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', patient_id) or patient_id.lower() == 'unknown':
            print(f'[SKIP] 유효하지 않은 환자 ID: {patient_id}', flush=True)
            skipped += 1
            continue

        output_file = args.output / f'{patient_id}.json'
        if output_file.exists() and not args.overwrite:
            print(f'[SKIP] 이미 JSON 존재: {output_file.name} (--overwrite로 갱신)', flush=True)
            skipped += 1
            continue

        images = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS)
        if not images:
            print(f'[SKIP] 사진 없음: {patient_id}', flush=True)
            skipped += 1
            continue

        if model is None:
            print('[START] buffalo_l 모델 로딩...', flush=True)
            model = get_model()

        successes = []
        errors = []
        vectors = []
        for image_path in images:
            try:
                face, status = single_face(model, load_image(image_path))
                if status != 'OK':
                    errors.append({'image_name': image_path.name, 'status': status})
                    continue

                embedding = unit(face.normed_embedding)
                vectors.append(embedding)
                successes.append({
                    'image_name': image_path.name,
                    'embedding': embedding.astype(float).tolist(),
                })
            except Exception as exc:
                errors.append({'image_name': image_path.name, 'status': 'Error', 'reason': str(exc)})

        if not vectors:
            print(f'[FAILED] {patient_id}: 유효한 얼굴 이미지 0장', flush=True)
            failed += 1
            continue

        # 여러 장 등록한 경우, 각 정규화 벡터의 평균을 다시 정규화한다.
        representative = unit(np.mean(vectors, axis=0))
        result = {
            'patient_id': patient_id,
            'model': MODEL_NAME,
            'embedding_dim': int(representative.size),
            'embedding_normalized': True,
            'embedding': representative.astype(float).tolist(),
            'images_used': len(successes),
            'images': successes,
            'failed_images': errors,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        save_json_atomic(output_file, result)
        exported += 1
        print(f'[OK] {patient_id}: {len(successes)}장 성공, {len(errors)}장 실패 -> {output_file}', flush=True)

    print(f'[DONE] 생성 {exported}명 / 건너뜀 {skipped}명 / 실패 {failed}명', flush=True)


if __name__ == '__main__':
    main()
