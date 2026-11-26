import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import base64  # noqa: E402

from tsmp import frames, household, session, transfer, wire  # noqa: E402

VIEW_UPDATE = 11
PIE_MENU = 6
INTERACTABLE = 5
SAVE_COMPLETE = 181
TRAVEL = 239
DIALOG = 40
NOTIFICATION = 41
SET_SIM_ACTIVE = 21
SELECTABLE = 120
OTHER_MSG = 40
# How the host's launcher relays in every test: "routed", as one of version 3
# does for a mod that speaks it, numbering its guests (see tsmp/frames.py,
# ROUTED), or "legacy", as one before it.
RELAY = os.environ.get("TSMP_RELAY", "routed")


def view_update(*op_types):
    """A ViewUpdate with one entry holding one operation per type."""
    operations = bytearray()
    for op_type in op_types:
        operation = bytearray()
        wire.put_field(operation, 1, wire.VARINT, op_type)
        wire.put_field(operation, 2, wire.BYTES, b"data")
        wire.put_field(operations, 1, wire.BYTES, bytes(operation))
    entry = bytearray()
    wire.put_field(entry, 1, wire.BYTES, b"\x0a\x04\x08\x00\x10\x00")
    wire.put_field(entry, 2, wire.BYTES, bytes(operations))
    update = bytearray()
    wire.put_field(update, 1, wire.BYTES, bytes(entry))
    return bytes(update)


def op_types(data):
    return [op_type for _, _, op_type, _ in wire.view_update_ops(data)]


class Refused(ValueError):
    def __init__(self, message, final):
        ValueError.__init__(self, message)
        self.final = final


class FakeGame:
    def __init__(self):
        self.zone = False
        self.sim_id = 0
        self.delivered = []
        self.executed = []
        # The guest each command in `executed` ran for.
        self.executed_for = []
        self.forgotten = []
        self.during_execute = None
        self.snapshots = []
        self.snapshot_zones = []
        # The (zone, lot) of each guest each save in `snapshots` is for.
        self.snapshot_places = []
        self.zone_number = 7
        self.lot_name = ""
        self.lot_names = {}
        self.adopted = []
        self.travellers = []
        self.adopt_error = None
        # The zone the snapshot put in place was for, as the game reports
        # it once loaded; None: it cannot tell.
        self.landed = None
        # A snapshot waits for the game to travel, and how often one was
        # given up.
        self.adopt_pending = False
        self.cancels = 0
        # Whether each snapshot in `adopted` was to join the host.
        self.joins = []
        # Travels the session ordered by itself: (zone, sims).
        self.travels = []
        # Our zone for each lot the host names, by lot.
        self.our_zones = {}
        self.home = ""
        self.cross = False
        # resync's record of the saves the host's was put in place in from
        # a save of another new game: the household played in each.
        self.crossed = {}
        # The game reloads the zone from its own save instead.
        self.loads_its_own = False
        self.put = 0
        # The next load is one from the menu, not a travel.
        self.by_hand = False
        self.save = 1
        self.contributions = []
        self.whole = []
        self.household = 5
        # Households the host's save does not have.
        self.missing = set()
        # How far the game's clock is.
        self.ticks = 0
        self.merged = []
        # (player, version, others) of each gift in `merged`.
        self.merged_as = []
        self.merge_outcome = "took in"
        self.merge_error = None
        self.merge_error_stays = True
        self.welcomed = []
        self.welcome_travellers = []
        self.welcome_households = []
        self.welcome_claimed = []
        self.refuse_sims = ()
        self.sent_off = []
        self.sent_off_households = []
        self.joined_sims = []
        self.notices = []
        self.looks = {}
        self.applied = []
        self.cheats = []
        self.leaving = False
        self.reloads = 0
        self.pauses = 0
        # The speed of the game's clock, and each speed a travel that was a
        # guest's gave back.
        self.speed = 1
        self.resumed = []
        # Our zones that are homes of households from other saves.
        self.foreign_homes = set()
        self.households = {}
        # How often the host was told that what guests gave is gone.
        self.gifts_forgotten = 0

    def in_zone(self):
        if not self.zone:
            # The game left its zone: a snapshot that waited is put in
            # place, or was dropped, by now.
            self.adopt_pending = False
        return self.zone

    def zone_id(self):
        return self.zone_number

    def lot(self):
        return self.lot_name

    def host_zone(self, zone_id):
        return zone_id

    def lot_of(self, zone_id):
        return self.lot_names.get(zone_id, "")

    def active_sim_id(self):
        return self.sim_id

    def deliver(self, msg_id, data):
        self.delivered.append((msg_id, data))

    def execute(self, name, args, sim_id, peer=None):
        self.executed.append((name, args, sim_id))
        self.executed_for.append(peer)
        if self.during_execute is not None:
            self.during_execute()

    def forget_peer(self, peer):
        self.forgotten.append(peer)

    def snapshot_zone(self, zone_id, lot, player=""):
        return zone_id or self.zone_number

    def snapshot(self, done, places):
        """One save for every guest waiting; the test answers it with one
        payload, which stands for each guest's snapshot."""
        self.snapshots.append(lambda payload: done(None if payload is None else [payload for _ in places]))
        self.snapshot_zones.extend(place[0] for place in places)
        # As the session leaves it: guests may join the save until it is done.
        self.snapshot_places.append(places)

    def adopt(self, payload, travellers=(), joined=None):
        if self.adopt_error is not None:
            raise self.adopt_error
        self.adopted.append(payload)
        self.travellers.append(list(travellers))
        self.joins.append(joined)
        self.adopt_pending = True
        if not self.loads_its_own:
            self.put += 1

    def adoptions(self):
        return self.put

    def adopted_zone(self):
        return self.landed or 0

    def cancel_adopt(self):
        self.cancels += 1
        if self.adopt_pending and not self.loads_its_own:
            # Given up before the game left its zone: never put in place.
            self.put -= 1
        self.adopt_pending = False

    def travel(self, zone_id, sim_ids):
        self.travels.append((zone_id, list(sim_ids)))

    def our_zone(self, zone_id, lot=""):
        return self.our_zones.get(lot, zone_id)

    def home_lot(self):
        return self.home

    def cross_save(self):
        return self.cross or (self.save in self.crossed and self.crossed[self.save] == self.household)

    def reload(self):
        self.reloads += 1

    def save_id(self):
        return self.save

    def contribute(self, done, whole=False):
        self.contributions.append(done)
        self.whole.append(whole)

    def household_id(self):
        return self.household

    def has_household(self, household_id):
        return household_id not in self.missing

    def playing_adopted(self):
        return self.put > 0

    def loaded_by_hand(self):
        """Once, after the player loaded a save from the menu; a load then
        forgets what came from the host."""
        by_hand, self.by_hand = self.by_hand, False
        if by_hand:
            self.put = 0
        return by_hand

    def progress(self):
        return str(self.ticks)

    def merge(self, payload, save_id=0, player="", version=None, others=()):
        if self.merge_error is not None:
            error, self.merge_error = self.merge_error, self.merge_error if self.merge_error_stays else None
            raise error
        self.merged.append((payload, save_id))
        self.merged_as.append((player, version, list(others)))
        return self.merge_outcome

    def forget_gifts(self):
        self.gifts_forgotten += 1

    def welcome(self, sim_id, before=(), travellers=(), claimed=(), guest_household=0):
        self.welcome_households.append(guest_household)
        self.welcome_travellers.append(list(travellers))
        self.welcome_claimed.append(set(claimed))
        if sim_id in self.refuse_sims or sim_id in claimed:
            return []
        self.welcomed.append(sim_id)
        # The travellers that are no other player's come along, as
        # household.welcome has them.
        return [sim_id] + [sim for sim in travellers if sim != sim_id and sim not in claimed]

    def send_off(self, sim_ids, guest_household=0):
        self.sent_off.append(list(sim_ids))
        self.sent_off_households.append(guest_household)

    def joined(self, sim_id):
        self.joined_sims.append(sim_id)

    def notify(self, text):
        self.notices.append(text)
        return text

    def appearance(self, sim_id):
        return self.looks.get(sim_id, b"")

    def look_changes(self, before, after):
        return (after, ["outfits"]) if after != before else (b"", [])

    def apply_appearance(self, sim_id, look, names=()):
        self.applied.append((sim_id, look))

    def leaving_zone(self):
        return self.leaving

    def client_cheat(self, line):
        self.cheats.append(line)

    def pause(self):
        self.pauses += 1

    def clock_speed(self):
        return self.speed

    def resume_after_travel(self, speed):
        self.resumed.append(speed)

    def foreign_home(self, zone_id):
        return zone_id in self.foreign_homes

    def same_household(self, a, b):
        return self.households.get(a, 1) == self.households.get(b, 1)


class Player:
    """One game with its session; `sent` collects the frames it emits, as
    the other games get them.

    A host's player stands in for its launcher too, which wraps what each
    guest sends in ROUTED with that guest's peer once it heard that our mod
    speaks version 3, and says which guests come and go with `peer_up` and
    `peer_gone` as well as `link`. `routed` False makes it a launcher from
    before version 3, which does none of this."""

    def __init__(self, role, routed=None):
        self.game = FakeGame()
        self.sent = []
        # Host: the peer each frame in `sent` is for, None for every guest.
        self.sent_to = []
        self.routed = RELAY == "routed" if routed is None else routed
        # Host: the launcher heard that our mod speaks version 3.
        self.speaks = False
        # Host: the guests whose links are up, by peer.
        self.peers_up = set()
        self.logged = []
        self.now = 1000.0
        consts = session.Consts(
            VIEW_UPDATE,
            PIE_MENU,
            INTERACTABLE,
            SET_SIM_ACTIVE,
            host_only=[SAVE_COMPLETE, TRAVEL],
            guest_local=[SAVE_COMPLETE],
            owned={DIALOG: 5, NOTIFICATION: 5},
            shared=[NOTIFICATION],
            notice=NOTIFICATION,
            travel=TRAVEL,
            selectable=SELECTABLE,
        )
        self.session = session.Session(self.game, consts, self._send, log=self.logged.append, clock=lambda: self.now)
        self.role = role

    def _send(self, frame):
        for kind, ident, payload in frames.Decoder().feed(frame):
            kind, ident, payload, to = frames.unroute(kind, ident, payload)
            if to is not None and not (self.role == session.HOST and self.routed and self.speaks):
                raise AssertionError("a ROUTED frame for a launcher that does not unwrap it")
            if kind == frames.CONTROL and frames.parse_json(payload).get("type") == "state":
                self.speaks = self.speaks or frames.number(frames.parse_json(payload).get("v")) >= 3
            self.sent.append((kind, ident, payload))
            self.sent_to.append(to)

    def take(self):
        sent, self.sent = self.sent, []
        self.sent_to = []
        return sent

    def take_routed(self):
        """(peer, frame) of each frame sent, the peer None for every guest."""
        to = self.sent_to
        return list(zip(to, self.take()))

    def controls(self):
        return [frames.parse_json(payload) for kind, _, payload in self.take() if kind == frames.CONTROL]

    def receive(self, sent, peer=1):
        """Frames from another game; on a host, from the guest numbered
        `peer`, through its launcher."""
        wraps = self.role == session.HOST and self.routed and self.speaks
        for kind, ident, payload in sent:
            if not wraps:
                self.session.frame(kind, ident, payload)
                continue
            (routed,) = frames.Decoder().feed(frames.route(peer, frames.pack(kind, ident, payload)))
            kind, ident, payload, peer = frames.unroute(*routed)
            self.session.frame(kind, ident, payload, peer)

    def link(self, remotes):
        """Our launcher has that many remotes now. A host's launcher of
        version 3 names them: the first `remotes` peers are up, and each one
        that is up again has reconnected."""
        if self.role == session.HOST and self.routed and self.speaks:
            for peer in sorted(self.peers_up - set(range(1, remotes + 1))):
                self._relay_says("peer_gone", peer=peer)
            for peer in range(1, remotes + 1):
                self._relay_says("peer_up", peer=peer, name="guest {}".format(peer), player="p{}".format(peer))
            self.peers_up = set(range(1, remotes + 1))
        self._relay_says("link", remotes=remotes)

    def _relay_says(self, kind, **fields):
        self.session.frame(frames.CONTROL, 0, frames.Decoder().feed(frames.control(kind, **fields))[0][2])

    def control(self, kind, **fields):
        """A control from the other game."""
        self.receive(frames.Decoder().feed(frames.control(kind, **fields)))

    def tick(self, seconds=0.0):
        self.now += seconds
        self.session.tick()

    def leave(self):
        self.game.zone = False
        self.tick()

    def arrive(self, lot=None):
        """The game is through its loading screen. A guest's then settles in
        before it orders a travel (session.SETTLE_SECONDS), which a player
        takes as long to notice."""
        if lot is not None:
            self.game.zone_number = lot
        self.game.zone = True
        self.tick()
        if self.role == session.GUEST:
            self.tick(session.SETTLE_SECONDS)


def both(host, guest, seconds=0.0):
    host.tick(seconds)
    guest.tick(seconds)
    settle(host, guest)


def connected(guest_save=1, guest_lot=7, snapshot_zone=None, cross=False):
    """A host and a guest in a zone each, with the relay's link events
    delivered and their frames exchanged until quiet. `snapshot_zone`
    stands in for the host's answer to which of its zones a guest's
    snapshot is of. `cross`: the guest's game knows that the host's save
    was put in place in a save of its own (resync.cross_save), as a guest
    with a family of its own that has loaded the host's state before."""
    host, guest = Player(session.HOST), Player(session.GUEST)
    guest.game.cross = cross
    if snapshot_zone is not None:
        host.game.snapshot_zone = snapshot_zone
    host.game.zone = guest.game.zone = True
    guest.game.sim_id = 222
    guest.game.save = guest_save
    guest.game.zone_number = guest_lot
    host.session.bridge_up(session.HOST)
    guest.session.bridge_up(session.GUEST)
    host.link(1)
    guest.link(1)
    settle(host, guest)
    return host, guest


def joined(cross=False):
    """Connected, and the guest has loaded the host's state once, as every
    guest does on arrival."""
    host, guest = connected(cross=cross)
    reloads_from_host(host, guest)
    return host, guest


def strangers():
    """A host and a guest on the same lot, each in a save of their own."""
    return connected(guest_save=2)


def travel_to(zone_id, *sim_ids):
    """A TravelSimsToZone message."""
    out = bytearray()
    wire.put_field(out, 1, wire.FIXED64, zone_id)
    wire.put_field(out, 2, wire.BYTES, b"".join(sim_id.to_bytes(8, "little") for sim_id in sim_ids))
    return bytes(out)


def owned(sim_id):
    """A message whose field 5 names the sim it belongs to."""
    out = bytearray()
    wire.put_field(out, 1, wire.VARINT, 9)
    wire.put_field(out, 5, wire.VARINT, sim_id)
    return bytes(out)


def settle(*players):
    """Exchange frames until all fall quiet. The one host among them is the
    hub, as its launcher is: what a guest sends reaches it from that guest's
    peer, numbered by the guest's place among the others, and what it sends
    reaches the guest it is for or, when for none, every guest. Guests hear
    only the host. Two hosts each take the other for their one guest."""
    hosts = [player for player in players if player.role == session.HOST]
    hub = hosts[0] if len(hosts) == 1 else None
    numbers = {}
    for player in players:
        if player is not hub:
            numbers[player] = len(numbers) + 1
    for _ in range(100):
        sent = [(player, player.take_routed()) for player in players]
        if not any(out for _, out in sent):
            return
        # In the order the first two always were: the second hears the
        # first, then the first the second.
        for receiver in players[1:] + players[:1]:
            for sender, out in sent:
                if sender is receiver or (hub is not None and hub not in (sender, receiver)):
                    continue
                number = numbers.get(receiver if sender is hub else sender, 1)
                if sender is hub:
                    unknown = [to for to, _ in out if to not in (None, 0) and to not in numbers.values()]
                    if unknown:
                        raise AssertionError("the host sent to peers {} it does not have".format(unknown))
                    chosen = [frame for to, frame in out if to in (None, 0, number)]
                else:
                    chosen = [frame for _, frame in out]
                receiver.receive(chosen, peer=number)
    raise AssertionError("the sessions never went quiet")


def host_reloads(host, guest):
    """The host's zone goes away and comes back, as when it edits a sim."""
    host.leave()
    settle(host, guest)
    host.arrive()
    settle(host, guest)


def answers(host, guest, payload=b"save"):
    """The host's save for the guest is done and reaches it."""
    host.game.snapshots[-1](payload)
    settle(host, guest)


def loads(host, guest):
    """The guest's game loads what it adopted."""
    guest.leave()
    guest.game.save = host.game.save
    guest.arrive()
    settle(host, guest)


def reloads_from_host(host, guest, payload=b"save"):
    """A stale guest asks for the host's state, gets it and loads it."""
    both(host, guest)
    answers(host, guest, payload)
    loads(host, guest)


def gives(host, guest, payload=b"household"):
    """A stale guest saves its household and the host answers."""
    both(host, guest)
    guest.game.contributions[-1](payload)
    settle(host, guest)


def guest_of(host):
    """What the host keeps of its one guest."""
    (peer,) = host.session.peers.values()
    return peer


def asked(player):
    return [payload for kind, _, payload in player.sent if kind == frames.CONTROL and b'"resync"' in payload]


