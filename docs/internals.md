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
connection. The stream's limit is either its size or something else; the
frame says which, and the replayed stream gets the sender's kind of limit.

Live mode switches this on by itself as soon as a zone runs (its loading
screen is gone), whether or not the session is live; `"native": false` in
`config.json` keeps it off, and so does `tsmp.native off` until another mode
is asked for. Every line a mod logs also travels as a `log` control frame:
the launchers show the latest lines and the other mod writes them to its own
`tsmp.log`, so one file tells what both games did.

#### Journal and catch-up

The host's state (`tsmp/resync.py`) carries no walls: the architecture of a
lot is only in the save file the game client writes. An edit the other game
does not replay is therefore lost there for good, and edits are made while
the two are not playing together all the time: the host builds while the
guest loads or reloads after a resync, during a link drop, or while the two
are on different lots. So each game keeps its own edits (`native.Sync`,
the same in both games, whoever is the host):

- From the moment a zone runs, every `RequestBuildBuyOp` of this game's
  client goes into a journal under the lot it was made on, `(save id, zone
  id)`, the zone named as the host names it (`live.Game.zone_id`), and is
  numbered 1, 2, ... per lot in the order the client made it. Calls the other
  game's client made are replayed past the hook and never journaled, so an
  edit is only ever sent by the game where it was made. A journal keeps at
  most 20000 operations or 8 MiB per lot; beyond that the oldest are dropped,
  with a line in the log (the first, then one per 1000).
- Each game keeps, per lot, the number of the other game's last operation it
  replayed (`applied`).
- Each time the session becomes live (`live.py` sees `Session.live` go from
  false to true), the game sends `native {ok, format}` and `native_have
  {zone, save, run, seq, want}`: whether it takes part, and how far it got
  with the other game's edits of the current lot. The answer to a
  `native_have` is every kept operation of that lot numbered above `seq`, in
  order and before any new one, at most 256 operations or 1 MiB per tick;
  then new operations as they are made. `want` asks the other game for its
  own `native_have`, so that a game that saw the session become live when
  the other did not still learns where to start. A game whose `have` is
  older than its oldest kept operation is sent what is left, flagged, and
  both logs say how many edits cannot be replayed.
- An operation is only replayed when it is for the lot and save of the
  receiver, has the number after the last one replayed (a smaller one is a
  duplicate and is ignored), and arrives while the session is live. One with
  a gap in front of it is not replayed: the receiver sends `native_have`
  again with its last number, once per number, and the sender starts over
  from there.
- An operation that cannot be replayed yet waits, for at most 30 s and up to
  256 operations or 2 MiB, and is tried again every tick: before a recorder
  exists, before the zone's object is hooked, and before the client has
  called the zone since it was loaded, there is no connection to replay on.
  The connection is kept per zone object and forgotten when the zone changes
  or an object is hooked anew, because every load of a zone opens a new
  session and an old connection id would be refused. What waits is dropped,
  with a line in the log, when the zone changes or the session stops being
  live (the next `native_have` gets it sent again), and after 30 s (asked
  for again at once).
- Numbers start at 1 each time a game starts, so `run`, drawn at random at
  start, tells the runs apart: an operation of another run than the one
  `applied` names starts that lot over, and a `have` naming another run of
  ours is answered from the start.

Both games share operations only while both say `ok`: allowed in
`config.json`, not switched off with the cheat, and a build of the game the
probe recognises. Otherwise each keeps journaling and logs once what holds
it back; `tsmp.status` shows it in the `native sync` line, with counts of
recorded, sent, replayed, waiting, duplicate, refused and too large
operations. A game that turns sync back on sends `native` and `native_have`
again and is caught up. An operation larger than 1 MiB is not shared; the
log says so with its size.

`NATIVE` payload, little endian: `u8 format` (1), `u8 flags` (1: the
stream's limit was its size; 2: the operations just before this one are no
longer kept by the sender), `u64 zone id`, `u64 save id`, `u32 run`, `u32
number`, then the call's own `u32` third and fourth arguments, `u32` read
position and the stream's bytes. The frame's id is the call's hash.

Known limitations:

- Two players editing the same element at nearly the same time may see the
  edits applied in different orders: each simulation applies its own edit
  first and the other's when it arrives. Making one game order all edits
  would mean holding a guest's call back from its own simulation, which has
  not been tried against the game.
- The journal and `applied` live in memory for as long as the game runs. A
  game that is started again has neither: it is sent the other game's whole
  journal of the lot again, including edits its save may already hold, and
  what it made itself before it was restarted is not sent at all.

Answered by running it: a `_ctypes` callback may run on the game's threads
(it takes the interpreter lock; no deadlock or crash was seen), and a client
draws an edit it did not ask for whether or not it is in build mode.

Open:

- Whether an object bought in build mode ends up placed twice, since objects
  also travel as simulation messages.
- Whether household funds are charged in both simulations, and what the
  other player's undo stack does with an edit it did not make.
- Behaviour after travel to another lot. The zones are hooked again when the
  zone changes and every 64 ticks, and edits wait until the new zone's
  client has called it; not yet tried.
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

Between the launchers, "LAN" may also be a session server; see
[Meeting through a session server](#meeting-through-a-session-server).

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
| 7 PART | transfer id | `u32 offset`, then a piece of a large payload |

Control messages:

- `link {remotes}` (launcher -> its game: how many other launchers are
  connected), `gone` (launcher -> the other side: the game here
  disconnected), `beat` (launcher -> launcher, never passed to a game).
- `state {role, in_zone, v, loaded, syncing, joined, apart, zone?, lot?,
  save?, world?, want?}` (game -> game). `in_zone` is false for a stale
  guest even in a zone; `loaded`, `syncing` (stale), `joined` (live) and
  `apart` are for the launchers to show: loading the other's game, waiting
  for it, getting in step, each on its own lot or save, playing alone for
  now after a failure, or joined. `v` is the version of this protocol (games that
  speak another one are not joined), `zone` the zone's id as the host names
  it, `lot` the lot it is on (`"<lot_description_id>:<active_plex>"`),
  `save` the `guid` of the game's `SaveGameData`, `world` the world its game
  client shows (see below), `want` asks for the other's state in return.
- `active_sim {sim_id}` (guest -> host).
- `resync {id, zone?, lot?, household?}` (guest -> host: send your state,
  for a guest on that lot, playing that household), answered by an `offer`
  of the state or `resync_failed {id, missing?}`; `missing` when the host's
  save does not have the household at all (its game was started again from
  a save without it), and the guest then gives all of it again.
- `merged {id, ok, final?, missing?, outcome?}` (host -> guest: your
  household is, or is not, in my save; `final` when trying again would not
  help, `missing` when the host lacks the household and the gift held only
  part of its sims, so the guest is to give all of it).
- `offer {id, what, size, ...}`, `got {id, have}`, `drop {id}`: a transfer
  (`tsmp/transfer.py`). `what` is `state` (with the `world` it shows) or
  `household` (with the `save` it comes from).
- `tell {text}` (host -> guest: a notification for the guest's player).
- `native {...}`, `native_have {...}`: build mode sync, see above.

Every request carries an `id` of the guest's, and every answer the id of the
request it answers, so a late answer to a request the guest gave up on is
told from the answer it waits for.

Snapshots and households run to megabytes. Sent whole they would hold up
everything behind them for as long as they take to cross a slow link, and
the launchers refuse frames past 64 MiB. So they go as `PART` frames of
128 KiB after an `offer`; the receiver confirms with `got` every 256 KiB,
and no more than 2 MiB is sent and not yet confirmed. Control frames pass in
between, and both sides see the transfer move.

The launchers only relay; the guest's launcher opens a second connection to
the host's with `JoinLive` and after the `Hello` reply the socket carries raw
frames. Policy lives in `tsmp/session.py`. The session is *live* while both
games have the same zone loaded and their game clients show the same world
(below). While live:

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

So every load of the host's zone is a *world*, named by the host (a number
drawn when the game starts and a count of loads), and each game says in
`state` which world its client shows: the host its own, a guest the one of
the state it last loaded, none when its client was built from its own
simulation or may have missed a message. A guest whose client does not show
the host's world is *stale*: it reports itself as not in a zone, shows its
client nothing new, and once the host is there asks for its state with
`resync`. That covers every way the two can part:

- the guest's first arrival: the save it loaded is the one the host loaded,
  but the host may have played since;
- the host's zone is loaded again;
- the guest's own zone is loaded again (CAS on the guest, the player
  loading the same save, its game restarting);
