"""The game's native simulation library, seen from Python.

Build mode edits never reach Python: the game client sends them straight to
`Simulation_x64.dll`, and the library answers the client directly (see
"Native route" in docs/internals.md). Synchronising them means recording and
replaying calls at that boundary.

`probe` is harmless: it finds the library in memory, recognises its build,
and checks that the tables we would hook hold what we expect. It reads memory
and changes nothing. Every address read is either inside the library's image
or, once the build is recognised, an object the library itself points at.

`Recorder` is the experiment that follows, off unless asked for with the
`tsmp.native` cheat. It gives the area and zone objects a copy of their table
of functions in which a few entries lead to Python functions that count the
call and pass it on unchanged. No code of the game is modified.

Addresses are relative to the library's base and belong to one build of the
game, so each known build has its own `Layout`.
"""

import collections
import json
import os
import struct

LIBRARY = "Simulation_x64.dll"

# slots: name -> (table address, slot, expected function address)
Layout = collections.namedtuple(
    "Layout",
    "game_version area_global area_table area_slots area_interface zone_map zone_table zone_slots stream_new slots",
)

# Keyed by (PE timestamp, image size), which together identify a build. This
# is the build the mod was written against; `load_mappings` adds the others.
KNOWN_BUILDS = {
    (0x6ACFF6C2, 0x10E1000): Layout(
        game_version="1.129.39.1030",
        # Pointer to the area object's sending interface; null outside a session.
        area_global=0xFE8010,
        area_table=0xBFCCE0,
        area_slots=22,
        # The interface sits this far into the area object, which keeps its
        # zones in a hash map: bucket array and bucket count at these offsets.
        area_interface=0xB8,
        zone_map=(0x268, 0x270),
        zone_table=0xC0BD18,
        zone_slots=32,
        # Makes a stream over existing bytes: (stream, data, size, limit).
        stream_new=0x606D00,
        slots={
            # Client -> simulation: every call of the game client enters here.
            "zone.receive": (0xC0BD18, 7, 0x95D40),
            # The handler one of those calls ends in.
            "zone.build_op": (0xC0BE40, 6, 0x96E50),
            # Simulation -> client: to every client of a zone, and to one.
            "area.broadcast": (0xBFCCE0, 2, 0x37620),
            "area.send": (0xBFCCE0, 3, 0x3E3A0),
        },
    ),
}


class Memory:
    """Reads and writes the current process through `_ctypes`.

    The game ships the native half of ctypes but not the Python package, so
    this builds the few types it needs directly.
    """

    def __init__(self):
        import _ctypes

        class U8(_ctypes._SimpleCData):
            _type_ = "B"

        class U32(_ctypes._SimpleCData):
            _type_ = "I"

        class U64(_ctypes._SimpleCData):
            _type_ = "Q"

        self._ctypes = _ctypes
        self._u8 = U8
        self._u32 = U32
        self._u64 = U64
        self._functions = {}

    def module_base(self, name):
        # The library is already loaded (the interpreter runs inside it), so
        # this returns its handle, which is its base address.
        return self._ctypes.LoadLibrary(name)

    def u32(self, address):
        return self._u32.from_address(address).value

    def u64(self, address):
        return self._u64.from_address(address).value

    def write_u64(self, address, value):
        self._u64.from_address(address).value = value

    def read(self, address, size):
        array = type("Bytes", (self._ctypes.Array,), {"_type_": self._u8, "_length_": size})
        return bytes(array.from_address(address))

    def _function_type(self, arguments):
        """A native function taking and returning 64-bit integers."""
        if arguments not in self._functions:
            self._functions[arguments] = type(
                "Function{}".format(arguments),
                (self._ctypes.CFuncPtr,),
                {
                    "_flags_": self._ctypes.FUNCFLAG_CDECL,
                    "_restype_": self._u64,
                    "_argtypes_": (self._u64,) * arguments,
                },
            )
        return self._functions[arguments]

    def function(self, address, arguments):
        """A Python callable for the native function at `address`."""
        return self._function_type(arguments)(address)

    def callback(self, function, arguments):
        """The address of a native function that calls `function`."""
        thunk = self._function_type(arguments)(function)
        # Native code may call it at any time until the process ends.
        self._ctypes.Py_INCREF(thunk)
        return self._u64.from_address(self._ctypes.addressof(thunk)).value

    def buffer(self, data):
        """The address of a copy of `data` that is never freed."""
        array = type("Buffer", (self._ctypes.Array,), {"_type_": self._u8, "_length_": len(data)})(*data)
        self._ctypes.Py_INCREF(array)
        return self._ctypes.addressof(array)

    def table(self, values):
        """The address of a new array of 64-bit values that is never freed."""
        array = type("Table", (self._ctypes.Array,), {"_type_": self._u64, "_length_": len(values)})(*values)
        self._ctypes.Py_INCREF(array)
        return self._ctypes.addressof(array)


