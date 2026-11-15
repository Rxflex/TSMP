"""Live mode: joins this game to the other player's through the launcher.

This is the glue between the game and the pure pieces: `bridge` (socket to
the launcher), `session` (who gets which message) and `hooks`.
"""

import functools
import json
import os

from tsmp import bridge as bridge_module
from tsmp import frames, hooks, log, native, resync, session as session_module

_bridge = None
_session = None
_ticks = 0
# Id of the zone whose loading screen has gone, and the ticks since then.
_loaded_zone = None
_loaded_ticks = 0
# Ticks to wait after the loading screen before joining, so that what the
# simulation sent while finishing the load still reaches the game client.
SETTLE_TICKS = 5
# Build mode synchronisation (tsmp/native.py) is switched on by itself once
# the session is live; these are the ticks between attempts and reports.
_native_sync = True
_native_note = None
_native_report = None
_native_samples = 0
NATIVE_START_TICKS = 32
NATIVE_REPORT_TICKS = 256
# Log lines waiting to be sent to the launcher and the other game.
_log_lines = []
MAX_LOG_LINES = 200
MAX_LOG_LINE = 300


def _name(module, name, default):
    return getattr(module, name, default)


def _consts():
    """Ids are looked up by name so a game update that renumbers them does
    not silently break routing; the defaults are from 1.129."""
    from protocolbuffers import Consts_pb2, DistributorOps_pb2

    operation = DistributorOps_pb2.Operation
    save = [
        _name(Consts_pb2, "MSG_GAME_SAVE_COMPLETE", 181),
        _name(Consts_pb2, "MSG_GAME_SAVE_LOCK_UNLOCK", 223),
        _name(Consts_pb2, "MSG_GAME_SAVE_CHEAT_LOCK", 406),
    ]
    travel = [
        _name(Consts_pb2, "MSG_TRAVEL_SIMS_TO_ZONE", 239),
        _name(Consts_pb2, "MSG_TRAVEL_MENU_SHOW", 42),
        _name(Consts_pb2, "MSG_TRAVEL_MENU_CREATE", 14),
    ]
    return session_module.Consts(
        view_update=_name(Consts_pb2, "MSG_OBJECTS_VIEW_UPDATE", 11),
        pie_menu=_name(Consts_pb2, "MSG_PIE_MENU_CREATE", 6),
        interactable=_name(Consts_pb2, "MSG_OBJECT_IS_INTERACTABLE", 5),
        set_sim_active=_name(operation, "SET_SIM_ACTIVE", 21),
        # Saving and travelling are driven by the host's game alone. The
        # guest's client still talks to its own simulation about saving: that
        # save is thrown away, the launcher copies the host's before a session.
        host_only=save + travel,
        guest_local=save,
    )


def _client():
    import services

    # While the game shuts a zone down there is no service manager at all.
    try:
        manager = services.client_manager()
    except AttributeError:
        return None
    if manager is None:
        return None
    return manager.get_first_client()


def _quote(arg):
    if arg and not any(c.isspace() or c == '"' for c in arg):
        return arg
    return '"{}"'.format(arg.replace('"', "'"))


class Game:
    """The adapter `session.Session` talks to the game through."""

    def __init__(self):
        # The pie menu the remote player has open. The game keeps one per
        # client, and both players share the host's client.
        self._remote_menu = None
        self._remote_parameters = None

    def in_zone(self):
        import services

        # A zone is "running" while its loading screen is still up, and the
        # game client needs everything its own simulation sends to finish
        # loading. Joining earlier leaves the guest on the loading screen.
        global _loaded_zone, _loaded_ticks
        try:
            zone = services.current_zone()
            if zone is None or not zone.is_zone_running:
                # The same zone can be loaded again: wait for its screen again.
                _loaded_zone = None
            if _loaded_zone is None or zone.id != _loaded_zone or _client() is None:
                _loaded_ticks = 0
                return False
        except Exception:
            return False
        _loaded_ticks += 1
        return _loaded_ticks > SETTLE_TICKS

    def zone_id(self):
        try:
            import services

            return services.current_zone_id() or 0
        except Exception:
            return 0

    def active_sim_id(self):
        try:
            sim_info = _client().active_sim_info
            return sim_info.id if sim_info is not None else 0
        except Exception:
            return 0

    def deliver(self, msg_id, data):
        import omega

        client = _client()
        if client is not None:
            omega.send(client.id, msg_id, data)

    def execute(self, name, args, sim_id):
        """Run a command for the remote player. Interaction commands act on
        `client.active_sim` and keep their state on the client, so for the
        duration of the command the client is made to look like theirs."""
        import services
        import sims4.commands

        client = _client()
        if client is None:
            return
        sim_info = services.sim_info_manager().get(sim_id) if sim_id else None
        if sim_info is None and name.startswith("interactions."):
            log.info("remote command {} dropped: no sim {}".format(name, sim_id))
            return
        saved = (client._active_sim_info, client._choice_menu, client._interaction_parameters)
        if sim_info is not None:
            client._active_sim_info = sim_info
        client._choice_menu = self._remote_menu
        client._interaction_parameters = self._remote_parameters or {}
        try:
            sims4.commands.execute(" ".join([name] + [_quote(a) for a in args]), client.id)
        finally:
            self._remote_menu = client._choice_menu
            self._remote_parameters = client._interaction_parameters
            client._active_sim_info, client._choice_menu, client._interaction_parameters = saved


    def snapshot(self, done, zone_id=0):
        resync.install()
        resync.take(done, zone_id)

    def adopt(self, payload):
        resync.adopt(payload, self.active_sim_id())


