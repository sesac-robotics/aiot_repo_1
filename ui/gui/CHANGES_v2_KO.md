# 수정 내역 v2

- Current CCTV: 이름·시간 제거. 낙상 범위/Fall detected/Confirm만 표시합니다. Confirm 후에도 이름·시간을 표시하지 않습니다.
- Current/Future CCTV: 첨부 샘플을 기준으로 병실 일러스트 지도 교체. 낙상 영역은 별도 SVG 오버레이입니다.
- Current Door Access: 이름 60px, 문 상태 48px, 항목/값 25px. 샘플의 두 열 구분선 배치 적용.
- Future Door Access: 이름 52px, 문 상태 44px, 항목/값 25px. 카메라는 비율을 유지해 가운데를 잘라 표시합니다.
- 기본 본문 17px, 작은 보조 문구 15px로 확대.
- Previews 전체를 실제 수정된 Qt 화면으로 다시 캡처했습니다.

Current 폴더 전체를 GUI 폴더로 복사해 실행합니다. 기존 data/people.local.json이 있다면 먼저 별도 보관하고 수정본의 data 폴더에 다시 넣으십시오.
이번 변경은 디자인과 샘플 알림 표현에 관한 것이며 실제 AI/센서 연결은 포함하지 않습니다.
