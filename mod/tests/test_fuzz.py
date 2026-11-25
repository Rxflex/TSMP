"""Two sessions driven through many random runs of what can happen to them:
reloads, travels, a link that drops, saves that fail, players pressing play,
time passing. Whatever happens:

- the two always fall quiet, with no messages sent back and forth for ever;
- when they are joined, the guest's game client was built from a snapshot of
  the host's current world, taken when the host's game was where it still is;
- once things calm down (the link is up, both are on one lot, saves work),
  they are joined before long.

Each run is seeded, so a failure names a run that can be replayed.
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import session, wire  # noqa: E402

from test_session import TRAVEL, Player, settle, travel_to  # noqa: E402

RUNS = int(os.environ.get("TSMP_FUZZ_RUNS", "300"))
STEPS = int(os.environ.get("TSMP_FUZZ_STEPS", "80"))


class Pair:
    def __init__(self, rng, own_families):
        self.rng = rng
        self.guest = Player(session.GUEST)
        guest = self.guest.game
        guest.zone = True
        guest.sim_id = 222
        if own_families:
            guest.save = 2
        # What the guest's client was built from.
        guest.shows = None
        self.linked = True
        self.was_joined = False
        # Where a travel the guest's game ordered and the session held goes.
        self.travelling_to = None
        self.host = self.new_host()
        self.guest.session.bridge_up(session.GUEST)
        self.host.link(1)
        self.guest.link(1)
        self.settle()

    def new_host(self, clock=None):
        """The host's game; one started again comes back at the lot of its
        save, with its clock where the save left it."""
        host = Player(session.HOST)
        if clock is not None:
            host.now = clock
        game = host.game
        game.zone = True
        game.paused = False
        game.pause = lambda: setattr(game, "paused", True)
        host.session.bridge_up(session.HOST)
        return host

    def settle(self):
        settle(self.host, self.guest)

    def state_of_host(self):
        return "{}@{}".format(self.host.session._world, self.host.game.ticks).encode()

    # -- what can happen ----------------------------------------------------

    def tick(self, seconds):
        for player in (self.host, self.guest):
            player.tick(seconds)
        self.settle()

    def host_plays(self):
        if not self.host.game.paused:
            self.host.game.ticks += 1

    def host_presses_play(self):
        if self.host.session.command("clock.setspeed", ["1"]):
            self.host.game.paused = False

    def answer_snapshot(self, works=True):
        snapshots = self.host.game.snapshots
        if snapshots:
            done = snapshots.pop(0)
            done(self.state_of_host() if works else None)
            self.settle()

    def answer_contribution(self, works=True):
        contributions = self.guest.game.contributions
        if contributions:
            done = contributions.pop(0)
            done(b"household" if works else None)
            self.settle()

    def guest_loads(self, lot=None):
        """The guest's game loads the zone again: the host's snapshot when it
        was asked to and puts it in place, else its own simulation."""
        guest = self.guest
        loading = guest.session._phase == session.LOADING and guest.game.adopted
        if loading and guest.game.travellers[-1] and self.travelling_to is not None:
            # The held travel, into the host's state.
            lot, self.travelling_to = self.travelling_to, None
        guest.leave()
        self.settle()
        if loading and not guest.game.loads_its_own:
            guest.game.save = self.host.game.save
            guest.game.shows = guest.game.adopted[-1]
        else:
            if loading and guest.game.put:
                # The snapshot was dropped before the load: not put in place.
                guest.game.put -= 1
            guest.game.shows = None
        guest.arrive(lot)
        self.settle()

    def reload(self, player, lot=None):
        if player is self.guest:
            self.guest_loads(lot)
            return
        player.leave()
        self.settle()
        player.arrive(lot)
        self.settle()

    def drop_link(self):
        if self.linked:
            self.linked = False
            self.host.link(0)
            self.guest.link(0)
            self.host.take()
            self.guest.take()

    def restore_link(self):
        if not self.linked:
            self.linked = True
            self.host.link(1)
            self.guest.link(1)
            self.settle()

    def travels_let_go(self):
        """A travel the session held and then let go: the client travels."""
        delivered = self.guest.game.delivered
        orders = [data for msg_id, data in delivered if msg_id == TRAVEL]
        if orders:
            delivered[:] = [item for item in delivered if item[0] != TRAVEL]
            self.travelling_to = None
            self.reload(self.guest, wire.travel(orders[-1])[0])

    def guest_orders_travel(self):
        """The guest's game orders its client to travel to the host's lot."""
        lot = self.host.game.zone_number
        order = travel_to(lot, 222)
        if self.guest.session.outbound(TRAVEL, order) is None:
            self.travelling_to = lot
            self.settle()
        else:
            self.reload(self.guest, lot)

    def guest_opens_cas(self):
        """The guest's player opens Create-a-Sim for its sim; most of the
        time it opens and the zone loads again when it closes."""
        guest = self.guest
        if guest.session.client_cheat("sims.exit2cas 222 1 222") and self.rng.random() < 0.7:
            guest.game.looks[222] = bytes([self.rng.randrange(256)])
            self.reload(guest)

    def restart_host(self):
        self.host = self.new_host(self.host.now)
        if self.linked:
            self.guest.control("gone")
            self.host.link(1)
            self.guest.link(1)
            self.settle()

    def guest_launcher_restarts(self):
        self.guest.session.bridge_down("restarted")
        self.guest.take()
        self.guest.session.bridge_up(session.GUEST)
        if self.linked:
            self.guest.link(1)
            self.host.link(1)
        self.settle()

    def host_refuses(self):
        rng = self.rng
        kind = rng.randrange(3)
        if kind == 0:
            self.host.game.merge_error = None
        else:
            error = ValueError("busy")
            error.final = False
            self.host.game.merge_error = error
            self.host.game.merge_error_stays = False

    def step(self):
        rng = self.rng
        choice = rng.randrange(19)
        if choice == 0:
            self.tick(rng.choice([0.5, 5, 20, 60, 200]))
        elif choice == 1:
            self.host_plays()
        elif choice == 2:
            self.host_presses_play()
        elif choice in (3, 4):
            self.answer_snapshot(works=rng.random() < 0.8)
        elif choice == 5:
            self.answer_contribution(works=rng.random() < 0.8)
        elif choice in (6, 7):
            if self.guest.session._phase == session.LOADING:
                self.guest_loads()
        elif choice == 8:
            self.reload(self.host, rng.choice([None, None, 8]))
        elif choice == 9:
            self.reload(self.guest, rng.choice([None, 7, 8]))
        elif choice == 10:
            self.drop_link()
        elif choice == 11:
            self.restore_link()
        elif choice == 12:
            self.guest.game.loads_its_own = rng.random() < 0.2
        elif choice == 13 and rng.random() < 0.3:
            self.restart_host()
        elif choice == 14 and rng.random() < 0.3:
            self.guest_launcher_restarts()
        elif choice == 15:
            self.host_refuses()
        elif choice == 16:
            self.guest_orders_travel()
        elif choice == 17:
            self.guest_opens_cas()
        else:
            self.tick(1)

    def calm(self):
        """The link is up, both on lot 7, saves and loads work."""
        self.restore_link()
        self.host.game.loads_its_own = self.guest.game.loads_its_own = False
        self.host.game.merge_error = None
        for player in (self.host, self.guest):
            if player.game.zone_number != 7:
                self.reload(player, 7)

    def calm_step(self):
        self.answer_snapshot()
        self.answer_contribution()
        if self.guest.session._phase == session.LOADING:
            self.guest_loads()
        elif not self.guest.session.stale and self.guest.game.zone_number != self.host.game.zone_number:
            # The guest goes where the host is.
            self.guest_orders_travel()
        self.tick(5)

    # -- what must hold -----------------------------------------------------

    def joined(self):
        return self.host.session.live and self.guest.session.live

    def check(self, test, where):
        joined = self.joined()
        if joined and not self.was_joined:
            test.assertEqual(self.guest.game.shows, self.state_of_host(),
                             "{}: joined on a client built from another state of the host".format(where))
        if joined:
            # Joined, the host's messages keep the guest's client in step.
            self.guest.game.shows = self.state_of_host()
        self.was_joined = joined


class FuzzTest(unittest.TestCase):
    def run_pair(self, seed, own_families):
        rng = random.Random(seed)
        pair = Pair(rng, own_families)
        where = "seed {} own families {}".format(seed, own_families)
        for step in range(STEPS):
            try:
                pair.step()
                pair.travels_let_go()
            except AssertionError as error:
                self.fail("{} step {}: {}".format(where, step, error))
            pair.check(self, "{} step {}".format(where, step))
        pair.calm()
        for _ in range(80):
            pair.calm_step()
            pair.travels_let_go()
            pair.check(self, where + " calm")
            if pair.joined():
                return
        self.fail("{}: not joined once things calmed down (guest {}, host holding {})".format(
            where, pair.guest.session._phase, pair.host.session.holding))

    def test_one_family(self):
        for seed in range(RUNS):
            self.run_pair(seed, own_families=False)

    def test_families_of_their_own(self):
        for seed in range(RUNS):
            self.run_pair(seed, own_families=True)


if __name__ == "__main__":
    unittest.main()
