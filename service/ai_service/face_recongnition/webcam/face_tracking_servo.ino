#include <Servo.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>

// 单机 V 协议：V,body,face,dx,dy,doorPulse\n，115200 baud。
// dx/dy 按画面短边归一化；正值表示目标在画面右/下方。
const byte PAN_PIN = 9;
const byte TILT_PIN = 10;
const byte DOOR_PIN = 11;

// 实测若追踪方向相反，只改对应方向常量。
const int PAN_DIRECTION = -1;
const int TILT_DIRECTION = -1;
const int PAN_MIN = 20;
const int PAN_MAX = 160;
const int TILT_MIN = 30;
const int TILT_MAX = 150;
const int TILT_HOME_ANGLE = 90;

// 沿用旧代码的 TILT_DIRECTION=-1，推定角度减小为向下；现场必须实测。
// 若实际相反，四项成组修改，并同步核对上面的 TILT_DIRECTION。
const int TILT_DOWN_DIRECTION = -1;
const int TILT_UP_DIRECTION = 1;
const int SCAN_BOTTOM_ANGLE = 30;
const int SCAN_TOP_ANGLE = 150;

const int CENTER_ENTER_ERROR = 150;  // 短边 15%，圆形停止阈值。
const int CENTER_EXIT_ERROR = 200;   // 短边 20%，圆形重启阈值。
const byte REVERSE_CONFIRM_PACKETS = 2;
const unsigned long TRACK_INTERVAL_MS = 200;
const unsigned long SCAN_INTERVAL_MS = 100;
const unsigned long CENTER_HOLD_MS = 2000;
const unsigned long FACE_LOSS_HOLD_MS = 1000;
const unsigned long CANDIDATE_GAP_MS = 850;
const unsigned long CANDIDATE_RETRY_MS = 250;
const unsigned long LINK_TIMEOUT_MS = 1500;
const unsigned long DOOR_HOLD_MS = 5000;
const int TRACK_STEP_DEG = 1;
const int SCAN_STEP_DEG = 1;
const byte FACE_CONFIRM_PACKETS = 3;

enum TrackingState {
  BODY_TRACKING,
  FACE_CANDIDATE,
  FACE_LOCKED,
  FACE_LOST,
  SCAN_TO_BOTTOM,
  SCAN_UP,
  RETURN_HOME
};

Servo panServo;
Servo tiltServo;
Servo doorServo;
TrackingState state = BODY_TRACKING;
TrackingState resumeState = BODY_TRACKING;
int panAngle = 90;
int tiltAngle = TILT_HOME_ANGLE;
int errorX = 0;
int errorY = 0;
bool bodySeen = false;
bool faceSeen = false;
bool trackingActive = false;
bool trackingPacketPending = false;
bool previousDoorPulse = false;
bool doorOpen = false;
bool scanConfigValid = true;
byte candidateCount = 0;
unsigned long candidateLastMs = 0;
unsigned long candidateRejectedMs = 0;
unsigned long lastPacketMs = 0;
unsigned long lastTrackMs = 0;
unsigned long lastScanMs = 0;
unsigned long centeredSinceMs = 0;
unsigned long lostSinceMs = 0;
unsigned long lastLockedFaceMs = 0;
unsigned long lastFaceObservedMs = 0;
unsigned long doorOpenedMs = 0;
unsigned long lastAckMs = 0;
char line[64];
byte lineLength = 0;
bool lineOverflow = false;

struct AxisControl {
  int lastDirection;
  int pendingDirection;
  byte reverseCount;
};
AxisControl panControl = {0, 0, 0};
AxisControl tiltControl = {0, 0, 0};

const __FlashStringHelper *stateName(TrackingState value) {
  switch (value) {
    case BODY_TRACKING: return F("BODY_TRACKING");
    case FACE_CANDIDATE: return F("FACE_CANDIDATE");
    case FACE_LOCKED: return F("FACE_LOCKED");
    case FACE_LOST: return F("FACE_LOST");
    case SCAN_TO_BOTTOM: return F("SCAN_TO_BOTTOM");
    case SCAN_UP: return F("SCAN_UP");
    case RETURN_HOME: return F("RETURN_HOME");
  }
  return F("UNKNOWN");
}

