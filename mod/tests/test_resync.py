import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import log, resync  # noqa: E402


class SnapshotTest(unittest.TestCase):
    def test_round_trip(self):
        save, objects = b"save " * 1000, b"objects " * 300
        payload = resync.pack(0x1234567890ABCDEF, save, objects)
        self.assertLess(len(payload), len(save))
        self.assertEqual(resync.unpack(payload), (0x1234567890ABCDEF, save, objects))

    def test_a_zone_without_objects(self):
        self.assertEqual(resync.unpack(resync.pack(7, b"save")), (7, b"save", b""))

    def test_reads_a_snapshot_from_a_game_without_compression(self):
        compression, resync.zlib = resync.zlib, None
        try:
            payload = resync.pack(7, b"save", b"objects")
        finally:
            resync.zlib = compression
        self.assertIn(b"saveobjects", payload)
        self.assertEqual(resync.unpack(payload), (7, b"save", b"objects"))

    def test_rejects_what_is_not_a_snapshot(self):
        payload = resync.pack(7, b"save", b"objects")
        for damaged in (b"", payload[:10], b"XXXX" + payload[4:], payload[:-3]):
            with self.assertRaises(ValueError):
                resync.unpack(damaged)


class RenameTest(unittest.TestCase):
    def test_renames_ids_wherever_they_are(self):
        try:
            from google.protobuf import descriptor_pb2
        except ImportError:
            self.skipTest("no protobuf here")
        message = descriptor_pb2.FileOptions()
        for value in (5, 6, 5):
            message.uninterpreted_option.add().positive_int_value = value
        message.uninterpreted_option[0].negative_int_value = 6
        self.assertEqual(resync.rename(message, {5: 50, 6: 60}), 4)
        self.assertEqual([o.positive_int_value for o in message.uninterpreted_option], [50, 60, 50])
        self.assertEqual(message.uninterpreted_option[0].negative_int_value, 60)

    def test_a_zone_the_host_names_as_we_do_keeps_its_name(self):
        self.assertEqual(resync.host_zone(7), 7)


