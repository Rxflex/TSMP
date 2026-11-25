"""Build operations shared between two games: journal, catch-up, replay and
what the two agree on. Two `Sync` objects stand in for the two games, with
a fake recorder for each and frames carried between them by hand."""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import frames, native  # noqa: E402

LOT = (77, 1001)  # (save id, zone id)
OTHER_LOT = (77, 1002)


def op(name, limit_is_size=True):
    return native.Op(native.BUILD_OP, 1, 2, 12, limit_is_size, name.encode())


class FakeRecorder:
    """What `Sync` needs of `native.Recorder`."""

    def __init__(self):
        self.sync = True
        self.ready = True
        self.made = []  # what this game's client did, not yet taken
        self.oversized = []
        self.replayed = []
        self.forgotten = 0

    def take(self):
        made, sizes = self.made, self.oversized
        self.made, self.oversized = [], []
        return made, sizes

    def replay(self, op):
        if not self.ready:
            return False
        self.replayed.append(op)
        return True

    def forget(self):
        self.forgotten += 1

    def start(self, receive=False):
        return 0


class Game:
    """One side: a Sync, its recorder, and where it is."""

    def __init__(self, run, known=True):
        self.sync = native.Sync(lambda: known, run=run)
        self.recorder = self.sync.recorder = FakeRecorder()
        self.place = LOT
        self.live = False
        self.now = 0.0
        self.outbox = []
        self.notes = []

    def tick(self):
        self.outbox += self.sync.tick(self.place, self.live, self.now)
        self.notes += self.sync.notes()

    def join(self):
        self.live = True
        self.sync.greet(self.place)

    def replayed(self):
        return [made.data.decode() for made in self.recorder.replayed]

    def noted(self, text):
        return [line for line in self.notes if text in line]


def deliver(source, target):
    """Hand everything `source` sent to `target`, as live.py does."""
    decoder = frames.Decoder()
    for kind, ident, payload in decoder.feed(b"".join(source.outbox)):
        if kind == frames.CONTROL:
            target.sync.control(frames.parse_json(payload))
        elif kind == frames.NATIVE:
            target.sync.incoming(ident, payload)
    del source.outbox[:]


def sent(game):
    """The operations in what `game` is about to send, as Shared."""
    decoder = frames.Decoder()
    return [
        native.Shared.unpack(ident, payload)
        for kind, ident, payload in decoder.feed(b"".join(game.outbox))
        if kind == frames.NATIVE
    ]


def controls(game):
    decoder = frames.Decoder()
    return [
        frames.parse_json(payload)
        for kind, _, payload in decoder.feed(b"".join(game.outbox))
        if kind == frames.CONTROL
    ]


class Pair(unittest.TestCase):
    def setUp(self):
        self.host = Game(run=1)
        self.guest = Game(run=2)

    def tick(self, rounds=3):
        """Let both run and exchange what they send, a few times over."""
        for _ in range(rounds):
            self.host.tick()
            self.guest.tick()
            deliver(self.host, self.guest)
            deliver(self.guest, self.host)

    def join(self):
        self.host.join()
        self.guest.join()
        self.tick()