def _endpoint(directory):
    """The launcher writes bridge.json while it wants the mod connected."""

    def read():
        try:
            with open(os.path.join(directory, "bridge.json"), "r", encoding="utf-8") as f:
                value = json.load(f)
            return int(value["port"]), value.get("role")
        except (OSError, ValueError, KeyError, TypeError):
            return None

    return read


def _watch_loading_screen():
    """The game client reports the end of the loading screen through
    `Zone.on_loading_screen_animation_finished`."""
    from zone import Zone

    original = Zone.on_loading_screen_animation_finished

    @functools.wraps(original)
    def on_loading_screen_animation_finished(self):
        global _loaded_zone
        try:
            return original(self)
        finally:
            _loaded_zone = self.id
            log.info("loading screen finished")

    Zone.on_loading_screen_animation_finished = on_loading_screen_animation_finished


def _forward_log(message):
    if len(_log_lines) < MAX_LOG_LINES:
        role = _session.role if _session is not None and _session.role else "-"
        _log_lines.append("[{}] {}".format(role, message.split("\n", 1)[0][:MAX_LOG_LINE]))


def _send_log():
    """Both launchers show these lines, and the other mod writes them to its
    own log, so one log file tells what happened in both games."""
    if _log_lines and _bridge.connected:
        lines = _log_lines[:]
        del _log_lines[:]
        _bridge.send(frames.control("log", lines=lines))


def _native_tick():
    global _native_note, _native_report, _native_samples
    if _native_sync and _session.live and _ticks % NATIVE_START_TICKS == 0 and not native.syncing():
        note = "; ".join(native.command("sync"))
        if note != _native_note:
            _native_note = note
            log.info("native sync: " + note)
    # Build operations go both ways, whoever is the host.
    for call, payload in native.tick():
        if _session.live:
            _bridge.send(frames.pack(frames.NATIVE, call, payload))
        log.info("native call {:x} ({} bytes, header {}) {}".format(
            call, len(payload), payload[:12].hex(), "sent" if _session.live else "not sent: session is not live"))
    if _ticks % NATIVE_REPORT_TICKS == 0:
        lines = native.recorder_lines()
        # The counts of everything else change all the time; only this line
        # is worth repeating in the log.
        report = [line for line in lines if line.startswith("native sync")]
        if report != _native_report:
            _native_report = report
            for line in report:
                log.info(line)
        samples = native.recorder_lines(samples=True)[len(lines):]
        for line in samples[_native_samples:]:
            log.info(line)
        _native_samples = len(samples)


def _tick():
    global _ticks
    _ticks += 1
    for event in _bridge.pump():
        kind = event[0]
        if kind == bridge_module.FRAME:
            try:
                if event[1] == frames.CONTROL and b'"type":"log"' in event[3]:
                    for line in (frames.parse_json(event[3]) or {}).get("lines") or []:
                        log.info(str(line)[:MAX_LOG_LINE + 16], forward=False)
                elif event[1] == frames.NATIVE:
                    done = _session.live and native.incoming(event[2], event[3])
                    log.info("native call {:x} ({} bytes) received, {}".format(
                        event[2], len(event[3]), "replayed" if done else "not replayed"))
                else:
                    _session.frame(event[1], event[2], event[3])
            except Exception:
                log.exception("frame handling failed")
        elif kind == bridge_module.UP:
            _session.bridge_up(event[1])
        elif kind == bridge_module.DOWN:
            _session.bridge_down(event[1])
    _session.tick()
    try:
        _native_tick()
    except Exception:
        log.exception("native sync failed")
    _send_log()


def start(directory, native_sync=True):
    global _bridge, _session, _native_sync
    _native_sync = native_sync
    log.set_forwarder(_forward_log)
    _bridge = bridge_module.Bridge(_endpoint(directory))
    _session = session_module.Session(Game(), _consts(), _bridge.send, log=log.info)
    hooks.set_outbound_router(lambda client, msg_id, data: _session.outbound(msg_id, data))
    hooks.set_inbound_gate(lambda name, args, session_id: _session.command(name, args))
    _watch_loading_screen()
    try:
        # From the start, so that the objects of the zone are known by the
        # time the other player asks for them.
        resync.install()
    except Exception:
        log.exception("resync is not available")
    hooks.install_tick(_tick)
    log.info("live mode ready")


def reload_from_host():
    """For the tsmp.resync cheat."""
    if _session is None or not _session.resync():
        return "only a guest in a live session can reload from the host"
    return "reloading from the host"


def describe():
    """Lines for the tsmp.status cheat."""
    if _session is None:
        return ["live: off"]
    lines = [
        "live: {} (role {}, launcher {}, other game {})".format(
            "yes" if _session.live else "no",
            _session.role or "-",
            "connected" if _bridge.connected else "not connected",
            "in game" if _session.remote_in_zone else "not in game",
        ) + (", waiting to reload after the host" if _session.stale else "")
          + (", on another lot" if _session.apart else ""),
        "ticks {}, relayed {}, delivered {}, forwarded {}, executed {}".format(
            _ticks, _session.relayed, _session.delivered, _session.forwarded, _session.executed
        ),
    ]
    if _session.role == session_module.HOST:
        lines.append("other player's sim: {}".format(_session.remote_sim_id or "none"))
    return lines
