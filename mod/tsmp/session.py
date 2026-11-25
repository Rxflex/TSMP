"""What each side of a multiplayer session does with messages and commands.

The host's simulation is the only one that counts. Its output goes to its own
game client and, as MSG frames, to the guest's game client. The guest's
simulation keeps running but is muted; commands from the guest's game client
are sent to the host as COMMAND frames and executed there.

The guest's game client only makes sense of the host's messages while it was
built from the host's state and has missed none of them since. So each load
of the host's zone is a *world* with an id of its own, each game says which
world its client shows, and the two are joined only while both show the same
one. A guest whose client shows anything else is *stale*: it stops taking
part, asks the host for its state, loads it (see tsmp/resync.py) and joins.
That is the case on its first arrival, after the host loaded its zone again
(it edited a sim, or travelled), after the guest's own zone was loaded again,
and after the link between the two was lost, as messages were lost with it.
The host keeps its game paused until the guest is in, since what it
simulates meanwhile would be missing on the guest.

Each game simulates one lot. Two players on different lots are *apart*: both
are connected, neither is muted, and each plays its own lot by itself. They
are joined when the guest comes to the lot the host is on.

Each player may come with a save of their own. Two games in different saves
cannot be joined, so the guest first gives its household to the host, who
puts it into its save (see tsmp/household.py), and then loads the host's
state, in which it keeps playing that household. The guest gives its
household again whenever it has played by itself: every few minutes while
apart, and before it joins the host on a lot.

Snapshots and households go in parts (see tsmp/transfer.py), and every step
that waits for the other game gives up when nothing has moved for a while,
however long the whole takes. A guest that gives up plays on by itself and
tries again later.

Pure logic with no game imports: the game is reached through the `game`
adapter (see live.Game) so this can be tested outside the game.
"""

import random
import time

from tsmp import frames, transfer, wire

HOST = "host"
GUEST = "guest"
# What the two mods say to each other. Games that speak another version are
# not joined; the launcher normally makes sure both run the same mod.
PROTOCOL = 2

# Commands the guest's game client issues that act on the shared world: they
# are executed by the host instead of locally.
FORWARD_PREFIXES = ("interactions.", "ui.dialog.")
CLOCK_NAMES = frozenset(["clock.setspeed", "clock.toggle_pause_unpause"])
FORWARD_NAMES = CLOCK_NAMES
# Commands that are dropped on the guest. `animation.*` reports progress of
# animations to the simulation that started them, which is the host's, and
# the host's own game client already reports it. The rest of `clock.*` would
# pause the host whenever the guest opens a menu.
BLOCK_PREFIXES = ("animation.", "clock.")

# Seconds, not ticks: over the internet most of the waiting is for the
# network, and a game that is loading does not tick at all.
#
# A save, the host's for a snapshot or the guest's for its household.
SAVE_SECONDS = 180.0
# A transfer that has not moved for this long is given up.
STALL_SECONDS = 45.0
# The host to answer for a household it has all of.
MERGE_SECONDS = 120.0
# The guest's game client to start loading the host's state.
RELOAD_SECONDS = 90.0
# The other game to be back after the link was lost; the session server
# keeps a session this long for a host whose connection dropped.
LINK_SECONDS = 120.0
# A host that is connected but in no zone (at the main menu, in CAS, on a
# loading screen) is waited for this long; longer than a visit to CAS.
AWAY_SECONDS = 600.0
# Between two gifts of a guest playing by itself. Giving takes a save, which
# the player notices.
GIVE_SECONDS = 300.0
# Host: a guest that is loading and has shown no sign of life for this long
# is not waited for.
HOLD_SECONDS = 300.0
# Host: how often the game is paused again while a guest loads.
PAUSE_SECONDS = 1.0
# Notifications of our own the session keeps track of at most.
MAX_NOTICES = 8
# A guest that gave up tries again after these, one after the other.
RETRY_SECONDS = (15.0, 60.0, 180.0, 300.0)

# What a stale guest is doing. A guest that is not stale is IDLE.
IDLE = "idle"
# Waiting for the host to be connected and in a zone.
WAITING = "waiting"
# Saving its household to give it to the host.
SAVING = "saving"
# Its household is on the way; waiting for `merged`.
GIVING = "giving"
# The host was asked for its state; waiting for it to save.
ASKING = "asking"
RECEIVING = "receiving"
# The host's state is here and the zone is being loaded again.
LOADING = "loading"


class Consts:
    """Message and operation ids the session needs, resolved by the caller
    from the game's protocol modules."""

    def __init__(self, view_update, pie_menu, interactable, set_sim_active, host_only=(), guest_local=(),
                 owned=None, shared=(), notice=None):
        self.view_update = view_update
        # Replies to a command, wanted only by the client that issued it.
        self.replies = frozenset([pie_menu, interactable])
        # Operation type that changes the client's selected sim.
        self.set_sim_active = set_sim_active
        # Messages the host's simulation sends that the guest must not see.
        self.host_only = frozenset(host_only)
        # Messages the guest's own simulation may still send to its client.
        self.guest_local = frozenset(guest_local)
        # Screens that belong to one sim: message id -> number of the field
        # that holds the sim's id. They are shown to the player of that sim.
        self.owned = dict(owned or {})
        # Owned messages the host also sees when they are not the guest's.
        self.shared = frozenset(shared)
        # The message that shows a notification, which is how the session
        # tells its own player what it is doing.
        self.notice = notice


