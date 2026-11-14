"""Mod startup: report the environment, start live mode, optionally capture traffic."""

import json
import os
import sys
import time

import tsmp
from tsmp import capture, hooks, live, log, native, paths

# A long play session produces a lot of traffic; stop rather than fill the disk.
CAPTURE_MAX_BYTES = 256 * 1024 * 1024
FLUSH_EVERY = 200

_writer = None
_records_since_flush = 0


def _load_config(directory):
    """Optional `<user dir>/tsmp/config.json`. Missing file means defaults."""
    config = {"capture": False, "live": True, "native": True}
    try:
        with open(os.path.join(directory, "config.json"), "r", encoding="utf-8") as f:
            config.update(json.load(f))
    except (OSError, ValueError):
        pass
    return config


def _loaded_packs():
    import sims4.common

    return sorted(str(pack).split(".")[-1] for pack in sims4.common.available_packs)


def _has_module(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _write_status(directory):
    """The launcher reads this file to verify which packs the game really
    loaded, since `-disablepacks:` is not always honoured."""
    status = {
        "mod_version": tsmp.__version__,
        "python": sys.version.split()[0],
        "packs": _loaded_packs(),
        "has_socket": _has_module("socket"),
        "has_select": _has_module("select"),
        # The native half of ctypes ships with the game; build mode sync
        # would need it to reach the build operations (docs/internals.md).
        "has_ctypes": _has_module("_ctypes"),
        "native": native.probe(),
        "started_at": int(time.time()),
    }
    tmp = os.path.join(directory, "status.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    os.replace(tmp, os.path.join(directory, "status.json"))
    return status


def _after_record():
    global _records_since_flush
    _records_since_flush += 1
    if _records_since_flush >= FLUSH_EVERY:
        _records_since_flush = 0
        _writer.flush()


def _on_message(client, msg_id, data):
    if _writer.write_message(msg_id, data):
        _after_record()


def _on_command(name, args, session_id):
    if _writer.write_command(session_id or 0, name, args):
        _after_record()


def _start_capture(directory):
    global _writer
    captures = os.path.join(directory, "captures")
    if not os.path.isdir(captures):
        os.makedirs(captures)
    path = os.path.join(captures, time.strftime("cap-%Y%m%d-%H%M%S.tsmpcap"))
    _writer = capture.CaptureWriter(open(path, "wb"), max_bytes=CAPTURE_MAX_BYTES)
    hooks.install_outbound_tap(_on_message)
    hooks.install_inbound_tap(_on_command)
    log.info("capturing to {}".format(path))


def _register_commands():
    import sims4.commands

    @sims4.commands.Command("tsmp.status", command_type=sims4.commands.CommandType.Live)
    def tsmp_status(_connection=None):
        output = sims4.commands.CheatOutput(_connection)
        output("TSMP {}".format(tsmp.__version__))
        output("packs: {}".format(", ".join(_loaded_packs())))
        if _problem is not None:
            output("problem: {}".format(_problem))
        if _writer is None:
            output("capture: off")
        else:
            _writer.flush()
            output("capture: {}".format("full" if _writer.full else "on"))
        for line in live.describe():
            output(line)
        # Probed again here: the area object only exists inside a session.
        line = native.describe()
        output(line)
        log.info(line)
        for line in native.recorder_lines():
            output(line)
        # Samples of the recorded bytes are too long for the cheat console.
        for line in native.recorder_lines(samples=True):
            log.info(line)

    @sims4.commands.Command("tsmp.native", command_type=sims4.commands.CommandType.Live)
    def tsmp_native(mode="on", _connection=None):
        """Experiment: count the native calls of build mode, or with sync repeat
        build operations in the other game. on, all, sync or off."""
        output = sims4.commands.CheatOutput(_connection)
        if mode not in ("on", "all", "sync", "off"):
            output("usage: tsmp.native on|all|sync|off")
            return
        log.info("tsmp.native {}".format(mode))
        for line in native.command(mode):
            output(line)
            log.info(line)


_problem = None


def start():
    global _problem
    # Registered first, so the cheat answers even if the rest fails.
    _register_commands()
    try:
        directory = paths.data_dir()
        if directory is None:
            _problem = "data folder not found from {}".format(paths.__file__)
            return
        status = _write_status(directory)
        log.info("TSMP {} loaded, packs: {}".format(tsmp.__version__, ", ".join(status["packs"])))
        config = _load_config(directory)
        for line in native.load_mappings(os.path.join(directory, "mappings")):
            log.info(line)
        if config.get("capture"):
            _start_capture(directory)
        if config.get("live"):
            live.start(directory, native_sync=bool(config.get("native")))
    except Exception as exc:
        _problem = repr(exc)
        raise
