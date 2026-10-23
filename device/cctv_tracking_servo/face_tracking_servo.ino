#include <Servo.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>

// 接线：水平 D9、俯仰 D10、门 D11、超声波 TRIG D6、ECHO D2。
// 舵机外部电源地、传感器地、Arduino GND 必须共地。
const byte PAN_PIN = 9;
const byte TILT_PIN = 10;
const byte DOOR_PIN = 11;
const byte TRIG_PIN = 6;
const byte ECHO_PIN = 2;  // Uno 外部中断脚
const int PAN_DIRECTION = -1;
const int TILT_DIRECTION = -1;
const int PAN_MIN = 20;
const int PAN_MAX = 160;
const int TILT_MIN = 30;
const int TILT_MAX = 150;
const int DEAD_ZONE = 80;
const unsigned long TRACK_STEP_MS = 40;
const unsigned long SCAN_STEP_MS = 100;
const unsigned long CENTER_HOLD_MS = 2000;
const unsigned long LINK_TIMEOUT_MS = 1500;
const unsigned long DOOR_HOLD_MS = 5000;
const unsigned long FACE_CLEAR_MS = 5000;
const unsigned long DOOR_MOVE_MS = 700;  // 无位置传感器时的预定动作时间
const int PERSON_DISTANCE_CM = 80;
const unsigned long SONAR_INTERVAL_MS = 60;
const unsigned long SONAR_TIMEOUT_US = 30000;
const unsigned long SONAR_FRESH_MS = 200;
const int DOOR_OPEN_ANGLE = 180;
const int DOOR_CLOSED_ANGLE = 0;

Servo panServo, tiltServo, doorServo;
int panAngle = 90, tiltAngle = 90;
int errorX = 0, errorY = 0;
bool bodySeen = false, faceSeen = false, scanning = false;
int scanDirection = 1;
unsigned long lastPacketMs = 0, centeredSinceMs = 0;
unsigned long lastTrackStepMs = 0, lastScanStepMs = 0;
unsigned long lastFaceMs = 0, doorOpenedMs = 0, doorMoveMs = 0;
long activeDoorRequestId = 0;
enum DoorState { CLOSED, OPENING, OPEN, CLOSING };
DoorState doorState = CLOSED;

// 记录最近的 OPEN ID，避免重发同一命令造成再次动作。
const byte RECENT_OPEN_COUNT = 8;
long recentOpenIds[RECENT_OPEN_COUNT] = {0};
byte recentOpenIndex = 0;

char line[80];
byte lineLength = 0;
bool lineOverflow = false;

volatile unsigned long echoRiseUs = 0, echoDurationUs = 0;
volatile bool echoReady = false, echoArmed = false;
unsigned long lastSonarTriggerMs = 0, sonarTriggerUs = 0, lastSonarValidMs = 0;
bool sonarWaiting = false, sonarValid = false;
int sonarCm = 0;

void echoInterrupt() {
  if (!echoArmed) return;
  if (digitalRead(ECHO_PIN) == HIGH) {
    echoRiseUs = micros();
  } else if (echoRiseUs != 0) {
    echoDurationUs = micros() - echoRiseUs;
    echoReady = true;
    echoArmed = false;
  }
}

void sendFeedback(const __FlashStringHelper *type, long id,
                  const __FlashStringHelper *result) {
  Serial.print(type);
  Serial.print(',');
  Serial.print(id);
  Serial.print(',');
  Serial.println(result);
}

// 严格解析十进制整数字段；不接受空字段、多余字符或溢出。
bool readNumber(char *&cursor, long &value, bool last) {
  if (*cursor == '\0') return false;
  errno = 0;
  char *end;
  value = strtol(cursor, &end, 10);
  if (errno == ERANGE || end == cursor ||
      *end != (last ? '\0' : ',')) return false;
  cursor = last ? end : end + 1;
  return true;
}

