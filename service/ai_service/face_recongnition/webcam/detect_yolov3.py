"""Track a detected face, falling back to the person's body center.

PC2에서 추적·촬영하고 좌표는 PC1으로 TCP 전송한다. MCU 연결은 PC1에서 담당한다.
촬영: 사람 1명 + 얼굴 품질 통과 + 가만히 3초 -> 가장 선명한 프레임 1장 저장.
저장 후에는 얼굴이 사라질 때까지 재촬영하지 않고, 나갔다 들어오면 다시 3초를 센다.
"""

import argparse
import math
import os
import time
from coordinate_sender import CoordinateSender
from datetime import datetime
from pathlib import Path

import cv2
from ultralytics import YOLO


def find_root():
    """프로젝트 최상위 폴더: 이 파일의 상위 폴더 중 webcam 폴더가 있는 곳."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "webcam").is_dir():
            return parent
    return here.parent.parent


ROOT = find_root()
MODEL_PATH = ROOT / "webcam" / "yolo11n.pt"
CAPTURE_DIR = ROOT / "data" / "captures"
SEND_INTERVAL = 0.1  # At most 10 updates per second.

# 촬영 조건 (카메라/조명에 맞게 --debug 로 수치를 보며 조정)
COUNT_SEC = 3.0      # 가만히 있어야 하는 시간(카운트다운)
MOVE_THRESHOLD = 100  # 얼굴 중심이 이 픽셀 이상 움직이면 카운트 다시 시작
ALIGN_THRESHOLD_X = 200  # 화면 중심에서 좌우 200픽셀 이내
ALIGN_THRESHOLD_Y = 200  # 화면 중심에서 상하 200픽셀 이내
LOST_SEC = 1.5       # 얼굴이 이 시간 이상 안 보이면 '나갔다'고 판단 (한두 프레임 놓침 방지)
JUMP_RATIO = 0.8     # 한 프레임에 얼굴 너비의 80% 이상 위치가 튀면 '다른 사람'으로 판단
MIN_FACE_PX = 120    # 최소 얼굴 너비(px)
MIN_SHARP = 40.0     # 최소 선명도
MIN_EYES = 2         # 얼굴 윗부분에서 보여야 하는 눈 개수 (손으로 가리면 줄어듦)


def choose_person(people, screen_center):
    """Choose the detected person closest to the center of the image."""
    if not people:
        return None
    return min(
        people,
        key=lambda person: math.dist(person[4:6], screen_center),
    )


def find_face(frame, person, detector):
    """Return the largest face box and center in full-frame coordinates."""
    x1, y1, x2, y2 = person[:4]
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    faces = detector.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
    )
    if len(faces) == 0:
        return None
    fx, fy, fw, fh = map(int, max(faces, key=lambda face: face[2] * face[3]))
    face_box = (x1 + fx, y1 + fy, x1 + fx + fw, y1 + fy + fh)
    face_center = (x1 + fx + fw // 2, y1 + fy + fh // 2)
    return face_box, face_center


def new_capture_state():
    """촬영 상태. start=카운트 시작 시각, anchor=카운트 시작 위치, best=카운트 중 가장 좋은 프레임."""
    return {"start": None, "anchor": None, "best": None, "best_score": 0.0,
            "captured": False, "last_seen": 0.0, "last_center": None}


def reset_count(state):
    """카운트만 초기화 (촬영 완료 여부는 유지)."""
    state.update(start=None, anchor=None, best=None, best_score=0.0)


def check_quality(frame, face_box, eye_cascade):
    """얼굴 품질 검사. 반환: (통과 여부, 안내 문구, 점수, 수치)."""
    x1, y1, x2, y2 = [max(int(v), 0) for v in face_box]
    crop = frame[y1:y2, x1:x2]
    sharp, eyes = 0.0, None
    if crop.size:  # 크기에 따라 값이 달라지지 않도록 112x112 로 맞춘 뒤 선명도 계산
        gray = cv2.cvtColor(cv2.resize(crop, (112, 112)), cv2.COLOR_BGR2GRAY)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if eye_cascade is not None:  # 얼굴 윗부분에서 눈 찾기
            upper = cv2.cvtColor(crop[: crop.shape[0] * 3 // 5], cv2.COLOR_BGR2GRAY)
            eyes = len(eye_cascade.detectMultiScale(upper, 1.1, 5, minSize=(crop.shape[1] // 8,) * 2))
    metrics = {"width": x2 - x1, "eyes": eyes, "sharp": round(sharp, 1)}
    if x2 - x1 < MIN_FACE_PX:
        return False, "Come closer", 0.0, metrics
    if eyes is not None and eyes < MIN_EYES:
        return False, "Do not cover your face", 0.0, metrics
    if sharp < MIN_SHARP:
        return False, "Hold still", 0.0, metrics
    return True, "", sharp, metrics


def capture_step(frame, face_box, people_count, state, eye_cascade, now=None):
    """프레임마다 호출. frame 은 화면 표시용 그림이 들어가기 전의 원본이어야 한다.
    반환: (저장할 프레임 또는 None, 안내 문구, 남은 초 또는 None, 품질 수치)."""
    now = time.monotonic() if now is None else now
    if people_count > 1:  # 출입 관리는 1명만 허용
        state["last_seen"] = now
        reset_count(state)
        return None, "Only one person", None, None
    if face_box is None:
        reset_count(state)  # 카운트 중 얼굴이 사라지면 카운트는 처음부터
        if now - state["last_seen"] > LOST_SEC:
            state.update(new_capture_state())  # 충분히 오래 사라짐 -> 다음 사람을 위해 초기화
        return None, "Waiting for face", None, None

    state["last_seen"] = now
    x1, y1, x2, y2 = face_box
    center, width = ((x1 + x2) / 2, (y1 + y2) / 2), x2 - x1
    last = state["last_center"]
    if last is not None and math.dist(center, last) > width * JUMP_RATIO:
        state.update(new_capture_state())  # 화면에서 안 나가고 사람이 바뀐 경우
        state["last_seen"] = now
    state["last_center"] = center

    if state["captured"]:
        return None, "Done - step away to scan again", None, None

    # 얼굴 중심과 실제 영상 중심의 차이 계산
    frame_height, frame_width = frame.shape[:2]
    face_cx = (x1 + x2) // 2
    face_cy = (y1 + y2) // 2

    offset_x = face_cx - frame_width // 2
    offset_y = face_cy - frame_height // 2

    aligned = (
        abs(offset_x) <= ALIGN_THRESHOLD_X
        and abs(offset_y) <= ALIGN_THRESHOLD_Y
    )

    # 정렬되지 않았으면 촬영 카운트다운 초기화
    if not aligned:
        reset_count(state)
        return None, "Align face to center", None, None

    ok, text, score, metrics = check_quality(frame, face_box, eye_cascade)

    if not ok:  # 조건이 깨지면 카운트 리셋
        reset_count(state)
        return None, text, None, metrics

    if state["anchor"] is None or math.dist(center, state["anchor"]) > MOVE_THRESHOLD:
        reset_count(state)  # 움직였으면 그 자리에서 카운트 다시 시작
        state["anchor"] = center
    if state["start"] is None:
        state["start"] = now
    if score > state["best_score"]:  # 카운트 중 가장 선명한 프레임 보관
        state["best_score"], state["best"] = score, frame
    remaining = COUNT_SEC - (now - state["start"])
    if remaining > 0:
        return None, "Hold still", remaining, metrics
    best = state["best"]
    state.update(captured=True, start=None, anchor=None, best=None, best_score=0.0)
    return best, "Captured", None, metrics


def save_capture(frame, out_dir):
    """임시 파일(.writing)에 저장 후 이름을 바꿔 공개 -> InsightFace 가 저장 중인 이미지를 읽지 않는다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = out_dir / datetime.now().strftime("face_%Y%m%d_%H%M%S_%f.jpg")
    temporary = filename.with_suffix(".writing")
    encoded_ok, jpeg = cv2.imencode(".jpg", frame)
    if not encoded_ok:
        raise OSError("JPEG 인코딩 실패")
    try:
        temporary.write_bytes(jpeg.tobytes())
        temporary.replace(filename)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return filename


