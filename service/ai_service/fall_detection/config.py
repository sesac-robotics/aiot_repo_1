"""Fall-detection component defaults.

Change this file for this component's stable local settings.  A video path,
USB camera index, or one-off RTSP URL is intentionally supplied at runtime
through ``run_fall_detection.py`` rather than saved here.
"""

# Models and YOLO tracking
POSE_MODEL = "yolo26m-pose.pt"
LSTM_MODEL = "lstm_fall_model.pth"
TRACKER = "bytetrack.yaml"
IMAGE_SIZE = 640
DETECTION_CONFIDENCE = 0.20
MAX_DETECTIONS = 30
DEVICE = "auto"  # "auto", "cpu", or CUDA index such as "0"

# Named RTSP sources.  Put a confirmed camera URL here only when needed.
# Empty values are deliberately rejected instead of connecting somewhere
# unexpected.
RTSP_SOURCES = {"octo": "rtsp://192.168.14.241:554/stream"}
OPENCV_FFMPEG_CAPTURE_OPTIONS = "rtsp_transport;tcp"

# Display and optional local artefacts
SHOW_PREVIEW = True
OUTPUT_PATH = None
RESULTS_JSONL_PATH = None

# Fall-state thresholds
POSSIBLE_CONSECUTIVE = 1
CONFIRM_CONSECUTIVE = 1
RECOVER_CONSECUTIVE = 15
# Conservative defaults for live CCTV.  The former values accepted unstable
# pose estimates (for example, a person partly occluded by a bag) too easily.
PROBABILITY_THRESHOLD = 0.65
GEOMETRY_THRESHOLD = 0.95
MIN_KEYPOINT_CONFIDENCE = 0.25
UPRIGHT_CONSECUTIVE = 5
REQUIRE_UPRIGHT_TRANSITION = True
LATCH_FALL = False

# Optional Main Service delivery.  Leave both as None for local-only demos.
EVENT_API_URL = None
EVENT_TIMEOUT_SECONDS = 3.0
EVENT_RETRIES = 0
STREAM_PUBLISH_URL = None
STREAM_PROTOCOL = "rtsp-tcp"

# Unified live analysis delivery.  When FRAME_API_URL is set, the AI sends a
# sampled raw JPEG and matching pose/fall JSON together in one HTTP multipart
# request.  Leave it as None for local-only inference.
FRAME_API_URL = None
FRAME_MAX_WIDTH = 640
FRAME_JPEG_QUALITY = 70
FRAME_MAX_FPS = 5.0
FRAME_TIMEOUT_SECONDS = 3.0
