# 최신 연동 안내

현재 버전의 전체 연결 방법은 EVENT_INTEGRATION_KO.md와 README_KO.md를 참고하십시오.
Current/run.py + workflow.py가 실행 코드입니다.
- 환자 저장: WorkflowMixin.save(), finish_db()
- 카드/DB/인식/문/낙상 결과 수신: receive_event(dict) 또는 App.events.incoming Signal
- 요청 조회: GET /requests
- PC1 환자 DB: pc1_db_adapter.py
- 로그: event_store.Store, Current/data/gui.sqlite3 또는 gui.external.sqlite3
- 영상 프레임 표시: App.set_cctv_frame(QImage/QPixmap)

Current는 문 제어 명령을 보내지 않습니다. 실제 센서 상태만 표시합니다.
Future는 이전 디자인 데모로 유지했습니다.

서버와 얼굴 인식 결과를 연결해 실행하려면 [PC1 연결 실행 안내](CONNECTED_PC1_KO.md)를 참고하세요.
