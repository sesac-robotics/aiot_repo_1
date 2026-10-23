"""Regression tests for the actual PC4 feedback filter supplied by the team."""
import unittest
from fix_pc4_feedback import patched_source


ORIGINAL = '''def forward(data):
    output = []
    serial_buffer = bytearray(data)
    while b"\\n" in serial_buffer:
        feedback, _, remaining = serial_buffer.partition(b"\\n")
        serial_buffer = bytearray(remaining)
        if feedback.startswith(b"DOOR,") and feedback in (b"DOOR,0", b"DOOR,1"):
            output.append(bytes(feedback) + b"\\n")
    return output
'''


class FeedbackFixTests(unittest.TestCase):
    def test_arduino_crlf_and_multiple_lines(self):
        original, fixed = {}, {}
        exec(ORIGINAL, original)
        exec(patched_source(ORIGINAL), fixed)
        data = b'ACK,V\r\nDOOR,1\r\nSTATE,1\r\nDOOR,0\r\n'
        self.assertEqual(original['forward'](data), [])
        self.assertEqual(fixed['forward'](data), [b'DOOR,1\n', b'DOOR,0\n'])
        self.assertEqual(fixed['forward'](b'DOOR,1\nDOOR,2\r\n'), [b'DOOR,1\n'])

    def test_repeat_is_unchanged_and_unknown_file_rejected(self):
        fixed = patched_source(ORIGINAL)
        self.assertEqual(patched_source(fixed), fixed)
        with self.assertRaises(ValueError):
            patched_source('print("different bridge")\n')


if __name__ == '__main__':
    unittest.main()
