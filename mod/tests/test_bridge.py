import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import bridge, frames  # noqa: E402


def pump_until(link, predicate, timeout=5.0):
    """Pump like the game would, collecting events until `predicate(events)`."""
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events += link.pump()
        if predicate(events):
            return events
        time.sleep(0.005)
    raise AssertionError("timed out; events so far: {!r}".format(events))


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.server.settimeout(5)
        self.port = self.server.getsockname()[1]
        self.addCleanup(self.server.close)

    def connect(self, role="host"):
        link = bridge.Bridge(lambda: (self.port, role))
        self.addCleanup(link.close)
        events = pump_until(link, lambda events: events)
        self.assertEqual(events, [(bridge.UP, role)])
        peer, _ = self.server.accept()
        peer.settimeout(5)
        self.addCleanup(peer.close)
        return link, peer

    def test_connects_and_exchanges_frames(self):
        link, peer = self.connect("guest")

        link.send(frames.pack(frames.MSG, 7, b"x" * 100000))
        decoder = frames.Decoder()
        got = []
        while not got:
            link.pump()
            got += decoder.feed(peer.recv(65536))
        self.assertEqual(got, [(frames.MSG, 7, b"x" * 100000)])

        peer.sendall(frames.control("link", remotes=1) + frames.pack(frames.MSG, 9, b"abc"))
        events = pump_until(link, lambda events: len(events) == 2)
        self.assertEqual(
            events,
            [(bridge.FRAME, frames.CONTROL, 0, b'{"remotes":1,"type":"link"}'), (bridge.FRAME, frames.MSG, 9, b"abc")],
        )

    def test_reports_a_lost_connection_and_reconnects(self):
        link, peer = self.connect()
        peer.close()
        events = pump_until(link, lambda events: events)
        self.assertEqual(events[0][0], bridge.DOWN)
        self.assertFalse(link.connected)

        link._next_attempt = 0.0
        events = pump_until(link, lambda events: events)
        self.assertEqual(events, [(bridge.UP, "host")])

    def test_waits_while_there_is_no_endpoint(self):
        endpoints = [None]
        link = bridge.Bridge(lambda: endpoints[0])
        self.addCleanup(link.close)
        self.assertEqual(link.pump(), [])
        link.send(b"dropped")
        self.assertFalse(link.connected)

        endpoints[0] = (self.port, "guest")
        link._next_attempt = 0.0
        self.assertEqual(pump_until(link, lambda events: events), [(bridge.UP, "guest")])

    def test_nothing_listening_is_not_an_error(self):
        self.server.close()
        link = bridge.Bridge(lambda: (self.port, "host"))
        self.addCleanup(link.close)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            self.assertEqual(link.pump(), [])
            time.sleep(0.05)

    def test_corrupt_stream_drops_the_connection(self):
        link, peer = self.connect()
        peer.sendall(bytes.fromhex("01" "00000000" "ffffffff"))
        events = pump_until(link, lambda events: events)
        self.assertEqual(events[0][0], bridge.DOWN)


if __name__ == "__main__":
    unittest.main()