class HandshakeTest(unittest.TestCase):
    def test_announces_state_when_the_bridge_comes_up(self):
        host = Player(session.HOST)
        host.session.bridge_up(session.HOST)
        self.assertEqual(host.controls(), [{"type": "state", "role": "host", "in_zone": False, "v": 3, "loaded": False,
                                            "syncing": False, "joined": False, "apart": False,
                                            "stopped": False, "want": True}])
        self.assertFalse(host.session.live)

    def test_announces_zone_changes_on_tick(self):
        host = Player(session.HOST)
        host.session.bridge_up(session.HOST)
        host.take()
        host.tick()
        self.assertEqual(host.take(), [])
        host.game.lot_name = "12:0"
        host.arrive()
        (state,) = host.controls()
        self.assertEqual(state["zone"], "7")
        self.assertEqual(state["lot"], "12:0")
        self.assertEqual(state["save"], "1")
        self.assertTrue(state["in_zone"])
        self.assertTrue(state["world"])

    def test_every_load_of_the_hosts_zone_is_another_world(self):
        host = Player(session.HOST)
        host.game.zone = True
        host.session.bridge_up(session.HOST)
        first = host.controls()[0]["world"]
        host.leave()
        host.arrive()
        self.assertNotEqual(host.controls()[-1]["world"], first)

    def test_a_guest_loads_the_hosts_state_before_it_joins(self):
        host, guest = connected()
        self.assertFalse(host.session.live)
        self.assertFalse(guest.session.live)
        self.assertTrue(guest.session.stale)
        both(host, guest)
        self.assertEqual(len(host.game.snapshots), 1)
        answers(host, guest)
        self.assertEqual(guest.game.adopted, [b"save"])
        loads(host, guest)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)
        self.assertEqual(guest_of(host).sim_id, 222)
        # Same family: nothing to bring over.
        self.assertEqual(guest.game.contributions, [])

    def test_a_late_game_learns_the_state_of_the_one_already_there(self):
        host, guest = Player(session.HOST), Player(session.GUEST)
        host.game.zone = guest.game.zone = True
        guest.game.sim_id = 9
        # The guest's game and both launchers are up before the host's game.
        guest.session.bridge_up(session.GUEST)
        guest.link(1)
        guest.take()
        # Now the host's game connects; its relay only tells it about the link.
        host.session.bridge_up(session.HOST)
        host.link(1)
        settle(host, guest)
        reloads_from_host(host, guest)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)
        self.assertEqual(guest_of(host).sim_id, 9)

    def test_not_live_until_the_other_game_loads(self):
        host, guest = Player(session.HOST), Player(session.GUEST)
        host.game.zone = True
        host.session.bridge_up(session.HOST)
        guest.session.bridge_up(session.GUEST)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        self.assertFalse(host.session.live)
        self.assertFalse(guest.session.stale)
        self.assertEqual(host.game.snapshots, [])

        guest.game.sim_id = 5
        guest.arrive()
        settle(host, guest)
        reloads_from_host(host, guest)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)
        self.assertEqual(guest_of(host).sim_id, 5)

    def test_bridge_down_ends_the_session(self):
        host, guest = joined()
        guest.session.bridge_down("test")
        self.assertFalse(guest.session.live)
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"x"), b"x")
        self.assertTrue(guest.session.command("interactions.select", ["1"]))

    def test_two_hosts_do_not_join(self):
        one, two = Player(session.HOST), Player(session.HOST)
        one.game.zone = two.game.zone = True
        one.session.bridge_up(session.HOST)
        two.session.bridge_up(session.HOST)
        one.link(1)
        two.link(1)
        settle(one, two)
        self.assertFalse(one.session.live)
        self.assertFalse(two.session.live)

    def test_games_with_another_version_of_the_mod_do_not_join(self):
        host, guest = connected()
        guest.control("state", role="host", in_zone=True, zone="7", save="1", world="x.1", v=1)
        self.assertFalse(guest.session.speaks_our_version)
        both(host, guest)
        self.assertFalse(guest.session.live)

    def test_guest_reports_a_new_selection(self):
        host, guest = joined()
        guest.tick()
        self.assertEqual(guest.take(), [])
        guest.game.sim_id = 333
        both(host, guest)
        self.assertEqual(guest_of(host).sim_id, 333)

    def test_lots_are_compared_by_what_names_them_in_any_save(self):
        host, guest = connected(guest_lot=99)
        host.game.lot_name = guest.game.lot_name = "12:0"
        host.leave()
        host.arrive()
        guest.leave()
        guest.arrive()
        settle(host, guest)
        self.assertTrue(guest.session.same_zone)
        self.assertFalse(guest.session.apart)
        # It left while it was loading the host's copy of lot 99, and tries
        # again shortly, this time to join.
        both(host, guest, session.RETRY_SECONDS[0] + 1)
        self.assertTrue(guest.session.stale)