class GuestStateTest(unittest.TestCase):
    def setUp(self):
        resync._names.forget()
        resync._provided.clear()
        resync._ran.clear()
        resync._travelled = False

    def test_a_lot_is_the_hosts_only_while_its_latest_snapshot_brought_it(self):
        resync._names.adopt(9, {})
        resync._note_provided(5, b"objects")
        self.assertIn(5, resync.provided_zones())
        # A later snapshot of that lot without its objects.
        resync._note_provided(5, b"")
        self.assertNotIn(5, resync.provided_zones())

    def test_a_load_from_the_menu_forgets_what_came_from_the_host(self):
        resync._names.adopt(9, {})
        resync._note_provided(5, b"objects")
        resync._ran.add(5)
        self.assertTrue(resync.loaded_by_hand())
        self.assertEqual(resync.provided_zones(), frozenset())
        self.assertEqual(resync.ran_zones(), frozenset())
        self.assertEqual(resync.adopted_save(), 0)

    def test_a_travel_is_not_a_load_from_the_menu(self):
        resync._names.adopt(9, {})
        resync._travelled = True
        self.assertFalse(resync.loaded_by_hand())
        self.assertEqual(resync.adopted_save(), 9)
        # Once: the next load without a travel is one from the menu.
        self.assertTrue(resync.loaded_by_hand())

    def test_a_save_without_a_travel_lets_the_hosts_snapshot_go(self):
        # Manage Worlds or the main menu: the game leaves its zone without
        # travelling, and whatever it loads next is not the host's state.
        game = Game(self)
        put = resync.snapshots_put()
        resync.adopt(game.snapshot(), [0x11])
        self.assertTrue(resync.pending())
        self.assertEqual((game.save(), game.persistence.saved), (0, 1))
        self.assertIsNone(resync._pending)
        self.assertEqual(resync.snapshots_put(), put)
        self.assertTrue(resync.loaded_by_hand())

    def test_the_travel_asked_for_puts_the_hosts_save_in_place(self):
        game = Game(self)
        put = resync.snapshots_put()
        resync.adopt(game.snapshot(), [0x11])
        self.assertEqual(game.travels, [([0x11], HOME_HERE)])
        game.travel()
        self.assertEqual(game.shutdowns_kept, 0)
        self.assertEqual((game.save(), game.persistence.saved), (0, 0))
        self.assertEqual(resync.snapshots_put(), put + 1)
        self.assertEqual(resync.adopted_zone(), HOME_HERE)
        self.assertFalse(resync.loaded_by_hand())
        self.assertEqual(resync.adopted_zone(), HOME_HERE)

    def test_a_travel_begun_before_the_snapshot_came_is_not_the_one_asked_for(self):
        game = Game(self)
        resync.install()
        sys.modules["game_services"].disable_shutdown()
        resync.adopt(game.snapshot(), [0x11])
        self.assertEqual((game.save(), game.persistence.saved), (0, 1))
        self.assertIsNone(resync._pending)

    def test_a_snapshot_given_up_is_not_put_in_place(self):
        game = Game(self)
        put = resync.snapshots_put()
        resync.adopt(game.snapshot(), [0x11])
        resync.cancel()
        self.assertFalse(resync.pending())
        # A travel of the player's own keeps its services as ever.
        game.travel()
        self.assertEqual(game.shutdowns_kept, 1)
        self.assertEqual((game.save(), game.persistence.saved), (0, 1))
        self.assertEqual(resync.snapshots_put(), put)
        resync.cancel()

    def test_a_load_from_the_menu_after_a_travel_nobody_asked_about_is_by_hand(self):
        game = Game(self)
        game.loaded(game.snapshot())
        self.assertFalse(resync.loaded_by_hand())
        game.travel()
        game.save()
        # The game loaded that zone; the session did not ask. Then the
        # player leaves by the menu and loads a save.
        game.save()
        self.assertTrue(resync.loaded_by_hand())

    def test_with_one_family_every_exit_from_a_zone_is_a_travel_as_before(self):
        game = Game(self, cross=False)
        resync.install()
        game.save()
        self.assertFalse(resync.loaded_by_hand())

    def test_with_one_family_any_save_puts_the_hosts_in_place_as_before(self):
        game = Game(self, cross=False)
        put = resync.snapshots_put()
        resync.adopt(game.snapshot(HOSTS_THERE), [0x11])
        self.assertFalse(resync.cross_save())
        # Manage Worlds: the game saves without travelling.
        self.assertEqual((game.save(), game.persistence.saved), (0, 0))
        self.assertEqual(resync.snapshots_put(), put + 1)
        self.assertIsNone(resync._pending)
        # And the travel of that load is forgotten with it.
        self.assertTrue(resync.loaded_by_hand())

    def test_a_travel_begun_is_not_given_up(self):
        game = Game(self)
        put = resync.snapshots_put()
        resync.adopt(game.snapshot(), [0x11])
        game.travel()
        # The game has begun travelling: the save comes later, after any
        # save under way.
        self.assertTrue(resync.leaving_zone())
        resync.cancel()
        self.assertTrue(resync.pending())
        self.assertEqual(game.shutdowns_kept, 0)
        self.assertEqual((game.save(), game.persistence.saved), (0, 0))
        self.assertEqual(resync.snapshots_put(), put + 1)
        self.assertEqual(resync.adopted_zone(), HOME_HERE)

    def test_from_a_save_of_our_own_no_sim_to_travel_with_is_refused(self):
        game = Game(self)
        with self.assertRaises(ValueError):
            resync.adopt(game.snapshot(), [0])
        self.assertIsNone(resync._pending)
        self.assertEqual(game.travels, [])


# The ids of the test saves. The guest's own save names the lot 50 (its
# home) HOME_HERE and the lot 40 (the host's home) HOSTS_HERE; the host's
# save names them HOME_THERE and HOSTS_THERE.
HOME_HERE, HOSTS_HERE, HOME_THERE, HOSTS_THERE = 0x200, 0x300, 0x900, 0x800
OURS, THEIRS = 0x10, 0x20
OUR_SIM, THEIR_SIM = 0x11, 0x21
OUR_ACCOUNT, THEIR_ACCOUNT, ANOTHER_ACCOUNT = 0xE9EC5F639B, 0xEC57779491, 0x77


