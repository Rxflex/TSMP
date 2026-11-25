"""Minimal protobuf wire-format reader.

Enough to walk the structure of the game's messages without its protobuf
runtime, which does not parse outside the game. Pure Python, no game imports.
"""

import struct

VARINT = 0
FIXED64 = 1
BYTES = 2
FIXED32 = 5


def _varint(data, pos):
    result = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def fields(data):
    """Yield (field_number, wire_type, value) for one message.

    Varints come out as ints, fixed-width fields as unsigned ints, and
    length-delimited fields as bytes (nested messages are not descended into).
    Raises ValueError on malformed input.
    """
    pos = 0
    end = len(data)
    try:
        while pos < end:
            key, pos = _varint(data, pos)
            number, wire_type = key >> 3, key & 7
            if wire_type == VARINT:
                value, pos = _varint(data, pos)
            elif wire_type == FIXED64:
                value = struct.unpack_from("<Q", data, pos)[0]
                pos += 8
            elif wire_type == BYTES:
                length, pos = _varint(data, pos)
                if pos + length > end:
                    raise ValueError("truncated field {}".format(number))
                value = bytes(data[pos:pos + length])
                pos += length
            elif wire_type == FIXED32:
                value = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            else:
                raise ValueError("unsupported wire type {}".format(wire_type))
            yield number, wire_type, value
    except (IndexError, struct.error):
        raise ValueError("truncated message")


def first_varint(data, number):
    """The first top-level varint field `number` of a message, or 0."""
    try:
        for field, wire_type, value in fields(data):
            if field == number and wire_type == VARINT:
                return value
    except ValueError:
        pass
    return 0


def travel(data):
    """(zone id, [sim ids]) of a TravelSimsToZone message: the zone in
    fixed64 field 1, the travellers in packed fixed64 field 2. (0, []) for
    what cannot be read."""
    zone_id, sim_ids = 0, []
    try:
        for number, wire_type, value in fields(data):
            if number == 1 and wire_type == FIXED64:
                zone_id = value
            elif number == 2 and wire_type == BYTES and len(value) % 8 == 0:
                sim_ids.extend(struct.unpack("<{}Q".format(len(value) // 8), value))
            elif number == 2 and wire_type == FIXED64:
                sim_ids.append(value)
    except ValueError:
        return 0, []
    return zone_id, sim_ids


def _put_varint(out, value):
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)


def put_field(out, number, wire_type, value):
    """Append one field to the bytearray `out`; the inverse of `fields`."""
    _put_varint(out, (number << 3) | wire_type)
    if wire_type == VARINT:
        _put_varint(out, value)
    elif wire_type == FIXED64:
        out += struct.pack("<Q", value)
    elif wire_type == BYTES:
        _put_varint(out, len(value))
        out += value
    elif wire_type == FIXED32:
        out += struct.pack("<I", value)
    else:
        raise ValueError("unsupported wire type {}".format(wire_type))


def _operation_type(operation):
    for number, wire_type, value in fields(operation):
        if number == 1 and wire_type == VARINT:
            return value
    return 0


def split_view_update(data, op_type):
    """Split a serialized `ViewUpdate` by operation type.

    Returns (without, only): two serialized view updates, the first holding
    every operation except those of type `op_type` and the second holding only
    those. Either is b"" when it would contain no operations. When no
    operation matches, `without` is `data` itself.
    """
    without = bytearray()
    only = bytearray()
    matched = False
    for number, wire_type, entry in fields(data):
        if number != 1 or wire_type != BYTES:
            put_field(without, number, wire_type, entry)
            continue
        # Everything in the entry that is not an operation list (the channel
        # the operations are addressed to) goes into both halves.
        common = bytearray()
        kept = bytearray()
        taken = bytearray()
        for number, wire_type, value in fields(entry):
            if number != 2 or wire_type != BYTES:
                put_field(common, number, wire_type, value)
                continue
            for number, wire_type, operation in fields(value):
                if number == 1 and wire_type == BYTES and _operation_type(operation) == op_type:
                    put_field(taken, number, wire_type, operation)
                else:
                    put_field(kept, number, wire_type, operation)
        if kept:
            half = bytearray(common)
            put_field(half, 2, BYTES, bytes(kept))
            put_field(without, 1, BYTES, bytes(half))
        if taken:
            matched = True
            half = bytearray(common)
            put_field(half, 2, BYTES, bytes(taken))
            put_field(only, 1, BYTES, bytes(half))
    if not matched:
        return data, b""
    return bytes(without), bytes(only)


def view_update_ops(data):
    """Yield (manager_id, object_id, op_type, op_data) for every operation in
    a serialized `ViewUpdate` (the payload of MSG_OBJECTS_VIEW_UPDATE).

    Field numbers, as seen in recorded traffic and matching the names in the
    game's Distributor_pb2 / DistributorOps_pb2:
        ViewUpdate.entries = 1
        ViewUpdateEntry.primary_channel = 1, .operation_list = 2
        OperationChannel.id = 1; its message has manager_id = 1, object_id = 2
        OperationList.operations = 1
        Operation.type = 1, .data = 2
    """
    for number, _, entry in fields(data):
        if number != 1:
            continue
        manager_id = object_id = 0
        operation_lists = []
        for number, _, value in fields(entry):
            if number == 1:
                for number, _, channel_id in fields(value):
                    if number == 1:
                        for number, _, part in fields(channel_id):
                            if number == 1:
                                manager_id = part
                            elif number == 2:
                                object_id = part
            elif number == 2:
                operation_lists.append(value)
        for operation_list in operation_lists:
            for number, _, operation in fields(operation_list):
                if number != 1:
                    continue
                op_type = 0
                op_data = b""
                for number, _, value in fields(operation):
                    if number == 1:
                        op_type = value
                    elif number == 2:
                        op_data = value
                yield manager_id, object_id, op_type, op_data
