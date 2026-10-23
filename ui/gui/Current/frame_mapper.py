"""Map Main Service pose/fall metadata into Current CCTV drawing annotations."""
from __future__ import annotations


def annotations_from_metadata(metadata: object) -> tuple[list[dict], dict]:
    """Join each person to its fall judgment by track ID.

    Returned coordinates are normalized to 0..1, so the CCTV tab can draw
    correctly on a JPEG resized by the Main Service. The Current UI exposes
    only two display states: ``normal`` and ``fall``.
    """
    if not isinstance(metadata, dict):
        return [], {"state": "normal", "person_count": 0}

    source_size = metadata.get("source_size") or metadata.get("image", {}).get("source_size") or [1, 1]
    source_width, source_height = max(float(source_size[0]), 1), max(float(source_size[1]), 1)
    judgments = {str(item.get("track_id")): item for item in metadata.get("fall_judgments", [])}
    annotations: list[dict] = []
    has_fall = False

    for person in metadata.get("persons", []):
        judgment = judgments.get(str(person.get("track_id")), {})
        state = "fall" if judgment.get("state") in {"fall_confirmed", "lying_after_fall"} else "normal"
        has_fall = has_fall or state == "fall"
        bbox = person.get("bbox_xyxy")
        normalized_bbox = None if not isinstance(bbox, list) or len(bbox) != 4 else [
            bbox[0] / source_width, bbox[1] / source_height,
            bbox[2] / source_width, bbox[3] / source_height,
        ]
        # CCTV 화면은 포즈 관절점 대신, 사람별 이벤트 상태만 표시한다.
        # landmarks는 Main Service 응답에는 남아 있지만 GUI로 전달하지 않는다.
        annotations.append({"bbox": normalized_bbox, "state": state})

    return annotations, {"state": "fall" if has_fall else "normal", "person_count": len(annotations)}
