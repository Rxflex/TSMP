"""The mod's bridge and session against the launcher's real relay.

Needs the `live_pair` example:  cargo build -p tsmp-server --example live_pair
Skipped when it has not been built. Every test runs twice: with the guest's
launcher dialling the host's, and with the two meeting through a session
server. A group of a host and two guests runs through the same relay, set
to take three players as only the example can.
"""

import os
import struct
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from tsmp import bridge, frames, session  # noqa: E402
from tsmp import peer as peer_module  # noqa: E402

from test_session import FakeGame, SET_SIM_ACTIVE, VIEW_UPDATE, PIE_MENU, INTERACTABLE, view_update  # noqa: E402

EXAMPLE = os.path.join(HERE, "..", "..", "target", "debug", "examples", "live_pair")
if os.name == "nt":
    EXAMPLE += ".exe"


class Player:
    """What live.py assembles inside the game: a bridge feeding a session.

    Keeps what crossed the bridge, as (kind, peer, inner kind) with None for
    a frame that was not wrapped, to show what the launcher wrapped.
    `ignore` are control types the mod leaves aside, as one of an older
    version does with those it does not know."""

    def __init__(self, port, role, ignore=()):
        self.game = FakeGame()
        self.bridge = bridge.Bridge(lambda: (port, role))
        self.ignore = frozenset(ignore)
        self.arrived = []
        self.sent = []
        consts = session.Consts(VIEW_UPDATE, PIE_MENU, INTERACTABLE, SET_SIM_ACTIVE)
        self.session = session.Session(self.game, consts, self.send)

    def send(self, frame):
        kind, ident, _ = struct.unpack_from("<BII", frame)
        self.sent.append(self.crossed(kind, ident, frame[9:]))
        self.bridge.send(frame)

    @staticmethod
    def crossed(kind, ident, payload):
        if kind != frames.ROUTED:
            return kind, None, None
        return kind, ident, frames.unroute(kind, ident, payload)[0]

    def tick(self):
        for event in self.bridge.pump():
            if event[0] == bridge.UP:
                self.session.bridge_up(event[1])
            elif event[0] == bridge.DOWN:
                self.session.bridge_down(event[1])
            else:
                self.arrived.append(self.crossed(*event[1:]))
                # As live.py does: a host's launcher may wrap what a guest sent.
                kind, ident, payload, peer = frames.unroute(*event[1:])
                if kind == frames.CONTROL and (frames.parse_json(payload) or {}).get("type") in self.ignore:
                    continue
                self.session.frame(kind, ident, payload, peer)
        self.session.tick()


