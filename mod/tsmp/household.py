"""Bringing a guest's household into the host's save.

Each player can make a household in a save of their own. For both to play in
one world the host's save has to hold both households, so the guest sends
its household over and then loads the host's save (see tsmp/resync.py), in
which it goes on playing that same household.

The same is done whenever the guest has played by itself, on a lot the host
is not on: what the guest's simulation did to its household is the truth,
and the host's save is brought up to date with it. The exceptions are a
guest coming back with a copy older than the host's (`Merges`) and sims of
the household the host plays that are on the host's lot.

What is sent (`give`) is a partial `SaveGameData` with the guest's household,
its sims, the zones of its home lot, of the lot it is on and of the lots its
sims are on, and the objects of the home lot, all under the host's zone ids.
The host (`take_in`) puts those in its own save and has its household
manager read them, the way the game does when a household is loaded from a
save. Zones the host does not have are matched to its own by lot.

`welcome` and `send_off` hand the guest's sims to the host's simulation and
take them back when the guest leaves the host's lot.

Only runs inside the game; the format is the one of a snapshot. `Merges` and
the helpers that take their game objects as arguments are pure.
"""

import json
import os

from tsmp import log, paths, resync

# What `take_in` did with a household.
TOOK_IN = "took in"
UPDATED = "updated"
PARTIAL = "partial"
KEPT = "kept"

MERGES_FILE = "households.json"


class Refusal(ValueError):
    """Why the host did not take a household in. A `final` refusal is about
    the payload itself, so sending it again would be refused again; any
    other may go through later."""

    def __init__(self, reason, final):
        super().__init__(reason)
        self.final = final


class Merges:
    """Host: for each household taken in, the guid of our save it was last
    merged into, and whether the guest has played it joined in that save
    since.

    A guest that played joined played in our save, so our copy is the newer
    one. When that guest later comes back from a save of its own, the copy
    it brings is from before, and taking it in would undo what was played
    here. The record lives in a small JSON file so that it outlasts the
    game; it is read once and written whole, and a failure to write is only
    logged."""

    # Households remembered; the oldest are forgotten first.
    LIMIT = 100

    def __init__(self, path):
        self._path = path
        self._entries = None

    def keeps_ours(self, household_id, origin_save, save):
        """True when the copy of `household_id` that comes from the save
        `origin_save` is older than ours in the save `save`."""
        if not save or not origin_save or origin_save == save:
            return False
        entry = self._load().get(str(household_id))
        return entry is not None and entry.get("save") == save and entry.get("joined") is True

    def joined(self, household_ids, save):
        for household_id in household_ids:
            self._set(household_id, save, True)
        self._write()

    def merged(self, household_id, save):
        self._set(household_id, save, False)
        self._write()

    def took(self, household_id, origin_save, save, outcome):
        """A copy of `household_id` from `origin_save` was taken into our
        save `save` with `outcome`."""
        if origin_save and origin_save == save:
            # Played in our own save: copies from any other are older.
            self.joined([household_id], save)
        elif outcome != KEPT:
            self.merged(household_id, save)

    def _set(self, household_id, save, joined):
        entries = self._load()
        key = str(household_id)
        # Moved to the end, so that the first one is the oldest.
        entries.pop(key, None)
        entries[key] = {"save": save, "joined": joined}
        while len(entries) > self.LIMIT:
            del entries[next(iter(entries))]

    def _load(self):
        if self._entries is None:
            self._entries = {}
            if self._path and os.path.exists(self._path):
                try:
                    with open(self._path, "r", encoding="utf-8") as f:
                        value = json.load(f)
                    if isinstance(value, dict):
                        self._entries = dict((k, v) for k, v in value.items() if isinstance(v, dict))
                except (OSError, ValueError):
                    log.exception("household: could not read {}".format(self._path))
        return self._entries

    def _write(self):
        if not self._path:
            return
        temporary = self._path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, separators=(",", ":"))
            os.replace(temporary, self._path)
        except (OSError, TypeError, ValueError):
            log.exception("household: could not write {}".format(self._path))


_merges = None


def _record():
    global _merges
    if _merges is None:
        try:
            directory = paths.data_dir()
        except OSError:
            log.exception("household: no folder to keep the record of merges in")
            directory = None
        _merges = Merges(os.path.join(directory, MERGES_FILE) if directory else None)
    return _merges


