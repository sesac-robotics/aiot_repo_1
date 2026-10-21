"""등록 폴더의 사진을 읽고 환자 ID별 대표 임베딩을 저장한다."""
from pathlib import Path
import numpy as np
from common import EXTENSIONS, get_model, load_image, save_json, single_face, unit

REGISTERED = Path('/app/data/registered')
REGISTRY = Path('/app/data/registry')


def main():
    if not REGISTERED.is_dir():
        raise FileNotFoundError('등록 폴더가 없습니다: /app/data/registered')
    model = get_model()
    identities, vectors, details = [], [], []
    for folder in sorted(REGISTERED.iterdir()):
        if not folder.is_dir():
            continue
        if folder.name.lower() == 'unknown':
            print('[제외] Unknown은 등록 ID로 사용할 수 없습니다.')
            continue
        embeddings, success, failures = [], [], []
        images = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS)
        for path in images:
            try:
                face, status = single_face(model, load_image(path))
                if status != 'OK':
                    raise ValueError(status)
                embeddings.append(unit(face.normed_embedding))
                success.append(path.name)
            except Exception as exc:
                failures.append({'file': path.name, 'reason': str(exc)})
        if embeddings:
            identities.append(folder.name)
            vectors.append(unit(np.mean(embeddings, axis=0)))
        details.append({'id': folder.name, 'used': success, 'failed': failures})
        print(f'{folder.name}: 성공 {len(success)}, 실패 {len(failures)}')
    if not identities:
        raise RuntimeError('등록 성공한 사람이 없습니다. 등록 사진을 확인하세요.')
    REGISTRY.mkdir(parents=True, exist_ok=True)
    np.savez(REGISTRY / 'faces.npz', ids=np.asarray(identities), embeddings=np.stack(vectors))
    save_json(REGISTRY / 'registration_report.json', {'registered_ids': identities, 'details': details})
    print(f'등록 완료: {len(identities)}명 → {REGISTRY / "faces.npz"}')


if __name__ == '__main__':
    main()