class BytesMemory:
    """A memory image held in a bytes object, for tests and offline tools."""

    def __init__(self, image, base):
        self._image = image
        self._base = base

    def module_base(self, name):
        return self._base

    def u32(self, address):
        return struct.unpack_from("<I", self._image, address - self._base)[0]

    def u64(self, address):
        return struct.unpack_from("<Q", self._image, address - self._base)[0]


def _number(value):
    """Mapping files write addresses as "0x..." strings and counts as numbers."""
    return value if isinstance(value, int) else int(value, 0)


def parse_mapping(data):
    """((timestamp, image size), Layout) from the contents of a mapping file.

    Raises on anything unexpected, so that half a mapping is never used.
    """
    if data["format"] != 1:
        raise ValueError("unknown format {!r}".format(data["format"]))
    timestamp, size = data["build"].split("-")
    slots = {}
    for name, (table, slot, expected) in data["slots"].items():
        slots[str(name)] = (_number(table), _number(slot), _number(expected))
    if not slots:
        raise ValueError("no slots")
    first, second = data["zone_map"]
    layout = Layout(
        game_version=str(data["game_version"]),
        area_global=_number(data["area_global"]),
        area_table=_number(data["area_table"]),
        area_slots=_number(data["area_slots"]),
        area_interface=_number(data["area_interface"]),
        zone_map=(_number(first), _number(second)),
        zone_table=_number(data["zone_table"]),
        zone_slots=_number(data["zone_slots"]),
        stream_new=_number(data["stream_new"]),
        slots=slots,
    )
    return (int(timestamp, 16), int(size, 16)), layout


def load_mappings(directory):
    """Add the builds described by `<directory>/*.json`, which the launcher
    puts there for the installed game version. Returns one line per file for
    the log. A mapping is only ever trusted as far as `probe` confirms it.
    """
    lines = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return lines
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), "r") as source:
                build, layout = parse_mapping(json.load(source))
        except Exception as exc:
            lines.append("mapping {} ignored: {!r}".format(name, exc))
            continue
        # The built-in layout is the tested one and stays.
        if build not in KNOWN_BUILDS:
            KNOWN_BUILDS[build] = layout
            lines.append("mapping {} for game {}".format(name, layout.game_version))
    return lines


def identify(memory, base):
    """(timestamp, image size) from the PE header at `base`, or None."""
    if memory.u32(base) & 0xFFFF != 0x5A4D:  # "MZ"
        return None
    header = base + memory.u32(base + 0x3C)
    if memory.u32(header) != 0x00004550:  # "PE\0\0"
        return None
    # File header follows the signature; the optional header follows that.
    return memory.u32(header + 8), memory.u32(header + 24 + 56)