- the link between the two is lost: what the host sent meanwhile is gone.
  The guest holds, tells its player, and waits `LINK_SECONDS` (two
  minutes, as long as the session server keeps a session) for the host;
  once back, it loads the host's state. A guest that finished loading the
  host's state while the link was down joins without loading again if the
  host still shows that world;
- the host is connected but in no zone (main menu, CAS, a loading screen):
  the guest tells its player and waits `AWAY_SECONDS` (ten minutes) before
  it plays on by itself.

Only a guest whose client shows the host's world is muted while stale; one
that never joined, or whose client was built from its own simulation since,
plays on with it until the host's state loads, so its client misses nothing.
A guest whose launcher goes away while joined loads its own zone again. A
guest that loaded the host's state of the host's lot and then finds the host
gone from it plays on, and gives its household when they meet again. A guest
that has not loaded the host's state since its game started (its save is
older than the host's game) loads the host's copy of its lot before it plays
apart, so that nothing it gives is older than what the host has.

What makes the rest possible is that the whole save lives in Python:

- `services.get_persistence_service()` owns one `SaveGameData` message; the
  game client reads it through `c_api_get_data_readonly`, and a zone is built
  from it.
- Leaving a zone goes through `areaserver.c_api_request_client_disconnect`,
  which saves with `PersistenceService.save_to_scratch_slot_gen` and, when
  travelling, first calls `game_services.disable_shutdown()` so that sims and
  households stay in memory.
- The objects of a zone are handed to the game client as a
  `ZoneObjectData` message through `persistence_module.run_persistence_operation`
  with `kPersistenceOpSaveZoneObjects`.
- The architecture of a lot (walls, floors, roofs, terrain) never reaches
  Python. In the save file a zone is one resource of type `0x00000006`,
  compressed with RefPack: field 1 is the zone id, field 2 the architecture
  (the same `LDNB` block the client sends the simulation in message 708 when
  a lot loads), field 3 the objects. The client fills field 2 itself when it
  writes the file and reads it from the file when it loads a zone. So a
  guest that was started from another save than the host's draws the host's
  objects inside its own version of the lot: where the host built a house on
  a lot that is empty in the guest's save, doors and windows stand without
  walls. Only the host's save file carries the host's architecture.

### Screens of one player

`UiDialogMessage` (`MSG_UI_DIALOG_SHOW`, `MSG_UI_NOTIFICATION_SHOW`) names
the sim it is for in `owner_id` (field 5), `TravelMenuCreate` in `sim_id`
(field 1). On the host a message whose sim is played by the guest is sent
to the guest and kept from the host's client; a dialog for any other sim
stays with the host. A sim is the guest's when it is the one the guest
controls, or, when the two play different households, any sim of that
household. Notifications that are not the guest's go to both, since the
game addresses general news to the active sim. Messages that name no sim go
to both as before. The guest's own simulation may send its client
`MSG_TRAVEL_SIMS_TO_ZONE`, which is how a destination picked in the guest's
travel picker takes effect. Not yet seen in two games.

The host pauses, saves to the scratch slot and sends both messages as a
transfer (`u8[4] "TSS1", u8 flags, u64 zone id, u32 save size, u32
objects size`, then the two, deflated when flag 1 is set). The guest keeps
them and asks its client to travel to the host's zone
(`travel_service.travel_sims_to_zone`). When the client disconnects to do so,
the guest's save step is replaced by putting the host's messages in place
and giving the save to the client with `kPersistenceOpSave` for the scratch
slot, as the game's own save ends (without that, a zone the guest's save
never had loads as a void: interface, no world),
and `disable_shutdown` is skipped so that everything is read again. After
its loading screen the guest's client shows the world the snapshot was
taken in, and it joins. If the host loaded its zone again meanwhile, the
worlds differ and the guest loads once more.

