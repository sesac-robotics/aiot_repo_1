# HCC PostgreSQL DB v0.1 — 실행 가이드 (Ubuntu)

**목표**: PC1 Main Server에서 PostgreSQL + pgvector를 사용해 HCC SR-01~06의 기본 데이터를 저장·조회한다. 기존 SQLite T01~T07 PoC를 대체·삭제하지 않으며, 별도 DB로 시작한다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `HCC_ERD_v0.1.drawio` | diagrams.net에서 수정 가능한 ERD. 전체·인물인증·낙상장치 3페이지 |
| `schema.sql` | **11개 기본 테이블**, PK/FK, CHECK, 인덱스, 수정 시각 트리거 |
| `hcc_db.py` | psycopg 3 기반 Python 접근 계층 (환자·얼굴·RFID·낙상·알람·도어) |
| `validation.py` | 실제 PC2 `buffalo_l` / 512차원 JSON 유효성 검사 |
| `db_cli.py` | 터미널에서 초기화, 사람 등록, 얼굴 임베딩 등록·검색, DB 헬스체크 |
| `run_db_smoke.py` | 실제 DB가 작동할 때 가상 데이터로 저장·검색·이력 시험 후 정리 |
| `compose.yaml` | **개발용** PostgreSQL 17 + pgvector 컨테이너 |
| `requirements.txt` | Python 라이브러리 |
| `.env.example` | 개발용 DSN 예시 (실제 비밀번호 사용 시 교체) |
| `DECISIONS_REQUIRED.md` | 팀원과 합의해야 할 업무 규칙 및 데이터 규격 |
| `tests/` | DB 없이도 가능한 형식·구조 단위 테스트 |

## 방법 A: Docker 이용 (추천)

> Ubuntu에 Docker Engine 및 Docker Compose Plugin이 이미 설치되어 있는 경우. 없다면 팀 환경에 맞게 Docker부터 설치하거나 기존 PostgreSQL을 사용한다.

```bash
# 1. 이 압축을 풀고 터미널로 폴더 이동
cd HCC_PostgreSQL_DB_v0.1

# 2. 로컬 DB 서버 시작 (외부에 노출되지 않고 127.0.0.1:5432에 바인딩)
docker compose up -d

docker compose ps

# 3. Python 가상환경 생성 및 패키지 설치
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. 아래 계정은 compose.yaml의 예시 개발 계정과 같음.
#   실제 프로젝트에서는 비밀번호를 교체하고, DSN을 안전하게 관리할 것.
export HCC_DB_DSN='postgresql://hcc_app:change_this_dev_password@127.0.0.1:5432/hcc'

# 5. 확장과 11개 테이블 생성
python3 db_cli.py init

# 6. 11개 테이블 생성 확인
python3 db_cli.py health

# 7. 실제 DB 연결을 사용하는 합성 데이터 smoke test
python3 run_db_smoke.py
```

**주의**: `compose.yaml`의 예시 비밀번호는 공개된 개발용 값이다. 변경하려면 컨테이너 **최초 생성 전에** `HCC_DB_PASSWORD` 환경변수와 `HCC_DB_DSN`의 비밀번호를 **동일하게** 설정하자. 기존 볼륨을 만든 뒤 비밀번호 환경변수만 바꾸면 저장된 DB 계정 비밀번호가 자동 변경되지 않는다.

간단한 테이블 점검:

```bash
docker compose exec postgres psql -U hcc_app -d hcc -c '\dt hcc.*'
docker compose exec postgres psql -U hcc_app -d hcc -c 'SELECT extversion FROM pg_extension WHERE extname = '\''vector'\'';'
```

**중지**는 `docker compose down` (데이터 볼륨 유지). `docker compose down -v`는 DB 데이터 볼륨까지 삭제하므로 실행하지 말 것.

## 방법 B: 이미 PostgreSQL 설치되어 있다면

PostgreSQL DB와 `pgvector` 확장 설치가 먼저 필요하다. 서버 관리자 권한으로 빈 `hcc` DB 및 적절한 DB 계정을 만든 다음 아래를 실행한다.

```bash
export HCC_DB_DSN='postgresql://<user>:<password>@127.0.0.1:5432/hcc'
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 db_cli.py init
python3 db_cli.py health
```

`CREATE EXTENSION vector` 권한이 없다면 PostgreSQL 관리자에게 해당 DB에서 한 번 실행해 달라고 요청하면 된다.

## 실제 얼굴 인식팀 JSON 2건 등록

압축파일 `PC1_Face_Embedding_Comparison.zip`이 **현재 이 폴더의 상위 디렉터리**에 있을 때:

