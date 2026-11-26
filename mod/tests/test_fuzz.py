"""A host and its guests driven through many random runs of what can happen
to them: reloads, travels, links that drop, saves that fail, players
pressing play, time passing. Each guest does what it does by itself, as its
player would, and the host by itself. Whatever happens:

- they always fall quiet, with no messages sent back and forth for ever;
- when a guest is joined, its game client was built from a snapshot of the
  host's current world, taken when the host's game was where it still is,
  and it shows the world the host has it show (the host's, or one of its
  own after it fell behind: see Session._world_for); but for a guest with
  a family of its own the host let in a moment behind, after it had sent
  it back to loading once already as its game moved on
  (session.MOVED_ON_RELOADS), which is told;
- a player with a family of its own who leaves through the menu while
  their game loads the host's state, and loads a lot of their own save, is
  not joined on it;
- what one guest does, or the host short of loading its zone again, never
  takes another guest out of the game;
- while the host holds its game for a guest that loads, the game stays
  paused, and only that guest getting in, its link staying down or the hold
  running out ends the hold;
- a guest's command runs only while that guest is in the game, as its sim;
- no sim is played by two guests at once;
- once things calm down (the links are up, all are on one lot, saves work),
  every guest is joined before long, and the host's game is not held
  paused for longer than a hold lasts after the last load; even when the
  host's game moves on while a guest loads, each guest with a family of
  its own loads at most CALM_ADOPTS times for each load of the host's
  zone. One of one family loads again until it is in step, as ever.

One guest stands for the two-player game, three for a group, one of whom
may leave for good before things calm down. The launcher
of a host with several guests numbers them (see tsmp/frames.py, ROUTED);
with one, it does or not as TSMP_RELAY says (see test_session.py).

Each run is seeded, so a failure names a run that can be replayed.
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import session, wire  # noqa: E402

from test_session import TRAVEL, Player, travel_to  # noqa: E402

RUNS = int(os.environ.get("TSMP_FUZZ_RUNS", "300"))
STEPS = int(os.environ.get("TSMP_FUZZ_STEPS", "80"))
# A group takes longer to drive, and each of its guests gets fewer of the
# steps: half the runs, each half as long again.
GROUP_RUNS = max(1, RUNS // 2)
GROUP_STEPS = STEPS * 3 // 2
# Once calm, the loads of one guest for one load of the host's zone: the
# first, and one more for a host that moved on (session.MOVED_ON_RELOADS),
# and one for a hold that ran out meanwhile.
CALM_ADOPTS = 3


class Member:
    """One guest of the group, and what the run knows of it beyond its
    session: whether its launcher is linked to the host's, and what its
    game client shows."""

    def __init__(self, number, own_family):
        self.number = number
        self.own_family = own_family
        # Guest 1 plays sim 222, as the one guest always did; the others
        # sims of their own.
        self.sim = 111 * (number + 1)
        self.player = Player(session.GUEST)
        game = self.player.game
        game.zone = True
        game.sim_id = self.sim
        if own_family:
            game.save = 2
            # As resync tells it from the names of its zones, or from its
            # record of such saves after a load from the menu.
            game.cross = True
        # What the guest's client was built from.
        game.shows = None
        self.linked = True
        self.was_joined = False
        # Its game takes long to load what it was sent, and is in the
        # loading screen: whether it loads the host's state, and the lot.
        self.slow = False
        self.loading = None
        # Until when the host held for it as its load began, and the host
        # and world of that hold: the hold is not to end before, but by
        # the guest getting in.
        self.held_until = (0.0, None, "")
        # Where a travel the guest's game ordered and the session held goes.
        self.travelling_to = None
        # Once calm: the host's world, and how many snapshots the guest had
        # adopted when it began.
        self.calm_from = (None, 0)

    @property
    def game(self):
        return self.player.game

    @property
    def session(self):
        return self.player.session


class Group:
    """A host and its guests, linked through the host's launcher, which
    `settle` stands in for."""

    def __init__(self, rng, families):
        """`families` says, for each guest, whether it plays a family of its
        own, in a save of its own."""
        self.rng = rng
        self.guests = [Member(number, own) for number, own in enumerate(families, 1)]
        # Several guests need a launcher that tells them apart.
        self.routed = True if len(self.guests) > 1 else None
        self.host = self.new_host()
        # When a game last loaded, which a hold for a guest lasts past.
        self.last_load = self.host.now
        # The guests the game was held for at the last look.
        self.held = set()
        # Guests that left for good.
        self.gone = []
        for member in self.guests:
            member.session.bridge_up(session.GUEST)
        for member in self.guests:
            self.relay(member, True)
        for member in self.guests:
            member.player.link(1)
        self.settle()

    def new_host(self, clock=None):
        """The host's game; one started again comes back at the lot of its
        save, with its clock where the save left it."""
        host = Player(session.HOST, routed=self.routed)
        if clock is not None:
            host.now = clock
        game = host.game
        game.zone = True
        game.paused = False
        game.pause = lambda: setattr(game, "paused", True)
        game.execute = self.execute_for(host)
        host.session.bridge_up(session.HOST)
        return host

    @staticmethod
    def execute_for(host):
        """The host's game runs a guest's command, as that guest's sim, and
        only while that guest is in the game; a clock command goes through
        the session again, as the game's own command hook would."""
        game = host.game

        def execute(name, args, sim_id, peer=None):
            sender = host.session.peers.get(peer)
            if sender is None or not host.session._live_with(sender):
                raise AssertionError("{} ran for guest {}, which is not in the game".format(name, peer))
            if sim_id != sender.sim_id:
                raise AssertionError("{} of guest {} ran as sim {}".format(name, peer, sim_id))
            game.executed.append((name, args, sim_id))
            game.executed_for.append(peer)
            if name in session.CLOCK_NAMES and host.session.command(name, args):
                game.paused = False
        return execute

    def relay(self, member, up):
        """The host's launcher hears a guest's launcher come, or come again,
        or go: one that numbers its guests says which."""
        host = self.host
        if host.routed and host.speaks:
            if up:
                host._relay_says("peer_up", peer=member.number, name="guest {}".format(member.number),
                                 player="p{}".format(member.number))
                host.peers_up.add(member.number)
            elif member.number in host.peers_up:
                host.peers_up.discard(member.number)
                host._relay_says("peer_gone", peer=member.number)
            remotes = len(host.peers_up)
        else:
            remotes = sum(1 for other in self.guests if other.linked)
        host._relay_says("link", remotes=remotes)

    def settle(self):
        """Exchange frames until all fall quiet, through the host's launcher:
        what a guest sends reaches the host from its peer, what the host
        sends reaches the guest it is for or, when for none, every guest.
        Nothing crosses a link that is down."""
        host = self.host
        numbers = [member.number for member in self.guests + self.gone]
        for _ in range(100):
            from_host = host.take_routed()
            from_guests = [member.player.take() for member in self.guests]
            if not from_host and not any(from_guests):
                return
            unknown = [to for to, _ in from_host if to not in (None, 0) and to not in numbers]
            if unknown:
                raise AssertionError("the host sent to peers {} it does not have".format(unknown))
            # The guests hear the host first, then the host each guest, as
            # the two always did.
            for member in self.guests:
                if member.linked:
                    member.player.receive([frame for to, frame in from_host if to in (None, 0, member.number)])
            for member, sent in zip(self.guests, from_guests):
                if member.linked:
                    host.receive(sent, peer=member.number)
        raise AssertionError("the sessions never went quiet")

    def state_of_host(self):
        return "{}@{}".format(self.host.session._world, self.host.game.ticks).encode()

    def pick(self, loads=True):
        """The guest that does the next thing; drawn only when there is a
        choice, so that a run with one guest does what it always did. Its
        player sits a loading screen out first, unless what happens to it
        does not wait for that (`loads` False)."""
        if len(self.guests) == 1:
            return self.guests[0]
        member = self.guests[self.rng.randrange(len(self.guests))]
        if loads:
            self.done_loading(member)
        return member

    # -- what can happen ----------------------------------------------------

    def tick(self, seconds):
        for player in [self.host] + [member.player for member in self.guests]:
            player.tick(seconds)
        self.settle()

    def host_plays(self):
        if not self.host.game.paused:
            self.host.game.ticks += 1

    def host_presses_play(self):
        if self.host.session.command("clock.setspeed", ["1"]):
            self.host.game.paused = False
            return True
        return False

    def answer_snapshot(self, works=True):
        snapshots = self.host.game.snapshots
        if snapshots:
            done = snapshots.pop(0)
            done(self.state_of_host() if works else None)
            self.settle()

    def answer_contribution(self, member, works=True):
        contributions = member.game.contributions
        if contributions:
            done = contributions.pop(0)
            done(b"household" if works else None)
            self.settle()

    def guest_loads(self, member, lot=None):
        """The guest's game loads the zone again: the host's snapshot when it
        was asked to and puts it in place, else its own simulation."""
        self.starts_loading(member, lot)
        self.done_loading(member)

    def starts_loading(self, member, lot=None):
        """The guest's game leaves its zone for the loading screen."""
        guest = member.player
        loading = guest.session._phase == session.LOADING and guest.game.adopted
        if loading and guest.game.travellers[-1] and member.travelling_to is not None:
            # The held travel, into the host's state.
            lot, member.travelling_to = member.travelling_to, None
        guest.leave()
        member.loading = (bool(loading), lot)
        peer = self.peer_of(member)
        member.held_until = (peer.hold_until if peer is not None and loading else 0.0, self.host,
                             self.host.session._world)
        self.settle()

    def done_loading(self, member):
        """The guest's game is through its loading screen."""
        if member.loading is None:
            return
        (loading, lot), member.loading = member.loading, None
        guest = member.player
        if loading and not guest.game.loads_its_own:
            guest.game.save = self.host.game.save
            guest.game.shows = guest.game.adopted[-1]
        else:
            if loading and guest.game.put:
                # The snapshot was dropped before the load: not put in place.
                guest.game.put -= 1
            guest.game.shows = None
        guest.arrive(lot)
        self.last_load = self.host.now
        self.settle()

    def leaves_through_menu(self, member, lot=None):
        """The guest's player leaves through the menu (Manage Worlds) while
        its game loads the host's state, and loads a lot of its own save:
        the game saves without travelling, which drops the snapshot that
        waited, and the zone loaded is one from the menu."""
        guest = member.player
        if guest.session._phase != session.LOADING or not member.own_family:
            # With one family any save of the guest's puts the host's in
            # place, as it always did.
            return
        if member.loading is None:
            guest.leave()
        member.loading = None
        member.travelling_to = None
        if guest.game.put and not guest.game.loads_its_own:
            guest.game.put -= 1
        guest.game.by_hand = True
        guest.game.shows = None
        guest.arrive(lot)
        self.settle()

    def host_moves_on(self):
        """The host's game moves on while it is paused, as its player builds:
        what a guest that loads gets is behind it. A guest joined on the
        state as it was is kept in step by the host's messages."""
        before = self.state_of_host()
        self.host.game.ticks += 1
        for member in self.guests:
            if self.joined(member) and member.game.shows == before:
                member.game.shows = self.state_of_host()

    def reload_host(self, lot=None):
        self.host.leave()
        self.settle()
        self.host.arrive(lot)
        self.last_load = self.host.now
        self.settle()

    def drop_link(self, member):
        """The link between the host's launcher and this guest's drops; what
        was under way between them is lost."""
        if member.linked:
            member.linked = False
            # The host may stop holding for it once it waited long enough.
            member.held_until = (0.0, None, "")
            self.relay(member, False)
            member.player.link(0)
            # What the host sends now reaches the others alone.
            member.player.take()

    def restore_link(self, member):
        if not member.linked:
            member.linked = True
            self.relay(member, True)
            member.player.link(1)
            self.settle()

    def travels_let_go(self, member):
        """A travel the session held and then let go: the client travels."""
        if member.loading is not None:
            return
        delivered = member.game.delivered
        orders = [data for msg_id, data in delivered if msg_id == TRAVEL]
        if orders:
            delivered[:] = [item for item in delivered if item[0] != TRAVEL]
            member.travelling_to = None
            self.guest_loads(member, wire.travel(orders[-1])[0])

    def guest_orders_travel(self, member):
        """The guest's game orders its client to travel to the host's lot,
        with its sim; in a group of one family, at times with the sim of
        another player of it too, which is not this guest's to have."""
        lot = self.host.game.zone_number
        sims = [member.sim]
        others = [other for other in self.guests
                  if other is not member and not (other.own_family or member.own_family)]
        if others and self.rng.random() < 0.3:
            sims.append(self.rng.choice(others).sim)
        order = travel_to(lot, *sims)
        if member.session.outbound(TRAVEL, order) is None:
            member.travelling_to = lot
            self.settle()
        else:
            self.guest_loads(member, lot)

    def guest_opens_cas(self, member):
        """The guest's player opens Create-a-Sim for its sim; most of the
        time it opens and the zone loads again when it closes."""
        sim = member.sim
        if member.session.client_cheat("sims.exit2cas {0} 1 {0}".format(sim)) and self.rng.random() < 0.7:
            member.game.looks[sim] = bytes([self.rng.randrange(256)])
            self.guest_loads(member)

    def guest_command(self, member, name):
        """The guest's player does something with its sim, or presses play:
        joined, its game sends it to the host's to run."""
        member.session.command(name, ["1"] if name in session.CLOCK_NAMES else [str(member.sim)])
        self.settle()

    def restart_host(self):
        self.host = self.new_host(self.host.now)
        self.last_load = self.host.now
        linked = [member for member in self.guests if member.linked]
        for member in linked:
            member.player.control("gone")
        for member in linked:
            self.relay(member, True)
        for member in linked:
            member.player.link(1)
        if linked:
            self.settle()

    def guest_launcher_restarts(self, member):
        guest = member.player
        member.held_until = (0.0, None, "")
        guest.session.bridge_down("restarted")
        guest.take()
        guest.session.bridge_up(session.GUEST)
        if member.linked:
            guest.link(1)
            self.relay(member, True)
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

    def step(self, test):
        """One random thing; what a guest does is checked to leave the
        others as they were."""
        rng = self.rng
        if len(self.guests) > 1 and rng.random() < 0.4:
            # In a group, each guest gets in less often between what takes
            # it out again: more of the time goes to what lets them in.
            self.help(test)
            return
        choice = rng.randrange(21)
        if choice == 0:
            self.by(test, None, self.tick, rng.choice([0.5, 5, 20, 60, 200]))
        elif choice == 1:
            self.by(test, None, self.host_plays)
        elif choice == 2:
            self.by(test, None, self.host_presses_play)
        elif choice in (3, 4):
            self.by(test, None, self.answer_snapshot, works=rng.random() < 0.8)
        elif choice == 5:
            member = self.pick()
            self.by(test, member, self.answer_contribution, member, works=rng.random() < 0.8)
        elif choice in (6, 7):
            member = self.pick()
            if member.session._phase == session.LOADING:
                self.by(test, member, self.guest_loads, member)
        elif choice == 8:
            self.reload_host(rng.choice([None, None, 8]))
        elif choice == 9:
            member = self.pick()
            self.by(test, member, self.guest_loads, member, rng.choice([None, 7, 8]))
        elif choice == 10:
            member = self.pick(loads=False)
            self.by(test, member, self.drop_link, member)
        elif choice == 11:
            member = self.pick(loads=False)
            self.by(test, member, self.restore_link, member)
        elif choice == 12:
            member = self.pick()
            member.game.loads_its_own = rng.random() < 0.2
        elif choice == 13 and rng.random() < 0.3:
            self.restart_host()
        elif choice == 14 and rng.random() < 0.3:
            member = self.pick(loads=False)
            self.by(test, member, self.guest_launcher_restarts, member)
        elif choice == 15:
            self.by(test, None, self.host_refuses)
        elif choice == 16:
            member = self.pick()
            self.by(test, member, self.guest_orders_travel, member)
        elif choice == 17:
            member = self.pick()
            self.by(test, member, self.guest_opens_cas, member)
        elif choice == 18:
            member = self.pick(loads=False)
            self.by(test, member, self.leaves_through_menu, member, rng.choice([None, 7, 8]))
        elif choice == 19:
            if any(member.session._phase == session.LOADING for member in self.guests):
                self.by(test, None, self.host_moves_on)
        else:
            self.by(test, None, self.tick, 1)
        for member in self.guests:
            self.by(test, member, self.travels_let_go, member)

    def help(self, test):
        """What lets the guests in: a save that works, the loads it starts,
        the households they give; and the host's player, who wants to play,
        pressing play, which a hold that ran out lets through."""
        if self.host_presses_play():
            self.by(test, None, self.host_plays)
        rng = self.rng
        self.by(test, None, self.answer_snapshot, works=rng.random() < 0.9)
        for member in self.guests:
            # A game that loads slowly keeps the others waiting, and once the
            # game is no longer held for it, they play on without it.
            member.slow = rng.random() < (0.9 if member.slow else 0.1)
            if member.loading is not None and not member.slow:
                self.by(test, member, self.done_loading, member)
            elif member.session._phase == session.LOADING and member.loading is None:
                self.by(test, member, self.starts_loading, member)
                if not member.slow:
                    self.by(test, member, self.done_loading, member)
            self.by(test, member, self.answer_contribution, member)
            if rng.random() < 0.2:
                name = rng.choice(["interactions.select", "clock.setspeed"])
                self.by(test, member, self.guest_command, member, name)
        self.by(test, None, self.tick, rng.choice([1, 1, 1, 30, 100]))

    def quits(self, member):
        """The guest's player leaves the game for good: its launcher's link
        drops, and does not come back."""
        self.drop_link(member)
        member.session.bridge_down("quit")
        member.player.take()
        self.guests.remove(member)
        self.gone.append(member)

    def calm(self):
        """The links are up, all on lot 7, saves and loads work. In a group,
        one guest may have left for good instead: the others play on."""
        for member in self.guests:
            self.done_loading(member)
        if len(self.guests) > 1 and self.rng.random() < 0.3:
            self.quits(self.rng.choice(self.guests))
        for member in self.guests:
            self.restore_link(member)
        self.host.game.loads_its_own = False
        for member in self.guests:
            member.game.loads_its_own = False
        self.host.game.merge_error = None
        if self.host.game.zone_number != 7:
            self.reload_host(7)
        for member in self.guests:
            if member.game.zone_number != 7:
                self.guest_loads(member, 7)
        self.last_load = self.host.now

    def calm_step(self, moves_on=False):
        """`moves_on`: the host's game moves on while each guest loads."""
        self.answer_snapshot()
        for member in self.guests:
            self.answer_contribution(member)
        for member in self.guests:
            if member.session._phase == session.LOADING:
                if moves_on:
                    self.host_moves_on()
                self.guest_loads(member)
            elif not member.session.stale and member.game.zone_number != self.host.game.zone_number:
                # The guest goes where the host is.
                self.guest_orders_travel(member)
        self.tick(5)

    # -- what must hold -----------------------------------------------------

    def peer_of(self, member):
        """What the host keeps of the guest; with one guest, whatever its
        launcher numbers it, the host's one peer."""
        peers = self.host.session.peers
        if self.routed is None:
            return next(iter(peers.values()), None)
        return peers.get(member.number)

    def joined(self, member):
        peer = self.peer_of(member)
        return peer is not None and self.host.session._live_with(peer) and member.session.live

    def all_joined(self):
        return all(self.joined(member) for member in self.guests)

    def by(self, test, member, action, *args, **fields):
        """`action`, done by `member`, or by the host or time passing when
        None: the other guests in the game stay in it, unless the host's
        zone was loaded again meanwhile. Time may end what a guest began
        itself, a Create-a-Sim that did not open."""
        host, world = self.host, self.host.session._world
        joined = [other for other in self.guests if other is not member and self.joined(other)
                  and (member is not None or other.session._cas is None)]
        action(*args, **fields)
        if self.host is not host or self.host.session._world != world:
            return
        for other in joined:
            test.assertTrue(self.joined(other), "{} by {} took guest {} out of the game".format(
                action.__name__, "guest {}".format(member.number) if member else "the host", other.number))

    def check(self, test, where):
        host = self.host.session
        for member in self.guests:
            joined = self.joined(member)
            name = "{}, guest {}".format(where, member.number)
            if joined and not member.was_joined:
                peer = self.peer_of(member)
                if peer.drift:
                    # Let in a moment behind: the host's world, an older
                    # moment of it, and both players were told.
                    test.assertEqual(member.game.shows.split(b"@")[0], self.state_of_host().split(b"@")[0],
                                     "{}: let in behind on another world of the host".format(name))
                    test.assertTrue(any("moment behind" in notice for notice in member.game.notices),
                                    "{}: let in behind without a word".format(name))
                else:
                    test.assertEqual(member.game.shows, self.state_of_host(),
                                     "{}: joined on a client built from another state of the host".format(name))
                test.assertEqual(member.session._world, host._world_for(self.peer_of(member)),
                                 "{}: joined showing another world than the host has it show".format(name))
            if joined:
                # Joined, the host's messages keep the guest's client in step.
                member.game.shows = self.state_of_host()
            member.was_joined = joined
        # A game held for a guest that loads stays paused: what it simulated
        # meanwhile would be missing in that guest's game.
        test.assertTrue(self.host.game.paused or not host.holding,
                        "{}: the host's game runs while held for a guest".format(where))
        # A guest that loads what the host saved for it is held for until it
        # is in, or the hold runs out: what another guest does cannot end it.
        for member in self.guests:
            until, held_by, world = member.held_until
            peer = self.peer_of(member)
            if (member.loading is not None and member.linked and held_by is self.host and world == host._world
                    and self.host.now < until and peer is not None):
                test.assertTrue(peer.hold_until, "{}: the hold for guest {} ended while it loads".format(
                    where, member.number))
        # No sim is played by two guests: neither the one each controls nor
        # those made theirs on our lot.
        played = {}
        for ident, peer in sorted(host.peers.items()):
            for sim in set([peer.sim_id] + list(peer.welcomed)) - {0}:
                test.assertNotIn(sim, played, "{}: sim {} is played by peers {} and {}".format(
                    where, sim, played.get(sim), ident))
                played[sim] = ident
            # What is about the sim of a guest that is joined goes to it;
            # of one that is not, which would drop it, to the host.
            if peer.sim_id and host._live_with(peer):
                test.assertIs(host._owner(peer.sim_id), peer,
                              "{}: what is about sim {} does not go to its player".format(where, peer.sim_id))

    def check_calm(self, test, where):
        """Calm, a hold ends once the guest it is for gets in, and that is
        not long after it began or the last load: the host is never held for
        ever. And no guest loads over and over for one load of the host's
        zone."""
        world = self.host.session._world
        for member in self.guests:
            if not member.own_family:
                # One family together loads until it is in step, as ever.
                continue
            if member.calm_from[0] != world:
                member.calm_from = (world, len(member.game.adopted))
            test.assertLessEqual(len(member.game.adopted) - member.calm_from[1], CALM_ADOPTS,
                                 "{}: guest {} loaded again and again".format(where, member.number))
        held = set(ident for ident, peer in self.host.session.peers.items() if peer.hold_until)
        if held - self.held:
            self.last_load = self.host.now
        self.held = held
        if held:
            test.assertLessEqual(self.host.now - self.last_load, session.HOLD_SECONDS,
                                 "{}: the game is held long after the last load".format(where))


