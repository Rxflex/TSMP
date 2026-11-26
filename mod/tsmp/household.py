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
its sims, the relationships those sims are in, the zones of its home lot, of
the lot it is on and of the lots its sims are on, and the objects of the home
lot, all under the host's zone ids. The host (`take_in`) puts those in its
own save and has its household manager read them, the way the game does when
a household is loaded from a save, and hands the relationships to its
relationship service. Zones the host does not have are matched to its own by
lot.

`welcome` and `send_off` hand the guest's sims to the host's simulation and
take them back when the guest leaves the host's lot.

With several guests each sim has one player. A guest is never handed a sim
another player controls or was handed (`welcome`'s `claimed`). A gift does
not carry the relationships of a sim another player plays or we play on our
lot (`take_in`'s `others`), nor those sims, nor the money and home of a
household other players also play (`keeps_household`). A gift from our own
save that is older than one another guest gave since of the same household
is not taken (`Gifts`).

Only runs inside the game; the format is the one of a snapshot. `Merges` and
the helpers that take their game objects as arguments are pure.
"""

import contextlib
import json
import os

from tsmp import log, paths, resync

# What `take_in` did with a household.
TOOK_IN = "took in"
UPDATED = "updated"
PARTIAL = "partial"
KEPT = "kept"
# Kept too: another player gave a newer copy from our save since.
OLDER = "older"

MERGES_FILE = "households.json"


class Refusal(ValueError):
    """Why the host did not take a household in. A `final` refusal is about
    the payload itself, so sending it again would be refused again; any
    other may go through later. `missing` says that the guest gave only
    part of a household we do not have, and is to give all of it."""

    def __init__(self, reason, final, missing=False):
        super().__init__(reason)
        self.final = final
        self.missing = missing


class Merges:
    """Host: for each household taken in, the guid of our save it was last
    merged into, and whether the guest has played it joined in that save
    since.

    A guest that played joined played in our save, so our copy is the newer
    one. When that guest later comes back from a save of its own, the copy
    it brings is from before, and taking it in would undo what was played
    here. It also says which households came from a save of another new
    game (`foreign`): the walls of their home here are ours, not theirs.
    The record lives in a small JSON file so that it outlasts the game; it
    is read once and written whole, and a failure to write is only
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

    def foreign(self, household_id, save):
        """True when `household_id` was taken into our save `save` from a
        save of another new game."""
        entry = self._load().get(str(household_id))
        return entry is not None and entry.get("save") == save and entry.get("foreign") is True

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
        elif outcome not in (KEPT, OLDER):
            self._set(household_id, save, False, foreign=bool(origin_save) or None)
            self._write()
        elif origin_save and not self.foreign(household_id, save):
            # Ours is kept, but the household came from elsewhere all the
            # same.
            entry = self._load().get(str(household_id))
            joined = entry is not None and entry.get("save") == save and entry.get("joined") is True
            self._set(household_id, save, joined, foreign=True)
            self._write()

    def _set(self, household_id, save, joined, foreign=None):
        """`foreign` None keeps what the record says of it in that save:
        where a household came from does not change as it is played."""
        entries = self._load()
        key = str(household_id)
        # Moved to the end, so that the first one is the oldest.
        old = entries.pop(key, None) or {}
        if foreign is None:
            foreign = old.get("save") == save and old.get("foreign") is True
        entries[key] = {"save": save, "joined": joined}
        if foreign:
            entries[key]["foreign"] = True
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


class Gifts:
    """Host: for each household a guest gave and we took in since our game
    started, who gave it and how far our game had taken things in then.

    Guests that play in our save each start from a snapshot of it. Two of
    them may play one household apart, and the gift of the one who loaded
    before the other's gift came in does not hold what that gift brought:
    taking it would undo what the other played. So each gift carries a
    version `(giver, base, stamp)`: who gives it; how many households and
    looks our game had taken in when the save the giver last loaded began,
    None when it loaded none of this game's; and that count with this gift.
    Counts only mean something while our game runs, so the record is not
    kept past it, nor past a load of a save from the menu (`forget`), which
    may be without what was given. A gift of the player who gave the last
    one carries on from it, and is never older."""

    def __init__(self):
        self._last = {}

    def older(self, household_id, save, version):
        """True when the gift of `household_id` with `version`, from our
        save `save`, is older than the copy last taken in."""
        if version is None:
            return False
        giver, base, _ = version
        entry = self._last.get((save, household_id))
        if entry is None or entry[0] == giver:
            return False
        return base is None or base < entry[1]

    def took(self, household_id, save, version):
        if version is not None:
            giver, _, stamp = version
            self._last[(save, household_id)] = (giver, stamp)

    def forget(self):
        self._last = {}


_merges = None
_gifts = Gifts()


def forget_gifts():
    """Host: our game loaded a save from the menu; what guests gave before
    may not be in it."""
    _gifts.forget()


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


def from_another_save(household_id):
    """Host: whether `household_id` was taken into the save we play from a
    save of another new game."""
    try:
        return _record().foreign(household_id, resync.current_save())
    except Exception:
        log.exception("household: could not tell where {:#x} came from".format(household_id))
        return False


def note_joined(household_ids):
    """Host: the guest plays these households joined in our save, so the
    copies in its own save are older than ours from now on."""
    try:
        _record().joined([household_id for household_id in household_ids if household_id],
                         resync.current_save())
    except Exception:
        log.exception("household: could not note the households played here")


def give(done, whole=False):
    """Guest: save, then call `done(payload)`, or `done(None)` on failure.
    `whole` gives all of the household, for a host that has none of it."""
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
        # In the host's save, our simulation only ran the lots we were on
        # since we loaded the host's state; the rest of what we have is the
        # host's copy as we loaded it, and older than the host's own.
        ours_only = resync.playing_adopted() and not whole
        ran = resync.ran_zones() | frozenset([here])
        stands = []
        for sim_id in household.sims.ids:
            sim = persistence.get_sim_proto_buff(sim_id)
            if sim is not None and ran_here(sim, ours_only, ran):
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
        relationships = _give_relationships(persistence, part)
        has_home = home and persistence.get_zone_proto_buff(home) is not None
        # In the host's save, the objects of our home are ours to give only
        # if we ran that lot since loading the host's state and the host gave
        # us its objects then; otherwise our game began from its own, older
        # copy of them.
        theirs = home in ran and home in resync.provided_zones()
        objects = resync.objects_of(home) if has_home and (not ours_only or theirs) else b""
        home, objects = resync.as_host(part, home, objects)
        save = part.SerializePartialToString()
        payload = resync.pack(home, save, objects)
        log.info("household: giving {:#x} with {} sims, {} relationships, home {:#x}, {} zones, "
                 "{} bytes ({} to send)".format(household_id, len(part.sims), relationships, home,
                                                len(part.zones), len(save) + len(objects), len(payload)))
        return payload

    def finished(persistence):
        try:
            payload = packed(persistence)
        except Exception:
            log.exception("household: could not pack the household")
            payload = None
        done(payload)

    resync.save_then(finished)


def _relationships(slot):
    """The sim-to-sim relationships of a `SaveSlotData`. The game keeps them
    for the whole save, one entry per pair of sims, and not with either sim;
    a gift keeps them in the same place as a whole save."""
    return slot.gameplay_data.relationship_service.relationships


def relationships_of(entries, sim_ids):
    """The relationships of `entries` (PersistableServiceRelationship, or
    anything with `sim_id_a` and `sim_id_b`) that one of `sim_ids` is in."""
    return [entry for entry in entries if entry.sim_id_a in sim_ids or entry.sim_id_b in sim_ids]


def _give_relationships(persistence, part):
    """Guest: copy into `part` the relationships of the sims it holds. Our
    save has just been made, and making it wrote the relationship service's
    state into it. Returns how many were copied; a failure is logged and
    the household is given without them."""
    try:
        sims = set(sim.sim_id for sim in part.sims)
        ours = _relationships(persistence.get_save_slot_proto_buff())
        given = _relationships(part.save_slot)
        for entry in relationships_of(ours, sims):
            given.add().CopyFrom(entry)
        return len(given)
    except Exception:
        log.exception("household: could not give the relationships of its sims")
        return 0


def lacking_sims(sim_ids, given, persistence):
    """The ids of `sim_ids` that neither the SimData `given` nor our save
    has: a household taken in without them would lose those sims."""
    have = set(sim.sim_id for sim in given)
    return [sim_id for sim_id in sim_ids if sim_id not in have and persistence.get_sim_proto_buff(sim_id) is None]


def ran_here(sim, ours_only, ran):
    """Whether to give the SimData `sim`: all of them from a save of our
    own, from the host's save only those in a zone of `ran`, the zones our
    simulation ran."""
    return not ours_only or sim.zone_id in ran


def _unique(zone_ids):
    """`zone_ids` without zeros and repeats, in order."""
    seen = []
    for zone_id in zone_ids:
        if zone_id and zone_id not in seen:
            seen.append(zone_id)
    return seen


def take_in(payload, origin_save=0, player="", version=None, others=()):
    """Host: put the guest's household into our save and load it.
    `origin_save` is the guid of the save the guest played it in, 0 when
    not known; `player` names the guest, whose own save's zone ids are its
    alone; `version` is the gift's (see `Gifts`), None when not known;
    `others` are the sims other players play. Returns what was done:
    TOOK_IN, UPDATED, PARTIAL (only sims of the household we play), KEPT
    (ours is newer, or could not be updated safely) or OLDER (another guest
    gave a newer copy since). Raises Refusal."""
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
        if known and origin_save and origin_save == save and _gifts.older(household_id, save, version):
            log.info("household: {:#x} comes from what we saved before another player gave it since; "
                     "theirs is newer and kept".format(household_id))
            return OLDER
        if not known:
            lacking = lacking_sims(given.sims.ids, part.sims, persistence)
            if lacking:
                raise Refusal("{} of its sims came without their data, and this save has none of them".format(
                    len(lacking)), final=False, missing=True)
        if keeps_household(household_id, services.active_household_id(), known, given.sims.ids, others):
            outcome = _update_played(persistence, part, household_id, player, others)
        else:
            # From a save of another new game, as a guest of a family of
            # its own brings it.
            foreign = bool(origin_save) and origin_save != save
            outcome = _take(persistence, part, given, home, objects, known, player, others, foreign=foreign)
    except Refusal:
        raise
    except Exception as error:
        log.exception("household: could not take {:#x} in".format(household_id))
        raise Refusal("could not take the household in: {}".format(error), final=False)
    _record().took(household_id, origin_save, save, outcome)
    if outcome not in (KEPT, OLDER):
        _gifts.took(household_id, save, version)
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


def _take(persistence, part, given, home, objects, known, player="", others=(), foreign=False):
    """A household we do not play: the guest's copy replaces ours. `foreign`
    says it comes from a save of another new game."""
    import services

    household_id = given.household_id
    account = persistence.get_account_proto_buff().nucleus_id
    # The household manager does not read again sims it has on our lot, so
    # what the guest has of them and of their relationships is not taken.
    on_lot = _on_our_lot(part.sims)
    sims = _copy_sims(persistence, part.sims)
    ours = persistence.get_household_proto_buff(household_id)
    if ours is None:
        ours = persistence.add_household_proto_buff(household_id)
    ours.CopyFrom(given)
    # Households of an account the game does not know get a made-up one.
    ours.account_id = account
    zone = _with_id(part.zones, home)
    ours.home_zone = _settle_home(persistence, ours, account, zone, home, objects, foreign=foreign)
    _place(persistence, sims, part.zones, home, ours.home_zone, player)

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
    _take_relationships(part, [sim for sim in sims if sim.sim_id not in on_lot], others)
    outcome = UPDATED if known else TOOK_IN
    log.info("household: {} {:#x} ({} sims, home {:#x})".format(
        outcome, household_id, len(part.sims), ours.home_zone))
    return outcome


def keeps_household(household_id, active_id, known, members, others):
    """Whether a gift of the household `household_id` with the sims
    `members` updates only some of its sims, keeping the household itself
    as ours: the one we play (`active_id`), or one we have of which other
    players than the giver play sims, `others`. Its money and home are
    being played by them too, and the giver's copy of them, and of their
    sims, is only what its game had when it loaded: taken whole, it would
    undo what they did (money spent here coming back, or earned lost)."""
    return household_id == active_id or (known and any(sim_id in set(others) for sim_id in members))


def _update_played(persistence, part, household_id, player="", others=()):
    """The guest plays a household that is played here too, by us or by
    other players, and played some of its sims apart. Those sims are the
    guest's truth; the household itself (money, home), the sims on our lot
    and the sims `others` other players play stay ours."""
    import services

    manager = services.household_manager()
    household = persistence.get_household_proto_buff(household_id)
    if household is None or manager.get(household_id) is None:
        log.info("household: {:#x} is played here but not loaded; ours is kept".format(household_id))
        return KEPT
    on_lot = _on_our_lot(part.sims)
    others = set(others)
    gift = _sims_to_update(part.sims, set(household.sims.ids), lambda sim_id: sim_id in on_lot or sim_id in others)
    if not gift:
        log.info("household: the other player's sims of {:#x} are all on this lot or played by others; ours are "
                 "kept".format(household_id))
        return KEPT
    sims = _copy_sims(persistence, gift)
    _place(persistence, sims, part.zones, household.home_zone, household.home_zone, player)
    if manager.load_household(household_id) is None:
        raise Refusal("the game did not load the household", final=False)
    _take_relationships(part, sims, others)
    log.info("household: {:#x} is played here; {} of its {} sims updated".format(
        household_id, len(sims), len(part.sims)))
    return PARTIAL


def _on_our_lot(sims):
    """Ids of the SimData `sims` whose sims stand on our lot here. The
    household manager reads again only the sims it does not have on this
    lot; one it has would be saved over our copy."""
    import services

    infos = services.sim_info_manager()
    current = services.current_zone_id()
    on_lot = set()
    for sim in sims:
        info = infos.get(sim.sim_id)
        if info is not None and (info.is_instanced() or info.zone_id == current):
            on_lot.add(sim.sim_id)
    return on_lot


def _sims_to_update(sims, members, here):
    """The SimData of `sims` that are `members` of the household and not
    `here(sim_id)`."""
    return [sim for sim in sims if sim.sim_id in members and not here(sim.sim_id)]


def relationships_to_apply(entries, taken, exists):
    """(the relationships of `entries` to put in place, how many of them are
    left out). Only those of the sims `taken` in: of any other sim, ours are
    the truth. One whose other sim is not in our game (`exists(sim_id)` is
    false) is left out: that is an NPC of the guest's own save, which does
    not come along, and the game would throw the relationship away at its
    next load anyway."""
    theirs = relationships_of(entries, taken)
    kept = [entry for entry in theirs if exists(entry.sim_id_a) and exists(entry.sim_id_b)]
    return kept, len(theirs) - len(kept)


def played_elsewhere(entries, taken, played):
    """(the relationships of `entries` to put in place, how many are left
    as ours): one whose other sim, the one not `taken` in, is `played(sim_id)`
    by another player, or simulated on our lot, keeps our copy. The gift's
    side of that sim is only what the giver's game had of it, older than
    what its own player, or our simulation, did with it meanwhile."""
    chosen = []
    for entry in entries:
        others = [sim_id for sim_id in (entry.sim_id_a, entry.sim_id_b) if sim_id not in taken]
        if not any(played(sim_id) for sim_id in others):
            chosen.append(entry)
    return chosen, len(entries) - len(chosen)


def _take_relationships(part, sims, others=()):
    """Host: put in place the guest's relationships of `sims`, the SimData
    just loaded, but for those with a sim of `others` (played by another
    player) or one on our lot. Loading a household does not read
    relationships, and the relationship service writes its own over the
    save's at every save, so they go to the service, the way its own load
    hands them over. A failure is only logged: the household stays taken
    in."""
    import services

    try:
        given = _relationships(part.save_slot)
        if not given:
            return
        infos = services.sim_info_manager()
        taken = set(sim.sim_id for sim in sims)
        chosen, missing = relationships_to_apply(given, taken, lambda sim_id: infos.get(sim_id) is not None)
        others = set(others)
        ours_id = services.active_household_id()
        chosen, ours = played_elsewhere(chosen, taken,
                                        lambda sim_id: sim_id in others or played_by_us(infos, sim_id, ours_id))
        carried = put_relationships(services.relationship_service(), infos, chosen, taken)
        log.info("household: {} of {} relationships carried, {} left out for a sim not in this save, {} kept "
                 "as ours for a sim played here or by another player".format(carried, len(chosen), missing, ours))
    except Exception:
        log.exception("household: could not take the relationships in")


def played_by_us(infos, sim_id, household_id):
    """Whether our player plays the sim on our lot now: one of our
    household `household_id` that our lot simulates. Not any sim on the
    lot: a townie walking by did nothing with the giver's sims here, and the
    giver's copy of their pair is the newer one."""
    info = infos.get(sim_id)
    try:
        return info is not None and info.household_id == household_id and bool(info.is_instanced())
    except Exception:
        return False


def put_relationships(service, infos, entries, taken):
    """Have the relationship service `service` hold the relationships
    `entries` in place of its own of those pairs, then send the game client
    those of the sims `taken`. `infos` is the sim info manager. Returns how
    many were put in place."""
    carried = 0
    with _quietly(service):
        for entry in entries:
            if _replace_relationship(service, infos, entry):
                carried += 1
    # Sent once the service talks to the client again: while it is quiet it
    # drops what it is asked to send.
    for sim_id in taken:
        _send_relationships(service, sim_id)
    return carried


def _quietly(service):
    """Keep the service from telling the game client about each bit and
    track as a relationship loads, as its own load does; nothing for a game
    without that."""
    quiet = getattr(service, "suppress_client_updates_context_manager", None)
    return quiet() if quiet is not None else contextlib.nullcontext()


# What the game does to a relationship once loaded, as after loading a save;
# a game that lacks one goes without.
_AFTER_LOAD = ("refresh_knowledge", "update_compatibility")


def _replace_relationship(service, infos, entry):
    """Make the relationship of the pair of `entry` the one it describes.
    Ours is destroyed first: loading over it would only add the guest's
    bits and tracks to ours, and keep those the guest lost. Returns whether
    it is in place; a failure is logged and the pair skipped."""
    a, b = entry.sim_id_a, entry.sim_id_b
    try:
        service.destroy_relationship(a, b, notify_client=False)
        relationship = service.create_relationship(a, b)
        if relationship is None:
            log.info("household: the game makes no relationship of {:#x} and {:#x}".format(a, b))
            return False
        relationship.load_relationship(entry)
        steps = _AFTER_LOAD
        if any(getattr(infos.get(sim_id), "is_player_sim", False) for sim_id in (a, b)):
            # Played sims' relationships decay; the game turns that on at load.
            steps = ("enable_player_sim_track_decay",) + steps
        for name in steps:
            step = getattr(relationship, name, None)
            if step is not None:
                step()
        return True
    except Exception:
        log.exception("household: could not carry the relationship of {:#x} and {:#x}".format(a, b))
        return False


def _send_relationships(service, sim_id):
    """Send the game client the relationships of `sim_id`, those with NPCs
    too: a sim of the guest's may be an NPC here, and is to show its
    relationships once the other player plays it."""
    try:
        try:
            service.send_relationship_info(sim_id, allow_sending_npc_info=True)
        except TypeError:
            # Older games know no such keyword, and send them all.
            service.send_relationship_info(sim_id)
    except Exception:
        log.exception("household: could not send the relationships of {:#x}".format(sim_id))


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


def _settle_home(persistence, household, account, given, home, objects, foreign=False):
    """Give the household its lot in our save, when that lot is free there.
    `given` is the guest's ZoneData of it, of a save of another new game
    when `foreign`. Returns the id of its home zone, 0 for none."""
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
    if foreign:
        # A lot of our save is owned by our account, as its owner record
        # says; the ZoneData of a guest from a save of its own names the
        # guest's. A copy of our save names ours already.
        zone.nucleus_id = account
    _set_owner(persistence, zone, household, account)
    return ours


# What says where a lot is in a save. A new game makes these up, so they are
# ours to keep when the contents of a lot come from another save.
_PLACE = ("zone_id", "neighborhood_id", "world_id", "lot_id")


def _same_lot(persistence, home, given):
    """Our zone for the guest's home: the one with its id, or, for a guest
    from a save of its own, the one on the same lot of the world."""
    lot = resync.lot_of(given)
    if lot:
        # An id of the guest's own save may name another lot here.
        return resync.on_lot(persistence, lot, home)
    return persistence.get_zone_proto_buff(home)


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


def _place(persistence, sims, zones, home, ours, player=""):
    """Sims from a save of the guest's own stand in zones we do not have.
    Put each where the guest will load: on the same lot here, as told by
    the guest's `zones`, or else on the household's lot here `ours`, or
    with us. A guest standing on our lot is to find its sims there, not
    sent home. Where a zone of the save of `player` led is noted for that
    player alone."""
    import services

    fallback_id = ours or services.current_zone_id() or 0
    fallback = persistence.get_zone_proto_buff(fallback_id) if fallback_id else None
    if home and fallback is not None:
        resync.lead(home, fallback_id, player)
    sent = dict((zone.zone_id, zone) for zone in zones)
    for sim in sims:
        if not sim.zone_id or _same_zone_here(persistence, sim.zone_id, sent.get(sim.zone_id)):
            continue
        zone = _landing(persistence, sent.get(sim.zone_id), fallback)
        if zone is None:
            continue
        log.info("household: sim {:#x} stands in zone {:#x}, which is {:#x} here{}".format(
            sim.sim_id, sim.zone_id, zone.zone_id, "" if zone is not fallback else " (no such lot)"))
        resync.lead(sim.zone_id, zone.zone_id, player)
        sim.zone_id = zone.zone_id
        sim.world_id = zone.world_id


def _same_zone_here(persistence, zone_id, given):
    """Whether our zone `zone_id` is the one the guest meant: we have it,
    and it is on the lot of the guest's ZoneData `given` when it sent one."""
    ours = persistence.get_zone_proto_buff(zone_id)
    if ours is None:
        return False
    lot = resync.lot_of(given)
    return not lot or resync.lot_of(ours) == lot


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


def welcome(sim_id, client, theirs=(), travellers=(), claimed=(), guest_household=0):
    """Host: the other player controls `sim_id`. Have that sim, and the sims
    of its household that came along to our lot, on the lot and in our game
    client's list of controllable sims, so that they are simulated as played
    sims and not sent home as visitors. `theirs` are the sims welcomed for
    that player before, `travellers` the sims the other player's game
    travelled with to our lot, `claimed` the sims other players play: none
    of them is handed over, and `sim_id` being one is refused, so that a
    guest that goes is never followed off the lot by another's sim.
    `guest_household` is the household the other player's game plays, 0
    when not known: a sim of ours is refused to a player of another one.
    Returns the ids of the sims now controllable for them, [] when
    refused."""
    import services

    infos = services.sim_info_manager()
    sim_info = infos.get(sim_id)
    if sim_info is None or client is None:
        log.info("household: the other player's sim {:#x} is not in this save".format(sim_id))
        return []
    claimed = set(claimed)
    if sim_id in claimed:
        log.info("household: another player plays the sim {:#x}; refused".format(sim_id))
        return []
    own = _household_id(client)
    before = [info.household_id for info in (infos.get(other) for other in theirs) if info is not None]
    if guest_household:
        # Its game says which household it plays, which holds before it
        # was handed any sim.
        before.append(guest_household)
    if _steals(sim_info.household_id, own, before):
        # A guest that plays another household asks for one of ours: it
        # would take a sim the host is playing.
        log.info("household: the other player plays another household and asks for our sim {:#x}; refused".format(
            sim_id))
        return []
    group = [sim_info]
    if sim_info.household_id != own:
        came = set(travellers)
        members = _members(sim_info)
        group += _travellers(sim_id, members, services.current_zone_id())
        group += [info for info in members if info.id in came and info.id != sim_id and info not in group]
        group = unclaimed(group, claimed)
    return [info.id for info in group if _make_controllable(client, info, own)]


def unclaimed(infos, claimed):
    """The sim infos of `infos` that are not of the sims `claimed`."""
    return [info for info in infos if info.id not in claimed]


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


def send_off(sim_ids, client, guest_household=0):
    """Host: the other player has left our lot with these sims. Without
    this we would go on simulating them, and our next save would overwrite
    what was played with them apart. Takes them out of our client's
    controllable sims (unless of our household) and off the lot. Returns the
    ids of the sims made to leave. A player of another household than ours,
    `guest_household` (0 when not known), never played one of ours, so none
    of ours is made to leave; with one family together the other player's
    sims are ours too, and go."""
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
            if stays(sim_info.household_id, own, guest_household):
                log.info("household: the sim {:#x} is of our household and stays".format(sim_id))
                continue
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


def stays(household_id, own, guest_household):
    """Whether a sim of `household_id` stays on our lot as a player of
    `guest_household` leaves it: one of our household `own`, which that
    player does not play."""
    return bool(guest_household) and guest_household != own and household_id == own


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
