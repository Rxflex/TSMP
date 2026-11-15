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


class RenameTest(unittest.TestCase):
    def test_renames_ids_wherever_they_are(self):
        try:
            from google.protobuf import descriptor_pb2
        except ImportError:
            self.skipTest("no protobuf here")
        message = descriptor_pb2.FileOptions()
        for value in (5, 6, 5):
            message.uninterpreted_option.add().positive_int_value = value
        message.uninterpreted_option[0].negative_int_value = 6
        self.assertEqual(resync.rename(message, {5: 50, 6: 60}), 4)
        self.assertEqual([o.positive_int_value for o in message.uninterpreted_option], [50, 60, 50])
        self.assertEqual(message.uninterpreted_option[0].negative_int_value, 60)

    def test_a_zone_the_host_names_as_we_do_keeps_its_name(self):
        self.assertEqual(resync.host_zone(7), 7)


if __name__ == "__main__":
    unittest.main()