From the request on, the host keeps its game paused: what it simulates
before the guest is in would be missing on the guest. It pauses again every
second and refuses `clock.setspeed` and `clock.toggle_pause_unpause` from
its own player, whom it tells why, until the guest is in or says it plays
on by itself (it is in a zone and not joined: on another lot, or it gave
up), the host's save fails, the guest takes none of the snapshot for
`HOLD_SECONDS`, or the link has been down for `LINK_SECONDS`. In the last
two cases the host's game may run on before the guest is in, so the host
starts a new world: a guest that gets in later with the snapshot sees that
it is behind and loads again. A guest whose game reloaded the zone from its
own save instead of the snapshot (the client took longer than
`RELOAD_SECONDS` to start, and the snapshot was dropped) does not join
either. The host never runs two saves for guests at once; one asked for
while another runs is taken when it ends, as the first may be older than
what the guest asks after. It holds only for a guest whose snapshot will be of the
host's own lot, which it works out from its save as the snapshot does; a guest that will be apart is sent its lot's
state while the host plays on, unpaused. A `drop` of what it holds for ends
the hold at once. After a hold runs out, the host only starts
a new world once its game has actually moved on (its clock or its objects),
so a guest that was merely slow to load still joins.

Every wait is in seconds and counts from the last sign of progress, not from
the request: a save gets `SAVE_SECONDS`, a transfer may take as long as it
needs while it moves and is given up after `STALL_SECONDS` without a part
or a confirmation, the host gets `MERGE_SECONDS` to answer for a household,
the game client `RELOAD_SECONDS` to start loading, and the loading screen
itself as long as it takes. A guest that gives up (the host cannot save,
the snapshot cannot be read, something stops moving) plays on by itself and
tries again after 15 s, 1, 3, then every 5 minutes. When its client showed
the host's world, it first loads its own zone again (`Game.reload`, a travel
to the zone it is in) so that the client shows the simulation it now plays.
The `tsmp.resync` cheat makes a guest load the host's state on request.

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
- The guest leaves the host's lot: the host takes the sims it played off its
  lot (`household.send_off`): out of the host client's selectable sims
  unless they are of the host's household, and made to leave
  (`make_sim_leave_now_must_run` of the zone situation manager, else
  `make_sim_leave`, else `schedule_destroy_asap`). Otherwise the host goes
  on simulating them, and its next save overwrites what the guest does with
  them apart.

Two saves of different new games give the same lot different zone ids, so
a zone is compared by its lot: `"<lot_description_id>:<active_plex>"` of
its `ZoneData` (`resync.lot_key`). A guest asking for the host's state names
its zone and that lot, and the host sends the zone of its own save on the
same lot, else the zone of that id, else the zone it led that id to when it
took the guest's household in, else its own zone.

### Households from different saves

Each player can make a household in a save of their own and bring it along.
`SaveGameData.guid` is the same in every save and slot that descends from one
new game and differs between new games, so two games compare it to know
whether they are in the same world. A session is never live across two
saves. Instead:

1. The guest saves (the save the game makes before a travel) and sends a
   partial `SaveGameData`: the `HouseholdData` of the household it plays, the
   `SimData` of its sims, the `ZoneData` of its home lot, of the lot it is
   on and of every lot one of its sims is on, and the `ZoneObjectData` of
   its home lot. A guest playing in a copy of the host's save (step 3) knows
   the host's lots by its own ids, so it renames them back to the host's
   first.
2. The host copies those into its own save through the persistence service
   (`add_sim_proto_buff`, `add_household_proto_buff`, the zone's message in
   place) and calls `HouseholdManager.load_household`, which is what the game
   does for a household it meets in a save. For a household already in
   memory that call reads every sim that is not on the current lot again.
   Zone ids are made up by every new game, so the guest's home is looked
   up in the host's save by `ZoneData.lot_description_id` (and
   `active_plex`), which name the lot of the world; the contents are copied
   under the host's ids. A sim that stands in a zone the host's save does
   not have is put on the same lot in the host's save, found from the
   `ZoneData` sent along, so a guest standing on the host's lot finds its
   sims there; only when that lot is not in the host's save does the sim go
   to the household's home, or to the host's lot when it has no home. The
   host remembers to send a guest asking for such a zone there (the
   snapshot names the zone the guest loads).
   The lot is only handed over when it has no owner in the host's save and
   the host is not standing on it; ownership is written both to
   `ZoneData.household_id` and to the lot's `lot_owner` in its neighbourhood.
   A household whose lot is taken arrives homeless.
   The household the host itself plays is never replaced: when the guest
   plays the same family and played some of its sims apart, only the
   `SimData` of those of its sims the host does not have on its lot is
   copied, and its money, home and lot stay the host's.
   A guest that comes back from a save of its own with a household it
   played joined in the host's save since it was last given brings an older
   copy, which is not taken in. The host keeps, in
   `<user dir>/tsmp/households.json`, the host save each household was last
   merged into and whether it has been played joined there since.
   `take_in` answers `took in`, `updated`, `partial` or `kept`, or raises
   `Refusal`, which is `final` when the payload itself can never be taken
   (unreadable, not one household, no household id) and otherwise may pass
   later.