void handleTrack(char *cursor) {
  long v[8];
  for (byte i = 0; i < 8; ++i) {
    if (!readNumber(cursor, v[i], i == 7)) return;
  }
  long b = v[0], f = v[1], x1 = v[2], y1 = v[3];
  long x2 = v[4], y2 = v[5], w = v[6], h = v[7];
  if ((b != 0 && b != 1) || (f != 0 && f != 1) || f > b ||
      w < 1 || w > 4096 || h < 1 || h > 4096) return;
  if (b) {
    if (x1 < 0 || y1 < 0 || x1 >= x2 || y1 >= y2 ||
        x2 > w || y2 > h) return;
  } else if (x1 != -1 || y1 != -1 || x2 != -1 || y2 != -1) return;

  unsigned long now = millis();
  bodySeen = b == 1;
  faceSeen = f == 1;
  lastPacketMs = now;
  if (bodySeen) {
    // 原算法使用 -1000～1000，死区 80 对应完整画面约 4%。
    errorX = (int)(((x1 + x2 - w) * 1000L) / w);
    errorY = (int)(((y1 + y2 - h) * 1000L) / h);
  } else {
    errorX = errorY = 0;
  }
  if (faceSeen) {
    lastFaceMs = now;
    scanning = false;
    centeredSinceMs = 0;
  } else if (!bodySeen) {
    scanning = false;
    centeredSinceMs = 0;
  }
}

bool seenOpenId(long id) {
  for (byte i = 0; i < RECENT_OPEN_COUNT; ++i) {
    if (recentOpenIds[i] == id) return true;
  }
  return false;
}

void handleDoor(char *cursor) {
  char *comma = strchr(cursor, ',');
  if (!comma) return;
  *comma = '\0';
  const char *action = cursor;
  cursor = comma + 1;
  long id;
  if (!readNumber(cursor, id, true) || id < 1) return;

  if (strcmp(action, "TEST") == 0) {
    // TEST 不修改任何门状态或定时器。
    sendFeedback(F("ACK"), id, F("TEST_OK"));
    return;
  }
  if (strcmp(action, "OPEN") != 0) {
    sendFeedback(F("ERROR"), id, F("BAD_ACTION"));
    return;
  }
  if (seenOpenId(id)) {
    sendFeedback(F("ACK"), id, F("OPEN_RECEIVED"));
    if (id == activeDoorRequestId && doorState == OPEN)
      sendFeedback(F("EVENT"), id, F("OPEN_COMPLETED"));
    return;
  }
  if (doorState == OPENING || doorState == CLOSING) {
    sendFeedback(F("ERROR"), id, F("DOOR_BUSY"));
    return;
  }
  recentOpenIds[recentOpenIndex] = id;
  recentOpenIndex = (recentOpenIndex + 1) % RECENT_OPEN_COUNT;
  activeDoorRequestId = id;
  sendFeedback(F("ACK"), id, F("OPEN_RECEIVED"));
  if (doorState == OPEN) {
    // 新 ID 对应显式请求，门已开时无需重复转动。
    doorOpenedMs = millis();
    sendFeedback(F("EVENT"), id, F("OPEN_COMPLETED"));
    return;
  }
  doorServo.write(DOOR_OPEN_ANGLE);
  doorMoveMs = millis();
  lastFaceMs = doorMoveMs;
  doorState = OPENING;
}

void handleLine(char *message) {
  if (strncmp(message, "TRACK,", 6) == 0) handleTrack(message + 6);
  else if (strncmp(message, "DOOR,", 5) == 0) handleDoor(message + 5);
}

void readSerial() {
  // 换行分帧；超长整行丢弃，防止截断包被误当成 OPEN。
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
}

void updateSonar(unsigned long now) {
  if (sonarWaiting) {
    bool ready;
    unsigned long duration;
    noInterrupts();
    ready = echoReady;
    duration = echoDurationUs;
    if (ready) echoReady = false;
    interrupts();
    if (ready) {
      sonarWaiting = false;
      int cm = (int)(duration / 58UL);
      sonarValid = cm >= 2 && cm <= 400;
      if (sonarValid) {
        sonarCm = cm;
        lastSonarValidMs = now;
      }
    } else if (micros() - sonarTriggerUs > SONAR_TIMEOUT_US) {
      noInterrupts();
      echoArmed = false;
      interrupts();
      sonarWaiting = false;
      sonarValid = false;  // 超时即未知，不能据此关门。
    }
  }
  if (!sonarWaiting && now - lastSonarTriggerMs >= SONAR_INTERVAL_MS) {
    lastSonarTriggerMs = now;
    sonarTriggerUs = micros();
    noInterrupts();
    echoRiseUs = 0;
    echoReady = false;
    echoArmed = true;
    interrupts();
    digitalWrite(TRIG_PIN, LOW);
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(10);  // 仅 10 微秒触发脉冲，不阻塞等待回波。
    digitalWrite(TRIG_PIN, LOW);
    sonarWaiting = true;
  }
}

