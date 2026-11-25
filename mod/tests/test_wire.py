import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import wire  # noqa: E402

# A view update recorded from the game: one entry for manager 0 / object 0
# holding two operations of type 86.
RECORDED = bytes.fromhex(
    "0a510a060a040800100012470a21085612180804150000c04018d0cbc5b3fe0c20b99f37"
    "28003000380425000000000a22085612190804150000c04018d0cbc5b3fe0c20ffa03728"
    "c601300038052500000000"
)


class FieldsTest(unittest.TestCase):
    def test_reads_each_wire_type(self):
        data = bytes.fromhex("08ac02" "1203616263" "1d01000000" "210200000000000000")
        self.assertEqual(
            list(wire.fields(data)),
            [
                (1, wire.VARINT, 300),
                (2, wire.BYTES, b"abc"),
                (3, wire.FIXED32, 1),
                (4, wire.FIXED64, 2),
            ],
        )

    def test_empty_message_has_no_fields(self):
        self.assertEqual(list(wire.fields(b"")), [])

    def test_truncated_input_is_an_error(self):
        with self.assertRaises(ValueError):
            list(wire.fields(bytes.fromhex("1205616263")))
        with self.assertRaises(ValueError):
            list(wire.fields(bytes.fromhex("08")))


class ViewUpdateTest(unittest.TestCase):
    def test_walks_recorded_update(self):
        ops = list(wire.view_update_ops(RECORDED))
        self.assertEqual([(m, o, t) for m, o, t, _ in ops], [(0, 0, 86), (0, 0, 86)])
        self.assertEqual(len(ops[0][3]), 0x18)

    def test_reads_object_ids(self):
        channel = bytes.fromhex("0a04" "0801" "1007")  # id { manager_id: 1 object_id: 7 }
        operation = bytes.fromhex("0803" "1201ff")  # type 3, data ff
        operation_list = b"\x0a" + bytes([len(operation)]) + operation
        entry = b"\x0a" + bytes([len(channel)]) + channel + b"\x12" + bytes([len(operation_list)]) + operation_list
        update = b"\x0a" + bytes([len(entry)]) + entry
        self.assertEqual(list(wire.view_update_ops(update)), [(1, 7, 3, b"\xff")])


class SplitViewUpdateTest(unittest.TestCase):
    def test_no_match_returns_the_input(self):
        without, only = wire.split_view_update(RECORDED, 21)
        self.assertIs(without, RECORDED)
        self.assertEqual(only, b"")

    def test_put_field_is_the_inverse_of_fields(self):
        out = bytearray()
        for number, wire_type, value in wire.fields(RECORDED):
            wire.put_field(out, number, wire_type, value)
        self.assertEqual(bytes(out), RECORDED)

    def test_all_operations_matching_leaves_nothing(self):
        without, only = wire.split_view_update(RECORDED, 86)
        self.assertEqual(without, b"")
        self.assertEqual(only, RECORDED)

    def test_splits_one_entry_and_keeps_its_channel(self):
        channel = bytes.fromhex("0a0408031007")  # manager 3, object 7
        update = bytearray()
        for op_types in ((86, 21, 87), (86, 88, 87)):
            operations = bytearray()
            for op_type, data in zip(op_types, (b"a", b"b", b"c")):
                operation = bytearray()
                wire.put_field(operation, 1, wire.VARINT, op_type)
                wire.put_field(operation, 2, wire.BYTES, data)
                wire.put_field(operations, 1, wire.BYTES, bytes(operation))
            entry = bytearray()
            wire.put_field(entry, 1, wire.BYTES, channel)
            wire.put_field(entry, 2, wire.BYTES, bytes(operations))
            wire.put_field(update, 1, wire.BYTES, bytes(entry))

        without, only = wire.split_view_update(bytes(update), 21)

        self.assertEqual(
            list(wire.view_update_ops(without)),
            [(3, 7, 86, b"a"), (3, 7, 87, b"c"), (3, 7, 86, b"a"), (3, 7, 88, b"b"), (3, 7, 87, b"c")],
        )
        self.assertEqual(list(wire.view_update_ops(only)), [(3, 7, 21, b"b")])

    def test_malformed_input_raises(self):
        with self.assertRaises(ValueError):
            wire.split_view_update(b"\x0a\x05\x01", 21)

class TravelTest(unittest.TestCase):
    def test_reads_where_to_and_who(self):
        out = bytearray()
        wire.put_field(out, 1, wire.FIXED64, 0x1122334455667788)
        wire.put_field(out, 2, wire.BYTES, (5).to_bytes(8, "little") + (6).to_bytes(8, "little"))
        wire.put_field(out, 3, wire.FIXED64, 5)
        self.assertEqual(wire.travel(bytes(out)), (0x1122334455667788, [5, 6]))

    def test_what_is_not_a_travel(self):
        self.assertEqual(wire.travel(b"\xff"), (0, []))
        self.assertEqual(wire.travel(b""), (0, []))


if __name__ == "__main__":
    unittest.main()

