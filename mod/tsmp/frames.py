"""Frames exchanged between the mod and the launcher, and relayed unchanged
between the two launchers.

Layout (little endian):
    kind     u8    see MSG / COMMAND / CONTROL
    id       u32   message id for MSG, otherwise 0
    length   u32   payload length
    payload  bytes

MSG      host -> guest  a serialized simulation message, to be handed to the
                        guest's game client as is
COMMAND  guest -> host  UTF-8 JSON {"name": ..., "args": [...]}: a command the
                        guest's game client issued
CONTROL  any direction  UTF-8 JSON {"type": ..., ...}
NATIVE   any direction  a call a game client made into its simulation without
                        passing through Python, to be repeated in the other
                        simulation; id is the call's hash (see tsmp/native.py)
PART     any direction  a piece of a large payload: the host's snapshot for a
                        guest (see tsmp/resync.py), a guest's household (see
                        tsmp/household.py); id is the transfer's (see
                        tsmp/transfer.py)

Pure Python, no game imports. crates/tsmp-net/src/live.rs is the other
implementation of this format.
"""

import json
import struct

MSG = 1
COMMAND = 2
CONTROL = 3
NATIVE = 4
PART = 7

MAX_PAYLOAD = 64 * 1024 * 1024

_HEADER = struct.Struct("<BII")


def pack(kind, ident, payload):
    return _HEADER.pack(kind, ident & 0xFFFFFFFF, len(payload)) + payload


def pack_json(kind, value):
    return pack(kind, 0, json.dumps(value, separators=(",", ":")).encode("utf-8"))


def control(type_, **fields):
    fields["type"] = type_
    return pack_json(CONTROL, fields)


def command(name, args):
    return pack_json(COMMAND, {"name": name, "args": [str(a) for a in args]})


def number(value):
    """An id or count from a CONTROL frame: ids are 64 bits wide and travel
    as text. 0 for anything that is not a number."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_json(payload):
    """The JSON object in a COMMAND or CONTROL payload, or None if it is not one."""
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class Decoder:
    """Reassembles frames from a byte stream."""

    def __init__(self):
        self._buffer = bytearray()

    def feed(self, data):
        """Add received bytes; return the complete frames as
        (kind, id, payload) tuples. Raises ValueError on a corrupt stream."""
        self._buffer += data
        frames = []
        pos = 0
        size = len(self._buffer)
        while size - pos >= _HEADER.size:
            kind, ident, length = _HEADER.unpack_from(self._buffer, pos)
            if length > MAX_PAYLOAD:
                raise ValueError("frame of {} bytes is too large".format(length))
            end = pos + _HEADER.size + length
            if end > size:
                break
            frames.append((kind, ident, bytes(self._buffer[pos + _HEADER.size:end])))
            pos = end
        if pos:
            del self._buffer[:pos]
        return frames