def draw_capture_overlay(frame, guide, remaining, metrics, debug):
    """안내 문구와 카운트다운 숫자를 그림. (cv2.putText 는 한글 미지원이라 영어 사용)"""
    good = remaining is not None or guide.startswith(("Captured", "Done"))
    color = (0, 200, 0) if good else (0, 0, 255)
    cv2.putText(frame, guide, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    if remaining is not None:
        cv2.putText(frame, str(math.ceil(remaining)), (10, 150),
                    cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 255, 255), 5)
    if debug and metrics:
        cv2.putText(frame, str(metrics), (10, frame.shape[0] - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)


def read_tcp_settings():
    """설정 우선순위: 실행 옵션 > 환경변수 > 프로젝트 .env > 기본값."""
    settings = {
        "PC1_TRACKING_HOST": "192.168.15.130",
        "PC1_TRACKING_PORT": "8102",
    }
    env_path = ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key in settings:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                settings[key] = value
    for key in settings:
        if key in os.environ:
            settings[key] = os.environ[key]
    return settings


def main():
    tcp_settings = read_tcp_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=0, help="V4L2 camera index")
    parser.add_argument("--model", type=Path, default=MODEL_PATH, help="YOLO model path")
    parser.add_argument("--out", type=Path, default=CAPTURE_DIR, help="캡처 저장 폴더")
    parser.add_argument("--no-eye-check", dest="eye_check", action="store_false",
                        help="눈 검사 끄기 (안경 등으로 자꾸 실패할 때)")
    parser.add_argument("--debug", action="store_true", help="화면에 품질 수치 표시")
    parser.add_argument(
    "--pc1-host",
    default=tcp_settings["PC1_TRACKING_HOST"],
    help="좌표를 받을 PC1 IP 또는 도메인",
    )
    parser.add_argument(
        "--pc1-port",
        type=int,
        default=tcp_settings["PC1_TRACKING_PORT"],
        help="PC1 TCP 수신 포트",
    )

    parser.add_argument("--no-tcp", action="store_true", help="TCP 전송 없이 로컬 테스트")
    args = parser.parse_args()


    # PC1 주소와 포트는 함께 지정해야 합니다.
    if (args.pc1_host is None) != (args.pc1_port is None):
        parser.error("--pc1-host와 --pc1-port를 함께 지정하세요.")

    if args.pc1_port is not None and not 1 <= args.pc1_port <= 65535:
        parser.error("--pc1-port는 1~65535 범위여야 합니다.")

    if not args.no_tcp and not args.pc1_host.strip():
        parser.error("PC1 TCP 주소가 비어 있습니다.")

    model = YOLO(str(args.model))

    face_detector = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    if face_detector.empty():
        raise RuntimeError("Cannot load OpenCV face detector")
    eye_detector = None
    if args.eye_check:
        eye_detector = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml"
        )
        if eye_detector.empty():
            raise RuntimeError("Cannot load OpenCV eye detector")
    cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera {args.camera}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    last_send = 0.0
    capture_state = new_capture_state()
    last_guide = None
    coordinate_sender = None

    try:
        if not args.no_tcp:
            coordinate_sender = CoordinateSender(
                host=args.pc1_host,
                port=args.pc1_port,
            )
            coordinate_sender.start()
        else:
            print(
                "[TCP] Disabled; local preview only",
                flush=True,
            )

        while True:
            now = time.monotonic()

            ok, frame = cap.read()
            if not ok:
                print("Camera frame unavailable")
                break
            raw = frame.copy()  # 촬영용 원본 (화면 표시용 그림이 들어가지 않게)

            height, width = frame.shape[:2]
            screen_center = (width // 2, height // 2)
            results = model(frame, classes=[0], conf=0.6, verbose=False)
            people = []
            for result in results:
                for box in result.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    x1, x2 = max(0, x1), min(width - 1, x2)
                    y1, y2 = max(0, y1), min(height - 1, y2)
                    if x1 >= x2 or y1 >= y2:
                        continue
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    people.append((x1, y1, x2, y2, cx, cy, float(box.conf[0])))

            person = choose_person(people, screen_center)
            face_box = None
            if person is None:
                # 새 Main Server 协议：无目标时四个框坐标统一为 -1。
                pc1_message = f"TRACK,0,0,-1,-1,-1,-1,{width},{height}\n"
                cv2.putText(frame, "No person detected", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            else:
                x1, y1, x2, y2, body_cx, body_cy, confidence = person
                face = find_face(frame, person, face_detector)
                if face is None:
                    target_cx, target_cy = body_cx, body_cy
                    track_box = (x1, y1, x2, y2)
                    tracking_mode = "BODY"
                else:
                    face_box, (target_cx, target_cy) = face
                    track_box = face_box
                    tracking_mode = "FACE"
                    cv2.rectangle(frame, face_box[:2], face_box[2:], (255, 255, 0), 2)

                dx, dy = target_cx - screen_center[0], target_cy - screen_center[1]

                # PC1 发送完整画面中的 Bounding Box；中心点只用于本地显示和截图。
                pc1_message = (
                    f"TRACK,1,{int(face is not None)},{track_box[0]},{track_box[1]},"
                    f"{track_box[2]},{track_box[3]},{width},{height}\n"
                )
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.circle(frame, (target_cx, target_cy), 5, (0, 0, 255), -1)
                cv2.line(frame, screen_center, (target_cx, target_cy), (0, 255, 255), 2)
                cv2.putText(frame, f"{tracking_mode} {confidence:.2f}  dx:{dx} dy:{dy}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            # 촬영 판단: 사람 수, 얼굴 위치, 품질을 보고 카운트/저장을 결정한다.
            to_save, guide, remaining, metrics = capture_step(
                raw, face_box, len(people), capture_state, eye_detector
            )
            if to_save is not None:
                try:
                    print(f"Screenshot saved: {save_capture(to_save, args.out)}")
                except OSError as exc:
                    print(f"Screenshot failed: {exc}")
                    capture_state["captured"] = False  # 저장 실패 -> 다시 시도
            if guide != last_guide:  # 안내 문구가 바뀔 때만 로그 출력
                print(f"[CAPTURE] {guide}")
                last_guide = guide
            draw_capture_overlay(frame, guide, remaining, metrics, args.debug)

            cv2.drawMarker(frame, screen_center, (255, 0, 0),
                           cv2.MARKER_CROSS, 25, 2)

            now = time.monotonic()
            if now - last_send >= SEND_INTERVAL:
                if coordinate_sender is not None:
                    coordinate_sender.publish(pc1_message)
                if args.debug or coordinate_sender is None:
                    print(f"[TRACK] {pc1_message.strip()}", flush=True)

                last_send = now

            cv2.imshow("Person + Face Tracking", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if coordinate_sender is not None:
                coordinate_sender.close()
        finally:
            cap.release()
            cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
