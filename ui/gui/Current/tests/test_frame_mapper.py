from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frame_mapper import annotations_from_metadata


class FrameMapperTest(unittest.TestCase):
    def test_sustained_post_fall_lying_is_displayed_as_fall(self):
        annotations, summary = annotations_from_metadata({
            "source_size": [100, 100],
            "persons": [{"track_id": 7, "bbox_xyxy": [0, 0, 50, 50]}],
            "fall_judgments": [{"track_id": 7, "state": "lying_after_fall"}],
        })

        self.assertEqual(annotations[0]["state"], "fall")
        self.assertEqual(summary["state"], "fall")

