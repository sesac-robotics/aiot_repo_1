# v3 디자인 수정

- People: 첨부 샘플에 맞춰 Personal/Health 제목 띠, 테두리 있는 정보칸, 하단 Edit/Delete 적용. ID/Name/Role/Blood 열 배치.
- Register Current: Personal/Health 두 카드와 하단 Face 상태 영역.
- Register Future: Face/Personal/Health 세 카드. 사진은 Future에만 표시.
- Register의 뒤로 버튼은 People로 이동.
- Logs: 검색 행 전체와 표의 좌우 경계 일치. 입력칸과 Reset은 동일한 48px 높이, 간격16px.
- Door Access: 큰 글씨를 유지하고 Personal/Health 제목 띠 적용.
- 공통 배경/표/입력칸 색상과 테두리를 통일.
- Current CCTV 이름·시간 제거 및 익명 낙상 범위 표시는 유지.
- 실제 Qt 화면 13개를 Previews에 다시 저장.

실행은 Current/run.py 또는 Future/run.py를 사용합니다. 운영 중인 GUI를 교체하기 전 기존 data/people.local.json을 보관하십시오.