class LinkTest(unittest.TestCase):
    def test_a_lost_link_holds_the_guest_and_it_loads_the_host_again_once_back(self):
        host, guest = joined()
        host.link(0)
        guest.link(0)
        self.assertFalse(host.session.live)
        self.assertFalse(guest.session.live)
        self.assertEqual(guest_of(host).sim_id, 0)
        # Its own simulation is not shown meanwhile; its player is told.
        self.assertTrue(guest.session.stale)
        self.assertIsNone(guest.session.outbound(OTHER_MSG, b"local"))
        self.assertTrue(any("lost the connection" in notice for notice in guest.game.notices))
        # What the host sent meanwhile is lost, so it loads the host again.
        guest.tick(30)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        self.assertFalse(guest.session.live)
        reloads_from_host(host, guest)
        self.assertEqual(len(host.game.snapshots), 2)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)

    def test_a_guest_whose_host_does_not_come_back_plays_on_by_itself(self):
        host, guest = joined()
        guest.link(0)
        guest.tick(session.LINK_SECONDS - 1)
        self.assertTrue(guest.session.stale)
        guest.tick(2)
        self.assertFalse(guest.session.stale)
        # Its game client showed the host's world; now it shows its own.
        self.assertEqual(guest.game.reloads, 1)
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"local"), b"local")
        self.assertTrue(guest.session.command("interactions.select", ["1"]))

    def test_gone_holds_the_guest_until_the_hosts_game_is_back(self):
        host, guest = joined()
        guest.control("gone")
        self.assertFalse(guest.session.live)
        self.assertTrue(guest.session.stale)
        # The host's game restarts: a new world, so the guest loads it.
        host = Player(session.HOST)
        host.game.zone = True
        host.session.bridge_up(session.HOST)
        host.link(1)
        settle(host, guest)
        reloads_from_host(host, guest)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)

    def test_a_link_lost_while_the_household_is_on_its_way_is_given_again(self):
        host, guest = strangers()
        both(host, guest)
        guest.game.contributions[0](b"household")
        host.take()
        guest.link(0)
        host.link(0)
        guest.take()
        guest.tick(10)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        both(host, guest)
        self.assertEqual(len(guest.game.contributions), 2)
        guest.game.contributions[1](b"household")
        settle(host, guest)
        self.assertEqual(host.game.merged, [(b"household", 2)])
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_host_back_in_another_save_has_our_household_brought_in(self):
        host, guest = joined()
        host.leave()
        settle(host, guest)
        host.game.save = 99
        host.arrive()
        settle(host, guest)
        both(host, guest)
        self.assertEqual(len(guest.game.contributions), 1)
        self.assertEqual(len(host.game.snapshots), 1)
        guest.game.contributions[0](b"household")
        settle(host, guest)
        self.assertEqual(host.game.merged, [(b"household", 1)])
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_new_peer_finds_nothing_of_the_old_ones_transfers(self):
        host, guest = connected()
        both(host, guest)
        host.game.snapshots[0](b"x" * (transfer.WINDOW * 2))
        self.assertIsNotNone(guest_of(host).transfers.outgoing(1))
        # The guest's launcher dialled again; the host's saw no gap.
        host.link(1)
        self.assertIsNone(guest_of(host).transfers.outgoing(1))

    def test_a_transfer_nobody_takes_is_dropped(self):
        host, guest = connected()
        both(host, guest)
        host.game.snapshots[0](b"x" * (transfer.WINDOW * 2))
        host.take()
        host.tick(session.STALL_SECONDS + 1)
        self.assertIsNone(guest_of(host).transfers.outgoing(1))

    def test_a_save_under_way_when_the_link_dropped_is_not_sent_for_a_new_request(self):
        host, guest = connected()
        both(host, guest)
        host.link(0)
        guest.link(0)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        both(host, guest, 30)
        # The first save still runs: no second one at the same time.
        self.assertEqual(len(host.game.snapshots), 1)
        host.game.snapshots[0](b"old")
        settle(host, guest)
        self.assertEqual(guest.game.adopted, [])
        self.assertEqual(len(host.game.snapshots), 2)
        answers(host, guest, b"new")
        self.assertEqual(guest.game.adopted, [b"new"])

    def test_a_link_lost_while_loading_changes_nothing(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        guest.link(0)
        host.link(0)
        loads(host, guest)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        self.assertTrue(guest.session.live)
        self.assertTrue(host.session.live)
        self.assertEqual(len(host.game.snapshots), 1)

    def test_a_snapshot_finished_after_the_link_was_lost_is_not_sent(self):
        host, guest = connected()
        both(host, guest)
        host.link(0)
        host.take()
        host.game.snapshots[0](b"save")
        self.assertEqual(host.take(), [])


    def test_a_host_at_the_menu_is_waited_for_and_then_left(self):
        host, guest = joined()
        host.leave()
        settle(host, guest)
        self.assertTrue(guest.session.stale)
        self.assertTrue(any("left its lot" in notice for notice in guest.game.notices))
        guest.tick(session.AWAY_SECONDS - 1)
        self.assertTrue(guest.session.stale)
        guest.tick(2)
        self.assertFalse(guest.session.stale)
        self.assertEqual(guest.game.reloads, 1)
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"local"), b"local")
        # Back in its game, the host is joined again.
        host.arrive()
        settle(host, guest)
        both(host, guest, session.RETRY_SECONDS[0] + 1)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_guest_that_loaded_while_the_link_was_down_joins_once_back(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        guest.link(0)
        host.link(0)
        loads(host, guest)
        # Its client shows the host's world: muted until the host is back.
        self.assertTrue(guest.session.stale)
        self.assertIsNone(guest.session.outbound(OTHER_MSG, b"local"))
        host.link(1)
        guest.link(1)
        settle(host, guest)
        both(host, guest)
        self.assertTrue(guest.session.live)
        self.assertTrue(host.session.live)
        self.assertEqual(len(host.game.snapshots), 1)

    def test_a_guest_that_loaded_while_the_link_was_down_loads_again_if_the_host_moved_on(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        guest.link(0)
        host.link(0)
        loads(host, guest)
        host_reloads(host, guest)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        both(host, guest)
        self.assertFalse(guest.session.live)
        self.assertEqual(len(host.game.snapshots), 2)
        answers(host, guest)
        loads(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_save_under_way_is_not_started_twice(self):
        host, guest = strangers()
        both(host, guest)
        guest.link(0)
        host.link(0)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        both(host, guest)
        self.assertEqual(len(guest.game.contributions), 1)
        guest.game.contributions[0](b"household")
        settle(host, guest)
        self.assertEqual(host.game.merged, [(b"household", 2)])

    def test_a_host_that_lost_our_household_is_given_all_of_it(self):
        host, guest = strangers()
        gives(host, guest)
        reloads_from_host(host, guest)
        # The host's game starts again from its save, without our household.
        host = Player(session.HOST)
        host.game.zone = True
        host.game.missing = {5}
        host.session.bridge_up(session.HOST)
        host.link(1)
        guest.control("gone")
        guest.link(1)
        settle(host, guest)
        both(host, guest)
        # From its own save it gave all of it, and does again.
        self.assertEqual(guest.game.whole, [True, True])
        host.game.missing = set()
        guest.game.contributions[-1](b"all of it")
        settle(host, guest)
        self.assertEqual(host.game.merged, [(b"all of it", 1)])
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_guest_that_loaded_while_the_host_left_gives_what_it_played(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        host.leave()
        settle(host, guest)
        loads(host, guest)
        self.assertFalse(guest.session.stale)
        guest.tick(30)
        # Back on the lot, the host has not seen what the guest played.
        host.arrive()
        settle(host, guest)
        both(host, guest)
        self.assertEqual(len(guest.game.contributions), 1)

    def test_a_guest_that_loaded_another_lot_while_the_link_was_down_plays_it(self):
        host, guest = joined()
        guest.leave()
        settle(host, guest)
        guest.arrive(8)
        settle(host, guest)
        both(host, guest)
        answers(host, guest)
        guest.link(0)
        host.link(0)
        loads(host, guest)
        self.assertFalse(guest.session.stale)
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"local"), b"local")

    def test_a_household_the_host_lacks_in_part_is_given_whole(self):
        # In the host's save a gift holds only what the guest ran.
        host, guest = joined()
        guest.leave()
        settle(host, guest)
        guest.arrive(8)
        settle(host, guest)
        reloads_from_host(host, guest)
        missing = Refused("sims came without their data", final=False)
        missing.missing = True
        host.game.merge_error = missing
        host.game.merge_error_stays = False
        guest.tick(session.GIVE_SECONDS + 1)
        self.assertEqual(guest.game.whole, [False])
        guest.game.contributions[0](b"part of it")
        settle(host, guest)
        guest.tick(1)
        self.assertEqual(guest.game.whole, [False, True])
        guest.game.contributions[1](b"all of it")
        settle(host, guest)
        self.assertEqual(host.game.merged, [(b"all of it", 1)])

    def test_the_launchers_hear_of_being_apart_after_the_link_returns(self):
        host, guest = joined()
        guest.leave()
        settle(host, guest)
        guest.arrive(8)
        settle(host, guest)
        reloads_from_host(host, guest)
        for player in (host, guest):
            player.link(0)
        for player in (host, guest):
            player.link(1)
        settle(host, guest)
        both(host, guest)
        for player in (host, guest):
            last = [state for state in player.controls() if state.get("type") == "state"][-1:]
            self.assertTrue(player.session.apart)
            self.assertTrue(all(state["apart"] for state in last), player.role)
            self.assertTrue(player.session._announced["apart"], player.role)

    def test_a_late_answer_to_an_earlier_save_does_not_spoil_the_latest(self):
        host, guest = connected()
        both(host, guest)
        both(host, guest, session.SAVE_SECONDS + 1)
        both(host, guest, session.RETRY_SECONDS[0] + 1)
        self.assertEqual(len(host.game.snapshots), 2)
        host.game.snapshots[0](b"old")
        settle(host, guest)
        host.game.snapshots[1](b"new")
        settle(host, guest)
        self.assertEqual(guest.game.adopted, [b"new"])

    def test_a_host_whose_lot_reloads_while_saving_saves_again(self):
        host, guest = connected()
        both(host, guest)
        host_reloads(host, guest)
        host.game.snapshots[0](b"old")
        settle(host, guest)
        self.assertEqual(guest.game.adopted, [])
        self.assertTrue(guest.session.stale)
        answers(host, guest, b"new")
        self.assertEqual(guest.game.adopted, [b"new"])

    def test_a_guest_whose_household_is_still_lacking_after_giving_all_stops(self):
        host, guest = strangers()
        missing = Refused("sims came without their data", final=False)
        missing.missing = True
        host.game.merge_error = missing
        gives(host, guest)
        # From its own save it gave all of it at once: nothing more to give.
        self.assertFalse(guest.session.stale)
        for _ in range(3):
            both(host, guest, session.RETRY_SECONDS[-1] + 1)
        self.assertEqual(len(guest.game.contributions), 1)

    def test_a_guest_drops_a_snapshot_of_a_world_the_host_left(self):
        host, guest = connected()
        both(host, guest)
        host.game.snapshots[0](b"x" * (transfer.WINDOW * 2))
        guest.receive(host.take()[:3])
        self.assertEqual(guest.session._phase, session.RECEIVING)
        host_reloads(host, guest)
        self.assertEqual(guest.game.adopted, [])
        self.assertIsNone(guest_of(host).transfers.outgoing(1))
        answers(host, guest, b"new")
        self.assertEqual(guest.game.adopted, [b"new"])

    def test_a_host_that_restarts_again_without_our_household_is_given_it_again(self):
        host, guest = strangers()
        gives(host, guest)
        reloads_from_host(host, guest)
        for _ in range(2):
            # The host's game starts again from a save without our household.
            host = Player(session.HOST)
            host.game.zone = True
            host.game.missing = {5}
            host.session.bridge_up(session.HOST)
            host.link(1)
            guest.control("gone")
            guest.link(1)
            settle(host, guest)
            both(host, guest)
            host.game.missing = set()
            guest.game.contributions[-1](b"all of it")
            settle(host, guest)
            self.assertEqual(host.game.merged, [(b"all of it", 1)])
            reloads_from_host(host, guest)
            self.assertTrue(guest.session.live)
        self.assertEqual(guest.game.whole[-2:], [True, True])

    def test_a_guest_that_gave_up_for_good_in_the_hosts_save_stays_quiet(self):
        host, guest = joined()
        host_reloads(host, guest)
        guest.session._give_up("the host lacks our household although we gave all of it", final=True)
        asked = len(host.game.snapshots)
        for _ in range(3):
            both(host, guest, session.RETRY_SECONDS[-1] + 1)
        self.assertEqual(len(host.game.snapshots), asked)

class HostTest(unittest.TestCase):
    def test_messages_pass_untouched_while_not_live(self):
        host = Player(session.HOST)
        host.session.bridge_up(session.HOST)
        host.take()
        self.assertEqual(host.session.outbound(OTHER_MSG, b"x"), b"x")
        self.assertEqual(host.take(), [])

    def test_messages_go_to_both_games(self):
        host, guest = joined()
        self.assertEqual(host.session.outbound(OTHER_MSG, b"x"), b"x")
        sent = host.take()
        self.assertEqual(sent, [(frames.MSG, OTHER_MSG, b"x")])
        guest.receive(sent)
        self.assertEqual(guest.game.delivered, [(OTHER_MSG, b"x")])

    def test_saving_and_travel_stay_on_the_host(self):
        host, _ = joined()
        self.assertEqual(host.session.outbound(SAVE_COMPLETE, b"x"), b"x")
        self.assertEqual(host.session.outbound(TRAVEL, b"x"), b"x")
        self.assertEqual(host.take(), [])

    def test_selection_changes_stay_on_the_host(self):
        host, _ = joined()
        mixed = view_update(86, SET_SIM_ACTIVE, 86)
        self.assertEqual(host.session.outbound(VIEW_UPDATE, mixed), mixed)
        (kind, ident, payload), = host.take()
        self.assertEqual((kind, ident), (frames.MSG, VIEW_UPDATE))
        self.assertEqual(op_types(payload), [86, 86])

        only = view_update(SET_SIM_ACTIVE)
        self.assertEqual(host.session.outbound(VIEW_UPDATE, only), only)
        self.assertEqual(host.take(), [])

    def test_own_pie_menu_is_not_shown_to_the_guest(self):
        host, _ = joined()
        self.assertEqual(host.session.outbound(PIE_MENU, b"menu"), b"menu")
        self.assertEqual(host.take(), [])

    def test_guest_command_runs_as_their_sim_and_replies_go_to_them(self):
        host, guest = joined()
        replies = []
        host.game.during_execute = lambda: replies.append(
            (host.session.outbound(PIE_MENU, b"menu"), host.session.outbound(OTHER_MSG, b"world"))
        )

        self.assertFalse(guest.session.command("interactions.choices", ["12", "2"]))
        host.receive(guest.take())

        self.assertEqual(host.game.executed, [("interactions.choices", ["12", "2"], 222)])
        # The menu is for the guest only; anything else is for everyone.
        self.assertEqual(replies, [(None, b"world")])
        self.assertEqual(host.take(), [(frames.MSG, PIE_MENU, b"menu"), (frames.MSG, OTHER_MSG, b"world")])
        # Afterwards the host's own menus are its own again.
        self.assertEqual(host.session.outbound(PIE_MENU, b"menu"), b"menu")
        self.assertEqual(host.take(), [])

    def test_a_failing_command_does_not_leak_remote_routing(self):
        host, _ = joined()

        def fail():
            raise RuntimeError("boom")

        host.game.during_execute = fail
        with self.assertRaises(RuntimeError):
            host.session.frame(frames.COMMAND, 0, b'{"name":"interactions.select","args":[]}')
        self.assertEqual(host.session.outbound(PIE_MENU, b"menu"), b"menu")

    def test_only_shared_world_commands_are_accepted(self):
        host, _ = joined()
        host.session.frame(frames.COMMAND, 0, b'{"name":"persistence.save_game","args":[]}')
        host.session.frame(frames.COMMAND, 0, b'{"name":5,"args":[]}')
        host.session.frame(frames.COMMAND, 0, b"garbage")
        self.assertEqual(host.game.executed, [])

    def test_host_commands_run_locally(self):
        host, _ = joined()
        self.assertTrue(host.session.command("interactions.select", ["1"]))
        self.assertTrue(host.session.command("clock.setspeed", ["1"]))
        self.assertEqual(host.take(), [])

    def test_frames_are_ignored_while_not_live(self):
        host = Player(session.HOST)
        host.session.bridge_up(session.HOST)
        host.session.frame(frames.COMMAND, 0, b'{"name":"interactions.select","args":[]}')
        self.assertEqual(host.game.executed, [])

    def test_the_host_pauses_when_a_guest_joins(self):
        host, guest = joined()
        self.assertGreaterEqual(host.game.pauses, 1)
        self.assertEqual(guest.game.pauses, 0)

    def test_the_host_has_the_guests_sim_brought_in_and_noted(self):
        host, guest = joined()
        self.assertEqual(host.game.welcomed, [222])
        self.assertEqual(host.game.joined_sims, [222])

    def test_the_host_keeps_its_own_sims_from_the_guest(self):
        host, guest = joined()
        host.game.refuse_sims = (111,)
        guest.game.sim_id = 111
        both(host, guest)
        self.assertEqual(host.game.welcomed, [222])

    def test_the_host_sends_the_guests_sims_off_when_it_leaves_for_another_lot(self):
        host, guest = joined()
        guest.leave()
        settle(host, guest)
        # Not while it only loads its zone again.
        self.assertEqual(host.game.sent_off, [])
        guest.arrive(8)
        settle(host, guest)
        self.assertEqual(host.game.sent_off, [[222]])


    def test_a_screen_for_the_guests_sim_opens_in_the_guests_game(self):
        host, guest = joined()
        host.game.sim_id = 111
        self.assertFalse(host.session.client_cheat("sims.exit2cas 222 1 0"))
        self.assertTrue(host.session.client_cheat("sims.exit2cas 111 1 0"))
        settle(host, guest)
        self.assertEqual(guest.game.cheats, ["sims.exit2cas 222 1 0"])

    def test_only_the_sim_the_guest_plays_now_is_sent_off(self):
        host, guest = joined()
        guest.game.sim_id = 333
        both(host, guest)
        guest.leave()
        settle(host, guest)
        guest.arrive(8)
        settle(host, guest)
        self.assertEqual(host.game.sent_off, [[333]])

    def test_a_host_is_no_longer_held_once_the_guest_drops_its_state(self):
        host, guest = connected()
        both(host, guest)
        host.game.snapshots[0](b"x" * (transfer.WINDOW * 2))
        guest.leave()
        settle(host, guest)
        self.assertFalse(host.session.holding)

    def test_the_host_holds_for_a_guest_whose_lot_it_lacks_but_sends_its_own(self):
        host, guest = connected(guest_lot=99, snapshot_zone=lambda zone_id, lot, player="": 7)
        self.assertTrue(host.session.holding)
        answers(host, guest)
        loads(host, guest)

    def test_a_guest_sent_the_hosts_lot_unheld_asks_again_to_join(self):
        host, guest = connected(guest_lot=99, snapshot_zone=lambda zone_id, lot, player="": 0)
        self.assertFalse(host.session.holding)
        answers(host, guest)
        guest.leave()
        guest.game.zone_number = 7
        guest.arrive()
        settle(host, guest)
        # It is on the host's lot, but not on a world the host held for.
        self.assertFalse(guest.session.live)
        self.assertTrue(guest.session.stale)

    def test_a_guest_edits_its_sim_and_the_host_takes_the_new_look(self):
        host, guest = joined()
        guest.game.looks[222] = b"before"
        self.assertTrue(guest.session.command("sims.modify_in_cas", ["222"]))
        self.assertTrue(guest.session.client_cheat("sims.exit2cas 222 1 222"))
        # Its own simulation runs Create-a-Sim.
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"own"), b"own")
        guest.leave()
        guest.game.looks[222] = b"after"
        guest.arrive()
        settle(host, guest)
        self.assertEqual(host.game.applied, [(222, b"after")])
        # The host took it before it saved its state for the guest.
        self.assertEqual(len(host.game.snapshots), 2)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_create_a_sim_command_the_game_refuses_changes_nothing(self):
        host, guest = joined()
        self.assertTrue(guest.session.command("sims.modify_in_cas", ["222"]))
        # No line to open it came: still muted, still forwarding.
        self.assertIsNone(guest.session.outbound(OTHER_MSG, b"own"))
        self.assertFalse(guest.session.command("interactions.select", ["1"]))

    def test_create_a_sim_that_did_not_open_loads_the_hosts_state_again(self):
        host, guest = joined()
        guest.session.client_cheat("sims.exit2cas 222 1 222")
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"own"), b"own")
        guest.tick(session.CAS_SECONDS + 1)
        self.assertTrue(guest.session.stale)
        self.assertIsNone(guest.session.outbound(OTHER_MSG, b"own"))

    def test_create_a_sim_waits_for_the_save_that_leaves_the_zone(self):
        host, guest = joined()
        guest.session.client_cheat("sims.exit2cas 222 1 222")
        guest.game.leaving = True
        guest.tick(session.CAS_SECONDS + 1)
        self.assertFalse(guest.session.stale)

    def test_a_new_look_waits_for_the_link_and_goes_before_the_request(self):
        host, guest = joined()
        guest.game.looks[222] = b"before"
        guest.session.client_cheat("sims.exit2cas 222 1 222")
        guest.leave()
        guest.link(0)
        host.link(0)
        guest.game.looks[222] = b"after"
        guest.arrive()
        guest.take()
        host.link(1)
        guest.link(1)
        sent = []
        for _ in range(20):
            to_host = guest.take()
            sent += [frames.parse_json(p).get("type") for kind, _, p in to_host if kind == frames.CONTROL]
            host.receive(to_host)
            guest.receive(host.take())
        self.assertLess(sent.index("appearance"), sent.index("resync"))
        self.assertEqual(host.game.applied, [(222, b"after")])
        self.assertIsNone(guest.session._look)

    def test_a_save_loaded_from_create_a_sims_menu_sends_no_look(self):
        host, guest = joined()
        guest.game.looks[222] = b"before"
        guest.session.client_cheat("sims.exit2cas 222 1 222")
        guest.leave()
        guest.game.looks[222] = b"from disk"
        guest.game.by_hand = True
        guest.arrive()
        settle(host, guest)
        self.assertEqual(host.game.applied, [])

    def test_the_hosts_line_comes_back_to_the_guests_own_sim(self):
        host, guest = joined()
        host.game.sim_id = 111
        host.session.client_cheat("sims.exit2cas 222 1 111 outfitType 1")
        settle(host, guest)
        self.assertEqual(guest.game.cheats, ["sims.exit2cas 222 1 222 outfitType 1"])

    def test_create_a_sim_closed_without_a_change_sends_nothing(self):
        host, guest = joined()
        guest.game.looks[222] = b"same"
        guest.session.command("sims.modify_in_cas", ["222"])
        guest.leave()
        guest.arrive()
        settle(host, guest)
        self.assertEqual(host.game.applied, [])

    def test_create_a_sim_stays_shut_while_the_hosts_game_loads(self):
        host, guest = joined()
        host_reloads(host, guest)
        self.assertFalse(guest.session.command("sims.modify_in_cas", ["222"]))
        self.assertFalse(guest.session.client_cheat("sims.exit2cas 222 1 222"))
        self.assertTrue(any("Create-a-Sim" in notice for notice in guest.game.notices))

    def test_create_a_sim_that_never_opened_leaves_the_guest_joined(self):
        host, guest = joined()
        guest.session.command("sims.modify_in_cas", ["222"])
        guest.tick(session.CAS_SECONDS + 1)
        self.assertIsNone(guest.session.outbound(OTHER_MSG, b"own"))
        self.assertTrue(guest.session.live)

    def test_the_guests_sim_is_kept_off_a_host_lot_that_loads_again(self):
        host, guest = joined()
        guest.leave()
        settle(host, guest)
        guest.arrive(8)
        settle(host, guest)
        reloads_from_host(host, guest)
        self.assertEqual(host.game.sent_off, [[222]])
        # The host's lot loads again, with the guest's sim on it from disk.
        host.leave()
        host.arrive()
        settle(host, guest)
        self.assertEqual(host.game.sent_off[-1], [222])
        # A host started again learns of it from the guest, which says it
        # apart too.
        host = Player(session.HOST)
        host.game.zone = True
        host.session.bridge_up(session.HOST)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        both(host, guest)
        self.assertEqual(host.game.sent_off, [[222]])
        self.assertEqual(host.game.welcomed, [])
        both(host, guest, 5)
        self.assertEqual(host.game.sent_off, [[222]])

    def test_a_host_that_changed_its_game_while_the_guest_loaded_has_it_load_again(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        # Building while paused moves the game on without a clock.
        host.game.ticks += 1
        loads(host, guest)
        self.assertFalse(guest.session.live)
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_short_drop_while_the_guest_loads_keeps_the_hosts_hold(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        host.link(0)
        host.tick(20)
        host.link(1)
        host.tick(session.LINK_SECONDS)
        self.assertTrue(host.session.holding)

    def test_each_player_hears_when_the_other_builds(self):
        host, guest = joined()
        host.session.command("clock.build_buy_pause_unpause", ["1"])
        settle(host, guest)
        self.assertIn("the other player is in build mode", guest.game.notices)
        guest.session.command("clock.build_buy_pause_unpause", ["1"])
        settle(host, guest)
        self.assertTrue(any("both of you" in notice for notice in host.game.notices))
        self.assertTrue(any("both of you" in notice for notice in guest.game.notices))
        host.session.command("clock.build_buy_pause_unpause", ["0"])
        settle(host, guest)
        self.assertIn("the other player left build mode", guest.game.notices)
        # A guest that connects again hears it from a host still building.
        guest.session.command("clock.build_buy_pause_unpause", ["0"])
        host.session.command("clock.build_buy_pause_unpause", ["1"])
        settle(host, guest)
        for player in (host, guest):
            player.link(0)
        for player in (host, guest):
            player.link(1)
        settle(host, guest)
        self.assertTrue(guest.session.remote_building)


    def test_build_mode_ends_with_the_zone(self):
        host, guest = joined()
        guest.session.command("clock.build_buy_pause_unpause", ["1"])
        settle(host, guest)
        self.assertTrue(guest_of(host).building)
        guest.leave()
        settle(host, guest)
        self.assertFalse(guest_of(host).building)
        self.assertFalse(guest.session._building)

class HoldTest(unittest.TestCase):
    def test_the_host_stays_paused_until_the_guest_is_in(self):
        host, guest = connected()
        both(host, guest)
        self.assertTrue(host.session.holding)
        self.assertTrue(any("loading" in notice for notice in host.game.notices))
        # The host's player cannot start the clock meanwhile.
        self.assertFalse(host.session.command("clock.setspeed", ["1"]))
        self.assertFalse(host.session.command("clock.toggle_pause_unpause", []))
        self.assertTrue(host.session.command("interactions.select", ["1"]))
        pauses = host.game.pauses
        host.tick(session.PAUSE_SECONDS + 0.5)
        self.assertEqual(host.game.pauses, pauses + 1)
        answers(host, guest)
        loads(host, guest)
        self.assertTrue(host.session.live)
        host.tick()
        self.assertFalse(host.session.holding)
        self.assertTrue(host.session.command("clock.setspeed", ["1"]))

    def test_a_guest_sent_back_to_loading_that_then_gives_up_in_its_zone_ends_the_hold(self):
        host, guest = connected(cross=True)
        both(host, guest)
        answers(host, guest)
        host.game.ticks += 1
        loads(host, guest)
        # Sent back once as our game moved on; it hears of it, asks again
        # and is held for, then its client never travels.
        self.assertTrue(guest.session.stale)
        both(host, guest)
        self.assertTrue(host.session.holding)
        answers(host, guest)
        self.assertTrue(host.session.holding)
        guest.tick(session.TRAVEL_START_SECONDS + 1)
        settle(host, guest)
        self.assertFalse(guest.session.stale)
        host.tick()
        self.assertFalse(host.session.holding)

    def test_a_guest_sent_back_is_held_for_until_it_hears_of_it(self):
        host, guest = connected(cross=True)
        both(host, guest)
        answers(host, guest)
        host.game.ticks += 1
        # The guest's state that shows our world, before it heard that it
        # is to load again.
        guest.leave()
        settle(host, guest)
        guest.game.save = host.game.save
        guest.arrive()
        sent = guest.take()
        host.receive(sent)
        host.tick()
        self.assertTrue(host.session.holding)
        guest.receive(host.take())
        settle(host, guest)
        self.assertTrue(guest.session.stale)

    def test_the_host_does_not_wait_for_a_guest_that_shows_no_sign_of_life(self):
        host, guest = connected()
        both(host, guest)
        host.tick(session.HOLD_SECONDS + 1)
        self.assertFalse(host.session.holding)

    def test_the_host_waits_no_longer_than_the_link_grace_for_a_lost_guest(self):
        host, guest = connected()
        both(host, guest)
        host.link(0)
        host.tick(session.LINK_SECONDS + 1)
        self.assertFalse(host.session.holding)

    def test_the_guests_own_notice_reaches_it_while_it_is_held(self):
        host, guest = joined()
        guest.link(0)
        self.assertTrue(guest.session.stale)
        notice = b"\x0a" + guest.game.notices[-1].encode("utf-8")
        self.assertEqual(guest.session.outbound(NOTIFICATION, notice), notice)
        self.assertIsNone(guest.session.outbound(NOTIFICATION, notice))


    def test_the_host_is_not_held_for_a_guest_that_plays_another_lot(self):
        host, guest = joined()
        guest.leave()
        settle(host, guest)
        guest.arrive(8)
        settle(host, guest)
        both(host, guest)
        # Its state is saved and sent, but it will not join.
        self.assertEqual(host.game.snapshot_zones[-1], 8)
        self.assertFalse(host.session.holding)
        self.assertTrue(host.session.command("clock.setspeed", ["1"]))
        answers(host, guest)
        loads(host, guest)
        self.assertTrue(guest.session.apart)

    def test_the_host_is_not_held_once_its_save_failed(self):
        host, guest = connected()
        both(host, guest)
        host.game.snapshots[0](None)
        settle(host, guest)
        self.assertFalse(host.session.holding)
        # Every try of the guest's holds again, and only for that try. It
        # never loaded the host's state, so it gives nothing first.
        both(host, guest, session.RETRY_SECONDS[0] + 1)
        self.assertEqual(guest.game.contributions, [])
        self.assertTrue(host.session.holding)

    def test_a_guest_that_gets_in_after_the_hold_lapsed_loads_again(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        guest.leave()
        host.tick(session.HOLD_SECONDS + 1)
        self.assertFalse(host.session.holding)
        # The host's player plays on now; the guest's state is older.
        host.game.ticks += 1
        host.tick(session.PAUSE_SECONDS + 1)
        settle(host, guest)
        guest.game.save = host.game.save
        guest.arrive()
        settle(host, guest)
        self.assertFalse(guest.session.live)
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)
        self.assertTrue(host.session.live)


    def test_a_hold_that_runs_out_while_the_host_stood_still_keeps_its_world(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        guest.leave()
        host.tick(session.HOLD_SECONDS + 1)
        host.tick(session.PAUSE_SECONDS + 1)
        guest.game.save = host.game.save
        guest.arrive()
        settle(host, guest)
        self.assertTrue(guest.session.live)
        self.assertEqual(len(host.game.snapshots), 1)

    def test_a_save_taken_again_after_the_host_reloaded_holds_again(self):
        host, guest = connected()
        both(host, guest)
        host.leave()
        host.arrive()
        settle(host, guest)
        host.game.snapshots[0](b"old")
        settle(host, guest)
        self.assertEqual(len(host.game.snapshots), 2)
        self.assertTrue(host.session.holding)
        self.assertFalse(host.session.command("clock.setspeed", ["1"]))
        answers(host, guest, b"new")
        loads(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_save_taken_again_on_another_lot_does_not_join_the_guest(self):
        host, guest = connected()
        both(host, guest)
        host.leave()
        host.arrive(8)
        settle(host, guest)
        sent = []
        host.game.snapshots[0](b"save")
        sent = [frames.parse_json(p) for kind, _, p in host.sent if kind == frames.CONTROL]
        offers = [message for message in sent if message.get("type") == "offer"]
        self.assertTrue(all(not offer.get("world") for offer in offers))
        self.assertFalse(host.session.holding)

class GuestTest(unittest.TestCase):
    def test_local_simulation_is_muted(self):
        _, guest = joined()
        self.assertIsNone(guest.session.outbound(OTHER_MSG, b"x"))
        self.assertIsNone(guest.session.outbound(PIE_MENU, b"x"))
        self.assertIsNone(guest.session.outbound(VIEW_UPDATE, view_update(86, 87)))
        self.assertEqual(guest.take(), [])

    def test_local_selection_and_saving_still_reach_the_client(self):
        _, guest = joined()
        self.assertEqual(guest.session.outbound(SAVE_COMPLETE, b"x"), b"x")
        kept = guest.session.outbound(VIEW_UPDATE, view_update(86, SET_SIM_ACTIVE))
        self.assertEqual(op_types(kept), [SET_SIM_ACTIVE])

    def test_world_commands_are_forwarded_instead_of_run(self):
        _, guest = joined()
        for name in ("interactions.has_choices", "ui.dialog.respond", "clock.setspeed"):
            self.assertFalse(guest.session.command(name, ["1", 2]))
        self.assertEqual(
            [frames.parse_json(payload) for _, _, payload in guest.take()],
            [
                {"name": "interactions.has_choices", "args": ["1", "2"]},
                {"name": "ui.dialog.respond", "args": ["1", "2"]},
                {"name": "clock.setspeed", "args": ["1", "2"]},
            ],
        )

    def test_animation_and_pause_requests_are_dropped(self):
        _, guest = joined()
        self.assertFalse(guest.session.command("animation.route_complete", []))
        self.assertFalse(guest.session.command("clock.request_pause", ["modal"]))
        self.assertEqual(guest.take(), [])

    def test_other_commands_run_locally_and_are_logged_once(self):
        _, guest = joined()
        del guest.logged[:]
        self.assertTrue(guest.session.command("sims.set_active", ["5"]))
        self.assertTrue(guest.session.command("sims.set_active", ["6"]))
        self.assertEqual(guest.logged, ["local command while live: sims.set_active"])
        self.assertEqual(guest.take(), [])

    def test_a_guest_that_never_joined_plays_on_while_it_waits(self):
        host, guest = connected()
        both(host, guest)
        self.assertTrue(guest.session.stale)
        # Its game client shows its own simulation, not the host's.
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"local"), b"local")
        self.assertTrue(guest.session.command("interactions.select", ["1"]))
        host.game.snapshots[0](None)
        settle(host, guest)
        self.assertEqual(guest.game.reloads, 0)

    def test_a_joined_guest_whose_launcher_goes_shows_its_own_game_again(self):
        host, guest = joined()
        guest.session.bridge_down("closed")
        self.assertEqual(guest.game.reloads, 1)

    def test_messages_are_not_delivered_before_live(self):
        guest = Player(session.GUEST)
        guest.session.bridge_up(session.GUEST)
        guest.session.frame(frames.MSG, OTHER_MSG, b"x")
        self.assertEqual(guest.game.delivered, [])

    def test_a_guest_whose_own_zone_loads_again_loads_the_host_again(self):
        # Create-a-Sim on the guest, or the player loading the same save.
        host, guest = joined()
        guest.leave()
        guest.arrive()
        settle(host, guest)
        self.assertFalse(guest.session.live)
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_joined_guest_whose_launcher_returns_still_owes_the_hosts_copy(self):
        host, guest = joined()
        guest.session.bridge_down("launcher restarted")
        guest.leave()
        guest.arrive(8)
        guest.session.bridge_up(session.GUEST)
        guest.link(1)
        settle(host, guest)
        both(host, guest, session.GIVE_SECONDS + 1)
        # It loads the host's copy of its sims before it gives any.
        self.assertEqual(guest.game.contributions, [])
        self.assertGreaterEqual(len(host.game.snapshots), 2)

    def test_a_guest_whose_own_zone_loaded_again_plays_on_while_it_catches_up(self):
        host, guest = joined()
        guest.leave()
        guest.arrive()
        settle(host, guest)
        self.assertTrue(guest.session.stale)
        # Its client was just built from its own simulation.
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"local"), b"local")

    def test_a_save_loaded_while_no_launcher_is_there_is_noted(self):
        host, guest = joined()
        guest.session.bridge_down("closed")
        guest.game.by_hand = True
        guest.leave()
        guest.arrive()
        self.assertFalse(guest.game.by_hand)
        self.assertEqual(guest.game.put, 0)

    def test_a_guest_started_apart_from_its_own_save_loads_the_hosts_copy_before_giving(self):
        host, guest = joined()
        self.moves_to(host, guest, 8)
        guest.session.bridge_down("restart")
        guest.game.put = 0
        guest.session.bridge_up(session.GUEST)
        asked_before = len(host.game.snapshots)
        guest.link(1)
        settle(host, guest)
        self.assertEqual(len(host.game.snapshots), asked_before + 1)
        both(host, guest, session.GIVE_SECONDS + 1)
        self.assertEqual(guest.game.contributions, [])

    def moves_to(self, host, guest, lot):
        guest.leave()
        settle(host, guest)
        guest.arrive(lot)
        settle(host, guest)
        reloads_from_host(host, guest)

    def test_a_guest_game_that_restarts_loads_the_host_again(self):
        host, guest = joined()
        guest.session.bridge_down("restart")
        guest.session.bridge_up(session.GUEST)
        guest.link(1)
        settle(host, guest)
        self.assertFalse(host.session.live)
        reloads_from_host(host, guest)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)


