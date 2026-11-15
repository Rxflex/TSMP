# Game internals

What TSMP relies on inside The Sims 4, and how each fact was established.
Checked against game version 1.129.39.1030 (Steam, Windows).

"Read" means the fact comes from reading the game's decompiled Python.
"Observed" means it was seen in a running game. Anything not yet observed is
listed under [Open questions](#open-questions).

## Reproducing the research

The game's Python lives in four archives of Python 3.7 bytecode:

    <install>/Data/Simulation/Gameplay/base.zip        standard library
    <install>/Data/Simulation/Gameplay/core.zip        sims4.* engine bindings
    <install>/Data/Simulation/Gameplay/simulation.zip  gameplay
    <install>/Game/Bin/Python/generated.zip            protobuf definitions

Extract them into `decompiled/pyc/` and decompile individual modules with
`decompyle3`. `decompiled/` is git-ignored: EA's code must not be committed.

## Embedded Python

- Python 3.7 (`python37_x64.dll`). Read.
- Native modules shipped in `Game/Bin/Python/DLLs`: `_socket`, `select`,
  `_ctypes`, `_queue`, `_decimal`, `pyexpat`, `unicodedata`. So a mod can open
  sockets. Read (file listing); importing them in-game is reported by the mod
  in `status.json`.

## How script mods are loaded

`sims4/importer/utils.py::module_names_gen` lists the modules of a `.ts4script`
(or `.zip`) archive with the pattern `.+\.py[co]$`, then imports each one by
its dotted name. Read. Consequences:

- An archive holding only `.py` sources is ignored without any error.
  Observed: the first TSMP build shipped sources and never ran.
- The `.pyc` files must be Python 3.7 bytecode, at their plain location
  (`tsmp/bootstrap.pyc`, not `__pycache__/`). `mod/build.py` does this.
- Every module in the archive is imported at startup, not just the package.
- Import errors are passed to the game's own logger (`logger.exception`).
  Whether that reaches a file in a release build has not been checked, so the
  mod keeps its own log.

## Simulation -> client traffic

The simulation ("server") is Python; the renderer and UI ("client") are C++.
Everything the simulation tells the client goes through one function:

    server/client.py
        def send_message(self, msg_id, msg):
            if self.active:
                omega.send(self.id, msg_id, msg.SerializeToString())

`omega.send(session_id, msg_id, data)` is native. No other gameplay module
references `omega` (checked by searching every `.pyc` for the name). Read.

Two kinds of message arrive there, both via `distributor/system.py`:

- **View updates.** Object-level operations (`distributor.ops`) are queued in
  the `Distributor` journal and flushed by `Distributor.process()` as one
  `MSG_OBJECTS_VIEW_UPDATE` holding a `ViewUpdate` protobuf.
- **Events.** `Distributor.add_event(msg_id, msg)` sends any other message id
  (UI, dialogs, notifications).

There is exactly one `Distributor` and it holds exactly one `client`;
`add_client` raises if a second registers. The game is single-client by
construction, so a second player cannot be added as another `Client`. TSMP
instead mirrors the stream to a second game process.

Hook: `tsmp/hooks.py::install_outbound_tap` replaces `Client.send_message`.

## Client -> simulation traffic

The client drives the simulation with text commands. `sims4.commands.Command`
registers a closure `invoke_command` with the native `_commands` module; the
native side calls it with string arguments and `_session_id`.

`invoke_command` looks up the module global `parse_args` on every call, before
its own `try`. Wrapping `sims4.commands.parse_args` therefore sees every
command, and the command's name is in the caller's frame as the free variable
`name`. Read.

Hook: `tsmp/hooks.py::install_inbound_tap`.

Right after `parse_args`, `invoke_command` runs
`if not is_command_available(relevant_type): return`, again through a module
global. A guest that must *forward* a command instead of running it uses the
pair: the `parse_args` wrapper asks the gate, and if the gate says no it sets a
flag that makes the wrapped `is_command_available` answer False once. The
command returns quietly, with no error on the native side.
Hook: `tsmp/hooks.py::set_inbound_gate`.

To run a command on behalf of the other player the host calls
`sims4.commands.execute(line, client.id)`. Commands find "the player's sim"
through the client object, so for the duration of the call the host's client
has `_active_sim_info`, `_choice_menu` and `_interaction_parameters` swapped
for the guest's (`tsmp/live.py::Game.execute`). Whether the native
`_commands.execute` always runs the command before returning is unverified;
if it defers, the swap is undone too early and the command acts for the
host's sim.

The mod needs to run code every tick without a thread (the embedded Python
has no safe way to touch the simulation from another thread).
`areaserver.c_api_server_tick`, which the native side calls for every server
tick, ends with `services.get_distributor_service().on_tick()`. That call does
not depend on a zone being loaded or on the game clock, and is skipped only
while a save is in progress. Wrapping `DistributorService.on_tick`
(`distributor.distributor_service`) therefore gives a per-tick callback on the
simulation thread. Read; that the native side keeps ticking while the game is
paused is expected but unverified. `tsmp.status` prints the tick count.
Hook: `tsmp/hooks.py::install_tick`.

## Packs

- `sims4.common.available_packs` is the set of packs the running game loaded,
  built from the native `_zone.is_available_pack`. Read. The mod writes it to
  `<user dir>/tsmp/status.json` so the launcher can confirm that
  `-disablepacks:` took effect.
- On disk, an owned pack is a folder named by its code (`EP01`, `GP04`, ...)
  directly under the install dir. `Delta/` has a folder for every pack whether
  owned or not and must not be used to detect ownership. Observed: a base-game
  install has a folder per pack under `Delta/` and none at the root.

## Build mode

`build_buy.py` is a thin wrapper over the native `_buildbuy` module. Walls,
floors, terrain and object placement are executed natively; Python receives
only notifications (`c_api_wall_contour_update`, `c_api_navmesh_update`,
`c_api_modify_household_funds`, enter/exit callbacks). `buildbuy_commands.py`
has no editing commands. Read.

Consequence: build edits cannot be replicated operation by operation from
Python alone. They are replicated one level lower, as described next: both
players build at the same time and each edit is repeated in the other game.

### Native route to build operations

First read from the strings and exports of the game binaries:

- The simulation is a separate library, `Game/Bin/Simulation_x64.dll`. The
  game client in `TS4_x64.exe` talks to it through a handful of package
  types: `PkgExecuteCmdMsg`, `PkgExecuteCmdWithArgsMsg` (commands, what the
  inbound hook sees), `PkgOmegaPDUMsg` (simulation -> client) and
  `PkgRequestBuildBuyOpMsg` with `RequestBuildBuyOp` / `BuildBuyOpResponse`.
  So each build edit is one serialized operation sent by the client. There is
  also a `SerializedUndoRedoStack`.
- `Game/Bin/Python/DLLs/_ctypes_x64.pyd` ships with the game, though the
  pure-Python `ctypes` package does not (the mod would bundle it). With it a
  script mod can call and patch native code without injecting a library.
  `status.json` reports `has_ctypes` to confirm the import works in the game.

- Everything the game client asks of the simulation arrives in one function
  of `Simulation_x64.dll` (RVA `0x24380` in the build of 2026-11-08; found
  through the reference to the string `RequestBuildBuyOp`). Its arguments
  are the interface object, a connection id, a 32-bit hash of the call name
  and a stream holding the serialized arguments; it compares the hash against
  41 values and unpacks each call. `RequestBuildBuyOp` is `0x3a0f613c`.
  The calls, by the names in the function: `ExecuteCommandStringOnServer`,
  `ExecuteCommandWithArgsOnServer`, `RequestBuildBuyOp`,
  `RequestBuildBuyVenueUpdate`, `RequestBuildBuyVenueOwnerUpdate`,
  `RequestBuildBuyLotUpdate`, `RequestSetSubVenue`, `RequestLotTraitsUpdate`,
  `RequestBusinessRulesUpdate`, `RequestHouseholdFundsUpdate`,
  `RequestSetLotOwner`, `RequestHouseholdInventoryViewed`,
  `RequestLoadPlexObjects`, `RequestRemoteTravelReservation`,
  `SendLotDisplayLevel`, `OnClientReady`, `ClientSendSystemEvent`,
  `SendBuildBuyEvent`, `SetBuildBuyGigLots`, `ZonesUpdatedResponse`,
  `RequestClientDisconnect`, `ReplyClockSyncUpdate`, `RequestServerFps`,
  `ZoneReservationRequest`, `ZoneFamilyCreateResponse`, `RemoveFamily`,
  `ChunkedUpload*` (5), memories (6).
  This is the one place where every client -> simulation call, native ones
  included, can be recorded and replayed as (hash, bytes). The mirror of it,
  simulation -> client, must exist in `TS4_x64.exe` (`BuildBuyOpResponse`,
  `OnApplyBlueprintResponse` are named there) and has not been located.

The code of `TS4_x64.exe` is encrypted on disk and is left alone; everything
below is in `Simulation_x64.dll`, which is not. Addresses are for the build
with PE timestamp `0x6acff6c2` (game 1.129.39.1030).

Client -> simulation:

- The function above belongs to `ZoneInstanceObjectBase`. `ZoneInstance`
  overrides it (slot 7 of its main table at `0xc0bd18`, function `0x95d40`):
  `receive(zone, connection: u32, call hash: u32, u32, u32, stream*)`. For all
  but two hashes it copies the stream and queues the call for the simulation
  thread, which later runs the base function. So it can be called from any
  thread, which is what replaying a recorded call needs.
- The stream is a plain 0x40-byte structure: table pointer (`0xc0b710`),
  `u32` state at +8 (0 = good), error text at +0x10, `u32` size at +0x20,
  read position at +0x24, limit at +0x28, data pointer at +0x30. It can be
  read and built without calling into the library.
- Decoded calls go to an interface at `zone + 0xa0` (table `0xc0be40`), one
  slot per call. `RequestBuildBuyOp` is slot 6 (`0x96e50`): it finds the
  session of the connection, and posts a `PkgRequestBuildBuyOpMsg` (message
  `0xd9c1dcc0`) that the area's ready state handles in `0x7dd20`, handing the
  payload as an opaque byte stream to the build code (`0x8d1c50`). A build
  operation is therefore one byte string plus the session it came from.

Simulation -> client:

- Everything goes through two methods of an interface of `AreaInstance`
  (pointer in the global at `0xfe8010`, table `0xbfcce0`):
  slot 3 `send(area, zone id, client id, message id: u32, data*, size)` at
  `0x3e3a0` and slot 2 `broadcast(area, zone id, message id, data*, size)`
  at `0x37620`. `_omega.send` (`0x61880`), which is what Python's
  `Client.send_message` ends in, is just a caller of slot 3.
- The build code calls slot 3 directly: a failed operation is answered with
  message 719 (`EVENT_BB_OP_FAIL`). The ids 708-739 (`EVENT_BB_*`: lot data,
  undo/redo state, household inventory, ...) are sent the same way. None of
  them pass through Python, which is why the capture of phase 0 holds only
  nine message ids, and why walls and wall coverings do not reach the other
  player while objects (sent by Python) do.

Both points are entries of tables of function pointers, so recording needs
no change to code: point an entry at our function, which notes the arguments
and calls the original. Since the guest already receives the host's messages
through `_omega.send`, recording slots 2 and 3 on the host should carry the
build results across with the transport that exists. The guest's own edits
would be recorded at `receive` and replayed into the host's `receive`.

The addresses above are also in `mappings/1.129.39.1030.json`; a file like
it describes any other build, and the launcher hands the mod the one for
the installed game version (`native.load_mappings`).

`tsmp/native.py` recognises the build and checks these tables, read-only;
its result is in `status.json` and in the `tsmp.status` cheat. Confirmed in
the game: `_ctypes` loads, the build is recognised, the tables match and the
area object is where expected.

The `tsmp.native on|all|off` cheat starts an experiment that is off by
default: a recorder hooks `area.send` and `area.broadcast` (`all` adds
`zone.receive` of every live zone) and counts calls by message id or call
hash, keeping the first bytes of build messages. `tsmp.status` prints the
counts; the samples go to the log. `off` restores the objects' tables.

First run with the recorder (guest side, one wall and one wallpaper): the
callbacks work on the game's threads. Each edit is one `RequestBuildBuyOp`
call (`3a0f613c`; the stream starts with the hash, a u32 and the payload
size), answered by a broadcast of message 706 that repeats the operation and
adds its result, and by message 728 to the requesting client. The guest's
own simulation handles the guest's edits, which is why they never reached
the host.

`tsmp.native sync` is what was built on that: each game sends its client's
build operations to the other as `NATIVE` frames, and the other repeats the
call in its own simulation through the original `zone.receive`, on its own
connection id. Both simulations then apply every edit and tell their own
client. Confirmed with two computers: walls, floors and wall coverings drawn
by either player appear for both.

A recorded stream is not at its start: by the time `receive` sees it, the
header (hash, connection, payload size) has been read. The frame therefore
carries the read position next to the bytes, and the replayed stream is
built with the same position. Replaying from position 0 makes the simulation
read the header as the operation, and it drops the zone session of the
connection.

Live mode switches this on by itself once the session is live (`"native":
false` in `config.json` keeps it off). Every line a mod logs also travels as
a `log` control frame: the launchers show the latest lines and the other mod
writes them to its own `tsmp.log`, so one file tells what both games did.

Answered by running it: a `_ctypes` callback may run on the game's threads
(it takes the interpreter lock; no deadlock or crash was seen), and a client
draws an edit it did not ask for whether or not it is in build mode.

Open:

- Whether an object bought in build mode ends up placed twice, since objects
  also travel as simulation messages.
- Whether household funds are charged in both simulations, and what the
  other player's undo stack does with an edit it did not make.
- Behaviour after travel to another lot. The hooks are put back every 64
  ticks, which should cover a new zone; not yet tried.
- The hash of the call names is not one of the common ones; recorded calls
  are replayed by number, so it is not needed.

Addresses change with every game update, so each supported build needs its
own table in `native.py`; an unknown build switches the native part off.

## Create-a-Sim

Entering CAS for an existing sim is a command (`sims.modify_in_cas`,
`sims.modify_outfit_in_cas`, `cas.modify_mannequin`, ...). Editing happens in
the client: `sims.exit2cas` is passed on to it with
`sims4.commands.client_cheat`, and the zone is loaded again from the scratch
save when the player comes back. See "Following the host through a reload"
for what that means for a session.

## User data

- `GameVersion.txt`: 4-byte little-endian length, then the version string.
- `Options.ini`: `scriptmodsenabled` and `modsdisabled` control whether
  `.ts4script` files load; script mods need `scriptmodsenabled = 1` and
  `modsdisabled = 0`.
- The user folder name is localized; identify it by the presence of
  `GameVersion.txt` and `Options.ini`.

## Observed traffic

From one 276-second capture (base game, heavily modded; live mode, build
mode, two lot changes), summarised with `mod/tools/capstat.py`.

- **Both taps work.** 6444 outbound messages (6.5 MB) and 2422 inbound
  commands were recorded, an average of about 23 KB/s uncompressed.
- **Zone load is in the stream.** The first view update after loading is a
  single 1.8 MB message carrying the whole lot (`OBJECT_CREATE`,
  `SIM_INFO_CREATE`, `HOUSEHOLD_CREATE`, ...). The same burst repeats after
  each lot change.
- **Almost everything is a view update**: 6237 of 6444 messages. The rest are
  `MSG_OBJECT_IS_INTERACTABLE`, `MSG_PIE_MENU_CREATE`, `MSG_UI_DIALOG_SHOW` /
  `_CLOSE`, save lock/complete and `MSG_TRAVEL_SIMS_TO_ZONE`.
- **`HEARTBEAT` is a third of all operations** (6117 of 19191), sent even
  while paused in build mode.
- **The client reports animation progress back**: `animation.arb_started`,
  `animation.route_time_update`, `animation.route_complete`,
  `animation.route_event_executed` are 53% of inbound commands. Only one
  client can answer these; on the host that stays the host's own client.
- **Player input is a small set of commands**: `interactions.has_choices` /
  `.choices` / `.select` (pie menu), `ui.dialog.respond` / `.pick_result`,
  `clock.setspeed`, `clock.request_pause` / `unrequest_pause` /
  `toggle_pause_unpause`. These are what a guest has to forward.
- **Per-client noise** that must not be forwarded as-is:
  `update.camera.information`, `ui.create_hovertip`.
- **The session id changes on every zone load** (3, 5, 7 in this capture).
- **Lot change**: `MSG_TRAVEL_SIMS_TO_ZONE` goes out, then the client sends
  `persistence.save_game_with_autosave`, then the new zone loads.
- **Build mode** is bracketed by `clock.build_buy_pause_unpause 1` / `0`.
  Between them the client sent no editing commands at all, and the simulation
  sent no `OBJECT_CREATE`; only side effects appeared (`OBJECT_DELETE`,
  `SET_BUILDBUY_USE_FLAGS`, `SET_MONEY`, footprint updates). This confirms
  build edits are not visible from Python.

The game's protobuf runtime does not parse messages outside the game (it
returns empty messages), so tools use `tsmp/wire.py`.

