"""Traffic capture file format (.tsmpcap).

Pure Python with no game imports, so the same code reads captures in tests
and tools outside the game.

Layout:
    magic   8 bytes  b"TSMPCAP1"
    records, each:
        kind     u8    see KIND_*
        time     f64   seconds since the capture started
        id       u32   message id (KIND_MSG_OUT) or session id (KIND_COMMAND_IN)
        length   u32   payload length
        payload  bytes

KIND_MSG_OUT payload is the serialized protobuf the simulation sent to the
game client. KIND_COMMAND_IN payload is UTF-8 JSON: {"name": ..., "args": [...]}.
"""

import json
import struct
import threading
import time

MAGIC = b"TSMPCAP1"

KIND_MSG_OUT = 0
KIND_COMMAND_IN = 1

_HEADER = struct.Struct("<BdII")


class CaptureWriter:
    def __init__(self, stream, max_bytes=None, clock=time.monotonic):
        self._stream = stream
        self._max_bytes = max_bytes
        self._clock = clock
        self._start = clock()
        self._lock = threading.Lock()
        self._written = len(MAGIC)
        self.full = False
        stream.write(MAGIC)

    def write(self, kind, ident, payload):
        """Append one record. Returns False once the size cap has been hit."""
        with self._lock:
            if self.full:
                return False
            size = _HEADER.size + len(payload)
            if self._max_bytes is not None and self._written + size > self._max_bytes:
                self.full = True
                self._stream.flush()
                return False
            elapsed = self._clock() - self._start
            self._stream.write(_HEADER.pack(kind, elapsed, ident & 0xFFFFFFFF, len(payload)))
            self._stream.write(payload)
            self._written += size
            return True

    def write_message(self, msg_id, data):
        return self.write(KIND_MSG_OUT, msg_id, data)

    def write_command(self, session_id, name, args):
        payload = json.dumps({"name": name, "args": [str(a) for a in args]}).encode("utf-8")
        return self.write(KIND_COMMAND_IN, session_id, payload)

    def flush(self):
        with self._lock:
            self._stream.flush()

    def close(self):
        with self._lock:
            self._stream.close()


def read(stream):
    """Yield (kind, time, id, payload) tuples. Stops cleanly at a truncated tail,
    which is what a capture looks like after the game was killed."""
    if stream.read(len(MAGIC)) != MAGIC:
        raise ValueError("not a TSMP capture file")
    while True:
        header = stream.read(_HEADER.size)
        if len(header) < _HEADER.size:
            return
        kind, elapsed, ident, length = _HEADER.unpack(header)
        payload = stream.read(length)
        if len(payload) < length:
            return
        yield kind, elapsed, ident, payload
