import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import frames, session, wire  # noqa: E402

VIEW_UPDATE = 11
PIE_MENU = 6
INTERACTABLE = 5
SAVE_COMPLETE = 181
TRAVEL = 239
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


class FakeGame:
    def __init__(self):
        self.zone = False
        self.sim_id = 0
        self.delivered = []
        self.executed = []
        self.during_execute = None

    def in_zone(self):
        return self.zone

    def active_sim_id(self):
        return self.sim_id

    def deliver(self, msg_id, data):
        self.delivered.append((msg_id, data))

    def execute(self, name, args, sim_id):
        self.executed.append((name, args, sim_id))
        if self.during_execute is not None:
            self.during_execute()


class Player:
    """One game with its session; `sent` collects the frames it emits."""

    def __init__(self, role):
        self.game = FakeGame()
        self.sent = []
        self.logged = []
        consts = session.Consts(
            VIEW_UPDATE,
            PIE_MENU,
            INTERACTABLE,
            SET_SIM_ACTIVE,
            host_only=[SAVE_COMPLETE, TRAVEL],
            guest_local=[SAVE_COMPLETE],
        )
        self.session = session.Session(self.game, consts, self._send, log=self.logged.append)
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
        self.session.frame(frames.CONTROL, 0, frames.Decoder().feed(frames.control("link", remotes=remotes))[0][2])


def joined():
    """A host and a guest, both in a zone, with the relay's link events
    delivered and their frames exchanged until quiet."""
    host, guest = Player(session.HOST), Player(session.GUEST)
    host.game.zone = guest.game.zone = True
    guest.game.sim_id = 222
    host.session.bridge_up(session.HOST)
    guest.session.bridge_up(session.GUEST)
    host.link(1)
    guest.link(1)
    settle(host, guest)
    return host, guest


def settle(host, guest):
    for _ in range(10):
        to_guest, to_host = host.take(), guest.take()
        if not to_guest and not to_host:
            return
        guest.receive(to_guest)
        host.receive(to_host)
    raise AssertionError("the sessions never went quiet")


class HandshakeTest(unittest.TestCase):
    def test_announces_state_when_the_bridge_comes_up(self):
        host = Player(session.HOST)
        host.session.bridge_up(session.HOST)
        self.assertEqual(host.controls(), [{"type": "state", "role": "host", "in_zone": False, "want": True}])
        self.assertFalse(host.session.live)

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
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)
        self.assertEqual(host.session.remote_sim_id, 9)

    def test_announces_zone_changes_on_tick(self):
        host = Player(session.HOST)
        host.session.bridge_up(session.HOST)
        host.take()
        host.session.tick()
        self.assertEqual(host.take(), [])
        host.game.zone = True
        host.session.tick()
        self.assertEqual(host.controls(), [{"type": "state", "role": "host", "in_zone": True}])

    def test_live_once_both_are_in_a_zone(self):
        host, guest = joined()
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)
        self.assertEqual(host.session.remote_sim_id, 222)

    def test_not_live_until_the_other_game_loads(self):
        host, guest = Player(session.HOST), Player(session.GUEST)
        host.game.zone = True
        host.session.bridge_up(session.HOST)
        guest.session.bridge_up(session.GUEST)
        host.link(1)
        guest.link(1)
        settle(host, guest)
        self.assertFalse(host.session.live)
        self.assertFalse(guest.session.live)

        guest.game.zone = True
        guest.game.sim_id = 5
        guest.session.tick()
        settle(host, guest)
        self.assertTrue(host.session.live)
        self.assertTrue(guest.session.live)
        self.assertEqual(host.session.remote_sim_id, 5)

    def test_losing_the_other_launcher_ends_the_session(self):
        host, guest = joined()
        host.link(0)
        self.assertFalse(host.session.live)
        self.assertEqual(host.session.remote_sim_id, 0)

    def test_gone_ends_the_session_until_the_game_is_back(self):
        host, guest = joined()
        guest.session.frame(frames.CONTROL, 0, b'{"type":"gone"}')
        self.assertFalse(guest.session.live)

        # The host's game restarts: the relay links again and it announces.
        guest.link(1)
        guest.take()
        guest.session.frame(frames.CONTROL, 0, b'{"type":"state","role":"host","in_zone":true}')
        self.assertTrue(guest.session.live)
        self.assertEqual(guest.controls(), [{"type": "active_sim", "sim_id": "222"}])

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

    def test_guest_reports_a_new_selection(self):
        host, guest = joined()
        guest.session.tick()
        self.assertEqual(guest.take(), [])
        guest.game.sim_id = 333
        guest.session.tick()
        settle(host, guest)
        self.assertEqual(host.session.remote_sim_id, 333)


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
        self.assertEqual(host.take(), [])

    def test_frames_are_ignored_while_not_live(self):
        host = Player(session.HOST)
        host.session.bridge_up(session.HOST)
        host.session.frame(frames.COMMAND, 0, b'{"name":"interactions.select","args":[]}')
        self.assertEqual(host.game.executed, [])


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


if __name__ == "__main__":
    unittest.main()
