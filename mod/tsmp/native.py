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

`Sync` is what live mode builds on it: each game keeps its own build
operations in a `Journal` and sends them to the other, which replays them,
so that an edit made while the two were not playing together still reaches
the other game later. It is pure Python and is tested with fakes.

Addresses are relative to the library's base and belong to one build of the
game, so each known build has its own `Layout`.
"""

import collections
import itertools
import json
import os
import struct

from tsmp import frames

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

# A call of the client as it was made, with what replaying it needs. The
# stream's limit was either its size or something else, and replay copies that.
Op = collections.namedtuple("Op", "call third fourth position limit_is_size data")


class Recorder:
    """Counts the calls crossing the boundary between client and simulation,
    and while `sync` is set keeps the client's build operations."""

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
        self.sync = False
        # Filled on the client's threads and emptied by `take` on the
        # simulation's; a deque is safe for both.
        self._outbox = collections.deque()
        self._oversized = collections.deque()
        # Zone object -> the connection its client last called it on. Every
        # load of a zone opens a new session, so a connection is only good
        # for the object it was seen on, and only once that object is hooked.
        self._connections = {}
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
        self._connections[zone] = connection & MASK32
        if call in SYNC_CALLS and self.sync and data:
            if size > MAX_SYNC_BYTES:
                self._oversized.append(size)
                return
            # Whoever dispatched the call has read part of the stream already.
            position = self._memory.u32(stream + STREAM_POSITION)
            limit_is_size = self._memory.u32(stream + STREAM_LIMIT) == size
            self._outbox.append(
                Op(call, third & MASK32, fourth & MASK32, position, limit_is_size, self._memory.read(data, size))
            )

    def take(self):
        """The operations recorded since the last call, and the sizes of
        those that were too large to keep."""
        ops = []
        while self._outbox:
            ops.append(self._outbox.popleft())
        sizes = []
        while self._oversized:
            sizes.append(self._oversized.popleft())
        return ops, sizes

    def forget(self):
        """Forget the connections seen: the zone was left or loaded again."""
        self._connections.clear()

    def _ready_zone(self):
        """A zone that has our table and whose client has called it since
        it got it, or None. Its connection is then one of its own session."""
        copy = self._copies.get(self._base + self._layout.zone_table)
        if copy is None:
            return None
        for zone in zones(self._memory, self._base, self._layout, self.area_tables()):
            if zone in self._connections and self._memory.u64(zone) == copy:
                return zone
        return None

    def replay(self, op):
        """Make a call of the other game's client in this simulation.

        The call goes to the original function, so it is not recorded and
        not sent back. Returns False if there is nowhere to make it yet.
        """
        zone = self._ready_zone()
        if zone is None or self._receive is None:
            # A zone loaded since has the game's own table. Hooked now, its
            # client's next call gives a later attempt the connection.
            self.start(receive=True)
            return False
        size = len(op.data)
        # The simulation handles the call later, on its own time, and may
        # keep pointing at these bytes: neither buffer is ever freed.
        stream = self._memory.buffer(bytes(STREAM_BYTES))
        self._stream_new(stream, self._memory.buffer(op.data), size, size if op.limit_is_size else 0)
        # Size and read position share eight bytes.
        self._memory.write_u64(stream + STREAM_SIZE, size | min(op.position, size) << 32)
        self._receive(zone, self._connections[zone], op.call, op.third, op.fourth, stream)
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
        # A new object may have been made where a freed one was.
        self._connections.pop(address, None)
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
        self._connections.clear()
        return restored

    def describe(self):
        lines = ["native recorder: {} objects, {} errors".format(len(self._hooked), self.errors)]
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


# This game's own operations kept per lot for the other game to catch up on.
JOURNAL_OPS = 20000
JOURNAL_BYTES = 8 * 1024 * 1024
# A full journal drops one operation for each new one; one line in so many.
JOURNAL_NOTE_EVERY = 1000
# What keeping an operation costs beyond its bytes, roughly.
OP_COST = 64
# The other game's operations waiting until they can be replayed here.
PENDING_OPS = 256
PENDING_BYTES = 2 * 1024 * 1024
PENDING_SECONDS = 30.0
# Sent in one tick at most, so that catching up does not hold a tick up.
SEND_OPS = 256
SEND_BYTES = 1024 * 1024
REHOOK_TICKS = 64


def _cost(op):
    return len(op.data) + OP_COST