3. The host answers `merged`, and the guest asks for the host's state as
   after any reload. The game client only travels to zones it was started
   with: asked for an id from the host's save it drops the world and shows
   the interface over a void. So before the guest keeps a snapshot it
   matches the host's zones to its own by `lot_description_id` and
   `active_plex` and renames every 64-bit field of the snapshot that holds
   a host zone id to the guest's id for that lot. From then on the guest
   reports its zone to the host under the host's id. That table belongs to
   the host's save it was made for (by `SaveGameData.guid`): once the guest
   plays another save it is dropped, so that a later session reports its
   zones as they are. The game client reconnects with the household it was
   playing, which the host's save now has, so the guest goes on playing its
   own household in the host's world.

When the host refuses for good (`final`: what came cannot be read, or holds
no household), both keep playing their own saves unjoined, and it is not
tried again for that save. Any other failure (the host is between zones,
the game did not load the household, the link dropped, the guest's save
failed) is tried again later, as above.

The same message keeps the host up to date afterwards: a guest that plays by
itself gives its household every `GIVE_SECONDS` seconds and before it joins
the host on a lot. While joined nothing is given, as the host's simulation is
the one that runs. A guest that was joined and gives up does not give its
household again until it has loaded the host's state: the host's copy is the
newer one. A guest playing in the host's save gives only the sims in zones
its own simulation ran since it last loaded the host's state (those whose
objects it saved), and its home lot's objects only if it ran that lot: the
rest of what it has is the host's copy as it loaded it, older than the
host's own. A guest saves for only one gift at a time; one wanted while a
save runs is taken from that save. A gift the host gave up on (the guest's
game was on a loading screen) is answered with `drop`, and the guest drops
one that has not moved for `STALL_SECONDS` itself. A
household given from the host's own save counts as played there, so a copy
from any other save is older. A guest playing in the host's save gives its home lot's
objects only when the host's snapshot brought them: otherwise its game
started that lot from its own copy (in a later session the host may not
have those objects in memory), and the host keeps its own. A guest that
never loaded the host's state in this game gives nothing of the host's save
after a failed first try; it asks again. A gift on its way when the guest
gives on arrival is dropped, so an older one is never merged after a newer
one. A host whose lot loads again while it saves for a guest saves again.

The sims the host sends off are those the guest plays at the time, not
every sim it once selected: in a shared family that is the one sim it plays.

When the guest reports the sim it controls and that sim belongs to another
household than the host's, the host adds it, and every sim of its household
whose `zone_id` is the host's zone (those that came along), to its client's
selectable sims and spawns those not on the lot, so that they are simulated
as played sims. A guest that has played another household is refused a sim
of the host's own household.

Not merged: relationships (they live in a service of the save, not in the
sims), the clock (each game keeps its own while apart), and whatever happened
on the guest's lot between its last gift and the host walking onto that lot.

This has only been run against fakes (`mod/tests/test_session.py`,
`test_relay.py`, `test_resync.py`, `test_household.py`); it has not been seen working in two games
yet.

Create-a-Sim and other screens the host's simulation opens with
`sims4.commands.client_cheat` for a sim the guest plays are kept from the
host's client (they would take the host's screen) and the guest is told
they cannot be opened yet. A joined guest is refused Create-a-Sim whatever opens it
(`sims.modify_in_cas`, `cas.*`, `sims.exit2cas` on its own client): it
would edit a sim of its muted simulation and lose the edits.