def probe(memory=None, area_tables=()):
    """Describe what was found, as a JSON-friendly dict. Never raises.

    `area_tables` are further addresses the area object's table may have,
    which is how the recorder's own copy is recognised.
    """
    result = {"ctypes": False}
    try:
        if memory is None:
            memory = Memory()
        result["ctypes"] = True
        base = memory.module_base(LIBRARY)
        if not base:
            result["error"] = "library not loaded"
            return result
        build = identify(memory, base)
        if build is None:
            result["error"] = "not a PE image"
            return result
        result["build"] = "{:x}-{:x}".format(*build)
        layout = KNOWN_BUILDS.get(build)
        result["known"] = layout is not None
        if layout is None:
            return result
        mismatched = []
        for name, (table, slot, expected) in sorted(layout.slots.items()):
            if memory.u64(base + table + 8 * slot) - base != expected:
                mismatched.append(name)
        result["mismatched"] = mismatched
        if mismatched:
            return result
        # Only now follow a pointer out of the image.
        area = memory.u64(base + layout.area_global)
        if not area:
            result["area"] = "none"
        elif memory.u64(area) == base + layout.area_table:
            result["area"] = "ok"
        elif memory.u64(area) in area_tables:
            result["area"] = "recorded"
        else:
            result["area"] = "unexpected"
    except Exception as exc:
        result["error"] = repr(exc)
    return result


def zones(memory, base, layout, area_tables=()):
    """Addresses of the live zone objects, or [] outside a session."""
    interface = memory.u64(base + layout.area_global)
    if not interface:
        return []
    table = memory.u64(interface)
    if table != base + layout.area_table and table not in area_tables:
        return []
    area = interface - layout.area_interface
    buckets = memory.u64(area + layout.zone_map[0])
    count = memory.u32(area + layout.zone_map[1])
    found = []
    if not buckets or count > 4096:
        return found
    for index in range(count):
        node = memory.u64(buckets + 8 * index)
        # A node is (key, zone, next node).
        while node and len(found) < 64:
            found.append(memory.u64(node + 8))
            node = memory.u64(node + 16)
    return [zone for zone in found if zone]


MASK32 = 0xFFFFFFFF
SAMPLE_BYTES = 48
MAX_SAMPLES = 40
# The stream handed to zone.receive holds its size and a pointer to its data.
STREAM_SIZE = 0x20
STREAM_POSITION = 0x24
STREAM_LIMIT = 0x28
STREAM_DATA = 0x30
STREAM_BYTES = 0x40
# What gets a sample of its bytes kept: build messages and the build operation.
BUILD_MESSAGES = range(700, 740)
BUILD_OP = 0x3A0F613C
# Calls of the client that are repeated in the other game's simulation.
SYNC_CALLS = frozenset([BUILD_OP])
MAX_SYNC_BYTES = 1024 * 1024