void setState(TrackingState next) {
  if (state == next) return;
  state = next;
  trackingPacketPending = false;
  trackingActive = false;
  panControl.pendingDirection = tiltControl.pendingDirection = 0;
  panControl.reverseCount = tiltControl.reverseCount = 0;
  Serial.print(F("STATE,"));
  Serial.println(stateName(state));
}

void writePan(int requested) {
  int bounded = constrain(requested, PAN_MIN, PAN_MAX);
  if (bounded != panAngle) {
    panAngle = bounded;
    panServo.write(panAngle);
  }
}

void writeTilt(int requested) {
  int bounded = constrain(requested, TILT_MIN, TILT_MAX);
  if (bounded != tiltAngle) {
    tiltAngle = bounded;
    tiltServo.write(tiltAngle);
  }
}

void setDoor(bool open, unsigned long now) {
  if (open) doorOpenedMs = now;
  if (doorOpen == open) return;
  doorOpen = open;
  doorServo.write(open ? 180 : 0);
  Serial.print(F("DOOR,"));
  Serial.println(open ? 1 : 0);
}

bool readNumber(char *&cursor, long &value, bool last) {
  if (*cursor == '\0') return false;
  errno = 0;
  char *end;
  value = strtol(cursor, &end, 10);
  if (errno == ERANGE || end == cursor || *end != (last ? '\0' : ',')) return false;
  cursor = last ? end : end + 1;
  return true;
}

bool insideCircle(int x, int y, int radius) {
  long squared = (long)x * x + (long)y * y;
  return squared <= (long)radius * radius;
}

bool validScanConfig() {
  return (TILT_DIRECTION == TILT_DOWN_DIRECTION &&
          TILT_UP_DIRECTION == -TILT_DOWN_DIRECTION &&
          (TILT_DOWN_DIRECTION == 1 || TILT_DOWN_DIRECTION == -1) &&
          SCAN_BOTTOM_ANGLE >= TILT_MIN && SCAN_BOTTOM_ANGLE <= TILT_MAX &&
          SCAN_TOP_ANGLE >= TILT_MIN && SCAN_TOP_ANGLE <= TILT_MAX &&
          TILT_HOME_ANGLE >= TILT_MIN && TILT_HOME_ANGLE <= TILT_MAX &&
          (SCAN_BOTTOM_ANGLE - TILT_HOME_ANGLE) * TILT_DOWN_DIRECTION > 0 &&
          (SCAN_TOP_ANGLE - SCAN_BOTTOM_ANGLE) * TILT_UP_DIRECTION > 0);
}

void startScan(unsigned long now) {
  if (!scanConfigValid) return;
  centeredSinceMs = 0;
  lastScanMs = now;
  setState(SCAN_TO_BOTTOM);
}

void beginCandidate(unsigned long now) {
  resumeState = state;
  candidateCount = 1;
  candidateLastMs = now;
  centeredSinceMs = 0;
  setState(FACE_CANDIDATE);
}

void rejectCandidate(unsigned long now) {
  candidateCount = 0;
  candidateRejectedMs = now;
  TrackingState previous = resumeState;
  setState(previous);
  // 已丢脸的计时不得因一帧误检而重置。
  if (state == FACE_LOST && now - lostSinceMs >= FACE_LOSS_HOLD_MS &&
      now - lastPacketMs <= CANDIDATE_GAP_MS) startScan(now);
  if (state == SCAN_TO_BOTTOM || state == SCAN_UP || state == RETURN_HOME) lastScanMs = now;
}

