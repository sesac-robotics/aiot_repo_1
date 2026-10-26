#include <Servo.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>

// Paired with /home/zxy/venv/yolo/detect_yolov3.py.
// Python sends: V,body,face,dx,dy,doorPulse followed by a newline at 115200 baud.
// dx/dy are -1000..1000; positive means the target is right/down in the image.
// Wiring: pan D9, tilt D10, door D11. Power servos externally and share GND.
const byte PAN_PIN = 9;
const byte TILT_PIN = 10;
const byte DOOR_PIN = 11;

// Change either sign if that axis moves away from the target.
const int PAN_DIRECTION = -1;
const int TILT_DIRECTION = -1;
const int PAN_MIN = 20;
const int PAN_MAX = 160;
const int TILT_MIN = 30;
const int TILT_MAX = 150;
const int TRACK_START_ERROR = 130;  // 开始转动需离开中心更远。
const int TRACK_STOP_ERROR = 70;    // 回到中心后停止，形成启停滞回。
const float TRACK_GAIN = 0.002f;    // 每包坐标的比例控制增益。
const float MAX_TRACK_STEP = 2.0f;  // 每包最多转动 2°。
const byte REVERSE_CONFIRM_PACKETS = 3;
const unsigned long TRACK_INTERVAL_MS = 40;
const unsigned long SCAN_INTERVAL_MS = 100;
const unsigned long CENTER_HOLD_MS = 2000;
const unsigned long FACE_LOSS_HOLD_MS = 1500;
const unsigned long LINK_TIMEOUT_MS = 1500;
const unsigned long DOOR_HOLD_MS = 5000;

Servo panServo;
Servo tiltServo;
Servo doorServo;
int panAngle = 90;
int tiltAngle = 90;
float panPosition = 90.0f;   // 累积不足 1° 的水平调整。
float tiltPosition = 90.0f;  // 累积不足 1° 的俯仰调整。
int errorX = 0;
int errorY = 0;
bool bodySeen = false;
bool faceSeen = false;
bool hadFace = false;
bool scanning = false;
bool doorOpen = false;
bool previousDoorPulse = false;
int scanDirection = 1;
unsigned long lastPacketMs = 0;
unsigned long lastTrackMs = 0;
bool trackingPacketPending = false;
unsigned long lastScanMs = 0;
unsigned long centeredSinceMs = 0;
unsigned long doorOpenedMs = 0;
unsigned long lastFaceMs = 0;
char line[64];
byte lineLength = 0;
bool lineOverflow = false;

struct AxisControl {
  bool active;
  int lastDirection;    // 最近实际允许的方向：-1、0、1。
  int pendingDirection;
  byte reverseCount;    // 只按收到的有效数据包计数，不按 loop 次数计数。
};

AxisControl panControl = {false, 0, 0, 0};
AxisControl tiltControl = {false, 0, 0, 0};

void pauseAxis(AxisControl &axis) {
  axis.active = false;
  axis.pendingDirection = 0;
  axis.reverseCount = 0;
}

void resetAxis(AxisControl &axis) {
  pauseAxis(axis);
  axis.lastDirection = 0;
}

void updateAxis(AxisControl &axis, int error) {
  int magnitude = abs(error);
  if (magnitude <= TRACK_STOP_ERROR ||
      (!axis.active && magnitude < TRACK_START_ERROR)) {
    pauseAxis(axis);
    return;
  }

  int requestedDirection = error > 0 ? 1 : -1;
  if (axis.lastDirection != 0 && requestedDirection != axis.lastDirection) {
    if (axis.pendingDirection == requestedDirection) {
      if (axis.reverseCount < REVERSE_CONFIRM_PACKETS) ++axis.reverseCount;
    } else {
      axis.pendingDirection = requestedDirection;
      axis.reverseCount = 1;
    }
    if (axis.reverseCount < REVERSE_CONFIRM_PACKETS) {
      axis.active = false;  // 反向尚未确认时停止，不沿旧方向继续走。
      return;
    }
  }

  axis.lastDirection = requestedDirection;
  axis.active = true;
  axis.pendingDirection = 0;
  axis.reverseCount = 0;
}

void setDoor(bool open, unsigned long now) {
  if (open) {
    doorOpenedMs = now;
  }
  if (doorOpen == open) return;
  doorOpen = open;
  doorServo.write(open ? 180 : 0);
  Serial.print(F("DOOR,"));
  Serial.println(open ? 1 : 0);
}

