/*
 * Door MCU 담당용 예제 (ESP32, Arduino IDE)
 * 문 상태가 바뀔 때마다 서버(PC1)의 TCP 8103 포트로 "1\n"(열림) / "0\n"(닫힘)을 보낸다.
 * 문을 열고 닫는 판단 로직은 MCU 담당이 isDoorOpen() 안에 넣으면 된다.
 *
 * 수정할 곳: WIFI_SSID, WIFI_PASS, SERVER_IP, isDoorOpen()
 */
#include <WiFi.h>

const char* WIFI_SSID = "YOUR_SSID";
const char* WIFI_PASS = "YOUR_PASS";
const char* SERVER_IP = "192.168.0.10";   // PC1 IP
const uint16_t SERVER_PORT = 8103;
const char* DOOR_ID = "";                  // 문이 여러 개면 "door_02" 처럼. 비우면 서버 기본 door_01

WiFiClient client;
int lastSent = -1;               // 마지막으로 보낸 값 (-1 = 아직 없음)
unsigned long lastTry = 0;

// TODO: 실제 문 상태 판단 (서보 각도, 리드 스위치 등)
bool isDoorOpen() {
  return false;
}

void sendState(int v) {
  String line = (strlen(DOOR_ID) ? String(DOOR_ID) + "," : String("")) + String(v) + "\n";
  client.print(line);
  Serial.print("[DOOR] sent ");
  Serial.print(line);
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(200);
  Serial.println("[DOOR] WiFi " + WiFi.localIP().toString());
}

void loop() {
  // 서버 연결이 끊겼으면 2초마다 재접속, 접속하면 현재 상태부터 보냄
  if (!client.connected()) {
    if (millis() - lastTry > 2000) {
      lastTry = millis();
      if (client.connect(SERVER_IP, SERVER_PORT)) {
        client.setNoDelay(true);
        Serial.println("[DOOR] server connected");
        lastSent = -1;
      }
    }
    return;
  }

  int now = isDoorOpen() ? 1 : 0;
  if (now != lastSent) {       // 바뀔 때만 전송
    sendState(now);
    lastSent = now;
  }
  delay(50);
}
