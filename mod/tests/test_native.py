import json
import os
import shutil
import struct
import tempfile
import unittest

from tsmp import native

BASE = 0x7FF000000000
BUILD = (0x6ACFF6C2, 0x10E1000)
LAYOUT = native.KNOWN_BUILDS[BUILD]
AREA_OBJECT = 0x1000  # offset of a fake area object placed inside the image


def image(build=BUILD, area=True):
    data = bytearray(0x1000000)
    struct.pack_into("<H", data, 0, 0x5A4D)
    struct.pack_into("<I", data, 0x3C, 0x138)
    struct.pack_into("<I", data, 0x138, 0x4550)
    struct.pack_into("<I", data, 0x138 + 8, build[0])
    struct.pack_into("<I", data, 0x138 + 24 + 56, build[1])
    for table, slot, expected in LAYOUT.slots.values():
        struct.pack_into("<Q", data, table + 8 * slot, BASE + expected)
    if area:
        struct.pack_into("<Q", data, LAYOUT.area_global, BASE + AREA_OBJECT)
        struct.pack_into("<Q", data, AREA_OBJECT, BASE + LAYOUT.area_table)
    return data


MAPPINGS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "mappings")


class MappingFiles(unittest.TestCase):
    def setUp(self):
        self.known = dict(native.KNOWN_BUILDS)
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        native.KNOWN_BUILDS.clear()
        native.KNOWN_BUILDS.update(self.known)
        shutil.rmtree(self.directory)

    def test_every_file_in_the_repository_parses_and_is_named_by_its_version(self):
        names = [name for name in os.listdir(MAPPINGS) if name.endswith(".json")]
        self.assertTrue(names)
        for name in names:
            with open(os.path.join(MAPPINGS, name)) as source:
                _, layout = native.parse_mapping(json.load(source))
            self.assertEqual(name, layout.game_version + ".json")

    def test_the_file_of_the_built_in_build_says_the_same(self):
        with open(os.path.join(MAPPINGS, LAYOUT.game_version + ".json")) as source:
            build, layout = native.parse_mapping(json.load(source))
        self.assertEqual(build, BUILD)
        self.assertEqual(layout, LAYOUT)

    def test_a_new_build_is_added_and_a_broken_file_is_reported(self):
        with open(os.path.join(MAPPINGS, LAYOUT.game_version + ".json")) as source:
            data = json.load(source)
        data.update(game_version="9.9.9.9", build="1234abcd-2000000")
        with open(os.path.join(self.directory, "9.9.9.9.json"), "w") as target:
            json.dump(data, target)
        del data["slots"]
        with open(os.path.join(self.directory, "broken.json"), "w") as target:
            json.dump(data, target)
        lines = native.load_mappings(self.directory)
        self.assertEqual(native.KNOWN_BUILDS[(0x1234ABCD, 0x2000000)].game_version, "9.9.9.9")
        self.assertEqual(len(lines), 2)
        self.assertIn("broken.json ignored", lines[1])

    def test_a_missing_folder_is_not_an_error(self):
        self.assertEqual(native.load_mappings(os.path.join(self.directory, "none")), [])


def probe(data):
    return native.probe(native.BytesMemory(bytes(data), BASE))


class ProbeTest(unittest.TestCase):
    def test_known_build_in_session(self):
        found = probe(image())
        self.assertEqual(found["build"], "6acff6c2-10e1000")
        self.assertTrue(found["known"])
        self.assertEqual(found["mismatched"], [])
        self.assertEqual(found["area"], "ok")

    def test_no_session(self):
        self.assertEqual(probe(image(area=False))["area"], "none")

    def test_unknown_build_reads_nothing_else(self):
        found = probe(image(build=(1, 2)))
        self.assertFalse(found["known"])
        self.assertNotIn("mismatched", found)
        self.assertNotIn("area", found)

    def test_changed_table_stops_before_following_pointers(self):
        data = image()
        table, slot, _ = LAYOUT.slots["area.send"]
        struct.pack_into("<Q", data, table + 8 * slot, BASE + 0x1234)
        found = probe(data)
        self.assertEqual(found["mismatched"], ["area.send"])
        self.assertNotIn("area", found)

    def test_not_an_image(self):
        self.assertEqual(probe(bytearray(0x1000))["error"], "not a PE image")

    def test_failure_is_reported_not_raised(self):
        self.assertIn("error", probe(bytearray(8)))


