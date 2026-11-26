"""Following the host through a reload of its zone.

Each game runs its own simulation, and the guest's game client is fed the
host's messages. That only works while both clients were built from the same
state. When the host's zone is loaded again (it edits a sim, or travels) its
client is rebuilt from scratch and the guest's is not, so the guest has to be
rebuilt as well, from what the host loaded.

The whole save lives in Python: the persistence service owns one
`SaveGameData` message, which a zone is initialised from, and hands the
objects of a zone to the game client as a `ZoneObjectData` message. So:

- the host saves the way the game does before any travel, and sends both
  messages (`take`);
- the guest keeps them (`adopt`) and asks its game client to travel to the
  host's zone. When the client disconnects to do so, the guest's own save
  step is replaced by putting the host's messages in place, and its services
  are shut down as on a full load rather than kept as on a travel, so that
  sims and households are read again too. Only that travel puts them in
  place: the game saves the same way when the player leaves by the menu,
  and a save put in place then would be loaded where the player went.

Two saves of different new games name the same lot with different zone
ids, so zones are matched by lot (`lot_of`), and a guest renames the host's
ids to its own (`_as_ours`) and back (`as_host`). A guest from a save of its
own (`cross_save`) goes on playing its own household and account in the
host's save, and keeps its own home as it has it (`_keep_ours`).

`pack`, `unpack`, `rename`, `lot_of`, `ZoneNames`, and `on_lot` and
`_zone_for` given a persistence service, are pure; the rest only runs
inside the game.
"""

import json
import os
import struct
import time

from tsmp import log, paths

try:
    import zlib
except ImportError:  # the game's Python is cut down
    zlib = None

MAGIC = b"TSS1"
_HEADER = struct.Struct("<4sBQII")
_COMPRESSED = 1
# Seconds the game client is given to start the reload once asked, unless
# the caller says otherwise.
PENDING_SECONDS = 90

# Bytes of the last ZoneObjectData the simulation gave the game client for
# each zone it saved, by zone id. A guest on a lot the host has left needs
# that lot as the host left it.
_objects = {}
# Host: zones of a guest's own save, by (the player whose save it is, id),
# and the zone of ours it loads in their place. Every new game makes up its
# ids, so two guests' saves may each have a zone of the same id on lots of
# their own.
_leads = {}
# (zone id, save bytes, objects bytes, deadline) waiting for the game client
# to disconnect.
_pending = None
# Whether the guest plays along with the host on the lot of the snapshot
# waiting, None when the caller did not say.
_pending_joined = None
# The game began the travel that `adopt` asked for, so the next save to
# leave the zone is the one to put the host's save in place of.
_travel_armed = False
# Guest: the zone the host's save last put in place was for, 0 for none
# since a load from the menu.
_adopted_zone = 0
_installed = False

CROSS_FILE = "cross-saves.json"


class CrossSaves:
    """Guest: the saves into which a host's save of another new game was
    put in place (see `cross_save`), each with the household we play there.

    Once put in place, the host's save carries the host's guid, and nothing
    in it tells a game started again from it that it holds a family from
    another save: until it loads the host's state anew, it would take
    itself for a copy of the host's save (one family together). The record
    lives in a small JSON file next to the record of households, so that it
    outlasts the game; it is read once and written whole, and a failure to
    write is only logged. The household tells such a save from a copy of
    the host's save given to us later for one family together, which plays
    the host's household."""

    # Saves remembered; the oldest are forgotten first.
    LIMIT = 64

    def __init__(self, path):
        self._path = path
        self._entries = None

    def played(self, save):
        """The household we play in our save `save` brought over from
        another, 0 when it is no such save."""
        return self._load().get(str(save), 0) if save else 0

    def note(self, save, household_id):
        if not save or not household_id or self.played(save) == household_id:
            return
        entries = self._load()
        entries.pop(str(save), None)
        entries[str(save)] = household_id
        while len(entries) > self.LIMIT:
            del entries[next(iter(entries))]
        self._write()

    def _load(self):
        if self._entries is None:
            self._entries = {}
            if self._path and os.path.exists(self._path):
                try:
                    with open(self._path, "r", encoding="utf-8") as f:
                        value = json.load(f)
                    if isinstance(value, dict):
                        self._entries = dict((k, v) for k, v in value.items() if isinstance(v, int))
                except (OSError, ValueError):
                    log.exception("resync: could not read {}".format(self._path))
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
            log.exception("resync: could not write {}".format(self._path))


