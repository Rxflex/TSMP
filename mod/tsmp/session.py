"""What each side of a multiplayer session does with messages and commands.

The host's simulation is the only one that counts. Its output goes to its own
game client and, as MSG frames, to the guest's game client. The guest's
simulation keeps running but is muted; commands from the guest's game client
are sent to the host as COMMAND frames and executed there.

The guest's game client only makes sense of the host's messages while it was
built from the same state as the host's. When the host's zone is loaded again
(it edited a sim, or travelled) the guest is stale: it stops taking part,
asks the host for its state and loads it (see tsmp/resync.py), then joins
again.

Each game simulates one lot. Two players on different lots are *apart*: both
are connected, neither is muted, and each plays its own lot by itself. They
are joined when the guest comes to the lot the host is on, and the guest
then loads the host's state as after any reload.

Each player may come with a save of their own. Two games in different saves
cannot be joined, so the guest first gives its household to the host, who
puts it into its save (see tsmp/household.py), and then loads the host's
state, in which it keeps playing that household. The guest gives its
household again whenever it has played by itself: every few minutes while
apart, and before it joins the host on a lot.

Pure logic with no game imports: the game is reached through the `game`
adapter (see live.Game) so this can be tested outside the game.
"""

from tsmp import frames, wire

HOST = "host"
GUEST = "guest"

# Commands the guest's game client issues that act on the shared world: they
# are executed by the host instead of locally.
FORWARD_PREFIXES = ("interactions.", "ui.dialog.")
FORWARD_NAMES = frozenset(["clock.setspeed", "clock.toggle_pause_unpause"])
# Commands that are dropped on the guest. `animation.*` reports progress of
# animations to the simulation that started them, which is the host's, and
# the host's own game client already reports it. The rest of `clock.*` would
# pause the host whenever the guest opens a menu.
BLOCK_PREFIXES = ("animation.", "clock.")
# Ticks a stale guest waits for the host's state, or for its own game client
# to start loading it, before it gives up and joins as it is.
RESYNC_TICKS = 1800
# Ticks between two times a guest playing by itself gives its household to
# the host. Giving takes a save, which the player notices.
GIVE_TICKS = 9000


class Consts:
    """Message and operation ids the session needs, resolved by the caller
    from the game's protocol modules."""

    def __init__(self, view_update, pie_menu, interactable, set_sim_active, host_only=(), guest_local=()):
        self.view_update = view_update
        # Replies to a command, wanted only by the client that issued it.
        self.replies = frozenset([pie_menu, interactable])
        # Operation type that changes the client's selected sim.
        self.set_sim_active = set_sim_active
        # Messages the host's simulation sends that the guest must not see.
        self.host_only = frozenset(host_only)
        # Messages the guest's own simulation may still send to its client.
        self.guest_local = frozenset(guest_local)


