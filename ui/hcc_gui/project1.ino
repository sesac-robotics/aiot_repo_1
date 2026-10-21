#include <SPI.h>
#include <MFRC522.h>

#define RST_PIN         9
#define SS_PIN          10

MFRC522 mfrc522(SS_PIN, RST_PIN);   // MFRC522 인스턴스 생성
MFRC522::MIFARE_Key key;

// 63바이트 패킷 구조체
struct __attribute__((__packed__)) PatientData {
    
    int32_t p_num;       // 환자 고유 번호 (4바이트)
    char name[10];       // 이름 (10바이트)
    char gender;         // 성별 (1바이트)
    int32_t age;         // 나이 (4바이트)
    char blood[6];       // 혈액형 (6바이트)
    float height;        // 키 (4바이트)
    float weight;        // 몸무게 (4바이트)
    char disease[15];    // 질병 (15바이트)
    char medication[15]; // 복용약 (15바이트)
};

PatientData patient;
const int PACKET_SIZE = sizeof(PatientData); 
byte serialBuffer[sizeof(PatientData)];

const byte startBlock = 4; // 데이터 시작 블록

void setup() {
    Serial.begin(9600); 
    SPI.begin();        
    mfrc522.PCD_Init(); 

    for (byte i = 0; i < 6; i++) {
        key.keyByte[i] = 0xFF;
    }
}

// 안전하게 카드를 검색하는 헬퍼 함수
bool waitForCard(int timeout_ms) {
    int loops = timeout_ms / 10;
    for (int i = 0; i < loops; i++) { 
        // 🚨 안테나를 깨우고 다시 읽을 수 있도록 세팅 유지
        if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
            return true;
        }
        delay(10);
    }
    return false;
}

void loop() {
    // 시리얼 명령이 들어왔을 때만 모든 로직을 수행하도록 집중화
    if (Serial.available() > 0) {
        byte cmd = Serial.read();

        // =================================================================
        // [1] 등록(Enroll) 처리: PC에서 데이터를 받아 카드에 쓰기
        // =================================================================
        if (cmd == 0x02) {
            long timeout = millis();
            while (Serial.available() < PACKET_SIZE) {
                if (millis() - timeout > 1000) {
                    Serial.write(0x08); // 데이터 수신 타임아웃 오류 코드
                    return; 
                }
            }
            Serial.readBytes(serialBuffer, PACKET_SIZE);
            memcpy(&patient, serialBuffer, PACKET_SIZE);

            // PC에 데이터 수신 완료 알림 (바이너리 응답 처리)
            Serial.write(0x09); 

            // 카드가 접촉될 때까지 최대 5초간 대기
            if (!waitForCard(5000)) {
                Serial.write(0x05); // 카드 미감지 오류 코드 송신
                return;
            }

            // RFID 카드에 분할 쓰기 시작
            byte *dataPtr = (byte *)&patient;
            int bytesWritten = 0;
            byte currentBlock = startBlock;
            MFRC522::StatusCode status;

            while (bytesWritten < PACKET_SIZE) {
                if ((currentBlock + 1) % 4 == 0) {
                    currentBlock++; // 섹터 트레일러 우회
                }

                status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, currentBlock, &key, &(mfrc522.uid));
                if (status != MFRC522::STATUS_OK) {
                    Serial.write(0x06); // 인증 실패
                    mfrc522.PICC_HaltA(); mfrc522.PCD_StopCrypto1();
                    return;
                }

                byte blockBuffer[16] = {0};
                int chunk = min(16, PACKET_SIZE - bytesWritten);
                memcpy(blockBuffer, dataPtr + bytesWritten, chunk);

                status = mfrc522.MIFARE_Write(currentBlock, blockBuffer, 16);
                if (status != MFRC522::STATUS_OK) {
                    Serial.write(0x07); // 쓰기 실패
                    mfrc522.PICC_HaltA(); mfrc522.PCD_StopCrypto1();
                    return;
                }

                bytesWritten += chunk;
                currentBlock++;
            }

            // 등록 성공 마커 전송 (파이썬 GUI에서 원하면 매핑 가능)
            Serial.write(0x0A); 
            mfrc522.PICC_HaltA();
            mfrc522.PCD_StopCrypto1();
        }
        
        // =================================================================
        // [2] 조회(Read) 처리: 카드에서 읽어서 PC로 전송
        // =================================================================
        else if (cmd == 0x03) {
            // 카드가 접촉될 때까지 최대 5초간 대기
            if (!waitForCard(5000)) {
                Serial.write(0x05); // 카드 미감지 오류 코드 송신
                return;
            }

            // RFID 카드로부터 63바이트 분할 로드
            byte *dataPtr = (byte *)&patient;
            int bytesRead = 0;
            byte currentBlock = startBlock; 
            MFRC522::StatusCode status;
            byte blockBuffer[18]; 

            while (bytesRead < PACKET_SIZE) {
                if ((currentBlock + 1) % 4 == 0) {
                    currentBlock++;
                }

                status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, currentBlock, &key, &(mfrc522.uid));
                if (status != MFRC522::STATUS_OK) {
                    Serial.write(0x06); // 인증 실패 코드 송신
                    mfrc522.PICC_HaltA(); mfrc522.PCD_StopCrypto1();
                    return;
                }

                byte size = sizeof(blockBuffer);
                status = mfrc522.MIFARE_Read(currentBlock, blockBuffer, &size);
                if (status != MFRC522::STATUS_OK) {
                    Serial.write(0x07); // 데이터 읽기 실패 코드 송신
                    mfrc522.PICC_HaltA(); mfrc522.PCD_StopCrypto1();
                    return;
                }

                int chunk = min(16, PACKET_SIZE - bytesRead);
                memcpy(dataPtr + bytesRead, blockBuffer, chunk);

                bytesRead += chunk;
                currentBlock++;
            }

            mfrc522.PICC_HaltA();
            mfrc522.PCD_StopCrypto1();

            // 성공 코드(0x04)와 데이터 패킷 연속 전송
            Serial.write(0x04);
            Serial.write((byte *)&patient, PACKET_SIZE);
        }
    }
}