def note_joined(household_ids):
    """Host: the guest plays these households joined in our save, so the
    copies in its own save are older than ours from now on."""
    try:
        _record().joined([household_id for household_id in household_ids if household_id],
                         resync.current_save())
    except Exception:
        log.exception("household: could not note the households played here")


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
        here = services.current_zone_id() or 0
        # In the host's save, our simulation only ran what is on our lot;
        # the rest of what we have is the host's copy as we loaded it, and
        # older than the host's own.
        ours_only = resync.playing_adopted()
        stands = []
        for sim_id in household.sims.ids:
            sim = persistence.get_sim_proto_buff(sim_id)
            if sim is not None and ran_here(sim, ours_only, here):
                part.sims.add().CopyFrom(sim)
                stands.append(sim.zone_id)
        home = household.home_zone
        # A host in another save matches by lot the zones it does not have,
        # which needs their ZoneData: where we live, where we are, and
        # where each sim is.
        for zone_id in _unique([home, here] + stands):
            zone = persistence.get_zone_proto_buff(zone_id)
            if zone is not None:
                part.zones.add().CopyFrom(zone)
        has_home = home and persistence.get_zone_proto_buff(home) is not None
        # What we keep of a lot we are not on is only as new as our last
        # visit, or the host's copy when it came with its save.
        objects = resync.objects_of(home) if has_home and (not ours_only or home == here) else b""
        home, objects = resync.as_host(part, home, objects)
        save = part.SerializePartialToString()
        payload = resync.pack(home, save, objects)
        log.info("household: giving {:#x} with {} sims, home {:#x}, {} zones, {} bytes ({} to send)".format(
            household_id, len(part.sims), home, len(part.zones), len(save) + len(objects), len(payload)))
        return payload

    resync.save_then(lambda persistence: done(packed(persistence)))


def ran_here(sim, ours_only, here):
    """Whether to give the SimData `sim`: all of them from a save of our
    own, only those on our lot `here` from the host's save."""
    return not ours_only or sim.zone_id == here


def _unique(zone_ids):
    """`zone_ids` without zeros and repeats, in order."""
    seen = []
    for zone_id in zone_ids:
        if zone_id and zone_id not in seen:
            seen.append(zone_id)
    return seen


def take_in(payload, origin_save=0):
    """Host: put the guest's household into our save and load it.
    `origin_save` is the guid of the save the guest played it in, 0 when
    not known. Returns what was done: TOOK_IN, UPDATED, PARTIAL (only sims
    of the household we play) or KEPT (ours is newer, or could not be
    updated safely). Raises Refusal."""
    import services
    from protocolbuffers import FileSerialization_pb2 as serialization

    home, part, objects = _read(payload, serialization.SaveGameData)
    given = part.households[0]
    household_id = given.household_id
    try:
        persistence = services.get_persistence_service()
        save = resync.current_save()
        known = persistence.get_household_proto_buff(household_id) is not None
        if known and _record().keeps_ours(household_id, origin_save, save):
            log.info("household: {:#x} comes from save {:#x} but was played here since; ours is newer and kept".format(
                household_id, origin_save))
            return KEPT
        if household_id == services.active_household_id():
            outcome = _update_played(persistence, part, household_id)
        else:
            outcome = _take(persistence, part, given, home, objects, known)
    except Refusal:
        raise
    except Exception as error:
        log.exception("household: could not take {:#x} in".format(household_id))
        raise Refusal("could not take the household in: {}".format(error), final=False)
    _record().took(household_id, origin_save, save, outcome)
    return outcome


def _read(payload, message_type):
    """(home zone id, `message_type` read from the payload, objects bytes).
    Raises a final Refusal for a payload that can never be taken in."""
    try:
        home, save, objects = resync.unpack(payload)
        part = message_type()
        part.MergeFromString(save)
    except Exception as error:
        raise Refusal("the household cannot be read: {}".format(error), final=True)
    if len(part.households) != 1:
        raise Refusal("expected one household, got {}".format(len(part.households)), final=True)
    if not part.households[0].household_id:
        raise Refusal("the household has no id", final=True)
    return home, part, objects


def _take(persistence, part, given, home, objects, known):
    """A household we do not play: the guest's copy replaces ours."""
    import services

    household_id = given.household_id
    account = persistence.get_account_proto_buff().nucleus_id
    sims = _copy_sims(persistence, part.sims)
    ours = persistence.get_household_proto_buff(household_id)
    if ours is None:
        ours = persistence.add_household_proto_buff(household_id)
    ours.CopyFrom(given)
    # Households of an account the game does not know get a made-up one.
    ours.account_id = account
    zone = _with_id(part.zones, home)
    ours.home_zone = _settle_home(persistence, ours, account, zone, home, objects)
    _place(persistence, sims, part.zones, home, ours.home_zone)

    manager = services.household_manager()
    loaded = manager.get(household_id)
    if loaded is not None:
        try:
            # Read again by load_household only for sims; money is ours to set.
            loaded.funds._funds = ours.money
        except Exception:
            log.exception("household: could not set the funds")
    if manager.load_household(household_id) is None:
        raise Refusal("the game did not load the household", final=False)
    outcome = UPDATED if known else TOOK_IN
    log.info("household: {} {:#x} ({} sims, home {:#x})".format(
        outcome, household_id, len(part.sims), ours.home_zone))
    return outcome


