import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import household, resync  # noqa: E402


class Zone:
    def __init__(self, zone_id, lot=0, plex=0, world=1):
        self.zone_id = zone_id
        self.lot_description_id = lot
        self.active_plex = plex
        self.world_id = world


class Persistence:
    """The two lookups of the persistence service that matching zones uses."""

    def __init__(self, *zones):
        self.zones = list(zones)

    def get_zone_proto_buff(self, zone_id):
        for zone in self.zones:
            if zone.zone_id == zone_id:
                return zone
        return None

    def zone_proto_buffs_gen(self):
        return iter(self.zones)

    def get_sim_proto_buff(self, sim_id):
        return None


class Sim:
    def __init__(self, sim_id, zone_id=0, household_id=0):
        self.sim_id = self.id = sim_id
        self.zone_id = zone_id
        self.household_id = household_id


class LotTest(unittest.TestCase):
    def test_a_lot_is_its_description_and_unit(self):
        self.assertEqual(resync.lot_of(Zone(5, lot=12, plex=3)), "12:3")
        self.assertEqual(resync.lot_of(Zone(5, lot=12)), "12:0")

    def test_a_zone_without_a_lot_has_no_key(self):
        self.assertEqual(resync.lot_of(None), "")
        self.assertEqual(resync.lot_of(Zone(5)), "")

    def test_finds_our_zone_on_a_lot(self):
        ours = Persistence(Zone(1, lot=10), Zone(2, lot=20), Zone(3, lot=20, plex=1))
        self.assertEqual(resync.on_lot(ours, "20:1").zone_id, 3)
        self.assertEqual(resync.on_lot(ours, "20:0", zone_id=99).zone_id, 2)
        self.assertIsNone(resync.on_lot(ours, "30:0"))
        self.assertIsNone(resync.on_lot(ours, ""))

    def test_the_named_zone_is_taken_when_it_is_on_that_lot(self):
        ours = Persistence(Zone(1, lot=10), Zone(2, lot=10))
        self.assertEqual(resync.on_lot(ours, "10:0", zone_id=2).zone_id, 2)


class ZoneForTest(unittest.TestCase):
    def setUp(self):
        self.ours = Persistence(Zone(1, lot=10), Zone(2, lot=20))

    def tearDown(self):
        resync._leads.clear()

    def test_the_lot_comes_before_the_id(self):
        # A save of another new game may have made up the same id for
        # another lot.
        self.assertEqual(resync._zone_for(self.ours, 1, "20:0", 2), 2)

    def test_then_the_id(self):
        self.assertEqual(resync._zone_for(self.ours, 2, "", 1), 2)
        self.assertEqual(resync._zone_for(self.ours, 2, "30:0", 1), 2)

    def test_then_where_the_zone_was_led(self):
        resync.lead(77, 2)
        self.assertEqual(resync._zone_for(self.ours, 77, "30:0", 1), 2)

    def test_then_our_zone(self):
        self.assertEqual(resync._zone_for(self.ours, 77, "30:0", 1), 1)
        self.assertEqual(resync._zone_for(self.ours, 0, "", 1), 1)
        resync.lead(77, 99)
        self.assertEqual(resync._zone_for(self.ours, 77, "", 1), 1)


class ZoneNamesTest(unittest.TestCase):
    def tearDown(self):
        resync.forget_names()
        resync._pending = None

    def test_names_hold_in_the_save_they_were_made_for(self):
        names = resync.ZoneNames()
        names.adopt(9, {100: 1, 200: 2})
        self.assertEqual(names.to_host(9), {1: 100, 2: 200})
        self.assertEqual(names.save, 9)

    def test_another_save_forgets_them(self):
        names = resync.ZoneNames()
        names.adopt(9, {100: 1})
        self.assertEqual(names.to_host(8), {})
        self.assertEqual(names.save, 0)
        self.assertEqual(names.to_host(9), {})

    def test_hold_while_the_hosts_save_waits_to_be_put_in_place(self):
        resync._names.adopt(9, {100: 1})
        resync._pending = (1, b"", b"", 0)
        self.assertEqual(resync.host_zone(1), 100)
        self.assertEqual(resync.adopted_save(), 9)

    def test_are_forgotten_outside_that_save(self):
        resync._names.adopt(9, {100: 1})
        # No game here, so the save played is unknown.
        self.assertEqual(resync.host_zone(1), 1)
        self.assertEqual(resync.adopted_save(), 0)

    def test_can_be_forgotten(self):
        resync._names.adopt(9, {100: 1})
        resync.forget_names()
        self.assertEqual(resync.adopted_save(), 0)

    def test_a_part_of_our_save_is_given_under_the_hosts_ids(self):
        try:
            from google.protobuf import descriptor_pb2
        except ImportError:
            self.skipTest("no protobuf here")
        resync._names.adopt(9, {100: 1})
        resync._pending = (1, b"", b"", 0)
        message = descriptor_pb2.FileOptions()
        message.uninterpreted_option.add().positive_int_value = 1
        message.uninterpreted_option.add().positive_int_value = 5
        self.assertEqual(resync.as_host(message, 1), (100, b""))
        self.assertEqual([o.positive_int_value for o in message.uninterpreted_option], [100, 5])
        self.assertEqual(resync.as_host(message, 7), (7, b""))


