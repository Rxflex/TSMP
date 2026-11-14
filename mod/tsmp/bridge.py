"""Connection from the mod to the launcher on this machine.

The game's Python runs on the simulation thread and must never block, and a
background thread would compete with the simulation for the GIL. So this is a
non-blocking socket driven by `pump()`, which the mod calls once per tick.

Pure Python, no game imports.
"""

import errno
import select
import socket
import time

from tsmp import frames

RETRY_SECONDS = 2.0
CONNECT_TIMEOUT = 5.0
# The launcher reads as fast as the network allows. If this much is waiting,
# the other side is gone or hopelessly behind; reconnecting is the only cure.
MAX_PENDING = 64 * 1024 * 1024
# Upper bound on what one tick may read, to keep ticks short.
MAX_READ_PER_PUMP = 8 * 1024 * 1024

_IN_PROGRESS = (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, getattr(errno, "WSAEWOULDBLOCK", 10035))

UP = "up"
DOWN = "down"
FRAME = "frame"


class Bridge:
    """`endpoint()` returns (port, role) for the launcher to connect to, or
    None while there is none; it is asked again before every attempt."""

    def __init__(self, endpoint, clock=time.monotonic):
        self._endpoint = endpoint
        self._clock = clock
        self._socket = None
        self._connected = False
        self._role = None
        self._deadline = 0.0
        self._next_attempt = 0.0
        self._out = bytearray()
        self._decoder = None

    @property
    def connected(self):
        return self._connected

    def send(self, frame):
        """Send a packed frame, or as much of it as the socket takes right
        now; the rest goes out on later pumps. Dropped while there is no
        connection."""
        if not self._connected:
            return
        self._out += frame
        try:
            self._write()
        except OSError:
            # The next pump notices the dead connection and reports it.
            pass

    def pump(self):
        """Advance the connection. Returns a list of events:
        (UP, role), (DOWN, reason) and (FRAME, kind, id, payload)."""
        events = []
        if self._socket is None:
            self._try_connect()
        elif not self._connected:
            self._finish_connect(events)
        if self._connected:
            try:
                self._read(events)
                self._write()
            except (OSError, ValueError) as exc:
                self._drop(events, str(exc))
        return events

    def close(self):
        self._drop([], "closed")

    def _try_connect(self):
        now = self._clock()
        if now < self._next_attempt:
            return
        self._next_attempt = now + RETRY_SECONDS
        endpoint = self._endpoint()
        if endpoint is None:
            return
        port, self._role = endpoint
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setblocking(False)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            code = sock.connect_ex(("127.0.0.1", port))
        except OSError:
            sock.close()
            return
        if code not in _IN_PROGRESS:
            sock.close()
            return
        self._socket = sock
        self._deadline = now + CONNECT_TIMEOUT

    def _finish_connect(self, events):
        sock = self._socket
        try:
            _, writable, failed = select.select([], [sock], [sock], 0)
            code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        except (OSError, ValueError):
            failed, writable, code = [sock], [], 1
        if failed or code:
            self._abandon()
        elif writable:
            self._connected = True
            self._decoder = frames.Decoder()
            del self._out[:]
            events.append((UP, self._role))
        elif self._clock() > self._deadline:
            self._abandon()

    def _abandon(self):
        try:
            self._socket.close()
        except OSError:
            pass
        self._socket = None
        self._connected = False

    def _drop(self, events, reason):
        was_connected = self._connected
        if self._socket is not None:
            self._abandon()
        del self._out[:]
        if was_connected:
            events.append((DOWN, reason))

    def _read(self, events):
        total = 0
        while total < MAX_READ_PER_PUMP:
            try:
                data = self._socket.recv(65536)
            except (BlockingIOError, InterruptedError):
                return
            if not data:
                raise OSError("launcher closed the connection")
            total += len(data)
            for kind, ident, payload in self._decoder.feed(data):
                events.append((FRAME, kind, ident, payload))

    def _write(self):
        if len(self._out) > MAX_PENDING:
            raise OSError("launcher is not reading")
        while self._out:
            try:
                sent = self._socket.send(self._out[:262144])
            except (BlockingIOError, InterruptedError):
                return
            if sent <= 0:
                return
            del self._out[:sent]
