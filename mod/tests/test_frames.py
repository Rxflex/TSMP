import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import frames  # noqa: E402


class FramesTest(unittest.TestCase):
    def test_layout_is_kind_id_length_payload(self):
        self.assertEqual(frames.pack(frames.MSG, 11, b"abc"), bytes.fromhex("01" "0b000000" "03000000") + b"abc")

    def test_decoder_reassembles_frames_split_anywhere(self):
        stream = frames.pack(frames.MSG, 11, b"hello") + frames.pack(frames.COMMAND, 0, b"") + frames.control("gone")
        for step in (1, 2, 7, len(stream)):
            decoder = frames.Decoder()
            got = []
            for start in range(0, len(stream), step):
                got += decoder.feed(stream[start:start + step])
            self.assertEqual(
                got,
                [(frames.MSG, 11, b"hello"), (frames.COMMAND, 0, b""), (frames.CONTROL, 0, b'{"type":"gone"}')],
            )

    def test_oversized_frame_is_rejected(self):
        header = bytes.fromhex("01" "00000000" "ffffffff")
        with self.assertRaises(ValueError):
            frames.Decoder().feed(header)

    def test_command_and_control_round_trip(self):
        (_, _, payload), = frames.Decoder().feed(frames.command("interactions.select", [5487, "1"]))
        self.assertEqual(frames.parse_json(payload), {"name": "interactions.select", "args": ["5487", "1"]})
        (_, _, payload), = frames.Decoder().feed(frames.control("state", role="host", in_zone=True))
        self.assertEqual(frames.parse_json(payload), {"type": "state", "role": "host", "in_zone": True})

    def test_parse_json_rejects_garbage(self):
        self.assertIsNone(frames.parse_json(b"\xff\xfe"))
        self.assertIsNone(frames.parse_json(b"[1]"))


if __name__ == "__main__":
    unittest.main()
