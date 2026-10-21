"""Per-person YOLO-Pose temporal fall analysis primitives.

This module keeps each tracked person's COCO-17 pose features in an independent
window.  It deliberately does not create synthetic MediaPipe joints or mix
sequences from different track IDs.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

import numpy as np


class FallState(str, Enum):
    NORMAL = "normal"
    POSSIBLE_FALL = "possible_fall"
    FALL_CONFIRMED = "fall_confirmed"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class PoseObservation:
    """One tracked YOLO COCO-17 pose in original-frame pixel coordinates."""

    track_id: int
    keypoints_xy: np.ndarray
    bbox_xyxy: tuple[float, float, float, float]
    detection_confidence: float | None = None
    keypoint_confidence: np.ndarray | None = None


@dataclass(frozen=True)
class TrackJudgment:
    track_id: int
    state: FallState
    frames_collected: int
    fall_probability: float | None
    geometry_score: float
    model_evidence: bool
    input_valid: bool = True
    input_rejected_reason: str | None = None
    fall_latched: bool = False
    confirmed_frame_id: int | None = None
    confirmed_timestamp_ms: int | None = None
    confirmed_at: str | None = None
    confirmed_score: float | None = None
    peak_fall_probability: float | None = None
    transition_evidence: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "track_id": self.track_id,
            "state": self.state.value,
            "frames_collected": self.frames_collected,
            "fall_probability": self.fall_probability,
            "geometry_score": self.geometry_score,
            "model_evidence": self.model_evidence,
            "input_valid": self.input_valid,
            "input_rejected_reason": self.input_rejected_reason,
            "fall_latched": self.fall_latched,
            "confirmed_frame_id": self.confirmed_frame_id,
            "confirmed_timestamp_ms": self.confirmed_timestamp_ms,
            "confirmed_at": self.confirmed_at,
            "confirmed_score": self.confirmed_score,
            "peak_fall_probability": self.peak_fall_probability,
            "transition_evidence": self.transition_evidence,
        }


@dataclass
class _TrackMemory:
    features: deque[list[float]]
    state: FallState = FallState.NORMAL
    evidence_frames: int = 0
    recovery_frames: int = 0
    last_seen_frame: int = -1
    fall_latched: bool = False
    confirmed_frame_id: int | None = None
    confirmed_timestamp_ms: int | None = None
    confirmed_at: str | None = None
    confirmed_score: float | None = None
    peak_fall_probability: float | None = None
    upright_frames: int = 0
    saw_upright_posture: bool = False


def extract_features(keypoints_xy: np.ndarray) -> list[float]:
    """Reproduce the bundled LSTM's three COCO-17 feature calculations.

    The supplied checkpoint was trained from nose (0), shoulders (5, 6), and
    hips (11, 12), in original image pixels.  Invalid geometry is rejected
    rather than silently mapped into another skeleton convention.
    """

    points = np.asarray(keypoints_xy, dtype=np.float32)
    if points.shape != (17, 2):
        raise ValueError(f"expected YOLO COCO-17 xy keypoints with shape (17, 2), got {points.shape}")
    if not np.isfinite(points).all():
        raise ValueError("keypoints contain non-finite coordinates")
    nose = points[0]
    mid_shoulder = (points[5] + points[6]) / 2
    mid_hip = (points[11] + points[12]) / 2
    delta = mid_hip - mid_shoulder
    angle = float(np.degrees(np.arctan2(delta[1], delta[0])))
    torso = float(np.linalg.norm(delta))
    head_distance = float(np.linalg.norm(nose - mid_hip))
    return [angle, torso, head_distance]


class PerTrackFallEngine:
    """Maintain isolated temporal LSTM windows and conservative fall states."""

    def __init__(
        self,
        predictor: Callable[[np.ndarray], float],
        *,
        window_size: int = 40,
        max_track_gap: int = 15,
        probability_threshold: float = 0.6,
        geometry_threshold: float = 0.7,
        possible_consecutive: int = 3,
        confirm_consecutive: int = 8,
        recover_consecutive: int = 15,
        min_keypoint_confidence: float = 0.05,
        latch_fall: bool = True,
        upright_consecutive: int = 5,
        require_upright_transition: bool = True,
    ) -> None:
        if window_size <= 0 or max_track_gap < 0:
            raise ValueError("window_size must be positive and max_track_gap must be non-negative")
        if not 0 < probability_threshold <= 1 or not 0 <= geometry_threshold <= 1:
            raise ValueError("thresholds must be probabilities in [0, 1]")
        if not 0 <= min_keypoint_confidence <= 1:
            raise ValueError("min_keypoint_confidence must be in [0, 1]")
        if min(possible_consecutive, confirm_consecutive, recover_consecutive, upright_consecutive) <= 0:
            raise ValueError("state transition frame counts must be positive")
        self.predictor = predictor
        self.window_size = window_size
        self.max_track_gap = max_track_gap
        self.probability_threshold = probability_threshold
        self.geometry_threshold = geometry_threshold
        self.possible_consecutive = possible_consecutive
        self.confirm_consecutive = confirm_consecutive
        self.recover_consecutive = recover_consecutive
        self.min_keypoint_confidence = min_keypoint_confidence
        self.latch_fall = latch_fall
        self.upright_consecutive = upright_consecutive
        self.require_upright_transition = require_upright_transition
        self._tracks: dict[int, _TrackMemory] = {}

    @staticmethod
    def geometry_score(observation: PoseObservation) -> float:
        """Score horizontal-body evidence from torso angle and bbox aspect ratio."""

        angle = extract_features(observation.keypoints_xy)[0]
        horizontal_torso = abs(float(np.cos(np.radians(angle))))
        x1, y1, x2, y2 = observation.bbox_xyxy
        width, height = max(0.0, x2 - x1), max(0.0, y2 - y1)
        landscape_bbox = min(width / max(height, 1e-6), 1.0)
        return float(0.6 * horizontal_torso + 0.4 * landscape_bbox)

    @staticmethod
    def is_upright(observation: PoseObservation) -> bool:
        """Return whether the torso is reliably closer to vertical than horizontal."""
        angle = extract_features(observation.keypoints_xy)[0]
        return abs(float(np.sin(np.radians(angle)))) >= 0.7

    def _purge_stale(self, frame_id: int) -> None:
        stale = [
            track_id
            for track_id, memory in self._tracks.items()
            if frame_id - memory.last_seen_frame > self.max_track_gap
        ]
        for track_id in stale:
            del self._tracks[track_id]

    def _transition(self, memory: _TrackMemory, evidence: bool) -> FallState:
        if self.latch_fall and memory.fall_latched:
            memory.state = FallState.FALL_CONFIRMED
            return memory.state
        if evidence:
            memory.evidence_frames += 1
            memory.recovery_frames = 0
            if memory.state in {FallState.NORMAL, FallState.RECOVERED}:
                if memory.evidence_frames >= self.possible_consecutive:
                    memory.state = FallState.POSSIBLE_FALL
            if memory.state is FallState.POSSIBLE_FALL and memory.evidence_frames >= self.confirm_consecutive:
                memory.state = FallState.FALL_CONFIRMED
                memory.fall_latched = self.latch_fall
            return memory.state

        memory.evidence_frames = 0
        if memory.state is FallState.POSSIBLE_FALL:
            memory.state = FallState.NORMAL
        elif memory.state is FallState.FALL_CONFIRMED:
            memory.recovery_frames += 1
            if memory.recovery_frames >= self.recover_consecutive:
                memory.state = FallState.RECOVERED
        else:
            memory.recovery_frames = 0
        return memory.state

    def _reset_memory(self, memory: _TrackMemory) -> None:
        memory.features.clear()
        keep_alert = self.latch_fall and memory.fall_latched
        memory.state = FallState.FALL_CONFIRMED if keep_alert else FallState.NORMAL
        memory.evidence_frames = 0
        memory.recovery_frames = 0
        if not keep_alert:
            memory.confirmed_frame_id = None
            memory.confirmed_timestamp_ms = None
            memory.confirmed_at = None
        memory.confirmed_score = None
        memory.peak_fall_probability = None
        memory.upright_frames = 0
        memory.saw_upright_posture = False

    def _input_rejection_reason(self, observation: PoseObservation) -> str | None:
        confidence = observation.keypoint_confidence
        if confidence is None:
            return "missing_keypoint_confidence"
        values = np.asarray(confidence, dtype=np.float32)
        if values.shape != (17,) or not np.isfinite(values).all():
            return "invalid_keypoint_confidence"
        required = values[[0, 5, 6, 11, 12]]
        if np.any(required < self.min_keypoint_confidence):
            return "low_required_keypoint_confidence"
        return None

    def update(
        self,
        frame_id: int,
        observations: list[PoseObservation],
        *,
        timestamp_ms: int | None = None,
        event_at: str | None = None,
    ) -> list[TrackJudgment]:
        """Update observed tracks; optional timestamps are stored at confirmation."""

        self._purge_stale(frame_id)
        updates: list[TrackJudgment] = []
        for observation in observations:
            if observation.track_id < 0:
                continue
            memory = self._tracks.setdefault(observation.track_id, _TrackMemory(deque(maxlen=self.window_size)))
            if memory.last_seen_frame >= 0 and frame_id != memory.last_seen_frame + 1:
                self._reset_memory(memory)
            memory.last_seen_frame = frame_id
            rejection_reason = self._input_rejection_reason(observation)
            if rejection_reason is not None:
                self._reset_memory(memory)
                updates.append(
                    TrackJudgment(
                        track_id=observation.track_id,
                        state=memory.state,
                        frames_collected=0,
                        fall_probability=None,
                        geometry_score=0.0,
                        model_evidence=False,
                        input_valid=False,
                        input_rejected_reason=rejection_reason,
                        fall_latched=memory.fall_latched,
                        confirmed_frame_id=memory.confirmed_frame_id,
                        confirmed_timestamp_ms=memory.confirmed_timestamp_ms,
                        confirmed_at=memory.confirmed_at,
                        confirmed_score=memory.confirmed_score,
                        peak_fall_probability=memory.peak_fall_probability,
                        transition_evidence=False,
                    )
                )
                continue
            memory.features.append(extract_features(observation.keypoints_xy))
            geometry = self.geometry_score(observation)
            if self.is_upright(observation):
                memory.upright_frames += 1
                if memory.upright_frames >= self.upright_consecutive:
                    memory.saw_upright_posture = True
            else:
                memory.upright_frames = 0
            transition_evidence = (not self.require_upright_transition) or memory.saw_upright_posture
            probability: float | None = None
            evidence = False
            if len(memory.features) == self.window_size:
                probability = float(self.predictor(np.asarray(memory.features, dtype=np.float32)))
                if not 0 <= probability <= 1:
                    raise ValueError(f"predictor returned probability outside [0, 1]: {probability}")
                evidence = (
                    probability >= self.probability_threshold
                    and geometry >= self.geometry_threshold
                    and transition_evidence
                )
            state = self._transition(memory, evidence)
            if state is FallState.FALL_CONFIRMED:
                if memory.confirmed_frame_id is None:
                    memory.confirmed_frame_id = frame_id
                    memory.confirmed_timestamp_ms = timestamp_ms
                    memory.confirmed_at = event_at
                    memory.confirmed_score = probability
                if probability is not None:
                    memory.peak_fall_probability = max(memory.peak_fall_probability or probability, probability)
            updates.append(
                TrackJudgment(
                    track_id=observation.track_id,
                    state=state,
                    frames_collected=len(memory.features),
                    fall_probability=probability,
                    geometry_score=geometry,
                    model_evidence=evidence,
                    fall_latched=memory.fall_latched,
                    confirmed_frame_id=memory.confirmed_frame_id,
                    confirmed_timestamp_ms=memory.confirmed_timestamp_ms,
                    confirmed_at=memory.confirmed_at,
                    confirmed_score=memory.confirmed_score,
                    peak_fall_probability=memory.peak_fall_probability,
                    transition_evidence=transition_evidence,
                )
            )
        return updates