_cross_saves = None


def _crossed():
    global _cross_saves
    if _cross_saves is None:
        try:
            directory = paths.data_dir()
        except OSError:
            log.exception("resync: no folder to keep the record of saves of families of their own in")
            directory = None
        _cross_saves = CrossSaves(os.path.join(directory, CROSS_FILE) if directory else None)
    return _cross_saves


def pack(zone_id, save, objects=b""):
    body = save + objects
    flags = 0
    if zlib is not None:
        body = zlib.compress(body, 1)
        flags = _COMPRESSED
    return _HEADER.pack(MAGIC, flags, zone_id, len(save), len(objects)) + body


def unpack(payload):
    """(zone id, save bytes, objects bytes). Raises ValueError."""
    if len(payload) < _HEADER.size:
        raise ValueError("snapshot is cut off")
    magic, flags, zone_id, save_size, objects_size = _HEADER.unpack_from(payload)
    if magic != MAGIC:
        raise ValueError("not a snapshot")
    body = payload[_HEADER.size:]
    if flags & _COMPRESSED:
        if zlib is None:
            raise ValueError("snapshot is compressed and this game cannot read it")
        try:
            body = zlib.decompress(body)
        except zlib.error as error:
            raise ValueError("snapshot is damaged: {}".format(error))
    if len(body) != save_size + objects_size:
        raise ValueError("snapshot has the wrong size")
    return zone_id, body[:save_size], body[save_size:]


class ZoneNames:
    """Guest: the host's id of each zone that has another id here, by ours,
    and the guid of the host's save they were made for.

    The table only means something in that save (or in ours while it is
    being replaced by it): a later session in another save has other zones,
    and reporting them under the old host's ids would make the new host
    send the wrong lot."""

    def __init__(self):
        self.save = 0
        self._theirs = {}

    def adopt(self, save, names):
        """`names` maps the host's ids to ours, as a snapshot was renamed."""
        self.save = save
        self._theirs = dict((ours, theirs) for theirs, ours in names.items())

    def forget(self):
        self.save = 0
        self._theirs = {}

    def to_host(self, save):
        """Our id -> the host's, for a game in the save `save`. Forgotten
        as soon as that is not the save they were made for."""
        if self.save and save != self.save:
            self.forget()
        return self._theirs


_names = ZoneNames()
_save_unknown = False
# Guest: how many of the host's snapshots were put in place, which tells a
# reload into the host's state from one of our own.
_put = 0
# Guest: the guid of the host's save last put in place, 0 once a save of
# our own was loaded since.
_adopted = 0
# Guest: zones our own simulation saved since the host's state was last put
# in place: those it ran, and of which what we have is newer than the
# host's copy. While objects are put in place of a zone's, they are not ours.
_ran = set()
_placing = False
# Guest: zones whose objects came with a snapshot of the host's save, and
# that save. Of any other, what our game client loads is our own older copy.
_provided = set()
_provided_save = 0
# A zone was left by a travel, which keeps the game's services up
# (`disable_shutdown`); a save loaded from the menu is not. Every exit from
# a zone saves to the scratch slot, so each such save tells its own exit
# from the last one, whether a load that followed it was consumed or not.
# Our own saves (for a snapshot, a household) save the same way and are
# not exits.
_travelled = False
_travelling = False
_saving_ours = False
# Our game saves to leave its zone, which can take a while.
_leaving = False


def current_save():
    """The guid of the save this game plays (`SaveGameData.guid`), which
    tells saves of different new games apart; 0 when it cannot be read."""
    global _save_unknown
    try:
        import services

        return services.get_persistence_service().get_save_slot_proto_guid() or 0
    except Exception:
        # Asked every tick: one line in the log is enough.
        if not _save_unknown:
            _save_unknown = True
            log.exception("resync: cannot tell which save is played")
        return 0


def _host_names():
    # While the host's save waits to be put in place ours is still loaded,
    # and the table is the one just made for it.
    return _names.to_host(_names.save if _pending is not None else current_save())


def host_zone(zone_id):
    """Guest: the id the host has for our zone `zone_id`."""
    return _host_names().get(zone_id, zone_id)


def forget_names():
    """Guest: our zones are no longer named after any host's."""
    _names.forget()


def snapshots_put():
    """Guest: how many of the host's snapshots have been put in place."""
    return _put