def _update_played(persistence, part, household_id):
    """The guest plays the household we play, and played some of its sims
    apart. Those sims are the guest's truth; the household itself (money,
    home) and the sims on our lot are being played here and stay ours."""
    import services

    manager = services.household_manager()
    household = persistence.get_household_proto_buff(household_id)
    if household is None or manager.get(household_id) is None:
        log.info("household: {:#x} is played here but not loaded; ours is kept".format(household_id))
        return KEPT
    infos = services.sim_info_manager()
    current = services.current_zone_id()

    def here(sim_id):
        # The household manager reads again only the sims it does not have
        # on this lot; one it has would be saved over our copy.
        info = infos.get(sim_id)
        return info is not None and (info.is_instanced() or info.zone_id == current)

    gift = _sims_to_update(part.sims, set(household.sims.ids), here)
    if not gift:
        log.info("household: the other player's sims of {:#x} are all on this lot; ours are kept".format(
            household_id))
        return KEPT
    sims = _copy_sims(persistence, gift)
    _place(persistence, sims, part.zones, household.home_zone, household.home_zone)
    if manager.load_household(household_id) is None:
        raise Refusal("the game did not load the household", final=False)
    log.info("household: {:#x} is played here; {} of its {} sims updated".format(
        household_id, len(sims), len(part.sims)))
    return PARTIAL


def _sims_to_update(sims, members, here):
    """The SimData of `sims` that are `members` of the household and not
    `here(sim_id)`."""
    return [sim for sim in sims if sim.sim_id in members and not here(sim.sim_id)]


def _copy_sims(persistence, sims):
    """Our SimData for each of `sims`, made a copy of it."""
    copied = []
    for sim in sims:
        ours = persistence.get_sim_proto_buff(sim.sim_id)
        if ours is None:
            ours = persistence.add_sim_proto_buff(sim.sim_id)
        ours.CopyFrom(sim)
        copied.append(ours)
    return copied


def _with_id(zones, zone_id):
    for zone in zones:
        if zone_id and zone.zone_id == zone_id:
            return zone
    return None


def _settle_home(persistence, household, account, given, home, objects):
    """Give the household its lot in our save, when that lot is free there.
    `given` is the guest's ZoneData of it. Returns the id of its home zone,
    0 for none."""
    import services

    household_id = household.household_id
    # A lot it owned before and has moved out of.
    for zone in persistence._save_game_data_proto.zones:
        if zone.household_id == household_id and zone.zone_id != home:
            zone.household_id = 0
            _set_owner(persistence, zone, None, account)
    if not home:
        return 0
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
    if zone is not None or given is None:
        return zone
    return resync.on_lot(persistence, resync.lot_of(given))


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


def _place(persistence, sims, zones, home, ours):
    """Sims from a save of the guest's own stand in zones we do not have.
    Put each where the guest will load: on the same lot here, as told by
    the guest's `zones`, or else on the household's lot here `ours`, or
    with us. A guest standing on our lot is to find its sims there, not
    sent home."""
    import services

    fallback_id = ours or services.current_zone_id() or 0
    fallback = persistence.get_zone_proto_buff(fallback_id) if fallback_id else None
    if home and fallback is not None:
        resync.lead(home, fallback_id)
    sent = dict((zone.zone_id, zone) for zone in zones)
    for sim in sims:
        if not sim.zone_id or persistence.get_zone_proto_buff(sim.zone_id) is not None:
            continue
        zone = _landing(persistence, sent.get(sim.zone_id), fallback)
        if zone is None:
            continue
        log.info("household: sim {:#x} stands in zone {:#x}, which is {:#x} here{}".format(
            sim.sim_id, sim.zone_id, zone.zone_id, "" if zone is not fallback else " (no such lot)"))
        resync.lead(sim.zone_id, zone.zone_id)
        sim.zone_id = zone.zone_id
        sim.world_id = zone.world_id


def _landing(persistence, given, fallback):
    """Our zone on the lot of the guest's ZoneData `given`, else `fallback`."""
    zone = resync.on_lot(persistence, resync.lot_of(given))
    return fallback if zone is None else zone


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