## Live session

```
game (host)                                                   game (guest)
 mod  <-loopback TCP->  launcher  <==== LAN ====>  launcher  <-loopback TCP->  mod
```

Each launcher writes `<user dir>/tsmp/bridge.json` (`{"port":N,"role":"host"}`
or `"guest"`) while a session is open and removes it afterwards. The mod reads
it before every connection attempt, so the game and the launcher can start in
either order.

Everything on these connections is a frame: `u8 kind, u32 id, u32 length,
payload`, little endian.

| kind | id | payload |
|---|---|---|
| 1 MSG | message id | the bytes the simulation passed to `Client.send_message` |
| 2 COMMAND | 0 | JSON `{"name": ..., "args": [...]}` |
| 3 CONTROL | 0 | JSON `{"type": ...}` |
| 4 NATIVE | hash of the call | a call a game client made into its simulation past Python |
| 5 STATE | 0 | the host's save as it is now (`tsmp/resync.py`) |
| 6 HOUSEHOLD | 0 | the guest's household, sims and home lot (`tsmp/household.py`) |

Control messages: `link {remotes}` (launcher -> its game: how many other
launchers are connected), `gone` (launcher -> the other side: the game here
disconnected), `state {role, in_zone, zone?, want?}` (game -> game; `want` asks for
the other's state in return), `active_sim {sim_id}` (guest -> host),
`resync {zone?}` (guest -> host: send your state, for a guest on that lot), `resync_failed` (host -> guest), `merged {ok}` (host -> guest: your
household is, or is not, in my save). `state` also carries `save`, the `guid`
of the game's `SaveGameData`.

The launchers only relay; the guest's launcher opens a second connection to
the host's with `JoinLive` and after the `Hello` reply the socket carries raw
frames. Policy lives in `tsmp/session.py`. The session is *live* while both
games have a zone loaded. While live:

- Host: every message its simulation sends goes to its own client and to the
  guest, except: replies to a command (`PIE_MENU_CREATE`,
  `OBJECT_IS_INTERACTABLE`) go only to whoever issued the command; saving and
  travel messages stay local; `SET_SIM_ACTIVE` operations are cut out of the
  guest's copy of a view update, so each player selects sims on their own.
- Guest: its own simulation keeps running but is muted: only save messages
  and `SET_SIM_ACTIVE` reach its client. `interactions.*`, `ui.dialog.*`,
  `clock.setspeed` and `clock.toggle_pause_unpause` are sent to the host and
  not run locally. `animation.*` and the rest of `clock.*` are dropped. Other
  commands still run against the muted local simulation and are logged once
  each in `tsmp.log` ("local command while live") as a list of what remains
  to be routed.

### Following the host through a reload

The guest's game client only makes sense of the host's messages while it was
built from the same state as the host's. Whenever the host's zone is loaded
again (it edits a sim in CAS, travels, loads a save) its client is rebuilt
and the guest's is not; joining again as before leaves the guest with a
broken interface on a world that no longer exists.

So a guest whose host leaves its zone while live becomes *stale*: it reports
itself as not in a zone, shows its client nothing new, and once the host is
back asks for its state with `resync`. What makes the rest possible is that
the whole save lives in Python:

- `services.get_persistence_service()` owns one `SaveGameData` message; the
  game client reads it through `c_api_get_data_readonly`, and a zone is built
  from it.
- Leaving a zone goes through `areaserver.c_api_request_client_disconnect`,
  which saves with `PersistenceService.save_to_scratch_slot_gen` and, when
  travelling, first calls `game_services.disable_shutdown()` so that sims and
  households stay in memory.
- The objects and architecture of a zone are handed to the game client as a
  `ZoneObjectData` message through `persistence_module.run_persistence_operation`
  with `kPersistenceOpSaveZoneObjects`.

The host pauses, saves to the scratch slot and sends both messages in a
`STATE` frame (`u8[4] "TSS1", u8 flags, u64 zone id, u32 save size, u32
objects size`, then the two, deflated when flag 1 is set). The guest keeps
them and asks its client to travel to the host's zone
(`travel_service.travel_sims_to_zone`). When the client disconnects to do so,
the guest's save step is replaced by putting the host's messages in place
and giving the save to the client with `kPersistenceOpSave` for the scratch
slot, as the game's own save ends (without that, a zone the guest's save
never had loads as a void: interface, no world),
and `disable_shutdown` is skipped so that everything is read again. After
its loading screen the guest joins as on a fresh start. If any step fails
(the host cannot save, nothing arrives in `RESYNC_TICKS`, the snapshot cannot
be read, the client does not reload) the guest joins as it is, which is what
happened before. The `tsmp.resync` cheat makes a guest do all this on request.

