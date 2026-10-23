"""YOLO26 Pose + ByteTrack + per-person LSTM fall-candidate pipeline.

Examples:
  python run_fall_detection.py --source assets/MOT17-04-DPM-raw.mp4 --no-show \
      --output results/annotated.mp4 --results-jsonl results/traces.jsonl
  python run_fall_detection.py --model yolo26m-pose.pt --rtsp-source octo

The LSTM score is a fall *candidate* signal.  ``fall_confirmed`` additionally
requires a horizontal-body geometry gate and consecutive evidence for one
persistent YOLO ByteTrack ID.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import config

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", config.OPENCV_FFMPEG_CAPTURE_OPTIONS)

import cv2  # noqa: E402
import torch  # noqa: E402
from ultralytics import YOLO  # noqa: E402

from adapters import observations_from_yolo_result
from central_delivery import (
    AsyncFrameAnalysisDelivery,
    CentralDeliveryError,
    HttpFrameAnalysisPublisher,
    HttpJsonEventPublisher,
    build_fall_event,
    event_deduplication_key,
    make_frame_publisher,
)
from fall_pipeline import PerTrackFallEngine, PoseObservation, TrackJudgment
from lstm_predictor import YoloPoseLstmPredictor

ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"
RESULTS_DIR = ROOT / "results"
RTSP_SOURCES = config.RTSP_SOURCES
COCO17_NAMES = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee",
    "right_knee", "left_ankle", "right_ankle",
)


def resolve_under_root(value: str, default_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = default_dir / path
    return path.resolve()


def resolve_result_path(value: str) -> Path:
    """Resolve a write target only when it remains under this workspace's results/."""
    candidate = (ROOT / Path(value).expanduser()).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    try:
        candidate.relative_to(RESULTS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("output paths must remain inside this workspace's results/ directory") from exc
    return candidate


def build_camera_metadata(camera_id: str, location: str | None) -> dict[str, str]:
    """Return stable camera context for every external frame/event consumer."""
    normalized_id = camera_id.strip()
    if not normalized_id:
        raise ValueError("camera_id must not be empty")
    normalized_location = location.strip() if location and location.strip() else "unspecified"
    return {"camera_id": normalized_id, "location": normalized_location}


def lstm_device_value(device: str | int) -> str:
    """Convert Ultralytics' numeric CUDA device convention for PyTorch."""
    return f"cuda:{device}" if isinstance(device, int) else str(device)


def latency_summary(samples_ms: list[float]) -> dict[str, float | int]:
    """Small dependency-free rolling-report helper for live verification."""
    if not samples_ms:
        return {"count": 0, "avg_ms": 0.0, "p95_ms": 0.0}
    ordered = sorted(samples_ms)
    return {
        "count": len(samples_ms),
        "avg_ms": round(sum(samples_ms) / len(samples_ms), 2),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 2),
    }


def open_capture(source: str | int) -> cv2.VideoCapture:
    """Open a file/RTSP string or a local V4L2 camera index."""
    if isinstance(source, int):
        capture = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if not capture.isOpened():
            capture.release()
            capture = cv2.VideoCapture(source)
    else:
        capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    if not capture.isOpened():
        capture.release()
        capture = cv2.VideoCapture(source) if not isinstance(source, int) else capture
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"cannot open video source: {source}")
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return capture


def observation_to_dict(observation: PoseObservation) -> dict[str, object]:
    landmarks = []
    for index, point in enumerate(observation.keypoints_xy):
        landmarks.append(
            {
                "name": COCO17_NAMES[index],
                "x": float(point[0]),
                "y": float(point[1]),
                "confidence": None if observation.keypoint_confidence is None else float(observation.keypoint_confidence[index]),
            }
        )
    return {
        "track_id": observation.track_id,
        "bbox_xyxy": list(observation.bbox_xyxy),
        "detection_confidence": observation.detection_confidence,
        "landmarks": landmarks,
    }


def frame_analysis_for_main_service(
    record: dict[str, object], pending_events: list[dict[str, object]]
) -> dict[str, object]:
    """Return exactly the existing PC1 ``FrameAnalysis`` HTTP contract.

    The local JSONL record deliberately retains extra diagnostic fields.  PC1
    validates live traffic strictly, so only its documented fields go over the
    wire.  JPEG bytes are added by ``HttpFrameAnalysisPublisher``.
    """
    event = record["event"]
    assert isinstance(event, dict)
    event_fields = (
        "event_type", "camera_id", "track_id", "confirmed_frame_id",
        "confirmed_at", "fall_probability",
    )
    return {
        name: record[name]
        for name in (
            "message_type", "frame_id", "observed_at", "camera",
            "source_size", "coordinate_system", "image", "persons",
            "fall_judgments",
        )
    } | {
        "event": {
            "current_states": event["current_states"],
            "new_events": [
                {name: payload[name] for name in event_fields}
                for payload in pending_events
            ],
        }
    }