def _note_provided(zone_id, objects):
    global _provided_save
    if _names.save != _provided_save:
        _provided.clear()
        _provided_save = _names.save
    if objects:
        _provided.add(zone_id)
    else:
        _provided.discard(zone_id)


def provided_zones():
    """Guest: the zones whose objects the host gave us in its save."""
    return frozenset(_provided)


def leaving_zone():
    """Whether our game has begun saving to leave its zone, since it last
    entered one. A guest from a save of its own counts the travel to the
    host's snapshot from when the game began it (`disable_shutdown`): the
    save comes after, and waits for any save under way to end first."""
    return _leaving or (_travel_armed and cross_save())


def _forget_travel():
    global _travelled
    travelled, _travelled = _travelled, False
    return travelled


def loaded_by_hand():
    """Guest, once a zone has loaded: whether it was loaded from the menu
    rather than reached by a travel. Such a load replaces the save in memory
    with one from disk, so what came from the host's save is forgotten."""
    global _adopted, _adopted_zone, _leaving
    _leaving = False
    if _forget_travel():
        return False
    _names.forget()
    _ran.clear()
    _provided.clear()
    _adopted = 0
    _adopted_zone = 0
    return True


def adopted_zone():
    """Guest: the zone the host's save last put in place was for, 0 when
    none was since a load from the menu. The game may load another one:
    the travel can be turned away, or the player may leave by the menu."""
    return _adopted_zone


def cross_save():
    """Guest: whether the host plays a save of another new game than ours,
    as the last snapshot showed: its zones had to be renamed to ours. Then
    we play our own household in it, under our own account, and the walls
    of each lot are those of our own save, not the host's. In a copy of the
    host's save (one family together) nothing is renamed. A game started
    again from a save the host's was put in place in knows it from the
    record of such saves (`CrossSaves`) until its first snapshot."""
    if _names.save and _names._theirs:
        return True
    save = current_save()
    played = _crossed().played(save) if save else 0
    if not played:
        return False
    try:
        import services

        # Asked every tick: a game that cannot tell is not one.
        return played == services.active_household_id()
    except Exception:
        return False


def cancel():
    """Guest: give up the snapshot waiting for our game to reload, whose
    travel the game did not start. One whose travel began is left alone:
    the game is past the point where it could keep its services, and
    loads our own save in full without it. Its deadline still bounds it
    (`pending`)."""
    global _pending, _pending_joined, _travel_armed
    if _travel_armed:
        log.info("resync: the travel to the host's snapshot has begun; it is not given up")
        return
    if _pending is not None:
        log.info("resync: the snapshot was given up")
    _pending = None
    _pending_joined = None
    _travel_armed = False


def ran_zones():
    """Guest: the zones our own simulation ran since we last loaded the
    host's state."""
    return frozenset(_ran)


def playing_adopted():
    """Guest: the save we play came from the host, so what our simulation
    has of anything it did not run itself is the host's copy, as it was
    when we loaded it. Only once the host's save is in place: a load that
    failed leaves our own."""
    return bool(_adopted) and _adopted == current_save()


def adopted_save():
    """Guest: the guid of the host's save our zone names were matched to,
    0 when none was."""
    return _names.save


def lot_of(zone):
    """"<lot_description_id>:<active_plex>" of a ZoneData, "" for none.
    It says which lot of the world (and which unit of it) a zone is, the
    same in every save of every new game, whereas zone ids are made up anew
    by each new game; so this is how zones of two saves are matched."""
    if zone is None or not zone.lot_description_id:
        return ""
    return "{}:{}".format(zone.lot_description_id, getattr(zone, "active_plex", 0))


def on_lot(persistence, lot, zone_id=0):
    """The ZoneData of `persistence` on the lot `lot` (see `lot_of`), None
    for none. The zone `zone_id` is checked first, as it usually is the one."""
    if not lot:
        return None
    zone = persistence.get_zone_proto_buff(zone_id) if zone_id else None
    if zone is not None and lot_of(zone) == lot:
        return zone
    for zone in persistence.zone_proto_buffs_gen():
        if lot_of(zone) == lot:
            return zone
    return None


def lot_key(zone_id):
    """`lot_of` the zone `zone_id` of this game's save, "" when unknown."""
    if not zone_id:
        return ""
    try:
        import services

        return lot_of(services.get_persistence_service().get_zone_proto_buff(zone_id))
    except Exception:
        log.exception("resync: could not read the lot of zone {:#x}".format(zone_id))
        return ""


