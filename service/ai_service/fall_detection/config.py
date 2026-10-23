"""Fall-detection component defaults.

Change this file for this component's stable local settings.  The default
camera can be started without command-line arguments; use command-line
options only for one-off video, USB-camera, or RTSP overrides.
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

# Project's normal camera.  ``python run_fall_detection.py`` uses this source
# and adds this stable context to every payload sent to the Main Service.
DEFAULT_RTSP_SOURCE = "octo"
CAMERA_ID = "camera_1"
CAMERA_LOCATION = "ROOM_1"

# Display and optional local artefacts
SHOW_PREVIEW = True
OUTPUT_PATH = None
RESULTS_JSONL_PATH = None

# Fall-state thresholds
POSSIBLE_CONSECUTIVE = 1
CONFIRM_CONSECUTIVE = 1
RECOVER_CONSECUTIVE = 15
# A confirmed fall becomes this many consecutive horizontal-pose frames before
# it is reported as a sustained post-fall lying state.
LYING_CONSECUTIVE = 5
LYING_GEOMETRY_THRESHOLD = 0.60
LYING_ASPECT_RATIO_THRESHOLD = 1.10
# A person must remain upright for this many frames before a sustained lying
# state is considered recovered.
UPRIGHT_RECOVER_CONSECUTIVE = 60
# Conservative defaults for live CCTV.  The former values accepted unstable
# pose estimates (for example, a person partly occluded by a bag) too easily.
PROBABILITY_THRESHOLD = 0.65
GEOMETRY_THRESHOLD = 0.7
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
FRAME_API_URL = "http://127.0.0.1:8101/api/v1/live/frame"
FRAME_MAX_WIDTH = 640
FRAME_JPEG_QUALITY = 70
FRAME_MAX_FPS = 20.0   #전송 프레임
FRAME_TIMEOUT_SECONDS = 3.0
