"""Driving a game from outside, for tests without a player at the keyboard.

Off unless `<user dir>/tsmp/config.json` has `"driver": true`. Then every
line written to `<user dir>/tsmp/run.txt` is run as a console command, as if
typed into the cheat console, and the file is removed. `tsmp.selftest` writes
what this game sees to the log, so that two games can be compared from their
shared log instead of by eye.
"""

import os

from tsmp import log

FILE = "run.txt"
# Ticks between two looks at the file.
EVERY = 30

_directory = None
_ticks = 0


def take(directory):
    """The commands waiting in the file, which is removed once read."""
    path = os.path.join(directory, FILE)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    try:
        os.remove(path)
    except OSError:
        pass
    lines = (line.strip() for line in _text(data).splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _text(data):
    """Whatever wrote the file: Windows PowerShell 5 writes UTF-16 with `>`,
    most editors UTF-8, some with a byte order mark."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def start(directory):
    global _directory
    _directory = directory
    log.info("driver: commands are read from {}".format(os.path.join(directory, FILE)))


def tick(client_id):
    """`client_id` is the game client to run commands as, or None."""
    global _ticks
    if _directory is None:
        return
    _ticks += 1
    if _ticks % EVERY or client_id is None:
        return
    for line in take(_directory):
        log.info("driver: {}".format(line))
        try:
            import sims4.commands

            sims4.commands.execute(line, client_id)
        except Exception:
            log.exception("driver: command failed")


def _fact(name, read):
    try:
        return "{}={}".format(name, read())
    except Exception as error:
        return "{}=? ({})".format(name, type(error).__name__)


def state(client):
    """One line per group of facts about what this game holds right now."""
    import services

    def zone():
        zone = services.current_zone()
        return "{:#x}".format(zone.id)

    def lot():
        data = services.get_persistence_service().get_zone_proto_buff(services.current_zone_id())
        return "{}/{}".format(data.lot_description_id, data.active_plex)

    def clock():
        return str(services.game_clock_service().clock_speed).split(".")[-1]

    def time():
        return int(services.time_service().sim_now.absolute_ticks())

    def household():
        return "{:#x}".format(client.household.id)

    def households():
        return len(tuple(services.household_manager().values()))

    def active():
        return "{:#x}".format(client.active_sim_info.id)

    def selectable():
        return ",".join(sorted("{:#x}".format(info.id) for info in client.selectable_sims))

    def on_lot():
        return ",".join(sorted(
            "{:#x}".format(info.id) for info in services.sim_info_manager().instanced_sims_gen()))

    def objects():
        return len(services.object_manager())

    def funds():
        return client.household.funds.money

    def save():
        return services.get_persistence_service().get_save_game_data_proto().guid

    return [
        " ".join([_fact("zone", zone), _fact("lot", lot), _fact("save", save), _fact("clock", clock),
                  _fact("time", time)]),
        " ".join([_fact("household", household), _fact("households", households), _fact("funds", funds),
                  _fact("objects", objects)]),
        " ".join([_fact("active", active), _fact("selectable", selectable)]),
        _fact("on_lot", on_lot),
    ]