```bash
mkdir -p /tmp/hcc_pc2_faces
unzip -q ../PC1_Face_Embedding_Comparison.zip -d /tmp/hcc_pc2_faces

python3 db_cli.py import-faces \
  --dir /tmp/hcc_pc2_faces/PC1_Face_Embedding_Comparison/db_embeddings
python3 db_cli.py persons
```

등록 결과로 `patient_001`, `patient_002`가 생성된다. 얼굴팀 JSON에는 이름이 없어서 **`name=NULL`인 환자 껍데기 레코드**만 먼저 만든다. 실제 관리자는 환자 정보를 입력하여 완성해야 한다.

실제 얼굴 비교 요청 테스트(아래 파일명을 폴더 안 실제 이름으로 바꿔서 실행):

```bash
python3 db_cli.py match-face --file /tmp/hcc_pc2_faces/PC1_Face_Embedding_Comparison/output/28ea61858d5b49d880d2cadce68f0488.json
```

이 결과는 PC1 DB **전체 등록자와의 유사도 비교**이며, 통신·DB 기능 시험일 뿐 AI 모델 자체의 정확도 시험은 아니다. 기존 실험 데이터 15개가 모두 Unknown일 수 있다.

## 환자·장치·낙상 데이터 기본 사용 예시

```bash
python3 db_cli.py add-patient --id P100 --name '테스트환자' --height 168 --weight 63 --room 301
python3 db_cli.py add-camera --id CAM01 --type CCTV --location '복도'
python3 db_cli.py add-door --id DOOR01 --location '현관'
python3 db_cli.py demo-fall --camera CAM01
python3 db_cli.py demo-door --door DOOR01 --action STATUS
```

`demo-fall`은 **가상 낙상 이벤트만 저장**, `demo-door`는 **PENDING 명령 기록만 생성**한다. 실제 AI/HW 통신은 하지 않는다. `run_db_smoke.py`는 생성했던 시험 데이터를 정리하지만 위 CLI 수동 예제의 데이터는 유지된다.

## Python 서버에서 사용할 때

```python
from hcc_db import HCCDatabase
from validation import read_object

db = HCCDatabase()  # HCC_DB_DSN 읽음
print(db.health())

# 얼굴 인식팀의 등록 JSON -> 환자 ID에 512차원 특징벡터 저장
saved = db.import_registered_face(read_object('/path/to/patient_001.json'))

# 인식 요청 JSON -> DB 전체 등록자에서 최고 유사도 비교
match = db.match_face(read_object('/path/to/<event_id>.json'), threshold=0.5)
print(match['result'], match['person_id'], match['similarity'])

# 최종 도어 제어는 별도 API와 장치 통신 로직에서 수행!
```

이 패키지는 **DB와 Python 데이터 접근 계층**을 구현한 것이고 HTTP 웹서버·GUI·실제 ESP32 모터 제어 코드는 포함하지 않는다. PC1에 API를 붙일 때는 이 메서드를 호출한다. DB에 직접 접근하는 프로그램은 PC1 서버로 제한하는 구성이 좋다.

## 유의사항

- SQL 생성은 PostgreSQL 17 + pgvector 기준으로 작성했으나, 이 파일 작성 환경에는 실행 중인 PostgreSQL 서버가 없어 **실제 DB 통합 시험은 사용자 Ubuntu에서 진행해야 한다**.
- 단위 테스트는 `python3 -m unittest discover -s tests -v`로 확인 가능.
- 기존 SQLite 테스트 DB에서 데이터를 자동 이관하지 않는다. **초기 v0.1 DB는 별도로 생성**한다.
- 이 코드는 교육용 프로토타입이다. 실제 개인정보·생체정보를 처리하는 실운영 환경에는 인증·권한·암호화·백업·삭제 정책이 추가로 필요하다.
- SQL은 최초 초기화용이다. 기존 설치된 스키마에 대해 파일을 수정하고 다시 `init`하는 것은 *마이그레이션이 아니다*. 스키마 변경은 명시적 마이그레이션과 백업을 사용한다.
- 현재 팀 Confluence에서 SR-07은 공식 정의가 확인되지 않았다. 추후 정의 후 확장하자.

## 외부 기술 자료

- PostgreSQL 문서: https://www.postgresql.org/docs/
- pgvector 저장·코사인 비교: https://github.com/pgvector/pgvector
- Python pgvector(psycopg3): https://github.com/pgvector/pgvector-python
- Psycopg3 문서: https://www.psycopg.org/psycopg3/docs/