class FuzzTest(unittest.TestCase):
    def run_group(self, seed, families, steps=STEPS):
        rng = random.Random(seed)
        group = Group(rng, families)
        where = "seed {} families {}".format(seed, families)
        for step in range(steps):
            try:
                group.step(self)
                group.check(self, "{} step {}".format(where, step))
            except AssertionError as error:
                self.fail("{} step {}: {}".format(where, step, error))
        group.calm()
        if group.gone:
            where += " guest {} gone".format(group.gone[0].number)
        # In some runs the host's game moves on during every load. A guest
        # of one family would load for as long as that lasts, as ever.
        moves_on = rng.random() < 0.3 and all(member.own_family for member in group.guests)
        # Long enough for a guest whose gift was refused to try again after
        # the longest wait (session.RETRY_SECONDS), and be answered.
        for step in range(200):
            group.calm_step(moves_on)
            group.check(self, "{} calm {}".format(where, step))
            group.check_calm(self, "{} calm {}".format(where, step))
            # The game may stay held a while for a guest that left, and a
            # Create-a-Sim that did not open still sends its guest back.
            if (group.all_joined() and not group.host.session.holding
                    and all(member.session._cas is None for member in group.guests)):
                break
        else:
            self.fail("{}: not joined once things calmed down ({}; host holding {})".format(
                where, ", ".join("guest {} {}{}".format(member.number, member.session._phase,
                                                       " joined" if group.joined(member) else "")
                                 for member in group.guests), group.host.session.holding))
        # All in, nobody holds the game, and a press of play lets it run.
        self.assertTrue(group.host_presses_play(), where + ": play refused with every guest in")
        group.tick(session.PAUSE_SECONDS + 1)
        self.assertFalse(group.host.game.paused, where + ": paused again with every guest in")

    def test_one_family(self):
        for seed in range(RUNS):
            self.run_group(seed, [False])

    def test_families_of_their_own(self):
        for seed in range(RUNS):
            self.run_group(seed, [True])

    def test_a_group_of_one_family(self):
        for seed in range(GROUP_RUNS):
            self.run_group(seed, [False] * 3, GROUP_STEPS)

    def test_a_group_with_families_of_their_own(self):
        for seed in range(GROUP_RUNS):
            self.run_group(seed, [True] * 3, GROUP_STEPS)

    def test_a_group_of_some_of_each(self):
        for seed in range(GROUP_RUNS):
            self.run_group(seed, [False, True, False], GROUP_STEPS)


if __name__ == "__main__":
    unittest.main()