def _schema():
    """Message classes with the names and field numbers of the game's
    FileSerialization messages that resync reads, as the public proto has
    them; the game's own module is not here."""
    try:
        from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    except ImportError:
        return None
    f = descriptor_pb2.FieldDescriptorProto
    kinds = {"u64": f.TYPE_UINT64, "f64": f.TYPE_FIXED64, "u32": f.TYPE_UINT32, "str": f.TYPE_STRING,
             "bytes": f.TYPE_BYTES, "float": f.TYPE_FLOAT}
    messages = [
        ("WorldLocation", [("x", 1, "float"), ("level", 7, "u32")]),
        ("SuperInteractionSaveState", [("interaction", 1, "u64")]),
        ("GameplaySimData", [("location", 1, ".t.WorldLocation"), ("interaction_state", 2,
                                                                     ".t.SuperInteractionSaveState")]),
        ("SimData", [("sim_id", 1, "f64"), ("zone_id", 2, "f64"), ("world_id", 3, "u32"), ("household_id", 4, "f64"),
                     ("nucleus_id", 23, "f64"), ("gameplay_data", 47, ".t.GameplaySimData")]),
        ("IdList", [("ids", 1, "f64*")]),
        ("HouseholdData", [("account_id", 1, "f64"), ("household_id", 2, "f64"), ("home_zone", 4, "f64"),
                           ("sims", 11, ".t.IdList"), ("creator_id", 20, "f64"), ("creator_name", 21, "str")]),
        ("GameplayZoneData", [("game_time", 1, "u64"), ("active_household_id_on_save", 5, "f64")]),
        ("ZoneData", [("zone_id", 1, "f64"), ("world_id", 3, "u32"), ("household_id", 6, "f64"),
                      ("nucleus_id", 7, "f64"), ("gameplay_zone_data", 11, ".t.GameplayZoneData"),
                      ("lot_description_id", 12, "f64"), ("active_plex", 18, "u32")]),
        ("HouseholdAccountPair", [("nucleus_id", 1, "f64"), ("persona_name", 2, "str"), ("household_id", 3, "u64")]),
        ("LotOwnerInfo", [("lot_description_id", 1, "u64"), ("zone_instance_id", 2, "f64"),
                          ("lot_owner", 4, ".t.HouseholdAccountPair*")]),
        ("NeighborhoodData", [("neighborhood_id", 1, "f64"), ("owner_id", 2, "f64"), ("lots", 5, ".t.LotOwnerInfo*"),
                              ("households", 7, ".t.HouseholdAccountPair*"),
                              ("npc_households", 8, ".t.HouseholdAccountPair*")]),
        ("AccountData", [("nucleus_id", 1, "f64"), ("persona_name", 2, "str"), ("number_of_saves", 18, "u64")]),
        ("SaveSlotData", [("active_household_id", 11, "u64"), ("nucleus_id", 12, "f64")]),
        ("SaveGameData", [("guid", 1, "u32"), ("save_slot", 2, ".t.SaveSlotData"), ("account", 3, ".t.AccountData"),
                          ("neighborhoods", 4, ".t.NeighborhoodData*"), ("households", 5, ".t.HouseholdData*"),
                          ("sims", 6, ".t.SimData*"), ("zones", 7, ".t.ZoneData*")]),
        ("ZoneObjectData", [("zone_id", 1, "f64"), ("objects", 2, "bytes")]),
    ]
    proto = descriptor_pb2.FileDescriptorProto(name="tsmp_test_serialization.proto", package="t", syntax="proto2")
    for name, fields in messages:
        message = proto.message_type.add(name=name)
        for field_name, number, kind in fields:
            many = kind.endswith("*")
            kind = kind.rstrip("*")
            field = message.field.add(name=field_name, number=number,
                                      label=f.LABEL_REPEATED if many else f.LABEL_OPTIONAL)
            if kind.startswith("."):
                field.type, field.type_name = f.TYPE_MESSAGE, kind
            else:
                field.type = kinds[kind]
    pool = descriptor_pool.DescriptorPool()
    pool.Add(proto)

    class Serialization:
        pass

    for name, _ in messages:
        descriptor = pool.FindMessageTypeByName("t." + name)
        if hasattr(message_factory, "GetMessageClass"):
            made = message_factory.GetMessageClass(descriptor)
        else:
            made = message_factory.MessageFactory(pool).GetPrototype(descriptor)
        setattr(Serialization, name, made)
    return Serialization


