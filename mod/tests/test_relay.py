"""The mod's bridge and session against the launcher's real relay.

Needs the `live_pair` example:  cargo build -p tsmp-net --example live_pair
Skipped when it has not been built.
"""

import os
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from tsmp import bridge, frames, session  # noqa: E402

from test_session import FakeGame, SET_SIM_ACTIVE, VIEW_UPDATE, PIE_MENU, INTERACTABLE, view_update  # noqa: E402

EXAMPLE = os.path.join(HERE, "..", "..", "target", "debug", "examples", "live_pair")
if os.name == "nt":
    EXAMPLE += ".exe"


class Player:
    """What live.py assembles inside the game: a bridge feeding a session."""

    def __init__(self, port, role):
        self.game = FakeGame()
        self.bridge = bridge.Bridge(lambda: (port, role))
        consts = session.Consts(VIEW_UPDATE, PIE_MENU, INTERACTABLE, SET_SIM_ACTIVE)
        self.session = session.Session(self.game, consts, self.bridge.send)

    def tick(self):
        for event in self.bridge.pump():
            if event[0] == bridge.UP:
                self.session.bridge_up(event[1])
            elif event[0] == bridge.DOWN:
                self.session.bridge_down(event[1])
            else:
                self.session.frame(*event[1:])
        self.session.tick()


@unittest.skipUnless(os.path.exists(EXAMPLE), "live_pair example is not built")
class RelayTest(unittest.TestCase):
    def setUp(self):
        self.relay = subprocess.Popen([EXAMPLE], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
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
        self.until("the session", lambda: host.session.live and guest.session.live)
        self.until("the guest's sim", lambda: host.session.remote_sim_id == 222)

        # Host simulation -> both clients, minus the host's sim selection.
        update = view_update(5, SET_SIM_ACTIVE, 7)
        self.assertEqual(host.session.outbound(VIEW_UPDATE, update), update)
        big = bytes(range(256)) * 4096
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

        # The host leaving its zone ends the session for both.
        host.game.zone = False
        self.until("the end", lambda: not guest.session.live)
        self.assertEqual(guest.session.outbound(40, b"local"), b"local")

    def test_a_game_that_restarts_rejoins(self):
        host, guest = self.host, self.guest
        host.game.zone = guest.game.zone = True
        self.until("the session", lambda: host.session.live and guest.session.live)

        guest.bridge.close()
        self.until("the host to notice", lambda: not host.session.live)

        self.guest = guest = Player(guest.bridge._endpoint()[0], session.GUEST)
        self.addCleanup(guest.bridge.close)
        guest.game.zone = True
        self.until("the session again", lambda: host.session.live and guest.session.live)


if __name__ == "__main__":
    unittest.main()
