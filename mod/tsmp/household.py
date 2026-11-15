"""Bringing a guest's household into the host's save.

Each player can make a household in a save of their own. For both to play in
one world the host's save has to hold both households, so the guest sends
its household over and then loads the host's save (see tsmp/resync.py), in
which it goes on playing that same household.

The same is done whenever the guest has played by itself, on a lot the host
is not on: what the guest's simulation did to its household is the truth,
and the host's save is brought up to date with it.

What is sent (`give`) is a partial `SaveGameData` with the guest's household,
its sims and its home lot, and the objects of that lot. The host (`take_in`)
puts those in its own save and has its household manager read them, the way
the game does when a household is loaded from a save.

Only runs inside the game; the format is the one of a snapshot.
"""

from tsmp import log, resync


def give(done):
    """Guest: save, then call `done(payload)`, or `done(None)` on failure."""
    import services
    from protocolbuffers import FileSerialization_pb2 as serialization

    def packed(persistence):
        if persistence is None:
            return None
        household_id = services.active_household_id()
        household = persistence.get_household_proto_buff(household_id) if household_id else None
        if household is None:
            log.info("household: nothing to give, no household is played")
            return None
        part = serialization.SaveGameData()
        part.households.add().CopyFrom(household)
        for sim_id in household.sims.ids:
            sim = persistence.get_sim_proto_buff(sim_id)
            if sim is not None:
                part.sims.add().CopyFrom(sim)
        home = household.home_zone
        zone = persistence.get_zone_proto_buff(home) if home else None
        if zone is not None:
            part.zones.add().CopyFrom(zone)
        save = part.SerializePartialToString()
        objects = resync.objects_of(home) if zone is not None else b""
        payload = resync.pack(home, save, objects)
        log.info("household: giving {:#x} with {} sims, home {:#x}, {} bytes ({} to send)".format(
            household_id, len(part.sims), home, len(save) + len(objects), len(payload)))
        return payload

    resync.save_then(lambda persistence: done(packed(persistence)))


def take_in(payload):
    """Host: put the guest's household into our save and load it. Raises
    ValueError when it cannot be taken."""
    import services
    from protocolbuffers import FileSerialization_pb2 as serialization

    home, save, objects = resync.unpack(payload)
    part = serialization.SaveGameData()
    part.MergeFromString(save)
    if len(part.households) != 1:
        raise ValueError("expected one household, got {}".format(len(part.households)))
    given = part.households[0]
    household_id = given.household_id
    if not household_id:
        raise ValueError("the household has no id")
    if household_id == services.active_household_id():
        # Both games would simulate it, and neither copy would be the truth.
        raise ValueError("the other player plays the household this game plays")

    persistence = services.get_persistence_service()
    account = persistence.get_account_proto_buff().nucleus_id
    sims = []
    for sim in part.sims:
        ours = persistence.get_sim_proto_buff(sim.sim_id)
        if ours is None:
            ours = persistence.add_sim_proto_buff(sim.sim_id)
        ours.CopyFrom(sim)
        sims.append(ours)
    ours = persistence.get_household_proto_buff(household_id)
    known = ours is not None
    if not known:
        ours = persistence.add_household_proto_buff(household_id)
    ours.CopyFrom(given)
    # Households of an account the game does not know get a made-up one.
    ours.account_id = account
    ours.home_zone = _settle_home(persistence, ours, account, part, home, objects)
    _place(persistence, sims, home, ours.home_zone)

    manager = services.household_manager()
    loaded = manager.get(household_id)
    if loaded is not None:
        try:
            # Read again by load_household only for sims; money is ours to set.
            loaded.funds._funds = ours.money
        except Exception:
            log.exception("household: could not set the funds")
    if manager.load_household(household_id) is None:
        raise ValueError("the game did not load the household")
    log.info("household: {} {:#x} ({} sims, home {:#x})".format(
        "updated" if known else "took in", household_id, len(part.sims), ours.home_zone))