def head_label_origin(keypoints_xy, *, frame_width: int, frame_height: int) -> tuple[int, int]:
    """Put the compact track tag just above and right of the nose landmark."""
    nose = keypoints_xy[0]
    x = min(max(round(float(nose[0])) + 6, 0), max(frame_width - 1, 0))
    y = min(max(round(float(nose[1])) - 8, 16), max(frame_height - 4, 16))
    return x, y


def render_base_pose(result) -> cv2.typing.MatLike:
    """Render YOLO pose landmarks and person boxes, but not YOLO class labels."""
    return result.plot(labels=False, boxes=True)


def render_judgments(frame: cv2.typing.MatLike, observations: list[PoseObservation], judgments: list[TrackJudgment]) -> cv2.typing.MatLike:
    canvas = frame.copy()
    by_track = {judgment.track_id: judgment for judgment in judgments}
    colors = {
        "normal": (0, 220, 0),
        "possible_fall": (0, 200, 255),
        "fall_confirmed": (0, 0, 255),
        "recovered": (255, 180, 0),
    }
    frame_height, frame_width = canvas.shape[:2]
    for observation in observations:
        judgment = by_track.get(observation.track_id)
        label_x, label_y = head_label_origin(
            observation.keypoints_xy,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        cv2.putText(
            canvas,
            f"ID {observation.track_id}",
            (label_x, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (255, 255, 0),
            2,
            cv2.LINE_AA,
        )
        if judgment is None or judgment.state.value == "normal":
            continue
        probability = "latched" if judgment.fall_probability is None else f"fall={judgment.fall_probability:.2f}"
        label = f"{judgment.state.value} {probability}"
        color = colors[judgment.state.value]
        cv2.putText(canvas, label, (label_x, min(label_y + 17, frame_height - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.46, color, 2, cv2.LINE_AA)
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description="YOLO Pose joint-sequence fall candidate detector")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--source", help="Local video path")
    source_group.add_argument("--rtsp-source", choices=RTSP_SOURCES, help="Named RTSP source")
    source_group.add_argument("--camera-index", type=int, help="Local USB/V4L2 webcam index, e.g. 2 for /dev/video2")
    parser.add_argument("--camera-id", help="Stable camera identifier; defaults to config.CAMERA_ID")
    parser.add_argument("--camera-location", help="Human-readable installation location; defaults to config.CAMERA_LOCATION")
    parser.add_argument("--model", default=config.POSE_MODEL, help="Pose checkpoint in models/ or an absolute path")
    parser.add_argument("--lstm-model", default=config.LSTM_MODEL, help="LSTM checkpoint in models/ or an absolute path")
    parser.add_argument("--tracker", default=config.TRACKER, help="Ultralytics tracker YAML")
    parser.add_argument("--imgsz", type=int, default=config.IMAGE_SIZE)
    parser.add_argument("--conf", type=float, default=config.DETECTION_CONFIDENCE)
    parser.add_argument("--max-det", type=int, default=config.MAX_DETECTIONS)
    parser.add_argument("--device", default=config.DEVICE, help="auto, cpu, or CUDA index")
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--output", default=config.OUTPUT_PATH, help="Annotated MP4 path under this workspace's results/")
    parser.add_argument("--results-jsonl", default=config.RESULTS_JSONL_PATH, help="Per-frame JSONL path under this workspace's results/")
    parser.add_argument(
        "--stream-publish-url",
        default=config.STREAM_PUBLISH_URL,
        help="Central annotated-video destination: rtsp://... for rtsp-tcp or udp://... for udp-mpegts",
    )
    parser.add_argument(
        "--stream-protocol",
        choices=("rtsp-tcp", "udp-mpegts"),
        default=config.STREAM_PROTOCOL,
        help="Annotated-video transport to the central server (default: rtsp-tcp)",
    )
    parser.add_argument(
        "--event-api-url",
        default=config.EVENT_API_URL, help="Central HTTP(S) endpoint that receives first fall_confirmed events as JSON",
    )
    parser.add_argument(
        "--frame-api-url", default=config.FRAME_API_URL,
        help="HTTP(S) endpoint that receives one raw JPEG and matching analysis JSON",
    )
    parser.add_argument("--frame-max-width", type=int, default=config.FRAME_MAX_WIDTH)
    parser.add_argument("--frame-jpeg-quality", type=int, default=config.FRAME_JPEG_QUALITY)
    parser.add_argument("--frame-max-fps", type=float, default=config.FRAME_MAX_FPS)
    parser.add_argument("--frame-timeout-seconds", type=float, default=config.FRAME_TIMEOUT_SECONDS)
    parser.add_argument("--event-timeout-seconds", type=float, default=config.EVENT_TIMEOUT_SECONDS)
    parser.add_argument("--event-retries", type=int, default=config.EVENT_RETRIES)
    parser.add_argument("--no-show", action="store_true", default=not config.SHOW_PREVIEW)
    parser.add_argument("--possible-consecutive", type=int, default=config.POSSIBLE_CONSECUTIVE)
    parser.add_argument("--confirm-consecutive", type=int, default=config.CONFIRM_CONSECUTIVE)
    parser.add_argument("--recover-consecutive", type=int, default=config.RECOVER_CONSECUTIVE)
    parser.add_argument("--probability-threshold", type=float, default=config.PROBABILITY_THRESHOLD)
    parser.add_argument("--geometry-threshold", type=float, default=config.GEOMETRY_THRESHOLD)
    parser.add_argument("--min-keypoint-confidence", type=float, default=config.MIN_KEYPOINT_CONFIDENCE,
                        help="Reject a pose window if nose/shoulders/hips confidence is below this")
    parser.add_argument("--upright-consecutive", type=int, default=config.UPRIGHT_CONSECUTIVE,
                        help="Consecutive upright pose frames required before a fall can be confirmed")
    parser.add_argument("--no-require-upright-transition", action="store_false", dest="require_upright_transition",
                        help="Allow fall confirmation without observing an upright posture first")
    parser.set_defaults(require_upright_transition=config.REQUIRE_UPRIGHT_TRANSITION)
    parser.add_argument("--no-latch-fall", action="store_false", dest="latch_fall",
                        help="Allow fall_confirmed to change to recovered while the track remains visible")
    parser.set_defaults(latch_fall=config.LATCH_FALL)
    args = parser.parse_args()

    # The deployed project normally uses its configured RTSP camera.  Keep the
    # command-line source choices for one-off videos, USB cameras, and cameras.
    if args.source is None and args.rtsp_source is None and args.camera_index is None:
        args.rtsp_source = config.DEFAULT_RTSP_SOURCE
    if args.rtsp_source and args.rtsp_source not in RTSP_SOURCES:
        parser.error("configured default RTSP source must be a key in RTSP_SOURCES")

    if not 0 < args.conf <= 1 or args.imgsz <= 0 or args.max_det <= 0:
        parser.error("--conf must be in (0, 1]; --imgsz and --max-det must be positive")
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error("--max-frames must be positive")
    if args.event_timeout_seconds <= 0 or args.event_retries < 0:
        parser.error("--event-timeout-seconds must be positive and --event-retries must be non-negative")
    if args.frame_max_width <= 0 or not 1 <= args.frame_jpeg_quality <= 100:
        parser.error("--frame-max-width must be positive and --frame-jpeg-quality must be in [1, 100]")
    if args.frame_max_fps <= 0 or args.frame_timeout_seconds <= 0:
        parser.error("--frame-max-fps and --frame-timeout-seconds must be positive")
    if args.frame_api_url and (args.stream_publish_url or args.event_api_url):
        parser.error("--frame-api-url cannot be combined with legacy stream/event delivery")
    if not 0 <= args.min_keypoint_confidence <= 1:
        parser.error("--min-keypoint-confidence must be in [0, 1]")
    model_path = resolve_under_root(args.model, MODELS_DIR)
    lstm_path = resolve_under_root(args.lstm_model, MODELS_DIR)
    if not model_path.is_file() or not lstm_path.is_file():
        parser.error("both --model and --lstm-model must name existing copied files")
    if args.camera_index is not None:
        if args.camera_index < 0:
            parser.error("--camera-index must be non-negative")
        source: str | int = args.camera_index
    elif args.rtsp_source:
        source = RTSP_SOURCES[args.rtsp_source]
    else:
        source = str(Path(args.source).expanduser().resolve())
    is_rtsp = bool(args.rtsp_source)
    try:
        default_camera_id = args.rtsp_source or (f"usb-camera-{args.camera_index}" if args.camera_index is not None else "local-video")
        camera = build_camera_metadata(
            args.camera_id or config.CAMERA_ID or default_camera_id,
            args.camera_location if args.camera_location is not None else config.CAMERA_LOCATION,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if is_rtsp and urlsplit(source).scheme.lower() not in {"rtsp", "rtsps"}:
        parser.error("named RTSP source is not an RTSP URL")
    if args.device == "auto":
        device: str | int = 0 if torch.cuda.is_available() else "cpu"
    elif args.device.isdigit():
        device = int(args.device)
    else:
        device = args.device

    try:
        output_path = None if args.output is None else resolve_result_path(args.output)
        jsonl_path = None if args.results_jsonl is None else resolve_result_path(args.results_jsonl)
    except ValueError as exc:
        parser.error(str(exc))
    for path in (output_path, jsonl_path):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    model = YOLO(str(model_path))
    predictor = YoloPoseLstmPredictor(lstm_path, device=lstm_device_value(device))
    engine = PerTrackFallEngine(
        predictor,
        probability_threshold=args.probability_threshold,
        geometry_threshold=args.geometry_threshold,
        possible_consecutive=args.possible_consecutive,
        confirm_consecutive=args.confirm_consecutive,
        recover_consecutive=args.recover_consecutive,
        min_keypoint_confidence=args.min_keypoint_confidence,
        latch_fall=args.latch_fall,
        upright_consecutive=args.upright_consecutive,
        require_upright_transition=args.require_upright_transition,
    )
    capture = open_capture(source)
    writer = None
    json_file = None if jsonl_path is None else jsonl_path.open("w", encoding="utf-8")
    event_publisher = (
        None
        if args.event_api_url is None
        else HttpJsonEventPublisher(
            endpoint=args.event_api_url,
            timeout_seconds=args.event_timeout_seconds,
            retries=args.event_retries,
        )
    )
    frame_publisher = (
        None
        if args.frame_api_url is None
        else HttpFrameAnalysisPublisher(
            endpoint=args.frame_api_url,
            max_width=args.frame_max_width,
            jpeg_quality=args.frame_jpeg_quality,
            max_fps=args.frame_max_fps,
            timeout_seconds=args.frame_timeout_seconds,
        )
    )
    frame_delivery = None if frame_publisher is None else AsyncFrameAnalysisDelivery(frame_publisher)
    stream_publisher = None
    delivered_event_keys: set[tuple[str, int, int]] = set()
    next_event_delivery_attempt: dict[tuple[str, int, int], float] = {}
    pending_frame_events: dict[tuple[str, int, int], dict[str, object]] = {}
    frame_id = 0
    started = time.perf_counter()
    inference_samples_ms: list[float] = []
    window_name = "YOLO Pose Fall Candidate (Q / Esc: exit)"
    if not args.no_show:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    print(f"[MODEL] {model_path.name} | [LSTM] {lstm_path.name} | [DEVICE] {device}")
    print(f"[SOURCE] {'RTSP' if is_rtsp else f'USB camera index {source}' if isinstance(source, int) else source}")
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            inference_started = time.perf_counter()
            timestamp_ms = round(capture.get(cv2.CAP_PROP_POS_MSEC)) if not is_rtsp else round((time.perf_counter() - started) * 1000)
            wall_time_ms = time.time_ns() // 1_000_000
            local_time = time.localtime(wall_time_ms / 1000)
            event_at = (
                time.strftime("%Y-%m-%dT%H:%M:%S", local_time)
                + f".{wall_time_ms % 1000:03d}"
                + time.strftime("%z", local_time)
            )
            result = model.track(
                source=frame,
                persist=True,
                tracker=args.tracker,
                conf=args.conf,
                imgsz=args.imgsz,
                max_det=args.max_det,
                device=device,
                verbose=False,
            )[0]
            observations = observations_from_yolo_result(result)
            judgments = engine.update(
                frame_id,
                observations,
                timestamp_ms=timestamp_ms,
                event_at=event_at,
            )
            # Covers model tracking + LSTM/FSM update only; delivery is on
            # workers and intentionally excluded from inference latency.
            inference_samples_ms.append((time.perf_counter() - inference_started) * 1000)
            height, width = frame.shape[:2]
            needs_rendered = output_path is not None or args.stream_publish_url is not None or not args.no_show
            rendered = None
            if needs_rendered:
                rendered = render_judgments(render_base_pose(result), observations, judgments)
                cv2.putText(
                    rendered,
                    f"{model_path.name} | tracks={len(observations)} | frame={frame_id}",
                    (12, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
            record = {
                "message_type": "frame_analysis",
                "frame_id": frame_id,
                "timestamp_ms": timestamp_ms,
                "observed_at": event_at,
                "observed_epoch_ms": wall_time_ms,
                "camera": camera,
                "source_size": [width, height],
                "pose_model": model_path.name,
                "lstm_model": lstm_path.name,
                "coordinate_system": "original_frame_pixels",
                "persons": [observation_to_dict(item) for item in observations],
                "fall_judgments": [item.to_dict() for item in judgments],
                "image": {
                    "format": "jpeg",
                    "source_size": [width, height],
                    "coordinate_system": "original_frame_pixels",
                },
            }
            for judgment in judgments:
                if judgment.state.value != "fall_confirmed":
                    continue
                payload = build_fall_event(
                    judgment,
                    camera=camera,
                    observed_at=event_at,
                    source_size=(width, height),
                )
                key = event_deduplication_key(payload)
                if key not in delivered_event_keys:
                    pending_frame_events.setdefault(key, payload)
            record["event"] = {
                "current_states": [
                    {
                        "track_id": item.track_id,
                        "state": item.state.value,
                        "confirmed_frame_id": item.confirmed_frame_id,
                    }
                    for item in judgments
                ],
                "new_events": [],
            }
            if frame_delivery is not None:
                # Display traffic is latest-only.  It never carries confirmed
                # events, so replacing an old display frame cannot lose one.
                frame_delivery.submit_display(frame, frame_analysis_for_main_service(record, []))
                for key, payload in list(pending_frame_events.items()):
                    if key in delivered_event_keys:
                        continue
                    event_record = frame_analysis_for_main_service(record, [payload])
                    frame_delivery.submit_event(frame, event_record)
                    delivered_event_keys.add(key)  # server event_id makes retries idempotent
                pending_frame_events.clear()
            if json_file is not None:
                json_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            if output_path is not None:
                assert rendered is not None
                if writer is None:
                    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), capture.get(cv2.CAP_PROP_FPS) or 30.0, (width, height))
                    if not writer.isOpened():
                        raise RuntimeError(f"cannot open output writer: {output_path}")
                writer.write(rendered)
            if args.stream_publish_url is not None:
                assert rendered is not None
                if stream_publisher is None:
                    stream_publisher = make_frame_publisher(
                        protocol=args.stream_protocol,
                        destination=args.stream_publish_url,
                        width=width,
                        height=height,
                        fps=capture.get(cv2.CAP_PROP_FPS) or 30.0,
                    )
                stream_publisher.write(rendered)
            if event_publisher is not None:
                for judgment in judgments:
                    if judgment.state.value != "fall_confirmed":
                        continue
                    payload = build_fall_event(
                        judgment,
                        camera=camera,
                        observed_at=event_at,
                        source_size=(width, height),
                    )
                    key = event_deduplication_key(payload)
                    if key in delivered_event_keys or time.monotonic() < next_event_delivery_attempt.get(key, 0.0):
                        continue
                    try:
                        response_status = event_publisher.publish(payload)
                    except CentralDeliveryError as exc:
                        next_event_delivery_attempt[key] = time.monotonic() + 5.0
                        print(f"[WARN] fall event delivery failed for {key}: {exc}")
                    else:
                        delivered_event_keys.add(key)
                        print(f"[EVENT] delivered fall_confirmed {key} HTTP {response_status}")
            if not args.no_show:
                assert rendered is not None
                cv2.imshow(window_name, rendered)
                if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break
            frame_id += 1
            if args.max_frames is not None and frame_id >= args.max_frames:
                break
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if json_file is not None:
            json_file.close()
        if stream_publisher is not None:
            stream_publisher.close()
        if frame_delivery is not None:
            frame_delivery.close()
        cv2.destroyAllWindows()
    delivery_stats = {} if frame_delivery is None else frame_delivery.metrics.snapshot()
    print(f"[DONE] processed_frames={frame_id} elapsed_seconds={time.perf_counter() - started:.2f} "
          f"inference={latency_summary(inference_samples_ms)} delivery={delivery_stats}")


if __name__ == "__main__":
    main()