@unittest.skipUnless(os.path.exists(EXAMPLE), "live_pair example is not built")
class RelayTest(unittest.TestCase):
    ARGS = []

    def setUp(self):
        self.relay = subprocess.Popen([EXAMPLE] + self.ARGS, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.addCleanup(self.stop)
        host_port, guest_port = map(int, self.relay.stdout.readline().split())
        self.host = Player(host_port, session.HOST)
        self.guest = Player(guest_port, session.GUEST)
        self.addCleanup(self.host.bridge.close)
        self.addCleanup(self.guest.bridge.close)

    def stop(self):
        self.relay.stdin.close()
        self.relay.wait(10)
        self.relay.stdout.close()

    def until(self, what, condition, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not condition():
            self.assertLess(time.monotonic(), deadline, "timed out waiting for " + what)
            self.host.tick()
            self.guest.tick()
            time.sleep(0.005)

    def test_two_games_play_through_two_launchers(self):
        host, guest = self.host, self.guest
        self.until("the bridges", lambda: host.session.remotes and guest.session.remotes)
        self.assertFalse(host.session.live or guest.session.live)

        # The guest reaches the zone first, the host afterwards.
        guest.game.sim_id = 222
        guest.game.zone = True
        self.until("the host to hear of the guest", lambda: host.session.remote_in_zone)
        self.assertFalse(guest.session.live)
        host.game.zone = True
        # On arrival the guest loads the host's state, which crosses in
        # parts, large as it is.
        big = bytes(range(256)) * 4096
        self.loads(big)
        self.until("the guest's sim", lambda: host.session.remote_sim_id == 222)

        # Host simulation -> both clients, minus the host's sim selection.
        update = view_update(5, SET_SIM_ACTIVE, 7)
        self.assertEqual(host.session.outbound(VIEW_UPDATE, update), update)
        self.assertEqual(host.session.outbound(40, big), big)
        self.until("the stream", lambda: len(guest.game.delivered) == 2)
        self.assertEqual(guest.game.delivered, [(VIEW_UPDATE, view_update(5, 7)), (40, big)])

        # Guest command -> host, whose reply goes back to the guest only.
        host.game.during_execute = lambda: self.assertIsNone(host.session.outbound(PIE_MENU, b"menu"))
        self.assertFalse(guest.session.command("interactions.choices", ["5487", "0"]))
        self.until("the command", lambda: host.game.executed)
        self.assertEqual(host.game.executed, [("interactions.choices", ["5487", "0"], 222)])
        self.until("the reply", lambda: len(guest.game.delivered) == 3)
        self.assertEqual(guest.game.delivered[-1], (PIE_MENU, b"menu"))

        # The host leaving its zone ends the session for both. Once it is
        # back the guest loads what the host saves and joins again.
        host.game.zone = False
        self.until("the end", lambda: not guest.session.live and guest.session.stale)
        self.assertIsNone(guest.session.outbound(40, b"local"))
        host.game.zone = True
        self.loads(b"another save")

    def test_a_game_that_restarts_rejoins(self):
        host, guest = self.host, self.guest
        host.game.zone = guest.game.zone = True
        self.loads(b"save")

        guest.bridge.close()
        self.until("the host to notice", lambda: not host.session.live)

        self.guest = guest = Player(guest.bridge._endpoint()[0], session.GUEST)
        self.addCleanup(guest.bridge.close)
        guest.game.zone = True
        # Its game client shows nothing of the host's any more.
        self.loads(b"save")

    def test_a_host_game_of_version_3_hears_its_guest_wrapped(self):
        """The host's mod speaks version 3: its launcher numbers the guest and
        wraps what it sends, and the host's mod wraps what is for that guest
        alone. The guest's game sees none of it."""
        host, guest = self.host, self.guest
        guest.game.sim_id = 222
        host.game.zone = guest.game.zone = True
        self.loads(b"save")
        self.until("the guest's sim", lambda: host.session.remote_sim_id == 222)
        self.assertTrue(host.session._routed)
        self.assertEqual(sorted(host.session.peers), [1])
        peer = host.session.peers[1]
        self.assertEqual((peer.name, peer.player, peer.up), ("guest", "guest-id", True))

        update = view_update(5, 7)
        self.assertEqual(host.session.outbound(VIEW_UPDATE, update), update)
        host.game.during_execute = lambda: self.assertIsNone(host.session.outbound(PIE_MENU, b"menu"))
        self.assertFalse(guest.session.command("interactions.choices", ["5487", "0"]))
        self.until("the reply", lambda: (PIE_MENU, b"menu") in guest.game.delivered)
        self.assertEqual(guest.game.delivered[-2:], [(VIEW_UPDATE, update), (PIE_MENU, b"menu")])

        # The command came wrapped with the guest's peer, never plain.
        self.assertIn((frames.ROUTED, 1, frames.COMMAND), host.arrived)
        self.assertNotIn((frames.COMMAND, None, None), host.arrived)
        # The snapshot and the reply were for the guest alone; the stream and
        # the host's state for every guest.
        self.assertIn((frames.ROUTED, 1, frames.PART), host.sent)
        self.assertIn((frames.ROUTED, 1, frames.MSG), host.sent)
        self.assertIn((frames.MSG, None, None), host.sent)
        self.assertNotIn(frames.ROUTED, [kind for kind, _, _ in guest.arrived + guest.sent])

    def test_a_host_game_of_version_2_gets_nothing_wrapped(self):
        """Both mods of the version before several guests: the host's
        launcher takes its one guest as it did then and wraps nothing, and the
        host's mod hears no `peer_up` or `peer_gone`, as one of that version
        would not."""
        protocol = session.PROTOCOL
        session.PROTOCOL = 2
        self.addCleanup(setattr, session, "PROTOCOL", protocol)
        self.host.ignore = frozenset(["peer_up", "peer_gone"])
        host, guest = self.host, self.guest
        guest.game.sim_id = 222
        host.game.zone = guest.game.zone = True
        self.loads(b"save")
        self.until("the guest's sim", lambda: host.session.remote_sim_id == 222)

        host.game.during_execute = lambda: self.assertIsNone(host.session.outbound(PIE_MENU, b"menu"))
        self.assertFalse(guest.session.command("interactions.choices", ["5487", "0"]))
        self.until("the reply", lambda: (PIE_MENU, b"menu") in guest.game.delivered)
        self.assertEqual(host.game.executed, [("interactions.choices", ["5487", "0"], 222)])

        self.assertFalse(host.session._routed)
        self.assertEqual(sorted(host.session.peers), [peer_module.LEGACY])
        self.assertIn((frames.COMMAND, None, None), host.arrived)
        for player in (host, guest):
            self.assertNotIn(frames.ROUTED, [kind for kind, _, _ in player.arrived + player.sent])

    def loads(self, payload):
        """The guest asks for the host's state, gets it, loads it and joins."""
        host, guest = self.host, self.guest
        asked = len(host.game.snapshots)
        adopted = len(guest.game.adopted)
        self.until("the request", lambda: len(host.game.snapshots) > asked)
        host.game.snapshots[-1](payload)
        self.until("the snapshot", lambda: len(guest.game.adopted) > adopted)
        self.assertEqual(guest.game.adopted[-1], payload)
        guest.game.zone = False
        self.until("the guest to leave", lambda: not guest.session._in_zone)
        guest.game.zone = True
        self.until("the session", lambda: host.session.live and guest.session.live)


class OnlineRelayTest(RelayTest):
    ARGS = ["online"]


@unittest.skipUnless(os.path.exists(EXAMPLE), "live_pair example is not built")
class GroupRelayTest(unittest.TestCase):
    """A host and two guests, which the relay numbers 1 and 2."""

    ARGS = ["players=3"]

    def setUp(self):
        self.relay = subprocess.Popen([EXAMPLE] + self.ARGS, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.addCleanup(self.stop)
        host_port, *guest_ports = map(int, self.relay.stdout.readline().split())
        self.host = Player(host_port, session.HOST)
        self.guests = [Player(port, session.GUEST) for port in guest_ports]
        for player in [self.host] + self.guests:
            self.addCleanup(player.bridge.close)

    def stop(self):
        self.relay.stdin.close()
        self.relay.wait(10)
        self.relay.stdout.close()

    def until(self, what, condition, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not condition():
            self.assertLess(time.monotonic(), deadline, "timed out waiting for " + what)
            for player in [self.host] + self.guests:
                player.tick()
            time.sleep(0.005)

    def test_each_guest_joins_and_hears_what_is_for_it(self):
        host, guests = self.host, self.guests
        self.assertEqual(len(guests), 2)
        first, second = guests
        first.game.sim_id, second.game.sim_id = 222, 333
        for player in [host] + guests:
            player.game.zone = True
        peers = host.session.peers
        self.until("both guests to ask", lambda: all(guest.session._phase == session.ASKING for guest in guests)
                   and sorted(peers) == [1, 2] and all(peers[n].saving or peers[n].asked for n in (1, 2)))
        # Each numbers its own requests: both asked with id 1.
        self.assertEqual([guest.session._request for guest in guests], [1, 1])
        self.assertEqual([(peers[n].name, peers[n].player) for n in (1, 2)],
                         [("guest", "guest-id"), ("guest2", "guest2-id")])
        self.until("the save", lambda: host.game.snapshots and all(peers[n].saving for n in (1, 2)))
        host.game.snapshots[-1](b"save")
        self.until("both snapshots", lambda: all(guest.game.adopted for guest in guests))
        for guest in guests:
            guest.game.zone = False
        self.until("the guests to leave", lambda: not any(guest.session._in_zone for guest in guests))
        for guest in guests:
            guest.game.zone = True
        self.until("both in", lambda: all(guest.session.live for guest in guests)
                   and all(host.session._live_with(peers[n]) for n in (1, 2)))
        self.until("their sims", lambda: (peers[1].sim_id, peers[2].sim_id) == (222, 333))

        # Wrapped for the second, then for all: the first gets only the
        # latter, which comes after on its link.
        host.session._relay(40, b"for the second", to=peers[2])
        self.assertEqual(host.session.outbound(41, b"for all"), b"for all")
        self.until("the stream", lambda: all((41, b"for all") in guest.game.delivered for guest in guests))
        self.assertIn((40, b"for the second"), second.game.delivered)
        self.assertNotIn((40, b"for the second"), first.game.delivered)
        self.assertIn((frames.ROUTED, 2, frames.MSG), host.sent)

        # The first's launcher goes: the second plays on.
        self.relay.stdin.write(b"drop 1\n")
        self.relay.stdin.flush()
        self.until("the first to go", lambda: not peers[1].up)
        self.assertTrue(second.session.live)
        self.assertTrue(host.session._live_with(peers[2]))
        self.assertEqual(host.game.forgotten, [1])
        self.assertEqual(host.session.outbound(41, b"after"), b"after")
        self.until("the stream after", lambda: (41, b"after") in second.game.delivered)


class OnlineGroupRelayTest(GroupRelayTest):
    ARGS = ["online", "players=3"]


if __name__ == "__main__":
    unittest.main()