def _span(numbers):
    """"5" or "5-9", for a log line about operations in a row."""
    if numbers[0] == numbers[-1]:
        return str(numbers[0])
    return "{}-{}".format(numbers[0], numbers[-1])


class _Lot:
    """The operations of one lot, numbered from 1 with no gaps; only the
    newest are kept."""

    def __init__(self):
        self.ops = collections.deque()
        self.size = 0
        self.next = 1
        self.dropped = 0

    def add(self, op):
        self.ops.append(op)
        self.size += _cost(op)
        self.next += 1
        return self.next - 1

    def drop_oldest(self):
        self.size -= _cost(self.ops.popleft())
        self.dropped += 1

    def after(self, seq, max_ops, max_bytes):
        """(number, op) of the kept operations numbered above `seq`, oldest
        first, as many as fit in `max_ops` and `max_bytes` but at least one."""
        first = self.next - len(self.ops)
        start = max(0, seq + 1 - first)
        found = []
        size = 0
        for op in itertools.islice(self.ops, start, None):
            if len(found) >= max_ops or (found and size + len(op.data) > max_bytes):
                break
            found.append((first + start + len(found), op))
            size += len(op.data)
        return found


class Journal:
    """This game's own build operations, kept per lot.

    The architecture of a lot is only in the save file the game client
    writes, and the host's state carries none of it, so an edit the other
    game does not replay is lost there for good. Every edit is therefore kept
    here, whether or not the two games are playing together when it is made,
    until the other game says it has it. A lot is keyed by (save id, zone id)
    and numbers its operations in the order the client made them. The oldest
    are dropped beyond `max_ops` or `max_bytes`, with a line in the log.
    """

    def __init__(self, note, max_ops=JOURNAL_OPS, max_bytes=JOURNAL_BYTES):
        self._note = note
        self._max_ops = max_ops
        self._max_bytes = max_bytes
        self._lots = {}

    def add(self, key, op):
        """Keep `op` for the lot `key`; returns its number."""
        lot = self._lots.setdefault(key, _Lot())
        seq = lot.add(op)
        while lot.ops and (len(lot.ops) > self._max_ops or lot.size > self._max_bytes):
            lot.drop_oldest()
            if lot.dropped % JOURNAL_NOTE_EVERY == 1:
                self._note(
                    "build journal of zone {} is full: {} oldest edits dropped so far".format(key[1], lot.dropped)
                )
        return seq

    def after(self, key, seq, max_ops=SEND_OPS, max_bytes=SEND_BYTES):
        """The kept operations of lot `key` numbered above `seq`, as `_Lot.after`."""
        lot = self._lots.get(key)
        return lot.after(seq, max_ops, max_bytes) if lot is not None else []


class Pending:
    """The other game's operations that could not be replayed yet, oldest
    first: no zone was hooked yet, or its client had not called it since it
    was loaded, so there was no connection to replay on. Bounded in number,
    bytes and time; whatever is dropped the other game sends again when asked.
    """

    def __init__(self, max_ops=PENDING_OPS, max_bytes=PENDING_BYTES, max_seconds=PENDING_SECONDS):
        self._entries = collections.deque()  # (shared, arrival time)
        self._size = 0
        self._max_ops = max_ops
        self._max_bytes = max_bytes
        self._max_seconds = max_seconds

    def __len__(self):
        return len(self._entries)

    def last(self, default):
        """The number of the newest waiting operation, or `default`."""
        return self._entries[-1][0].seq if self._entries else default

    def add(self, shared, now):
        """Returns False, keeping nothing, when there is no room."""
        cost = _cost(shared.op)
        if len(self._entries) >= self._max_ops or self._size + cost > self._max_bytes:
            return False
        self._entries.append((shared, now))
        self._size += cost
        return True

    def head(self):
        return self._entries[0][0]

    def pop(self):
        shared, _ = self._entries.popleft()
        self._size -= _cost(shared.op)

    def expired(self, now):
        return bool(self._entries) and now - self._entries[0][1] > self._max_seconds

    def clear(self):
        """Drop everything; returns how many there were."""
        count = len(self._entries)
        self._entries.clear()
        self._size = 0
        return count


FORMAT = 1
FLAG_LIMIT_IS_SIZE = 1
FLAG_AFTER_GAP = 2
_SHARED = struct.Struct("<BBQQIIIII")


