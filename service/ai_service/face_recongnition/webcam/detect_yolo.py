"""单机人脸追踪与优质截图：YOLO、Haar、Arduino V 协议。"""

import argparse
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import serial
from ultralytics import YOLO


# 调参入口。Arduino 也使用短边比例 15%/20% 的圆形滞回。
MODEL_PATH = Path(__file__).resolve().with_name("yolo11n.pt")
# 在仓库 webcam/ 运行时沿用 InsightFace 监听的 data/captures 目录。
_HERE = Path(__file__).resolve().parent
CAPTURE_DIR = (_HERE.parent / "data" / "captures" if _HERE.name == "webcam"
               else _HERE / "captures")
SEND_INTERVAL = 0.10
RECONNECT_INTERVAL = 2.0
FACE_CONFIRM_FRAMES = 3
FACE_LOST_SECONDS = 1.0
CANDIDATE_RETRY_SECONDS = 0.25
BODY_CENTER_SECONDS = 2.0
SCAN_CYCLE_SECONDS = 30.0  # 仅同步 Python 显示状态；扫描运动由 Arduino 负责。
CENTER_ENTER_RATIO = 0.15
CENTER_EXIT_RATIO = 0.20  # 仅用于预览；执行滞回由 Arduino 负责。
EDGE_MARGIN_RATIO = 0.08
EDGE_FORCE_ERROR = 220  # 高于 Arduino 的 20% 退出阈值。
STABLE_TIME = 2.0
COOLDOWN = 3.0
MOVE_THRESHOLD = 20
FACE_REARM_SECONDS = 1.5
FACE_JUMP_RATIO = 1.5
MIN_FACE_PX = 60
MIN_SHARP = 20.0
MIN_EYES = 2  # 仅 --require-eyes 时生效；眼镜佩戴者可关闭。


def center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def area(box):
    return max(1.0, (box[2] - box[0]) * (box[3] - box[1]))


def overlap(a, b):
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    return intersection / (area(a) + area(b) - intersection)


def match_box(boxes, previous, frame_size, allow_new):
    """按位置、大小、重叠度延续原目标；无法证明真实身份。"""
    if not boxes:
        return None
    width, height = frame_size
    if previous is None or allow_new:
        return min(boxes, key=lambda box: math.dist(center(box), (width / 2, height / 2)))
    span = min(width, height)
    scored = []
    for box in boxes:
        distance = math.dist(center(box), center(previous)) / span
        size_ratio = area(box) / area(previous)
        iou = overlap(box, previous)
        if (distance <= 0.25 or iou >= 0.15) and 0.35 <= size_ratio <= 2.8:
            scored.append((distance + abs(math.log(size_ratio)) * 0.08 - iou * 0.20, box))
    return min(scored, key=lambda item: item[0])[1] if scored else None


def control_error(box, frame_size, face=False):
    """两个轴按同一短边缩放，Arduino 才能用欧氏距离表示圆形目标区。"""
    width, height = frame_size
    short = min(width, height)
    cx, cy = center(box)
    dx = round((cx - width / 2) * 1000 / short)
    dy = round((cy - height / 2) * 1000 / short)
    fov_limited = False
    if face:
        mx, my = width * EDGE_MARGIN_RATIO, height * EDGE_MARGIN_RATIO
        left = box[0] < mx
        right = box[2] > width - mx
        top = box[1] < my
        bottom = box[3] > height - my
        # 大脸同时碰到对侧边缘时，无法满足两侧安全边距，禁止来回纠偏。
        if left and right:
            fov_limited = True
        elif left:
            dx = min(dx, -EDGE_FORCE_ERROR)
        elif right:
            dx = max(dx, EDGE_FORCE_ERROR)
        if top and bottom:
            fov_limited = True
        elif top:
            dy = min(dy, -EDGE_FORCE_ERROR)
        elif bottom:
            dy = max(dy, EDGE_FORCE_ERROR)
    return max(-1000, min(1000, dx)), max(-1000, min(1000, dy)), fov_limited


