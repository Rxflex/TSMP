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
ROUTED   host game <-> its launcher only
                        one whole frame of any other kind as the payload; id
                        is the peer the launcher numbered the guest it comes
                        from or goes to, 0 for every guest

A host's launcher may relay for several guests. Once the host's mod says in
its `state` that it speaks version 3, the launcher wraps each frame a guest
sends in ROUTED with that guest's peer, and unwraps a ROUTED frame from the
host's game to send the frame inside to that peer alone; a plain frame from
the host's game goes to every guest. Nothing else ever sees ROUTED: a
guest's game and the wire between two launchers carry the frames as they
were, and a launcher from before version 3 neither wraps nor unwraps.

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
ROUTED = 8

MAX_PAYLOAD = 64 * 1024 * 1024

_HEADER = struct.Struct("<BII")


def pack(kind, ident, payload):
    return _HEADER.pack(kind, ident & 0xFFFFFFFF, len(payload)) + payload


def route(peer, frame):
    """A packed frame wrapped for one peer, or for every one with peer 0."""
    return pack(ROUTED, peer, frame)


def unroute(kind, ident, payload):
    """(kind, id, payload, peer) of a frame as it arrived: the frame inside
    a ROUTED one and the peer it came from, or the frame itself and None.
    Raises ValueError for a ROUTED frame that does not hold exactly one
    other frame."""
    if kind != ROUTED:
        return kind, ident, payload, None
    if len(payload) < _HEADER.size:
        raise ValueError("routed frame is cut off")
    inner, inner_ident, length = _HEADER.unpack_from(payload)
    if inner == ROUTED or _HEADER.size + length != len(payload):
        raise ValueError("routed frame does not hold one frame")
    return inner, inner_ident, payload[_HEADER.size:], ident


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
            # A routed frame holds a whole frame, header and all.
            if length > MAX_PAYLOAD + (_HEADER.size if kind == ROUTED else 0):
                raise ValueError("frame of {} bytes is too large".format(length))
            end = pos + _HEADER.size + length
            if end > size:
                break
            frames.append((kind, ident, bytes(self._buffer[pos + _HEADER.size:end])))
            pos = end
        if pos:
            del self._buffer[:pos]
        return frames