class PlaceTest(unittest.TestCase):
    def test_a_sim_lands_on_the_same_lot_here(self):
        ours = Persistence(Zone(1, lot=10), Zone(2, lot=20))
        fallback = ours.zones[0]
        self.assertIs(household._landing(ours, Zone(500, lot=20), fallback), ours.zones[1])

    def test_falls_back_when_no_lot_matches(self):
        ours = Persistence(Zone(1, lot=10))
        fallback = ours.zones[0]
        self.assertIs(household._landing(ours, Zone(500, lot=30), fallback), fallback)
        self.assertIs(household._landing(ours, None, fallback), fallback)
        self.assertIsNone(household._landing(ours, None, None))

    def test_an_id_of_the_guests_save_that_names_another_lot_here_is_not_taken(self):
        ours = Persistence(Zone(5, lot=10), Zone(6, lot=20))
        # The guest's home 5 is on lot 20 in its save; our 5 is lot 10.
        self.assertIs(household._same_lot(ours, 5, Zone(5, lot=20)), ours.zones[1])
        self.assertIs(household._same_lot(ours, 5, Zone(5, lot=10)), ours.zones[0])
        self.assertIs(household._same_lot(ours, 5, None), ours.zones[0])
        self.assertFalse(household._same_zone_here(ours, 5, Zone(5, lot=20)))
        self.assertTrue(household._same_zone_here(ours, 5, Zone(5, lot=10)))
        self.assertTrue(household._same_zone_here(ours, 5, None))
        self.assertFalse(household._same_zone_here(ours, 7, None))

    def test_zones_are_sent_once(self):
        self.assertEqual(household._unique([5, 0, 6, 5, 7, 6]), [5, 6, 7])

    def test_finds_a_sent_zone_by_id(self):
        zones = [Zone(5), Zone(6)]
        self.assertIs(household._with_id(zones, 6), zones[1])
        self.assertIsNone(household._with_id(zones, 0))


class PlayedHouseholdTest(unittest.TestCase):
    def test_only_sims_of_the_household_away_from_this_lot_are_updated(self):
        sims = [Sim(1), Sim(2), Sim(3), Sim(4)]
        gift = household._sims_to_update(sims, {1, 2, 3}, lambda sim_id: sim_id == 2)
        self.assertEqual([sim.sim_id for sim in gift], [1, 3])


class WelcomeTest(unittest.TestCase):
    def test_a_sim_of_ours_is_refused_to_a_player_of_another_household(self):
        self.assertTrue(household._steals(1, 1, [2]))
        self.assertFalse(household._steals(1, 1, []))
        self.assertFalse(household._steals(1, 1, [1]))
        self.assertFalse(household._steals(2, 1, [2]))

    def test_the_sims_that_came_along_are_those_in_this_zone(self):
        members = [Sim(1, zone_id=7), Sim(2, zone_id=7), Sim(3, zone_id=8)]
        self.assertEqual([sim.id for sim in household._travellers(1, members, 7)], [2])
        self.assertEqual(household._travellers(1, members, 0), [])


class SendOffTest(unittest.TestCase):
    def test_the_first_way_that_works_is_taken(self):
        calls = []

        def fails():
            calls.append("a")
            raise AttributeError("no such call")

        ways = [("a", fails), ("b", lambda: calls.append("b")), ("c", lambda: calls.append("c"))]
        self.assertEqual(household._first_that_works(ways), "b")
        self.assertEqual(calls, ["a", "b"])
        self.assertIsNone(household._first_that_works([("a", fails)]))


class Pair:
    """A relationship entry of a save, as far as picking it goes."""

    def __init__(self, a, b):
        self.sim_id_a = a
        self.sim_id_b = b


class Info:
    def __init__(self, player=False):
        self.is_player_sim = player


