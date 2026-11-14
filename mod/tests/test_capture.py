import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tsmp import capture, paths  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class NonClosingBytesIO(io.BytesIO):
    def close(self):
        pass


class CaptureRoundTrip(unittest.TestCase):
    def test_messages_and_commands_round_trip(self):
        clock = FakeClock()
        stream = NonClosingBytesIO()
        writer = capture.CaptureWriter(stream, clock=clock)
        clock.now = 101.5
        self.assertTrue(writer.write_message(7, b"\x01\x02\x03"))
        clock.now = 102.0
        self.assertTrue(writer.write_command(42, "interactions.select", ["12", 3]))

        stream.seek(0)
        records = list(capture.read(stream))

        self.assertEqual(records[0], (capture.KIND_MSG_OUT, 1.5, 7, b"\x01\x02\x03"))
        kind, elapsed, ident, payload = records[1]
        self.assertEqual((kind, elapsed, ident), (capture.KIND_COMMAND_IN, 2.0, 42))
        self.assertEqual(json.loads(payload), {"name": "interactions.select", "args": ["12", "3"]})

    def test_size_cap_stops_writing(self):
        stream = NonClosingBytesIO()
        writer = capture.CaptureWriter(stream, max_bytes=len(capture.MAGIC) + 30)
        self.assertTrue(writer.write_message(1, b"abc"))
        self.assertFalse(writer.write_message(2, b"x" * 100))
        self.assertTrue(writer.full)
        self.assertFalse(writer.write_message(3, b""))

        stream.seek(0)
        self.assertEqual([r[2] for r in capture.read(stream)], [1])

    def test_truncated_tail_is_ignored(self):
        stream = NonClosingBytesIO()
        writer = capture.CaptureWriter(stream)
        writer.write_message(1, b"complete")
        writer.write_message(2, b"cut off here")
        data = stream.getvalue()[:-5]

        records = list(capture.read(io.BytesIO(data)))

        self.assertEqual([r[3] for r in records], [b"complete"])

    def test_rejects_foreign_file(self):
        with self.assertRaises(ValueError):
            list(capture.read(io.BytesIO(b"not a capture")))


class FindUserDir(unittest.TestCase):
    def test_finds_parent_of_mods(self):
        root = os.path.abspath(os.sep)
        user_dir = os.path.join(root, "Docs", "The Sims 4")
        script = os.path.join(user_dir, "Mods", "tsmp", "tsmp.ts4script", "tsmp", "paths.py")
        self.assertEqual(paths.find_user_dir(script), user_dir)

    def test_none_outside_mods(self):
        self.assertIsNone(paths.find_user_dir(os.path.join(os.path.abspath(os.sep), "a", "b.py")))


if __name__ == "__main__":
    unittest.main()
