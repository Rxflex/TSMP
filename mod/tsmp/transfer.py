"""Large payloads sent in parts: the host's snapshot for a guest, a guest's
household for the host.

Sent as one frame, a snapshot of several megabytes would hold up everything
the two games say after it for as long as it takes to cross a slow link, the
receiver could not tell a slow transfer from a lost one, and the launchers
refuse frames past a size. In parts, with the receiver saying how much it
has, control frames pass in between, both sides see progress, and only a
window of parts is ever in flight.

    offer {id, what, size, ...}   sender -> receiver, before the parts
    PART frame, id = transfer id  u32 offset, then that many bytes on
    got {id, have}                receiver -> sender, as parts arrive
    drop {id}                     either way: the transfer is abandoned

Ids are the ones of the request a transfer answers, so both sides tell a
transfer they still want from one they gave up on.

Pure Python, no game imports.
"""

import struct

from tsmp import frames

PART_BYTES = 128 * 1024
# Bytes sent and not yet confirmed. Enough to fill a link of a few MB/s
# with a round trip of a few hundred milliseconds.
WINDOW = 2 * 1024 * 1024
# The receiver confirms whenever it has this much more.
ACK_BYTES = 256 * 1024
# Nothing a session sends comes near this.
MAX_SIZE = 1024 * 1024 * 1024

_OFFSET = struct.Struct("<I")


class Outgoing:
    def __init__(self, ident, what, payload, now):
        self.ident = ident
        self.what = what
        self.payload = payload
        self.sent = 0
        self.acked = 0
        # When the receiver last said it has more.
        self.progressed = now

    @property
    def size(self):
        return len(self.payload)

    @property
    def done(self):
        return self.acked >= self.size

    def parts(self):
        """The parts that fit in the window now."""
        while self.sent < self.size and self.sent - self.acked < WINDOW:
            end = min(self.sent + PART_BYTES, self.size)
            yield frames.pack(frames.PART, self.ident, _OFFSET.pack(self.sent) + self.payload[self.sent:end])
            self.sent = end

    def ack(self, have, now):
        have = min(have, self.sent)
        if have > self.acked:
            self.acked = have
            self.progressed = now


class Incoming:
    def __init__(self, ident, what, size, fields, now):
        self.ident = ident
        self.what = what
        self.size = size
        # What the offer said besides: whatever the receiver needs to know.
        self.fields = fields
        self._data = bytearray()
        self._acked = 0
        self.progressed = now

    @property
    def have(self):
        return len(self._data)

    @property
    def complete(self):
        return self.have >= self.size

    @property
    def payload(self):
        return bytes(self._data)

    def add(self, part, now):
        """Take one PART payload. Returns True when it is time to confirm.
        Raises ValueError for a part out of order or past the end."""
        if len(part) < _OFFSET.size:
            raise ValueError("part is cut off")
        (offset,) = _OFFSET.unpack_from(part)
        data = part[_OFFSET.size:]
        if offset != self.have or self.have + len(data) > self.size:
            raise ValueError("part at {} does not follow {} of {}".format(offset, self.have, self.size))
        self._data += data
        self.progressed = now
        if self.complete or self.have - self._acked >= ACK_BYTES:
            self._acked = self.have
            return True
        return False


class Transfers:
    """Every transfer of one session, both ways. `send(frame)` queues a
    frame for the other game, `clock()` tells the time in seconds."""

    def __init__(self, send, clock):
        self._send = send
        self._clock = clock
        self._out = {}
        self._in = {}

    def send(self, what, ident, payload, **fields):
        """Start sending `payload`; the parts follow as the window allows."""
        self.drop(ident)
        fields.update(id=ident, what=what, size=len(payload))
        self._send(frames.control("offer", **fields))
        self._out[ident] = Outgoing(ident, what, payload, self._clock())
        self.pump()

    def pump(self):
        """Send what the window allows of every transfer. Once per tick."""
        for outgoing in list(self._out.values()):
            for part in outgoing.parts():
                self._send(part)

    def outgoing(self, ident):
        """The transfer being sent under `ident`, until it is confirmed whole
        or dropped."""
        return self._out.get(ident)

    def incoming(self, ident):
        return self._in.get(ident)

    def receive(self, message):
        """Take an offer, a `got` or a `drop`. Returns the new Incoming for
        an offer, which the caller keeps with `accept` or refuses with
        `refuse`; None for the rest."""
        ident = frames.number(message.get("id"))
        kind = message.get("type")
        if kind == "got":
            outgoing = self._out.get(ident)
            if outgoing is not None:
                outgoing.ack(frames.number(message.get("have")), self._clock())
                if outgoing.done:
                    del self._out[ident]
                else:
                    self.pump()
        elif kind == "drop":
            self._out.pop(ident, None)
            self._in.pop(ident, None)
        elif kind == "offer":
            size = frames.number(message.get("size"))
            fields = dict((key, value) for key, value in message.items() if key not in ("type", "id", "what", "size"))
            return Incoming(ident, message.get("what"), size, fields, self._clock())
        return None

    def accept(self, incoming):
        if not 0 <= incoming.size <= MAX_SIZE:
            self.refuse(incoming.ident)
            return None
        self._in[incoming.ident] = incoming
        if incoming.complete:
            return self._finish(incoming)
        return None

    def refuse(self, ident):
        self._in.pop(ident, None)
        self._send(frames.control("drop", id=ident))

    def part(self, ident, payload):
        """Take a PART frame. Returns the Incoming once it is whole."""
        incoming = self._in.get(ident)
        if incoming is None:
            return None
        try:
            confirm = incoming.add(payload, self._clock())
        except ValueError:
            self.refuse(ident)
            return None
        if confirm:
            self._send(frames.control("got", id=ident, have=incoming.have))
        if incoming.complete:
            return self._finish(incoming)
        return None

    def _finish(self, incoming):
        del self._in[incoming.ident]
        if incoming.size == 0:
            self._send(frames.control("got", id=incoming.ident, have=0))
        return incoming

    def drop(self, ident):
        """Stop sending or receiving `ident`, and tell the other side."""
        if self._out.pop(ident, None) is not None or self._in.pop(ident, None) is not None:
            self._send(frames.control("drop", id=ident))

    def expire(self, seconds):
        """Drop, quietly, every transfer that has not moved for `seconds`:
        the other side gave it up or never knew of it."""
        before = self._clock() - seconds
        for transfers in (self._out, self._in):
            for ident in [ident for ident, moving in transfers.items() if moving.progressed < before]:
                del transfers[ident]

    def forget(self):
        """The other game is gone: whatever was under way is lost."""
        self._out.clear()
        self._in.clear()
