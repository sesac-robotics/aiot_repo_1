"""Adapters from Ultralytics tracking tensors to testable fall-pipeline values."""

from __future__ import annotations

import numpy as np

from fall_pipeline import PoseObservation


def build_observations(
    *,
    track_ids: np.ndarray,
    boxes_xyxy: np.ndarray,
    keypoints_xy: np.ndarray,
    detection_confidences: np.ndarray,
    keypoint_confidences: np.ndarray | None,
) -> list[PoseObservation]:
    """Validate aligned YOLO outputs and build tracked COCO-17 observations."""

    ids = np.asarray(track_ids).reshape(-1)
    boxes = np.asarray(boxes_xyxy, dtype=np.float32)
    poses = np.asarray(keypoints_xy, dtype=np.float32)
    confidences = np.asarray(detection_confidences, dtype=np.float32).reshape(-1)
    count = len(ids)
    if boxes.shape != (count, 4) or poses.shape != (count, 17, 2) or confidences.shape != (count,):
        raise ValueError("YOLO tracking boxes, IDs, confidences, and COCO-17 poses must be aligned")
    kp_conf = None if keypoint_confidences is None else np.asarray(keypoint_confidences, dtype=np.float32)
    if kp_conf is not None and kp_conf.shape != (count, 17):
        raise ValueError("YOLO keypoint confidences must have shape (people, 17)")
    observations: list[PoseObservation] = []
    for index, track_id in enumerate(ids):
        if not np.isfinite(track_id) or int(track_id) < 0:
            continue
        observations.append(
            PoseObservation(
                track_id=int(track_id),
                keypoints_xy=poses[index],
                bbox_xyxy=tuple(float(value) for value in boxes[index]),
                detection_confidence=float(confidences[index]),
                keypoint_confidence=None if kp_conf is None else kp_conf[index],
            )
        )
    return observations


def observations_from_yolo_result(result) -> list[PoseObservation]:
    """Extract only persistently tracked pose results; untracked detections are skipped."""

    boxes = result.boxes
    keypoints = result.keypoints
    if boxes is None or keypoints is None or boxes.id is None or keypoints.xy is None:
        return []
    return build_observations(
        track_ids=boxes.id.detach().cpu().numpy(),
        boxes_xyxy=boxes.xyxy.detach().cpu().numpy(),
        keypoints_xy=keypoints.xy.detach().cpu().numpy(),
        detection_confidences=boxes.conf.detach().cpu().numpy(),
        keypoint_confidences=None if keypoints.conf is None else keypoints.conf.detach().cpu().numpy(),
    )
