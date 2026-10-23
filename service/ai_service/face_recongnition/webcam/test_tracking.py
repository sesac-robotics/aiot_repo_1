"""不依赖摄像头或 Arduino 的目标选择与状态测试。"""

import math
import unittest

from detect_yolo import TargetController, box_overlaps_center_circle, control_error


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

    def test_face_box_edge_uses_15_and_20_percent_hysteresis(self):
        ctrl = TargetController()
        # 中心点在蓝圈外，但左边框已进入 72px 蓝圈。
        touches_blue = (390, 200, 470, 280)
        self.assertTrue(box_overlaps_center_circle(touches_blue, FRAME, 0.15))
        self.assertGreater(math.dist(((430, 240)), (320, 240)), 72)
        for t in (1.0, 1.1, 1.2):
            decision = ctrl.process([], [touches_blue], FRAME, t)
        self.assertEqual((decision.state, decision.dx, decision.dy),
                         ("FACE_LOCKED", 0, 0))

        # 离开蓝圈但仍与 96px 红圈相交，继续保持停止。
        between_circles = (400, 200, 480, 280)
        decision = ctrl.process([], [between_circles], FRAME, 1.3)
        self.assertEqual((decision.dx, decision.dy), (0, 0))

        # 人脸框完全离开红圈后才重新输出追踪误差。
        outside_red = (420, 200, 500, 280)
        decision = ctrl.process([], [outside_red], FRAME, 1.4)
        self.assertNotEqual((decision.dx, decision.dy), (0, 0))

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