void handleLine(char *message) {
  if (strncmp(message, "V,", 2) != 0) return;
  char *cursor = message + 2;
  long values[5];
  for (byte i = 0; i < 5; ++i) {
    if (!readNumber(cursor, values[i], i == 4)) return;
  }
  if ((values[0] != 0 && values[0] != 1) ||
      (values[1] != 0 && values[1] != 1) ||
      (values[4] != 0 && values[4] != 1) ||
      values[2] < -1000 || values[2] > 1000 ||
      values[3] < -1000 || values[3] > 1000) return;

  unsigned long now = millis();
  bool wasConnected = lastPacketMs != 0 && now - lastPacketMs <= LINK_TIMEOUT_MS;
  if (!wasConnected) {
    // 断线后的首包只重建状态，不沿旧的追踪位置继续移动。
    state = BODY_TRACKING;
    candidateCount = 0;
    trackingActive = false;
    panControl.lastDirection = tiltControl.lastDirection = 0;
    centeredSinceMs = 0;
  }
  lastPacketMs = now;
  bodySeen = values[0] == 1;
  faceSeen = values[1] == 1;
  errorX = (int)values[2];
  errorY = (int)values[3];
  trackingPacketPending = false;

  bool pulse = values[4] == 1;
  if (pulse && !previousDoorPulse) setDoor(true, now);
  previousDoorPulse = pulse;
  if (faceSeen) lastFaceObservedMs = now;  // 候选人脸也延后原有自动关门时间。

  if (state == FACE_CANDIDATE) {
    if (!faceSeen || now - candidateLastMs > CANDIDATE_GAP_MS) {
      rejectCandidate(now);
      if (faceSeen && now - candidateRejectedMs >= CANDIDATE_RETRY_MS) beginCandidate(now);
    } else {
      ++candidateCount;
      candidateLastMs = now;
      if (candidateCount >= FACE_CONFIRM_PACKETS) {
        setState(FACE_LOCKED);
        lastLockedFaceMs = now;
        lostSinceMs = 0;
        trackingPacketPending = true;
      }
    }
  } else if (state == FACE_LOCKED) {
    if (faceSeen) {
      lastLockedFaceMs = now;
      trackingPacketPending = true;
    } else {
      lostSinceMs = now;
      setState(FACE_LOST);
    }
  } else if (faceSeen && now - candidateRejectedMs >= CANDIDATE_RETRY_MS) {
    beginCandidate(now);
  } else if (state == BODY_TRACKING) {
    trackingPacketPending = bodySeen;
  }

  // 只有新收到的实际检测包才累计身体居中时间，避免断流前误启动扫描。
  if (state == BODY_TRACKING) {
    if (bodySeen && !faceSeen && insideCircle(errorX, errorY, CENTER_ENTER_ERROR)) {
      if (centeredSinceMs == 0) centeredSinceMs = now;
      else if (now - centeredSinceMs >= CENTER_HOLD_MS) startScan(now);
    } else {
      centeredSinceMs = 0;
    }
  }

  if (lastAckMs == 0 || now - lastAckMs >= 5000) {
    Serial.println(F("ACK,V"));
    lastAckMs = now;
  }
}

void stepTiltToward(int target, int direction) {
  if (tiltAngle == target) return;
  int next = constrain(tiltAngle + direction * SCAN_STEP_DEG, TILT_MIN, TILT_MAX);
  if ((direction > 0 && next > target) || (direction < 0 && next < target)) next = target;
  writeTilt(next);
}

int allowedDirection(AxisControl &axis, int requested) {
  if (requested == 0) return 0;
  if (axis.lastDirection != 0 && requested != axis.lastDirection) {
    if (axis.pendingDirection == requested) {
      if (axis.reverseCount < REVERSE_CONFIRM_PACKETS) ++axis.reverseCount;
    } else {
      axis.pendingDirection = requested;
      axis.reverseCount = 1;
    }
    if (axis.reverseCount < REVERSE_CONFIRM_PACKETS) return 0;
  }
  axis.lastDirection = requested;
  axis.pendingDirection = 0;
  axis.reverseCount = 0;
  return requested;
}