class LotsTest(unittest.TestCase):
    def moves(self, player, other, lot):
        player.leave()
        settle(player, other)
        player.arrive(lot)
        settle(player, other)

    def test_players_on_different_lots_each_play_their_own(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        # It was muted until now, so it starts from what the host has.
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        self.assertEqual(host.game.snapshot_zones, [7, 8])
        self.assertEqual(guest.game.contributions, [])
        self.assertFalse(guest.session.live)
        self.assertFalse(host.session.live)
        self.assertTrue(guest.session.apart)
        self.assertTrue(host.session.apart)
        self.assertFalse(guest.session.stale)
        # Nothing is muted and nothing is sent to the other.
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"local"), b"local")
        self.assertTrue(guest.session.command("interactions.choices", ["1"]))
        host.take()
        self.assertEqual(host.session.outbound(OTHER_MSG, b"local"), b"local")
        self.assertEqual(host.take(), [])

    def test_a_guest_does_not_follow_a_host_that_travels(self):
        host, guest = joined()
        self.moves(host, guest, 8)
        # The lot the host played is loaded as the host left it.
        both(host, guest)
        self.assertEqual(host.game.snapshot_zones, [7, 7])
        answers(host, guest)
        guest.leave()
        guest.arrive()
        settle(host, guest)
        self.assertFalse(guest.session.stale)
        self.assertTrue(guest.session.apart)
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"local"), b"local")

    def test_a_guest_coming_to_the_hosts_lot_loads_it_from_the_host(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        self.moves(guest, host, 7)
        self.assertTrue(guest.session.stale)
        self.assertFalse(host.session.live)
        # What it did by itself goes to the host first.
        gives(host, guest)
        self.assertEqual(host.game.merged, [(b"household", 1)])
        self.assertEqual(asked(guest), [])
        reloads_from_host(host, guest)
        self.assertEqual(host.game.snapshot_zones, [7, 8, 7])
        self.assertTrue(guest.session.live)
        self.assertTrue(host.session.live)

    def test_a_joined_guest_that_gives_up_on_another_lot_tries_again_and_gives_nothing(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        both(host, guest)
        answers(host, guest, None)
        self.assertFalse(guest.session.stale)
        for _ in range(4):
            both(host, guest, session.RETRY_SECONDS[-1] + 1)
            answers(host, guest, None)
        self.assertEqual(guest.game.contributions, [])
        self.assertEqual(len(host.game.snapshots), 6)
        both(host, guest, session.RETRY_SECONDS[-1] + 1)
        answers(host, guest)
        loads(host, guest)
        self.assertTrue(guest.session.apart)
        guest.tick(session.GIVE_SECONDS + 1)
        guest.tick()
        self.assertEqual(len(guest.game.contributions), 1)

    def test_a_guest_playing_by_itself_gives_its_household_now_and_then(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        # The guest settled in for a while since it arrived.
        guest.tick(session.GIVE_SECONDS - session.SETTLE_SECONDS - 1)
        self.assertEqual(guest.game.contributions, [])
        guest.tick(2)
        self.assertEqual(len(guest.game.contributions), 1)
        guest.tick(session.GIVE_SECONDS)
        self.assertEqual(len(guest.game.contributions), 1)
        guest.game.contributions[0](b"household")
        settle(host, guest)
        self.assertEqual(host.game.merged, [(b"household", 1)])
        self.assertFalse(guest.session.stale)
        self.assertTrue(guest.session.apart)
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"local"), b"local")

    def test_an_answer_to_a_gift_does_not_end_the_giving_on_arrival(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        guest.tick(session.GIVE_SECONDS + 1)
        guest.game.contributions[0](b"older")
        to_host = guest.take()
        self.moves(guest, host, 7)
        guest.tick()
        self.assertEqual(len(guest.game.contributions), 2)
        # The host answers for the gift only now.
        host.receive(to_host)
        settle(host, guest)
        self.assertEqual(asked(guest), [])
        guest.game.contributions[1](b"newer")
        settle(host, guest)
        self.assertEqual([payload for payload, _ in host.game.merged], [b"older", b"newer"])

    def test_a_gift_saved_when_the_guest_comes_back_is_what_it_gives(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        guest.tick(session.GIVE_SECONDS + 1)
        self.moves(guest, host, 7)
        guest.tick()
        # One save at a time: the gift's, still running, is the newest.
        self.assertEqual(len(guest.game.contributions), 1)
        guest.game.contributions[0](b"household")
        settle(host, guest)
        self.assertEqual([payload for payload, _ in host.game.merged], [b"household"])
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_gift_the_host_gave_up_on_does_not_stop_later_ones(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        guest.tick(session.GIVE_SECONDS + 1)
        guest.game.contributions[0](b"x" * (transfer.WINDOW * 2))
        guest.take()
        guest.tick(session.STALL_SECONDS + 1)
        guest.tick(session.GIVE_SECONDS)
        self.assertEqual(len(guest.game.contributions), 2)

    def test_an_older_gift_on_its_way_is_dropped_for_the_arrival_one(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        guest.tick(session.GIVE_SECONDS + 1)
        guest.game.contributions[0](b"o" * (transfer.WINDOW * 2))
        guest.take()
        self.moves(guest, host, 7)
        guest.tick()
        guest.game.contributions[-1](b"arrival")
        settle(host, guest)
        self.assertEqual([payload for payload, _ in host.game.merged], [b"arrival"])
        self.assertIsNone(guest.session._transfers.outgoing(guest.session._gift or -1))

    def test_a_guest_that_played_alone_keeps_its_play_when_the_host_cannot_take_it(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        host.game.merge_error = ValueError("busy")
        host.game.merge_error_stays = False
        self.moves(guest, host, 7)
        gives(host, guest)
        # Not the host's state, which would undo what it played.
        self.assertFalse(guest.session.stale)
        self.assertEqual(guest.game.adopted, [b"save", b"save"])
        both(host, guest, session.RETRY_SECONDS[0] + 1)
        gives(host, guest)
        self.assertEqual(len(host.game.merged), 1)

    def test_a_guest_owing_the_hosts_copy_asks_for_it_on_request(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        guest.session._owes = True
        guest.session._alone = False
        both(host, guest)
        both(host, guest, session.SAVE_SECONDS + 1)
        self.assertTrue(guest.session.resync())
        both(host, guest)
        self.assertEqual(guest.game.contributions, [])

    def test_a_guest_travelling_to_the_hosts_lot_loads_it_once(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        loads_before = len(guest.game.adopted)
        # The guest's game orders its client to travel to the host's lot.
        order = travel_to(7, 222, 333)
        self.assertIsNone(guest.session.outbound(TRAVEL, order))
        self.assertTrue(guest.session.stale)
        settle(host, guest)
        # Played by itself, so its household goes first; then the state of
        # the lot it travels to.
        guest.game.contributions[-1](b"household")
        settle(host, guest)
        self.assertEqual(host.game.snapshot_zones[-1], 7)
        self.assertTrue(host.session.holding)
        answers(host, guest)
        self.assertEqual(len(guest.game.adopted), loads_before + 1)
        self.assertEqual(guest.game.travellers[-1], [222, 333])
        # One loading screen: from lot 8 straight into the host's lot.
        guest.leave()
        guest.arrive(7)
        settle(host, guest)
        self.assertTrue(guest.session.live)
        self.assertTrue(host.session.live)

    def test_a_held_travel_goes_when_the_launcher_goes(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        order = travel_to(7, 222)
        self.assertIsNone(guest.session.outbound(TRAVEL, order))
        guest.session.bridge_down("closed")
        self.assertEqual(guest.game.delivered[-1], (TRAVEL, order))
        self.assertIsNone(guest.session._travel)

    def test_a_held_travel_waits_little_for_a_host_that_left_its_lot(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        guest.session._alone = False
        order = travel_to(7, 222)
        guest.session.outbound(TRAVEL, order)
        settle(host, guest)
        host.leave()
        settle(host, guest)
        guest.tick(session.TRAVEL_WAIT_SECONDS + 1)
        guest.tick(1)
        self.assertEqual(guest.game.delivered[-1], (TRAVEL, order))

    def test_the_host_has_the_sims_that_travel_with_the_guest(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        guest.session._alone = False
        guest.session.outbound(TRAVEL, travel_to(7, 222, 333))
        settle(host, guest)
        answers(host, guest)
        guest.leave()
        guest.arrive(7)
        settle(host, guest)
        self.assertTrue(guest.session.live)
        self.assertEqual(host.game.welcome_travellers[-1], [222, 333])

    def test_a_travel_elsewhere_goes_as_it_is(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        order = travel_to(9, 222)
        self.assertEqual(guest.session.outbound(TRAVEL, order), order)
        self.assertFalse(guest.session.stale)

    def test_a_held_travel_goes_as_it_was_ordered_when_the_host_cannot_save(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        guest.session._alone = False
        order = travel_to(7, 222)
        self.assertIsNone(guest.session.outbound(TRAVEL, order))
        settle(host, guest)
        answers(host, guest, None)
        self.assertFalse(guest.session.stale)
        self.assertEqual(guest.game.delivered[-1], (TRAVEL, order))

    def test_a_guest_playing_by_itself_can_ask_to_reload(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.resync())
        gives(host, guest)
        reloads_from_host(host, guest)
        self.assertEqual(host.game.merged, [(b"household", 1)])
        self.assertEqual(host.game.snapshot_zones, [7, 8, 8])
        self.assertTrue(guest.session.apart)

    def test_starting_on_different_lots(self):
        host, guest = Player(session.HOST), Player(session.GUEST)
        guest.game.zone_number = 8
        host.session.bridge_up(session.HOST)
        guest.session.bridge_up(session.GUEST)
        host.link(1)
        guest.link(1)
        host.game.zone = guest.game.zone = True
        both(host, guest)
        # Its save is older than the host's game: it loads the host's copy
        # of its lot first.
        self.assertTrue(guest.session.stale)
        self.assertFalse(host.session.holding)
        answers(host, guest)
        loads(host, guest)
        self.assertFalse(guest.session.live)
        self.assertFalse(guest.session.stale)
        self.assertTrue(guest.session.apart)


class HouseholdTest(unittest.TestCase):
    def test_games_in_different_saves_are_not_joined(self):
        host, guest = strangers()
        self.assertFalse(host.session.live)
        self.assertFalse(guest.session.live)
        self.assertTrue(guest.session.stale)
        self.assertEqual(host.game.snapshots, [])

    def test_the_guest_brings_its_household_into_the_hosts_save(self):
        host, guest = strangers()
        gives(host, guest)
        self.assertEqual(host.game.merged, [(b"household", 2)])
        reloads_from_host(host, guest)
        self.assertEqual(host.game.snapshot_zones, [7])
        self.assertEqual(guest.game.adopted, [b"save"])
        self.assertTrue(guest.session.live)
        self.assertTrue(host.session.live)
        self.assertEqual(host.game.welcomed, [222])

    def test_a_large_household_crosses_in_parts(self):
        host, guest = strangers()
        big = bytes(range(256)) * 20000
        gives(host, guest, big)
        self.assertEqual(host.game.merged, [(big, 2)])

    def test_each_plays_their_own_save_when_the_host_refuses_for_good(self):
        host, guest = strangers()
        host.game.merge_error = Refused("no", final=True)
        gives(host, guest)
        self.assertFalse(guest.session.stale)
        self.assertFalse(guest.session.live)
        self.assertFalse(host.session.live)
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"local"), b"local")
        self.assertTrue(guest.session.command("interactions.choices", ["1"]))
        # Not tried again and again.
        for _ in range(5):
            both(host, guest, session.RETRY_SECONDS[-1] + 1)
        self.assertEqual(len(guest.game.contributions), 1)
        self.assertEqual(host.game.snapshots, [])

    def test_a_refusal_that_may_pass_is_tried_again_later(self):
        host, guest = strangers()
        host.game.merge_error = ValueError("the host is busy")
        gives(host, guest)
        self.assertFalse(guest.session.stale)
        host.game.merge_error = None
        both(host, guest, session.RETRY_SECONDS[0] - 1)
        self.assertEqual(len(guest.game.contributions), 1)
        both(host, guest, 2)
        self.assertEqual(len(guest.game.contributions), 2)
        guest.game.contributions[1](b"household")
        settle(host, guest)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_host_outside_its_zone_has_the_household_given_again(self):
        host, guest = strangers()
        both(host, guest)
        guest.game.contributions[0](b"household")
        to_host = guest.take()
        host.game.zone = False
        host.tick()
        host.take()
        host.receive(to_host)
        settle(host, guest)
        self.assertEqual(host.game.merged, [])
        host.arrive()
        settle(host, guest)
        both(host, guest, session.RETRY_SECONDS[0] + 1)
        self.assertEqual(len(guest.game.contributions), 2)

    def test_a_save_loaded_by_hand_is_brought_over_again(self):
        host, guest = strangers()
        host.game.merge_error = Refused("no", final=True)
        gives(host, guest)
        self.assertEqual(len(guest.game.contributions), 1)
        host.game.merge_error = None
        # The player goes to the menu and loads their save again.
        guest.game.by_hand = True
        guest.leave()
        guest.arrive()
        settle(host, guest)
        guest.tick()
        self.assertEqual(len(guest.game.contributions), 2)

    def test_each_plays_their_own_save_when_the_guest_cannot_save(self):
        host, guest = strangers()
        gives(host, guest, None)
        self.assertEqual(host.game.merged, [])
        self.assertFalse(guest.session.stale)
        self.assertFalse(guest.session.live)

    def test_each_plays_their_own_save_when_the_host_never_answers(self):
        host, guest = strangers()
        both(host, guest)
        guest.game.contributions[0](b"household")
        guest.take()
        guest.tick(session.STALL_SECONDS - 1)
        self.assertTrue(guest.session.stale)
        guest.tick(2)
        self.assertFalse(guest.session.stale)
        self.assertFalse(guest.session.live)
        self.assertEqual(asked(guest), [])

    def test_a_long_upload_leaves_the_host_time_to_answer(self):
        host, guest = strangers()
        both(host, guest)
        guest.game.contributions[0](b"x" * (8 * 1024 * 1024))
        for _ in range(100):
            to_host = guest.take()
            if not to_host:
                break
            guest.tick(session.STALL_SECONDS - 1)
            host.receive(to_host)
            guest.receive([frame for frame in host.take() if b'"merged"' not in frame[2]])
        self.assertGreater(guest.now, 1000 + session.MERGE_SECONDS)
        guest.tick(session.MERGE_SECONDS - 1)
        self.assertTrue(guest.session.stale)
        guest.tick(2)
        self.assertFalse(guest.session.stale)

    def test_a_guest_whose_save_never_ends_gives_up(self):
        host, guest = strangers()
        both(host, guest)
        guest.tick(session.SAVE_SECONDS + 1)
        self.assertFalse(guest.session.stale)

    def test_a_host_outside_a_zone_does_not_take_a_household(self):
        host = Player(session.HOST)
        host.session.bridge_up(session.HOST)
        host.take()
        host.control("offer", id=3, what="household", size=0, save="2")
        self.assertEqual(host.game.merged, [])
        self.assertEqual(host.controls()[-1], {"type": "merged", "id": 3, "ok": False, "final": False})

    def test_a_guest_does_not_take_households(self):
        host, guest = joined()
        guest.control("offer", id=3, what="household", size=0)
        self.assertEqual(guest.game.merged, [])
        self.assertEqual(guest.controls(), [{"type": "drop", "id": 3}])

    def test_a_dialog_is_shown_to_the_player_of_its_sim(self):
        host, guest = joined()
        host.game.sim_id = 111
        theirs, ours, nobody = owned(222), owned(111), owned(0)

        self.assertIsNone(host.session.outbound(DIALOG, theirs))
        self.assertEqual(host.session.outbound(DIALOG, ours), ours)
        self.assertEqual(host.session.outbound(DIALOG, nobody), nobody)
        sent = [frame[2] for frame in host.take() if frame[0] == frames.MSG]
        self.assertEqual(sent, [theirs, nobody])

    def test_a_dialog_of_the_guests_household_is_the_guests(self):
        host, guest = joined()
        host.game.sim_id = 111
        host.game.households = {222: 2, 223: 2}
        relative, stranger = owned(223), owned(333)

        self.assertIsNone(host.session.outbound(DIALOG, relative))
        self.assertEqual(host.session.outbound(DIALOG, stranger), stranger)
        # In one shared household only the sim the guest controls is theirs.
        host.game.households = {}
        self.assertEqual(host.session.outbound(DIALOG, relative), relative)

    def test_the_host_sees_notifications_that_are_not_the_guests(self):
        host, guest = joined()
        host.game.sim_id = 111
        theirs, ours = owned(222), owned(111)

        self.assertIsNone(host.session.outbound(NOTIFICATION, theirs))
        self.assertEqual(host.session.outbound(NOTIFICATION, ours), ours)
        sent = [frame[2] for frame in host.take() if frame[0] == frames.MSG]
        self.assertEqual(sent, [theirs, ours])


class ResyncTest(unittest.TestCase):
    def test_a_guest_does_not_rejoin_a_host_that_reloaded(self):
        host, guest = joined()
        host_reloads(host, guest)
        self.assertTrue(guest.session.stale)
        self.assertFalse(guest.session.live)
        self.assertFalse(host.session.live)
        # Its own simulation is not shown in the meantime.
        self.assertIsNone(guest.session.outbound(OTHER_MSG, b"local"))
        self.assertFalse(guest.session.command("interactions.choices", ["1"]))
        self.assertFalse(guest.session.command("clock.pause", []))
        self.assertTrue(guest.session.command("ui.something_else", []))

    def test_the_guest_loads_what_the_host_saves(self):
        host, guest = joined()
        host_reloads(host, guest)
        self.assertEqual(len(host.game.snapshots), 2)
        host.game.snapshots[-1](b"the host's save")
        settle(host, guest)
        self.assertEqual(guest.game.adopted[-1], b"the host's save")
        # What its simulation sends while reloading reaches its game client.
        self.assertEqual(guest.session.outbound(TRAVEL, b"go"), b"go")
        self.assertFalse(guest.session.live)

        guest.leave()
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"loading"), b"loading")
        guest.arrive()
        settle(host, guest)
        self.assertFalse(guest.session.stale)
        self.assertTrue(guest.session.live)
        self.assertTrue(host.session.live)
        self.assertEqual(guest_of(host).sim_id, 222)

    def test_asks_only_once(self):
        host, guest = joined()
        host_reloads(host, guest)
        for _ in range(5):
            both(host, guest, 1)
        self.assertEqual(len(host.game.snapshots), 2)

    def test_a_slow_transfer_is_waited_for_as_long_as_it_moves(self):
        host, guest = joined()
        host_reloads(host, guest)
        big = bytes(range(256)) * 40000
        host.game.snapshots[-1](big)
        for _ in range(200):
            to_guest = host.take()
            if not to_guest:
                break
            guest.receive(to_guest)
            guest.tick(session.STALL_SECONDS - 1)
            host.receive(guest.take())
        self.assertGreater(guest.now, 1000 + session.SAVE_SECONDS)
        self.assertEqual(guest.game.adopted[-1], big)

    def test_a_transfer_that_stops_is_given_up(self):
        host, guest = joined()
        host_reloads(host, guest)
        host.game.snapshots[-1](bytes(range(256)) * 40000)
        guest.receive(host.take()[:3])
        guest.tick(session.STALL_SECONDS + 1)
        self.assertFalse(guest.session.stale)
        self.assertEqual(guest.game.adopted, [b"save"])

    def test_plays_on_by_itself_when_the_host_cannot_save_and_tries_again(self):
        host, guest = joined()
        host_reloads(host, guest)
        host.game.snapshots[-1](None)
        settle(host, guest)
        self.assertFalse(guest.session.stale)
        self.assertFalse(guest.session.live)
        self.assertEqual(guest.game.reloads, 1)
        self.assertEqual(guest.session.outbound(OTHER_MSG, b"local"), b"local")
        # What the host has of our household is newer than what we played
        # meanwhile: it is not given, the host's state is loaded again.
        reloads_from_host(host, guest)
        self.assertEqual(guest.game.contributions, [])
        both(host, guest, session.RETRY_SECONDS[0] + 1)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)
        self.assertTrue(host.session.live)

    def test_gives_up_when_the_host_never_answers(self):
        host, guest = joined()
        host_reloads(host, guest)
        guest.tick(session.SAVE_SECONDS + 1)
        self.assertFalse(guest.session.stale)
        self.assertFalse(guest.session.live)

    def test_gives_up_when_the_save_cannot_be_loaded(self):
        host, guest = joined()
        guest.game.adopt_error = ValueError("damaged")
        host_reloads(host, guest)
        answers(host, guest, b"x")
        self.assertFalse(guest.session.stale)
        self.assertFalse(guest.session.live)
        self.assertTrue(any("damaged" in line for line in guest.logged))

    def test_gives_up_when_the_game_does_not_reload(self):
        for cross in (False, True):
            host, guest = joined(cross)
            host_reloads(host, guest)
            answers(host, guest, b"x")
            # It began to save to leave its zone, and never got further.
            guest.game.leaving = True
            guest.tick(session.RELOAD_SECONDS - 1)
            self.assertTrue(guest.session.stale)
            guest.tick(2)
            self.assertFalse(guest.session.stale)
            # With one family the snapshot runs out by itself, as it
            # always did.
            self.assertEqual(guest.game.cancels, 1 if cross else 0)

    def test_with_one_family_a_game_that_never_starts_to_travel_is_waited_for_as_before(self):
        host, guest = joined()
        host_reloads(host, guest)
        answers(host, guest, b"x")
        guest.tick(session.TRAVEL_START_SECONDS + 1)
        self.assertTrue(guest.session.stale)
        guest.tick(session.RELOAD_SECONDS)
        self.assertFalse(guest.session.stale)
        self.assertIn("the game did not load the host's state; playing on by ourselves", guest.game.notices)

    def test_gives_up_soon_when_the_game_never_starts_to_travel(self):
        host, guest = joined(cross=True)
        host_reloads(host, guest)
        answers(host, guest, b"x")
        put = guest.game.put
        guest.tick(session.TRAVEL_START_SECONDS - 1)
        self.assertTrue(guest.session.stale)
        self.assertEqual(guest.game.cancels, 0)
        guest.tick(2)
        self.assertFalse(guest.session.stale)
        self.assertEqual(guest.game.cancels, 1)
        self.assertEqual(guest.game.put, put - 1)
        self.assertIn("the game did not start loading the host's state; playing on by ourselves", guest.game.notices)
        # It tries again later, as after any other failure.
        guest.tick(session.RETRY_SECONDS[0] + 1)
        self.assertTrue(guest.session.stale)

    def test_the_host_state_is_loaded_once_the_guest_has_settled_in(self):
        host, guest = joined(cross=True)
        # Its own zone loads again and has only just come out of the
        # loading screen; it asks for the host's state at once.
        guest.leave()
        settle(host, guest)
        guest.game.zone = True
        guest.tick()
        settle(host, guest)
        self.assertEqual(len(host.game.snapshots), 2)
        adopted = len(guest.game.adopted)
        answers(host, guest, b"x")
        self.assertEqual(len(guest.game.adopted), adopted)
        guest.tick(session.SETTLE_SECONDS + 0.5)
        self.assertEqual(guest.game.adopted[-1], b"x")

    def test_with_one_family_the_host_state_is_loaded_at_once_as_before(self):
        host, guest = joined()
        guest.leave()
        settle(host, guest)
        guest.game.zone = True
        guest.tick()
        settle(host, guest)
        answers(host, guest, b"x")
        self.assertEqual(guest.game.adopted[-1], b"x")

    def test_a_guest_without_a_sim_waits_for_one_before_it_loads(self):
        host, guest = joined(cross=True)
        guest.game.sim_id = 0
        host_reloads(host, guest)
        both(host, guest)
        adopted = len(guest.game.adopted)
        answers(host, guest, b"x")
        guest.tick(session.SETTLE_SECONDS - 1)
        self.assertEqual(len(guest.game.adopted), adopted)
        guest.game.sim_id = 222
        guest.tick(0.5)
        self.assertEqual(guest.game.adopted[-1], b"x")

    def test_a_game_that_loads_its_own_state_instead_is_not_joined(self):
        host, guest = joined()
        guest.game.loads_its_own = True
        host_reloads(host, guest)
        answers(host, guest)
        loads(host, guest)
        self.assertFalse(guest.session.live)
        self.assertFalse(guest.session.stale)
        self.assertFalse(host.session.live)

    def test_a_long_loading_screen_is_waited_for(self):
        host, guest = joined()
        host_reloads(host, guest)
        answers(host, guest)
        guest.leave()
        guest.tick(session.RELOAD_SECONDS * 5)
        guest.arrive()
        settle(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_host_that_reloads_while_the_guest_loads_is_loaded_again(self):
        host, guest = joined()
        host_reloads(host, guest)
        answers(host, guest)
        host_reloads(host, guest)
        loads(host, guest)
        self.assertFalse(guest.session.live)
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_leaving_the_zone_by_hand_is_a_fresh_start(self):
        host, guest = joined()
        host_reloads(host, guest)
        guest.leave()
        self.assertFalse(guest.session.stale)

    def test_a_guest_can_ask_to_reload(self):
        host, guest = joined()
        self.assertFalse(host.session.resync())
        self.assertTrue(guest.session.resync())
        settle(host, guest)
        self.assertFalse(host.session.live)
        self.assertEqual(len(host.game.snapshots), 2)

    def test_a_snapshot_is_refused_by_a_guest_that_did_not_ask(self):
        host, guest = joined()
        guest.control("offer", id=99, what="state", size=1, world="x")
        self.assertEqual(guest.controls(), [{"type": "drop", "id": 99}])
        guest.session.frame(frames.PART, 99, b"\0\0\0\0x")
        self.assertEqual(guest.game.adopted, [b"save"])

    def test_a_late_answer_to_an_earlier_request_is_ignored(self):
        host, guest = joined()
        host_reloads(host, guest)
        guest.tick(session.SAVE_SECONDS + 1)
        both(host, guest, session.RETRY_SECONDS[0] + 1)
        # The first request's save ends only now.
        host.game.snapshots[1](b"old")
        settle(host, guest)
        self.assertEqual(guest.game.adopted, [b"save"])
        host.game.snapshots[-1](b"new")
        settle(host, guest)
        self.assertEqual(guest.game.adopted, [b"save", b"new"])

    def test_a_guest_asking_again_is_saved_for_again_once_the_save_under_way_ends(self):
        host, guest = connected()
        both(host, guest)
        host.control("resync", id=50, zone="7")
        # What was saved may be older than what it asks after, so it is
        # saved again.
        host.game.snapshots[0](b"save")
        self.assertEqual(len(host.game.snapshots), 2)
        host.game.snapshots[1](b"save")
        offers = [message for message in host.controls() if message["type"] == "offer"]
        self.assertEqual([offer["id"] for offer in offers], [50])


def cross_joined():
    """A guest with a family of its own, from a save of its own, brought
    into the host's save and joined to it."""
    host, guest = strangers()
    gives(host, guest)
    reloads_from_host(host, guest)
    return host, guest


def view_update_of(*operations):
    """A ViewUpdate with one entry holding the operations (type, data)."""
    ops = bytearray()
    for op_type, data in operations:
        operation = bytearray()
        wire.put_field(operation, 1, wire.VARINT, op_type)
        wire.put_field(operation, 2, wire.BYTES, data)
        wire.put_field(ops, 1, wire.BYTES, bytes(operation))
    entry = bytearray()
    wire.put_field(entry, 1, wire.BYTES, b"\x0a\x04\x08\x00\x10\x00")
    wire.put_field(entry, 2, wire.BYTES, bytes(ops))
    update = bytearray()
    wire.put_field(update, 1, wire.BYTES, bytes(entry))
    return bytes(update)


def selectable(*sim_ids):
    """An UpdateSelectableSims listing those sims."""
    out = bytearray()
    for sim_id in sim_ids:
        sim = bytearray()
        wire.put_field(sim, 1, wire.VARINT, sim_id)
        wire.put_field(sim, 5, wire.VARINT, 1)
        wire.put_field(out, 1, wire.BYTES, bytes(sim))
    return bytes(out)


def listed(update):
    """The sims of each SELECTABLE operation of a view update."""
    return [[wire.first_varint(sim, 1) for number, _, sim in wire.fields(data) if number == 1]
            for _, _, op_type, data in wire.view_update_ops(update) if op_type == SELECTABLE]


class LandingTest(unittest.TestCase):
    """What a guest finds once it loaded the host's state: the lot and the
    household it was for, or not."""

    def test_a_load_that_lands_on_another_lot_joins_nothing(self):
        host, guest = connected(cross=True)
        both(host, guest)
        answers(host, guest)
        # Our game moved on meanwhile: a guest that showed the state would
        # be sent back to loading.
        host.game.ticks += 1
        # The player left through the menu and loaded lot 9; the host's
        # state, for lot 7, was put in place all the same.
        guest.game.landed = 7
        guest.leave()
        guest.game.save = host.game.save
        guest.arrive(9)
        settle(host, guest)
        self.assertIn("the game loaded another lot than the host's state was for", guest.logged)
        self.assertNotIn("loaded the host's state", guest.logged)
        self.assertFalse(guest.session.live)
        self.assertEqual(guest.session._world, "")
        self.assertEqual(guest_of(host).world, "")
        self.assertFalse(any("moved on" in line for line in host.logged))
        # It plays apart, and the host is not held for it.
        host.tick()
        self.assertFalse(host.session.holding)

    def test_a_load_on_the_lot_it_was_for_joins(self):
        host, guest = connected(cross=True)
        both(host, guest)
        answers(host, guest)
        self.assertEqual(guest.game.joins, [True])
        guest.game.landed = 7
        loads(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_load_is_matched_to_our_zone_by_our_own_id_when_the_lot_is_unknown(self):
        host, guest = connected(cross=True)
        # The host names our zone 7 otherwise: the snapshot was renamed to
        # our zones before it was put in place, so our id is what counts.
        guest.game.host_zone = lambda zone_id: 0x77
        both(host, guest)
        answers(host, guest)
        guest.game.landed = 7
        loads(host, guest)
        self.assertNotIn("the game loaded another lot than the host's state was for", guest.logged)
        self.assertTrue(guest.session.live)

    def test_with_one_family_where_a_load_lands_is_not_checked(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        guest.game.landed = 9
        loads(host, guest)
        self.assertNotIn("the game loaded another lot than the host's state was for", guest.logged)
        self.assertTrue(guest.session.live)

    def test_a_guest_whose_game_plays_another_household_after_loading_does_not_join(self):
        host, guest = strangers()
        gives(host, guest)
        both(host, guest)
        answers(host, guest)
        # The host's save makes its own household the one played.
        guest.game.household = 6
        loads(host, guest)
        self.assertIn("this game now plays another household; switch back to yours", guest.game.notices)
        self.assertFalse(guest.session.live)
        self.assertFalse(host.session.live)
        self.assertEqual(host.game.welcomed, [])
        contributions = len(guest.game.contributions)
        guest.tick(1)
        settle(host, guest)
        self.assertEqual(len(guest.game.contributions), contributions)
        # Back to its own household, the guest loads the host's state again
        # after a while, and joins.
        guest.game.household = 5
        guest.tick(session.RETRY_SECONDS[0] + 1)
        settle(host, guest)
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_with_one_family_the_household_played_after_loading_is_not_checked(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        guest.game.household = 6
        loads(host, guest)
        self.assertTrue(guest.session.live)

    def test_a_cross_save_guest_restarted_on_its_own_home_plays_it(self):
        host, guest = self.restarted_at_home(cross=True)
        self.assertIn("on our own home with the host elsewhere; playing it by ourselves", guest.logged)
        self.assertFalse(guest.session.stale)
        self.assertTrue(guest.session.apart)
        # The host hears that it plays a family of its own.
        self.assertTrue(guest_of(host).cross)
        for _ in range(20):
            both(host, guest, 1)
        self.assertEqual(guest.logged.count("on our own home with the host elsewhere; playing it by ourselves"), 1)
        self.assertEqual(host.game.snapshots, [])
        self.assertEqual(guest.game.adopted, [])
        # What it plays there goes to the host now and then, as on any lot
        # it plays by itself.
        self.assertEqual(guest.game.contributions, [])
        both(host, guest, session.GIVE_SECONDS)
        self.assertEqual(len(guest.game.contributions), 1)

    def test_a_guest_with_one_family_restarted_apart_loads_the_hosts_copy(self):
        host, guest = self.restarted_at_home(cross=False)
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        self.assertEqual(len(guest.game.adopted), 1)

    @staticmethod
    def restarted_at_home(cross):
        """A guest whose game started again from the save that holds the
        host's world, on the lot of its own household's home, the host on
        another."""
        host, guest = Player(session.HOST), Player(session.GUEST)
        host.game.zone = guest.game.zone = True
        host.game.lot_name = "100:0"
        guest.game.sim_id = 222
        guest.game.zone_number = 9
        guest.game.lot_name = guest.game.home = "900:0"
        # A game started again knows nothing of the host's names for its
        # zones; resync's record of saves the host's save was put in place
        # in from another (resync.CrossSaves) says it plays such a save.
        if cross:
            guest.game.crossed[guest.game.save] = guest.game.household
        host.session.bridge_up(session.HOST)
        guest.session.bridge_up(session.GUEST)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        return host, guest

    def test_the_guest_tells_the_host_it_plays_a_family_of_its_own(self):
        host, guest = cross_joined()
        self.assertTrue(guest.session.cross_save)
        self.assertTrue(guest.session._state_fields().get("cross"))
        self.assertTrue(guest_of(host).cross)
        self.assertTrue(host.session.cross_save)
        # The host never hands it a sim of its own household.
        self.assertEqual(host.game.welcome_households, [5])

    def test_one_family_says_nothing_of_it(self):
        host, guest = joined()
        self.assertFalse(guest.session.cross_save)
        self.assertNotIn("cross", guest.session._state_fields())
        self.assertFalse(guest_of(host).cross)
        self.assertEqual(host.game.welcome_households, [0])


class TravelTest(unittest.TestCase):
    """A guest's sim that the host's simulation sends to another lot."""

    def sends_home(self, host, guest, zone=9, lot="900:0"):
        host.game.lot_names[zone] = lot
        results = []
        host.game.during_execute = lambda: results.append(host.session.outbound(TRAVEL, travel_to(zone, 222)))
        self.assertFalse(guest.session.command("interactions.push", ["1"]))
        settle(host, guest)
        host.game.during_execute = None
        return results

    def test_a_guest_sent_home_travels_and_the_host_stays(self):
        host, guest = cross_joined()
        self.assertEqual(self.sends_home(host, guest), [None])
        self.assertNotIn(TRAVEL, [msg_id for msg_id, _ in guest.game.delivered])
        self.assertIn([222], host.game.sent_off)
        # Sims of our household among them would stay: the guest plays a
        # family of its own.
        self.assertEqual(host.game.sent_off_households[-1], guest_of(host).household)
        self.assertTrue(guest_of(host).household)
        # The guest asks for the host's state of that lot, to travel into
        # with its sim.
        self.assertTrue(guest.session.stale)
        self.assertEqual(host.game.snapshot_places[-1][0][:2], (9, "900:0"))
        # A guest from a save of its own: the home of its household is not
        # sent with the host's objects of it.
        self.assertTrue(host.game.snapshot_places[-1][0][3])
        self.assertEqual(guest_of(host).travellers, [222])
        self.assertFalse(host.session.holding)
        answers(host, guest)
        self.assertEqual(guest.game.travellers[-1], [222])
        # Not to join the host, which is elsewhere: what is there of ours
        # stays ours.
        self.assertEqual(guest.game.joins[-1], False)
        guest.leave()
        guest.arrive(9)
        settle(host, guest)
        self.assertFalse(guest.session.stale)
        self.assertTrue(guest.session.apart)
        self.assertEqual(host.game.zone_number, 7)

    def test_a_guest_sent_home_whose_host_cannot_answer_travels_by_itself(self):
        host, guest = cross_joined()
        guest.game.our_zones["900:0"] = 19
        self.sends_home(host, guest)
        host.game.snapshots[-1](None)
        settle(host, guest)
        self.assertEqual(guest.game.travels, [(19, [222])])

    def test_the_hosts_own_travel_goes_as_it_was(self):
        host, guest = cross_joined()
        host.game.sim_id = 111
        order = travel_to(9, 111)
        self.assertEqual(host.session.outbound(TRAVEL, order), order)
        self.assertTrue(any("our game travels to 0x9" in line for line in host.logged))
        # Our game paused itself for its own loading screen.
        self.assertEqual(host.game.resumed, [])

    def test_a_guest_sent_home_leaves_the_hosts_game_at_the_speed_it_had(self):
        host, guest = cross_joined()
        host.game.speed = 2
        host.tick()
        # The game pauses as it orders the travel, which our client never
        # gets.
        host.game.speed = 0
        self.assertEqual(self.sends_home(host, guest), [None])
        self.assertEqual(host.game.resumed, [2])

    def test_a_guest_sent_home_while_it_gets_in_step_travels_once_it_is(self):
        host, guest = cross_joined()
        host_reloads(host, guest)
        self.assertTrue(guest.session.stale)
        # The host had sent our sim home already.
        guest.control("travel", zone="9", lot="900:0", sims=["222"])
        self.assertIn("the host has our sim travel while we are getting in step; travelling once we are",
                      guest.logged)
        reloads_from_host(host, guest)
        both(host, guest)
        self.assertIn("travelling to 0x9 (lot 900:0), as our sim was sent there", guest.logged)
        self.assertTrue(guest.session.stale)
        self.assertEqual(host.game.snapshot_places[-1][0][:2], (9, "900:0"))

    def test_a_guest_sent_to_a_lot_it_lacks_loads_the_hosts_state(self):
        host, guest = cross_joined()
        guest.game.our_zones["900:0"] = 0
        snapshots = len(host.game.snapshots)
        guest.control("travel", zone="9", lot="900:0", sims=["222"])
        settle(host, guest)
        self.assertIn("the host sent our sim to a lot our game does not have; loading the host's state instead",
                      guest.game.notices)
        self.assertTrue(guest.session.stale)
        self.assertEqual(len(host.game.snapshots), snapshots + 1)

    def test_the_host_is_told_once_that_a_home_from_another_save_has_its_walls_elsewhere(self):
        host, guest = cross_joined()
        host.game.sim_id = 111
        host.game.foreign_homes = {9}
        order = travel_to(9, 111)
        for _ in range(2):
            self.assertEqual(host.session.outbound(TRAVEL, order), order)
        told = [notice for notice in host.game.notices if "home of a family from another save" in notice]
        self.assertEqual(len(told), 1)
        self.assertEqual(host.session.outbound(TRAVEL, travel_to(8, 111)), travel_to(8, 111))
        self.assertEqual(len([notice for notice in host.game.notices if "another save" in notice]), 1)

    def test_with_one_family_a_guests_travel_is_the_hosts_as_before(self):
        host, guest = joined()
        host.game.foreign_homes = {9}
        order = travel_to(9, 222)
        self.assertEqual(self.sends_home(host, guest), [order])
        self.assertEqual(host.game.resumed, [])
        self.assertFalse(any("another save" in notice for notice in host.game.notices))
        self.assertFalse(any(host.game.sent_off_households))
        self.assertFalse(any(place[3] for places in host.game.snapshot_places for place in places))
        self.assertTrue(any("our game travels to 0x9 with sims 222, as" in line for line in host.logged))


class SkewerTest(unittest.TestCase):
    def test_a_view_update_splits_by_several_types_at_once(self):
        update = view_update_of((5, b"a"), (SET_SIM_ACTIVE, b"b"), (SELECTABLE, b"c"))
        without, only = wire.split_view_update(update, {SET_SIM_ACTIVE, SELECTABLE})
        self.assertEqual(op_types(without), [5])
        self.assertEqual(op_types(only), [SET_SIM_ACTIVE, SELECTABLE])
        self.assertEqual(wire.edit_view_update(update, lambda op_type, data: None), b"")

    def test_a_guest_with_a_family_of_its_own_sees_only_its_sims(self):
        host, guest = cross_joined()
        update = view_update_of((5, b"a"), (SET_SIM_ACTIVE, b"b"), (SELECTABLE, selectable(111, 222)))
        self.assertEqual(host.session.outbound(VIEW_UPDATE, update), update)
        settle(host, guest)
        (msg_id, data), = [item for item in guest.game.delivered if item[0] == VIEW_UPDATE]
        self.assertEqual(op_types(data), [5, SELECTABLE])
        self.assertEqual(listed(data), [[222]])

    def test_a_list_of_none_of_its_sims_is_kept_from_it(self):
        host, guest = cross_joined()
        update = view_update_of((5, b"a"), (SELECTABLE, selectable(111)))
        host.session.outbound(VIEW_UPDATE, update)
        settle(host, guest)
        (msg_id, data), = [item for item in guest.game.delivered if item[0] == VIEW_UPDATE]
        self.assertEqual(op_types(data), [5])

    def test_with_one_family_the_guest_sees_the_whole_list(self):
        host, guest = joined()
        update = view_update_of((5, b"a"), (SET_SIM_ACTIVE, b"b"), (SELECTABLE, selectable(111, 222)))
        host.session.outbound(VIEW_UPDATE, update)
        settle(host, guest)
        (msg_id, data), = [item for item in guest.game.delivered if item[0] == VIEW_UPDATE]
        self.assertEqual(listed(data), [[111, 222]])
        self.assertEqual(op_types(data), [5, SELECTABLE])


class ReloadLoopTest(unittest.TestCase):
    def test_a_host_that_moves_on_once_while_the_guest_loads_has_it_load_once_more(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        host.game.ticks += 1
        loads(host, guest)
        self.assertTrue(any("moved on while the other player loaded (ticks 0->1)" in line for line in host.logged))
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)
        self.assertEqual(len(guest.game.adopted), 2)

    def test_a_host_that_moves_on_during_every_load_lets_the_guest_in_behind(self):
        host, guest = connected(cross=True)
        for _ in range(20):
            if guest.session.live:
                break
            both(host, guest, 30)
            if host.game.snapshots and guest.session._phase == session.ASKING:
                answers(host, guest)
            host.game.ticks += 1
            if guest.session._phase == session.LOADING:
                loads(host, guest)
        self.assertTrue(guest.session.live)
        self.assertTrue(host.session.live)
        self.assertEqual(len(guest.game.adopted), 2)
        self.assertTrue(any("got in a moment behind" in notice for notice in host.game.notices))
        self.assertTrue(any("you got in a moment behind" in notice for notice in guest.game.notices))
        self.assertTrue(guest_of(host).drift)
        # Joined, the count starts again.
        self.assertEqual(guest_of(host).moved_on, 0)

    def test_with_one_family_a_host_that_moves_on_during_every_load_has_the_guest_load_again(self):
        host, guest = connected()
        for _ in range(10):
            both(host, guest, 30)
            if host.game.snapshots and guest.session._phase == session.ASKING:
                answers(host, guest)
            host.game.ticks += 1
            if guest.session._phase == session.LOADING:
                loads(host, guest)
        # It is never let in out of step, as before.
        self.assertFalse(guest.session.live)
        self.assertGreater(len(guest.game.adopted), 3)
        self.assertEqual(guest_of(host).drift, "")
        self.assertFalse(any("moment behind" in notice for notice in host.game.notices))
        # Once the host stands still, it gets in.
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

    def test_with_one_family_a_guest_whose_loads_are_missed_loads_again_at_once(self):
        host, guest = joined()
        peer = guest_of(host)
        for _ in range(3):
            host.session._bump(peer)
            settle(host, guest)
            self.assertTrue(guest.session.stale)
            reloads_from_host(host, guest)
            self.assertTrue(guest.session.live)

    def test_a_guest_whose_loads_are_missed_again_and_again_waits_before_loading(self):
        host, guest = joined(cross=True)
        peer = guest_of(host)

        def misses():
            # The host has the guest show another world right after it
            # loaded, as a host whose game moved on meanwhile does.
            host.session._bump(peer)
            settle(host, guest)

        misses()
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)
        misses()
        self.assertFalse(guest.session.stale)
        guest.tick(session.RETRY_SECONDS[0] - 1)
        settle(host, guest)
        self.assertFalse(guest.session.stale)
        guest.tick(2)
        settle(host, guest)
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        misses()
        self.assertTrue(any("keeps changing while we load it" in notice for notice in guest.game.notices))
        guest.tick(session.RETRY_SECONDS[1] + 1)
        settle(host, guest)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)
        # In for a while, it loads again at once after the next change.
        both(host, guest, session.LIVE_SECONDS + 1)
        both(host, guest)
        misses()
        self.assertTrue(guest.session.stale)


class HeldClockTest(unittest.TestCase):
    def test_no_clock_command_but_build_mode_runs_while_held(self):
        host, guest = connected(cross=True)
        both(host, guest)
        self.assertTrue(host.session.holding)
        self.assertFalse(host.session.command("clock.unrequest_pause", []))
        self.assertFalse(host.session.command("clock.request_pause", []))
        self.assertFalse(host.session.command("clock.setspeed_internal", ["1"]))
        self.assertTrue(host.session.command(session.BUILD_COMMAND, ["1"]))
        self.assertTrue(host.session.command(session.BUILD_COMMAND, ["0"]))

    def test_with_one_family_only_play_and_speed_are_refused_while_held_as_before(self):
        host, guest = connected()
        both(host, guest)
        self.assertTrue(host.session.holding)
        self.assertFalse(host.session.command("clock.setspeed", ["1"]))
        self.assertFalse(host.session.command("clock.toggle_pause_unpause", []))
        self.assertTrue(host.session.command("clock.unrequest_pause", []))
        self.assertTrue(host.session.command("clock.request_pause", ["Dialog", "Manager", "Set", "Dialog", "Visible"]))
        answers(host, guest)
        loads(host, guest)
        host.tick()
        self.assertTrue(host.session.command("clock.unrequest_pause", ["Dialog", "Manager", "Set", "Dialog",
                                                                      "Visible"]))

    def test_with_one_family_the_game_is_paused_again_each_second_while_held_as_before(self):
        host, guest = connected()
        both(host, guest)
        pauses = host.game.pauses
        host.tick()
        host.tick(0.5)
        self.assertEqual(host.game.pauses, pauses)
        host.tick(session.PAUSE_SECONDS)
        self.assertEqual(host.game.pauses, pauses + 1)

    def test_a_pause_of_the_interface_from_before_the_hold_may_end(self):
        host, guest = connected(cross=True)
        both(host, guest)
        self.assertTrue(host.session.command("clock.unrequest_pause", ["Layout", "Manager", "Set", "Modal",
                                                                      "Visible"]))

    def test_the_end_of_a_pause_refused_while_held_is_refused_too(self):
        host, guest = connected(cross=True)
        both(host, guest)
        dialog = ["Dialog", "Manager", "Set", "Dialog", "Visible"]
        self.assertFalse(host.session.command("clock.request_pause", dialog))
        answers(host, guest)
        loads(host, guest)
        host.tick()
        self.assertFalse(host.session.holding)
        self.assertFalse(host.session.command("clock.unrequest_pause", dialog))
        # Any later one is the game's own again.
        self.assertTrue(host.session.command("clock.request_pause", dialog))
        self.assertTrue(host.session.command("clock.unrequest_pause", dialog))

    def test_the_game_is_paused_again_at_every_tick_while_held(self):
        host, guest = connected(cross=True)
        both(host, guest)
        pauses = host.game.pauses
        host.tick()
        host.tick()
        self.assertEqual(host.game.pauses, pauses + 2)

    def test_build_mode_is_logged_with_what_became_of_it(self):
        host, guest = joined()
        self.assertTrue(host.session.command(session.BUILD_COMMAND, ["1"]))
        self.assertIn("build mode on while live: runs here", host.logged)
        self.assertFalse(guest.session.command(session.BUILD_COMMAND, ["1"]))
        self.assertIn("build mode on while live: dropped", guest.logged)


def legacy_pair():
    """Joined through a host's launcher from before version 3."""
    host, guest = Player(session.HOST, routed=False), Player(session.GUEST)
    host.game.zone = guest.game.zone = True
    guest.game.sim_id = 222
    host.session.bridge_up(session.HOST)
    guest.session.bridge_up(session.GUEST)
    host.link(1)
    guest.link(1)
    settle(host, guest)
    reloads_from_host(host, guest)
    return host, guest


class RoutedTest(unittest.TestCase):
    """A host's launcher of version 3 numbers its guests: the host says what
    is for one guest to it alone, and what is for all to all."""

    def setUp(self):
        global RELAY
        relay, RELAY = RELAY, "routed"

        def restore():
            global RELAY
            RELAY = relay

        self.addCleanup(restore)

    def test_the_guest_is_known_by_its_peer(self):
        host, guest = joined()
        self.assertEqual(list(host.session.peers), [1])
        peer = host.session.peers[1]
        self.assertEqual((peer.name, peer.player, peer.up, peer.sim_id), ("guest 1", "p1", True, 222))
        self.assertTrue(host.session.live)

    def test_the_stream_goes_to_every_guest_and_replies_to_the_one_that_asked(self):
        host, guest = joined()
        host.take()
        host.session.outbound(OTHER_MSG, b"x")
        self.assertEqual(host.take_routed(), [(None, (frames.MSG, OTHER_MSG, b"x"))])
        host.game.during_execute = lambda: host.session.outbound(PIE_MENU, b"menu")
        guest.session.command("interactions.choices", ["5487", "0"])
        settle(host, guest)
        self.assertEqual(guest.game.delivered[-1], (PIE_MENU, b"menu"))
        host.game.during_execute = lambda: host.session.outbound(PIE_MENU, b"menu")
        host.receive([(frames.COMMAND, 0, b'{"name":"interactions.choices","args":[]}')])
        self.assertEqual(host.take_routed(), [(1, (frames.MSG, PIE_MENU, b"menu"))])

    def test_answers_to_a_guest_go_to_it_alone(self):
        host, guest = connected()
        guest.tick()
        host.take()
        sent = guest.take()
        host.receive(sent)
        both(host, guest)
        host.game.snapshots[0](b"save")
        routed = host.take_routed()
        offers = [to for to, (kind, _, payload) in routed if kind == frames.CONTROL and b'"offer"' in payload]
        parts = [to for to, (kind, _, _) in routed if kind == frames.PART]
        self.assertEqual(offers, [1])
        self.assertEqual(set(parts), {1})

    def test_peer_gone_is_a_lost_link_and_the_same_peer_comes_back(self):
        host, guest = joined()
        host.link(0)
        guest.link(0)
        self.assertFalse(host.session.live)
        self.assertFalse(host.session.peers[1].up)
        self.assertEqual(host.session.remotes, 0)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        reloads_from_host(host, guest)
        self.assertTrue(host.session.live)
        self.assertEqual(list(host.session.peers), [1])

    def test_a_hold_outlasts_a_short_drop_of_the_guests_link(self):
        host, guest = connected()
        both(host, guest)
        self.assertTrue(host.session.holding)
        host.link(0)
        host.tick(session.LINK_SECONDS - 1)
        self.assertTrue(host.session.holding)
        host.link(1)
        host.tick(10)
        self.assertTrue(host.session.holding)

    def test_link_before_peer_up_is_the_same_guest(self):
        """A launcher that heard of our version only after its remote came
        says `link` first, then names the remote."""
        host, guest = Player(session.HOST), Player(session.GUEST)
        host.game.zone = guest.game.zone = True
        guest.game.sim_id = 222
        host.session.bridge_up(session.HOST)
        guest.session.bridge_up(session.GUEST)
        host._relay_says("link", remotes=1)
        guest.link(1)
        self.assertEqual(list(host.session.peers), [session.LEGACY])
        host.peers_up = {1}
        host._relay_says("peer_up", peer=1, name="guest 1", player="p1")
        self.assertEqual(list(host.session.peers), [1])
        settle(host, guest)
        reloads_from_host(host, guest)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)
        self.assertEqual(guest_of(host).sim_id, 222)

    def test_frames_that_crossed_before_the_launcher_heard_our_version_are_the_guests(self):
        host, guest = joined()
        host.speaks = False
        guest.game.sim_id = 333
        both(host, guest)
        host.speaks = True
        self.assertEqual(guest_of(host).sim_id, 333)

    def test_a_routed_frame_names_the_remote_a_link_brought_up(self):
        """A launcher that wraps without having said `peer_up` for its remote."""
        host, guest = Player(session.HOST), Player(session.GUEST)
        host.game.zone = guest.game.zone = True
        host.session.bridge_up(session.HOST)
        guest.session.bridge_up(session.GUEST)
        host._relay_says("link", remotes=1)
        guest.link(1)
        settle(host, guest)
        self.assertEqual(list(host.session.peers), [1])
        self.assertTrue(host.session.peers[1].up)
        self.assertEqual(host.session.peers[1].save, 1)

    def test_a_plain_frame_from_one_of_several_named_guests_is_dropped(self):
        host, guest = joined()
        host._relay_says("peer_up", peer=2, name="guest 2", player="p2")
        host.take()
        host.session.frame(frames.CONTROL, 0, b'{"type":"active_sim","sim_id":"9"}')
        self.assertEqual(host.session.peers[1].sim_id, 222)
        self.assertEqual(host.session.peers[2].sim_id, 0)
        self.assertIn("dropped a frame from a guest our launcher did not name", host.logged)

    def test_a_guest_cannot_speak_for_the_launcher(self):
        """What our launcher says of its remotes is never wrapped: a guest's
        game that sends the same is not believed."""
        host, guest = joined()
        host._relay_says("peer_up", peer=2, name="guest 2", player="p2")
        for control in (b'{"type":"link","remotes":0}', b'{"type":"peer_gone","peer":2}',
                        b'{"type":"peer_up","peer":3,"name":"x","player":"x"}'):
            host.session.frame(frames.CONTROL, 0, control, 1)
        self.assertTrue(host.session.peers[1].up)
        self.assertTrue(host.session.peers[2].up)
        self.assertNotIn(3, host.session.peers)
        self.assertTrue(host.session.live)

    def test_a_hold_for_a_guest_that_left_lasts_its_grace_while_another_plays(self):
        """Each hold ends on what its own guest does: another player who
        comes in meanwhile does not end it, is told why the game stays
        paused, and is not sent back to loading when the hold runs out."""
        host, first = connected()
        both(host, first)
        self.assertTrue(host.session.holding)
        host._relay_says("peer_gone", peer=1)
        host._relay_says("link", remotes=0)
        host.take()

        other = Player(session.GUEST)
        other.game.zone = True
        other.game.sim_id = 333
        other.game.zone_number = 7
        other.session.bridge_up(session.GUEST)
        host._relay_says("peer_up", peer=2, name="guest 2", player="p2")
        host._relay_says("link", remotes=1)
        other.link(1)

        def exchange():
            for _ in range(100):
                out, back = host.take_routed(), other.take()
                if not out and not back:
                    return
                other.receive([frame for to, frame in out if to in (None, 0, 2)])
                host.receive(back, peer=2)
            raise AssertionError("the sessions never went quiet")

        exchange()
        host.tick()
        other.tick()
        exchange()
        while host.game.snapshots:
            host.game.snapshots.pop(0)(b"save")
            exchange()
        other.leave()
        other.game.save = host.game.save
        other.arrive()
        exchange()
        self.assertTrue(other.session.live)
        self.assertTrue(host.session.holding)
        self.assertIn("guest 1 is loading; the game stays paused until they are in", other.game.notices)
        world = host.session._world
        for _ in range(int(session.LINK_SECONDS) + 10):
            host.game.ticks += 1
            host.tick(1.0)
            other.tick(1.0)
            exchange()
        self.assertFalse(host.session.holding)
        self.assertEqual(host.session._world, world)
        pauses = host.game.pauses
        host.tick(10.0)
        self.assertEqual(host.game.pauses, pauses)
        self.assertTrue(other.session.live)

    def test_a_guest_that_asked_while_we_saved_for_another_is_refused_once_we_left(self):
        host, first = connected()
        both(host, first)
        self.assertEqual(len(host.game.snapshots), 1)
        host._relay_says("peer_gone", peer=1)
        host._relay_says("peer_up", peer=2, name="guest 2", player="p2")
        host.take()
        # The save for the first runs still, and as our game has not moved
        # since it began, it answers the second too.
        host.receive(frames.Decoder().feed(frames.control("resync", id=5, zone=7, lot="")), peer=2)
        self.assertEqual(len(host.game.snapshots), 1)
        self.assertEqual(host.session.peers[2].saving, (5, 7, ""))
        host.leave()
        host.take()
        host.game.snapshots[0](b"save")
        refused = [(to, frames.parse_json(payload)) for to, (kind, _, payload) in host.take_routed()
                   if kind == frames.CONTROL and b'"resync_failed"' in payload]
        self.assertEqual(refused, [(2, {"type": "resync_failed", "id": 5})])
        self.assertIsNone(host.session.peers[2].asked)
        self.assertIsNone(host.session.peers[2].saving)

    def test_a_guest_takes_no_notice_of_peers(self):
        host, guest = joined()
        guest.session.frame(frames.CONTROL, 0, b'{"type":"peer_gone","peer":1}')
        self.assertTrue(guest.session.live)

    def test_a_guest_with_another_version_does_not_join(self):
        host, guest = connected()
        guest.control("state", role="guest", in_zone=True, zone="7", save="1", world="x.1", v=2)
        host.control("state", role="guest", in_zone=True, zone="7", save="1", world=host.session._world, v=2)
        self.assertFalse(host.session.live)
        self.assertFalse(guest.session.live)
        self.assertTrue(any("another version of the mod (2, ours 3)" in line for line in host.logged))


class LegacyRelayTest(unittest.TestCase):
    """A host's launcher from before version 3 wraps nothing and names no
    guest: the host's mod says everything to its one remote, plainly."""

    def test_joins_plays_and_comes_back_after_a_lost_link(self):
        host, guest = legacy_pair()
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)
        self.assertEqual(list(host.session.peers), [session.LEGACY])
        self.assertEqual(guest_of(host).sim_id, 222)
        host.game.during_execute = lambda: host.session.outbound(PIE_MENU, b"menu")
        guest.session.command("interactions.choices", ["5487", "0"])
        settle(host, guest)
        self.assertEqual(host.game.executed, [("interactions.choices", ["5487", "0"], 222)])
        self.assertEqual(guest.game.delivered[-1], (PIE_MENU, b"menu"))
        host.link(0)
        guest.link(0)
        self.assertFalse(host.session.live)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        reloads_from_host(host, guest)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)

    def test_no_frame_is_routed(self):
        # Player raises on a ROUTED frame for a launcher that does not unwrap.
        host, guest = legacy_pair()
        host.session.outbound(OTHER_MSG, b"x")
        host.link(1)
        settle(host, guest)
        self.assertFalse(host.session._routed)

    def test_a_new_launcher_names_the_remote_the_old_one_had(self):
        """The host's launcher is started again, now of version 3."""
        host, guest = legacy_pair()
        host.session.bridge_down("restarted")
        host.session.bridge_up(session.HOST)
        host.routed = True
        host.link(1)
        settle(host, guest)
        self.assertEqual(list(host.session.peers), [1])
        reloads_from_host(host, guest)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)

    def test_an_old_launcher_after_a_new_one(self):
        host, guest = joined()
        host.session.bridge_down("restarted")
        host.session.bridge_up(session.HOST)
        host.routed = False
        host.link(1)
        settle(host, guest)
        self.assertEqual(list(host.session.peers), [session.LEGACY])
        reloads_from_host(host, guest)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)


def group(count=2, strangers=()):
    """A host and `count` guests on its lot, connected through the host's
    launcher, which numbers them 1, 2, ...; guest n plays sim 111 * (n + 1).
    None of them has loaded the host's state yet; those numbered in
    `strangers` play a save of their own."""
    host = Player(session.HOST)
    host.game.zone = True
    host.session.bridge_up(session.HOST)
    guests = [newcomer(number, 2 if number in strangers else 1) for number in range(1, count + 1)]
    host.link(count)
    for guest in guests:
        guest.link(1)
    settle(host, *guests)
    return host, guests


def newcomer(number, save=1):
    guest = Player(session.GUEST)
    guest.game.zone = True
    guest.game.save = save
    guest.game.sim_id = 111 * (number + 1)
    guest.session.bridge_up(session.GUEST)
    return guest


def comes_late(host, guests):
    """One more guest connects, the others staying as they are."""
    number = len(guests) + 1
    guest = newcomer(number)
    host.peers_up.add(number)
    host._relay_says("peer_up", peer=number, name="guest {}".format(number), player="p{}".format(number))
    guest.link(1)
    guests.append(guest)
    settle(host, *guests)
    return guest


def all_tick(host, guests, seconds=0.0):
    for player in [host] + guests:
        player.tick(seconds)
    settle(host, *guests)


def load(host, guests, *loading):
    """Those guests' games load what they adopted."""
    for guest in loading:
        guest.leave()
        guest.game.save = host.game.save
        guest.arrive()
    settle(host, *guests)


def all_in(count=2):
    """A group whose guests have all loaded the host's state, with one save."""
    host, guests = group(count)
    all_tick(host, guests)
    host.game.snapshots[-1](b"save")
    settle(host, *guests)
    load(host, guests, *guests)
    return host, guests


def offers(host):
    """(peer, id, world) of each state offered in what the host sent and the
    guests have yet to get."""
    return [(to, frames.parse_json(payload)["id"], frames.parse_json(payload).get("world"))
            for to, (kind, _, payload) in zip(host.sent_to, host.sent)
            if kind == frames.CONTROL and b'"offer"' in payload and b'"state"' in payload]


class GroupTest(unittest.TestCase):
    """A host with several guests: each is joined or not by itself."""

    def setUp(self):
        global RELAY
        relay, RELAY = RELAY, "routed"

        def restore():
            global RELAY
            RELAY = relay

        self.addCleanup(restore)

    def assertLive(self, host, guests, *live):
        for number, guest in enumerate(guests, 1):
            self.assertEqual(guest.session.live, number in live, "guest {}".format(number))
            self.assertEqual(host.session._live_with(host.session.peers[number]), number in live,
                             "host with guest {}".format(number))

    def test_every_guest_joins_with_its_own_sim(self):
        host, guests = all_in(3)
        self.assertLive(host, guests, 1, 2, 3)
        self.assertEqual([host.session.peers[n].sim_id for n in (1, 2, 3)], [222, 333, 444])
        self.assertFalse(host.session.holding)

    def test_requests_with_the_same_id_from_two_guests_are_each_answered(self):
        host, guests = group(2)
        all_tick(host, guests)
        # Each guest numbers its own requests: both asked with 1.
        self.assertEqual([guest.session._request for guest in guests], [1, 1])
        self.assertEqual([host.session.peers[n].saving[0] for n in (1, 2)], [1, 1])
        host.take()
        host.game.snapshots[-1](b"save")
        self.assertEqual(sorted(offers(host)), [(1, 1, host.session._world), (2, 1, host.session._world)])
        settle(host, *guests)
        self.assertEqual([guest.game.adopted for guest in guests], [[b"save"], [b"save"]])
        load(host, guests, *guests)
        self.assertLive(host, guests, 1, 2)

    def test_guests_that_ask_at_once_are_answered_by_one_save(self):
        host, guests = all_in(3)
        world, snapshots = host.session._world, len(host.game.snapshots)
        host.leave()
        settle(host, *guests)
        host.arrive()
        settle(host, *guests)
        self.assertNotEqual(host.session._world, world)
        self.assertLive(host, guests)
        all_tick(host, guests)
        self.assertEqual(len(host.game.snapshots), snapshots + 1)
        self.assertEqual(len(host.game.snapshot_places[-1]), 3)
        host.game.snapshots[-1](b"again")
        settle(host, *guests)
        load(host, guests, *guests)
        self.assertLive(host, guests, 1, 2, 3)
        self.assertEqual([guest.game.adopted[-1] for guest in guests], [b"again"] * 3)

    def test_a_guest_that_asks_once_our_game_moved_waits_for_the_next_save(self):
        host, guests = all_in(1)
        first = guests[0]
        first.leave()
        first.arrive()
        settle(host, *guests)
        snapshots = len(host.game.snapshots)
        host.game.ticks += 1
        late = comes_late(host, guests)
        all_tick(host, guests)
        self.assertEqual(len(host.game.snapshots), snapshots)
        self.assertEqual(len(host.game.snapshot_places[-1]), 1)
        self.assertIsNotNone(host.session.peers[2].asked)
        host.game.snapshots[-1](b"first")
        settle(host, *guests)
        self.assertEqual(len(host.game.snapshots), snapshots + 1)
        self.assertEqual(first.game.adopted[-1], b"first")
        self.assertEqual(late.game.adopted, [])
        host.game.snapshots[-1](b"second")
        settle(host, *guests)
        self.assertEqual(late.game.adopted, [b"second"])

    def test_one_guests_hold_is_not_ended_by_another_guests_state(self):
        host, guests = all_in(2)
        first, second = guests
        # The first guest's own zone loads again: it asks once more, and we
        # hold for it.
        first.leave()
        first.arrive()
        settle(host, *guests)
        all_tick(host, guests)
        self.assertTrue(host.session.peers[1].hold_until)
        self.assertFalse(host.session.peers[2].hold_until)
        # The second, joined, says it is in a zone, as one that got in does.
        second.session._announce()
        settle(host, *guests)
        all_tick(host, guests, 1.0)
        self.assertTrue(host.session.holding)
        self.assertTrue(host.session.peers[1].hold_until)
        self.assertLive(host, guests, 2)
        host.game.snapshots[-1](b"save")
        settle(host, *guests)
        load(host, guests, first)
        self.assertFalse(host.session.holding)
        self.assertLive(host, guests, 1, 2)

    def test_the_game_is_held_until_the_last_guest_is_in(self):
        host, guests = group(2)
        all_tick(host, guests)
        host.game.snapshots[-1](b"save")
        settle(host, *guests)
        load(host, guests, guests[0])
        self.assertLive(host, guests, 1)
        self.assertTrue(host.session.holding)
        self.assertFalse(host.session.command("clock.setspeed", ["1"]))
        pauses = host.game.pauses
        all_tick(host, guests, session.PAUSE_SECONDS + 1)
        self.assertGreater(host.game.pauses, pauses)
        load(host, guests, guests[1])
        self.assertLive(host, guests, 1, 2)
        self.assertFalse(host.session.holding)
        self.assertTrue(host.session.command("clock.setspeed", ["1"]))

    def test_a_guest_in_while_the_game_is_held_for_another_is_told(self):
        host, guests = group(2)
        all_tick(host, guests)
        host.game.snapshots[-1](b"save")
        settle(host, *guests)
        load(host, guests, guests[0])
        self.assertIn("guest 2 is loading; the game stays paused until they are in", guests[0].game.notices)
        # Its player presses play; the host's game hears the command as it
        # runs it, and refuses it.
        host.game.during_execute = lambda: host.session.command("clock.setspeed", ["1"])
        guests[0].session.command("clock.setspeed", ["1"])
        settle(host, *guests)
        self.assertEqual(host.game.executed_for, [1])
        self.assertIn("the game stays paused while guest 2 is loading", guests[0].game.notices)
        self.assertEqual(guests[1].game.notices.count("the game stays paused while guest 2 is loading"), 0)

    def test_guests_in_are_told_when_the_game_is_held_for_a_late_one(self):
        host, guests = all_in(1)
        late = comes_late(host, guests)
        all_tick(host, guests)
        self.assertTrue(host.session.peers[2].hold_until)
        self.assertIn("guest 2 is loading; the game stays paused until they are in", guests[0].game.notices)
        self.assertNotIn("guest 2 is loading; the game stays paused until they are in", late.game.notices)

    def test_a_late_guest_loads_alone_and_the_world_stays(self):
        host, guests = all_in(2)
        world, adopted = host.session._world, [list(guest.game.adopted) for guest in guests]
        late = comes_late(host, guests)
        self.assertLive(host, guests, 1, 2)
        all_tick(host, guests)
        host.game.snapshots[-1](b"late")
        settle(host, *guests)
        load(host, guests, late)
        self.assertLive(host, guests, 1, 2, 3)
        self.assertEqual(host.session._world, world)
        self.assertEqual([guest.game.adopted for guest in guests[:2]], adopted)
        self.assertEqual(late.game.adopted, [b"late"])

    def test_a_late_guest_behind_our_game_loads_again_alone(self):
        host, guests = all_in(2)
        world = host.session._world
        late = comes_late(host, guests)
        all_tick(host, guests)
        host.game.snapshots[-1](b"late")
        settle(host, *guests)
        # Our game moved on before the late guest got in: its state is
        # behind, the others' is not.
        host.game.ticks += 1
        load(host, guests, late)
        self.assertLive(host, guests, 1, 2)
        self.assertEqual(host.session._world, world)
        self.assertEqual(host.session.peers[3].epoch, 1)
        self.assertTrue(late.session.stale)
        all_tick(host, guests)
        host.take()
        host.game.snapshots[-1](b"again")
        self.assertEqual(offers(host), [(3, late.session._request, world + ".1")])
        settle(host, *guests)
        load(host, guests, late)
        self.assertLive(host, guests, 1, 2, 3)
        self.assertEqual(host.session._world, world)
        self.assertEqual(late.session._world, world + ".1")
        self.assertEqual(guests[0].session._world, world)

    def test_a_hold_that_lapses_sends_only_its_guest_back_to_loading(self):
        host, guests = all_in(1)
        world = host.session._world
        late = comes_late(host, guests)
        all_tick(host, guests)
        host.game.snapshots[-1](b"late")
        settle(host, *guests)
        for _ in range(int(session.HOLD_SECONDS) + 5):
            host.tick(1.0)
            settle(host, *guests)
        self.assertFalse(host.session.holding)
        host.game.ticks += 1
        all_tick(host, guests, 2.0)
        self.assertEqual(host.session.peers[2].epoch, 1)
        self.assertEqual(host.session._world, world)
        self.assertLive(host, guests, 1)
        load(host, guests, late)
        self.assertLive(host, guests, 1)
        self.assertTrue(late.session.stale)

    def test_one_guest_leaving_keeps_the_others_in_and_their_transfers(self):
        host, guests = all_in(3)
        third = guests[2]
        third.leave()
        third.arrive()
        settle(host, *guests)
        all_tick(host, guests)
        host.game.snapshots[-1](b"x" * (transfer.WINDOW * 2))
        request = third.session._request
        self.assertIsNotNone(host.session.peers[3].transfers.outgoing(request))
        host.peers_up.discard(1)
        host._relay_says("peer_gone", peer=1)
        self.assertFalse(host.session.peers[1].up)
        self.assertIsNotNone(host.session.peers[3].transfers.outgoing(request))
        self.assertEqual(host.game.forgotten, [1])
        guests[0].link(0)
        settle(host, *guests)
        load(host, guests, third)
        self.assertLive(host, guests, 2, 3)
        self.assertEqual(third.game.adopted[-1], b"x" * (transfer.WINDOW * 2))
        host.game.during_execute = None
        guests[1].session.command("interactions.choices", ["5487", "0"])
        settle(host, *guests)
        self.assertEqual(host.game.executed[-1], ("interactions.choices", ["5487", "0"], 333))

    def test_commands_run_as_the_sending_guests_sim_and_only_while_it_is_in(self):
        host, guests = group(2)
        all_tick(host, guests)
        host.game.snapshots[-1](b"save")
        settle(host, *guests)
        load(host, guests, guests[1])
        self.assertLive(host, guests, 2)
        command = (frames.COMMAND, 0, b'{"name":"interactions.choices","args":["1"]}')
        host.receive([command], peer=1)
        self.assertEqual(host.game.executed, [])
        host.receive([command], peer=2)
        self.assertEqual(host.game.executed, [("interactions.choices", ["1"], 333)])
        self.assertEqual(host.game.executed_for, [2])

    def test_replies_and_screens_reach_only_their_guest(self):
        host, guests = all_in(3)
        first, second, third = guests
        host.game.households = {222: 2, 333: 3, 444: 4, 555: 3}
        host.game.during_execute = lambda: host.session.outbound(PIE_MENU, b"menu")
        second.session.command("interactions.choices", ["5487", "0"])
        settle(host, *guests)
        self.assertEqual([(PIE_MENU, b"menu") in guest.game.delivered for guest in guests], [False, True, False])
        host.game.during_execute = None
        host.take()
        dialog = owned(444)
        self.assertIsNone(host.session.outbound(DIALOG, dialog))
        # A sim of the second guest's household, which it does not control.
        family = owned(555)
        self.assertIsNone(host.session.outbound(DIALOG, family))
        self.assertEqual(host.take_routed(), [(3, (frames.MSG, DIALOG, dialog)), (2, (frames.MSG, DIALOG, family))])
        self.assertEqual(host.session.outbound(OTHER_MSG, b"x"), b"x")
        self.assertEqual(host.take_routed(), [(None, (frames.MSG, OTHER_MSG, b"x"))])

    def test_a_household_two_guests_play_from_is_ours_to_answer_for(self):
        host, guests = all_in(2)
        host.game.households = {222: 3, 333: 3, 555: 3}
        host.take()
        dialog = owned(555)
        self.assertEqual(host.session.outbound(DIALOG, dialog), dialog)
        self.assertEqual(host.take_routed(), [])

    def test_the_stream_goes_only_to_the_guests_in(self):
        host, guests = group(2)
        all_tick(host, guests)
        host.game.snapshots[-1](b"save")
        settle(host, *guests)
        load(host, guests, guests[0])
        host.take()
        update = view_update(5, SET_SIM_ACTIVE)
        host.session.outbound(VIEW_UPDATE, update)
        host.session.outbound(OTHER_MSG, b"x")
        sent = host.take_routed()
        self.assertEqual([to for to, _ in sent], [1, 1])
        self.assertEqual(op_types(sent[0][1][2]), [5])

    def test_each_guest_hears_who_else_builds(self):
        host, guests = all_in(2)
        first, second = guests
        first.session.command(session.BUILD_COMMAND, ["1"])
        settle(host, *guests)
        self.assertIn("the other player is in build mode", second.game.notices)
        self.assertFalse(any("build" in notice for notice in first.game.notices))
        self.assertIn("guest 1 is in build mode", host.game.notices)
        host.session.command(session.BUILD_COMMAND, ["1"])
        settle(host, *guests)
        self.assertIn("the host, guest 1 are in build mode", second.game.notices)
        self.assertTrue(any("both of you" in notice for notice in first.game.notices))
        first.session.command(session.BUILD_COMMAND, ["0"])
        settle(host, *guests)
        self.assertEqual(second.game.notices[-1], "the host is in build mode")
        host.session.command(session.BUILD_COMMAND, ["0"])
        settle(host, *guests)
        self.assertEqual(second.game.notices[-1], "the other player left build mode")

    def test_a_status_line_for_each_guest(self):
        host, guests = group(2)
        all_tick(host, guests)
        host.game.snapshots[-1](b"save")
        settle(host, *guests)
        load(host, guests, guests[0])
        lines = host.session.describe_peers()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("guest 1 (guest 1): joined, sim 222"), lines[0])
        self.assertTrue(lines[1].startswith("guest 2 (guest 2): loading, sim none"), lines[1])
        self.assertTrue(lines[1].endswith("held for"), lines[1])

    def test_log_lines_about_a_guest_name_it(self):
        host, guests = all_in(2)
        self.assertTrue(any(line.startswith("guest 2: ") for line in host.logged))
        self.assertEqual(host.session.label(2), "guest 2")
        self.assertEqual(host.session.label(None), "")

    def test_what_is_about_the_sim_of_a_guest_not_joined_is_ours(self):
        host, guests = all_in(2)
        plays_apart(host, guests, guests[0], 8)
        self.assertLive(host, guests, 2)
        self.assertEqual(host.session.peers[1].sim_id, 222)
        host.take()
        # Relayed to the guest playing apart, it would be dropped there, and
        # nobody would see it.
        dialog = owned(222)
        self.assertEqual(host.session.outbound(DIALOG, dialog), dialog)
        self.assertTrue(host.session.client_cheat("sims.exit2cas 222 1 0"))
        self.assertEqual(host.take_routed(), [])

    def test_what_came_through_a_launcher_goes_with_it(self):
        host, guests = group(2, strangers=(2,))
        all_tick(host, guests)
        guests[1].game.contributions[-1](b"household")
        settle(host, *guests)
        host.game.snapshots[-1](b"save")
        settle(host, *guests)
        self.assertEqual(len(host.session._gifts), 1)
        self.assertTrue(host.session.peers[1].bases)
        # The launcher starts again, and may number other players so.
        host.session.bridge_down("restarting")
        self.assertEqual(host.session._gifts, [])
        self.assertIsNone(host.session._batch)
        self.assertEqual([peer.bases for _, peer in sorted(host.session.peers.items())], [{}, {}])
        host.session.bridge_up(session.HOST)
        host.tick()
        self.assertEqual(host.game.merged, [])

    def test_a_guest_first_on_a_lot_apart_is_still_first_after_its_link_drops(self):
        host, guests = all_in(3)
        first, second = guests[0], guests[1]
        plays_apart(host, guests, first, 8)
        plays_apart(host, guests, second, 8)
        peers = host.session.peers
        orders = (peers[1].place_order, peers[2].place_order)
        host.peers_up.discard(1)
        host._relay_says("peer_gone", peer=1)
        host.peers_up.add(1)
        host._relay_says("peer_up", peer=1, name="guest 1", player="p1")
        settle(host, *guests)
        all_tick(host, guests)
        self.assertEqual((peers[1].place_order, peers[2].place_order), orders)
        self.assertFalse(any("already playing" in notice for notice in first.game.notices))
        gives_apart(host, guests, second, b"second")
        gives_apart(host, guests, first, b"first")
        self.assertEqual(host.game.merged, [(b"first", 1)])


def plays_apart(host, guests, guest, lot):
    """The guest goes to another lot of the host's save, loads it from the
    host, and plays it by itself."""
    guest.leave()
    settle(host, *guests)
    guest.arrive(lot)
    settle(host, *guests)
    all_tick(host, guests)
    host.game.snapshots[-1](b"lot")
    settle(host, *guests)
    load(host, guests, guest)
    all_tick(host, guests)


def gives_apart(host, guests, guest, payload):
    """A guest playing by itself gives its household, as it does now and
    then."""
    guest.tick(session.GIVE_SECONDS + 1)
    guest.game.contributions[-1](payload)
    settle(host, *guests)


def comes_back(host, guests, guest, payload=b"back"):
    """A guest playing apart comes to the host's lot: it gives what it
    played, loads the host's state and joins."""
    guest.leave()
    settle(host, *guests)
    guest.arrive(host.game.zone_number)
    settle(host, *guests)
    all_tick(host, guests)
    guest.game.contributions[-1](payload)
    settle(host, *guests)
    all_tick(host, guests)
    host.game.snapshots[-1](b"save")
    settle(host, *guests)
    load(host, guests, guest)
    all_tick(host, guests)


def versioned(host):
    """Has the host's game refuse a gift of household 5 older than one
    another guest gave since, as household.take_in does."""
    gifts = household.Gifts()
    merge = host.game.merge

    def merge_once(payload, save_id=0, player="", version=None, others=()):
        merge(payload, save_id, player, version, others)
        if gifts.older(5, save_id, version):
            return session.GIFT_OLDER
        gifts.took(5, save_id, version)
        return household.UPDATED

    host.game.merge = merge_once


def look(sim_id):
    return frames.Decoder().feed(frames.control(
        "appearance", sim_id=str(sim_id), look=base64.b64encode(b"look").decode("ascii"), fields=["outfits"]))


class FamiliesTest(unittest.TestCase):
    """Households and sims with several guests: each sim has one player."""

    def setUp(self):
        global RELAY
        relay, RELAY = RELAY, "routed"

        def restore():
            global RELAY
            RELAY = relay

        self.addCleanup(restore)

    def test_a_guest_is_never_handed_another_players_sim(self):
        host, guests = all_in(2)
        first, second = host.session.peers[1], host.session.peers[2]
        self.assertEqual((first.welcomed, second.welcomed), ([222], [333]))
        # The first comes along with two of its household, one of which the
        # second plays.
        first.travellers = [333, 555]
        guests[0].game.sim_id = 223
        all_tick(host, guests)
        self.assertEqual(host.game.welcome_claimed[-1], {333})
        self.assertEqual(first.welcomed, [223, 555])
        self.assertEqual(second.welcomed, [333])
        # Nor is it handed the sim another controls.
        guests[0].game.sim_id = 333
        all_tick(host, guests)
        self.assertEqual(first.welcomed, [223, 555])
        self.assertEqual(second.welcomed, [333])

    def test_a_sim_that_came_along_is_its_players_once_it_takes_control(self):
        host, guests = all_in(2)
        first, second = host.session.peers[1], host.session.peers[2]
        first.travellers = [555]
        guests[0].game.sim_id = 223
        all_tick(host, guests)
        self.assertEqual(first.welcomed, [223, 555])
        guests[1].game.sim_id = 555
        all_tick(host, guests)
        self.assertEqual(first.welcomed, [223])
        self.assertEqual(second.welcomed, [555])
        self.assertIs(host.session._owner(555), second)

    def test_a_guest_that_leaves_takes_only_its_own_sims_off_our_lot(self):
        host, guests = all_in(2)
        first = host.session.peers[1]
        plays_apart(host, guests, guests[0], 8)
        self.assertEqual(host.game.sent_off, [[222]])
        # Over there it plays a sim the second plays here: still the
        # second's, and not claimed by the first.
        guests[0].game.sim_id = 333
        all_tick(host, guests)
        self.assertEqual(first.away, [])
        self.assertEqual(host.game.sent_off, [[222]])
        self.assertEqual(host.session.peers[2].welcomed, [333])
        # Our lot loaded again with it on it: it is still the second's.
        host.leave()
        settle(host, *guests)
        host.arrive()
        settle(host, *guests)
        self.assertEqual(host.game.sent_off, [[222]])

    def test_a_gift_waits_while_we_save_and_hold_for_another_guest(self):
        host, guests = group(2, strangers=(2,))
        first, second = guests
        all_tick(host, guests)
        # The first asks for our state; the second, of a save of its own,
        # brings its household while we save for the first.
        self.assertEqual(len(host.game.snapshots), 1)
        second.game.contributions[-1](b"household")
        settle(host, *guests)
        self.assertEqual(host.game.merged, [])
        self.assertEqual(len(host.session._gifts), 1)
        self.assertIn("guest 1 is loading; your household is taken in once they are in", second.game.notices)
        # Told it is still to come, it waits past the time it gives the host.
        for _ in range(2):
            all_tick(host, guests, session.MERGE_SECONDS / 2 + 10)
        self.assertEqual(second.session._phase, session.GIVING)
        host.game.snapshots[-1](b"save")
        settle(host, *guests)
        # We still hold for the first.
        self.assertEqual(host.game.merged, [])
        load(host, guests, first)
        all_tick(host, guests)
        self.assertEqual(host.game.merged, [(b"household", 2)])
        self.assertEqual(host.session._gifts, [])
        self.assertEqual(second.session._phase, session.ASKING)
        self.assertTrue(first.session.live)

    def test_a_gift_while_we_save_for_its_own_guest_alone_is_taken_at_once(self):
        host, guests = group(1, strangers=(1,))
        all_tick(host, guests)
        guests[0].game.contributions[-1](b"household")
        settle(host, *guests)
        self.assertEqual(host.game.merged, [(b"household", 2)])

    def test_a_waiting_gift_of_a_guest_that_left_is_dropped(self):
        host, guests = group(2, strangers=(2,))
        all_tick(host, guests)
        guests[1].game.contributions[-1](b"household")
        settle(host, *guests)
        self.assertEqual(len(host.session._gifts), 1)
        host.peers_up.discard(2)
        host._relay_says("peer_gone", peer=2)
        self.assertEqual(host.session._gifts, [])
        host.game.snapshots[-1](b"save")
        settle(host, *guests)
        load(host, guests, guests[0])
        all_tick(host, guests)
        self.assertEqual(host.game.merged, [])

    def test_a_gift_from_before_another_guests_gift_is_older(self):
        host, guests = all_in(2)
        versioned(host)
        # Both play the same household apart, from the same state of ours.
        plays_apart(host, guests, guests[0], 8)
        plays_apart(host, guests, guests[1], 9)
        gives_apart(host, guests, guests[0], b"first")
        self.assertEqual(host.game.merged_as[-1][:2], ("p1", ("p1", 0, 1)))
        # What the other player plays, here or elsewhere, keeps our copy of
        # its relationships.
        self.assertIn(333, host.game.merged_as[-1][2])
        self.assertNotIn(222, host.game.merged_as[-1][2])
        gives_apart(host, guests, guests[1], b"second")
        self.assertEqual(host.game.merged_as[-1][:2], ("p2", ("p2", 0, 2)))
        self.assertIn("another player gave your household since you loaded the host's game; the host keeps theirs",
                      guests[1].game.notices)
        # Loading our state again, it first gives what it played, which is
        # still older, and is kept out; then it loads ours.
        guests[1].session.resync()
        settle(host, *guests)
        guests[1].game.contributions[-1](b"before loading")
        settle(host, *guests)
        self.assertEqual(host.game.merged_as[-1][1], ("p2", 0, 3))
        self.assertEqual(guests[1].session._phase, session.ASKING)
        all_tick(host, guests)
        host.game.snapshots[-1](b"lot")
        settle(host, *guests)
        load(host, guests, guests[1])
        all_tick(host, guests)
        # What it gives from there is newer.
        notices = len(guests[1].game.notices)
        gives_apart(host, guests, guests[1], b"third")
        self.assertEqual(host.game.merged[-1], (b"third", 1))
        self.assertEqual(host.game.merged_as[-1][1], ("p2", 3, 4))
        self.assertEqual(len(guests[1].game.notices), notices)

    def test_the_second_guest_apart_on_one_lot_is_told_and_its_gift_not_taken(self):
        host, guests = all_in(3)
        first, second, third = guests
        plays_apart(host, guests, first, 8)
        self.assertFalse(any("already playing" in notice for notice in first.game.notices))
        plays_apart(host, guests, second, 8)
        self.assertIn("guest 1 is already playing this lot; what you play here is not taken into the host's game "
                      "while they are here", second.game.notices)
        # Another lot is the third's alone.
        plays_apart(host, guests, third, 9)
        self.assertFalse(any("already playing" in notice for notice in third.game.notices))
        gives_apart(host, guests, second, b"second")
        self.assertEqual(host.game.merged, [])
        self.assertIn("guest 1 is already playing that lot; the host keeps your household as it has it",
                      second.game.notices)
        gives_apart(host, guests, first, b"first")
        gives_apart(host, guests, third, b"third")
        self.assertEqual(host.game.merged, [(b"first", 1), (b"third", 1)])
        # Once the first has left the lot, the second plays it alone.
        plays_apart(host, guests, first, 10)
        gives_apart(host, guests, second, b"again")
        self.assertEqual(host.game.merged[-1], (b"again", 1))

    def test_a_guest_restyles_only_the_sims_it_plays(self):
        host, guests = all_in(2)
        # The first once played the second's sim on another lot.
        host.session.peers[1].away = [333]
        host.take()
        host.receive(look(333), peer=1)
        self.assertEqual(host.game.applied, [])
        self.assertIn({"type": "appearance_done", "sim_id": "333", "ok": False}, host.controls())
        host.receive(look(333), peer=2)
        host.receive(look(222), peer=1)
        self.assertEqual(host.game.applied, [(333, b"look"), (222, b"look")])

    def test_each_guests_snapshot_is_of_its_own_players_zones(self):
        host, guests = group(3)
        all_tick(host, guests)
        self.assertEqual([place[2] for place in host.game.snapshot_places[-1]], ["p1", "p2", "p3"])

    def test_a_sim_another_guest_was_handed_stays_theirs(self):
        host, guests = all_in(2)
        host.game.households = {222: 3, 333: 3}
        first, second = host.session.peers[1], host.session.peers[2]
        # The first takes control of the sim the second plays.
        guests[0].game.sim_id = 333
        all_tick(host, guests)
        self.assertEqual((first.sim_id, second.sim_id), (333, 333))
        self.assertEqual((first.welcomed, second.welcomed), ([222], [333]))
        host.take()
        dialog = owned(333)
        self.assertIsNone(host.session.outbound(DIALOG, dialog))
        self.assertEqual(host.take_routed(), [(2, (frames.MSG, DIALOG, dialog))])
        # Its commands would act as that sim; the second's do.
        command = (frames.COMMAND, 0, b'{"name":"interactions.choices","args":["1"]}')
        host.receive([command], peer=1)
        self.assertEqual(host.game.executed, [])
        host.receive([command], peer=2)
        self.assertEqual(host.game.executed, [("interactions.choices", ["1"], 333)])
        self.assertEqual(host.game.executed_for, [2])

    def test_a_guest_apart_playing_a_sim_another_plays_here_leaves_it_theirs(self):
        host, guests = all_in(2)
        first, second = host.session.peers[1], host.session.peers[2]
        plays_apart(host, guests, guests[0], 8)
        guests[0].game.sim_id = 333
        all_tick(host, guests)
        self.assertEqual(first.away, [])
        # The second switches sims and back: 333 is still its to play here.
        guests[1].game.sim_id = 334
        all_tick(host, guests)
        guests[1].game.sim_id = 333
        all_tick(host, guests)
        self.assertEqual(second.welcomed, [333])
        self.assertNotIn(333, host.game.welcome_claimed[-1])
        # Leaving, it takes 333 off our lot, not 334.
        plays_apart(host, guests, guests[1], 9)
        self.assertEqual(host.game.sent_off[-1], [333])

    def test_sims_handed_to_a_guest_that_has_gone_are_free_for_another(self):
        host, guests = all_in(2)
        first, second = host.session.peers[1], host.session.peers[2]
        first.travellers = [555]
        guests[0].game.sim_id = 223
        all_tick(host, guests)
        self.assertEqual(first.welcomed, [223, 555])
        host.peers_up.discard(1)
        host._relay_says("peer_gone", peer=1)
        second.travellers = [555]
        guests[1].game.sim_id = 334
        all_tick(host, guests)
        self.assertNotIn(555, host.game.welcome_claimed[-1])
        self.assertEqual(second.welcomed, [334, 555])
        self.assertEqual(first.welcomed, [223])
        host.receive(look(555), peer=2)
        self.assertEqual(host.game.applied, [(555, b"look")])

    def test_a_guest_back_on_our_lot_no_longer_claims_what_it_played_apart(self):
        host, guests = all_in(2)
        first, second = host.session.peers[1], host.session.peers[2]
        plays_apart(host, guests, guests[0], 8)
        self.assertEqual(first.away, [222])
        comes_back(host, guests, guests[0])
        self.assertTrue(guests[0].session.live)
        self.assertEqual((first.welcomed, first.away), ([222], []))
        guests[0].game.sim_id = 224
        all_tick(host, guests)
        guests[1].game.sim_id = 222
        all_tick(host, guests)
        self.assertEqual(host.game.welcome_claimed[-1], {224})
        self.assertEqual(second.welcomed, [222])

    def test_a_guest_restyles_no_sim_another_plays_apart(self):
        host, guests = all_in(2)
        plays_apart(host, guests, guests[0], 8)
        # The second once played the first's sim elsewhere.
        host.session.peers[2].away = [222]
        host.take()
        host.receive(look(222), peer=2)
        self.assertEqual(host.game.applied, [])
        self.assertIn({"type": "appearance_done", "sim_id": "222", "ok": False}, host.controls())
        host.receive(look(222), peer=1)
        self.assertEqual(host.game.applied, [(222, b"look")])

    def test_the_second_guest_apart_on_one_lot_that_comes_to_ours_loads_our_copy(self):
        host, guests = all_in(3)
        first, second = guests[0], guests[1]
        plays_apart(host, guests, first, 8)
        plays_apart(host, guests, second, 8)
        gives_apart(host, guests, second, b"second on lot 8")
        self.assertEqual(host.game.merged, [])
        # It comes to our lot with what it played on lot 8, while the first
        # still plays there.
        second.leave()
        settle(host, *guests)
        second.arrive(host.game.zone_number)
        settle(host, *guests)
        all_tick(host, guests)
        self.assertEqual(second.session._phase, session.SAVING)
        second.game.contributions[-1](b"second on arrival")
        settle(host, *guests)
        self.assertEqual(host.game.merged, [])
        self.assertEqual(host.session.peers[1].place, (1, "", 8))
        # Told the host keeps its household, it loads the host's.
        self.assertEqual(second.session._phase, session.ASKING)
        all_tick(host, guests)
        host.game.snapshots[-1](b"save")
        settle(host, *guests)
        load(host, guests, second)
        self.assertTrue(second.session.live)
        # Once it loaded ours, what it plays elsewhere is its own.
        plays_apart(host, guests, second, 10)
        gives_apart(host, guests, second, b"second on lot 10")
        self.assertEqual(host.game.merged, [(b"second on lot 10", 1)])

    def test_a_guest_whose_load_failed_gives_as_new_as_what_it_plays(self):
        host, guests = all_in(2)
        versioned(host)
        plays_apart(host, guests, guests[0], 8)
        plays_apart(host, guests, guests[1], 9)
        gives_apart(host, guests, guests[0], b"first")
        # The second asks again, gives what it played, which is older, and
        # its game cannot load what we saved after the first's gift.
        guests[1].session.resync()
        settle(host, *guests)
        guests[1].game.contributions[-1](b"before loading")
        settle(host, *guests)
        all_tick(host, guests)
        guests[1].game.adopt_error = ValueError("broken")
        host.game.snapshots[-1](b"lot")
        settle(host, *guests)
        self.assertEqual(guests[1].session._phase, session.IDLE)
        # It plays on from what it had: older than the first's gift still.
        notices = len(guests[1].game.notices)
        gives_apart(host, guests, guests[1], b"second")
        self.assertEqual(host.game.merged_as[-1][1][:2], ("p2", 0))
        self.assertEqual(len(guests[1].game.notices), notices + 1)

    def test_a_save_loaded_from_the_menu_forgets_what_guests_gave(self):
        host, guests = all_in(2)
        self.assertTrue(all(peer.bases for peer in host.session.peers.values()))
        # A travel, or a load of our lot again, keeps it.
        host.leave()
        host.arrive()
        settle(host, *guests)
        self.assertEqual(host.game.gifts_forgotten, 0)
        self.assertTrue(all(peer.bases for peer in host.session.peers.values()))
        host.leave()
        host.game.by_hand = True
        host.arrive()
        settle(host, *guests)
        self.assertEqual(host.game.gifts_forgotten, 1)
        self.assertEqual([peer.bases for _, peer in sorted(host.session.peers.items())], [{}, {}])


if __name__ == "__main__":
    unittest.main()
