"""Minimal file logger. The game's own logging is unavailable to mods."""

import os
import time
import traceback

from tsmp import paths

_MAX_BYTES = 4 * 1024 * 1024
_path = None
# Called with every line logged here, so that it can be shown elsewhere.
_forward = None


def set_forwarder(forward):
    global _forward
    _forward = forward


def _log_path():
    global _path
    if _path is None:
        directory = paths.data_dir()
        if directory is None:
            return None
        _path = os.path.join(directory, "tsmp.log")
        try:
            if os.path.getsize(_path) > _MAX_BYTES:
                os.replace(_path, _path + ".old")
        except OSError:
            pass
    return _path


def info(message, forward=True):
    if forward and _forward is not None:
        try:
            _forward(message)
        except Exception:
            pass
    path = _log_path()
    if path is None:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("{} {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


def exception(message):
    info("{}\n{}".format(message, traceback.format_exc()))