SERIALIZATION = _schema()


class Persistence:
    """The persistence service of the guest's game, as far as resync uses
    it; `save_to_scratch_slot_gen` is the game's own save, replaced by
    resync's hook once installed."""

    def __init__(self, data):
        self._save_game_data_proto = data
        self.saved = 0
        self.built = 0

    def save_to_scratch_slot_gen(self, timeline):
        self.saved += 1
        return 0
        yield

    def save_using(self, save_gen):
        _run(save_gen(None))

    def get_save_game_data_proto(self):
        return self._save_game_data_proto

    def get_save_slot_proto_guid(self):
        return self._save_game_data_proto.guid

    def build_caches(self):
        self.built += 1

    def zone_proto_buffs_gen(self):
        return iter(self._save_game_data_proto.zones)

    def get_zone_proto_buff(self, zone_id):
        return next((zone for zone in self._save_game_data_proto.zones if zone.zone_id == zone_id), None)

    def get_household_proto_buff(self, household_id):
        return next((household for household in self._save_game_data_proto.households
                     if household.household_id == household_id), None)

    def get_sim_proto_buff(self, sim_id):
        return next((sim for sim in self._save_game_data_proto.sims if sim.sim_id == sim_id), None)


def _run(generator):
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        return stop.value


def _module(name, **values):
    module = types.ModuleType(name)
    module.__dict__.update(values)
    return module


def _save(guid, zones, households, sims, active, account, persona):
    """A SaveGameData: `zones` are (id, lot, owner household, account,
    saved), `households` (id, home, sims, account), `sims` (id, household,
    zone, x, account)."""
    data = SERIALIZATION.SaveGameData()
    data.guid = guid
    data.save_slot.active_household_id = active
    data.save_slot.nucleus_id = account
    data.account.nucleus_id = account
    data.account.persona_name = persona
    data.account.number_of_saves = guid
    neighborhood = data.neighborhoods.add(neighborhood_id=1, owner_id=account)
    for zone_id, lot, owner, owner_account, saved in zones:
        zone = data.zones.add(zone_id=zone_id, lot_description_id=lot, household_id=owner, nucleus_id=owner_account,
                              world_id=7)
        if saved:
            zone.gameplay_zone_data.game_time = 1
            zone.gameplay_zone_data.active_household_id_on_save = active
        entry = neighborhood.lots.add(lot_description_id=lot, zone_instance_id=zone_id)
        if owner:
            entry.lot_owner.add(nucleus_id=owner_account, persona_name=persona if owner_account == account else "x",
                                household_id=owner)
    for household_id, home, members, owner_account in households:
        household = data.households.add(household_id=household_id, home_zone=home, account_id=owner_account,
                                        creator_id=owner_account)
        household.sims.ids.extend(members)
        neighborhood.households.add(nucleus_id=owner_account, persona_name=persona, household_id=household_id)
    for sim_id, household_id, zone_id, x, owner_account in sims:
        sim = data.sims.add(sim_id=sim_id, household_id=household_id, zone_id=zone_id, world_id=7,
                            nucleus_id=owner_account)
        sim.gameplay_data.location.x = x
        sim.gameplay_data.interaction_state.interaction = int(x)
    return data


def _objects(zone_id, what):
    return SERIALIZATION.ZoneObjectData(zone_id=zone_id, objects=what).SerializeToString()