class Shared(collections.namedtuple("Shared", "zone save run seq after_gap op")):
    """A build operation as it travels to the other game in a NATIVE frame.

    Payload, little endian: u8 format (1), u8 flags, u64 zone id, u64 save
    id, u32 run, u32 number, then the call's u32 third and fourth arguments,
    the u32 read position of its stream and the stream's bytes. The zone is
    named as the host names it. Numbers start at 1 every time a game is
    started, so `run`, drawn at random then, tells the runs of a game apart.
    Flag 1: the stream's limit was its size. Flag 2: the sender no longer
    keeps the operations just before this one, so a gap is expected.
    """

    __slots__ = ()

    @property
    def key(self):
        return self.save, self.zone

    def frame(self):
        op = self.op
        flags = (FLAG_LIMIT_IS_SIZE if op.limit_is_size else 0) | (FLAG_AFTER_GAP if self.after_gap else 0)
        header = _SHARED.pack(FORMAT, flags, self.zone, self.save, self.run, self.seq, op.third, op.fourth, op.position)
        return frames.pack(frames.NATIVE, op.call, header + op.data)

    @classmethod
    def unpack(cls, call, payload):
        """The operation in a NATIVE frame, or None if it is not one this reads."""
        if call not in SYNC_CALLS or len(payload) < _SHARED.size or payload[0] != FORMAT:
            return None
        _, flags, zone, save, run, seq, third, fourth, position = _SHARED.unpack_from(payload)
        op = Op(call, third, fourth, position, bool(flags & FLAG_LIMIT_IS_SIZE), payload[_SHARED.size:])
        return cls(zone, save, run, seq, bool(flags & FLAG_AFTER_GAP), op)


