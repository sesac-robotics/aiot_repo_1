import math
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from validation import require_aware_time, normalize_face_embedding, validate_registered, validate_captured


def payload(**override):
    p = {"patient_id":"patient_001","model":"buffalo_l","embedding_dim":512,
         "embedding":[1.0]+[0.0]*511,"images_used":1,"created_at":"2026-10-16T09:15:07+00:00"}
    p.update(override)
    return p


class TestFaceJson(unittest.TestCase):
    def test_valid_registration(self):
        pid, model, vec, count, when=validate_registered(payload())
        self.assertEqual(pid,'patient_001')
        self.assertEqual(model,'buffalo_l')
        self.assertEqual(len(vec),512)
        self.assertAlmostEqual(math.sqrt(sum(x*x for x in vec)),1.0)
        self.assertEqual(count,1)
        self.assertEqual(when.tzinfo,timezone.utc)

    def test_normalizes_vectors(self):
        v=normalize_face_embedding(payload(embedding=[5.0]+[0.0]*511))
        self.assertEqual(v[0],1.0)

    def test_rejects_bad_dimension(self):
        with self.assertRaises(ValueError): normalize_face_embedding(payload(embedding_dim=16))
        with self.assertRaises(ValueError): normalize_face_embedding(payload(embedding=[1.0]*16))

    def test_rejects_bad_values(self):
        for values in ([0.0]*512, [math.nan]+[1.0]*511, [True]+[1.0]*511):
            with self.assertRaises(ValueError): normalize_face_embedding(payload(embedding=values))

    def test_rejects_model_mismatch(self):
        with self.assertRaises(ValueError): normalize_face_embedding(payload(model='mock_face_v1'))

    def test_capture_validation(self):
        cap=payload(event_id='ev-123',status='OK',timestamp='2026-10-19T10:00:00+09:00')
        eid,model,vec,stamp=validate_captured(cap)
        self.assertEqual(eid,'ev-123')
        self.assertEqual(stamp.utcoffset().total_seconds(),32400)

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError): require_aware_time('2026-10-19T10:00:00')

    def test_non_ok_capture_rejected(self):
        with self.assertRaises(ValueError):
            validate_captured(payload(event_id='ev-124',status='NoFace',timestamp='2026-10-19T10:00:00+09:00'))


if __name__=='__main__':unittest.main()