### Different lots

A game simulates one lot, so two players on different lots cannot share a
simulation. `state` carries the id of the zone a game is in, and a session is
only live while both are in the same one. Otherwise the two are *apart*:
connected, not muted, each playing its lot by itself.

- The host travels: the guest does not follow. It reloads the lot it is on
  from the host's save (the host keeps the `ZoneObjectData` of every zone it
  saved, so the lot comes as the host left it) and plays on alone.
- The guest travels away from the host: while joined its own simulation was
  muted and is behind, so on the new lot it loads the host's state once more
  and then plays alone.
- The guest arrives on the host's lot: it gives its household to the host
  (below), loads the host's state and joins.

### Households from different saves

Each player can make a household in a save of their own and bring it along.
`SaveGameData.guid` is the same in every save and slot that descends from one
new game and differs between new games, so two games compare it to know
whether they are in the same world. A session is never live across two
saves. Instead:

1. The guest saves (the save the game makes before a travel) and sends a
   partial `SaveGameData`: the `HouseholdData` of the household it plays, the
   `SimData` of its sims, the `ZoneData` of its home lot, and the
   `ZoneObjectData` of that lot.
2. The host copies those into its own save through the persistence service
   (`add_sim_proto_buff`, `add_household_proto_buff`, the zone's message in
   place) and calls `HouseholdManager.load_household`, which is what the game
   does for a household it meets in a save. For a household already in
   memory that call reads every sim that is not on the current lot again.
   Zone ids are made up by every new game, so the guest's home is looked
   up in the host's save by `ZoneData.lot_description_id` (and
   `active_plex`), which name the lot of the world; the contents are copied
   under the host's ids. Sims that stand in zones the host's save does not
   have are put on that lot, or on the host's lot when the household gets
   no home, and the host remembers to send a guest asking for such a zone
   there (the snapshot names the zone the guest loads).
   The lot is only handed over when it has no owner in the host's save and
   the host is not standing on it; ownership is written both to
   `ZoneData.household_id` and to the lot's `lot_owner` in its neighbourhood.
   A household whose lot is taken arrives homeless. The household the host
   itself plays is never replaced.
