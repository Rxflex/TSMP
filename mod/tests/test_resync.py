import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import resync  # noqa: E402


class SnapshotTest(unittest.TestCase):
    def test_round_trip(self):
        save, objects = b"save " * 1000, b"objects " * 300
        payload = resync.pack(0x1234567890ABCDEF, save, objects)
        self.assertLess(len(payload), len(save))
        self.assertEqual(resync.unpack(payload), (0x1234567890ABCDEF, save, objects))

    def test_a_zone_without_objects(self):
        self.assertEqual(resync.unpack(resync.pack(7, b"save")), (7, b"save", b""))

    def test_reads_a_snapshot_from_a_game_without_compression(self):
        compression, resync.zlib = resync.zlib, None
        try:
            payload = resync.pack(7, b"save", b"objects")
        finally:
            resync.zlib = compression
        self.assertIn(b"saveobjects", payload)
        self.assertEqual(resync.unpack(payload), (7, b"save", b"objects"))

    def test_rejects_what_is_not_a_snapshot(self):
        payload = resync.pack(7, b"save", b"objects")
        for damaged in (b"", payload[:10], b"XXXX" + payload[4:], payload[:-3]):
            with self.assertRaises(ValueError):
                resync.unpack(damaged)


if __name__ == "__main__":
    unittest.main()