class CatchUpTest(Pair):
    def test_edits_made_before_the_session_is_live_reach_the_other_game(self):
        # The host builds while the guest is still loading.
        self.host.recorder.made += [op("wall"), op("floor")]
        self.tick()
        self.assertEqual(self.guest.replayed(), [])
        self.join()
        self.assertEqual(self.guest.replayed(), ["wall", "floor"])
        # New edits follow, both ways.
        self.host.recorder.made.append(op("door"))
        self.guest.recorder.made.append(op("paint"))
        self.tick()
        self.assertEqual(self.guest.replayed(), ["wall", "floor", "door"])
        self.assertEqual(self.host.replayed(), ["paint"])
        self.assertEqual(self.guest.sync.applied[LOT], (1, 3))
        self.assertEqual(self.host.sync.applied[LOT], (2, 1))

    def test_edits_made_during_a_link_drop_are_caught_up_and_none_twice(self):
        self.join()
        self.host.recorder.made.append(op("one"))
        self.tick()
        self.host.live = self.guest.live = False
        self.host.recorder.made.append(op("two"))
        self.tick()
        # Each says again whether it takes part once the session is back.
        self.assertEqual(self.host.sync.state(), "waiting for the other game")
        self.join()
        self.host.recorder.made.append(op("three"))
        self.tick()
        self.assertEqual(self.guest.replayed(), ["one", "two", "three"])

    def test_edits_on_another_lot_wait_for_that_lot(self):
        self.join()
        self.host.live = self.guest.live = False
        self.host.place = OTHER_LOT
        self.host.recorder.made.append(op("elsewhere"))
        self.tick()
        self.host.place = LOT
        self.host.recorder.made.append(op("here"))
        self.join()
        self.assertEqual(self.guest.replayed(), ["here"])
        # Both go to the other lot.
        self.host.live = self.guest.live = False
        self.tick()
        self.host.place = self.guest.place = OTHER_LOT
        self.join()
        self.assertEqual(self.guest.replayed(), ["here", "elsewhere"])

    def test_a_frame_for_another_lot_is_not_replayed(self):
        self.join()
        shared = native.Shared(OTHER_LOT[1], OTHER_LOT[0], 1, 1, False, op("elsewhere"))
        self.guest.sync.incoming(native.BUILD_OP, shared.frame()[9:])
        self.guest.tick()
        self.assertEqual(self.guest.replayed(), [])
        self.assertTrue(self.guest.noted("made on another lot or save"))
        self.assertNotIn(OTHER_LOT, self.guest.sync.applied)

    def test_a_duplicate_is_ignored(self):
        self.join()
        self.host.recorder.made.append(op("wall"))
        self.host.tick()
        self.host.outbox *= 2
        deliver(self.host, self.guest)
        self.guest.tick()
        self.assertEqual(self.guest.replayed(), ["wall"])
        self.assertEqual(self.guest.sync.duplicates, 1)

    def test_an_operation_out_of_order_is_asked_for_again(self):
        self.join()
        self.host.recorder.made += [op("one"), op("two")]
        self.host.tick()
        # The first is lost on the way.
        del self.host.outbox[0]
        deliver(self.host, self.guest)
        self.guest.tick()
        self.assertEqual(self.guest.replayed(), [])
        (have,) = controls(self.guest)
        self.assertEqual((have["type"], have["seq"]), ("native_have", 0))
        self.tick()
        self.assertEqual(self.guest.replayed(), ["one", "two"])

    def test_a_game_started_again_numbers_from_one_and_is_not_ignored(self):
        self.join()
        self.host.recorder.made += [op("one"), op("two")]
        self.tick()
        self.host.live = self.guest.live = False
        self.tick()
        # The host's game is restarted: a new run, a new journal.
        self.host = Game(run=9)
        self.host.recorder.made.append(op("again"))
        self.join()
        self.assertEqual(self.guest.replayed(), ["one", "two", "again"])
        self.assertEqual(self.guest.sync.applied[LOT], (9, 1))

    def test_a_greeting_is_answered_with_one(self):
        # Only the guest saw the session become live; the host answers.
        self.host.tick()
        self.host.sync.control({"type": "native_have", "save": "77", "zone": "1001", "run": "0", "seq": 0, "want": True})
        self.host.tick()
        answer = controls(self.host)
        self.assertEqual([message["type"] for message in answer], ["native", "native_have"])
        self.assertEqual((answer[1]["zone"], answer[1]["save"], answer[1]["want"]), ("1001", "77", False))

    def test_more_than_the_queue_holds_arrive_at_once(self):
        self.join()
        self.host.recorder.made += [op(str(index)) for index in range(native.PENDING_OPS)]
        self.host.tick()
        self.host.recorder.made += [op("more")]
        self.host.tick()
        deliver(self.host, self.guest)
        self.guest.tick()
        self.assertEqual(len(self.guest.replayed()), native.PENDING_OPS + 1)


    def test_a_catch_up_larger_than_the_queue_while_replay_waits_arrives_whole(self):
        # The guest's zone is not ready to replay yet when it joins.
        count = native.PENDING_OPS + 40
        self.host.recorder.made += [op(str(index)) for index in range(count)]
        self.host.tick()
        self.guest.recorder.ready = False
        self.join()
        self.tick(5)
        self.guest.recorder.ready = True
        self.tick(10)
        self.assertEqual(self.guest.replayed(), [str(index) for index in range(count)])


