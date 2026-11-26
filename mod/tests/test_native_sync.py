"""Build operations shared between two games: journal, catch-up, replay and
what the two agree on. Two `Sync` objects stand in for the two games, with
a fake recorder for each and frames carried between them by hand."""

import collections
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


WALL_OP = 0xC6BE180D
BULLDOZE_OP = 0x6D016A95


def typed(kind, name="op"):
    """An operation of the op type `kind`, which is what a refusal names:
    the first u32 after the stream's header, which has been read."""
    return native.Op(native.BUILD_OP, 1, 2, 12, True, bytes(12) + struct.pack("<I", kind) + name.encode())


class FakeRecorder:
    """What `Sync` needs of `native.Recorder`."""

    def __init__(self):
        self.sync = True
        self.ready = True
        self.made = []  # what this game's client did, not yet taken
        self.oversized = []
        self.replayed = []
        self.forgotten = 0
        self.lot_data = None
        self.lot_loads = 0
        self.failures = collections.deque()

    def load(self, lot=0x3B4AB2C0, size=104065, crc=0x1234):
        """The simulation sent its client the lot data of a zone."""
        self.lot_data = (lot, size, crc)
        self.lot_loads += 1

    def refuse(self, kind, code=0x1001):
        """The simulation refused an operation of type `kind`."""
        self.failures.append((code, kind))

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

    def __init__(self, run, known=True, cross=False):
        self.sync = native.Sync(lambda: known, run=run)
        self.sync.cross = cross
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