def welcome(sim_id, client, theirs=()):
    """Host: the other player controls `sim_id`. Have that sim, and the sims
    of its household that came along to our lot, on the lot and in our game
    client's list of controllable sims, so that they are simulated as played
    sims and not sent home as visitors. `theirs` are the sims welcomed for
    that player before. Returns the ids of the sims now controllable for
    them, [] when refused."""
    import services

    infos = services.sim_info_manager()
    sim_info = infos.get(sim_id)
    if sim_info is None or client is None:
        log.info("household: the other player's sim {:#x} is not in this save".format(sim_id))
        return []
    own = _household_id(client)
    before = [info for info in (infos.get(other) for other in theirs) if info is not None]
    if _steals(sim_info.household_id, own, [info.household_id for info in before]):
        # A guest that plays another household asks for one of ours: it
        # would take a sim the host is playing.
        log.info("household: the other player plays another household and asks for our sim {:#x}; refused".format(
            sim_id))
        return []
    group = [sim_info]
    if sim_info.household_id != own:
        group += _travellers(sim_id, _members(sim_info), services.current_zone_id())
    return [info.id for info in group if _make_controllable(client, info, own)]


def _household_id(client):
    household = getattr(client, "household", None)
    return household.id if household is not None else 0


def _steals(household_id, own, before):
    """True when a sim of `household_id` is asked for by a player whose sims
    so far were of households `before`, and it is of our household `own`
    while those were not."""
    return household_id == own and any(other != own for other in before)


def _members(sim_info):
    try:
        return list(sim_info.household.sim_info_gen())
    except Exception:
        log.exception("household: could not list the household of {:#x}".format(sim_info.id))
        return []


def _travellers(sim_id, members, zone_id):
    """The `members` other than `sim_id` that are in the zone `zone_id`:
    those that travelled with it."""
    return [info for info in members if info.id != sim_id and zone_id and info.zone_id == zone_id]


def _make_controllable(client, sim_info, own):
    try:
        if sim_info.household_id != own and sim_info not in client.selectable_sims:
            client.add_selectable_sim_info(sim_info)
            log.info("household: the other player's sim {:#x} is now controllable here".format(sim_info.id))
        if not sim_info.is_instanced():
            from sims.sim_spawner import SimSpawner

            spawned = SimSpawner.spawn_sim(sim_info)
            log.info("household: the other player's sim {:#x} {}".format(
                sim_info.id, "was brought to the lot" if spawned else "could not be brought to the lot"))
        return True
    except Exception:
        log.exception("household: could not hand the sim {:#x} to the other player".format(sim_info.id))
        return False


def send_off(sim_ids, client):
    """Host: the other player has left our lot with these sims. Without
    this we would go on simulating them, and our next save would overwrite
    what was played with them apart. Takes them out of our client's
    controllable sims (unless of our household) and off the lot. Returns the
    ids of the sims made to leave."""
    import services

    infos = services.sim_info_manager()
    own = _household_id(client)
    active = getattr(client, "active_sim_info", None)
    gone = []
    for sim_id in sim_ids:
        try:
            sim_info = infos.get(sim_id)
            if sim_info is None:
                log.info("household: the other player's sim {:#x} is not in this save".format(sim_id))
                continue
            if client is not None and sim_info.household_id != own and sim_info in client.selectable_sims:
                client.remove_selectable_sim_info(sim_info)
                log.info("household: the other player's sim {:#x} is no longer controllable here".format(sim_id))
            sim = sim_info.get_sim_instance() if sim_info.is_instanced() else None
            if sim is None or (active is not None and active.id == sim_id):
                continue
            how = _first_that_works(_ways_to_leave(sim))
            log.info("household: the other player's sim {:#x} {}".format(
                sim_id, "leaves the lot ({})".format(how) if how else "could not be made to leave the lot"))
            if how:
                gone.append(sim_id)
        except Exception:
            log.exception("household: could not send the sim {:#x} off".format(sim_id))
    return gone


def _ways_to_leave(sim):
    """(name, call) of what makes a sim leave the lot, the gentlest first."""
    import services

    def situations():
        return services.get_zone_situation_manager()

    return [
        ("make_sim_leave_now_must_run", lambda: situations().make_sim_leave_now_must_run(sim)),
        ("make_sim_leave", lambda: situations().make_sim_leave(sim)),
        ("schedule_destroy_asap", lambda: sim.schedule_destroy_asap(
            source=sim, cause="Taken off the lot: the other player plays this sim elsewhere.")),
    ]


def _first_that_works(ways):
    """The name of the first of `ways` whose call does not fail, else None."""
    for name, call in ways:
        try:
            call()
            return name
        except Exception as error:
            log.info("household: {} failed: {!r}".format(name, error))
    return None