class PendingTest(Pair):
    def test_an_operation_waits_until_it_can_be_replayed(self):
        self.guest.recorder.ready = False
        self.join()
        self.host.recorder.made += [op("one"), op("two")]
        self.tick()
        self.assertEqual(self.guest.replayed(), [])
        self.assertIn("waiting 2", self.guest.sync.describe()[0])
        self.guest.recorder.ready = True
        self.tick(1)
        self.assertEqual(self.guest.replayed(), ["one", "two"])

    def test_waiting_too_long_drops_and_asks_again(self):
        self.guest.recorder.ready = False
        self.join()
        self.host.recorder.made.append(op("one"))
        self.tick()
        self.guest.now = native.PENDING_SECONDS + 1
        self.guest.tick()
        self.assertTrue(self.guest.noted("dropped before replay"))
        self.assertEqual([c["type"] for c in controls(self.guest)], ["native_have"])
        self.guest.recorder.ready = True
        self.tick()
        self.assertEqual(self.guest.replayed(), ["one"])

    def test_a_zone_change_drops_what_waits(self):
        self.guest.recorder.ready = False
        self.join()
        self.host.recorder.made.append(op("one"))
        self.tick()
        forgotten = self.guest.recorder.forgotten
        self.guest.place = None
        self.guest.tick()
        self.assertTrue(self.guest.noted("dropped before replay: the zone changed"))
        self.assertEqual(self.guest.recorder.forgotten, forgotten + 1)
        self.assertIn("waiting 0", self.guest.sync.describe()[0])

    def test_the_session_ending_drops_what_waits(self):
        self.guest.recorder.ready = False
        self.join()
        self.host.recorder.made.append(op("one"))
        self.tick()
        self.guest.live = False
        self.guest.tick()
        self.assertTrue(self.guest.noted("dropped before replay: the session is no longer live"))

    def test_without_a_recorder_an_operation_waits(self):
        self.guest.sync.recorder = None
        self.join()
        self.host.recorder.made.append(op("one"))
        self.tick()
        self.assertIn("waiting 1", self.guest.sync.describe()[0])
        self.guest.sync.recorder = self.guest.recorder
        self.tick(1)
        self.assertEqual(self.guest.replayed(), ["one"])

    def test_the_queue_is_bounded(self):
        pending = native.Pending(max_ops=2, max_bytes=1000, max_seconds=5)
        shared = native.Shared(1, 2, 3, 1, False, op("x"))
        self.assertTrue(pending.add(shared, 0))
        self.assertTrue(pending.add(shared._replace(seq=2), 0))
        self.assertFalse(pending.add(shared._replace(seq=3), 0))
        self.assertEqual(pending.last(0), 2)
        self.assertFalse(pending.expired(5))
        self.assertTrue(pending.expired(6))
        self.assertFalse(native.Pending(max_bytes=native.OP_COST).add(shared, 0))


class NegotiationTest(Pair):
    def test_nothing_is_shared_unless_both_take_part(self):
        self.guest.sync.user_off = True
        self.host.recorder.made.append(op("wall"))
        self.join()
        self.assertEqual(self.host.sync.sent, 0)
        self.assertEqual(self.guest.replayed(), [])
        self.assertEqual(self.host.sync.state(), "off in the other game")
        self.assertEqual(self.guest.sync.state(), "off here")
        self.assertIn("off in the other game", self.host.sync.describe()[0])
        self.assertEqual(len(self.host.noted("sync with the other game: off in the other game")), 1)
        self.tick()
        self.assertEqual(len(self.host.noted("sync with the other game: off in the other game")), 1)
        # Switched on again: the guest says so and catches up.
        self.guest.sync.user_off = False
        self.tick()
        self.assertEqual(self.guest.replayed(), ["wall"])
        self.assertTrue(self.host.noted("sync with the other game: on"))

    def test_an_unknown_build_does_not_take_part(self):
        self.guest = Game(run=2, known=False)
        self.join()
        self.assertEqual(self.host.sync.state(), "off in the other game")

    def test_a_game_that_never_said_does_not_take_part(self):
        self.host.live = True
        self.host.recorder.made.append(op("wall"))
        self.host.sync.control({"type": "native_have", "save": "77", "zone": "1001", "run": "0", "seq": 0})
        self.host.tick()
        self.assertEqual(sent(self.host), [])
        self.assertEqual(self.host.sync.state(), "waiting for the other game")

    def test_another_format_does_not_take_part(self):
        self.host.sync.control({"type": "native", "ok": True, "format": native.FORMAT + 1})
        self.assertEqual(self.host.sync.state(), "off in the other game")

    def test_a_malformed_have_is_noted(self):
        self.host.sync.control({"type": "native_have", "zone": "x"})
        self.assertTrue(self.host.sync.notes())


    def test_edits_refused_while_off_are_sent_once_on_again(self):
        self.join()
        self.guest.recorder.made.append(op("wall"))
        self.guest.tick()
        # Turned off in the same tick as the edit reaches the host.
        self.guest.sync.user_off = True
        self.guest.tick()
        deliver(self.guest, self.host)
        self.host.tick()
        self.assertEqual(self.host.replayed(), [])
        self.guest.sync.user_off = False
        self.tick(5)
        self.assertEqual(self.host.replayed(), ["wall"])