class Game:
    """The modules of the guest's game that resync hooks and calls, with
    our own save loaded: our household OURS lives on HOME_HERE with its sim
    standing there at x=1, and our game saved that lot's objects (b"ours")."""

    NAMES = ("services", "services.persistence_service", "game_services", "persistence_module", "element_utils",
             "world", "world.travel_service", "protocolbuffers", "protocolbuffers.FileSerialization_pb2")

    def __init__(self, test, cross=True):
        if SERIALIZATION is None:
            test.skipTest("no protobuf here")
        self.test = test
        self.cross = cross
        self.travels = []
        self.shutdowns_kept = 0
        self.placed = []
        if cross:
            ours = _save(1, [(HOME_HERE, 50, OURS, OUR_ACCOUNT, True), (HOSTS_HERE, 40, 0, 0, False)],
                         [(OURS, HOME_HERE, [OUR_SIM], OUR_ACCOUNT)], [(OUR_SIM, OURS, HOME_HERE, 1, OUR_ACCOUNT)],
                         OURS, OUR_ACCOUNT, "smkywzrd")
        else:
            # One family together: our save is a copy of the host's.
            ours = self.hosts(HOSTS_THERE, on=HOSTS_THERE)
        self.active = OURS if cross else THEIRS
        game = self

        class PersistenceService(Persistence):
            """A class of its own for each game, for resync to hook."""

        self.persistence = PersistenceService(ours)

        def run_persistence_operation(operation, message, *args, **kwargs):
            if operation == "objects":
                game.placed.append((message.zone_id, message.objects))

        def disable_shutdown():
            game.shutdowns_kept += 1

        class Waiting:
            def trigger_soft_stop(self):
                pass

        self.modules = {
            "services": _module("services", get_persistence_service=lambda: game.persistence,
                                active_household_id=lambda: game.active, current_zone_id=lambda: HOME_HERE),
            "services.persistence_service": _module("services.persistence_service",
                                                    PersistenceService=PersistenceService),
            "game_services": _module("game_services", disable_shutdown=disable_shutdown),
            "persistence_module": _module(
                "persistence_module", run_persistence_operation=run_persistence_operation,
                PersistenceOpType=types.SimpleNamespace(kPersistenceOpSaveZoneObjects="objects",
                                                        kPersistenceOpSave="save")),
            "element_utils": _module("element_utils", soft_sleep_forever=Waiting,
                                     run_child=lambda timeline, element: iter(())),
            "world.travel_service": _module(
                "world.travel_service", travel_sims_to_zone=lambda sims, zone: game.travels.append((sims, zone))),
            "protocolbuffers.FileSerialization_pb2": SERIALIZATION,
        }
        self.modules["world"] = _module("world", travel_service=self.modules["world.travel_service"])
        self.modules["protocolbuffers"] = _module("protocolbuffers", FileSerialization_pb2=SERIALIZATION)
        self.kept = dict((name, sys.modules.get(name)) for name in self.NAMES)
        sys.modules.update(self.modules)
        test.addCleanup(self.close)
        resync._objects[HOME_HERE] = _objects(HOME_HERE, b"ours")

    def close(self):
        for name, module in self.kept.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        resync._installed = False
        resync._travel_armed = False
        resync.cancel()
        resync._objects.clear()
        resync._cross_saves = None

    def hosts(self, zone_id, on):
        """The host's save, with our household taken in on our home's lot
        and the host's sim standing in the zone `on`."""
        return _save(2, [(HOME_THERE, 50, OURS, THEIR_ACCOUNT, True), (HOSTS_THERE, 40, THEIRS, THEIR_ACCOUNT, True),
                         (0x700, 60, 0x30, ANOTHER_ACCOUNT, False)],
                     [(OURS, HOME_THERE, [OUR_SIM], THEIR_ACCOUNT), (THEIRS, HOSTS_THERE, [THEIR_SIM], THEIR_ACCOUNT),
                      (0x30, 0x700, [], ANOTHER_ACCOUNT)],
                     [(OUR_SIM, OURS, HOME_THERE, 2, THEIR_ACCOUNT), (THEIR_SIM, THEIRS, on, 3, THEIR_ACCOUNT)],
                     THEIRS, THEIR_ACCOUNT, "kotanhens")

    def snapshot(self, zone_id=HOME_THERE, on=HOSTS_THERE, objects=b"theirs"):
        return resync.pack(zone_id, self.hosts(zone_id, on).SerializeToString(),
                           _objects(zone_id, objects) if objects else b"")

    def travel(self):
        resync.install()
        sys.modules["game_services"].disable_shutdown()

    def save(self):
        return _run(self.persistence.save_to_scratch_slot_gen(None))

    def loaded(self, snapshot, joined=None):
        """Adopt `snapshot`, travel and have it put in place. Returns our
        save as it then is."""
        self.travels = []
        resync.adopt(snapshot, [OUR_SIM], joined=joined)
        self.travel()
        saved = self.persistence.saved
        self.test.assertEqual((self.save(), self.persistence.saved), (0, saved))
        return self.persistence.get_save_game_data_proto()


