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

    def test_a_routed_frame_holds_a_whole_frame_under_the_peer(self):
        inner = frames.pack(frames.MSG, 11, b"abc")
        self.assertEqual(frames.route(3, inner), bytes.fromhex("08" "03000000" "0c000000") + inner)
        (routed,) = frames.Decoder().feed(frames.route(3, inner))
        self.assertEqual(frames.unroute(*routed), (frames.MSG, 11, b"abc", 3))
        self.assertEqual(frames.unroute(frames.MSG, 11, b"abc"), (frames.MSG, 11, b"abc", None))

    def test_a_routed_frame_may_hold_the_largest_frame(self):
        header = bytes.fromhex("08" "01000000") + (frames.MAX_PAYLOAD + 9).to_bytes(4, "little")
        self.assertEqual(frames.Decoder().feed(header), [])
        with self.assertRaises(ValueError):
            frames.Decoder().feed(bytes.fromhex("08" "01000000") + (frames.MAX_PAYLOAD + 10).to_bytes(4, "little"))

    def test_a_routed_frame_that_does_not_hold_one_frame_is_rejected(self):
        inner = frames.pack(frames.MSG, 11, b"abc")
        for payload in (inner[:5], inner[:-1], inner + b"x", frames.route(2, inner)):
            with self.assertRaises(ValueError):
                frames.unroute(frames.ROUTED, 1, payload)

    def test_parse_json_rejects_garbage(self):
        self.assertIsNone(frames.parse_json(b"\xff\xfe"))
        self.assertIsNone(frames.parse_json(b"[1]"))


if __name__ == "__main__":
    unittest.main()