void trackOnce(unsigned long now) {
  if (!trackingPacketPending || now - lastTrackMs < TRACK_INTERVAL_MS) return;
  trackingPacketPending = false;  // 一个有效新包最多调整一次。
  lastTrackMs = now;
  if (trackingActive) {
    if (insideCircle(errorX, errorY, CENTER_ENTER_ERROR)) trackingActive = false;
  } else if (!insideCircle(errorX, errorY, CENTER_EXIT_ERROR)) {
    trackingActive = true;
  }
  if (!trackingActive) return;
  int directionX = allowedDirection(panControl, (errorX > 0) - (errorX < 0));
  int directionY = allowedDirection(tiltControl, (errorY > 0) - (errorY < 0));
  if (directionX) writePan(panAngle + PAN_DIRECTION * directionX * TRACK_STEP_DEG);
  if (directionY) writeTilt(tiltAngle + TILT_DIRECTION * directionY * TRACK_STEP_DEG);
}

void updateState(unsigned long now) {
  bool connected = lastPacketMs != 0 && now - lastPacketMs <= LINK_TIMEOUT_MS;
  if (!connected) {
    if (state != BODY_TRACKING) setState(BODY_TRACKING);
    bodySeen = faceSeen = false;
    candidateCount = 0;
    centeredSinceMs = 0;
    trackingPacketPending = false;
    previousDoorPulse = false;
    panControl.lastDirection = tiltControl.lastDirection = 0;
    return;
  }
  if (state == FACE_CANDIDATE) {
    if (now - candidateLastMs > CANDIDATE_GAP_MS) rejectCandidate(now);
    return;  // 候选时 PAN/TILT 全部暂停。
  }
  if (state == FACE_LOST) {
    if (now - lostSinceMs >= FACE_LOSS_HOLD_MS &&
        now - lastPacketMs <= CANDIDATE_GAP_MS) startScan(now);
    return;  // 不跟身体，也不使用旧坐标。
  }
  if (state == BODY_TRACKING || state == FACE_LOCKED) {
    trackOnce(now);
    return;
  }
  // 扫描和回位完全不依赖 bodySeen，PAN 始终不动。
  if (now - lastScanMs < SCAN_INTERVAL_MS) return;
  lastScanMs = now;
  if (state == SCAN_TO_BOTTOM) {
    stepTiltToward(SCAN_BOTTOM_ANGLE, TILT_DOWN_DIRECTION);
    if (tiltAngle == SCAN_BOTTOM_ANGLE) setState(SCAN_UP);
  } else if (state == SCAN_UP) {
    stepTiltToward(SCAN_TOP_ANGLE, TILT_UP_DIRECTION);
    if (tiltAngle == SCAN_TOP_ANGLE) setState(RETURN_HOME);
  } else if (state == RETURN_HOME) {
    if (tiltAngle != TILT_HOME_ANGLE) {
      stepTiltToward(TILT_HOME_ANGLE, tiltAngle < TILT_HOME_ANGLE ? 1 : -1);
    }
    if (tiltAngle == TILT_HOME_ANGLE) {
      centeredSinceMs = 0;
      setState(BODY_TRACKING);
    }
  }
}

void setup() {
  panServo.attach(PAN_PIN);
  tiltServo.attach(TILT_PIN);
  doorServo.attach(DOOR_PIN);
  panServo.write(panAngle);
  tiltServo.write(tiltAngle);
  doorServo.write(0);
  Serial.begin(115200);
  scanConfigValid = validScanConfig();
  if (!scanConfigValid) Serial.println(F("ERROR,SCAN_CONFIG"));
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      if (!lineOverflow) {
        line[lineLength] = '\0';
        handleLine(line);
      }
      lineLength = 0;
      lineOverflow = false;
    } else if (c != '\r' && !lineOverflow) {
      if (lineLength < sizeof(line) - 1) line[lineLength++] = c;
      else lineOverflow = true;
    }
  }
  unsigned long now = millis();
  updateState(now);
  if (doorOpen && now - doorOpenedMs >= DOOR_HOLD_MS &&
      now - lastFaceObservedMs >= DOOR_HOLD_MS) {
    setDoor(false, now);
  }
}