def pending():
    """True while a snapshot waits for the game client to reload."""
    global _pending, _travel_armed
    if _pending is not None and time.time() > _pending[3]:
        _pending = None
        _travel_armed = False
        log.info("resync: the game client did not reload, snapshot dropped")
    return _pending is not None


def install():
    """Hook the three places a reload passes through. Safe to call twice."""
    global _installed
    if _installed:
        return
    import functools

    import game_services
    import persistence_module
    from services.persistence_service import PersistenceService

    save_objects = persistence_module.PersistenceOpType.kPersistenceOpSaveZoneObjects
    original_run = persistence_module.run_persistence_operation

    @functools.wraps(original_run)
    def run_persistence_operation(operation, message, *args, **kwargs):
        if operation == save_objects:
            try:
                _objects[message.zone_id] = message.SerializeToString()
                if not _placing:
                    _ran.add(message.zone_id)
            except Exception:
                log.exception("resync: could not keep the zone's objects")
        return original_run(operation, message, *args, **kwargs)

    persistence_module.run_persistence_operation = run_persistence_operation

    original_save = PersistenceService.save_to_scratch_slot_gen

    @functools.wraps(original_save)
    def save_to_scratch_slot_gen(self, timeline):
        global _travelled, _travelling, _travel_armed, _leaving, _pending, _pending_joined
        # One family together: any save puts the host's in place, and any
        # exit from a zone counts as a travel, as it always did.
        cross = cross_save()
        armed = False
        if not _saving_ours:
            _leaving = True
            _travelled = _travelling if cross else True
            _travelling = False
            armed, _travel_armed = _travel_armed, False
        if pending():
            if armed or not cross:
                # Our own state is the one being replaced: saving it
                # would only overwrite what the host sent.
                _put_in_place(self)
                yield from _hand_over_gen(self, timeline)
                return 0
            if not _saving_ours:
                # Manage Worlds, the main menu or a quit: the game goes
                # wherever the player chose, and would load the host's save
                # there, with the host's household on the host's lot.
                _pending = None
                _pending_joined = None
                log.info("resync: the game saved to leave its zone without travelling; the host's snapshot is dropped")
        result = yield from original_save(self, timeline)
        return result

    PersistenceService.save_to_scratch_slot_gen = save_to_scratch_slot_gen

    original_disable = game_services.disable_shutdown

    @functools.wraps(original_disable)
    def disable_shutdown():
        global _travelling, _travel_armed
        # The game calls this only as it begins a travel, before it saves.
        _travelling = True
        if pending():
            # A travel keeps sims and households in memory. Ours are stale,
            # so let them go and be loaded again from the host's save.
            _travel_armed = True
            log.info("resync: reloading in full instead of travelling")
            return
        return original_disable()

    game_services.disable_shutdown = disable_shutdown
    _installed = True


def _hand_over_gen(persistence, timeline):
    """Give the save now in place to the game client, as the last step of a
    save does. The client builds the world around a zone from what it was
    last given, and a save of the host's can name zones ours never had."""
    try:
        import element_utils
        import persistence_module

        waiting = element_utils.soft_sleep_forever()
        persistence_module.run_persistence_operation(
            persistence_module.PersistenceOpType.kPersistenceOpSave,
            persistence.get_save_game_data_proto(), 0,
            lambda slot_id, success: waiting.trigger_soft_stop())
        yield from element_utils.run_child(timeline, waiting)
        log.info("resync: the game client has the host's save")
    except Exception:
        log.exception("resync: could not give the host's save to the game client")