class CrossSaveTest(Pair):
    """Families from saves of their own: each game's walls come from its own
    save, so edits are only shared on a lot both loaded the same."""

    def setUp(self):
        self.host = Game(run=1, cross=True)
        self.guest = Game(run=2, cross=True)

    def test_different_walls_share_nothing_and_say_so_once(self):
        self.host.recorder.load(size=104065, crc=0x1111)
        self.guest.recorder.load(size=104858, crc=0x2222)
        self.host.recorder.made.append(op("wall"))
        self.host.tick()
        self.join()
        self.host.recorder.made.append(op("floor"))
        self.tick()
        self.assertEqual(sent(self.host), [])
        self.assertEqual(self.host.sync.sent, 0)
        self.assertEqual(self.guest.replayed(), [])
        self.assertFalse(self.host.sync.active)
        self.assertEqual(self.host.sync.state(), native.DIFFERENT_WALLS)
        self.assertIn(native.DIFFERENT_WALLS, self.guest.sync.describe()[0])
        for game in (self.host, self.guest):
            self.assertEqual(len(game.noted(native.DIFFERENT_WALLS)), 1)
            self.assertTrue(game.noted("walls of zone 1001: here 3b4ab2c0-"))
            self.assertTrue(game.noted("(different)"))
        # An edit that is sent all the same is not replayed.
        shared = native.Shared(LOT[1], LOT[0], 1, 1, False, op("wall"))
        self.guest.sync.incoming(native.BUILD_OP, shared.frame()[9:])
        self.guest.tick()
        self.assertEqual(self.guest.replayed(), [])
        self.assertTrue(self.guest.noted("not replayed: build mode sync is " + native.DIFFERENT_WALLS))

    def test_the_same_walls_share_as_before(self):
        self.host.recorder.load()
        self.guest.recorder.load()
        self.host.recorder.made.append(op("wall"))
        self.host.tick()
        self.join()
        self.guest.recorder.made.append(op("paint"))
        self.tick()
        self.assertEqual(self.guest.replayed(), ["wall"])
        self.assertEqual(self.host.replayed(), ["paint"])
        self.assertTrue(self.host.noted("(the same)"))
        self.assertTrue(self.host.noted("sync with the other game: on"))

    def test_walls_not_known_share_nothing(self):
        # The host's zone was loaded before its recorder was hooked.
        self.guest.recorder.load()
        self.host.recorder.made.append(op("wall"))
        self.join()
        self.assertEqual(self.guest.replayed(), [])
        self.assertEqual(self.host.sync.state(), native.UNKNOWN_WALLS)
        self.assertEqual(self.guest.sync.state(), native.UNKNOWN_WALLS)

    def test_walls_learnt_after_the_greeting_are_told_and_sharing_starts(self):
        self.guest.recorder.load()
        self.host.recorder.made.append(op("wall"))
        self.join()
        self.assertEqual(self.guest.replayed(), [])
        # The host's zone is loaded again, with the same walls as the guest's.
        self.host.place = None
        self.tick(1)
        self.host.recorder.load()
        self.host.place = LOT
        self.tick()
        self.assertEqual(self.guest.replayed(), ["wall"])
        self.assertEqual(self.guest.sync.state(), "on")

    def test_edits_refused_before_the_host_knew_are_sent_again(self):
        # The host has not heard yet that the guest's save is its own.
        self.host.sync.cross = False
        self.guest.recorder.load()
        self.host.recorder.made.append(op("wall"))
        self.join()
        self.assertEqual(self.guest.replayed(), [])
        self.assertTrue(self.guest.noted("not replayed: build mode sync is " + native.UNKNOWN_WALLS))
        # It hears, and its zone is loaded again with the guest's walls.
        self.host.sync.cross = True
        self.host.place = None
        self.tick(1)
        self.host.recorder.load()
        self.host.place = LOT
        self.tick(5)
        self.assertEqual(self.guest.replayed(), ["wall"])

    def test_lot_data_seen_before_the_zone_changed_is_not_the_next_zones(self):
        self.host.recorder.load()
        self.host.tick()
        self.assertTrue(self.host.sync.arch)
        # Straight to another zone, with no lot data of its own.
        self.host.place = OTHER_LOT
        self.host.tick()
        self.assertIsNone(self.host.sync.arch)
        # Left, then loaded: the lot data comes while nothing runs.
        self.host.place = None
        self.host.tick()
        self.host.recorder.load(lot=0x3951B050, size=170602)
        self.host.tick()
        self.host.place = LOT
        self.host.tick()
        self.assertEqual(self.host.sync.arch, "3951b050-170602-00001234")

    def test_one_family_shares_whatever_the_walls(self):
        self.host.sync.cross = self.guest.sync.cross = False
        self.host.recorder.load(crc=0x1111)
        self.guest.recorder.load(crc=0x2222)
        self.host.recorder.made.append(op("wall"))
        self.join()
        self.assertEqual(self.guest.replayed(), ["wall"])
        # The fingerprints are logged all the same, to see if they can be trusted.
        self.assertTrue(self.host.noted("(different)"))
        self.assertEqual(self.host.sync.state(), "on")

    def test_a_link_drop_loses_nothing(self):
        self.host.recorder.load()
        self.guest.recorder.load()
        self.guest.recorder.ready = False
        self.join()
        self.host.recorder.made.append(op("one"))
        self.tick()
        # Dropped before it could be replayed; the guest's game runs on.
        self.host.live = self.guest.live = False
        self.tick()
        self.guest.recorder.ready = True
        self.host.recorder.made.append(op("two"))
        self.join()
        self.assertEqual(self.guest.replayed(), ["one", "two"])