class RecorderTest(unittest.TestCase):
    """Hooks real native tables built in this process, standing in for the game's."""

    def setUp(self):
        self.memory = memory = native.Memory()
        self.calls = []

        def original(name, arguments, result):
            def function(*args):
                self.calls.append((name,) + args)
                return result

            return memory.callback(function, arguments)

        send = original("send", 6, 11)
        broadcast = original("broadcast", 5, 12)
        receive = original("receive", 6, 13)

        def stream_new(stream, data, size, limit):
            memory.write_u64(stream + 0x20, size)
            memory.write_u64(stream + 0x28, limit)
            memory.write_u64(stream + 0x30, data)
            return stream

        stream_new = memory.callback(stream_new, 4)
        # Each table is preceded by the entry describing its type.
        area_table = memory.table([0xAAAA, 0, 0, broadcast, send]) + 8
        zone_table = memory.table([0xBBBB] + [0] * 7 + [receive]) + 8
        self.zone = memory.table([zone_table, 0, 0, 0, 0x10, 0, 0])
        node = memory.table([1, self.zone, 0])
        buckets = memory.table([0, node])
        # The area object: interface 0xB8 in, zone map further on.
        area = memory.table([0] * 80)
        self.interface = area + 0xB8
        memory.write_u64(self.interface, area_table)
        memory.write_u64(area + 0x268, buckets)
        memory.write_u64(area + 0x270, 2)
        self.area_table = area_table
        self.zone_table = zone_table
        self.layout = native.Layout(
            game_version="test",
            area_global=memory.table([self.interface]),
            area_table=area_table,
            area_slots=4,
            area_interface=0xB8,
            zone_map=(0x268, 0x270),
            zone_table=zone_table,
            zone_slots=8,
            stream_new=stream_new,
            slots={
                "zone.receive": (zone_table, 7, receive),
                "area.broadcast": (area_table, 2, broadcast),
                "area.send": (area_table, 3, send),
            },
        )
        self.recorder = native.Recorder(memory, 0, self.layout)

    def call(self, obj, slot, *args):
        function = self.memory.u64(self.memory.u64(obj) + 8 * slot)
        return self.memory.function(function, len(args) + 1)(obj, *args)

    def test_zones_found(self):
        self.assertEqual(native.zones(self.memory, 0, self.layout), [self.zone])

    def test_calls_are_counted_and_passed_on(self):
        self.assertEqual(self.recorder.start(), 1)
        data = self.memory.table([0x0807060504030201])
        self.assertEqual(self.call(self.interface, 3, 5, 6, 719, data, 8), 11)
        self.assertEqual(self.call(self.interface, 2, 5, 40, data, 4), 12)
        self.assertEqual(self.calls, [("send", self.interface, 5, 6, 719, data, 8), ("broadcast", self.interface, 5, 40, data, 4)])
        self.assertEqual(self.recorder.counts, {("send", 719): [1, 8], ("broadcast", 40): [1, 4]})
        self.assertEqual(self.recorder.samples, [("send", 719, 8, bytes(range(1, 9)))])
        self.assertEqual(self.recorder.errors, 0)
        # The type entry before the table came along with the copy.
        self.assertEqual(self.memory.u64(self.memory.u64(self.interface) - 8), 0xAAAA)

    def test_zone_calls_only_when_asked(self):
        self.recorder.start()
        self.assertEqual(self.memory.u64(self.zone), self.zone_table)
        self.assertEqual(self.recorder.start(receive=True), 1)
        data = self.memory.table([0x1122334455667788] * 2)
        stream = self.memory.table([0, 0, 0, 0, 16, 0, data, 0])
        self.assertEqual(self.call(self.zone, 7, 1, native.BUILD_OP, 0, 0, stream), 13)
        self.assertEqual(self.recorder.counts, {("receive", native.BUILD_OP): [1, 16]})
        self.assertEqual(len(self.recorder.samples[0][3]), 16)

    def test_failing_note_still_passes_the_call_on(self):
        self.recorder.start()
        # A size with no data behind it: reading the sample is skipped, and a
        # note that raises must not lose the call.
        self.recorder.note = None
        self.assertEqual(self.call(self.interface, 3, 5, 6, 719, 0, 8), 11)
        self.assertEqual(self.recorder.errors, 1)
        self.assertEqual(len(self.calls), 1)

    def stream(self, data, limit=None, position=0, size=None):
        """A stream over `data` as the game's client hands it to the zone."""
        size = len(data) if size is None else size
        address = self.memory.buffer(data)
        return self.memory.table([0, 0, 0, 0, size | position << 32, size if limit is None else limit, address, 0])

    def replayed_stream(self):
        name, zone, connection, call, third, fourth, stream = self.calls[-1]
        self.assertEqual((name, zone, call), ("receive", self.zone, native.BUILD_OP))
        return connection, third, fourth, stream

    def test_build_operation_is_queued_and_replayed(self):
        recorder = self.recorder
        recorder.sync = True
        recorder.start(receive=True)
        theirs = native.Op(native.BUILD_OP, 5, 6, 2, True, b"theirs")
        # No call of our client yet: there is no connection to replay on.
        self.assertFalse(recorder.replay(theirs))
        stream = self.stream(b"operation", position=2)
        self.call(self.zone, 7, 21, native.BUILD_OP, 3, 4, stream)
        self.call(self.zone, 7, 21, 0x1234, 0, 0, stream)
        self.assertEqual(recorder.take(), ([native.Op(native.BUILD_OP, 3, 4, 2, True, b"operation")], []))
        self.assertEqual(recorder.take(), ([], []))
        del self.calls[:]
        # The other game's call arrives: made on our connection, not recorded.
        self.assertTrue(recorder.replay(theirs))
        connection, third, fourth, replayed = self.replayed_stream()
        self.assertEqual((connection, third, fourth), (21, 5, 6))
        self.assertEqual(self.memory.u32(replayed + 0x20), 6)
        self.assertEqual(self.memory.u32(replayed + 0x24), 2)
        self.assertEqual(self.memory.u32(replayed + 0x28), 6)
        self.assertEqual(self.memory.read(self.memory.u64(replayed + 0x30), 6), b"theirs")
        self.assertEqual(recorder.take(), ([], []))

    def test_the_limit_kind_of_the_sender_is_replayed(self):
        recorder = self.recorder
        recorder.sync = True
        recorder.start(receive=True)
        self.call(self.zone, 7, 21, native.BUILD_OP, 3, 4, self.stream(b"operation", limit=0))
        (op,), _ = recorder.take()
        self.assertFalse(op.limit_is_size)
        # Ours had its size as its limit, the sender's did not.
        self.call(self.zone, 7, 21, native.BUILD_OP, 3, 4, self.stream(b"mine"))
        self.assertTrue(recorder.take()[0][0].limit_is_size)
        self.assertTrue(recorder.replay(op._replace(data=b"theirs")))
        self.assertEqual(self.memory.u32(self.replayed_stream()[3] + 0x28), 0)

    def test_a_connection_is_only_used_on_the_zone_object_it_was_seen_on(self):
        recorder = self.recorder
        recorder.start(receive=True)
        op = native.Op(native.BUILD_OP, 5, 6, 0, True, b"theirs")
        self.call(self.zone, 7, 21, 0x1234, 0, 0, self.stream(b"x"))
        self.assertTrue(recorder.replay(op))
        # The zone was loaded again: its new session has a new connection.
        recorder.forget()
        self.assertFalse(recorder.replay(op))
        self.call(self.zone, 7, 22, 0x1234, 0, 0, self.stream(b"x"))
        self.assertTrue(recorder.replay(op))
        self.assertEqual(self.replayed_stream()[0], 22)
        # A new zone object where the old one was has the game's own table.
        self.memory.write_u64(self.zone, self.zone_table)
        self.assertFalse(recorder.replay(op))
        # That attempt hooked it, but its client has not called it yet.
        self.assertNotEqual(self.memory.u64(self.zone), self.zone_table)
        self.assertFalse(recorder.replay(op))
        self.call(self.zone, 7, 23, 0x1234, 0, 0, self.stream(b"x"))
        self.assertTrue(recorder.replay(op))
        self.assertEqual(self.replayed_stream()[0], 23)

    def test_an_operation_too_large_to_share_is_counted(self):
        recorder = self.recorder
        recorder.sync = True
        recorder.start(receive=True)
        size = native.MAX_SYNC_BYTES + 1
        self.call(self.zone, 7, 21, native.BUILD_OP, 3, 4, self.stream(bytes(64), size=size))
        self.assertEqual(recorder.take(), ([], [size]))

    def test_nothing_is_kept_unless_syncing(self):
        self.recorder.start(receive=True)
        self.call(self.zone, 7, 21, native.BUILD_OP, 3, 4, self.stream(b"operation"))
        self.assertEqual(self.recorder.take(), ([], []))

    def test_stop_restores_tables(self):
        self.recorder.start(receive=True)
        self.assertEqual(self.recorder.stop(), 2)
        self.assertEqual(self.memory.u64(self.interface), self.area_table)
        self.assertEqual(self.memory.u64(self.zone), self.zone_table)
        self.assertEqual(self.call(self.interface, 3, 5, 6, 719, 0, 0), 11)
        self.assertEqual(self.recorder.counts, {})
        # Starting again reuses the same copies.
        self.assertEqual(self.recorder.start(receive=True), 2)


if __name__ == "__main__":
    unittest.main()