def _put_in_place(persistence):
    global _pending, _pending_joined, _put, _adopted, _adopted_zone, _travel_armed
    zone_id, save, objects, _ = _pending
    joined = _pending_joined
    _pending = None
    _pending_joined = None
    _travel_armed = False
    cross = cross_save()
    data = persistence.get_save_game_data_proto()
    ours = None
    if cross:
        try:
            # Read before our save is cleared: what of it stays ours.
            ours = _OursBefore(persistence, data, _active_household_id())
        except Exception:
            log.exception("resync: could not read what of our save is ours; the host's is taken whole")
    _put += 1
    _adopted = _names.save
    _adopted_zone = zone_id
    data.Clear()
    data.MergeFromString(save)
    theirs = objects
    if ours is not None:
        try:
            objects = _keep_ours(data, ours, zone_id, objects, joined)
        except Exception:
            log.exception("resync: could not keep what of our save is ours")
    if cross:
        # A game started again from this save is to know it too.
        _crossed().note(data.guid, data.save_slot.active_household_id)
    _ran.clear()
    if not cross:
        # One family together: the load that follows is the travel that
        # began this save, as it always was.
        _forget_travel()
    _note_provided(zone_id, objects if objects == theirs else b"")
    persistence.build_caches()
    if objects:
        keep_objects(zone_id, objects)
    log.info("resync: the host's save is in place ({} bytes, {} of objects), zone {:#x}{}".format(
        len(save), len(objects), zone_id, ", after the game began travelling" if cross else ""))


def _active_household_id():
    """The household our game plays, 0 when it cannot tell."""
    try:
        import services

        return services.active_household_id() or 0
    except Exception:
        log.exception("resync: cannot tell which household is played")
        return 0


def _copy(message):
    copied = type(message)()
    copied.CopyFrom(message)
    return copied


class _OursBefore:
    """Guest: what of our own save is still ours once the host's is put in
    its place, read while ours is still there: the household we play, our
    account, and our household's home with its objects and the sims of
    ours that stand on it."""

    def __init__(self, persistence, data, household_id):
        self.household_id = household_id or data.save_slot.active_household_id
        self.account = _copy(data.account)
        self.save = data.guid
        # Who made our household, which a save of ours that an earlier
        # version of the mod gave the host's account still names (see
        # `_our_account`).
        self.creator = (0, "")
        self.home = 0
        self.objects = b""
        self.sims = {}
        household = persistence.get_household_proto_buff(self.household_id) if self.household_id else None
        if household is None:
            return
        self.creator = (getattr(household, "creator_id", 0) or 0, getattr(household, "creator_name", "") or "")
        self.home = household.home_zone
        if not self.home:
            return
        # What we last saved of our home, unless it came from the host and
        # our simulation did not run that lot since.
        if self.home not in _provided or self.home in _ran:
            self.objects = objects_of(self.home)
        for sim_id in household.sims.ids:
            sim = persistence.get_sim_proto_buff(sim_id)
            if sim is not None and sim.zone_id == self.home:
                self.sims[sim_id] = _copy(sim)


def _keep_ours(data, ours, zone_id, objects, joined):
    """Guest from a save of its own: make the host's save `data`, just put
    in place, ours where it is ours, and return the objects to put in place
    of the zone `zone_id`, `objects` being the host's.

    - The household we play stays the one played, as it was before the
      load, or the game takes us for another player on that lot and starts
      our sims anew, dropping what they were doing.
    - Our account stays ours. The host's save names the host's account as
      the owner of every lot and household; our game is logged in as us.
    - Our own home, while the host is not on it, is our own lot: its walls
      are those of our save, which the host's simulation of its objects and
      of our sims there never saw.
    """
    if joined is None:
        # Before the household played is made ours.
        joined = _host_is_on(data, zone_id)
    played = ours.household_id
    if played and any(household.household_id == played for household in data.households):
        data.save_slot.active_household_id = played
        for zone in data.zones:
            # A zone never saved has nothing to compare with; giving it
            # gameplay data would make it look visited.
            if (zone.household_id == played or zone.zone_id == zone_id) and zone.HasField("gameplay_zone_data"):
                zone.gameplay_zone_data.active_household_id_on_save = played
    else:
        log.info("resync: the host's save has not our household {:#x}".format(played))
    renamed = as_our_account(data, _our_account(data, ours))
    if renamed:
        log.info("resync: {} records of the host's account are ours here".format(renamed))
    if not ours.home or zone_id != ours.home or joined:
        return objects
    by_id = dict((sim.sim_id, sim) for sim in data.sims)
    for sim_id, before in ours.sims.items():
        sim = by_id.get(sim_id)
        if sim is not None:
            _put_back(sim, before)
    log.info("resync: our home {:#x} keeps {} and {} of our sims where they were".format(
        zone_id, "our copy of its objects" if ours.objects else "the objects our game client has", len(ours.sims)))
    return ours.objects