class Recorder:
    """Counts the calls crossing the boundary between client and simulation."""

    def __init__(self, memory, base, layout):
        self._memory = memory
        self._base = base
        self._layout = layout
        self._hooks = None  # table address -> {slot: address of our function}
        self._copies = {}  # table address -> address of our copy
        self._hooked = {}  # object address -> table address
        self.counts = {}  # (kind, id) -> [calls, bytes]
        self.samples = []
        self.errors = 0
        # Synchronisation: the client's calls waiting to be sent to the other
        # game, and what replaying the other game's calls here needs.
        self.sync = False
        self.outbox = []
        self.connection = None
        self.limit_is_size = True
        self.recorded = 0
        self.replayed = 0
        self.dropped = 0
        self._receive = None
        self._stream_new = None

    def note(self, kind, ident, data, size):
        entry = self.counts.setdefault((kind, ident), [0, 0])
        entry[0] += 1
        entry[1] += size
        wanted = ident == BUILD_OP if kind == "receive" else ident in BUILD_MESSAGES
        if wanted and data and len(self.samples) < MAX_SAMPLES:
            self.samples.append((kind, ident, size, self._memory.read(data, min(size, SAMPLE_BYTES))))

    def _on_send(self, area, zone, client, message, data, size):
        self.note("send", message & MASK32, data, size & MASK32)

    def _on_broadcast(self, area, zone, message, data, size):
        self.note("broadcast", message & MASK32, data, size & MASK32)

    def _on_receive(self, zone, connection, call, third, fourth, stream):
        size = self._memory.u32(stream + STREAM_SIZE) if stream else 0
        data = self._memory.u64(stream + STREAM_DATA) if stream else 0
        call &= MASK32
        self.note("receive", call, data, size)
        self.connection = connection & MASK32
        if call in SYNC_CALLS:
            self.limit_is_size = self._memory.u32(stream + STREAM_LIMIT) == size if stream else True
            if self.sync and data and size <= MAX_SYNC_BYTES:
                self.recorded += 1
                # Whoever dispatched the call has read part of the stream already.
                position = self._memory.u32(stream + STREAM_POSITION)
                self.outbox.append((call, third & MASK32, fourth & MASK32, position, self._memory.read(data, size)))

    def replay(self, call, third, fourth, position, data):
        """Make a call of the other game's client in this simulation.

        The call goes to the original function, so it is not recorded and
        not sent back. Returns False if there is nowhere to make it yet.
        """
        found = zones(self._memory, self._base, self._layout, self.area_tables())
        if not found or self.connection is None or self._receive is None:
            self.dropped += 1
            return False
        # The simulation handles the call later, on its own time, and may
        # keep pointing at these bytes: neither buffer is ever freed.
        stream = self._memory.buffer(bytes(STREAM_BYTES))
        self._stream_new(stream, self._memory.buffer(data), len(data), len(data) if self.limit_is_size else 0)
        # Size and read position share eight bytes.
        self._memory.write_u64(stream + STREAM_SIZE, len(data) | min(position, len(data)) << 32)
        self._receive(found[0], self.connection, call, third, fourth, stream)
        self.replayed += 1
        return True

    def passing_on(self, original, note):
        """A function that notes a call and then makes it."""

        def hook(*args):
            try:
                note(*args)
            except Exception:
                self.errors += 1
            return original(*args)

        return hook

    def _make_hooks(self):
        hooks = {}
        for name, arguments, note in (
            ("area.send", 6, self._on_send),
            ("area.broadcast", 5, self._on_broadcast),
            ("zone.receive", 6, self._on_receive),
        ):
            table, slot, expected = self._layout.slots[name]
            original = self._memory.function(self._base + expected, arguments)
            if name == "zone.receive":
                self._receive = original
                self._stream_new = self._memory.function(self._base + self._layout.stream_new, 4)
            target = self._memory.callback(self.passing_on(original, note), arguments)
            hooks.setdefault(self._base + table, {})[slot] = target
        return hooks

    def attach(self, address, table, slots, hooks):
        """Point the object at `address` to a copy of `table` with `hooks` in it."""
        if self._memory.u64(address) != table:
            return False
        if table not in self._copies:
            # The entry before a table describes the type; it comes along.
            values = [self._memory.u64(table + 8 * index) for index in range(-1, slots)]
            for slot, target in hooks.items():
                values[slot + 1] = target
            self._copies[table] = self._memory.table(values) + 8
        self._memory.write_u64(address, self._copies[table])
        self._hooked[address] = table
        return True

    def start(self, receive=False):
        """Hook the area object and, if asked, the zones. Returns how many were new."""
        layout = self._layout
        if self._hooks is None:
            self._hooks = self._make_hooks()
        attached = 0
        area_table = self._base + layout.area_table
        zone_table = self._base + layout.zone_table
        interface = self._memory.u64(self._base + layout.area_global)
        if interface and self.attach(interface, area_table, layout.area_slots, self._hooks[area_table]):
            attached += 1
        if receive:
            for zone in zones(self._memory, self._base, layout, self.area_tables()):
                if self.attach(zone, zone_table, layout.zone_slots, self._hooks[zone_table]):
                    attached += 1
        return attached

    def area_tables(self):
        copy = self._copies.get(self._base + self._layout.area_table)
        return () if copy is None else (copy,)

    def stop(self):
        """Give every object that still has our table its own back."""
        # Objects hooked earlier may have been freed since (a zone is left
        # when travelling), so only those the library still points at are read.
        live = set(zones(self._memory, self._base, self._layout, self.area_tables()))
        live.add(self._memory.u64(self._base + self._layout.area_global))
        restored = 0
        for address, table in self._hooked.items():
            if address in live and self._memory.u64(address) == self._copies[table]:
                self._memory.write_u64(address, table)
                restored += 1
        self._hooked = {}
        return restored

    def describe(self):
        lines = ["native recorder: {} objects, {} errors".format(len(self._hooked), self.errors)]
        if self.sync:
            lines.append(
                "native sync: recorded {}, replayed {}, dropped {}, connection {}, limit {}".format(
                    self.recorded, self.replayed, self.dropped, self.connection, "size" if self.limit_is_size else "other"
                )
            )
        for kind in ("send", "broadcast", "receive"):
            parts = [
                "{} x{} ({}b)".format(_name(kind, ident), entry[0], entry[1])
                for (k, ident), entry in sorted(self.counts.items())
                if k == kind
            ]
            if parts:
                lines.append("{}: {}".format(kind, ", ".join(parts)))
        return lines

    def sample_lines(self):
        return [
            "{} {} size {}: {}".format(kind, _name(kind, ident), size, head.hex())
            for kind, ident, size, head in self.samples
        ]