Both launchers copy every save into `saves.tsmp-backup/<time>` before a
session with households of their own: the host's save takes in the guest's
household, and the guest plays on in the host's world, which a save of its
game writes over its own slot.

Known gaps: the guest does not follow the host into CAS, it waits, and it
cannot open CAS for its own sim; replies to commands that still run locally
on the guest (hovertips, for one) never arrive; arriving on the host's lot
takes two loading screens, the travel and then the host's state.

## Meeting through a session server

The game is not involved here; this is how two launchers on different
networks reach each other. Neither usually accepts connections from the
internet, so both connect out to a `tsmp-server` and the server joins the
connections (`crates/tsmp-net/src/rendezvous.rs`, `crates/tsmp-server`).

```
launcher (host) ==control line==> server <==== launcher (guest)
launcher (host) ==one connection per guest connection==^
```

Messages are the launcher protocol's frames (`u32` length, JSON), at most
4 KiB each. The first message of a connection says what it is:

| request | from | answer |
|---|---|---|
| `open {protocol, name, game_version, resume?}` | host | `opened {code, token, ping_seconds}`; the connection stays open as the session's control line |
| `join {protocol, code}` | guest | `joined` once the host has answered, then the connection is the host's |
| `accept {protocol, token, tunnel}` | host, a new connection | `accepted`, then the connection is the guest's |

On the control line the server sends `incoming {tunnel, from}` for each
`join` and `ping` every `ping_seconds`; the host answers `pong` and ends the
session with `close`. A guest waits up to 10 s for the host's `accept`. After
`joined`/`accepted` the server copies bytes both ways and passes on the end of
a stream, so what follows is exactly what a guest would send on the local
network: `hello` and the sync messages, or `join_live` and the relay's frames.
The host's launcher hands such a connection to the same code as one accepted
on its own port (`host.rs::Lobby::welcome`), and the guest's launcher opens
one in place of a direct connection wherever it would dial
(`route.rs::Route::open`), the live link's redials included.

A control line that stops answering pings for three intervals, or drops,
leaves the session waiting for its host for two minutes. The host's launcher
opens again with `resume {code, token}` and keeps its code, so an invite stays
good through a short loss of the network; a guest asking meanwhile is told
the host is away and the live link keeps redialling. After those two minutes
the session is closed, but its code is kept for its host for half an hour:
nobody else is given it, a guest is still told the host is away, and the host
that comes back with its token gets the same code. A live link whose dials
keep failing waits longer each time, up to 16 s, so that it does not knock
on the server many times a minute. A code is eight symbols
of Crockford's base 32 (40 bits, generated by the system's random source),
written `XXXX-XXXX`; reading accepts any case and `O`, `I`, `L` for `0`, `1`,
`1`. An address that asks for 20 codes that do not exist within a minute is
refused for the rest of that minute. The token is 128 random bits and is
needed for `accept` and `resume`.

All connections have TCP keep-alive probes (30 s, then every 10 s), on the
launchers as on the server: a joined connection may be silent for as long as a
game is loading, and routers forget silent connections. Between two launchers
the live link also beats: each sends `beat` every 2 s when it has nothing
else to send, and drops a link that has been silent for 15 s, or whose writes
have not moved for 30 s, so that the games hear of a dead link in seconds and
the guest's launcher dials again.

The server's limits (sessions in all and per address, connections in all and
per address, guests waiting per session) are options of `tsmp-server`.

Tried over loopback against the real launchers' code
(`crates/tsmp-server/tests/server.rs`, the launcher's
`a_guest_joins_from_another_network_with_an_invite`) and with the mod's
session logic (`mod/tests/test_relay.py`, `OnlineRelayTest`). Not yet tried
with two games on two networks.

## Open questions

1. Can a second game process be fed a live stream through `omega.send` and
   render it? This is the core assumption of the guest side. The whole path is
   tested up to that call (`mod/tests/test_relay.py`) but not in two games.
2. Does `-disablepacks:` work when `TS4_x64.exe` is started directly?
3. What is sent when entering and leaving CAS? The capture has no CAS command.
4. Are any client -> simulation paths *not* commands? Build mode shows that
   some client state reaches the simulation natively.
