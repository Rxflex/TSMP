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

`pack` and `unpack` are pure; the rest only runs inside the game.
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
# Seconds the game client is given to start the reload once asked.
PENDING_SECONDS = 60

# Bytes of the last ZoneObjectData the simulation gave the game client for
# each zone it saved, by zone id. A guest on a lot the host has left needs
# that lot as the host left it.
_objects = {}
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
            except Exception:
                log.exception("resync: could not keep the zone's objects")
        return original_run(operation, message, *args, **kwargs)

    persistence_module.run_persistence_operation = run_persistence_operation

    original_save = PersistenceService.save_to_scratch_slot_gen

    @functools.wraps(original_save)
    def save_to_scratch_slot_gen(self, timeline):
        if pending():
            # Our own state is the one being replaced: saving it would only
            # overwrite what the host sent.
            _put_in_place(self)
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


def _put_in_place(persistence):
    global _pending
    zone_id, save, objects, _ = _pending
    _pending = None
    import persistence_module
    from protocolbuffers import FileSerialization_pb2 as serialization

    data = persistence.get_save_game_data_proto()
    data.Clear()
    data.MergeFromString(save)
    persistence.build_caches()
    if objects:
        message = serialization.ZoneObjectData()
        message.MergeFromString(objects)
        persistence_module.run_persistence_operation(
            persistence_module.PersistenceOpType.kPersistenceOpSaveZoneObjects, message, 0, None)
    log.info("resync: the host's save is in place ({} bytes, {} of objects), zone {:#x}".format(
        len(save), len(objects), zone_id))


def take(done, zone_id=0):
    """Host: save, then call `done(payload)`, or `done(None)` on failure.
    The save runs over several ticks, like any save of the game. The snapshot
    is for a guest in `zone_id`, or in the host's own zone when that is 0."""
    import services
    from clock import ClockSpeedMode

    persistence = services.get_persistence_service()
    # What happens between the snapshot and the guest being back is lost on
    # the guest, so nothing should happen. Either player can resume.
    try:
        services.game_clock_service().set_clock_speed(ClockSpeedMode.PAUSED)
    except Exception:
        log.exception("resync: could not pause")

    def save_gen(timeline):
        payload = None
        try:
            result = yield from persistence.save_to_scratch_slot_gen(timeline)
            if result != 0:
                log.info("resync: the game refused to save ({})".format(result))
            else:
                target = zone_id or services.current_zone_id() or 0
                save = persistence.get_save_game_data_proto().SerializeToString()
                objects = _objects.get(target, b"")
                payload = pack(target, save, objects)
                log.info("resync: snapshot for zone {:#x} taken, {} bytes ({} to send)".format(
                    target, len(save) + len(objects), len(payload)))
        except Exception:
            log.exception("resync: snapshot failed")
        done(payload)

    persistence.save_using(save_gen)


def adopt(payload, sim_id):
    """Guest: keep the host's snapshot and ask the game client to reload
    the zone it was made for, which is the one we are in. Raises ValueError for a snapshot that cannot be
    read."""
    global _pending
    zone_id, save, objects = unpack(payload)
    if not zone_id or not save:
        raise ValueError("snapshot is empty")
    install()
    from world import travel_service

    _pending = (zone_id, save, objects, time.time() + PENDING_SECONDS)
    log.info("resync: reloading zone {:#x} from the host's save".format(zone_id))
    travel_service.travel_sims_to_zone([sim_id] if sim_id else [], zone_id)