class Session:
    """`game` provides:
        in_zone()                      True while a zone is loaded and running
        zone_id()                      id of that zone
        active_sim_id()                id of the locally selected sim, or 0
        deliver(msg_id, data)          hand a message to the local game client
        execute(name, args, sim_id)    run a command as the remote player
        snapshot(done, zone_id)        host: save, then done(bytes or None);
                                       the snapshot is for a guest in that zone
        adopt(payload)                 guest: load the host's snapshot by
                                       reloading the zone; raises on failure
        save_id()                      what tells one save from another, or 0
        contribute(done)               guest: save, then done(bytes or None)
                                       with the household it plays
        merge(payload)                 host: take that household into its
                                       save; raises on failure
        welcome(sim_id)                host: have the other player's sim on
                                       the lot and controllable
    `send(frame)` queues a packed frame for the launcher.
    """

    def __init__(self, game, consts, send, log=None):
        self._game = game
        self._consts = consts
        self._send = send
        self._log = log or (lambda text: None)
        self.role = None
        self.remotes = 0
        self.remote_in_zone = False
        self.remote_sim_id = 0
        self._in_zone = False
        self._zone = 0
        self.remote_zone = 0
        self._save = 0
        self.remote_save = 0
        # Guest: the host's save we last tried to get into.
        self._glued = 0
        # Guest: our household is to be given to the host before its state
        # is loaded, and whether that is under way.
        self._give = False
        self._giving = False
        # Guest: tick of the last time the household was given while apart.
        self._gave = 0
        # Guest: we were joined since we last loaded the host's state, so the
        # host's simulation, not ours, knows how things are.
        self._behind = False
        # Guest: the two were on different lots since the host's state was
        # last loaded, so what we have of the host's lot is not what it has.
        self._apart = False
        self._reported_sim_id = 0
        self._remote_depth = 0
        self._was_live = False
        self._seen_local = set()
        self._ticks = 0
        # Guest: the host's zone was loaded again and ours was not.
        self._stale = False
        # Guest: tick at which the host was asked for its state, or the game
        # client for a reload.
        self._asked = 0
        # Guest: the host's state arrived and our zone is being loaded again.
        self._adopting = False
        self._left = False
        # Host: a snapshot is being taken.
        self._snapshotting = False
        self.forwarded = 0
        self.executed = 0
        self.relayed = 0
        self.delivered = 0

    @property
    def live(self):
        """True while the two games are joined: messages flow host -> guest
        and commands guest -> host."""
        return (self.role is not None and self.remotes > 0 and self._in_zone and self.remote_in_zone
                and self.same_zone and self.same_save and not self._stale)

    @property
    def same_zone(self):
        """False when both are known to be on different lots."""
        return not self._zone or not self.remote_zone or self._zone == self.remote_zone

    @property
    def same_save(self):
        """False when both are known to play different saves."""
        return not self._save or not self.remote_save or self._save == self.remote_save

    @property
    def apart(self):
        """True while both play, each on a lot of its own."""
        return (self.role is not None and self.remotes > 0 and self._in_zone and self.remote_in_zone
                and not (self.same_zone and self.same_save))

    @property
    def stale(self):
        return self._stale

    @property
    def _holding(self):
        """True while a stale guest waits for the host: its game client is
        shown nothing new, as its own simulation is not the one that counts."""
        return self.role == GUEST and self._stale and not self._adopting and self._in_zone

    # -- connection to the launcher -------------------------------------

    def bridge_up(self, role):
        self.role = role if role in (HOST, GUEST) else None
        self._forget_remote()
        self._fresh()
        self.remotes = 0
        self._in_zone = bool(self._game.in_zone())
        self._zone = self._zone_id() if self._in_zone else 0
        self._save = self._save_id() if self._in_zone else 0
        self._glued = 0
        self._announce(want=True)
        self._log("bridge connected, role {}".format(self.role))
        self._check_live()

    def bridge_down(self, reason=""):
        self._log("bridge lost: {}".format(reason))
        self.role = None
        self.remotes = 0
        self._forget_remote()
        self._fresh()
        self._check_live()

    def _fresh(self):
        self._stale = False
        self._asked = 0
        self._adopting = False
        self._left = False
        self._snapshotting = False
        self._apart = False
        self._behind = False
        self._give = False
        self._giving = False
        self._gave = self._ticks

    def _save_id(self):
        try:
            return int(self._game.save_id() or 0)
        except Exception:
            return 0

    def _zone_id(self):
        try:
            return int(self._game.zone_id() or 0)
        except Exception:
            return 0

    def _forget_remote(self):
        self.remote_in_zone = False
        self.remote_zone = 0
        self.remote_save = 0
        self.remote_sim_id = 0
        self._reported_sim_id = 0

    def _announce(self, want=False):
        """Tell the other game our state. `want` asks it to answer with its
        own, which a game that has just (re)connected needs."""
        if self.role is None:
            return
        # A stale guest is in a zone, but not in one the host can play with.
        in_zone = self._in_zone and not self._stale
        fields = {"role": self.role, "in_zone": in_zone}
        if self._in_zone and self._zone:
            # As text: ids are 64 bits wide.
            fields["zone"] = str(self._zone)
        if self._in_zone and self._save:
            fields["save"] = str(self._save)
        if want:
            fields["want"] = True
        self._send(frames.control("state", **fields))

    def _check_live(self):
        if self.role == GUEST and not self._stale and self._in_zone and self.remote_in_zone and self.remotes > 0:
            if not self.same_save:
                if self._glued != self.remote_save:
                    # Once for a load: if it does not work, each plays alone.
                    self._glued = self.remote_save
                    self._log("the host plays another save; bringing our household into it")
                    self._go_stale(give=True)
            elif not self.same_zone:
                if self._behind:
                    # We were muted on the host's lot: what we have of our
                    # sims is older than what the host has.
                    self._log("left the host's lot")
                    self._go_stale()
                elif not self._apart:
                    self._apart = True
                    self._gave = self._ticks
                    self._log("the host is on another lot; playing this one by ourselves")
            elif self._apart:
                # Our copy of this lot was not played by the host, and the
                # host's copy of our household was not played by us.
                self._log("on the host's lot again")
                self._go_stale(give=True)
        live = self.live
        if live == self._was_live:
            return
        self._was_live = live
        self._log("session is live as {}".format(self.role) if live else "session is not live")
        if live and self.role == GUEST:
            self._behind = True
            self._report_sim()

    def _report_sim(self):
        sim_id = self._game.active_sim_id() or 0
        if sim_id != self._reported_sim_id:
            self._reported_sim_id = sim_id
            self._send(frames.control("active_sim", sim_id=str(sim_id)))

    # -- once per simulation tick ---------------------------------------

    def tick(self):
        if self.role is None:
            return
        self._ticks += 1
        in_zone = bool(self._game.in_zone())
        zone = self._zone_id() if in_zone else 0
        if in_zone != self._in_zone or zone != self._zone:
            self._in_zone = in_zone
            self._zone = zone
            if in_zone:
                self._save = self._save_id()
            # The host forgets our sim when we leave a zone.
            self._reported_sim_id = 0
            if self._stale:
                self._zone_changed_while_stale()
            elif in_zone:
                # The player loaded something by hand. If that is a save of
                # their own again, its household is brought over again.
                self._glued = 0
            self._announce()
            self._check_live()
        if self.role == GUEST and self._stale:
            self._catch_up()
            self._check_live()
        if self.role == GUEST and self.live:
            self._report_sim()
        if self.role == GUEST and self._apart and not self._stale and self.apart and self.same_save:
            if not self._giving and self._ticks - self._gave > GIVE_TICKS:
                self._contribute()

    # -- a guest following the host through a reload ---------------------

    def resync(self):
        """Guest: load the host's state again, as after a reload of its
        zone. Returns False when there is nobody to load it from."""
        if self.role != GUEST or self._stale or not (self.live or self.apart):
            return False
        self._log("reloading from the host on request")
        # Apart, we are the one who knows how our household is.
        self._go_stale(give=not self.live)
        self._check_live()
        return True

    def _go_stale(self, give=False):
        self._stale = True
        self._asked = 0
        self._adopting = False
        self._left = False
        self._apart = False
        self._behind = False
        self._give = give
        self._giving = False
        self._announce()

    # -- a guest giving its household to the host ------------------------

    def _contribute(self):
        self._giving = True
        self._gave = self._ticks
        try:
            self._game.contribute(self._contributed)
        except Exception as error:
            self._log("could not save our household: {}".format(error))
            self._contributed(None)

    def _contributed(self, payload):
        if not self._giving or self.role != GUEST:
            return
        if payload is not None and self.remotes > 0:
            self._send(frames.pack(frames.HOUSEHOLD, 0, payload))
            if self._stale:
                # The host answers with `merged`.
                return
        self._giving = False
        if self._stale:
            self._given(False)

    def _given(self, ok):
        """The host has our household, or will not have it."""
        self._give = False
        self._giving = False
        self._asked = 0
        if ok:
            self._log("the host has our household")
        elif self.same_save:
            self._log("the host did not take our household; loading its state as it is")
        else:
            self._settle("the host did not take our household; each plays their own save")

    def _merge(self, payload):
        if self.role != HOST:
            return
        try:
            if not self._in_zone:
                raise ValueError("no zone is loaded")
            self._game.merge(payload)
        except Exception as error:
            self._log("could not take the other player's household: {}".format(error))
            self._send(frames.control("merged", ok=False))
        else:
            self._send(frames.control("merged", ok=True))

    def _settle(self, why):
        """Stop being stale, with or without the host's state."""
        self._log(why)
        self._fresh()
        self._reported_sim_id = 0
        self._announce()

    def _host_lost(self):
        if self._stale and not self._adopting:
            self._settle("the host is gone; staying as we are")
        self._snapshotting = False

    def _zone_changed_while_stale(self):
        if not self._adopting:
            # The player loaded something by hand; that is a fresh start.
            if not self._in_zone:
                self._settle("left the zone before the host's state arrived")
        elif not self._in_zone:
            self._left = True
        elif self._left:
            self._settle("reloaded after the host")

    def _catch_up(self):
        if self._adopting:
            if not self._left and self._ticks - self._asked > RESYNC_TICKS:
                self._settle("the game did not reload after the host; staying as we are")
        elif self._giving:
            if self._ticks - self._asked > RESYNC_TICKS:
                self._given(False)
        elif self._asked:
            if self._ticks - self._asked > RESYNC_TICKS:
                self._settle("the host did not send its state; staying as we are")
        elif not (self._in_zone and self.remote_in_zone and self.remotes > 0):
            pass
        elif self._give:
            self._asked = self._ticks
            self._log("giving our household to the host")
            self._contribute()
        else:
            self._asked = self._ticks
            self._log("asking the host for its state")
            if self._zone:
                self._send(frames.control("resync", zone=str(self._zone)))
            else:
                self._send(frames.control("resync"))

    def _adopt(self, payload):
        if self.role != GUEST or not self._stale or self._adopting or not self._in_zone:
            return
        try:
            self._game.adopt(payload)
        except Exception as error:
            self._settle("could not load the host's state ({}); staying as we are".format(error))
            return
        self._adopting = True
        self._left = False
        self._asked = self._ticks

    def _snapshot(self, zone=0):
        if self.role != HOST or self._snapshotting:
            return
        if not self._in_zone:
            self._send(frames.control("resync_failed"))
            return
        self._snapshotting = True
        self._log("the other player is reloading; saving for them")
        try:
            self._game.snapshot(self._snapshot_done, zone)
        except Exception as error:
            self._log("could not save for the other player: {}".format(error))
            self._snapshot_done(None)

    def _snapshot_done(self, payload):
        if not self._snapshotting:
            return
        self._snapshotting = False
        if self.role != HOST:
            return
        if payload is None:
            self._send(frames.control("resync_failed"))
        else:
            self._send(frames.pack(frames.STATE, 0, payload))

    # -- frames from the other side -------------------------------------

    def frame(self, kind, ident, payload):
        if kind == frames.CONTROL:
            self._control(frames.parse_json(payload) or {})
        elif kind == frames.MSG:
            if self.role == GUEST and self.live:
                self.delivered += 1
                self._game.deliver(ident, payload)
        elif kind == frames.COMMAND:
            if self.role == HOST and self.live:
                self._execute(frames.parse_json(payload) or {})
        elif kind == frames.STATE:
            self._adopt(payload)
            self._check_live()
        elif kind == frames.HOUSEHOLD:
            self._merge(payload)

    def _control(self, message):
        kind = message.get("type")
        if kind == "link":
            self.remotes = int(message.get("remotes") or 0)
            if self.remotes == 0:
                self._forget_remote()
                self._host_lost()
            # Whoever just connected has not heard our state yet, nor we theirs.
            if self.remotes:
                self._announce(want=True)
        elif kind == "gone":
            self._forget_remote()
            self._host_lost()
        elif kind == "state":
            if message.get("role") == self.role:
                self._log("the other game is also a {}; ignoring it".format(self.role))
                return
            if message.get("want"):
                self._announce()
            was_live = self.live
            self.remote_in_zone = bool(message.get("in_zone"))
            self.remote_zone = _number(message.get("zone"))
            self.remote_save = _number(message.get("save"))
            if self.role == GUEST and was_live and not self.remote_in_zone:
                # Its zone will be built again, and its game client with it.
                self._log("the host left its zone")
                self._go_stale()
            if not self.remote_in_zone:
                self.remote_sim_id = 0
                self._reported_sim_id = 0
        elif kind == "resync":
            self._snapshot(_number(message.get("zone")))
        elif kind == "resync_failed":
            if self.role == GUEST and self._stale and not self._adopting:
                self._settle("the host could not save; staying as we are")
        elif kind == "merged":
            if self.role == GUEST and self._stale and self._giving:
                self._given(bool(message.get("ok")))
        elif kind == "active_sim":
            self.remote_sim_id = _number(message.get("sim_id"))
            if self.role == HOST and self.remote_sim_id and self._in_zone:
                try:
                    self._game.welcome(self.remote_sim_id)
                except Exception as error:
                    self._log("could not bring the other player's sim in: {}".format(error))
        self._check_live()

    def _execute(self, message):
        name = message.get("name")
        args = message.get("args")
        if not isinstance(name, str) or not isinstance(args, list) or not _forwarded(name):
            return
        self.executed += 1
        self._remote_depth += 1
        try:
            self._game.execute(name, [str(a) for a in args], self.remote_sim_id)
        finally:
            self._remote_depth -= 1

    # -- hooks into the local game --------------------------------------

    def outbound(self, msg_id, data):
        """The local simulation is sending `data` to the local game client.
        Returns what the local client should receive instead, or None for
        nothing."""
        if not self.live and not self._holding:
            return data
        consts = self._consts
        if self.role == HOST:
            if msg_id in consts.replies:
                if self._remote_depth:
                    self._relay(msg_id, data)
                    return None
                return data
            if msg_id in consts.host_only:
                return data
            if msg_id == consts.view_update:
                # Each player selects sims independently.
                try:
                    remote, _ = wire.split_view_update(data, consts.set_sim_active)
                except ValueError:
                    remote = data
                if remote:
                    self._relay(msg_id, remote)
                return data
            self._relay(msg_id, data)
            return data
        if msg_id in consts.guest_local:
            return data
        if msg_id == consts.view_update:
            try:
                _, selection = wire.split_view_update(data, consts.set_sim_active)
            except ValueError:
                return None
            return selection or None
        return None

    def _relay(self, msg_id, data):
        self.relayed += 1
        self._send(frames.pack(frames.MSG, msg_id, data))

    def command(self, name, args):
        """The local game client issued a command. Returns False if it must
        not run locally."""
        if self._holding:
            return not (_forwarded(name) or name.startswith(BLOCK_PREFIXES))
        if self.role != GUEST or not self.live:
            return True
        if _forwarded(name):
            self.forwarded += 1
            self._send(frames.command(name, args))
            return False
        if name.startswith(BLOCK_PREFIXES):
            return False
        if name not in self._seen_local:
            # A record of what still runs against the muted local simulation.
            self._seen_local.add(name)
            self._log("local command while live: {}".format(name))
        return True


def _number(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _forwarded(name):
    return name in FORWARD_NAMES or name.startswith(FORWARD_PREFIXES)