@dataclass
class Decision:
    state: str
    face_box: tuple | None
    body_box: tuple | None
    body_seen: int
    face_seen: int
    dx: int
    dy: int
    fov_limited: bool = False


class TargetController:
    """每次 process 只接收一张新拍摄并完成检测的帧。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.state = "BODY_TRACKING"
        self.resume_state = "BODY_TRACKING"
        self.face_box = None
        self.body_box = None
        self.candidate_box = None
        self.candidate_frames = 0
        self.candidate_retry_after = 0.0
        self.centered_since = None
        self.lost_since = None
        self.scan_since = None

    def _start_candidate(self, face):
        self.resume_state = self.state
        self.state = "FACE_CANDIDATE"
        self.candidate_box = face
        self.candidate_frames = 1
        self.centered_since = None

    def process(self, bodies, faces, frame_size, now):
        width, height = frame_size
        body = match_box(bodies, self.body_box, frame_size, self.body_box is None)
        if body is None and bodies:
            body = match_box(bodies, None, frame_size, True)
        self.body_box = body
        face = None
        if self.state == "FACE_CANDIDATE":
            face = match_box(faces, self.candidate_box, frame_size, False)
            if face is None:
                self.state = self.resume_state
                self.candidate_box = None
                self.candidate_frames = 0
                self.candidate_retry_after = now + CANDIDATE_RETRY_SECONDS
            else:
                self.candidate_box = face
                self.candidate_frames += 1
                if self.candidate_frames >= FACE_CONFIRM_FRAMES:
                    self.face_box = face
                    self.state = "FACE_LOCKED"
                    self.lost_since = None
                    self.scan_since = None
        elif self.state == "FACE_LOCKED":
            face = match_box(faces, self.face_box, frame_size, False)
            if face is None:
                self.state = "FACE_LOST"
                self.lost_since = now
            else:
                self.face_box = face
        elif self.state == "FACE_LOST":
            # 丢脸 1 秒内只允许与原框匹配；偶发误检不会重置丢失计时。
            face = match_box(faces, self.face_box, frame_size, False)
            if face is not None and now >= self.candidate_retry_after:
                self._start_candidate(face)
            elif now - self.lost_since >= FACE_LOST_SECONDS:
                self.state = "SCANNING"
                self.scan_since = now
                self.face_box = None
        elif self.state == "SCANNING":
            face = match_box(faces, None, frame_size, True)
            if face is not None and now >= self.candidate_retry_after:
                self._start_candidate(face)
            elif now - self.scan_since >= SCAN_CYCLE_SECONDS:
                # Uno 自行完成扫描并回 90°；不依赖该显示计时来控制硬件。
                self.state = "BODY_TRACKING"
                self.centered_since = None
        else:  # BODY_TRACKING
            face = match_box(faces, self.face_box, frame_size, True)
            if face is not None and now >= self.candidate_retry_after:
                self._start_candidate(face)
            elif body is not None:
                error_x, error_y, _ = control_error(body, frame_size)
                distance = math.hypot(error_x, error_y)
                if distance <= CENTER_ENTER_RATIO * 1000:
                    if self.centered_since is None:
                        self.centered_since = now
                    elif now - self.centered_since >= BODY_CENTER_SECONDS:
                        self.state = "SCANNING"
                        self.scan_since = now
                else:
                    self.centered_since = None
            else:
                self.centered_since = None

        # face=1 必须来自当前帧真实检测；候选期间发送零误差来暂停舵机。
        face_seen = int(face is not None and self.state in ("FACE_CANDIDATE", "FACE_LOCKED"))
        dx = dy = 0
        fov_limited = False
        if self.state == "FACE_LOCKED" and face_seen:
            dx, dy, fov_limited = control_error(face, frame_size, face=True)
        elif self.state in ("BODY_TRACKING", "SCANNING") and body is not None:
            # 扫描期间 Uno 忽略身体误差；回位后立刻有最新身体坐标可用。
            dx, dy, _ = control_error(body, frame_size)
        return Decision(self.state, face if face_seen else None, body,
                        int(bool(bodies)), face_seen, dx, dy, fov_limited)


def validate_devices(camera_arg, serial_port, parser):
    if os.name != "posix":
        return
    camera_text, serial_text = str(camera_arg), str(serial_port)
    if serial_text.lower().startswith(("/dev/video", "/dev/v4l/")):
        parser.error("--serial-port 指向摄像头，请指定 Arduino 串口")
    if camera_text.lower().startswith(("/dev/tty", "com")):
        parser.error("--camera 指向串口，请指定摄像头")
    if camera_text.startswith("/dev/") and serial_text.startswith("/dev/"):
        if os.path.realpath(camera_text) == os.path.realpath(serial_text):
            parser.error("摄像头和串口不能是同一设备")


def open_arduino(serial_port):
    kwargs = dict(baudrate=115200, timeout=0, write_timeout=0.1)
    if os.name == "posix":
        kwargs["exclusive"] = True
    try:
        return serial.Serial(serial_port, **kwargs)
    except TypeError:
        kwargs.pop("exclusive", None)
        return serial.Serial(serial_port, **kwargs)


def detect_boxes(model, detector, frame):
    height, width = frame.shape[:2]
    bodies = []
    for result in model(frame, classes=[0], conf=0.6, verbose=False):
        for box in result.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            bounds = (max(0, x1), max(0, y1), min(width - 1, x2), min(height - 1, y2))
            if bounds[0] < bounds[2] and bounds[1] < bounds[3]:
                bodies.append(bounds)
    # 全画面找脸：没有完整人体框时仍可确认和追踪。
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    found = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
    faces = [(int(x), int(y), int(x + w), int(y + h)) for x, y, w, h in found]
    return bodies, faces


def new_capture_state(last_capture=float("-inf")):
    """同一次到访只保存一张优质图，离开 1.5 秒后重新允许拍摄。"""
    return {"start": None, "anchor": None, "best": None, "best_score": 0.0,
            "captured": False, "last_seen": float("-inf"), "last_center": None,
            "last_capture": last_capture}


def reset_count(state):
    state.update(start=None, anchor=None, best=None, best_score=0.0)


def check_quality(frame, face_box, eye_detector=None):
    """沿用 GitHub detect_yolo.py 的脸宽与 Laplacian 清晰度筛选。"""
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = face_box
    x1, x2 = max(0, int(x1)), min(width, int(x2))
    y1, y2 = max(0, int(y1)), min(height, int(y2))
    face_width = x2 - x1
    metrics = {"width": face_width, "sharp": 0.0, "eyes": None}
    if face_width < MIN_FACE_PX or y2 <= y1:
        return False, "Come closer", 0.0, metrics
    crop = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(cv2.resize(crop, (112, 112)), cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    metrics["sharp"] = round(sharp, 1)
    if sharp < MIN_SHARP:
        return False, "Hold still - image blurry", sharp, metrics
    if eye_detector is not None:
        upper = crop[:max(1, crop.shape[0] * 3 // 5)]
        upper_gray = cv2.cvtColor(upper, cv2.COLOR_BGR2GRAY)
        eyes = len(eye_detector.detectMultiScale(
            upper_gray, scaleFactor=1.1, minNeighbors=5,
            minSize=(max(8, crop.shape[1] // 8),) * 2))
        metrics["eyes"] = eyes
        if eyes < MIN_EYES:
            return False, "Show both eyes", sharp, metrics
    return True, "", sharp, metrics


def capture_step(frame, face_box, people_count, state, now, aligned,
                 fov_limited=False, eye_detector=None):
    """仅消费 FACE_LOCKED 的当前帧；稳定期保留最清晰原图。"""
    if face_box is None:
        reset_count(state)
        if now - state["last_seen"] >= FACE_REARM_SECONDS:
            last_capture = state["last_capture"]
            state.update(new_capture_state(last_capture))
        return None, "Waiting for locked face", None, None

    state["last_seen"] = now
    face_center = center(face_box)
    face_width = face_box[2] - face_box[0]
    previous = state["last_center"]
    if previous is not None and math.dist(face_center, previous) > face_width * FACE_JUMP_RATIO:
        last_capture = state["last_capture"]
        state.update(new_capture_state(last_capture))
        state["last_seen"] = now
    state["last_center"] = face_center

    if people_count > 1:
        reset_count(state)
        return None, "Only one person", None, None
    if state["captured"]:
        return None, "Captured - step away to scan again", None, None
    if fov_limited:
        reset_count(state)
        return None, "Step back - face outside field of view", None, None
    if not aligned:
        reset_count(state)
        return None, "Align face inside blue circle", None, None

    quality_ok, guide, score, metrics = check_quality(frame, face_box, eye_detector)
    if not quality_ok:
        reset_count(state)
        return None, guide, None, metrics
    if state["anchor"] is None or math.dist(face_center, state["anchor"]) > MOVE_THRESHOLD:
        reset_count(state)
        state["anchor"] = face_center
    if state["start"] is None:
        state["start"] = now
    if score > state["best_score"]:
        state["best_score"] = score
        state["best"] = frame.copy()
    remaining = STABLE_TIME - (now - state["start"])
    if remaining > 0:
        return None, "Hold still", remaining, metrics
    if now - state["last_capture"] < COOLDOWN:
        return None, "Capture cooldown", None, metrics
    return state["best"], "Captured", None, metrics


def save_capture(frame, out_dir):
    """先写临时文件再原子改名，避免识别服务读取未完成的 JPEG。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = out_dir / datetime.now().strftime("face_%Y%m%d_%H%M%S_%f.jpg")
    temporary = filename.with_suffix(".writing")
    ok, jpeg = cv2.imencode(".jpg", frame)
    if not ok:
        raise OSError("JPEG 编码失败")
    try:
        temporary.write_bytes(jpeg.tobytes())
        temporary.replace(filename)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return filename


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", default="0", help="摄像头编号或设备路径")
    parser.add_argument("--serial-port", default="COM3" if os.name == "nt" else "/dev/ttyACM0")
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    parser.add_argument("--out", type=Path, default=CAPTURE_DIR, help="优质截图保存目录")
    parser.add_argument("--require-eyes", action="store_true", help="截图前要求检测到两只眼睛")
    parser.add_argument("--debug", action="store_true", help="预览画面显示清晰度等指标")
    parser.add_argument("--door-pulse", action="store_true", help="连接后发送一次开门脉冲")
    args = parser.parse_args()
    validate_devices(args.camera, args.serial_port, parser)
    if not args.model.is_file():
        parser.error(f"找不到 YOLO 模型：{args.model}")
    model = YOLO(str(args.model))
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if detector.empty():
        raise RuntimeError("无法加载 OpenCV Haar 人脸检测器")
    eye_detector = None
    if args.require_eyes:
        eye_detector = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml")
        if eye_detector.empty():
            raise RuntimeError("无法加载 OpenCV 眼睛检测器")
    source = int(args.camera) if str(args.camera).isdecimal() else args.camera
    backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_V4L2
    cap = cv2.VideoCapture(source, backend) if isinstance(source, int) else cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开摄像头：{args.camera}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    controller = TargetController()
    port = None
    reply_buffer = b""
    next_reconnect = 0.0
    last_send = 0.0
    pending_door_pulse = args.door_pulse
    capture_state = new_capture_state()
    last_guide = None
    try:
        while True:
            now = time.monotonic()
            if port is None and now >= next_reconnect:
                try:
                    port = open_arduino(args.serial_port)
                    time.sleep(2)  # Uno 打开串口时重启。
                    controller.reset()
                    reset_count(capture_state)
                    print(f"Arduino 已连接：{args.serial_port} @ 115200")
                except (serial.SerialException, OSError) as error:
                    print(f"Arduino 未连接：{error}")
                    next_reconnect = time.monotonic() + RECONNECT_INTERVAL
            ok, frame = cap.read()
            if not ok:
                print("摄像头读取失败，停止追踪")
                break
            raw_frame = frame.copy()
            height, width = frame.shape[:2]
            bodies, faces = detect_boxes(model, detector, frame)
            frame_time = time.monotonic()
            decision = controller.process(bodies, faces, (width, height), frame_time)

            locked_face = decision.face_box if decision.state == "FACE_LOCKED" else None
            # 到达中心圆且边缘安全后才开始倒计时；追踪、扫描状态机保持原样。
            aligned = math.hypot(decision.dx, decision.dy) <= CENTER_ENTER_RATIO * 1000
            to_save, guide, remaining, metrics = capture_step(
                raw_frame, locked_face, len(bodies), capture_state, frame_time,
                aligned=aligned, fov_limited=decision.fov_limited,
                eye_detector=eye_detector)
            if to_save is not None:
                try:
                    name = save_capture(to_save, args.out)
                    capture_state["captured"] = True
                    capture_state["last_capture"] = frame_time
                    print(f"优质截图：{name}")
                except OSError as error:
                    guide = f"Screenshot failed: {error}"
                    print(guide)
            if guide != last_guide:
                print(f"[CAPTURE] {guide}")
                last_guide = guide

            for box in bodies:
                cv2.rectangle(frame, box[:2], box[2:], (0, 180, 0), 1)
            for box in faces:
                cv2.rectangle(frame, box[:2], box[2:], (150, 150, 0), 1)
            if decision.face_box:
                cv2.rectangle(frame, decision.face_box[:2], decision.face_box[2:], (0, 255, 255), 2)
            cv2.circle(frame, (width // 2, height // 2), round(min(width, height) * CENTER_ENTER_RATIO), (255, 0, 0), 1)
            cv2.circle(frame, (width // 2, height // 2), round(min(width, height) * CENTER_EXIT_RATIO), (180, 0, 180), 1)
            margin_x, margin_y = round(width * EDGE_MARGIN_RATIO), round(height * EDGE_MARGIN_RATIO)
            cv2.rectangle(frame, (margin_x, margin_y),
                          (width - margin_x, height - margin_y), (80, 80, 80), 1)
            cv2.putText(frame, f"{decision.state} dx={decision.dx} dy={decision.dy}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.putText(frame, guide, (10, 60), cv2.FONT_HERSHEY_SIMPLEX,
                        0.58, (0, 180, 0) if remaining is not None else (0, 0, 255), 2)
            if remaining is not None:
                cv2.putText(frame, str(math.ceil(remaining)), (10, 145),
                            cv2.FONT_HERSHEY_SIMPLEX, 2.5, (0, 255, 255), 4)
            if args.debug and metrics:
                cv2.putText(frame, str(metrics), (10, height - 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            if decision.fov_limited:
                cv2.putText(frame, "FOV LIMIT", (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            # 一帧最多发送一次；没有重发缓存，Arduino 的 3 包来自不同采集帧。
            if frame_time - last_send >= SEND_INTERVAL:
                message = (f"V,{decision.body_seen},{decision.face_seen},"
                           f"{decision.dx},{decision.dy},{int(pending_door_pulse)}\n")
                if port is not None:
                    try:
                        port.write(message.encode("ascii"))
                        pending_door_pulse = False
                        print(f"Arduino <- {message.strip()}")
                        if port.in_waiting:
                            reply_buffer += port.read(port.in_waiting)
                            while b"\n" in reply_buffer:
                                reply, reply_buffer = reply_buffer.split(b"\n", 1)
                                print("Arduino -> " + reply.decode("ascii", errors="replace").strip())
                            if len(reply_buffer) > 256:
                                reply_buffer = b""
                    except (serial.SerialException, OSError) as error:
                        print(f"串口断开：{error}")
                        try:
                            port.close()
                        except OSError:
                            pass
                        port = None
                        reply_buffer = b""
                        controller.reset()
                        reset_count(capture_state)
                        next_reconnect = frame_time + RECONNECT_INTERVAL
                last_send = frame_time
            cv2.imshow("Person + Face Tracking", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("o"):
                pending_door_pulse = True
                print("已排队单次开门脉冲")
            elif key == ord("q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        if port is not None:
            try:
                port.write(b"V,0,0,0,0,0\n")
            except (serial.SerialException, OSError):
                pass
            port.close()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