3. The host answers `merged`, and the guest asks for the host's state as
   after any reload. The game client reconnects with the household it was
   playing, which the host's save now has, so the guest goes on playing its
   own household in the host's world.

When the host refuses, or the guest cannot save, both keep playing their own
saves unjoined, and it is not tried again for that save.

The same message keeps the host up to date afterwards: a guest that plays by
itself gives its household every `GIVE_TICKS` ticks and before it joins the
host on a lot. While joined nothing is given, as the host's simulation is the
one that runs.

When the guest reports the sim it controls and that sim belongs to another
household than the host's, the host adds it to its client's selectable sims
and spawns it if it is not on the lot, so that it is simulated as a played
sim.

Not merged: relationships (they live in a service of the save, not in the
sims), the clock (each game keeps its own while apart), and whatever happened
on the guest's lot between its last gift and the host walking onto that lot.

This has only been run against fakes (`mod/tests/test_session.py`,
`test_relay.py`, `test_resync.py`); it has not been seen working in two games
yet.

Known gaps: there is no replay, so the host should stay paused until the
guest is in; dialogs and notifications go to both players; the guest does not
follow the host into CAS, it waits; replies to commands that still run
locally on the guest (hovertips, for one) never arrive.

## Open questions

1. Can a second game process be fed a live stream through `omega.send` and
   render it? This is the core assumption of the guest side. The whole path is
   tested up to that call (`mod/tests/test_relay.py`) but not in two games.
2. Does `-disablepacks:` work when `TS4_x64.exe` is started directly?
3. What is sent when entering and leaving CAS? The capture has no CAS command.
4. Are any client -> simulation paths *not* commands? Build mode shows that
   some client state reaches the simulation natively.