class Session:
    """`game` provides:
        in_zone()                      True while a zone is loaded and running
        zone_id()                      id of that zone, as the host names it
        lot()                          what names its lot in any save, or ""
        active_sim_id()                id of the sim the local player
                                       selected, or 0
        deliver(msg_id, data)          hand a message to the local game client
        execute(name, args, sim_id)    run a command as the remote player
        snapshot(done, zone_id, lot)   host: save, then done(bytes or None);
                                       the snapshot is for a guest there
        adopt(payload)                 guest: load the host's snapshot by
                                       reloading the zone; raises on failure
        adoptions()                    guest: how many snapshots the game has
                                       loaded so far
        reload()                       guest: load our own zone again, so that
                                       its game client shows our simulation
        save_id()                      what tells one save from another, or 0
        household_id()                 guest: the household we play, or 0
        has_household(household_id)    host: whether our save has it
        progress()                     host: text that changes whenever the
                                       game moves on (its clock, its
                                       objects), "" when it cannot tell
        contribute(done, whole)        guest: save, then done(bytes or None)
                                       with the household it plays: all of
                                       it, or what our simulation ran
        merge(payload, save_id)        host: take that household, from that
                                       save, into ours; raises on failure, an
                                       error with `final` set when trying
                                       again would not help
        same_household(a, b)           True if both sim ids are in one household
        pause()                        host: pause the game
        welcome(sim_id, before)        host: have the other player's sim on
                                       the lot and controllable; returns the
                                       ids now theirs, none to refuse it
        send_off(sim_ids)              host: the other player left with these
        joined(sim_id)                 host: the other player plays it here
        notify(text)                   tell the local player something
    `send(frame)` queues a packed frame for the launcher; `clock()` tells the
    time in seconds.
    """

    def __init__(self, game, consts, send, log=None, clock=time.monotonic):
        self._game = game
        self._consts = consts
        self._send = send
        self._log = log or (lambda text: None)
        self._clock = clock
        self._transfers = transfer.Transfers(send, clock)
        self.role = None
        self.remotes = 0
        self.remote_in_zone = False
        self.remote_sim_id = 0
        self.remote_zone = 0
        self.remote_lot = ""
        self.remote_save = 0
        # The world the other game's client shows.
        self.remote_world = ""
        self._remote_version = PROTOCOL
        self._in_zone = False
        self._zone = 0
        self._lot = ""
        self._save = 0
        # The world our game client shows: the host makes one up for every
        # load of its zone, a guest takes the one of the state it loaded.
        self._world = ""
        self._boot = "{:08x}".format(random.getrandbits(32))
        self._loads = 0
        self._was_live = False
        self._reported_sim_id = 0
        self._remote_depth = 0
        self._seen_local = set()
        # Texts of our own notifications on their way to our game client.
        self._notices = []
        # Guest: what it is doing about being stale, the request it waits on
        # and until when.
        self._phase = IDLE
        self._request = 0
        self._requests = 0
        self._deadline = 0.0
        self._give = False
        self._offered_world = ""
        self._left = False
        # Guest: since when the host cannot be reached, and since when it is
        # in no zone, 0 while it can and is.
        self._lost_at = 0.0
        self._away_at = 0.0
        # Guest: the link went down while we loaded the host's state, and
        # the world we loaded then, to join if the host still shows it.
        self._lost_loading = False
        self._resume = ""
        # Guest: since when a save for our household runs, 0 when none does,
        # and whether a household is wanted from it once it is done.
        self._contributing = 0.0
        self._want_give = False
        # Guest: the host does not have our household at all, so all of it
        # is to be given, not only what our simulation ran.
        self._whole = False
        # Guest: our simulation is behind, as the host's ran our sims since
        # we last loaded its state; and our household was played by us
        # since the host last had it.
        self._behind = False
        self._alone = False
        # Guest: we gave up while behind, so the host's copy of our sims is
        # the one to load, not ours to give.
        self._owes = False
        # Guest: how many of the host's snapshots our game had put in place
        # when it was asked to load the latest.
        self._put_before = 0
        # Guest: the host's save we gave up getting into for good, when
        # to try again after giving up for now, and how often we have.
        self._glued = 0
        self._retry_at = 0.0
        self._failures = 0
        self._gave = 0.0
        # Guest: the gift on its way, if any.
        self._gift = 0
        # Host: the request being answered, (id, zone, lot), and the one
        # to answer once the snapshot under way is taken.
        self._saving = None
        self._asked = None
        # Host: since when a save of ours for a guest runs, 0 when none
        # does. The game finishes a save it began whatever happens to the
        # request, and two must not run at once.
        self._taking = 0.0
        # Host: until when the game is held paused for a guest that loads,
        # and when it was last paused for it.
        self._hold_until = 0.0
        self._paused_at = 0.0
        # Host: how far our game was when the hold began, and whether the
        # hold ran out with a guest perhaps still loading what we saved;
        # checked once a second.
        self._held_at = ""
        self._lapsed = False
        self._looked_at = 0.0
        # Host: sims made the other player's on our lot.
        self._welcomed = []
        self.forwarded = 0
        self.executed = 0
        self.relayed = 0
        self.delivered = 0

    # -- what the session is ----------------------------------------------

    @property
    def live(self):
        """True while the two games are joined: messages flow host -> guest
        and commands guest -> host."""
        return (self.role is not None and self.remotes > 0 and self._in_zone and self.remote_in_zone
                and self.same_zone and self.same_save and self._phase == IDLE and self.speaks_our_version
                and bool(self._world) and self._world == self.remote_world)

    @property
    def same_zone(self):
        """False when both are known to be on different lots. Lots are
        compared by what names them in any save, ids are made up by every
        new game."""
        if self._lot and self.remote_lot:
            return self._lot == self.remote_lot
        return not self._zone or not self.remote_zone or self._zone == self.remote_zone

    @property
    def same_save(self):
        """False when both are known to play different saves."""
        return not self._save or not self.remote_save or self._save == self.remote_save

    @property
    def speaks_our_version(self):
        return self._remote_version == PROTOCOL

    @property
    def apart(self):
        """True while both play, each on a lot of its own."""
        return (self.role is not None and self.remotes > 0 and self._in_zone and self.remote_in_zone
                and not (self.same_zone and self.same_save))

    @property
    def stale(self):
        return self._phase != IDLE

    @property
    def holding(self):
        """Host: the game is kept paused for a guest that loads."""
        return bool(self._hold_until)

    @property
    def _muted(self):
        """True while a stale guest is in its zone and its game client shows
        the host's world: the client is shown nothing new, as our own
        simulation is not the one it was built from. Not while it loads,
        when our simulation builds the client anew. A guest whose client
        shows its own simulation plays on with it until the host's state
        loads."""
        return self.role == GUEST and self._phase not in (IDLE, LOADING) and self._in_zone and self._behind

    # -- connection to the launcher -------------------------------------

    def bridge_up(self, role):
        self._reset(role if role in (HOST, GUEST) else None)
        self._in_zone = bool(self._game.in_zone())
        self._read_zone()
        if self.role == HOST and self._in_zone:
            self._new_world()
        self._announce(want=True)
        self._log("bridge connected, role {}".format(self.role))
        self._check()

    def bridge_down(self, reason=""):
        self._log("bridge lost: {}".format(reason))
        if self.role == GUEST and self._behind and self._in_zone and self._phase != LOADING:
            # Our game client shows the host's world, which is gone.
            self._reload()
        self._reset(None)
        self._check()

    def _reset(self, role):
        self.role = role
        self.remotes = 0
        self._forget_remote()
        self._transfers.forget()
        self._world = ""
        self._idle()
        self._behind = False
        self._alone = False
        self._owes = False
        self._glued = 0
        self._retry_at = 0.0
        self._failures = 0
        self._gave = self._clock()
        self._saving = None
        self._asked = None
        self._hold_until = 0.0
        self._lapsed = False
        self._welcomed = []
        self._lost_loading = False
        self._whole = False

    def _idle(self):
        self._phase = IDLE
        self._request = 0
        self._give = False
        self._offered_world = ""
        self._left = False
        self._lost_at = 0.0
        self._away_at = 0.0
        self._resume = ""
        self._want_give = False

    def _read_zone(self):
        self._zone = self._call("zone_id", 0) if self._in_zone else 0
        self._lot = self._call("lot", "") if self._in_zone else ""
        if self._in_zone:
            self._save = self._call("save_id", 0)

    def _call(self, name, default, *args):
        try:
            value = getattr(self._game, name)(*args)
        except Exception:
            return default
        if isinstance(default, int):
            return frames.number(value)
        return str(value or default)

    def _new_world(self):
        self._loads += 1
        self._world = "{}.{}".format(self._boot, self._loads)

    def _forget_remote(self):
        self.remote_in_zone = False
        self.remote_zone = 0
        self.remote_lot = ""
        self.remote_save = 0
        self.remote_world = ""
        self.remote_sim_id = 0
        self._remote_version = PROTOCOL
        self._reported_sim_id = 0

    def _announce(self, want=False):
        """Tell the other game our state. `want` asks it to answer with its
        own, which a game that has just (re)connected needs."""
        if self.role is None:
            return
        # A stale guest is in a zone, but not in one the host can play with.
        in_zone = self._in_zone and not self.stale
        fields = {"role": self.role, "in_zone": in_zone, "v": PROTOCOL,
                  # For the launchers to show.
                  "loaded": self._in_zone, "syncing": self.stale, "joined": self.live}
        if self._in_zone and self._zone:
            # As text: ids are 64 bits wide.
            fields["zone"] = str(self._zone)
        if self._in_zone and self._lot:
            fields["lot"] = self._lot
        if self._in_zone and self._save:
            fields["save"] = str(self._save)
        if in_zone and self._world:
            fields["world"] = self._world
        if want:
            fields["want"] = True
        self._send(frames.control("state", **fields))

    def _tell(self, text):
        """Tell the local player, and only them, past the muting of a stale
        guest. The game sends the notification with the tick's messages."""
        self._log(text)
        try:
            self._game.notify(text)
        except Exception:
            return
        self._notices = self._notices[-(MAX_NOTICES - 1):] + [text.encode("utf-8")]

    # -- what follows from what is known ----------------------------------

    def _check(self):
        if self.role == GUEST:
            self._follow()
        self._watch_live()

    def _follow(self):
        """Guest: from what both games are doing, what to do next."""
        now = self._clock()
        if self._phase != IDLE:
            self._catch_up(now)
            return
        if not (self.remotes and self._in_zone and self.remote_in_zone and self.speaks_our_version):
            return
        if not self.same_save:
            if self._glued != self.remote_save and now >= self._retry_at:
                self._go_stale("the host plays another save; bringing our household into it", give=True)
        elif not self.same_zone:
            if self._behind:
                # We were muted on the host's lot: what we have of our sims
                # is older than what the host has.
                self._go_stale("left the host's lot")
            elif self._owes:
                if now >= self._retry_at:
                    self._go_stale("loading the host's copy of our sims")
            elif not self._alone:
                self._alone = True
                self._gave = now
                self._log("the host is on another lot; playing this one by ourselves")
            else:
                gift = self._transfers.outgoing(self._gift)
                if gift is not None and now - gift.progressed > STALL_SECONDS:
                    # The host gave up on it, perhaps without a word.
                    self._transfers.drop(self._gift)
                    gift = None
                if gift is None and now - self._gave > GIVE_SECONDS:
                    self._give_household(gift=True)
        elif self._world != self.remote_world and self.remote_world and now >= self._retry_at:
            if self._alone:
                # Our copy of this lot was not played by the host, and the
                # host's copy of our household was not played by us.
                self._go_stale("on the host's lot; bringing our household over", give=True)
            else:
                self._go_stale("our game does not show the host's world; loading it")

    def _go_stale(self, why, give=False, tell=False):
        if tell:
            self._tell(why)
        else:
            self._log(why)
        self._idle()
        self._phase = WAITING
        self._give = give
        self._announce()
        self._catch_up(self._clock())

    def _catch_up(self, now):
        """Guest, stale: start the next step once the host can answer, and
        give up on a step that is taking too long."""
        if self._phase == WAITING:
            if not self.remotes:
                if self._lost_at and now - self._lost_at > LINK_SECONDS:
                    self._give_up("the host did not come back; playing on by ourselves")
                return
            if not self.remote_in_zone:
                self._away_at = self._away_at or now
                if now - self._away_at > AWAY_SECONDS:
                    self._give_up("the host is not in a game; playing on by ourselves")
                return
            self._lost_at = self._away_at = 0.0
            if not self._in_zone:
                return
            if self._resume:
                resume, self._resume = self._resume, ""
                if resume == self.remote_world and self.same_zone and self.same_save:
                    # The host still shows the world we loaded.
                    self._log("the host is back; joining it")
                    self._world = resume
                    self._idle()
                    self._announce()
                    return
            if not self.same_save:
                # The host came back in another save: ours is to be brought
                # into it first, or not at all.
                if self._glued == self.remote_save:
                    self._give_up("the host plays a save we cannot join; each plays their own", final=True)
                    return
                self._give = True
            if self._give:
                self._give_household()
            else:
                self._ask()
        elif self._phase == LOADING:
            if not self._left and now > self._deadline:
                self._give_up("the game did not load the host's state; playing on by ourselves")
        elif self._phase in (GIVING, RECEIVING):
            moving = self._transfers.outgoing(self._request) or self._transfers.incoming(self._request)
            if moving is not None:
                if now - moving.progressed > STALL_SECONDS:
                    self._give_up("the transfer stopped; playing on by ourselves")
            elif not self._deadline:
                # All of it is there; the host has a while to answer.
                self._deadline = now + MERGE_SECONDS
            elif now > self._deadline:
                self._give_up("the host did not answer; playing on by ourselves")
        elif now > self._deadline:
            self._give_up("the {} took too long; playing on by ourselves".format(
                "save" if self._phase == SAVING else "host"))

    def _next(self, phase, seconds):
        self._requests += 1
        self._request = self._requests
        self._phase = phase
        self._deadline = self._clock() + seconds
        return self._request

    def _give_up(self, why, final=False):
        """Guest: stop being stale without the host's state. Our simulation
        is the one that counts again; its game client is made to show it
        when it showed the host's."""
        self._tell(why)
        request = self._request
        self._idle()
        self._transfers.drop(request)
        self._world = ""
        # Behind, the host's copy of our household is newer than ours, and
        # it is the one to keep.
        self._owes = self._owes or self._behind
        self._alone = not self._owes
        if final:
            self._glued = self.remote_save
        else:
            self._retry_at = self._clock() + RETRY_SECONDS[min(self._failures, len(RETRY_SECONDS) - 1)]
            self._failures += 1
        if self._behind and self._in_zone:
            self._behind = False
            self._reload()
        self._announce()

    def _reload(self):
        try:
            self._game.reload()
        except Exception as error:
            self._log("could not load our own zone again: {}".format(error))

    # -- a guest giving its household to the host ------------------------

    def _give_household(self, gift=False):
        """Save, then send the household we play. A gift is what a guest
        playing by itself gives now and then; it waits for nothing."""
        now = self._clock()
        if self._contributing and now - self._contributing < SAVE_SECONDS:
            # One save at a time: the one that runs is newer than what
            # came before, and is taken once it is done.
            if not gift:
                self._next(SAVING, SAVE_SECONDS)
                self._want_give = True
                self._log("giving our household to the host once the save under way is done")
            return
        if gift:
            self._requests += 1
            request = self._requests
            self._gave = now
        else:
            request = self._next(SAVING, SAVE_SECONDS)
            self._log("giving our household to the host")
        self._contributing = now
        try:
            self._game.contribute(lambda payload: self._saved(request, gift, payload), self._whole)
        except Exception as error:
            self._log("could not save our household: {}".format(error))
            self._saved(request, gift, None)

    def _saved(self, request, gift, payload):
        self._contributing = 0.0
        if self.role != GUEST:
            return
        if self._want_give:
            self._want_give = False
            request, gift = self._request, False
        if gift:
            if payload is not None and self.remotes and self.apart and self._phase == IDLE:
                self._gift = request
                self._transfers.send("household", request, payload, save=str(self._save))
            return
        if self._phase != SAVING or request != self._request:
            return
        if payload is None:
            if self.same_save:
                self._log("could not save our household; loading the host's state as it is")
                self._ask()
            else:
                self._give_up("could not save our household; each plays their own save")
        elif not self.remotes:
            self._phase = WAITING
        else:
            self._phase = GIVING
            self._deadline = 0.0
            self._transfers.send("household", request, payload, save=str(self._save))

    def _merged(self, message):
        request = frames.number(message.get("id"))
        if self.role != GUEST or self._phase != GIVING or request != self._request:
            return
        if message.get("ok"):
            self._alone = False
            self._log("the host has our household ({})".format(message.get("outcome") or "taken in"))
            self._ask()
        elif self.same_save:
            self._log("the host did not take our household; loading its state as it is")
            self._ask()
        else:
            self._give_up("the host did not take our household; each plays their own save",
                          final=bool(message.get("final")))

    # -- a guest loading the host's state -------------------------------

    def _ask(self):
        request = self._next(ASKING, SAVE_SECONDS)
        self._log("asking the host for its state")
        fields = {"id": request}
        if self._zone:
            fields["zone"] = str(self._zone)
        if self._lot:
            fields["lot"] = self._lot
        household = self._call("household_id", 0)
        if household:
            fields["household"] = str(household)
        self._send(frames.control("resync", **fields))

    def _offered(self, incoming):
        """The host offers its state."""
        if self.role != GUEST or self._phase != ASKING or incoming.ident != self._request:
            self._transfers.refuse(incoming.ident)
            return
        self._phase = RECEIVING
        self._deadline = 0.0
        self._offered_world = str(incoming.fields.get("world") or "")
        self._log("the host's state is coming ({} bytes)".format(incoming.size))
        done = self._transfers.accept(incoming)
        if done is not None:
            self._adopt(done.payload)

    def _adopt(self, payload):
        if self.role != GUEST or self._phase != RECEIVING or not self._in_zone:
            return
        self._put_before = self._call("adoptions", 0)
        try:
            self._game.adopt(payload)
        except Exception as error:
            self._give_up("could not load the host's state ({}); playing on by ourselves".format(error))
            return
        self._phase = LOADING
        self._left = False
        self._lost_loading = False
        self._deadline = self._clock() + RELOAD_SECONDS

    def _loaded(self):
        """Back in a zone after loading the host's state: our game client
        shows the host's world and our simulation is as the host's was."""
        if self._call("adoptions", 0) <= self._put_before:
            # The game loaded the zone, but from our own save.
            self._give_up("the game loaded our own state, not the host's; playing on by ourselves")
            return
        self._log("loaded the host's state")
        world, lost = self._offered_world, self._lost_loading
        self._idle()
        self._lost_loading = False
        self._alone = False
        self._owes = False
        self._whole = False
        self._failures = 0
        self._retry_at = 0.0
        if lost and not self.remotes:
            # Our game client shows the host's world, which the host may
            # still show once it is back.
            self._tell("loaded the host's state, but the connection is down; waiting for it")
            self._behind = True
            self._phase = WAITING
            self._lost_at = self._clock()
            self._resume = world
        else:
            self._world = world
            self._behind = False

    def resync(self):
        """Guest: load the host's state again, as after a reload of its
        zone. Returns False when there is nobody to load it from."""
        if self.role != GUEST or self.stale or not (self.live or self.apart):
            return False
        # Apart, we are the one who knows how our household is.
        self._go_stale("reloading from the host on request", give=not self.live)
        self._watch_live()
        return True

    # -- the host answering a guest -------------------------------------

    def _resync(self, message):
        request = (frames.number(message.get("id")), frames.number(message.get("zone")), str(message.get("lot") or ""))
        if self.role != HOST:
            return
        if not self._in_zone:
            self._refuse_resync(request[0])
            return
        household = frames.number(message.get("household"))
        if household and not self._call("has_household", 1, household):
            # Our game was started again from a save without it.
            self._log("the other player's household is not in our save; asking for it")
            self._send(frames.control("resync_failed", id=request[0], missing=True))
            return
        if self._on_our_lot(request[1], request[2]):
            self._hold()
        self._asked = request
        if not self._taking or self._clock() - self._taking > SAVE_SECONDS:
            self._take()

    def _on_our_lot(self, zone, lot):
        """Whether a guest that asks for the state of that zone and lot will
        be on ours, and join."""
        if lot and self._lot:
            return lot == self._lot
        return not zone or not self._zone or zone == self._zone

    def _take(self):
        request, self._asked = self._asked, None
        self._saving = request
        self._taking = self._clock()
        world = self._world
        self._log("the other player is loading; saving for them")
        try:
            self._game.snapshot(lambda payload: self._taken(request, world, payload), request[1], request[2])
        except Exception as error:
            self._log("could not save for the other player: {}".format(error))
            self._taken(request, world, None)

    def _taken(self, request, world, payload):
        self._taking = 0.0
        current, self._saving = self._saving, None
        if self.role != HOST:
            return
        if self._asked is not None:
            # Asked again meanwhile: what was saved may be older than what
            # the guest asks after, as when its household came in between.
            if self._in_zone:
                self._take()
            else:
                self._refuse_resync(self._asked[0])
                self._asked = None
            return
        if current is not request or not self.remotes:
            # Asked for by a guest that has gone since.
            return
        if payload is None or world != self._world:
            self._refuse_resync(request[0])
        else:
            self._transfers.send("state", request[0], payload, world=world)

    def _refuse_resync(self, request):
        """The guest gives up on this answer; it holds us again if it asks
        again."""
        self._send(frames.control("resync_failed", id=request))
        self._hold_until = 0.0

    def _hold(self):
        if not self._hold_until:
            self._tell("the other player is loading; the game stays paused until they are in")
            self._held_at = self._call("progress", "")
        self._lapsed = False
        self._hold_until = self._clock() + HOLD_SECONDS
        self._pause()

    def _pause(self):
        self._paused_at = self._clock()
        try:
            self._game.pause()
        except Exception as error:
            self._log("could not pause for the other player: {}".format(error))

    def _keep_holding(self, now):
        self._watch_lapse(now)
        if not self._hold_until:
            return
        if self.live or not self._in_zone or self.remote_in_zone:
            # In, or gone from our zone, or playing by itself: a guest that
            # is loading says it is not in a zone.
            self._hold_until = 0.0
            self._log("the game is no longer held for the other player")
        elif now > self._hold_until:
            self._hold_until = 0.0
            self._lapsed = True
            self._log("the other player did not get in; the game is no longer held for them")
        elif now - self._paused_at > PAUSE_SECONDS:
            self._pause()

    def _watch_lapse(self, now):
        """After a hold ran out: once our game has moved on from where the
        hold began, a guest still loading what we saved would join on a
        world behind ours, so it is another world."""
        if not self._lapsed or now - self._looked_at < PAUSE_SECONDS:
            return
        self._looked_at = now
        if self.live or not self._in_zone:
            self._lapsed = False
            return
        progress = self._call("progress", "")
        if not progress or progress != self._held_at:
            self._lapsed = False
            self._log("our game moved on since the other player's state was saved")
            self._new_world()
            self._announce()

    def _merge(self, incoming):
        if self.role != HOST:
            return
        request = incoming.ident
        try:
            if not self._in_zone:
                raise ValueError("no zone is loaded")
            outcome = self._game.merge(incoming.payload, frames.number(incoming.fields.get("save")))
        except Exception as error:
            final = bool(getattr(error, "final", False))
            self._log("could not take the other player's household: {}".format(error))
            self._send(frames.control("merged", id=request, ok=False, final=final))
        else:
            self._send(frames.control("merged", id=request, ok=True, outcome=str(outcome or "")))

    def _welcome(self):
        if self.role != HOST or not self.remote_sim_id or not self._in_zone:
            return
        try:
            theirs = self._game.welcome(self.remote_sim_id, tuple(self._welcomed))
        except Exception as error:
            self._log("could not bring the other player's sim in: {}".format(error))
            return
        if not theirs:
            self._log("the other player's sim {} is not theirs to play here".format(self.remote_sim_id))
            return
        for sim_id in theirs:
            if sim_id not in self._welcomed:
                self._welcomed.append(sim_id)

    def _send_off(self):
        """Host: the guest took its sims to another lot. Left here they would
        go on being simulated, and our next save would undo what the guest
        does with them there."""
        sims, self._welcomed = self._welcomed, []
        if sims:
            try:
                self._game.send_off(sims)
            except Exception as error:
                self._log("could not send the other player's sims off: {}".format(error))

    # -- once per simulation tick ---------------------------------------

    def tick(self):
        if self.role is None:
            return
        in_zone = bool(self._game.in_zone())
        zone = self._call("zone_id", 0) if in_zone else 0
        if in_zone != self._in_zone or zone != self._zone:
            self._in_zone = in_zone
            self._read_zone()
            # The host forgets our sim when we leave a zone.
            self._reported_sim_id = 0
            self._zone_changed()
            self._announce()
        self._transfers.pump()
        if self.role == HOST:
            # A guest gives up on these after as long, and says nothing if
            # its link went down meanwhile.
            self._transfers.expire(STALL_SECONDS)
        self._keep_holding(self._clock())
        self._check()
        if self.role == GUEST and self.live:
            self._report_sim()

    def _zone_changed(self):
        if self.role == HOST:
            self._world = ""
            if self._in_zone:
                self._new_world()
            return
        if self._phase == LOADING:
            if not self._in_zone:
                self._left = True
            elif self._left:
                self._loaded()
            return
        # Whatever our game client showed, it now shows our own simulation.
        self._world = ""
        if self._phase != IDLE and not self._in_zone:
            self._give_up("left the zone before the host's state arrived")
        elif self._phase == IDLE and self._in_zone:
            # The player loaded something by hand. If that is a save of
            # their own again, its household is brought over again.
            self._glued = 0

    # -- the session going live and back -------------------------------

    def _watch_live(self):
        live = self.live
        if live == self._was_live:
            return
        self._was_live = live
        self._log("session is live as {}".format(self.role) if live else "session is not live")
        self._announce()
        if not live:
            return
        self._keep_holding(self._clock())
        if self.role == GUEST:
            self._behind = True
            self._report_sim()
            return
        # The guest's game client comes out of a loading screen paused and
        # only hears of a speed when it changes. Had we kept running, it
        # would show a pause that pressing play, which changes nothing here,
        # never lifts. Paused, both agree, and either player resumes.
        self._pause()
        if self.remote_sim_id:
            self._joined()

    def _joined(self):
        try:
            self._game.joined(self.remote_sim_id)
        except Exception as error:
            self._log("could not note the other player's household: {}".format(error))

    def _report_sim(self):
        sim_id = self._game.active_sim_id() or 0
        if sim_id != self._reported_sim_id:
            self._reported_sim_id = sim_id
            self._send(frames.control("active_sim", sim_id=str(sim_id)))

    def _lost(self, why):
        """The other game cannot be reached, for now or for good."""
        self._transfers.forget()
        was_live = self._was_live
        self._forget_remote()
        if self.role == HOST:
            self._saving = None
            self._asked = None
            if self._hold_until:
                self._hold_until = min(self._hold_until, self._clock() + LINK_SECONDS)
            return
        if self.role != GUEST:
            return
        if self._phase == LOADING:
            # What is being loaded is all we need from the host; whether
            # it is still the host's world is seen once both are back.
            self._lost_loading = True
            return
        if was_live or self._phase != IDLE:
            # What the host sends meanwhile is lost, so our game client will
            # not show the host's world any more.
            self._tell("lost the connection to the host ({}); waiting for it".format(why))
            self._world = ""
            give, resume = self._give, self._resume
            self._idle()
            self._phase = WAITING
            self._give = give
            self._resume = resume
            self._lost_at = self._clock()
            self._announce()

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
        elif kind == frames.PART:
            done = self._transfers.part(ident, payload)
            if done is not None:
                self._finished(done)
            self._check()

    def _finished(self, incoming):
        if incoming.what == "state":
            self._adopt(incoming.payload)
        elif incoming.what == "household":
            self._merge(incoming)

    def _seen(self, message):
        """Host: the guest that is loading still takes our state."""
        outgoing = self._transfers.outgoing(frames.number(message.get("id")))
        if self._hold_until and outgoing is not None and outgoing.what == "state":
            self._hold_until = max(self._hold_until, self._clock() + HOLD_SECONDS)

    def _control(self, message):
        kind = message.get("type")
        if kind == "link":
            remotes = frames.number(message.get("remotes"))
            self.remotes = remotes
            if remotes == 0:
                self._lost("the link is down")
            else:
                # Whoever just connected has not heard our state yet, nor we
                # theirs, and knows nothing of what was under way.
                self._transfers.forget()
                self._announce(want=True)
        elif kind == "gone":
            self._lost("the other game is gone")
        elif kind == "state":
            self._state(message)
        elif kind in ("offer", "got", "drop"):
            self._seen(message)
            offer = self._transfers.receive(message)
            if offer is None:
                pass
            elif offer.what == "state":
                self._offered(offer)
            elif offer.what == "household" and self.role == HOST:
                done = self._transfers.accept(offer)
                if done is not None:
                    self._finished(done)
            else:
                self._transfers.refuse(offer.ident)
        elif kind == "resync":
            self._resync(message)
        elif kind == "resync_failed":
            if self.role == GUEST and self._phase == ASKING and frames.number(message.get("id")) == self._request:
                if message.get("missing"):
                    # The host's game started again from a save without our
                    # household: all of it is given again, however old.
                    self._whole = True
                    self._go_stale("the host does not have our household; giving it again", give=True)
                else:
                    self._give_up("the host could not save; playing on by ourselves")
        elif kind == "merged":
            self._merged(message)
        elif kind == "tell":
            if self.role == GUEST:
                self._tell(str(message.get("text") or "")[:200])
        elif kind == "active_sim":
            self.remote_sim_id = frames.number(message.get("sim_id"))
            self._welcome()
            if self.live and self.remote_sim_id:
                self._joined()
        self._check()

    def _state(self, message):
        if message.get("role") == self.role:
            self._log("the other game is also a {}; ignoring it".format(self.role))
            return
        if message.get("want"):
            self._announce()
        version = frames.number(message.get("v"))
        if version != self._remote_version:
            self._remote_version = version
            if version != PROTOCOL:
                self._log("the other game runs another version of the mod ({}, ours {}); not joining".format(
                    version, PROTOCOL))
        was_live = self.live
        was_zone, was_lot = self.remote_zone, self.remote_lot
        self.remote_in_zone = bool(message.get("in_zone"))
        self.remote_zone = frames.number(message.get("zone"))
        self.remote_lot = str(message.get("lot") or "")
        self.remote_save = frames.number(message.get("save"))
        self.remote_world = str(message.get("world") or "")
        if self.role == GUEST and was_live and not self.remote_in_zone:
            # Its zone will be built again, and its game client with it.
            self._go_stale("the host left its lot; waiting for it", tell=True)
        if not self.remote_in_zone:
            self.remote_sim_id = 0
            self._reported_sim_id = 0
        elif self.role == HOST and not self.same_zone and (self.remote_zone, self.remote_lot) != (was_zone, was_lot):
            self._send_off()

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
        consts = self._consts
        if self._notices and msg_id == consts.notice:
            for text in self._notices:
                if text in data:
                    self._notices.remove(text)
                    return data
        if not (self.live or self._muted):
            return data
        if self.role == HOST:
            if msg_id in consts.owned:
                owner = wire.first_varint(data, consts.owned[msg_id])
                if owner and self._guests(owner):
                    self._relay(msg_id, data)
                    return None
                if owner and msg_id not in consts.shared:
                    return data
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

    def _guests(self, sim_id):
        """Whether `sim_id` is played by the guest: the sim it controls, or
        one of that sim's household when the host plays another household."""
        theirs = self.remote_sim_id
        if not theirs:
            return False
        if sim_id == theirs:
            return True
        ours = self._game.active_sim_id()
        if sim_id == ours:
            return False
        try:
            same = self._game.same_household
            return same(sim_id, theirs) and not same(theirs, ours)
        except Exception:
            return False

    def client_cheat(self, line):
        """Host: the simulation passes `line` on to our game client, as it
        does to open Create-a-Sim. Returns False to keep it from ours when it
        names a sim the guest plays: it would take our player's screen, and
        the guest's own client cannot edit a sim of the host's simulation."""
        if self.role != HOST or not self.live:
            return True
        for word in line.split():
            sim_id = frames.number(word)
            if sim_id and self._guests(sim_id):
                self._log("kept {!r} from our game client: it is for the other player's sim".format(line))
                self._send(frames.control("tell", text="Create-a-Sim and other screens of the host's game "
                                                       "cannot be opened for your sim yet"))
                return False
        return True

    def _relay(self, msg_id, data):
        self.relayed += 1
        self._send(frames.pack(frames.MSG, msg_id, data))

    def command(self, name, args):
        """The local game client issued a command. Returns False if it must
        not run locally."""
        if self.role == HOST and self._hold_until and name in CLOCK_NAMES:
            self._log("{} refused: the other player is loading".format(name))
            return False
        if self._muted:
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


def _forwarded(name):
    return name in FORWARD_NAMES or name.startswith(FORWARD_PREFIXES)
