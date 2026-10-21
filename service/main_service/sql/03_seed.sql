-- 개발·시연용 기본 장치 (필요하면 GUI나 API로 추가)
INSERT INTO hcc.cameras (camera_id, camera_type, location) VALUES
  ('CAM01', 'TRACKING', 'room_101'),
  ('FACE01', 'FACE', 'entrance')
ON CONFLICT DO NOTHING;
INSERT INTO hcc.doors (door_id, location) VALUES ('door_01', 'entrance') ON CONFLICT DO NOTHING;