class RestartTest(Pair):
    """A game started again has replayed none of the other's operations as
    far as it knows, but its save holds those it replayed before."""

    def restart_guest(self, cross):
        self.host = Game(run=1, cross=cross)
        self.guest = Game(run=2, cross=cross)
        for game in (self.host, self.guest):
            game.recorder.load()
        self.join()
        self.host.recorder.made += [op("one"), op("two")]
        self.tick()
        self.assertEqual(self.guest.replayed(), ["one", "two"])
        self.host.live = self.guest.live = False
        self.tick()
        self.guest = Game(run=5, cross=cross)
        self.guest.recorder.load()
        self.host.recorder.made.append(op("three"))
        self.join()

    def test_what_was_replayed_before_is_not_replayed_again(self):
        self.restart_guest(cross=True)
        self.assertEqual(self.guest.replayed(), ["three"])
        self.assertEqual(self.guest.sync.applied[LOT], (1, 3))
        self.assertTrue(self.host.noted("build op(s) 1-2 of zone 1001, sent to it before"))
        self.assertTrue(self.guest.noted("build op(s) 1-2 of the other game on zone 1001 were replayed"))
        self.assertEqual(self.guest.sync.duplicates, 0)

    def test_one_family_replays_everything_as_before(self):
        self.restart_guest(cross=False)
        self.assertEqual(self.guest.replayed(), ["one", "two", "three"])

    def test_a_held_notice_does_not_undo_what_was_replayed(self):
        self.join()
        self.host.recorder.made.append(op("one"))
        self.tick()
        self.guest.sync.control({"type": "native_held", "save": "77", "zone": "1001", "run": "1", "seq": 9})
        self.assertEqual(self.guest.sync.applied[LOT], (1, 1))
        self.guest.sync.control({"type": "native_held", "save": "77"})
        self.assertTrue([line for line in self.guest.sync.notes() if "native_held not understood" in line])


class RefusalTest(Pair):
    """The simulation refuses an operation it cannot apply, in message 719,
    after the call was made."""

    def test_a_refused_replay_is_not_counted_as_replayed(self):
        self.join()
        self.host.recorder.made.append(typed(WALL_OP))
        self.tick()
        self.assertEqual(self.guest.sync.replayed, 1)
        self.guest.recorder.refuse(WALL_OP)
        self.tick()
        self.assertEqual(self.guest.sync.replayed, 0)
        self.assertEqual(self.guest.sync.failed_here, 1)
        self.assertEqual(self.guest.sync.failed_own, 0)
        line = self.guest.sync.describe()[0]
        self.assertIn("replayed 0", line)
        self.assertIn("refused here 1 (codes 0x1001 x1)", line)
        self.assertTrue(self.guest.noted("1 replayed build op(s) of the other game refused here (codes 0x1001 x1)"))

    def test_a_refusal_of_our_own_is_counted_apart(self):
        self.join()
        self.host.recorder.made.append(typed(WALL_OP))
        self.tick()
        self.guest.recorder.made.append(typed(BULLDOZE_OP))
        self.guest.recorder.refuse(BULLDOZE_OP, code=0x1003)
        self.guest.recorder.refuse(0)
        self.tick()
        self.assertEqual(self.guest.sync.replayed, 1)
        self.assertEqual(self.guest.sync.failed_here, 0)
        self.assertEqual(self.guest.sync.failed_own, 2)
        self.assertIn("own refused 2 (codes 0x1001 x1, 0x1003 x1)", self.guest.sync.describe()[0])

    def test_each_refusal_takes_one_operation_oldest_first(self):
        self.join()
        self.guest.recorder.made.append(typed(WALL_OP, "mine"))
        self.guest.tick()
        self.host.recorder.made.append(typed(WALL_OP, "theirs"))
        self.tick()
        self.guest.recorder.refuse(WALL_OP)
        self.tick()
        # The guest's own edit came first.
        self.assertEqual((self.guest.sync.failed_own, self.guest.sync.failed_here), (1, 0))
        self.guest.recorder.refuse(WALL_OP)
        self.tick()
        self.assertEqual((self.guest.sync.failed_own, self.guest.sync.failed_here), (1, 1))

    def test_a_refusal_long_after_the_replay_is_not_its(self):
        self.join()
        self.host.recorder.made.append(typed(WALL_OP))
        self.tick()
        for _ in range(native.REFUSAL_TICKS + 1):
            self.guest.tick()
        self.guest.recorder.refuse(WALL_OP)
        self.guest.tick()
        self.assertEqual((self.guest.sync.replayed, self.guest.sync.failed_own), (1, 1))


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
