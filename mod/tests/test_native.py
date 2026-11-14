import struct
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

    def test_build_operation_is_queued_and_replayed(self):
        recorder = self.recorder
        recorder.sync = True
        recorder.start(receive=True)
        self.assertFalse(recorder.replay(native.BUILD_OP, 3, 4, 0, b"early"))
        data = self.memory.buffer(b"operation")
        stream = self.memory.table([0, 0, 0, 0, 9 | 2 << 32, 9, data, 0])
        self.call(self.zone, 7, 21, native.BUILD_OP, 3, 4, stream)
        self.call(self.zone, 7, 21, 0x1234, 0, 0, stream)
        self.assertEqual(recorder.outbox, [(native.BUILD_OP, 3, 4, 2, b"operation")])
        del self.calls[:]
        # The other game's call arrives: made on our connection, not recorded.
        self.assertTrue(recorder.replay(native.BUILD_OP, 5, 6, 2, b"theirs"))
        name, zone, connection, call, third, fourth, replayed = self.calls[0]
        self.assertEqual((name, zone, connection, call, third, fourth), ("receive", self.zone, 21, native.BUILD_OP, 5, 6))
        self.assertEqual(self.memory.u32(replayed + 0x20), 6)
        self.assertEqual(self.memory.u32(replayed + 0x24), 2)
        self.assertEqual(self.memory.u32(replayed + 0x28), 6)
        self.assertEqual(self.memory.read(self.memory.u64(replayed + 0x30), 6), b"theirs")
        self.assertEqual(len(recorder.outbox), 1)
        self.assertEqual((recorder.replayed, recorder.dropped), (1, 1))

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