def _our_account(data, ours):
    """The AccountData that is ours, for the host's save `data` just put in
    place. The one our save had, unless that is the host's: a save of ours
    the host's was put in place in by version 0.0.1 of the mod took the
    host's account whole, and our own is left only as the one that made
    the household we play. The game reads the account it is logged in as
    from that same save, so nothing else tells it. That is only taken in a
    save that came from this host's (the same guid): elsewhere a household
    made by someone else, say from the gallery, would name a stranger."""
    account = ours.account
    if not account.nucleus_id or account.nucleus_id != data.account.nucleus_id:
        return account
    creator, name = ours.creator
    if ours.save == data.guid and creator and creator != account.nucleus_id:
        account = _copy(account)
        account.nucleus_id = creator
        if name:
            account.persona_name = name
        log.info("resync: our save carries the host's account {:#x}, as version 0.0.1 left it; the account that "
                 "made our household, {:#x}, is ours".format(data.account.nucleus_id, creator))
        return account
    log.info("resync: our save carries the host's account {:#x}; it stays the host's here".format(
        data.account.nucleus_id))
    return account


def _host_is_on(data, zone_id):
    """Whether the host's save `data` has the household it plays on the zone
    `zone_id`: the host is there, and the guest joins it."""
    played = data.save_slot.active_household_id
    return any(sim.household_id == played and sim.zone_id == zone_id for sim in data.sims) if played else False


# Where a SimData says its sim stands and what it was doing there, which the
# game reads back together.
_WHERE = ("location", "interaction_state")


def _put_back(sim, before):
    """Give the SimData `sim` the place of `before`, our own copy of it."""
    sim.zone_id = before.zone_id
    sim.world_id = before.world_id
    for name in _WHERE:
        if before.gameplay_data.HasField(name):
            getattr(sim.gameplay_data, name).CopyFrom(getattr(before.gameplay_data, name))
        else:
            sim.gameplay_data.ClearField(name)


def as_our_account(data, account):
    """Make the account of the SaveGameData `data` `account` (an
    AccountData), and every record of its own account in it ours: the
    owner of the save, of its households, sims and zones, and of the lots
    of its neighbourhoods. Records of any other account are left as they
    are. Returns how many were changed, 0 when the accounts are the same."""
    theirs, persona = data.account.nucleus_id, data.account.persona_name
    mine = account.nucleus_id
    if not mine or mine == theirs:
        return 0
    data.account.CopyFrom(account)
    count = 1

    def ours(record, name):
        # A game whose messages lack a field has nothing of it to change.
        if getattr(record, name, None) == theirs:
            setattr(record, name, mine)
            return 1
        return 0

    def pairs(entries):
        changed = 0
        for pair in entries or ():
            if pair.nucleus_id == theirs:
                pair.nucleus_id = mine
                if pair.persona_name == persona:
                    pair.persona_name = account.persona_name
                changed += 1
        return changed

    count += ours(data.save_slot, "nucleus_id")
    for household in data.households:
        count += ours(household, "account_id")
    for sim in data.sims:
        count += ours(sim, "nucleus_id")
    for zone in data.zones:
        count += ours(zone, "nucleus_id")
    for neighborhood in data.neighborhoods:
        count += ours(neighborhood, "owner_id")
        for lot in neighborhood.lots:
            count += pairs(lot.lot_owner)
        count += pairs(getattr(neighborhood, "households", None)) + pairs(getattr(neighborhood, "npc_households", None))
    return count


def lead(zone_id, ours, player=""):
    """Host: a guest of `player` that asks for `zone_id`, which is a zone of
    its own save and not of ours, is to load our zone `ours` instead."""
    if zone_id and ours and zone_id != ours:
        _leads[(player, zone_id)] = ours


def snapshot_zone(zone_id, lot, player=""):
    """Host: the zone of ours a snapshot for a guest of `player` in
    `zone_id` on `lot` would be of, 0 when that cannot be told."""
    import services

    return _zone_for(services.get_persistence_service(), zone_id, lot, services.current_zone_id(),
                     say=lambda text: None, player=player)