def _name(kind, ident):
    # Calls from the client are known by a hash, messages by a small number.
    return "{:x}".format(ident) if kind == "receive" else str(ident)


_recorder = None


def describe():
    """One line for the tsmp.status cheat and the log."""
    found = probe(area_tables=_recorder.area_tables() if _recorder else ())
    return "native: " + ", ".join("{} {}".format(key, found[key]) for key in sorted(found))


def command(mode):
    """The tsmp.native cheat: "on", "all" (also the client's calls), "sync"
    (also repeat build operations in the other game) or "off"."""
    global _recorder
    if mode == "off":
        if _recorder:
            _recorder.sync = False
        return ["restored {} objects".format(_recorder.stop() if _recorder else 0)]
    if _recorder is None:
        memory = Memory()
        found = probe(memory)
        if not found.get("known") or found.get("mismatched") or found.get("area") != "ok":
            return ["not started, " + describe()]
        base = memory.module_base(LIBRARY)
        _recorder = Recorder(memory, base, KNOWN_BUILDS[identify(memory, base)])
    if mode == "sync":
        _recorder.sync = True
    return ["recording on {} new objects".format(_recorder.start(receive=mode in ("all", "sync")))]


REHOOK_TICKS = 64
_ticks = 0


def tick():
    """Once per simulation tick: the calls to send to the other game, as
    (call, payload) pairs. Also hooks the zones that appeared since."""
    global _ticks
    if _recorder is None or not _recorder.sync:
        return []
    _ticks += 1
    if _ticks % REHOOK_TICKS == 0:
        _recorder.start(receive=True)
    # The client's thread may be adding to the list at this moment.
    calls = []
    while _recorder.outbox:
        calls.append(_recorder.outbox.pop(0))
    return [
        (call, struct.pack("<III", third, fourth, position) + data)
        for call, third, fourth, position, data in calls
    ]


def incoming(call, payload):
    """A call the other game's client made, as `tick` returned it there."""
    if _recorder is None or not _recorder.sync or call not in SYNC_CALLS or len(payload) < 12:
        return False
    third, fourth, position = struct.unpack_from("<III", payload)
    return _recorder.replay(call, third, fourth, position, payload[12:])


def syncing():
    return _recorder is not None and _recorder.sync


def recorder_lines(samples=False):
    if _recorder is None:
        return []
    return _recorder.describe() + (_recorder.sample_lines() if samples else [])