// Parse one decimal field, rejecting empty fields, trailing text and overflow.
bool readNumber(char *&cursor, long &value, bool last) {
  if (*cursor == '\0') return false;
  errno = 0;
  char *end;
  value = strtol(cursor, &end, 10);
  if (errno == ERANGE || end == cursor || *end != (last ? '\0' : ','))
    return false;
  cursor = last ? end : end + 1;
  return true;
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
      values[1] > values[0] ||
      values[2] < -1000 || values[2] > 1000 ||
      values[3] < -1000 || values[3] > 1000) return;

  unsigned long now = millis();
  bodySeen = values[0] == 1;
  faceSeen = values[1] == 1;
  errorX = (int)values[2];
  errorY = (int)values[3];
  lastPacketMs = now;

  bool pulse = values[4] == 1;
  if (pulse && !previousDoorPulse) setDoor(true, now);
  previousDoorPulse = pulse;
  if (faceSeen) {
    hadFace = true;
    lastFaceMs = now;
    if (scanning) resetAxis(tiltControl);
    scanning = false;  // 找到脸的这一包就结束上下扫描。
    centeredSinceMs = 0;
  } else if (!bodySeen) {
    scanning = false;
    centeredSinceMs = 0;
    resetAxis(panControl);
    resetAxis(tiltControl);
  }

  // 刚丢脸时保持两轴位置；过了 1.5 秒才按身体中心继续追踪。
  if (bodySeen && !faceSeen && hadFace &&
      now - lastFaceMs < FACE_LOSS_HOLD_MS) {
    scanning = false;
    centeredSinceMs = 0;
    pauseAxis(panControl);
    pauseAxis(tiltControl);
    trackingPacketPending = false;
  } else if (bodySeen) {
    updateAxis(panControl, errorX);
    if (!scanning) updateAxis(tiltControl, errorY);
    trackingPacketPending = true;
  } else {
    trackingPacketPending = false;
  }

  // The Python program prints this response, confirming matching protocol.
  static unsigned long lastAckMs = 0;
  if (lastAckMs == 0 || now - lastAckMs >= 5000) {
    Serial.println(F("ACK,V"));
    lastAckMs = now;
  }
}

void setup() {
  panServo.attach(PAN_PIN);
  tiltServo.attach(TILT_PIN);
  doorServo.attach(DOOR_PIN);
  panServo.write(panAngle);    // Horizontal starts at 90 degrees.
  tiltServo.write(tiltAngle);  // Vertical starts at 90 degrees.
  doorServo.write(0);          // Door starts closed.
  Serial.begin(115200);
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
  bool connected = lastPacketMs != 0 && now - lastPacketMs <= LINK_TIMEOUT_MS;
  if (!connected) {
    bodySeen = false;
    faceSeen = false;
    hadFace = false;
    scanning = false;
    centeredSinceMs = 0;
    previousDoorPulse = false;
    trackingPacketPending = false;
    resetAxis(panControl);
    resetAxis(tiltControl);
  }
  if (doorOpen && now - doorOpenedMs >= DOOR_HOLD_MS &&
      now - lastFaceMs >= DOOR_HOLD_MS) {
    setDoor(false, now);
  }

  if (!connected || !bodySeen) return;
  bool holdingFace = !faceSeen && hadFace &&
                     now - lastFaceMs < FACE_LOSS_HOLD_MS;
  if (holdingFace) return;

  // 身体对准中心并连续 2 秒找不到脸，才慢速上下扫描。
  if (!faceSeen && !scanning &&
      abs(errorX) <= TRACK_STOP_ERROR &&
      abs(errorY) <= TRACK_STOP_ERROR) {
    if (centeredSinceMs == 0) centeredSinceMs = now;
    if (now - centeredSinceMs >= CENTER_HOLD_MS) {
      scanning = true;
      scanDirection = 1;
      lastScanMs = now;
      resetAxis(tiltControl);
    }
  } else if (!scanning) {
    centeredSinceMs = 0;
  }

  // 每包有效坐标最多执行一步；等待新包时绝不重复追旧位置。
  if (trackingPacketPending && now - lastTrackMs >= TRACK_INTERVAL_MS) {
    trackingPacketPending = false;
    lastTrackMs = now;
    if (panControl.active) {
      float deltaX = constrain(TRACK_GAIN * abs(errorX), 0.0f, MAX_TRACK_STEP);
      panPosition = constrain(
          panPosition + PAN_DIRECTION * panControl.lastDirection * deltaX,
          (float)PAN_MIN, (float)PAN_MAX);
      int newAngle = (int)(panPosition + 0.5f);
      if (newAngle != panAngle) {
        panAngle = newAngle;
        panServo.write(panAngle);
      }
    }
    if (!scanning && tiltControl.active) {
      float deltaY = constrain(TRACK_GAIN * abs(errorY), 0.0f, MAX_TRACK_STEP);
      tiltPosition = constrain(
          tiltPosition + TILT_DIRECTION * tiltControl.lastDirection * deltaY,
          (float)TILT_MIN, (float)TILT_MAX);
      int newAngle = (int)(tiltPosition + 0.5f);
      if (newAngle != tiltAngle) {
        tiltAngle = newAngle;
        tiltServo.write(tiltAngle);
      }
    }
  }

  if (scanning && now - lastScanMs >= SCAN_INTERVAL_MS) {
    lastScanMs = now;
    tiltAngle = constrain(tiltAngle + scanDirection, TILT_MIN, TILT_MAX);
    tiltPosition = (float)tiltAngle;  // 扫描期间同步位置，恢复追踪时不会跳角度。
    tiltServo.write(tiltAngle);
    if (tiltAngle == TILT_MAX) scanDirection = -1;
    if (tiltAngle == TILT_MIN) scanDirection = 1;
  }
}