class _Lines(list):
    """The lines resync logs meanwhile."""

    def __enter__(self):
        log.set_forwarder(self.append)
        return self

    def __exit__(self, *exc):
        log.set_forwarder(None)


def _ids(messages, name):
    return [getattr(message, name) for message in messages]


class OwnFamilyTest(unittest.TestCase):
    """A guest from a save of its own goes on playing its own household and
    account in the host's save, and its own home stays its own."""

    def setUp(self):
        resync._names.forget()
        resync._provided.clear()
        resync._ran.clear()
        resync._travelled = resync._travelling = False

    def test_the_hosts_save_is_renamed_to_our_zones(self):
        game = Game(self)
        data = game.loaded(game.snapshot())
        self.assertTrue(resync.cross_save())
        self.assertEqual(game.travels, [([OUR_SIM], HOME_HERE)])
        self.assertEqual(sorted(_ids(data.zones, "zone_id")), sorted([HOME_HERE, HOSTS_HERE, 0x700]))
        self.assertEqual(resync.adopted_zone(), HOME_HERE)

    def test_our_household_stays_the_one_played(self):
        game = Game(self)
        data = game.loaded(game.snapshot(HOSTS_THERE, on=HOSTS_THERE))
        self.assertEqual(data.save_slot.active_household_id, OURS)
        zones = dict((zone.zone_id, zone) for zone in data.zones)
        # Our home, and the lot we load, were saved with us playing.
        self.assertEqual(zones[HOME_HERE].gameplay_zone_data.active_household_id_on_save, OURS)
        self.assertEqual(zones[HOSTS_HERE].gameplay_zone_data.active_household_id_on_save, OURS)
        # A lot never saved is not made to look visited.
        self.assertFalse(zones[0x700].HasField("gameplay_zone_data"))

    def test_our_account_stays_ours(self):
        game = Game(self)
        data = game.loaded(game.snapshot())
        self.assertEqual((data.account.nucleus_id, data.account.persona_name), (OUR_ACCOUNT, "smkywzrd"))
        # The rest of the account is ours too.
        self.assertEqual(data.account.number_of_saves, 1)
        self.assertEqual(data.save_slot.nucleus_id, OUR_ACCOUNT)
        households = dict((household.household_id, household) for household in data.households)
        self.assertEqual(households[OURS].account_id, OUR_ACCOUNT)
        self.assertEqual(households[THEIRS].account_id, OUR_ACCOUNT)
        # Another player's account, and who made a household, are left.
        self.assertEqual(households[0x30].account_id, ANOTHER_ACCOUNT)
        self.assertEqual(households[THEIRS].creator_id, THEIR_ACCOUNT)
        self.assertEqual(set(_ids(data.sims, "nucleus_id")), {OUR_ACCOUNT})
        zones = dict((zone.zone_id, zone) for zone in data.zones)
        self.assertEqual(zones[HOME_HERE].nucleus_id, OUR_ACCOUNT)
        self.assertEqual(zones[0x700].nucleus_id, ANOTHER_ACCOUNT)
        neighborhood = data.neighborhoods[0]
        self.assertEqual(neighborhood.owner_id, OUR_ACCOUNT)
        owners = dict((lot.zone_instance_id, lot.lot_owner[0]) for lot in neighborhood.lots if lot.lot_owner)
        self.assertEqual((owners[HOME_HERE].nucleus_id, owners[HOME_HERE].persona_name), (OUR_ACCOUNT, "smkywzrd"))
        self.assertEqual(owners[HOME_HERE].household_id, OURS)
        self.assertEqual((owners[0x700].nucleus_id, owners[0x700].persona_name), (ANOTHER_ACCOUNT, "x"))
        self.assertEqual(set(_ids(neighborhood.households, "nucleus_id")), {OUR_ACCOUNT, ANOTHER_ACCOUNT})

    def test_our_home_apart_keeps_its_objects_and_our_sims_where_they_were(self):
        game = Game(self)
        data = game.loaded(game.snapshot(HOME_THERE, on=HOSTS_THERE))
        self.assertEqual(game.placed, [(HOME_HERE, b"ours")])
        self.assertEqual(resync.objects_of(HOME_HERE), _objects(HOME_HERE, b"ours"))
        self.assertNotIn(HOME_HERE, resync.provided_zones())
        sim = next(sim for sim in data.sims if sim.sim_id == OUR_SIM)
        self.assertEqual((sim.zone_id, sim.gameplay_data.location.x, sim.gameplay_data.interaction_state.interaction),
                         (HOME_HERE, 1, 1))

    def test_our_home_with_the_host_on_it_is_the_hosts(self):
        game = Game(self)
        # Told, or seen from where the host's sims stand in its save.
        for joined in (True, None):
            data = game.loaded(game.snapshot(HOME_THERE, on=HOME_THERE), joined=joined)
            self.assertEqual(game.placed[-1], (HOME_HERE, b"theirs"))
            sim = next(sim for sim in data.sims if sim.sim_id == OUR_SIM)
            self.assertEqual(sim.gameplay_data.location.x, 2)

    def test_another_lot_apart_is_the_hosts(self):
        game = Game(self)
        game.loaded(game.snapshot(HOSTS_THERE, on=HOME_THERE), joined=False)
        self.assertEqual(game.placed, [(HOSTS_HERE, b"theirs")])

    def test_a_home_the_host_sent_without_objects_keeps_what_our_client_has(self):
        game = Game(self)
        resync._objects.clear()
        game.loaded(game.snapshot(HOME_THERE, objects=b""))
        self.assertEqual(game.placed, [])

    def test_a_game_started_again_from_that_save_knows_it_plays_a_family_of_its_own(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        path = os.path.join(directory, resync.CROSS_FILE)
        resync._cross_saves = resync.CrossSaves(path)
        game = Game(self)
        data = game.loaded(game.snapshot())
        self.assertTrue(resync.cross_save())
        # The game starts again from what it saved: no names, no record in
        # memory, and the save carries the host's guid.
        resync._names.forget()
        resync._cross_saves = None
        self.assertEqual(data.guid, 2)
        resync._cross_saves = resync.CrossSaves(path)
        self.assertTrue(resync.cross_save())
        # A copy of that same save of the host's, given to us for one family
        # together, plays the host's household and is not one.
        game.active = THEIRS
        self.assertFalse(resync.cross_save())

    @staticmethod
    def given_the_hosts_account(game, guid=2):
        """Our save as version 0.0.1 left it: the host's save in place in
        it, with the host's account everywhere, and ours only as the one
        that made our household."""
        mangled = game.hosts(HOME_THERE, on=HOSTS_THERE)
        renamed = SERIALIZATION.SaveGameData()
        renamed.MergeFromString(resync._as_ours(HOME_THERE, mangled.SerializeToString(), b"")[1])
        renamed.guid = guid
        ours = next(household for household in renamed.households if household.household_id == OURS)
        ours.creator_id, ours.creator_name = OUR_ACCOUNT, "smkywzrd"
        game.persistence._save_game_data_proto.CopyFrom(renamed)
        resync._names.forget()

    def test_a_save_of_another_hosts_with_the_hosts_account_keeps_it(self):
        game = Game(self)
        self.given_the_hosts_account(game, guid=3)
        with _Lines() as lines:
            data = game.loaded(game.snapshot())
        self.assertEqual(data.account.nucleus_id, THEIR_ACCOUNT)
        self.assertTrue(any("our save carries the host's account" in line and "stays the host's" in line
                            for line in lines))

    def test_a_save_that_an_earlier_version_gave_the_hosts_account_gets_ours_back(self):
        game = Game(self)
        self.given_the_hosts_account(game)
        data = game.loaded(game.snapshot())
        self.assertEqual((data.account.nucleus_id, data.account.persona_name), (OUR_ACCOUNT, "smkywzrd"))
        self.assertEqual(data.save_slot.nucleus_id, OUR_ACCOUNT)
        households = dict((household.household_id, household) for household in data.households)
        self.assertEqual(households[OURS].account_id, OUR_ACCOUNT)
        zones = dict((zone.zone_id, zone) for zone in data.zones)
        self.assertEqual(zones[HOME_HERE].nucleus_id, OUR_ACCOUNT)

    def test_a_household_made_by_another_account_does_not_name_ours(self):
        game = Game(self)
        # Our own save, with a household from the gallery: made by someone
        # else, ours all the same.
        ours = game.persistence.get_household_proto_buff(OURS)
        ours.creator_id, ours.creator_name = 0x5555, "someone"
        data = game.loaded(game.snapshot())
        self.assertEqual((data.account.nucleus_id, data.account.persona_name), (OUR_ACCOUNT, "smkywzrd"))
        self.assertEqual(set(_ids(data.sims, "nucleus_id")), {OUR_ACCOUNT})

    def test_one_family_together_takes_the_hosts_save_whole(self):
        game = Game(self, cross=False)
        data = game.loaded(game.snapshot(HOME_THERE, on=HOSTS_THERE))
        self.assertFalse(resync.cross_save())
        self.assertEqual(game.travels, [([OUR_SIM], HOME_THERE)])
        self.assertEqual(data.save_slot.active_household_id, THEIRS)
        self.assertEqual(data.account.nucleus_id, THEIR_ACCOUNT)
        self.assertEqual(set(_ids(data.households, "account_id")), {THEIR_ACCOUNT, ANOTHER_ACCOUNT})
        self.assertEqual(game.placed, [(HOME_THERE, b"theirs")])
        sim = next(sim for sim in data.sims if sim.sim_id == OUR_SIM)
        self.assertEqual(sim.gameplay_data.location.x, 2)


class TakeTest(unittest.TestCase):
    """The host's side: the home of a household from another save goes to a
    guest of another save without its objects."""

    def setUp(self):
        from tsmp import household

        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.merges, household._merges = household._merges, household.Merges(
            os.path.join(self.directory, household.MERGES_FILE))
        self.addCleanup(setattr, household, "_merges", self.merges)
        game = Game(self, cross=False)
        game.active = THEIRS
        household._merges.took(OURS, 1, 2, household.TOOK_IN)
        resync._objects[HOME_THERE] = _objects(HOME_THERE, b"ran here")
        resync._objects[HOSTS_THERE] = _objects(HOSTS_THERE, b"ours")
        self.addCleanup(resync._leads.clear)

    def take(self, *places):
        taken = []
        resync.take(taken.append, list(places))
        return [resync.unpack(payload)[2] for payload in taken[0]]

    def test_packs_no_objects_of_such_a_home_for_a_guest_of_another_save(self):
        self.assertEqual(self.take((HOME_THERE, "50:0", "p1", True), (HOSTS_THERE, "40:0", "p1", True)),
                         [b"", _objects(HOSTS_THERE, b"ours")])

    def test_packs_them_for_a_guest_of_our_save(self):
        objects = _objects(HOME_THERE, b"ran here")
        self.assertEqual(self.take((HOME_THERE, "50:0", "p1", False), (HOME_THERE, "50:0", "p2")), [objects, objects])


if __name__ == "__main__":
    unittest.main()