class Sync:
    """Shares this game's build operations with the other game and replays
    the other game's here, the same way in both games whoever is the host.

    Each game keeps its own operations in a `Journal` from the moment a zone
    runs, and the number of the other game's last operation it replayed per
    lot in `applied`. Each time the session becomes live, `greet` tells the
    other game whether this one takes part (`native`) and how far it got on
    the current lot (`native_have`); the answer is every kept operation
    after that, in order, then new ones as they are made. Operations are
    only sent and replayed while both games take part, on the lot both are
    on. One that arrives out of order is not replayed: the other game is
    asked again from the last one replayed, so that none is skipped.
    """

    def __init__(self, known, run=None):
        self.recorder = None
        # Whether config.json allows it, and whether the player switched it
        # off with the cheat, which live mode then leaves alone.
        self.enabled = True
        self.user_off = False
        self._known = known
        self.run = run if run is not None else (struct.unpack("<I", os.urandom(4))[0] or 1)
        self.journal = Journal(self._note)
        self._pending = Pending()
        # (save, zone) -> (run, number) of the other game's last operation replayed here.
        self.applied = {}
        # (save, zone) -> number of our last operation the other game has or
        # was sent; none is sent before the other game said how far it got.
        self._cursors = {}
        # Whether the other game takes part; None until it says.
        self.theirs_ok = None
        self.place = None
        self._live = False
        self._said_ok = None
        self._shown = None
        self._asked = None
        self._inbox = []
        self._frames = []
        self._notes = []
        self._ticks = 0
        self.recorded = 0
        self.sent = 0
        self.replayed = 0
        self.duplicates = 0
        self.refused = 0
        self.oversized = 0

    @property
    def ok(self):
        """Whether this game takes part: allowed, and its build of the
        game is one whose tables are known."""
        return self.enabled and not self.user_off and bool(self._known())

    @property
    def active(self):
        return self.ok and self.theirs_ok is True

    def state(self):
        if not self.ok:
            return "off here"
        if self.theirs_ok is None:
            return "waiting for the other game"
        return "on" if self.theirs_ok else "off in the other game"

    def _note(self, line):
        self._notes.append(line)

    def notes(self):
        """Lines for the log since the last call."""
        lines, self._notes = self._notes, []
        return lines

    def _have(self, key):
        run, seq = self.applied.get(key, (0, 0))
        return {"save": str(key[0]), "zone": str(key[1]), "run": str(run), "seq": seq}

    def greet(self, place, want=True):
        """Tell the other game whether we take part and how far we got with
        its edits of the lot `place`; `want` asks it for the same."""
        self._said_ok = self.ok
        self._frames.append(frames.control("native", ok=self._said_ok, format=FORMAT))
        if place is not None:
            self._frames.append(frames.control("native_have", want=want, **self._have(place)))

    def _ask(self, key):
        """Ask the other game again for what follows our last replayed
        operation of lot `key`; once until something changes."""
        marker = (key, self.applied.get(key))
        if marker != self._asked:
            self._asked = marker
            self._frames.append(frames.control("native_have", **self._have(key)))

    def control(self, message):
        """A `native` or `native_have` control message of the other game."""
        kind = message.get("type")
        if kind == "native":
            self.theirs_ok = bool(message.get("ok")) and message.get("format") == FORMAT
        elif kind == "native_have":
            try:
                key = (int(message["save"]), int(message["zone"]))
                run, seq = int(message.get("run") or 0), int(message["seq"])
            except (KeyError, TypeError, ValueError):
                self._note("native_have not understood: {!r}".format(message)[:200])
                return
            # Numbers of an earlier run of this game mean nothing in this one.
            self._cursors[key] = seq if run == self.run else 0
            if message.get("want"):
                self.greet(self.place, want=False)

    def incoming(self, call, payload):
        """A NATIVE frame of the other game; handled on the next tick, when
        it is known whether the session is still live."""
        self._inbox.append((call, payload))

    def tick(self, place, live, now):
        """Once per simulation tick. `place` is (save id, zone id) of the
        zone running here or None, `live` whether the session is live and
        `now` a time in seconds. Returns the frames to send."""
        self._ticks += 1
        if place != self.place:
            self._moved(place)
        elif self.recorder is not None and self.recorder.sync and self._ticks % REHOOK_TICKS == 0:
            self.recorder.start(receive=True)
        if self._live and not live:
            self._left()
        self._live = live
        self._record()
        if live:
            self._announce()
        self._take(now)
        if live and self.active:
            self._send()
        result, self._frames = self._frames, []
        return result

    def _moved(self, place):
        self._drop_pending("the zone changed")
        self.place = place
        if self.recorder is not None:
            self.recorder.forget()
            if self.recorder.sync:
                self.recorder.start(receive=True)

    def _left(self):
        self._drop_pending("the session is no longer live")
        # Both games tell each other again when it is live again.
        self.theirs_ok = None
        self._cursors.clear()
        self._shown = None

    def _record(self):
        if self.recorder is None:
            return
        ops, sizes = self.recorder.take()
        for size in sizes:
            self.oversized += 1
            self._note("build op too large to share, {} bytes".format(size))
        for op in ops:
            if self.place is None:
                self._note("build op made while no zone runs, not kept")
            else:
                self.journal.add(self.place, op)
                self.recorded += 1

    def _announce(self):
        """While live: tell the other game when this one starts or stops
        taking part, and log once what the two agree on."""
        if self.ok != self._said_ok:
            self.greet(self.place, want=False)
        state = self.state()
        if state != self._shown and state != "waiting for the other game":
            self._shown = state
            self._note("build mode sync with the other game: " + state)

    def _take(self, now):
        """Replay what waits and what arrived, in order. Each is replayed as
        soon as it is accepted, so that the queue only holds those that
        cannot be replayed yet."""
        replayed = []
        blocked = not self._replay(replayed)
        inbox, self._inbox = self._inbox, []
        refused = {}
        for call, payload in inbox:
            reason = self._accept(Shared.unpack(call, payload), now)
            if reason:
                refused[reason] = refused.get(reason, 0) + 1
            elif not blocked:
                blocked = not self._replay(replayed)
        for reason, count in sorted(refused.items()):
            self.refused += count
            self._note("{} build op(s) of the other game not replayed: {}".format(count, reason))
        if replayed:
            self.replayed += len(replayed)
            self._note("replayed build op(s) {} of the other game".format(_span(replayed)))
        if self._pending.expired(now):
            self._drop_pending("none could be replayed for {:.0f} s".format(PENDING_SECONDS))
            # Asked for at once: nothing else might ask for them.
            self._ask(self.place)

    def _accept(self, shared, now):
        """Queue an operation of the other game for replay. Returns why it
        was not, or None (also for one replayed before)."""
        if shared is None:
            return "not in a format this game reads"
        if not self._live:
            return "the session is not live"
        if not self.active:
            return "build mode sync is " + self.state()
        if shared.key != self.place:
            return "made on another lot or save"
        run, done = self.applied.get(shared.key, (shared.run, 0))
        if run != shared.run:
            # The other game was started again and numbers from 1.
            done = 0
        last = self._pending.last(done)
        if shared.seq <= last:
            self.duplicates += 1
            return None
        if shared.seq > last + 1:
            if not shared.after_gap:
                self._ask(shared.key)
                return "an earlier one is missing; asked for it again"
            self._note(
                "{} edits of the other game on this lot are lost: it no longer keeps them".format(
                    shared.seq - last - 1
                )
            )
        if not self._pending.add(shared, now):
            return "too many are waiting"
        return None

    def _replay(self, replayed):
        """Replay what waits, adding the numbers to `replayed`. Returns
        False if something is left that cannot be replayed yet."""
        while len(self._pending):
            shared = self._pending.head()
            if self.recorder is None or not self.recorder.replay(shared.op):
                return False
            self._pending.pop()
            self.applied[shared.key] = (shared.run, shared.seq)
            replayed.append(shared.seq)
        return True

    def _drop_pending(self, reason):
        count = self._pending.clear()
        self._asked = None
        if count:
            self._note("{} build op(s) of the other game dropped before replay: {}".format(count, reason))

    def _send(self):
        key = self.place
        cursor = self._cursors.get(key)
        if key is None or cursor is None:
            return
        ops = self.journal.after(key, cursor)
        if not ops:
            return
        size = 0
        for seq, op in ops:
            after_gap = seq != cursor + 1
            if after_gap:
                self._note(
                    "the other game lacks {} edits of this lot that are no longer kept; "
                    "they cannot be replayed there".format(seq - cursor - 1)
                )
            self._frames.append(Shared(key[1], key[0], self.run, seq, after_gap, op).frame())
            cursor = seq
            size += len(op.data)
        self._cursors[key] = cursor
        self.sent += len(ops)
        self._note("sent build op(s) {} ({} bytes)".format(_span([seq for seq, _ in ops]), size))

    def describe(self):
        return [
            "native sync: {}; recorded {}, sent {}, replayed {}, waiting {}, duplicates {}, "
            "not replayed {}, too large {}".format(
                self.state(), self.recorded, self.sent, self.replayed, len(self._pending),
                self.duplicates, self.refused, self.oversized,
            )
        ]


