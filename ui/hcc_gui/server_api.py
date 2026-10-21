"""GUI ↔ 중앙 서버 연결 도우미. (표준 라이브러리만 사용, 추가 설치 없음)

예전에 psycopg2로 hospital_db에 직접 하던 일을 서버 API로 한다.
필드 이름은 GUI patients 표와 똑같다: p_num, name, gender, age, blood_type, height, weight, disease, meds

서버 주소 바꾸기 (서버가 다른 PC에 있을 때):
    HCC_SERVER=192.168.0.10 python3 patient_monitoring.py
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

SERVER = os.getenv("HCC_SERVER", "127.0.0.1")       # 서버 PC IP
API_PORT = int(os.getenv("HCC_API_PORT", "8101"))   # 환자 등록·조회·삭제 (HTTP)
GUI_TCP_PORT = int(os.getenv("HCC_GUI_PORT", "8080"))  # FALL / DOOR_IS_OPEN / RFID_IN:... 실시간 알림 (TCP)
API = f"http://{SERVER}:{API_PORT}/api/v1"

COLUMNS = ["p_num", "name", "gender", "age", "blood_type", "height", "weight", "disease", "meds"]


class ServerError(Exception):
    pass


def _request(method, path, body=None, timeout=2.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail")
        except Exception:
            detail = e.reason
        raise ServerError(f"{e.code} {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ServerError(f"서버 연결 실패 ({SERVER}:{API_PORT}): {e}") from None


def list_patients(timeout=2.0):
    """[(p_num, name, gender, age, blood_type, height, weight, disease, meds), ...]  예전 SELECT 결과와 같은 모양."""
    items = _request("GET", "/patients", timeout=timeout)["items"]
    return [tuple(p.get(c) for c in COLUMNS) for p in items]


def save_patient(p_num, name, gender, age, blood_type, height, weight, disease, meds):
    """예전 INSERT ... ON CONFLICT (p_num) DO UPDATE 와 같음. 같은 번호면 덮어쓴다."""
    return _request("PUT", f"/patients/{p_num}", {
        "name": name, "gender": gender, "age": age, "blood_type": blood_type,
        "height": height, "weight": weight, "disease": disease, "meds": meds})


def delete_patient(p_num):
    """예전 DELETE FROM patients WHERE p_num = ... 와 같음. 얼굴 등록과 카드 연결도 같이 해제된다."""
    return _request("DELETE", f"/patients/{urllib.parse.quote(str(p_num))}")
