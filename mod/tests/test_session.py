import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import frames, session, transfer, wire  # noqa: E402

VIEW_UPDATE = 11
PIE_MENU = 6
INTERACTABLE = 5
SAVE_COMPLETE = 181
TRAVEL = 239
DIALOG = 40
NOTIFICATION = 41
SET_SIM_ACTIVE = 21
OTHER_MSG = 40


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
        self.during_execute = None
        self.snapshots = []
        self.snapshot_zones = []
        self.zone_number = 7
        self.lot_name = ""
        self.adopted = []
        self.adopt_error = None
        # The game reloads the zone from its own save instead.
        self.loads_its_own = False
        self.put = 0
        self.save = 1
        self.contributions = []
        self.merged = []
        self.merge_error = None
        self.welcomed = []
        self.refuse_sims = ()
        self.sent_off = []
        self.joined_sims = []
        self.notices = []
        self.reloads = 0
        self.pauses = 0
        self.households = {}

    def in_zone(self):
        return self.zone

    def zone_id(self):
        return self.zone_number

    def lot(self):
        return self.lot_name

    def active_sim_id(self):
        return self.sim_id

    def deliver(self, msg_id, data):
        self.delivered.append((msg_id, data))

    def execute(self, name, args, sim_id):
        self.executed.append((name, args, sim_id))
        if self.during_execute is not None:
            self.during_execute()

    def snapshot(self, done, zone_id=0, lot=""):
        self.snapshots.append(done)
        self.snapshot_zones.append(zone_id)

    def adopt(self, payload):
        if self.adopt_error is not None:
            raise self.adopt_error
        self.adopted.append(payload)
        if not self.loads_its_own:
            self.put += 1

    def adoptions(self):
        return self.put

    def reload(self):
        self.reloads += 1

    def save_id(self):
        return self.save

    def contribute(self, done):
        self.contributions.append(done)

    def merge(self, payload, save_id=0):
        if self.merge_error is not None:
            raise self.merge_error
        self.merged.append((payload, save_id))
        return "took in"

    def welcome(self, sim_id, before=()):
        if sim_id in self.refuse_sims:
            return []
        self.welcomed.append(sim_id)
        return [sim_id]

    def send_off(self, sim_ids):
        self.sent_off.append(list(sim_ids))

    def joined(self, sim_id):
        self.joined_sims.append(sim_id)

    def notify(self, text):
        self.notices.append(text)
        return text

    def pause(self):
        self.pauses += 1

    def same_household(self, a, b):
        return self.households.get(a, 1) == self.households.get(b, 1)


class Player:
    """One game with its session; `sent` collects the frames it emits."""

    def __init__(self, role):
        self.game = FakeGame()
        self.sent = []
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
        )
        self.session = session.Session(self.game, consts, self._send, log=self.logged.append, clock=lambda: self.now)
        self.role = role

    def _send(self, frame):
        self.sent += frames.Decoder().feed(frame)

    def take(self):
        sent, self.sent = self.sent, []
        return sent

    def controls(self):
        return [frames.parse_json(payload) for kind, _, payload in self.take() if kind == frames.CONTROL]

    def receive(self, sent):
        for kind, ident, payload in sent:
            self.session.frame(kind, ident, payload)

    def link(self, remotes):
        self.control("link", remotes=remotes)

    def control(self, kind, **fields):
        self.session.frame(frames.CONTROL, 0, frames.Decoder().feed(frames.control(kind, **fields))[0][2])

    def tick(self, seconds=0.0):
        self.now += seconds
        self.session.tick()

    def leave(self):
        self.game.zone = False
        self.tick()

    def arrive(self, lot=None):
        if lot is not None:
            self.game.zone_number = lot
        self.game.zone = True
        self.tick()


def both(host, guest, seconds=0.0):
    host.tick(seconds)
    guest.tick(seconds)
    settle(host, guest)


def connected(guest_save=1, guest_lot=7):
    """A host and a guest in a zone each, with the relay's link events
    delivered and their frames exchanged until quiet."""
    host, guest = Player(session.HOST), Player(session.GUEST)
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


def joined():
    """Connected, and the guest has loaded the host's state once, as every
    guest does on arrival."""
    host, guest = connected()
    reloads_from_host(host, guest)
    return host, guest


def strangers():
    """A host and a guest on the same lot, each in a save of their own."""
    return connected(guest_save=2)


def owned(sim_id):
    """A message whose field 5 names the sim it belongs to."""
    out = bytearray()
    wire.put_field(out, 1, wire.VARINT, 9)
    wire.put_field(out, 5, wire.VARINT, sim_id)
    return bytes(out)