_build_known = None


def _known_build():
    """Whether this game's build is known and its tables are as expected.
    It cannot change while the game runs, so it is probed once."""
    global _build_known
    if _build_known is None:
        found = probe()
        _build_known = bool(found.get("known")) and not found.get("mismatched")
    return _build_known


_sync = Sync(_known_build)


def describe():
    """One line for the tsmp.status cheat and the log."""
    recorder = _sync.recorder
    found = probe(area_tables=recorder.area_tables() if recorder else ())
    return "native: " + ", ".join("{} {}".format(key, found[key]) for key in sorted(found))


def command(mode):
    """The tsmp.native cheat: "on", "all" (also the client's calls), "sync"
    (also share build operations with the other game) or "off". "off" holds
    until another mode is asked for: live mode does not switch sync on again."""
    _sync.user_off = mode == "off"
    recorder = _sync.recorder
    if mode == "off":
        if recorder:
            recorder.sync = False
        return ["restored {} objects".format(recorder.stop() if recorder else 0)]
    if recorder is None:
        memory = Memory()
        found = probe(memory)
        if not found.get("known") or found.get("mismatched") or found.get("area") != "ok":
            return ["not started, " + describe()]
        base = memory.module_base(LIBRARY)
        recorder = _sync.recorder = Recorder(memory, base, KNOWN_BUILDS[identify(memory, base)])
    if mode == "sync":
        recorder.sync = True
    return ["recording on {} new objects".format(recorder.start(receive=mode in ("all", "sync")))]


def configure(enabled):
    """Whether config.json lets live mode share build operations."""
    _sync.enabled = bool(enabled)


def wanted():
    """Whether live mode should switch sync on: allowed, not switched off
    with the cheat, and not on already."""
    recorder = _sync.recorder
    return _sync.enabled and not _sync.user_off and not (recorder is not None and recorder.sync)


def joined(place):
    """The session has just become live, on the lot `place`."""
    _sync.greet(place)


def control(message):
    _sync.control(message)


def incoming(call, payload):
    _sync.incoming(call, payload)


def tick(place, live, now):
    return _sync.tick(place, live, now)


def notes():
    return _sync.notes()


def status_lines():
    return _sync.describe()


def recorder_lines(samples=False):
    recorder = _sync.recorder
    if recorder is None:
        return []
    return recorder.describe() + (recorder.sample_lines() if samples else [])