class Relationship:
    """A relationship of the game that has no `update_compatibility`, as a
    game might not."""

    def __init__(self, calls, pair):
        self.calls = calls
        self.pair = pair

    def load_relationship(self, entry):
        if entry.sim_id_b == 99:
            raise KeyError("damaged")
        self.calls.append(("load", self.pair, entry))

    def enable_player_sim_track_decay(self):
        self.calls.append(("decay", self.pair))

    def refresh_knowledge(self):
        self.calls.append(("knowledge", self.pair))


class RelationshipService:
    """The relationship service of an older game: `send_relationship_info`
    takes no keyword for NPCs."""

    def __init__(self, refused=()):
        self.calls = []
        self.quiet = False
        self.refused = refused

    def destroy_relationship(self, a, b, notify_client=True):
        self.calls.append(("destroy", (a, b), notify_client, self.quiet))

    def create_relationship(self, a, b):
        if (a, b) in self.refused:
            return None
        return Relationship(self.calls, (a, b))

    def send_relationship_info(self, sim_id, target_sim_id=None):
        self.calls.append(("send", sim_id, self.quiet))

    @contextlib.contextmanager
    def suppress_client_updates_context_manager(self):
        self.quiet = True
        try:
            yield
        finally:
            self.quiet = False


class RelationshipsTest(unittest.TestCase):
    def test_the_relationships_a_sim_is_in_are_given(self):
        entries = [Pair(1, 2), Pair(2, 3), Pair(3, 4), Pair(4, 1)]
        self.assertEqual(household.relationships_of(entries, {1}), [entries[0], entries[3]])
        self.assertEqual(household.relationships_of(entries, {2, 3}), entries[:3])
        self.assertEqual(household.relationships_of(entries, set()), [])

    def test_only_those_of_sims_taken_in_whose_sims_are_all_here_are_applied(self):
        entries = [Pair(1, 2), Pair(1, 50), Pair(2, 3), Pair(3, 4), Pair(51, 2)]
        here = {1, 2, 3, 4}
        chosen, missing = household.relationships_to_apply(entries, {1, 2}, lambda sim_id: sim_id in here)
        # 3 and 4 were not taken in: ours of them stay.
        self.assertEqual(chosen, [entries[0], entries[2]])
        self.assertEqual(missing, 2)

    def test_each_pair_is_replaced_quietly_then_sent(self):
        service = RelationshipService(refused={(5, 6)})
        infos = {1: Info(player=True), 2: Info(), 3: Info(), 5: Info(), 6: Info()}
        entries = [Pair(1, 2), Pair(2, 3), Pair(5, 6)]
        self.assertEqual(household.put_relationships(service, infos, entries, [1, 2]), 2)
        self.assertEqual(service.calls, [
            ("destroy", (1, 2), False, True),
            ("load", (1, 2), entries[0]),
            ("decay", (1, 2)),
            ("knowledge", (1, 2)),
            # Neither is a played sim: theirs does not decay.
            ("destroy", (2, 3), False, True),
            ("load", (2, 3), entries[1]),
            ("knowledge", (2, 3)),
            # The game makes no relationship of these.
            ("destroy", (5, 6), False, True),
            ("send", 1, False),
            ("send", 2, False),
        ])

    def test_a_pair_that_fails_is_skipped(self):
        service = RelationshipService()
        infos = {1: Info(), 2: Info(), 99: Info()}
        entries = [Pair(1, 99), Pair(1, 2)]
        self.assertEqual(household.put_relationships(service, infos, entries, [1]), 1)
        self.assertIn(("load", (1, 2), entries[1]), service.calls)
        self.assertNotIn(("knowledge", (1, 99)), service.calls)

    def test_a_game_without_a_quiet_mode_still_takes_them(self):
        class Plain(RelationshipService):
            suppress_client_updates_context_manager = None

        service = Plain()
        self.assertEqual(household.put_relationships(service, {1: Info(), 2: Info()}, [Pair(1, 2)], [1]), 1)
        self.assertEqual(service.calls[-1], ("send", 1, False))

    def test_npc_relationships_are_sent_where_the_game_asks_for_them(self):
        sent = []

        class Newer:
            def send_relationship_info(self, sim_id, target_sim_id=None, allow_sending_npc_info=False):
                sent.append((sim_id, allow_sending_npc_info))

        household._send_relationships(Newer(), 7)
        self.assertEqual(sent, [(7, True)])


class Message:
    """Enough of a SaveGameData for reading a gift: b"h5h6" holds the
    households 5 and 6, b"h" one without an id."""

    def __init__(self):
        self.households = []

    def MergeFromString(self, data):
        if data == b"damaged":
            raise RuntimeError("cannot parse")
        self.households = [Sim(0) for _ in range(data.count(b"h"))]
        for entry, household_id in zip(self.households, data.split(b"h")[1:]):
            entry.household_id = int(household_id or b"0")


