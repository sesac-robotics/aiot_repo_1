"""不依赖摄像头或 Arduino 的目标选择与状态测试。"""

import math
import unittest

from detect_yolo import TargetController, control_error


FRAME = (640, 480)
BODY = (220, 100, 420, 470)
FACE = (280, 175, 360, 265)


class TrackingTests(unittest.TestCase):
    def test_circle_and_edge(self):
        centered = control_error(FACE, FRAME, face=True)
        self.assertEqual(centered, (0, -42, False))
        # 相同短边缩放，圆形距离而不是两个轴各自判定。
        diagonal = control_error((352, 222, 432, 312), FRAME, face=True)
        self.assertGreater(math.hypot(*diagonal[:2]), 150)
        left_edge = control_error((0, 175, 560, 265), FRAME, face=True)
        self.assertEqual(left_edge[0], -220)
        huge = control_error((0, 0, 640, 480), FRAME, face=True)
        self.assertTrue(huge[2])
        self.assertEqual(huge[:2], (0, 0))

    def test_body_center_then_scan_without_body(self):
        ctrl = TargetController()
        self.assertEqual(ctrl.process([BODY], [], FRAME, 1.0).state, "BODY_TRACKING")
        self.assertEqual(ctrl.process([BODY], [], FRAME, 3.1).state, "SCANNING")
        self.assertEqual(ctrl.process([], [], FRAME, 3.2).state, "SCANNING")

    def test_face_only_three_distinct_process_calls(self):
        ctrl = TargetController()
        self.assertEqual(ctrl.process([], [FACE], FRAME, 1.0).state, "FACE_CANDIDATE")
        self.assertEqual(ctrl.process([], [FACE], FRAME, 1.1).state, "FACE_CANDIDATE")
        decision = ctrl.process([], [FACE], FRAME, 1.2)
        self.assertEqual(decision.state, "FACE_LOCKED")
        self.assertEqual((decision.body_seen, decision.face_seen), (0, 1))

    def test_lost_face_holds_then_scans(self):
        ctrl = TargetController()
        for t in (1.0, 1.1, 1.2):
            ctrl.process([BODY], [FACE], FRAME, t)
        lost = ctrl.process([BODY], [], FRAME, 1.3)
        self.assertEqual((lost.state, lost.face_seen, lost.dx, lost.dy),
                         ("FACE_LOST", 0, 0, 0))
        self.assertEqual(ctrl.process([BODY], [], FRAME, 1.8).state, "FACE_LOST")
        self.assertEqual(ctrl.process([], [], FRAME, 2.31).state, "SCANNING")

    def test_scan_candidate_failure_resumes_scan(self):
        ctrl = TargetController()
        ctrl.process([BODY], [], FRAME, 1.0)
        ctrl.process([BODY], [], FRAME, 3.1)
        self.assertEqual(ctrl.process([], [FACE], FRAME, 3.2).state, "FACE_CANDIDATE")
        self.assertEqual(ctrl.process([], [], FRAME, 3.3).state, "SCANNING")

    def test_single_false_face_does_not_extend_loss_hold(self):
        ctrl = TargetController()
        for t in (1.0, 1.1, 1.2):
            ctrl.process([], [FACE], FRAME, t)
        ctrl.process([], [], FRAME, 1.3)
        self.assertEqual(ctrl.process([], [FACE], FRAME, 1.8).state, "FACE_CANDIDATE")
        self.assertEqual(ctrl.process([], [], FRAME, 1.9).state, "FACE_LOST")
        self.assertEqual(ctrl.process([], [], FRAME, 2.31).state, "SCANNING")

    def test_keep_original_face_when_two_present(self):
        ctrl = TargetController()
        other = (50, 175, 130, 265)
        for t in (1.0, 1.1, 1.2):
            ctrl.process([], [FACE], FRAME, t)
        decision = ctrl.process([], [other, FACE], FRAME, 1.3)
        self.assertEqual(decision.face_box, FACE)
        # 原脸消失时，即使另一个人还在，也进入丢失等待。
        self.assertEqual(ctrl.process([], [other], FRAME, 1.4).state, "FACE_LOST")


if __name__ == "__main__":
    unittest.main()
