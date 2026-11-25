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
  sims and households are read again too.

Two saves of different new games name the same lot with different zone
ids, so zones are matched by lot (`lot_of`), and a guest renames the host's
ids to its own (`_as_ours`) and back (`as_host`).

`pack`, `unpack`, `rename`, `lot_of`, `ZoneNames`, and `on_lot` and
`_zone_for` given a persistence service, are pure; the rest only runs
inside the game.
"""

import struct
import time

from tsmp import log

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
# Host: zones of a guest's own save, by id, and the zone of ours it loads
# in their place.
_leads = {}
# (zone id, save bytes, objects bytes, deadline) waiting for the game client
# to disconnect.
_pending = None
_installed = False


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
# A zone was left by a travel, which saves to the scratch slot first; a save
# loaded from the menu is not. Our own saves (for a snapshot, a household)
# save the same way and are not travels.
_travelled = False
_saving_ours = False


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


def _forget_travel():
    global _travelled
    travelled, _travelled = _travelled, False
    return travelled


def loaded_by_hand():
    """Guest, once a zone has loaded: whether it was loaded from the menu
    rather than reached by a travel. Such a load replaces the save in memory
    with one from disk, so what came from the host's save is forgotten."""
    global _adopted
    if _forget_travel():
        return False
    _names.forget()
    _ran.clear()
    _provided.clear()
    _adopted = 0
    return True


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
    global _pending
    if _pending is not None and time.time() > _pending[3]:
        _pending = None
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
        global _travelled
        if not _saving_ours:
            _travelled = True
        if pending():
            # Our own state is the one being replaced: saving it would only
            # overwrite what the host sent.
            _put_in_place(self)
            yield from _hand_over_gen(self, timeline)
            return 0
        result = yield from original_save(self, timeline)
        return result

    PersistenceService.save_to_scratch_slot_gen = save_to_scratch_slot_gen

    original_disable = game_services.disable_shutdown

    @functools.wraps(original_disable)
    def disable_shutdown():
        if pending():
            # A travel keeps sims and households in memory. Ours are stale,
            # so let them go and be loaded again from the host's save.
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
    global _pending, _put
    zone_id, save, objects, _ = _pending
    _pending = None
    global _adopted
    _put += 1
    _adopted = _names.save
    _ran.clear()
    _forget_travel()
    _note_provided(zone_id, objects)
    data = persistence.get_save_game_data_proto()
    data.Clear()
    data.MergeFromString(save)
    persistence.build_caches()
    if objects:
        keep_objects(zone_id, objects)
    log.info("resync: the host's save is in place ({} bytes, {} of objects), zone {:#x}".format(
        len(save), len(objects), zone_id))


def lead(zone_id, ours):
    """Host: a guest that asks for `zone_id`, which is a zone of its own save
    and not of ours, is to load our zone `ours` instead."""
    if zone_id and ours and zone_id != ours:
        _leads[zone_id] = ours


def snapshot_zone(zone_id, lot):
    """Host: the zone of ours a snapshot for a guest in `zone_id` on `lot`
    would be of, 0 when that cannot be told."""
    import services

    return _zone_for(services.get_persistence_service(), zone_id, lot, services.current_zone_id(),
                     say=lambda text: None)


def _zone_for(persistence, zone_id, lot, current, say=log.info):
    """The zone of our save a guest in `zone_id`, on the lot `lot` (see
    `lot_of`), is to load. Zone ids are made up anew by every new game, so a
    guest that comes from a save of its own names a zone we do not have, or
    even one we have for another lot; the lot is what both saves agree on.
    Then comes the id, then where `lead` sent that zone, then our own zone
    `current`."""
    zone = on_lot(persistence, lot, zone_id)
    if zone is not None:
        if zone.zone_id != zone_id:
            say("resync: the guest's lot {} is our zone {:#x}".format(lot, zone.zone_id))
        return zone.zone_id
    if zone_id and persistence.get_zone_proto_buff(zone_id) is None:
        led = _leads.get(zone_id, 0)
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


def take(done, zone_id=0, lot=""):
    """Host: save, then call `done(payload)`, or `done(None)` on failure.
    The snapshot is for a guest in `zone_id` on the lot `lot` (`lot_key` of
    that zone in the guest's save), or in the host's own zone when neither
    is known here. The session pauses the game for a guest that will join;
    one that will play another lot does not stop ours."""
    import services

    def saved(persistence):
        payload = None
        try:
            if persistence is not None:
                target = _zone_for(persistence, zone_id, lot, services.current_zone_id())
                save = persistence.get_save_game_data_proto().SerializeToString()
                objects = _objects.get(target, b"")
                payload = pack(target, save, objects)
                log.info("resync: snapshot for zone {:#x} taken, {} bytes ({} to send)".format(
                    target, len(save) + len(objects), len(payload)))
        except Exception:
            log.exception("resync: snapshot failed")
        done(payload)

    save_then(saved)


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


def adopt(payload, sim_id, seconds=PENDING_SECONDS):
    """Guest: keep the host's snapshot and ask the game client to load the
    zone it was made for: the one we are in, or the host's choice when its
    save does not have ours; the client has `seconds` to start. Raises
    ValueError for a snapshot that cannot be read."""
    global _pending
    zone_id, save, objects = unpack(payload)
    if not zone_id or not save:
        raise ValueError("snapshot is empty")
    install()
    zone_id, save, objects = _as_ours(zone_id, save, objects)
    from world import travel_service

    _pending = (zone_id, save, objects, time.time() + seconds)
    log.info("resync: loading zone {:#x} from the host's save".format(zone_id))
    travel_service.travel_sims_to_zone([sim_id] if sim_id else [], zone_id)