def settle(host, guest):
    for _ in range(100):
        to_guest, to_host = host.take(), guest.take()
        if not to_guest and not to_host:
            return
        guest.receive(to_guest)
        host.receive(to_host)
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


def asked(player):
    return [payload for kind, _, payload in player.sent if kind == frames.CONTROL and b'"resync"' in payload]


class HandshakeTest(unittest.TestCase):
    def test_announces_state_when_the_bridge_comes_up(self):
        host = Player(session.HOST)
        host.session.bridge_up(session.HOST)
        self.assertEqual(host.controls(), [{"type": "state", "role": "host", "in_zone": False, "v": 2, "want": True}])
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
        self.assertEqual(host.session.remote_sim_id, 222)
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
        self.assertEqual(host.session.remote_sim_id, 9)

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
        self.assertEqual(host.session.remote_sim_id, 5)

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
        self.assertEqual(host.session.remote_sim_id, 333)

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
        self.assertTrue(guest.session.stale)


class LinkTest(unittest.TestCase):
    def test_a_lost_link_holds_the_guest_and_it_loads_the_host_again_once_back(self):
        host, guest = joined()
        host.link(0)
        guest.link(0)
        self.assertFalse(host.session.live)
        self.assertFalse(guest.session.live)
        self.assertEqual(host.session.remote_sim_id, 0)
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
        self.assertIsNotNone(host.session._transfers.outgoing(1))
        # The guest's launcher dialled again; the host's saw no gap.
        host.link(1)
        self.assertIsNone(host.session._transfers.outgoing(1))

    def test_a_transfer_nobody_takes_is_dropped(self):
        host, guest = connected()
        both(host, guest)
        host.game.snapshots[0](b"x" * (transfer.WINDOW * 2))
        host.take()
        host.tick(session.STALL_SECONDS + 1)
        self.assertIsNone(host.session._transfers.outgoing(1))

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


    def test_a_screen_for_the_guests_sim_does_not_take_the_hosts(self):
        host, guest = joined()
        host.game.sim_id = 111
        self.assertFalse(host.session.client_cheat("sims.exit2cas 222 1 0"))
        self.assertTrue(host.session.client_cheat("sims.exit2cas 111 1 0"))
        settle(host, guest)
        self.assertTrue(any("Create-a-Sim" in notice for notice in guest.game.notices))


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
        self.assertTrue(host.session.holding)
        answers(host, guest)
        loads(host, guest)
        self.assertTrue(guest.session.apart)
        host.tick()
        self.assertFalse(host.session.holding)
        self.assertTrue(host.session.command("clock.setspeed", ["1"]))

    def test_the_host_is_not_held_once_its_save_failed(self):
        host, guest = connected()
        both(host, guest)
        host.game.snapshots[0](None)
        settle(host, guest)
        self.assertFalse(host.session.holding)
        # Every try of the guest's holds again, and only for that try.
        both(host, guest, session.RETRY_SECONDS[0] + 1)
        gives(host, guest)
        self.assertTrue(host.session.holding)

    def test_a_guest_that_gets_in_after_the_hold_lapsed_loads_again(self):
        host, guest = connected()
        both(host, guest)
        answers(host, guest)
        guest.leave()
        host.tick(session.HOLD_SECONDS + 1)
        self.assertFalse(host.session.holding)
        # The host's player may play on now; the guest's state is older.
        guest.game.save = host.game.save
        guest.arrive()
        settle(host, guest)
        self.assertFalse(guest.session.live)
        self.assertTrue(guest.session.stale)
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)
        self.assertTrue(host.session.live)


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
        guest.tick(session.GIVE_SECONDS - 1)
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

    def test_a_gift_saved_after_the_guest_came_back_is_not_sent(self):
        host, guest = joined()
        self.moves(guest, host, 8)
        reloads_from_host(host, guest)
        guest.tick(session.GIVE_SECONDS + 1)
        self.moves(guest, host, 7)
        guest.tick()
        # The gift's save ends after the one for joining began.
        guest.game.contributions[0](b"gift")
        guest.game.contributions[1](b"household")
        settle(host, guest)
        self.assertEqual([payload for payload, _ in host.game.merged], [b"household"])
        reloads_from_host(host, guest)
        self.assertTrue(guest.session.live)

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
        self.assertEqual(host.session.remote_sim_id, 222)

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
        host, guest = joined()
        host_reloads(host, guest)
        answers(host, guest, b"x")
        guest.tick(session.RELOAD_SECONDS - 1)
        self.assertTrue(guest.session.stale)
        guest.tick(2)
        self.assertFalse(guest.session.stale)

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


if __name__ == "__main__":
    unittest.main()