def _zone_for(persistence, zone_id, lot, current, say=log.info, player=""):
    """The zone of our save a guest of `player` in `zone_id`, on the lot
    `lot` (see `lot_of`), is to load. Zone ids are made up anew by every new
    game, so a guest that comes from a save of its own names a zone we do
    not have, or even one we have for another lot; the lot is what both
    saves agree on. Then comes the id, then where `lead` sent that zone of
    that player's, then our own zone `current`."""
    zone = on_lot(persistence, lot, zone_id)
    if zone is not None:
        if zone.zone_id != zone_id:
            say("resync: the guest's lot {} is our zone {:#x}".format(lot, zone.zone_id))
        return zone.zone_id
    if zone_id and persistence.get_zone_proto_buff(zone_id) is None:
        led = _leads.get((player, zone_id), 0)
        if led and persistence.get_zone_proto_buff(led) is None:
            led = 0
        say("resync: zone {:#x} is not in this save; the guest loads {} instead".format(
            zone_id, "{:#x}".format(led) if led else "our zone"))
        zone_id = led
    return zone_id or current or 0


def objects_of(zone_id):
    """Bytes of the objects of a zone as this game last saved them."""
    return _objects.get(zone_id, b"")


def keep_objects(zone_id, objects):
    """Make `objects` (bytes of a ZoneObjectData) the saved objects of a
    zone, here and in the game client, which is what loads them."""
    import persistence_module
    from protocolbuffers import FileSerialization_pb2 as serialization

    global _placing
    message = serialization.ZoneObjectData()
    message.MergeFromString(objects)
    _placing = True
    try:
        persistence_module.run_persistence_operation(
            persistence_module.PersistenceOpType.kPersistenceOpSaveZoneObjects, message, 0, None)
    finally:
        _placing = False
    _objects[zone_id] = objects


def save_then(done):
    """Save the way the game does before a travel, then call
    `done(persistence service)`, or `done(None)` when the game refused. The
    save runs over several ticks."""
    import services

    persistence = services.get_persistence_service()

    def save_gen(timeline):
        global _saving_ours
        saved = None
        _saving_ours = True
        try:
            result = yield from persistence.save_to_scratch_slot_gen(timeline)
            if result != 0:
                log.info("resync: the game refused to save ({})".format(result))
            else:
                saved = persistence
        except Exception:
            log.exception("resync: saving failed")
        finally:
            _saving_ours = False
        try:
            done(saved)
        except Exception:
            log.exception("resync: could not use the save")

    persistence.save_using(save_gen)


def take(done, places):
    """Host: save once, then call `done(payloads)`: a snapshot, or None when
    it could not be made, for each (zone id, lot, player[, cross]) of
    `places`, or `done(None)` when the game did not save. A snapshot is for
    a guest of `player` in `zone_id` on the lot `lot` (`lot_key` of that
    zone in the guest's save), or in the host's own zone when neither is
    known here; `cross` says the guest plays a save of its own. Guests that
    wait at once share the save, the slow part, and the pause it takes;
    what differs between them is the zone whose objects go with it, and
    guests of one zone share the packed snapshot too. The session pauses
    the game for a guest that will join; one that will play another lot
    does not stop ours.

    A guest from a save of its own is sent no objects of the home of a
    household that came from another save: the walls of that lot here are
    ours, not those of the save the household lives in, and what our
    simulation did with its objects on them is wrong there. The guest
    keeps its own copy."""
    import services

    def saved(persistence):
        save = None
        try:
            if persistence is not None:
                save = persistence.get_save_game_data_proto().SerializeToString()
        except Exception:
            log.exception("resync: snapshot failed")
        if save is None:
            done(None)
            return
        current = services.current_zone_id()
        packed = {}
        payloads = []
        for place in places:
            zone_id, lot = place[0], place[1]
            payload = None
            try:
                target = _zone_for(persistence, zone_id, lot, current, player=place[2] if len(place) > 2 else "")
                bare = len(place) > 3 and bool(place[3]) and foreign_home(persistence, target)
                key = (target, bare)
                if key not in packed:
                    objects = b"" if bare else _objects.get(target, b"")
                    packed[key] = pack(target, save, objects)
                    log.info("resync: snapshot for zone {:#x} taken, {} bytes ({} to send){}".format(
                        target, len(save) + len(objects), len(packed[key]),
                        "; the home of a household from another save goes without its objects" if bare else ""))
                payload = packed[key]
            except Exception:
                log.exception("resync: snapshot failed")
            payloads.append(payload)
        done(payloads)

    save_then(saved)


def foreign_home(persistence, zone_id):
    """Host: whether our zone `zone_id` is the home of a household taken in
    from a save of another new game (see household.from_another_save)."""
    from tsmp import household

    zone = persistence.get_zone_proto_buff(zone_id) if zone_id else None
    return zone is not None and bool(zone.household_id) and household.from_another_save(zone.household_id)