def _settle_home(persistence, household, account, part, home, objects):
    """Give the household its lot in our save, when that lot is free there.
    Returns the id of its home zone, 0 for none."""
    import services

    household_id = household.household_id
    # A lot it owned before and has moved out of.
    for zone in persistence._save_game_data_proto.zones:
        if zone.household_id == household_id and zone.zone_id != home:
            zone.household_id = 0
            _set_owner(persistence, zone, None, account)
    if not home:
        return 0
    given = part.zones[0] if len(part.zones) == 1 and part.zones[0].zone_id == home else None
    zone = _same_lot(persistence, home, given)
    if zone is None:
        log.info("household: its home {:#x} is not in this save; it has no home here".format(home))
        return 0
    ours = zone.zone_id
    if ours != home:
        log.info("household: its home {:#x} is the lot {:#x} here".format(home, ours))
    if zone.household_id not in (0, household_id):
        log.info("household: its home {:#x} belongs to household {:#x} here; it has no home here".format(
            ours, zone.household_id))
        return 0
    if given is None or not objects:
        log.info("household: the lot {:#x} came without its contents; ours are kept".format(ours))
    elif ours == services.current_zone_id():
        log.info("household: we are on the lot {:#x}; ours is kept as it is".format(ours))
    else:
        _copy_lot(zone, given)
        resync.keep_objects(ours, _objects_for(ours, objects))
    zone.household_id = household_id
    _set_owner(persistence, zone, household, account)
    return ours


# What says where a lot is in a save. A new game makes these up, so they are
# ours to keep when the contents of a lot come from another save.
_PLACE = ("zone_id", "neighborhood_id", "world_id", "lot_id")


def _same_lot(persistence, home, given):
    """Our zone for the guest's home: the one with its id, or, for a guest
    from a save of its own, the one on the same lot of the world."""
    zone = persistence.get_zone_proto_buff(home)
    if zone is not None or given is None or not given.lot_description_id:
        return zone
    for zone in persistence.zone_proto_buffs_gen():
        if zone.lot_description_id == given.lot_description_id and zone.active_plex == given.active_plex:
            return zone
    return None


def _copy_lot(zone, given):
    kept = type(zone)()
    kept.CopyFrom(zone)
    zone.CopyFrom(given)
    for name in _PLACE:
        if name in zone.DESCRIPTOR.fields_by_name:
            setattr(zone, name, getattr(kept, name))


def _objects_for(zone_id, objects):
    """`objects` (bytes of a ZoneObjectData) as the objects of our zone."""
    from protocolbuffers import FileSerialization_pb2 as serialization

    message = serialization.ZoneObjectData()
    message.MergeFromString(objects)
    if message.zone_id == zone_id:
        return objects
    message.zone_id = zone_id
    return message.SerializeToString()


def _place(persistence, sims, home, ours):
    """Sims from a save of the guest's own stand in zones we do not have.
    Put them where the guest will load: on their lot here, or with us."""
    import services

    landing = ours or services.current_zone_id() or 0
    zone = persistence.get_zone_proto_buff(landing) if landing else None
    if zone is None:
        return
    if home:
        resync.lead(home, landing)
    for sim in sims:
        if sim.zone_id and persistence.get_zone_proto_buff(sim.zone_id) is None:
            resync.lead(sim.zone_id, landing)
            sim.zone_id = landing
            sim.world_id = zone.world_id


def _set_owner(persistence, zone, household, account):
    lot = persistence.get_lot_data_from_zone_data(zone)
    if lot is None:
        return
    del lot.lot_owner[:]
    if household is not None:
        owner = lot.lot_owner.add()
        owner.nucleus_id = account
        owner.persona_name = ""
        owner.household_id = household.household_id
        owner.household_name = household.name


def welcome(sim_id, client):
    """Host: the other player controls `sim_id`. Have that sim on the lot and
    in our game client's list of controllable sims, so that it is simulated
    as a played sim and not sent home as a visitor."""
    import services

    sim_info = services.sim_info_manager().get(sim_id)
    if sim_info is None or client is None:
        log.info("household: the other player's sim {:#x} is not in this save".format(sim_id))
        return
    if sim_info.household is not client.household and sim_info not in client.selectable_sims:
        client.add_selectable_sim_info(sim_info)
        log.info("household: the other player's sim {:#x} is now controllable here".format(sim_id))
    if not sim_info.is_instanced():
        from sims.sim_spawner import SimSpawner

        spawned = SimSpawner.spawn_sim(sim_info)
        log.info("household: the other player's sim {:#x} {}".format(
            sim_id, "was brought to the lot" if spawned else "could not be brought to the lot"))
