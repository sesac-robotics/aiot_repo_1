# T-SAFE GUI

환자 관리, 출입 상태, 낙상 이벤트를 표시하는 Python / PyQt6 데스크톱 GUI입니다.

**처음 실행한다면 [한국어 설치·실행 안내](README_KO.md#1-설치와-실행)를 확인하세요.**

- `Current/`: 현재 실행 버전. 기본 모드는 외부 서버 없이 SQLite와 샘플 환자로 실행합니다.
- `Future/`: 이전 디자인 데모.
- `Previews/`: 화면 예시.
- [장치·서버 연동](EVENT_INTEGRATION_KO.md)

가상환경은 `gui/.venv`에 각자 생성합니다. `.venv` 자체는 GitHub에 올리지 않으며,
`Current/requirements.txt`로 의존성을 설치합니다. 실제 카메라·카드·문 센서와
PC1 서버 연결에는 별도의 장치 프로그램과 서버가 필요합니다.