void updateDoor(unsigned long now, bool linkValid) {
  if (doorState == OPENING && now - doorMoveMs >= DOOR_MOVE_MS) {
    doorState = OPEN;
    doorOpenedMs = now;
    sendFeedback(F("EVENT"), activeDoorRequestId, F("OPEN_COMPLETED"));
  }
  if (doorState == CLOSING && now - doorMoveMs >= DOOR_MOVE_MS) {
    doorState = CLOSED;
    sendFeedback(F("EVENT"), activeDoorRequestId, F("CLOSE_COMPLETED"));
  }
  if (doorState != OPEN) return;
  bool clear = sonarValid && now - lastSonarValidMs <= SONAR_FRESH_MS &&
               sonarCm > PERSON_DISTANCE_CM;
  // TRACK 中断时人脸状态未知，维持开门。
  if (linkValid && !faceSeen && clear &&
      now - doorOpenedMs >= DOOR_HOLD_MS &&
      now - lastFaceMs >= FACE_CLEAR_MS) {
    doorServo.write(DOOR_CLOSED_ANGLE);
    doorMoveMs = now;
    doorState = CLOSING;
  }
}

void updateTracking(unsigned long now, bool linkValid) {
  if (!linkValid) {
    bodySeen = faceSeen = scanning = false;
    centeredSinceMs = 0;
    return;
  }
  if (!faceSeen && bodySeen && !scanning &&
      abs(errorX) <= DEAD_ZONE && abs(errorY) <= DEAD_ZONE) {
    if (centeredSinceMs == 0) centeredSinceMs = now;
    if (now - centeredSinceMs >= CENTER_HOLD_MS) {
      scanning = true;
      scanDirection = 1;
      lastScanStepMs = now;
    }
  } else if (!scanning) centeredSinceMs = 0;
  if (!bodySeen) return;
  if (now - lastTrackStepMs >= TRACK_STEP_MS) {
    lastTrackStepMs = now;
    if (abs(errorX) > DEAD_ZONE) {
      int stepX = constrain(abs(errorX) / 200, 1, 4);
      panAngle = constrain(panAngle + PAN_DIRECTION *
                           (errorX > 0 ? stepX : -stepX), PAN_MIN, PAN_MAX);
      panServo.write(panAngle);
    }
    if (!scanning && abs(errorY) > DEAD_ZONE) {
      int stepY = constrain(abs(errorY) / 200, 1, 4);
      tiltAngle = constrain(tiltAngle + TILT_DIRECTION *
                            (errorY > 0 ? stepY : -stepY), TILT_MIN, TILT_MAX);
      tiltServo.write(tiltAngle);
    }
  }
  if (scanning && now - lastScanStepMs >= SCAN_STEP_MS) {
    lastScanStepMs = now;
    tiltAngle = constrain(tiltAngle + scanDirection, TILT_MIN, TILT_MAX);
    tiltServo.write(tiltAngle);
    if (tiltAngle == TILT_MAX) scanDirection = -1;
    if (tiltAngle == TILT_MIN) scanDirection = 1;
  }
}

void setup() {
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);
  attachInterrupt(digitalPinToInterrupt(ECHO_PIN), echoInterrupt, CHANGE);
  panServo.attach(PAN_PIN);
  tiltServo.attach(TILT_PIN);
  doorServo.attach(DOOR_PIN);
  panServo.write(panAngle);
  tiltServo.write(tiltAngle);
  doorServo.write(DOOR_CLOSED_ANGLE);
  Serial.begin(115200);
}

void loop() {
  readSerial();
  unsigned long now = millis();
  updateSonar(now);
  bool linkValid = lastPacketMs != 0 && now - lastPacketMs <= LINK_TIMEOUT_MS;
  updateDoor(now, linkValid);
  updateTracking(now, linkValid);
}