class RefusalTest(unittest.TestCase):
    def refusal(self, payload):
        with self.assertRaises(household.Refusal) as caught:
            household._read(payload, Message)
        return caught.exception

    def test_a_payload_that_cannot_be_taken_is_refused_for_good(self):
        for payload in (b"", b"XXXX", resync.pack(1, b"damaged"), resync.pack(1, b""),
                        resync.pack(1, b"h5h6"), resync.pack(1, b"h")):
            refusal = self.refusal(payload)
            self.assertTrue(refusal.final, refusal)
            self.assertIsInstance(refusal, ValueError)

    def test_a_good_payload_is_read(self):
        home, part, objects = household._read(resync.pack(3, b"h5", b"objects"), Message)
        self.assertEqual((home, part.households[0].household_id, objects), (3, 5, b"objects"))

    def test_other_refusals_may_pass_later(self):
        self.assertFalse(household.Refusal("the game did not load the household", final=False).final)


class MergesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, household.MERGES_FILE)

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_a_household_played_here_since_its_merge_keeps_ours(self):
        merges = household.Merges(self.path)
        merges.merged(5, 9)
        self.assertFalse(merges.keeps_ours(5, 8, 9))
        merges.joined([5], 9)
        self.assertTrue(merges.keeps_ours(5, 8, 9))
        # From our own save, or from an unknown one, it is the newer copy.
        self.assertFalse(merges.keeps_ours(5, 9, 9))
        self.assertFalse(merges.keeps_ours(5, 0, 9))
        # Played in another save of ours.
        self.assertFalse(merges.keeps_ours(5, 8, 7))
        self.assertFalse(merges.keeps_ours(6, 8, 9))
        merges.merged(5, 9)
        self.assertFalse(merges.keeps_ours(5, 8, 9))

    def test_a_copy_played_in_our_own_save_makes_others_older(self):
        merges = household.Merges(self.path)
        merges.took(5, 8, 9, household.TOOK_IN)
        self.assertFalse(merges.keeps_ours(5, 8, 9))
        # The guest plays it apart in our save and gives it again.
        merges.took(5, 9, 9, household.UPDATED)
        self.assertTrue(merges.keeps_ours(5, 8, 9))
        # Kept means nothing came in.
        merges.took(5, 8, 9, household.KEPT)
        self.assertTrue(merges.keeps_ours(5, 8, 9))

    def test_a_household_we_lack_is_not_taken_in_without_all_its_sims(self):
        self.assertEqual(household.lacking_sims([1, 2, 3], [Sim(1), Sim(3)], Persistence()), [2])
        self.assertEqual(household.lacking_sims([1], [Sim(1)], Persistence()), [])
        self.assertTrue(household.Refusal("x", final=False, missing=True).missing)
        self.assertFalse(household.Refusal("x", final=True).missing)

    def test_from_the_hosts_save_only_sims_on_our_lot_are_given(self):
        on_lot, elsewhere = Sim(1, zone_id=7), Sim(2, zone_id=8)
        self.assertTrue(household.ran_here(elsewhere, False, {7}))
        self.assertTrue(household.ran_here(on_lot, True, {7}))
        self.assertFalse(household.ran_here(elsewhere, True, {7}))
        # Left at home, where our simulation ran since.
        self.assertTrue(household.ran_here(elsewhere, True, {7, 8}))

    def test_is_kept_in_a_file(self):
        household.Merges(self.path).joined([5, 6], 9)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["6"], {"save": 9, "joined": True})
        self.assertTrue(household.Merges(self.path).keeps_ours(6, 8, 9))

    def test_forgets_the_oldest(self):
        merges = household.Merges(self.path)
        merges.joined(range(1, household.Merges.LIMIT + 2), 9)
        self.assertFalse(merges.keeps_ours(1, 8, 9))
        self.assertTrue(household.Merges(self.path).keeps_ours(2, 8, 9))

    def test_a_damaged_file_is_an_empty_record(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        merges = household.Merges(self.path)
        self.assertFalse(merges.keeps_ours(5, 8, 9))
        merges.joined([5], 9)
        self.assertTrue(household.Merges(self.path).keeps_ours(5, 8, 9))

    def test_a_file_that_cannot_be_written_does_not_fail(self):
        merges = household.Merges(os.path.join(self.directory, "missing", household.MERGES_FILE))
        merges.joined([5], 9)
        self.assertTrue(merges.keeps_ours(5, 8, 9))
        household.Merges(None).joined([5], 9)


if __name__ == "__main__":
    unittest.main()