class JournalTest(Pair):
    def test_the_oldest_are_dropped_and_the_peer_is_told(self):
        self.host.sync.journal = native.Journal(self.host.sync._note, max_ops=2)
        self.host.recorder.made += [op("one"), op("two"), op("three")]
        self.tick()
        self.assertTrue(self.host.noted("is full: 1 oldest edits dropped"))
        self.join()
        self.assertEqual(self.guest.replayed(), ["two", "three"])
        self.assertTrue(self.host.noted("the other game lacks 1 edits"))
        self.assertTrue(self.guest.noted("1 edits of the other game on this lot are lost"))

    def test_numbers_and_limits(self):
        notes = []
        journal = native.Journal(notes.append, max_ops=10, max_bytes=3 * (native.OP_COST + 3))
        for name in ("one", "two", "six", "ten"):
            journal.add(LOT, op(name))
        self.assertEqual(journal.add(OTHER_LOT, op("own")), 1)
        self.assertEqual([seq for seq, _ in journal.after(LOT, 0)], [2, 3, 4])
        self.assertEqual([seq for seq, _ in journal.after(LOT, 3)], [4])
        self.assertEqual(journal.after(LOT, 4), [])
        self.assertEqual(journal.after((0, 0), 0), [])
        self.assertEqual(len(notes), 1)
        # At least one even when larger than the limit, then no more than fits.
        self.assertEqual(len(journal.after(LOT, 0, max_bytes=1)), 1)
        self.assertEqual(len(journal.after(LOT, 0, max_ops=2)), 2)

    def test_edits_made_while_no_zone_runs_are_not_kept(self):
        self.host.place = None
        self.host.recorder.made.append(op("lost"))
        self.host.tick()
        self.assertTrue(self.host.noted("while no zone runs"))
        self.assertEqual(self.host.sync.recorded, 0)

    def test_an_operation_too_large_to_share_is_logged(self):
        self.host.recorder.oversized.append(2000000)
        self.host.tick()
        self.assertEqual(self.host.noted("too large"), ["build op too large to share, 2000000 bytes"])
        self.assertIn("too large 1", self.host.sync.describe()[0])

    def test_a_catch_up_is_spread_over_ticks(self):
        for index in range(native.SEND_OPS + 5):
            self.host.recorder.made.append(op(str(index)))
        self.host.join()
        self.guest.join()
        self.host.tick()
        deliver(self.host, self.guest)
        self.guest.tick()
        deliver(self.guest, self.host)
        self.host.tick()
        self.assertEqual(len(sent(self.host)), native.SEND_OPS)
        self.tick()
        self.assertEqual(len(self.guest.replayed()), native.SEND_OPS + 5)


class PayloadTest(unittest.TestCase):
    def test_header_and_round_trip(self):
        shared = native.Shared(0x1122334455667788, 0x99, 7, 3, True, op("wall", limit_is_size=False))
        kind, call, payload = frames.Decoder().feed(shared.frame())[0]
        self.assertEqual((kind, call), (frames.NATIVE, native.BUILD_OP))
        self.assertEqual(
            struct.unpack_from("<BBQQII", payload), (1, native.FLAG_AFTER_GAP, 0x1122334455667788, 0x99, 7, 3)
        )
        # Then what the call itself needs, as before.
        self.assertEqual(struct.unpack_from("<III", payload, 26), (1, 2, 12))
        self.assertEqual(payload[38:], b"wall")
        self.assertEqual(native.Shared.unpack(call, payload), shared)
        self.assertTrue(native.Shared.unpack(call, shared._replace(op=op("w")).frame()[9:]).op.limit_is_size)

    def test_what_is_not_read(self):
        payload = native.Shared(1, 2, 3, 4, False, op("wall")).frame()[9:]
        self.assertIsNone(native.Shared.unpack(0x1234, payload))
        self.assertIsNone(native.Shared.unpack(native.BUILD_OP, b"\x02" + payload[1:]))
        self.assertIsNone(native.Shared.unpack(native.BUILD_OP, payload[:20]))


class CommandTest(unittest.TestCase):
    """The cheat's off holds against live mode's own start."""

    def setUp(self):
        sync = native._sync
        saved = (sync.recorder, sync.enabled, sync.user_off)

        def restore():
            sync.recorder, sync.enabled, sync.user_off = saved

        self.addCleanup(restore)
        sync.recorder = None
        sync.enabled = True

    def test_off_holds_until_another_mode(self):
        self.assertTrue(native.wanted())
        self.assertEqual(native.command("off"), ["restored 0 objects"])
        self.assertFalse(native.wanted())
        self.assertTrue(native._sync.user_off)
        # Outside the game nothing can start, but the player's choice counts.
        native.command("sync")
        self.assertFalse(native._sync.user_off)
        self.assertTrue(native.wanted())

    def test_config_keeps_it_off(self):
        native.configure(False)
        self.assertFalse(native.wanted())

    def test_not_wanted_once_syncing(self):
        native._sync.recorder = FakeRecorder()
        self.assertFalse(native.wanted())


if __name__ == "__main__":
    unittest.main()
