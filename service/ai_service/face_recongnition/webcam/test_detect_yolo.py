"""单机状态和优质截图逻辑的无硬件测试。"""

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from detect_yolo import (TargetController, capture_step, check_quality,
                         new_capture_state, save_capture)


FACE = (280, 175, 360, 265)
FRAME_SIZE = (640, 480)


def textured_frame(seed=1):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    rng = np.random.default_rng(seed)
    frame[175:265, 280:360] = rng.integers(0, 256, (90, 80, 3), dtype=np.uint8)
    return frame


class CaptureTests(unittest.TestCase):
    def test_quality_rejects_blurry_and_small_faces(self):
        sharp = textured_frame()
        self.assertTrue(check_quality(sharp, FACE)[0])
        self.assertFalse(check_quality(np.zeros_like(sharp), FACE)[0])
        self.assertFalse(check_quality(sharp, (300, 175, 340, 265))[0])

    def test_confirm_then_wait_and_save_best_frame(self):
        controller = TargetController()
        for now in (1.0, 1.1):
            self.assertEqual(controller.process([], [FACE], FRAME_SIZE, now).state,
                             "FACE_CANDIDATE")
        self.assertEqual(controller.process([], [FACE], FRAME_SIZE, 1.2).state,
                         "FACE_LOCKED")
        state = new_capture_state()
        first = textured_frame(1)
        best = textured_frame(2)
        self.assertIsNone(capture_step(first, FACE, 0, state, 1.2, True)[0])
        self.assertIsNone(capture_step(best, FACE, 0, state, 2.0, True)[0])
        chosen, _, _, _ = capture_step(best, FACE, 0, state, 3.21, True)
        self.assertIsNotNone(chosen)
        self.assertGreaterEqual(state["best_score"], 20)
        expected = max((first, best), key=lambda image: check_quality(image, FACE)[2])
        self.assertTrue(np.array_equal(chosen, expected))
        state["captured"] = True
        state["last_capture"] = 3.21
        self.assertIsNone(capture_step(best, FACE, 0, state, 3.5, True)[0])
        capture_step(best, None, 0, state, 4.0, False)
        capture_step(best, None, 0, state, 5.1, False)
        self.assertFalse(state["captured"])

    def test_alignment_and_multiple_people_block_capture(self):
        state = new_capture_state()
        frame = textured_frame()
        self.assertIsNone(capture_step(frame, FACE, 1, state, 1.0, False)[0])
        self.assertIsNone(capture_step(frame, FACE, 2, state, 1.1, True)[0])
        self.assertIsNone(capture_step(frame, FACE, 1, state, 1.2, True, True)[0])
        self.assertIsNone(state["start"])

    def test_atomic_save(self):
        with tempfile.TemporaryDirectory() as temp:
            filename = save_capture(textured_frame(), Path(temp))
            self.assertTrue(filename.is_file())
            self.assertEqual(list(Path(temp).glob("*.writing")), [])
            self.assertIsNotNone(cv2.imread(str(filename)))


if __name__ == "__main__":
    unittest.main()