def rename(message, names):
    """Replace every 64-bit number in a protobuf message that is a key of
    `names` with its value. Returns how many were replaced."""
    count = 0
    for field, value in message.ListFields():
        kind = field.cpp_type
        # `label` is what the game's protobuf has; newer ones say is_repeated.
        many = field.label == 3 if hasattr(field, "label") else field.is_repeated
        if kind == 10:
            for item in (value if many else (value,)):
                count += rename(item, names)
        elif kind in (2, 4):
            if many:
                for index, item in enumerate(value):
                    if item in names:
                        value[index] = names[item]
                        count += 1
            elif value in names and not field.is_extension:
                setattr(message, field.name, names[value])
                count += 1
    return count


def _renamed(names, data, objects):
    """Rename zone ids by `names` in `data` (a message, changed in place)
    and in `objects` (bytes of a ZoneObjectData). Returns (ids renamed,
    objects)."""
    if not names:
        return 0, objects
    count = rename(data, names)
    if objects:
        from protocolbuffers import FileSerialization_pb2 as serialization

        contents = serialization.ZoneObjectData()
        contents.MergeFromString(objects)
        count += rename(contents, names)
        objects = contents.SerializeToString()
    return count, objects


def as_host(data, zone_id, objects=b""):
    """Guest: a part of our save as the host names it, the other way round
    from `_as_ours`. Once we play in a copy of the host's save, its lots go
    by our ids here, which the host does not know. `data` is renamed in
    place; returns (the host's id for `zone_id`, `objects` renamed)."""
    names = _host_names()
    count, objects = _renamed(names, data, objects)
    if count:
        log.info("resync: {} ids renamed to the host's".format(count))
    return names.get(zone_id, zone_id), objects


def _as_ours(zone_id, save, objects):
    """A snapshot of the host's with its zones under the ids they have
    here. Every new game makes zone ids up, and the game client can only
    travel to the ones it was started with; what does not change between
    saves is which lot of the world a zone is."""
    import services
    from protocolbuffers import FileSerialization_pb2 as serialization

    here = {}
    for zone in services.get_persistence_service().zone_proto_buffs_gen():
        here.setdefault(lot_of(zone), zone.zone_id)
    here.pop("", None)
    data = serialization.SaveGameData()
    data.MergeFromString(save)
    names = {}
    for zone in data.zones:
        ours = here.get(lot_of(zone))
        if ours and ours != zone.zone_id:
            names[zone.zone_id] = ours
    _names.adopt(getattr(data, "guid", 0) or 0, names)
    if not names:
        return zone_id, save, objects
    replaced, objects = _renamed(names, data, objects)
    save = data.SerializeToString()
    log.info("resync: the host's save names {} of its {} zones differently; {} ids renamed to ours".format(
        len(names), len(data.zones), replaced))
    if zone_id not in names:
        log.info("resync: the host's zone {:#x} is not a lot of our save".format(zone_id))
    return names.get(zone_id, zone_id), save, objects


def adopt(payload, sim_ids, seconds=PENDING_SECONDS, joined=None):
    """Guest: keep the host's snapshot and ask the game client to load the
    zone it was made for, with the sims `sim_ids`: the one we are in or are
    travelling to, or the host's choice when its save does not have ours;
    the client has `seconds` to start. `joined` says whether we will play
    along with the host there; None leaves it to where the host's sims
    stand in its save. Raises ValueError for a snapshot that cannot be
    read, and, from a save of our own, for no sim to travel with."""
    global _pending, _pending_joined, _travel_armed
    zone_id, save, objects = unpack(payload)
    if not zone_id or not save:
        raise ValueError("snapshot is empty")
    install()
    zone_id, save, objects = _as_ours(zone_id, save, objects)
    sim_ids = [sim_id for sim_id in sim_ids if sim_id]
    if not sim_ids and cross_save():
        # A travel with nobody goes nowhere, and the snapshot would wait
        # for a load that never comes.
        raise ValueError("no sim to travel with")
    from world import travel_service

    _pending = (zone_id, save, objects, time.time() + seconds)
    _pending_joined = joined
    # Only a travel that begins from now on is the one asked for.
    _travel_armed = False
    log.info("resync: loading zone {:#x} from the host's save".format(zone_id))
    travel_service.travel_sims_to_zone(sim_ids, zone_id)
