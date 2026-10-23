"""State-machine tests for sustained post-fall lying detection."""

from __future__ import annotations

import unittest

import numpy as np

from fall_pipeline import FallState, PerTrackFallEngine, PoseObservation


def _pose(*, horizontal: bool, bbox: tuple[float, float, float, float]) -> PoseObservation:
    points = np.zeros((17, 2), dtype=np.float32)
    # Required nose, shoulders, hips.  A horizontal torso has equal y values;
    # an upright torso has equal x values.
    points[0] = (50, 20)
    if horizontal:
        points[5], points[6] = (40, 50), (40, 60)
        points[11], points[12] = (80, 50), (80, 60)
    else:
        points[5], points[6] = (45, 40), (55, 40)
        points[11], points[12] = (45, 90), (55, 90)
    return PoseObservation(1, points, bbox, keypoint_confidence=np.ones(17, dtype=np.float32))


class SustainedLyingStateTest(unittest.TestCase):
    def test_confirmed_fall_becomes_lying_and_requires_sustained_upright_recovery(self) -> None:
        score = {"value": .9}
        engine = PerTrackFallEngine(
            lambda _features: score["value"],
            window_size=1,
            probability_threshold=.5,
            geometry_threshold=.5,
            possible_consecutive=1,
            confirm_consecutive=1,
            recover_consecutive=1,
            latch_fall=False,
            require_upright_transition=False,
            lying_consecutive=2,
            lying_geometry_threshold=.5,
            lying_aspect_ratio_threshold=1.1,
            upright_recover_consecutive=2,
        )
        lying = _pose(horizontal=True, bbox=(0, 0, 120, 40))
        upright = _pose(horizontal=False, bbox=(0, 0, 40, 120))

        self.assertEqual(engine.update(1, [lying])[0].state, FallState.FALL_CONFIRMED)
        self.assertEqual(engine.update(2, [lying])[0].state, FallState.FALL_CONFIRMED)
        # LSTM output no longer determines recovery once lying_after_fall begins.
        self.assertEqual(engine.update(3, [lying])[0].state, FallState.LYING_AFTER_FALL)
        score["value"] = .1
        self.assertEqual(engine.update(4, [lying])[0].state, FallState.LYING_AFTER_FALL)
        self.assertEqual(engine.update(5, [upright])[0].state, FallState.LYING_AFTER_FALL)
        self.assertEqual(engine.update(6, [upright])[0].state, FallState.RECOVERED)

    def test_low_confidence_pose_does_not_clear_a_confirmed_incident(self) -> None:
        engine = PerTrackFallEngine(
            lambda _features: .9,
            window_size=1,
            probability_threshold=.5,
            geometry_threshold=.5,
            possible_consecutive=1,
            confirm_consecutive=1,
            latch_fall=False,
            require_upright_transition=False,
            lying_consecutive=1,
        )
        lying = _pose(horizontal=True, bbox=(0, 0, 120, 40))
        self.assertEqual(engine.update(1, [lying])[0].state, FallState.FALL_CONFIRMED)
        self.assertEqual(engine.update(2, [lying])[0].state, FallState.LYING_AFTER_FALL)

        low_confidence = PoseObservation(
            track_id=1,
            keypoints_xy=lying.keypoints_xy,
            bbox_xyxy=lying.bbox_xyxy,
            keypoint_confidence=np.zeros(17, dtype=np.float32),
        )
        judgment = engine.update(3, [low_confidence])[0]
        self.assertFalse(judgment.input_valid)
        self.assertEqual(judgment.state, FallState.LYING_AFTER_FALL)
