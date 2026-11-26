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

A host keeps what it knows of each guest in a `peer.Peer` of its own, under
the number its launcher gave that guest; a guest has one for the host. Each
guest is joined to the host or not by itself: the host's stream goes to the
guests that are joined, what is for one guest (a reply to its command, a
screen of its sim, its snapshot) goes to it alone (see `Session.send`), and
its commands run as its own sim. A guest knows nothing of the other guests;
the host tells it what it needs to, such as that the game is held for one of
them.

So that one guest that is slow to load never sends the others back to
loading, the world a guest is to show is the host's zone load and, after the
host's game moved on from a state saved for that guest alone, a number of
that guest's own (see `Session._world_for`). Only a load of the host's zone
makes every guest load again. The game stays paused while any guest loads,
as there is one clock, and one save answers every guest that waits for one.

Pure logic with no game imports: the game is reached through the `game`
adapter (see live.Game) so this can be tested outside the game.
"""

import base64
import random
import time

from tsmp import frames, wire
from tsmp.peer import LEGACY, Peer

HOST = "host"
GUEST = "guest"
# What the two mods say to each other. Games that speak another version are
# not joined; the launcher normally makes sure both run the same mod. From 3
# a host's launcher may number its guests (see tsmp/frames.py, ROUTED).
PROTOCOL = 3
# Controls of the launcher's own, about its remotes rather than from one:
# `link {remotes}` from every launcher, and from a host's launcher of version
# 3 also `peer_up {peer, name, player}` and `peer_gone {peer}`.
LINK_CONTROLS = frozenset(["link", "peer_up", "peer_gone"])

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
# Commands that open Create-a-Sim, and the line the simulation passes on to
# its game client to do so (`sims.exit2cas <sim> <household> <active sim>`).
CAS_PREFIXES = ("sims.modify_in_cas", "sims.modify_outfit_in_cas", "cas.")
CAS_SCREEN = "sims.exit2cas"
CAS_REFUSED = "Create-a-Sim cannot be opened while the other player's game is being loaded"
# The command that brackets build mode: `1` on entering it, `0` on leaving.
BUILD_COMMAND = "clock.build_buy_pause_unpause"
# A guest holding back a travel to the host's lot waits this long for a host
# that left it or cannot be reached, then travels as ordered.
TRAVEL_WAIT_SECONDS = 30.0
# A guest that was let into Create-a-Sim and has not left its zone within
# this many seconds did not go.
CAS_SECONDS = 30.0
BOTH_BUILDING = ("both of you are in build mode: build on different parts of the lot, as one wall edited "
                 "by both at the same moment may end up differently in the two games")

# Seconds, not ticks: over the internet most of the waiting is for the
# network, and a game that is loading does not tick at all.
#
# A save, the host's for a snapshot or the guest's for its household.
SAVE_SECONDS = 180.0
# A transfer that has not moved for this long is given up.
STALL_SECONDS = 45.0
# The host to answer for a household it has all of, or to say again that it
# is still to come (`merging`).
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
# The outcome of a gift older than one another guest gave since (see
# household.OLDER): ours is kept, and the giver loads it.
GIFT_OLDER = "older"
# Kept as ours too (household.KEPT): what a guest gives from a lot apart
# that another guest came to first.
GIFT_KEPT = "kept"
# Host: how many of the states last sent to a guest, and of the lots apart
# it came to since, are remembered.
REMEMBERED = 8

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
                 owned=None, shared=(), notice=None, travel=None):
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
        # The message that orders the game client to travel to a lot.
        self.travel = travel


class Session:
    """`game` provides:
        in_zone()                      True while a zone is loaded and running
        zone_id()                      id of that zone, as the host names it
        lot()                          what names its lot in any save, or ""
        active_sim_id()                id of the sim the local player
                                       selected, or 0
        deliver(msg_id, data)          hand a message to the local game client
        execute(name, args, sim_id, peer)
                                       run a command as the remote player
                                       numbered `peer`
        forget_peer(peer)              host: that guest's link is down; what
                                       the game keeps for its commands goes
        snapshot(done, places)         host: save once, then done(a list with
                                       bytes or None for each (zone id, lot,
                                       player) of `places`, a snapshot for a
                                       guest there), or done(None)
        adopt(payload, travellers)     guest: load the host's snapshot by
                                       reloading the zone, or travelling with
                                       those sims; raises on failure
        adoptions()                    guest: how many snapshots the game has
                                       loaded so far
        reload()                       guest: load our own zone again, so that
                                       its game client shows our simulation
        save_id()                      what tells one save from another, or 0
        household_id()                 guest: the household we play, or 0
        has_household(household_id)    host: whether our save has it
        playing_adopted()              guest: whether this game loaded the
                                       host's state and plays it
        snapshot_zone(zone_id, lot, player)
                                       host: the zone of ours a snapshot for
                                       that player's guest there would be
                                       of, or 0
        host_zone(zone_id)             guest: the host's id for a zone of ours
        lot_of(zone_id)                what names that zone's lot, or ""
        loaded_by_hand()               the zone just loaded is not the end of
                                       a travel but of a load from the menu;
                                       a guest's forgets what came from the
                                       host
        forget_gifts()                 host: what guests gave is no longer in
                                       our game (see household.Gifts)
        progress()                     host: text that changes whenever the
                                       game moves on (its clock, its
                                       objects), "" when it cannot tell
        contribute(done, whole)        guest: save, then done(bytes or None)
                                       with the household it plays: all of
                                       it, or what our simulation ran
        merge(payload, save_id, player, version, others)
                                       host: take that household, from that
                                       save, of that player, into ours (see
                                       household.take_in); raises on failure,
                                       an error with `final` set when trying
                                       again would not help
        same_household(a, b)           True if both sim ids are in one household
        pause()                        host: pause the game
        welcome(sim_id, before, travellers, claimed)
                                       host: have the other player's sim on
                                       the lot and controllable, but none of
                                       the sims `claimed` by other players;
                                       returns the ids now theirs, none to
                                       refuse it
        send_off(sim_ids)              host: the other player left with these
        joined(sim_id)                 host: the other player plays it here
        notify(text)                   tell the local player something
        appearance(sim_id)             bytes of how that sim looks now, b""
        look_changes(before, after)    (look, field names) of what changed
        apply_appearance(sim_id, look, names)
                                       host: give our sim those fields of it
        leaving_zone()                 our game saves to leave its zone
        client_cheat(line)             guest: pass a line to our game client
    `send(frame)` queues a packed frame for the launcher; `clock()` tells the
    time in seconds.

    The host keeps a `peer.Peer` for each guest in `peers`; a guest keeps one
    for the host. The `remote_*` attributes are those of the guest's host,
    and on the host those of its first guest, for whoever just looks; what
    the host does goes by each guest's own.
    """

    def __init__(self, game, consts, send, log=None, clock=time.monotonic):
        self._game = game
        self._consts = consts
        self._send = send
        self._log = log or (lambda text: None)
        self._clock = clock
        self.role = None
        # How many remotes our launcher has: on the host, the guests whose
        # links are up.
        self.remotes = 0
        # Guest: the host. Host: each guest by its peer number, kept while
        # its link is down, as it may come back.
        self._host = Peer(LEGACY, self.send, clock)
        self.peers = {}
        # Host: our launcher numbers its remotes, and unwraps ROUTED frames.
        self._routed = False
        # Host: the guest whose command runs, which the replies are for.
        self._executing = None
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
        self._remote_depth = 0
        self._seen_local = set()
        # What we last told the other games of ourselves, and the host
        # which world each guest is to show.
        self._announced = None
        self._announced_worlds = ()
        # Our player is in build mode, as the other game was last told.
        self._building = False
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
        # is to be given, not only what our simulation ran; and the host's
        # world when it took all of it in.
        self._whole = False
        self._whole_world = ""
        # Guest: we are loading our own zone again.
        self._reloading = False
        # Guest: Create-a-Sim was opened for one of our sims while joined:
        # (sim id, how it looked, when, in which save); and what changed,
        # (sim id, look, field names), until the host says it has it.
        self._cas = None
        self._look = None
        self._look_sent = False
        # Guest: the travel to the host's lot our game ordered, held back
        # until the host's state is here: (message, zone of ours, the host's
        # name for it, its lot, the travellers).
        self._travel = None
        # Guest: whether what we sent under a request held all of the
        # household.
        self._sent_whole = {}
        # Guest: our simulation is behind, as the host's ran our sims since
        # we last loaded its state; and our household was played by us
        # since the host last had it.
        self._behind = False
        self._alone = False
        # Guest: our game client shows the host's world, not our own
        # simulation, which it was not built from.
        self._shows_host = False
        # Guest: the state we asked for is the one of the host's own lot,
        # which we will join, and the host's world when we asked.
        self._joining = False
        self._asked_world = ""
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
        # Guest: the request whose state of the host's our game last loaded,
        # 0 when it plays none: what we give is as new as that state.
        self._loaded_request = 0
        # Host: since when a save of ours for guests runs, 0 when none
        # does. The game finishes a save it began whatever happens to the
        # requests, and two must not run at once: guests that ask meanwhile
        # are all answered by the next one.
        self._taking = 0.0
        # Host: how many saves for guests were begun; only the latest one
        # answers. Who the one under way is for: ([(guest, request, world
        # it is to show)], [(zone, lot) of each], how far our game was when
        # it began, `_changes` then), None while none runs.
        self._takes = 0
        self._batch = None
        # Host: households and looks our game took in from guests, so far;
        # a save begun before one is older than what was given.
        self._changes = 0
        # Host: households given that wait while we save or hold for
        # another guest, in the order they came: [guest, the transfer, when
        # it was last told to wait].
        self._gifts = []
        # Host: how many times a guest came to a lot apart from ours, which
        # tells who was on a lot first.
        self._arrivals = 0
        # Host: when the game was last paused for a guest that loads.
        self._paused_at = 0.0
        self.forwarded = 0
        self.executed = 0
        self.relayed = 0
        self.delivered = 0

    # -- what the session is ----------------------------------------------

    @property
    def live(self):
        """True while the two games are joined: messages flow host -> guest
        and commands guest -> host. On the host, with any guest."""
        return any(self._live_with(peer) for peer in self._all_peers())

    def _live_with(self, peer):
        return (self.role is not None and peer.up and self._in_zone and peer.in_zone
                and self._same_zone(peer) and self._same_save(peer) and self._phase == IDLE
                and self._speaks(peer) and bool(self._world) and self._world_for(peer) == peer.world)

    def _world_for(self, peer):
        """The world the other game is to show to be joined. A guest's host
        shows one. A host's is the load of its zone, and for a guest whose
        state it saved and then moved on from before the guest got in, that
        load and how often that happened (see `_bump`): that guest loads
        again, the others, who got in on time, play on."""
        if self.role != HOST or not self._world or not peer.epoch:
            return self._world
        return "{}.{}".format(self._world, peer.epoch)

    def _bump(self, peer):
        """Host: the state of ours the guest loads, or is loading, is behind
        our game: it is to show another world before it joins, and the other
        guests stay as they are."""
        peer.epoch += 1
        self._announce()

    def _joined_peers(self):
        """Host: the guests joined to us."""
        return [peer for _, peer in sorted(self.peers.items()) if self._live_with(peer)]

    @property
    def same_zone(self):
        return self._same_zone(self._remote)

    def _same_zone(self, peer):
        """False when both are known to be on different lots. Lots are
        compared by what names them in any save, ids are made up by every
        new game."""
        if self._lot and peer.lot:
            return self._lot == peer.lot
        return not self._zone or not peer.zone or self._zone == peer.zone

    @property
    def same_save(self):
        return self._same_save(self._remote)

    def _same_save(self, peer):
        """False when both are known to play different saves."""
        return not self._save or not peer.save or self._save == peer.save

    @property
    def speaks_our_version(self):
        return self._speaks(self._remote)

    @staticmethod
    def _speaks(peer):
        return peer.version is None or peer.version == PROTOCOL

    @property
    def apart(self):
        """True while both play, each on a lot of its own. On the host, with
        any guest."""
        return any(self._apart_with(peer) for peer in self._all_peers())

    def _apart_with(self, peer):
        return (self.role is not None and peer.up and self._in_zone and peer.in_zone
                and not (self._same_zone(peer) and self._same_save(peer)))

    @property
    def stale(self):
        return self._phase != IDLE

    @property
    def holding(self):
        """Host: the game is kept paused for a guest that loads."""
        return any(peer.hold_until for peer in self.peers.values())

    def _held_for(self, but=None):
        """Host: the guests the game is held for, other than `but`."""
        return [peer for _, peer in sorted(self.peers.items()) if peer.hold_until and peer is not but]

    def _called(self, peer):
        """What our player is told a guest is: by name once there are
        several."""
        if self.role == HOST and len(self.peers) > 1 and peer.label:
            return peer.label
        return "the other player"

    def _log_for(self, peer, text):
        """A line of our log about one guest, which names it on a host."""
        self._log("{}: {}".format(peer.label, text) if self.role == HOST and peer.label else text)

    def label(self, ident):
        """Host: what the log calls the guest numbered `ident`, "" when it
        cannot tell."""
        peer = self.peers.get(ident) if self.role == HOST and ident is not None else None
        return peer.label if peer is not None else ""

    def describe_peers(self):
        """Host: a line on each guest, for the tsmp.status cheat."""
        lines = []
        for ident, peer in sorted(self.peers.items()):
            if self._live_with(peer):
                doing = "joined"
            elif not peer.up:
                doing = "not connected"
            elif not peer.in_zone:
                doing = "loading" if peer.hold_until else "not in game"
            elif self._apart_with(peer):
                doing = "apart"
            elif not self._speaks(peer):
                doing = "another version ({})".format(peer.version)
            else:
                doing = "getting in step"
            lines.append("guest {}{}: {}, sim {}, world {}{}{}".format(
                ident, " ({})".format(peer.name) if peer.name else "", doing, peer.sim_id or "none",
                peer.world or "-", " (to show {})".format(self._world_for(peer))
                if peer.world != self._world_for(peer) else "",
                ", held for" if peer.hold_until else ""))
        return lines

    # -- the other games -------------------------------------------------

    def _all_peers(self):
        """The other games we know of: the host's guests, or the guest's host."""
        return list(self.peers.values()) if self.role == HOST else [self._host]

    @property
    def _remote(self):
        """The guest's host. On the host its one guest: the first whose link
        is up, else the first at all, else nobody."""
        if self.role != HOST:
            return self._host
        ordered = sorted(self.peers.items())
        for _, peer in ordered:
            if peer.up:
                return peer
        return ordered[0][1] if ordered else Peer(LEGACY, lambda frame, to: None, self._clock)

    def _peer(self, ident):
        """Host: the guest numbered `ident`, known from now on."""
        peer = self.peers.get(ident)
        if peer is not None:
            return peer
        if len(self.peers) == 1 and (ident == LEGACY or LEGACY in self.peers):
            # The one remote, named by a launcher that numbers its remotes
            # where one before named none, or the other way round: still the
            # same game, keeping what we had of it.
            (peer,) = self.peers.values()
            del self.peers[peer.ident]
            peer.ident = ident
        else:
            peer = Peer(ident, self.send, self._clock)
        self.peers[ident] = peer
        return peer

    def _from(self, ident):
        """The peer a frame came from: `ident` from a ROUTED frame, None for
        a plain one. A host whose launcher names its remotes may still get
        plain frames from one: those that crossed before the launcher heard
        that we speak version 3. None when it cannot be told whose it is."""
        if self.role != HOST:
            return self._host
        if ident is not None:
            self._routed = True
            return self._peer(ident)
        if LEGACY in self.peers:
            return self.peers[LEGACY]
        if len(self.peers) <= 1:
            return self._peer(next(iter(self.peers)) if self.peers else LEGACY)
        return None

    def send(self, frame, to=None):
        """Queue a packed frame for the launcher: for every other game, or,
        on the host, for the guest numbered `to` alone."""
        if to and self._routed:
            frame = frames.route(to, frame)
        self._send(frame)

    def _to(self, peer, frame):
        self.send(frame, to=peer.ident)

    @property
    def _muted(self):
        """True while a stale guest is in its zone and its game client shows
        the host's world: the client is shown nothing new, as our own
        simulation is not the one it was built from. Not while it loads,
        when our simulation builds the client anew. A guest whose client
        shows its own simulation plays on with it until the host's state
        loads."""
        return self.role == GUEST and self._phase not in (IDLE, LOADING) and self._in_zone and self._shows_host

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
        if self.role == GUEST and self._shows_host and self._in_zone and self._phase != LOADING:
            # Our game client shows the host's world, which is gone.
            self._reload()
        if self.role == GUEST:
            self._let_travel_go()
        # What our game has of our sims is behind the host's whatever the
        # launcher does: it loads the host's copy before it gives any.
        self._owes = self._owes or (self.role == GUEST and self._behind)
        self._reset(None)
        self._check()

    def _reset(self, role):
        self.role = role
        self.remotes = 0
        # A launcher that comes up anew may be another one, older or newer.
        self._routed = False
        self._host.reset()
        for peer in self.peers.values():
            peer.reset()
        # Gifts that came through the old launcher: its guests are not
        # known to be the ones that number now, and give again. The game
        # finishes a save it began (`_taking`); its answers find nobody.
        self._gifts = []
        self._batch = None
        self._world = ""
        self._idle()
        self._behind = False
        self._alone = False
        self._shows_host = False
        self._glued = 0
        self._retry_at = 0.0
        self._failures = 0
        self._gave = self._clock()
        self._lost_loading = False
        self._whole = False
        self._travel = None

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
        # Every guest is to load this one, and none is behind it yet.
        for peer in self.peers.values():
            peer.epoch = 0

    def _announce(self, want=False, to=None):
        """Tell the other games our state. `want` asks them to answer with
        their own, which a game that has just (re)connected needs. To every
        guest, as our launcher reads what it shows of our game from it, but
        for an answer to `to` alone when nothing changed since the last. One
        frame for all while all are to show one world, else one for each,
        with its own (see `_world_for`)."""
        if self.role is None:
            return
        fields = self._state_fields()
        worlds = self._worlds()
        if to is not None and (fields, worlds) == (self._announced, self._announced_worlds):
            self._to(to, self._state_frame(fields, to, want))
            return
        self._announced, self._announced_worlds = dict(fields), worlds
        apart = self._worlds_apart()
        if not apart:
            if want:
                fields["want"] = True
            self.send(frames.control("state", **fields))
            return
        for peer in apart:
            self._to(peer, self._state_frame(fields, peer, want and to in (None, peer)))

    def _state_frame(self, fields, peer, want):
        fields = dict(fields)
        if "world" in fields:
            fields["world"] = self._world_for(peer)
        if want:
            fields["want"] = True
        return frames.control("state", **fields)

    def _worlds_apart(self):
        """Host: the guests whose links are up when they are not all to show
        the same world, none when they are."""
        if self.role != HOST:
            return []
        up = [peer for _, peer in sorted(self.peers.items()) if peer.up]
        return up if any(peer.epoch for peer in up) else []

    def _worlds(self):
        """Host: the world each guest whose link is up is told to show."""
        if self.role != HOST:
            return ()
        return tuple((ident, self._world_for(peer)) for ident, peer in sorted(self.peers.items()) if peer.up)

    def _state_fields(self):
        # A stale guest is in a zone, but not in one the host can play with.
        in_zone = self._in_zone and not self.stale
        fields = {"role": self.role, "in_zone": in_zone, "v": PROTOCOL,
                  # For the launchers to show.
                  "loaded": self._in_zone, "syncing": self.stale, "joined": self.live, "apart": self.apart,
                  "stopped": bool(self._glued) and self._glued == self.remote_save}
        if self._in_zone and self._zone:
            # As text: ids are 64 bits wide.
            fields["zone"] = str(self._zone)
        if self._in_zone and self._lot:
            fields["lot"] = self._lot
        if self._in_zone and self._save:
            fields["save"] = str(self._save)
        if in_zone and self._world:
            fields["world"] = self._world
        return fields

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
        if self.role == HOST:
            self._share_building()
        told = (self._announced, self._announced_worlds)
        if self.role is not None and (self._state_fields(), self._worlds()) != told:
            # What the other games and the launchers know of us is out of date.
            self._announce()

    def _follow(self):
        """Guest: from what both games are doing, what to do next."""
        now = self._clock()
        if self._phase != IDLE:
            self._catch_up(now)
            return
        if self._world and not self.live and self._in_zone and not (self.remotes and self.remote_in_zone):
            # We loaded the host's state, and the host left it before we
            # joined: what we play from now on is ours to give.
            self._world = ""
            if not self._alone and not self._owes:
                self._alone = True
                self._gave = now
        if not (self.remotes and self._in_zone and self.remote_in_zone and self.speaks_our_version):
            return
        if not self.same_save:
            if self._glued != self.remote_save and now >= self._retry_at:
                self._go_stale("the host plays another save; bringing our household into it", give=True)
        elif self._glued and self._glued == self.remote_save:
            # We gave up on this save for good.
            pass
        elif not self.same_zone:
            if self._behind:
                # We were muted on the host's lot: what we have of our sims
                # is older than what the host has.
                self._go_stale("left the host's lot")
            elif self._owes or not self._call("playing_adopted", True):
                # What we have of our sims is older than the host's: our
                # game started from a save, not from the host's state.
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
            if self._travel is not None and (self._lost_at or self._away_at) and \
                    now - (self._lost_at or self._away_at) > TRAVEL_WAIT_SECONDS:
                self._give_up("the host is not on its lot; travelling as ordered")
                return
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
        # What a game that never loaded the host's state has of the host's
        # save is older than the host's own: not ours to give.
        self._alone = not self._owes and (not self.same_save or self._call("playing_adopted", True))
        if final:
            self._glued = self.remote_save
        self._retry_at = self._clock() + RETRY_SECONDS[min(self._failures, len(RETRY_SECONDS) - 1)]
        self._failures += 1
        self._behind = False
        if self._shows_host and self._in_zone:
            self._reload()
        self._shows_host = False
        self._let_travel_go()
        self._announce()

    def _reload(self):
        self._reloading = True
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
            # The host has none of what we did not run ourselves when it
            # plays another save than ours.
            whole = self._whole or not self.same_save
            self._game.contribute(lambda payload: self._saved(request, gift, payload, whole), whole)
        except Exception as error:
            self._log("could not save our household: {}".format(error))
            self._saved(request, gift, None, False)

    def _saved(self, request, gift, payload, whole=False):
        self._contributing = 0.0
        if self.role != GUEST:
            return
        if self._want_give:
            self._want_give = False
            request, gift = self._request, False
        if payload is not None and self._whole and not whole:
            # The host turned out to lack what this leaves out.
            if not gift and self._phase == SAVING and request == self._request:
                self._give_household()
            return
        self._sent_whole[request] = whole
        for old in [old for old in self._sent_whole if old < request - 16]:
            del self._sent_whole[old]
        if gift:
            if payload is not None and self.remotes and self.apart and self._phase == IDLE:
                self._gift = request
                self._transfers.send("household", request, payload, **self._gift_fields())
            return
        if self._phase != SAVING or request != self._request:
            return
        if payload is None:
            if self.same_save and not self._alone:
                self._log("could not save our household; loading the host's state as it is")
                self._ask()
            elif self.same_save:
                self._give_up("could not save our household; playing on by ourselves")
            else:
                self._give_up("could not save our household; each plays their own save")
        elif not self.remotes:
            self._phase = WAITING
        else:
            if self._gift and self._gift != request:
                # Older than this one, and not to be merged after it.
                self._transfers.drop(self._gift)
                self._gift = 0
            self._phase = GIVING
            self._deadline = 0.0
            self._transfers.send("household", request, payload, **self._gift_fields())

    def _gift_fields(self):
        """What goes with our household: the save we played it in, and the
        state of the host's it was played from, which the host tells how new
        it is by."""
        fields = {"save": str(self._save)}
        if self._loaded_request:
            fields["loaded"] = self._loaded_request
        return fields

    def _merged(self, message):
        request = frames.number(message.get("id"))
        if self.role == GUEST and message.get("missing"):
            # The host lacks sims we gave only in part: it is to have all.
            if self._sent_whole.get(request):
                # It was all of it.
                if request in (self._request, self._gift):
                    self._give_up("the host lacks sims of our household that we do not have either", final=True)
                return
            self._whole = True
            if self._phase in (SAVING, GIVING):
                self._go_stale("the host does not have all of our household; giving it again", give=True)
            else:
                self._gave = 0.0
            return
        if self.role != GUEST or self._phase != GIVING or request != self._request:
            return
        if message.get("ok"):
            if self._sent_whole.get(request):
                # The host has all of it for as long as this world of its lasts.
                self._whole_world = self.remote_world
            self._alone = False
            self._log("the host has our household ({})".format(message.get("outcome") or "taken in"))
            self._ask()
        elif self.same_save and not self._alone:
            self._log("the host did not take our household; loading its state as it is")
            self._ask()
        elif self.same_save and not message.get("final"):
            # Loading the host's state now would undo what we played.
            self._give_up("the host could not take our household for now; playing on by ourselves")
        else:
            self._give_up("the host did not take our household; each plays their own save",
                          final=bool(message.get("final")))

    def _merging(self, message):
        """Guest: the host has our household and takes it in once it is done
        with another guest's load: it has as long again to answer."""
        if self.role == GUEST and self._phase == GIVING and frames.number(message.get("id")) == self._request:
            self._deadline = self._clock() + MERGE_SECONDS

    # -- a guest loading the host's state -------------------------------

    def _ask(self):
        self._send_look()
        # Lots compare across saves; we may still be in a save of our own.
        # A guest about to travel asks for the lot it travels to.
        self._joining = self.same_zone or self._travel is not None
        self._asked_world = self.remote_world
        request = self._next(ASKING, SAVE_SECONDS)
        self._log("asking the host for its state")
        fields = {"id": request}
        zone, lot = (self._travel[2], self._travel[3]) if self._travel is not None else (self._zone, self._lot)
        if zone:
            fields["zone"] = str(zone)
        if lot:
            fields["lot"] = lot
        household = self._call("household_id", 0)
        if household:
            fields["household"] = str(household)
        if self._travel is not None:
            fields["travellers"] = [str(sim_id) for sim_id in self._travel[4]]
        self.send(frames.control("resync", **fields))

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
        travellers = self._travel[4] if self._travel is not None else ()
        try:
            self._game.adopt(payload, travellers)
        except Exception as error:
            self._give_up("could not load the host's state ({}); playing on by ourselves".format(error))
            return
        self._phase = LOADING
        self._left = False
        self._lost_loading = False
        # The game travels into the host's state now.
        self._travel = None
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
        self._loaded_request = self._request
        self._idle()
        self._lost_loading = False
        self._alone = False
        self._owes = False
        self._whole = False
        self._failures = 0
        self._retry_at = 0.0
        if lost and not self.remotes and self._joining:
            # Our game client shows the host's world, which the host may
            # still show once it is back.
            self._tell("loaded the host's state, but the connection is down; waiting for it")
            self._behind = self._shows_host = True
            self._phase = WAITING
            self._lost_at = self._clock()
            self._resume = world
        else:
            self._world = world
            self._behind = self._shows_host = False

    def resync(self):
        """Guest: load the host's state again, as after a reload of its
        zone. Returns False when there is nobody to load it from."""
        if self.role != GUEST or self.stale or not (self.live or self.apart):
            return False
        # Apart, we are the one who knows how our household is.
        self._go_stale("reloading from the host on request", give=not self.live and self._alone)
        self._watch_live()
        return True

    # -- the host answering a guest -------------------------------------

    def _resync(self, message, peer):
        request = (frames.number(message.get("id")), frames.number(message.get("zone")), str(message.get("lot") or ""))
        if self.role != HOST:
            return
        if not self._in_zone:
            self._refuse_resync(peer, request[0])
            return
        household = frames.number(message.get("household"))
        if household and not self._call("has_household", 1, household):
            # Our game was started again from a save without it.
            self._log_for(peer, "the other player's household is not in our save; asking for it")
            self._to(peer, frames.control("resync_failed", id=request[0], missing=True))
            return
        self._hold_for(peer, request)
        peer.asked = request
        # The sims a guest travels with, to be on our lot with it.
        peer.travellers = [frames.number(sim_id) for sim_id in message.get("travellers") or []]
        if not self._taking or self._clock() - self._taking > SAVE_SECONDS:
            self._take()
        elif self._may_join_save(peer):
            self._join_save(peer)

    def _hold_for(self, peer, request):
        """Hold, and say the world, for a guest whose snapshot is of our own
        lot: it will join. Asked again at every save, as we may have moved."""
        target = self._call("snapshot_zone", 0, request[1], request[2], self._key(peer))
        if self._in_zone and (target == self._zone if target else self._on_our_lot(request[1], request[2])):
            self._hold(peer)
            peer.held_for = request[0]
        elif peer.held_for == request[0]:
            peer.held_for = 0

    def _on_our_lot(self, zone, lot):
        """Whether a guest that asks for the state of that zone and lot will
        be on ours, and join."""
        if lot and self._lot:
            return lot == self._lot
        return not zone or not self._zone or zone == self._zone

    def _take(self):
        """Save for every guest that waits for a state of ours. One save is
        all their snapshots, each of the lot that guest is to load: after a
        load of our zone, which sends every guest back to loading, the game
        saves and pauses once for all of them. A guest whose save is left
        over from one that took too long is saved for again."""
        waiting = [peer for _, peer in sorted(self.peers.items()) if peer.asked is not None or peer.saving is not None]
        if not waiting:
            return
        for peer in waiting:
            if peer.asked is None:
                peer.asked = peer.saving
            peer.saving = None
        self._taking = self._clock()
        self._takes += 1
        number = self._takes
        world = self._world
        changes = self._changes
        # Who the save is for, and where each is: guests that ask while it
        # runs may still come in (see `_join_save`), until it is done.
        batch, places = [], []
        self._batch = (batch, places, self._call("progress", ""), self._changes)
        for peer in waiting:
            self._join_save(peer)
        try:
            self._game.snapshot(lambda payloads: self._taken(number, world, batch, payloads, changes), places)
        except Exception as error:
            self._log("could not save for the other player: {}".format(error))
            self._taken(number, world, batch, None, changes)

    def _may_join_save(self, peer):
        """Whether the save under way holds the state the guest asks for:
        our game has not moved since it began, nor taken in a household or
        a look, and the guest is not in it already: one that asks again, as
        after its link dropped, may have changed what it asks after. Then
        guests that ask at once, as after a load of our zone, share one save
        and one pause."""
        if self._batch is None:
            return False
        batch, _, progress, changes = self._batch
        return (bool(progress) and progress == self._call("progress", "") and changes == self._changes
                and all(other is not peer for other, _, _ in batch))

    def _join_save(self, peer):
        batch, places, _, _ = self._batch
        request, peer.asked = peer.asked, None
        self._hold_for(peer, request)
        peer.saving = request
        batch.append((peer, request, self._world_for(peer)))
        places.append((request[1], request[2], self._key(peer)))
        self._log_for(peer, "the other player is loading; saving for them")

    def _taken(self, number, world, batch, payloads, changes=0):
        if number != self._takes:
            # A later save runs, and answers.
            return
        self._taking = 0.0
        self._batch = None
        for index, (peer, request, shown) in enumerate(batch):
            current, peer.saving = peer.saving, None
            if self.role == HOST:
                payload = payloads[index] if payloads is not None and index < len(payloads) else None
                self._answer(peer, current, request, world, shown, payload, changes)
        if self.role != HOST or self._taking:
            return
        # Guests that asked while the save ran, or asked again, are saved
        # for once more, all of them at once.
        waiting = [peer for _, peer in sorted(self.peers.items()) if peer.asked is not None]
        if waiting and self._in_zone:
            self._take()
            return
        for peer in waiting:
            self._refuse_resync(peer, peer.asked[0])
            peer.asked = None

    def _answer(self, peer, current, request, world, shown, payload, changes=0):
        """Send the guest the state saved for it, `world` being our zone's
        load and `shown` the world it was to show when the save began, and
        `changes` what we had taken in from guests then."""
        if peer.asked is not None:
            # Asked again meanwhile: what was saved may be older than what
            # the guest asks after, as when its household came in between.
            # The next save answers.
            return
        if current is not request or not peer.up:
            # Asked for by a guest that has gone since.
            return
        moved = world != self._world or shown != self._world_for(peer)
        if payload is not None and moved and self._in_zone:
            # Our zone was loaded again while we saved, or our game moved on
            # from what the guest was loading: save it as it is now.
            peer.asked = request
        elif payload is None or moved:
            self._refuse_resync(peer, request[0])
        else:
            # Only a guest we held for joins on this world; one we did not
            # hold for, as it asked for another lot, finds out it is on ours
            # and asks again.
            held = request[0] == peer.held_for
            if held:
                # What our game is at in the state the guest will show.
                peer.taken_at = self._call("progress", "")
            # What its household, given once it loaded this state, will be
            # as new as, and from which of the lots it came to on (see
            # `_merge`): a lot apart it is on and loads, else those it comes
            # to next.
            since = peer.place_order if peer.place is not None and not held else self._arrivals + 1
            # The latest last: a guest whose game started again numbers its
            # requests from 1 again.
            peer.bases.pop(request[0], None)
            peer.bases[request[0]] = (changes, since)
            for old in list(peer.bases)[:-REMEMBERED]:
                del peer.bases[old]
            peer.transfers.send("state", request[0], payload, world=shown if held else "")

    def _refuse_resync(self, peer, request):
        """The guest gives up on this answer; it holds us again if it asks
        again."""
        self._to(peer, frames.control("resync_failed", id=request))
        peer.hold_until = 0.0

    def _hold(self, peer):
        """Keep the game paused for a guest that loads our state, as what we
        simulate meanwhile would be missing in its game. There is one clock:
        the guests already in are paused too, and told why."""
        if not peer.hold_until:
            self._tell("{} is loading; the game stays paused until they are in".format(self._called(peer)))
            peer.held_at = self._call("progress", "")
            for other in self._joined_peers():
                if other is not peer:
                    self._tell_guest(other, "{} is loading; the game stays paused until they are in".format(
                        peer.name or "another player"))
        peer.lapsed = False
        peer.hold_until = self._clock() + HOLD_SECONDS
        self._pause()

    def _tell_guest(self, peer, text):
        """Host: tell the player of that guest something, past its game."""
        self._to(peer, frames.control("tell", text=text))

    def _pause(self):
        self._paused_at = self._clock()
        try:
            self._game.pause()
        except Exception as error:
            self._log("could not pause for the other player: {}".format(error))

    def _keep_holding(self, now):
        for peer in list(self.peers.values()):
            self._keep_holding_for(peer, now)

    def _keep_holding_for(self, peer, now):
        """Each hold ends on what its own guest does, never on what another
        does: a guest that is in says so, one that is loading does not."""
        self._watch_lapse(peer, now)
        if not peer.hold_until:
            return
        if self._live_with(peer) or not self._in_zone or peer.in_zone:
            # In, or gone from our zone, or playing by itself: a guest that
            # is loading says it is not in a zone. A save that crosses our
            # zone loading again joins only if held for again.
            peer.hold_until = 0.0
            if not self._in_zone:
                peer.held_for = 0
            self._log("the game is no longer held for {}".format(self._called(peer)))
        elif now > peer.hold_until or (peer.link_lost_at and now - peer.link_lost_at > LINK_SECONDS):
            peer.hold_until = 0.0
            peer.link_lost_at = 0.0
            peer.lapsed = True
            self._log("{} did not get in; the game is no longer held for them".format(self._called(peer)))
        elif now - self._paused_at > PAUSE_SECONDS:
            self._pause()

    def _watch_lapse(self, peer, now):
        """After a hold ran out: once our game has moved on from where the
        hold began, a guest still loading what we saved would join on a
        world behind ours, so it is to show another. Of that guest alone:
        the others are not behind."""
        if not peer.lapsed or now - peer.looked_at < PAUSE_SECONDS:
            return
        peer.looked_at = now
        if self._live_with(peer) or not self._in_zone:
            peer.lapsed = False
            return
        progress = self._call("progress", "")
        if not progress or progress != peer.held_at:
            peer.lapsed = False
            self._log_for(peer, "our game moved on since the other player's state was saved")
            self._bump(peer)

    def _given(self, peer, incoming):
        """Host: a guest's household is here. Taken in at once, unless we
        save or hold for another guest: what that guest loads is to be our
        game as it was saved, and taking a household in edits our save and
        loads sims. It then waits, and is taken in as soon as nothing holds
        it, gifts that nothing holds in the order they came; a newer gift of
        the same guest takes the place of its older one."""
        self._gifts = [entry for entry in self._gifts if entry[0] is not peer] + [[peer, incoming, 0.0]]
        self._merge_waiting()

    def _busy_for(self, peer):
        """Host: the other guests a gift of `peer` waits for: those our save
        under way is for, and those we hold for."""
        busy = []
        if self._batch is not None and self._clock() - self._taking <= SAVE_SECONDS:
            # One that takes longer is not waited for, as by those who ask.
            busy += [other for other, _, _ in self._batch[0] if other is not peer]
        busy += [other for other in self._held_for(but=peer) if other not in busy]
        return busy

    def _merge_waiting(self):
        """Host: take in the gifts that need wait no longer; tell the guests
        of the others, once in a while, that theirs is still to come, which
        keeps them from giving up on it."""
        now = self._clock()
        for entry in list(self._gifts):
            peer, incoming, told = entry
            busy = self._busy_for(peer) if self.role == HOST else []
            if not busy:
                self._gifts.remove(entry)
                self._merge(peer, incoming)
            elif not told or now - told > MERGE_SECONDS / 2:
                if not told:
                    self._log_for(peer, "the other player's household waits while we save or hold for {}".format(
                        _names(busy)))
                    self._tell_guest(peer, "{} {} loading; your household is taken in once they are in".format(
                        _names(busy), "is" if len(busy) == 1 else "are"))
                entry[2] = now
                self._to(peer, frames.control("merging", id=incoming.ident))

    def _key(self, peer):
        """Host: what tells the guest's player from another's in what our
        game keeps for them, as the zones of its own save."""
        return peer.player or str(peer.ident)

    def _claimed(self, peer):
        """Host: (the sims other guests than `peer` control on our lot or
        between lots, those they took off our lot to play on another, those
        made theirs on our lot). The one a guest controls is its own; one it
        was handed as having come along with it is only until another player
        takes control of it. A guest playing apart claims only the sims it
        took off our lot (`_send_off`), not one another player plays here;
        what it plays elsewhere is its own only while it is not back on our
        lot, and none of it while its link is down, as a guest that has
        gone for good would otherwise keep its sims from the others."""
        controlled, away, handed = set(), set(), set()
        for other in self.peers.values():
            if other is peer:
                continue
            if other.sim_id and not self._apart_with(other):
                controlled.add(other.sim_id)
            if other.up and not self._here(other):
                away.update(other.away)
            if other.up:
                handed.update(other.welcomed)
        return controlled, away, handed

    def _here(self, peer):
        """Host: the guest is on our lot, joined or about to be."""
        return peer.in_zone and self._same_zone(peer) and self._same_save(peer)

    def _taken_from(self, peer):
        """Host: the guest controls a sim it was refused, as another player
        was handed it or plays it elsewhere first."""
        if not peer.sim_id or peer.sim_id in peer.welcomed:
            return False
        _, away, handed = self._claimed(peer)
        return peer.sim_id in away or peer.sim_id in handed

    def _played_here(self, peer):
        """Host: the sims other guests than `peer` play on our lot."""
        controlled, _, handed = self._claimed(peer)
        return controlled | handed

    def _others_sims(self, peer):
        """Host: the sims any other guest than `peer` plays, here or
        elsewhere."""
        controlled, away, handed = self._claimed(peer)
        return controlled | away | handed

    def _merge(self, peer, incoming):
        if self.role != HOST:
            return
        self._changes += 1
        request = incoming.ident
        # The state of ours the guest played from, as it says: what it
        # gives is as new as that. One sent but never loaded does not count.
        base, since = peer.bases.get(frames.number(incoming.fields.get("loaded")), (None, 0))
        save = frames.number(incoming.fields.get("save"))
        # Only lots of our own save: a household from another is brought in
        # whole, and is not ours to keep.
        first = self._first_where(peer, since) if not save or save == self._save else None
        if first is not None and self._in_zone:
            # Two games ran that lot apart, and the first one there plays
            # it: what the second did there would undo what the first does.
            # Judged by where the guest played, not where it is: it may have
            # come to our lot since. It loads our copy, as it was told.
            self._log_for(peer, "{} plays the lot the other player gives its household from; not taken".format(
                first.label or "another guest"))
            self._tell_guest(peer, "{} is already playing that lot; the host keeps your household as it has it".format(
                first.name or "another player"))
            self._to(peer, frames.control("merged", id=request, ok=True, outcome=GIFT_KEPT))
            return
        try:
            if not self._in_zone:
                raise ValueError("no zone is loaded")
            outcome = self._game.merge(incoming.payload, save, self._key(peer),
                                       (self._key(peer), base, self._changes), sorted(self._others_sims(peer)))
        except Exception as error:
            final = bool(getattr(error, "final", False))
            self._log("could not take the other player's household: {}".format(error))
            fields = {"id": request, "ok": False, "final": final}
            if getattr(error, "missing", False):
                fields["missing"] = True
            self._to(peer, frames.control("merged", **fields))
        else:
            if outcome == GIFT_OLDER:
                self._tell_guest(peer, "another player gave your household since you loaded the host's game; "
                                       "the host keeps theirs")
            self._to(peer, frames.control("merged", id=request, ok=True, outcome=str(outcome or "")))

    def _welcome(self, peer):
        if self.role != HOST or not peer.sim_id or not self._in_zone:
            return
        controlled, away, handed = self._claimed(peer)
        # The sim it controls may have come along with another guest: it
        # is this one's now. No other sim another player plays is handed.
        claimed = controlled | away | (handed - {peer.sim_id})
        try:
            theirs = self._game.welcome(peer.sim_id, tuple(peer.welcomed), tuple(peer.travellers),
                                        tuple(sorted(claimed)))
        except Exception as error:
            self._log("could not bring the other player's sim in: {}".format(error))
            return
        if not theirs:
            self._log("the other player's sim {} is not theirs to play here".format(peer.sim_id))
            return
        # What the guest plays now; sims it played before are ours again,
        # and none is played elsewhere: `_send_off` notes them again when it
        # leaves.
        peer.welcomed = list(theirs)
        peer.away = []
        self._unclaim(peer, theirs)

    def _unclaim(self, peer, sims):
        """Host: `sims` are the guest's now, and no longer made over to any
        other guest on our lot, but for one that other guest controls."""
        for other in self.peers.values():
            mine = set(sims) - {other.sim_id}
            if other is not peer and mine & set(other.welcomed):
                other.welcomed = [sim for sim in other.welcomed if sim not in mine]

    def _send_off(self, peer, away=None):
        """Host: the guest took its sims to another lot, or says it plays
        `away` there. Left here they would go on being simulated, and our
        next save would undo what the guest does with them there. Once for
        each load of our lot."""
        away = peer.away if away is None else away
        # Never one another player plays here: it stays with them, and is
        # not the guest's to claim while it is elsewhere.
        others = self._played_here(peer)
        sims = [sim for sim in list(peer.welcomed) + [sim for sim in away if sim not in peer.welcomed]
                if sim not in others]
        if not peer.welcomed and sims == peer.away and peer.sent_off_in == self._world:
            return
        peer.away, peer.welcomed = sims, []
        peer.sent_off_in = self._world
        if sims:
            try:
                self._game.send_off(sims)
            except Exception as error:
                self._log("could not send the other player's sims off: {}".format(error))

    def _note_place(self, peer):
        """Host: the lot apart from ours the guest is on, and when it came
        there. Only the host's lot is shared: two guests on another lot each
        run it in a game of their own, and only the first one there gives
        what was played there (`_merge`); the next one is told. Kept while
        the guest is between lots, and while our own zone is not known."""
        if not self._in_zone or not (peer.zone or peer.lot):
            return
        place = None if self._same_zone(peer) and self._same_save(peer) else (peer.save, peer.lot, peer.zone)
        if place == peer.place:
            return
        peer.place = place
        if place is None:
            return
        self._arrivals += 1
        peer.place_order = self._arrivals
        peer.played = peer.played[-(REMEMBERED - 1):] + [(place, peer.place_order)]
        first = self._first_on(peer)
        if first is not None:
            self._log_for(peer, "the other player is on a lot {} plays apart from us".format(
                first.label or "another guest"))
            self._tell_guest(peer, "{} is already playing this lot; what you play here is not taken into the "
                                   "host's game while they are here".format(first.name or "another player"))

    def _first_on(self, peer):
        """Host: the guest that came first to the lot apart from ours that
        `peer` is on, when that is another; else None."""
        return self._first_at(peer, peer.place, peer.place_order)

    def _first_at(self, peer, place, order):
        """Host: the guest other than `peer` still on `place`, which came
        there before the arrival numbered `order`, the first of them; else
        None."""
        if place is None:
            return None
        before = [other for other in self.peers.values()
                  if other is not peer and other.up and other.place_order < order
                  and _same_place(other.place, place)]
        return min(before, key=lambda other: other.place_order) if before else None

    def _first_where(self, peer, since):
        """Host: a guest that came first to a lot apart from ours that `peer`
        played on since the arrival numbered `since`, and is still there."""
        for place, order in peer.played:
            first = self._first_at(peer, place, order) if order >= since else None
            if first is not None:
                return first
        return None

    # -- once per simulation tick ---------------------------------------

    def tick(self):
        if self.role is None:
            self._unattended()
            return
        in_zone = bool(self._game.in_zone())
        zone = self._call("zone_id", 0) if in_zone else 0
        if in_zone != self._in_zone or zone != self._zone:
            self._in_zone = in_zone
            self._read_zone()
            # The host forgets our sim when we leave a zone.
            for peer in self._all_peers():
                peer.reported_sim_id = 0
            self._zone_changed()
            self._announce()
        if (self._cas is not None and self._in_zone and self._clock() - self._cas[2] > CAS_SECONDS
                and not self._call("leaving_zone", 0)):
            # Create-a-Sim did not open after all, and our own simulation was
            # let through to our client meanwhile.
            self._cas = None
            if self.live:
                self._go_stale("Create-a-Sim did not open; loading the host's state again")
        for peer in self._all_peers():
            peer.transfers.pump()
            if self.role == HOST:
                # A guest gives up on these after as long, and says nothing
                # if its link went down meanwhile.
                peer.transfers.expire(STALL_SECONDS)
        self._keep_holding(self._clock())
        if self._gifts:
            self._merge_waiting()
        self._check()
        if self.role == GUEST and (self.live or self.apart):
            # Apart too: the host keeps the sim off its lot.
            self._report_sim()

    def _unattended(self):
        """No launcher: still note each load, so that a save loaded from the
        menu meanwhile is known for one."""
        in_zone = bool(self._game.in_zone())
        if in_zone != self._in_zone:
            self._in_zone = in_zone
            if in_zone:
                self._reloading = False
                if self._call("loaded_by_hand", 0):
                    self._call("forget_gifts", 0)

    def _zone_changed(self):
        if self._building:
            # A client built again is not in build mode.
            self._building = False
            if self.role == HOST:
                self._share_building()
            elif self.remotes:
                self.send(frames.control("building", on=False))
        if self.role == HOST:
            self._world = ""
            if self._in_zone and self._call("loaded_by_hand", 0):
                # A save from the menu: households guests gave since our game
                # started may be gone from it, and what the states we sent
                # held tells nothing of it.
                self._call("forget_gifts", 0)
                for peer in self.peers.values():
                    peer.bases = {}
            if self._in_zone:
                self._new_world()
                for peer in list(self.peers.values()):
                    if peer.away and peer.in_zone and not self._same_zone(peer):
                        # Our lot was loaded again, perhaps with them on it.
                        self._send_off(peer)
            return
        if self._phase == LOADING:
            if not self._in_zone:
                self._left = True
            elif self._left:
                self._loaded()
            return
        # Whatever our game client showed, it now shows our own simulation.
        self._world = ""
        self._shows_host = False
        by_hand = 0
        if self._phase == IDLE and self._in_zone:
            by_hand = self._call("loaded_by_hand", 0)
            if by_hand:
                self._look = None
        if self._cas is not None and self._in_zone:
            self._closed_cas(by_hand)
        if self._phase != IDLE and not self._in_zone:
            self._give_up("left the zone before the host's state arrived")
        elif self._phase == IDLE and self._in_zone:
            if self._reloading:
                # Our own reload, of our own zone.
                self._reloading = False
            elif by_hand:
                # The player loaded a save from the menu: what we played
                # before is gone, and it may be an older copy of the host's
                # save, or a save of their own, whose household is brought
                # over again.
                self._glued = 0
                self._alone = False
                self._retry_at = 0.0
                self._failures = 0
                self._loaded_request = 0

    # -- the session going live and back -------------------------------

    def _watch_live(self):
        live = self.live
        if live != self._was_live:
            self._was_live = live
            self._log("session is live as {}".format(self.role) if live else "session is not live")
            self._announce()
            if live and self.role == GUEST:
                self._behind = self._shows_host = True
                self._report_sim()
        if self.role == HOST:
            self._watch_peers()

    def _watch_peers(self):
        """Host: each guest that joins, alone or with others already in."""
        joining = []
        for _, peer in sorted(self.peers.items()):
            live = self._live_with(peer)
            if live != peer.was_live:
                peer.was_live = live
                if len(self.peers) > 1:
                    self._log_for(peer, "joined" if live else "no longer joined")
                if live:
                    joining.append(peer)
        if not joining:
            return
        self._keep_holding(self._clock())
        # The guest's game client comes out of a loading screen paused and
        # only hears of a speed when it changes. Had we kept running, it
        # would show a pause that pressing play, which changes nothing here,
        # never lifts. Paused, all agree, and any player resumes.
        self._pause()
        for peer in joining:
            if peer.sim_id:
                self._joined(peer)
            held = self._held_for(but=peer)
            if held:
                # It comes into a game that stays paused for another.
                self._tell_guest(peer, "{} {} loading; the game stays paused until they are in".format(
                    _names(held), "is" if len(held) == 1 else "are"))

    def _joined(self, peer):
        try:
            self._game.joined(peer.sim_id)
        except Exception as error:
            self._log("could not note the other player's household: {}".format(error))

    def _report_sim(self):
        sim_id = self._game.active_sim_id() or 0
        if sim_id != self._reported_sim_id:
            self._reported_sim_id = sim_id
            self.send(frames.control("active_sim", sim_id=str(sim_id)))

    def _lost(self, peer, why):
        """The other game cannot be reached, for now or for good."""
        peer.transfers.forget()
        was_live = self._was_live
        peer.forget()
        if self.role == HOST:
            # That guest alone: the others play on.
            peer.saving = None
            peer.asked = None
            # A gift of its that waits has nobody to answer: it gives again.
            self._gifts = [entry for entry in self._gifts if entry[0] is not peer]
            if peer.hold_until and not peer.link_lost_at:
                peer.link_lost_at = self._clock()
            self._call("forget_peer", 0, peer.ident)
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

    def frame(self, kind, ident, payload, peer=None):
        """A frame from the launcher. `peer` is the guest a host's launcher
        says it comes from (see tsmp/frames.py, ROUTED), None when it says
        nothing."""
        message = frames.parse_json(payload) or {} if kind == frames.CONTROL else None
        if message is not None and peer is None and message.get("type") in LINK_CONTROLS:
            # The launcher's own, about its remotes. Those it says itself
            # are never wrapped: one that came from a guest is not believed,
            # and `_control` leaves it aside.
            self._link(message)
            self._check()
            return
        remote = self._from(peer)
        if remote is None:
            self._log("dropped a frame from a guest our launcher did not name")
            return
        if kind == frames.CONTROL:
            self._control(message, remote)
        elif kind == frames.MSG:
            if self.role == GUEST and self.live:
                self.delivered += 1
                self._game.deliver(ident, payload)
        elif kind == frames.COMMAND:
            if self.role == HOST and self._live_with(remote):
                self._execute(frames.parse_json(payload) or {}, remote)
        elif kind == frames.PART:
            done = remote.transfers.part(ident, payload)
            if done is not None:
                self._finished(done, remote)
            self._check()

    def _finished(self, incoming, peer):
        if incoming.what == "state":
            self._adopt(incoming.payload)
        elif incoming.what == "household":
            # Counted once taken in (`_merge`): one that waits has not
            # changed what our saves hold yet.
            self._given(peer, incoming)

    def _seen(self, message, peer):
        """Host: the guest that is loading still takes our state, or gave
        up on what we hold for."""
        if not peer.hold_until:
            return
        ident = frames.number(message.get("id"))
        outgoing = peer.transfers.outgoing(ident)
        if outgoing is None or outgoing.what != "state":
            return
        if message.get("type") == "got":
            peer.hold_until = max(peer.hold_until, self._clock() + HOLD_SECONDS)
        elif message.get("type") == "drop" and ident == peer.held_for and peer.asked is None:
            peer.hold_until = 0.0
            self._log("{} gave up loading; the game is no longer held for them".format(self._called(peer)))

    def _link(self, message):
        """Our launcher's remotes come and go. A host's launcher of version 3
        names each with `peer_up` and `peer_gone`, and still says `link`
        for mods before it, which a host that heard those leaves aside."""
        kind = message.get("type")
        if self.role != HOST:
            if kind != "link":
                return
            remotes = frames.number(message.get("remotes"))
            self.remotes = remotes
            self._host.up = remotes > 0
            if remotes == 0:
                self._lost(self._host, "the link is down")
            else:
                self._linked(self._host)
            return
        if kind == "link":
            if frames.number(message.get("remotes")) == 0:
                for peer in list(self.peers.values()):
                    self._unlinked(peer)
            elif not self._routed:
                self._linked(self._peer(LEGACY))
            return
        self._routed = True
        ident = frames.number(message.get("peer"))
        if kind == "peer_up":
            peer = self._peer(ident)
            peer.name = str(message.get("name") or "")
            peer.player = str(message.get("player") or "")
            self._linked(peer)
        elif ident in self.peers:
            self._unlinked(self.peers[ident])

    def _linked(self, peer):
        """Whoever just connected has not heard our state yet, nor we
        theirs, and knows nothing of what was under way."""
        peer.up = True
        if self.role == HOST:
            self.remotes = sum(1 for other in self.peers.values() if other.up)
        # Its launcher keeps its number for a guest that comes back: the
        # wait for it ends, and only for it.
        peer.link_lost_at = 0.0
        peer.transfers.forget()
        self._look_sent = False
        self._announce(want=True, to=peer)
        self._send_look()
        if self.role == HOST:
            peer.building_sent = ()
            self._share_building()
        elif self._building:
            self._to(peer, frames.control("building", on=True))

    def _unlinked(self, peer):
        """Host: a guest's link is down, for now or for good."""
        peer.up = False
        self.remotes = sum(1 for other in self.peers.values() if other.up)
        self._lost(peer, "the link is down")

    def _control(self, message, peer):
        kind = message.get("type")
        if kind == "gone":
            self._lost(peer, "the other game is gone")
        elif kind == "state":
            self._state(message, peer)
        elif kind in ("offer", "got", "drop"):
            self._seen(message, peer)
            offer = peer.transfers.receive(message)
            if offer is None:
                pass
            elif offer.what == "state":
                self._offered(offer)
            elif offer.what == "household" and self.role == HOST:
                done = peer.transfers.accept(offer)
                if done is not None:
                    self._finished(done, peer)
            else:
                peer.transfers.refuse(offer.ident)
        elif kind == "resync":
            self._resync(message, peer)
        elif kind == "resync_failed":
            if self.role == GUEST and self._phase == ASKING and frames.number(message.get("id")) == self._request:
                if message.get("missing") and self._whole and self._whole_world == self._asked_world:
                    self._give_up("the host lacks our household although we gave all of it", final=True)
                elif message.get("missing"):
                    # The host's game started again from a save without our
                    # household: all of it is given again, however old.
                    self._whole = True
                    self._go_stale("the host does not have our household; giving it again", give=True)
                elif not self.remote_in_zone or self.remote_world != self._asked_world:
                    # The host left the state it was saving; wait for its new one.
                    self._go_stale("the host's lot changed while it saved; waiting for it")
                else:
                    self._give_up("the host could not save; playing on by ourselves")
        elif kind == "merged":
            self._merged(message)
        elif kind == "merging":
            self._merging(message)
        elif kind == "tell":
            if self.role == GUEST:
                self._tell(str(message.get("text") or "")[:200])
        elif kind == "cas":
            line = self._our_cas_line(str(message.get("line") or ""))
            if self.role == GUEST and line and self.live:
                try:
                    self._game.client_cheat(line)
                except Exception as error:
                    self._cas = None
                    self._log("could not open Create-a-Sim: {}".format(error))
        elif kind == "appearance":
            # A save begun before is older than what the guest gave.
            self._changes += 1
            self._appearance(message, peer)
        elif kind == "appearance_done":
            self._appearance_done(message)
        elif kind == "building":
            self._remote_build_mode(message, peer)
        elif kind == "active_sim":
            peer.sim_id = frames.number(message.get("sim_id"))
            if self.role == HOST and peer.sim_id:
                # Its own, also when it came along with another guest.
                self._unclaim(peer, [peer.sim_id])
            if self.role == HOST and self._apart_with(peer):
                if peer.sim_id:
                    self._send_off(peer, [peer.sim_id])
            elif self._live_with(peer):
                self._welcome(peer)
            if self._live_with(peer) and peer.sim_id:
                self._joined(peer)
        self._check()

    def _state(self, message, peer):
        if message.get("role") == self.role:
            self._log("the other game is also a {}; ignoring it".format(self.role))
            return
        if message.get("want"):
            self._announce(to=peer)
        version = frames.number(message.get("v"))
        if version != peer.version:
            peer.version = version
            if version != PROTOCOL:
                self._log_for(peer, "the other game runs another version of the mod ({}, ours {}); not joining".format(
                    version, PROTOCOL))
        was_live = self.live
        was_zone, was_lot = peer.zone, peer.lot
        peer.in_zone = bool(message.get("in_zone"))
        peer.zone = frames.number(message.get("zone"))
        peer.lot = str(message.get("lot") or "")
        peer.save = frames.number(message.get("save"))
        world = str(message.get("world") or "")
        if not peer.in_zone or world != peer.world:
            peer.building = False
        peer.world = world
        if self.role == HOST and peer.taken_at and peer.world and peer.world == self._world_for(peer):
            # The guest shows the state we saved for it: if our game moved
            # on since, it is behind ours, and loads again. It alone: the
            # guests in with us are not behind.
            taken, peer.taken_at = peer.taken_at, ""
            now_at = self._call("progress", "")
            if now_at and now_at != taken:
                self._log_for(peer, "our game moved on while the other player loaded; they load it again")
                peer.world = ""
                self._bump(peer)
        if self.role == GUEST and was_live and not peer.in_zone:
            # Its zone will be built again, and its game client with it.
            self._go_stale("the host left its lot; waiting for it", tell=True)
        if (self.role == GUEST and self._phase in (ASKING, RECEIVING) and self._joining
                and (not peer.in_zone or peer.world != self._asked_world)):
            # What is on its way is of a world the host has left.
            peer.transfers.drop(self._request)
            self._go_stale("the host's lot changed while it sent its state; waiting for it")
        if not peer.in_zone:
            peer.sim_id = 0
            peer.reported_sim_id = 0
        elif self.role == HOST and not self._same_zone(peer) and (peer.zone, peer.lot) != (was_zone, was_lot):
            self._send_off(peer)
        if self.role == HOST:
            self._note_place(peer)

    def _execute(self, message, peer):
        name = message.get("name")
        args = message.get("args")
        if not isinstance(name, str) or not isinstance(args, list) or not _forwarded(name):
            return
        if name.startswith(FORWARD_PREFIXES) and self._taken_from(peer):
            # It would act as a sim another player plays.
            self._log_for(peer, "{} refused: sim {} is another player's".format(name, peer.sim_id))
            return
        self.executed += 1
        self._remote_depth += 1
        executing, self._executing = self._executing, peer
        try:
            # As that guest's sim, with what our game keeps for that guest's
            # menus: the replies it makes on the way are that guest's alone.
            self._game.execute(name, [str(a) for a in args], peer.sim_id, peer.ident)
        finally:
            self._remote_depth -= 1
            self._executing = executing

    # -- hooks into the local game --------------------------------------

    def outbound(self, msg_id, data):
        """The local simulation is sending `data` to the local game client.
        Returns what the local client should receive instead, or None for
        nothing."""
        consts = self._consts
        if msg_id == consts.travel and consts.travel is not None and self._holds_travel(data):
            return None
        if self._notices and msg_id == consts.notice:
            for text in self._notices:
                if text in data:
                    self._notices.remove(text)
                    return data
        if not (self.live or self._muted) or self._cas is not None:
            return data
        if self.role == HOST:
            if msg_id in consts.owned:
                owner = wire.first_varint(data, consts.owned[msg_id])
                guest = self._owner(owner) if owner else None
                if guest is not None:
                    self._relay(msg_id, data, to=guest)
                    return None
                if owner and msg_id not in consts.shared:
                    return data
            if msg_id in consts.replies:
                if self._remote_depth:
                    self._relay(msg_id, data, to=self._executing)
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

    def _holds_travel(self, data):
        """Guest: our game orders its client to travel. To the lot the host
        is on, that would be two loading screens: our own copy of the lot,
        then the host's state. So the order is held back, the host's state
        fetched, and the game travels once, into it."""
        if (self.role != GUEST or self._phase != IDLE or self.live or not self.remotes or not self.remote_in_zone
                or not self.speaks_our_version or self._travel is not None
                or (self._glued and self._glued == self.remote_save)):
            return False
        zone_id, sims = wire.travel(data)
        if not zone_id:
            return False
        theirs = self._call("host_zone", 0, zone_id)
        lot = self._call("lot_of", "", zone_id)
        if not (lot == self.remote_lot if lot and self.remote_lot else theirs and theirs == self.remote_zone):
            return False
        self._travel = (data, zone_id, theirs, lot, sims)
        self._tell("travelling to the other player's lot; bringing their game along")
        self._go_stale("travelling to the host's lot", give=self._alone or not self.same_save)
        return True

    def _let_travel_go(self):
        """Guest: the held travel goes ahead as our game ordered it."""
        travel, self._travel = self._travel, None
        if travel is not None and self._in_zone:
            self._log("travelling as our game ordered")
            try:
                self._game.deliver(self._consts.travel, travel[0])
            except Exception as error:
                self._log("could not travel: {}".format(error))

    def _owner(self, sim_id):
        """Host: the guest that plays `sim_id`, or None for our own player.
        Only a guest joined to us: one that is not drops what we relay, so
        a screen for a sim of its is ours, as with one guest that is not
        joined. The player who controls it first, but for one that took
        control of a sim another player was handed before it: that one
        keeps it (see `_welcome`); then the guest it was made over to on our
        lot; then the guest of its household, when that is not ours. A
        household that more than one guest plays from is ours to answer
        for, as long as none of them controls the sim: a screen of it would
        otherwise go to one of them at random."""
        playing = [peer for _, peer in sorted(self.peers.items()) if peer.sim_id and self._live_with(peer)]
        for peer in playing:
            if sim_id == peer.sim_id and (sim_id in peer.welcomed or not any(
                    sim_id in other.welcomed for other in playing if other is not peer)):
                return peer
        ours = self._game.active_sim_id()
        if sim_id == ours:
            return None
        for peer in playing:
            if sim_id in peer.welcomed:
                return peer
        try:
            same = self._game.same_household
            theirs = [peer for peer in playing if same(sim_id, peer.sim_id) and not same(peer.sim_id, ours)]
        except Exception:
            return None
        return theirs[0] if len(theirs) == 1 else None

    def client_cheat(self, line):
        """Host: the simulation passes `line` on to our game client, as it
        does to open Create-a-Sim. Returns False to keep it from ours when it
        names a sim the guest plays: it would take our player's screen, and
        the guest's own client cannot edit a sim of the host's simulation."""
        if self.role == GUEST and line.startswith(CAS_SCREEN):
            return self._open_cas(line)
        if self.role != HOST or not self.live:
            return True
        for sim_id in _numbers(line):
            guest = self._owner(sim_id)
            if guest is not None:
                # The guest's own client edits it, and we take the new look.
                self._log("passing {!r} to the other player's game: it is for their sim".format(line))
                self._to(guest, frames.control("cas", line=line))
                return False
        return True

    def _our_cas_line(self, line):
        """The host's line to open Create-a-Sim, made ours: its third number
        is the sim the client comes back to, the host's own there."""
        words = line.split()
        if len(words) < 2 or words[0] != CAS_SCREEN:
            return ""
        if len(words) >= 4 and frames.number(words[3]):
            words[3] = str(self._game.active_sim_id() or words[1])
        return " ".join(words)

    def _build_mode(self, on):
        """Our player enters or leaves build mode; the others are told, as
        two players building one lot at once should edit different parts of
        it: the same wall edited by both at the same instant may end up
        differently in the two games."""
        if on == self._building:
            return
        self._building = on
        if self.role == HOST:
            self._share_building()
        elif self.remotes and self.role is not None:
            self.send(frames.control("building", on=on))
        if on and any(peer.building and self._same_zone(peer) and self._same_save(peer)
                      for peer in self._all_peers()):
            self._tell(BOTH_BUILDING)

    def _builders(self, but):
        """Host: who builds on our lot, other than the guest `but`, by the
        names its player is told."""
        names = ["the host"] if self._building else []
        for _, peer in sorted(self.peers.items()):
            if (peer is not but and peer.building and peer.in_zone and self._same_zone(peer)
                    and self._same_save(peer)):
                names.append(peer.name or "another player")
        return names

    def _share_building(self):
        """Host: each guest hears who else builds on our lot: `on` while
        anyone does, as a host with one guest says of itself, and `who`."""
        for _, peer in sorted(self.peers.items()):
            if not peer.up:
                continue
            names = tuple(self._builders(but=peer))
            if names != peer.building_sent:
                peer.building_sent = names
                self._to(peer, frames.control("building", on=bool(names), who=list(names)))

    def _remote_build_mode(self, message, peer):
        on = bool(message.get("on"))
        who = message.get("who") if self.role == GUEST else None
        names = tuple(str(name)[:40] for name in who) if isinstance(who, list) else ()
        if on == peer.building and names == peer.builders:
            return
        before, peer.building, peer.builders = peer.builders, on, names
        if self.role == HOST:
            self._share_building()
        if not (self._same_zone(peer) and self._same_save(peer) and peer.in_zone):
            return
        if len(names) > 1 or len(before) > 1:
            # Several others: say who.
            if not on:
                self._tell("nobody else is in build mode")
            elif self._building:
                self._tell("{} and you are in build mode: build on different parts of the lot, as one wall "
                           "edited by several at the same moment may end up differently in the games".format(
                               ", ".join(names)))
            else:
                self._tell("{} {} in build mode".format(", ".join(names), "is" if len(names) == 1 else "are"))
        elif not on:
            self._tell("{} left build mode".format(self._called(peer)))
        elif self._building:
            self._tell(BOTH_BUILDING)
        else:
            self._tell("{} is in build mode".format(self._called(peer)))

    def _open_cas(self, line):
        """Guest: our game client is told to open Create-a-Sim with `line`
        (`sims.exit2cas <sim> ...`). Joined, we let it and note how the sim
        looks, to send what changes to the host; while our client shows a
        host's world we are loading, it stays shut. Returns whether it may
        open."""
        if self._muted:
            self._cas = None
            self._tell(CAS_REFUSED)
            return False
        if self.live or self._cas is not None:
            numbers = _numbers(line)
            sim_id = numbers[0] if numbers else self._game.active_sim_id()
            try:
                before = self._game.appearance(sim_id)
            except Exception as error:
                self._log("could not note how sim {} looks: {}".format(sim_id, error))
                before = b""
            self._cas = (sim_id, before, self._clock(), self._save)
            self._log("Create-a-Sim opens for our sim {}; what changes goes to the host".format(sim_id))
        return True

    def _closed_cas(self, by_hand):
        """Guest: back from Create-a-Sim. The host has the sim as it was; it
        takes what changed before it saves its state for us. Not after a
        save was loaded from the menu meanwhile: that is another look."""
        sim_id, before, _, save = self._cas
        self._cas = None
        if by_hand or self._save != save:
            return
        try:
            after = self._game.appearance(sim_id)
            look, names = self._game.look_changes(before, after)
        except Exception as error:
            self._log("could not read how sim {} looks now: {}".format(sim_id, error))
            return
        if names:
            self._look = (sim_id, look, names)
            self._look_sent = False
            self._send_look()

    def _send_look(self):
        """Guest: the new look is on its way to the host until it says it
        has it; sent again on every new link, always before we ask for its
        state."""
        if self._look is None or self._look_sent or not self.remotes or not self.same_save:
            return
        sim_id, look, names = self._look
        self.send(frames.control("appearance", sim_id=str(sim_id), look=base64.b64encode(look).decode("ascii"),
                                  fields=list(names)))
        self._look_sent = True
        self._log("sent the new look of sim {} to the host".format(sim_id))

    def _appearance(self, message, peer):
        """Host: the guest edited one of its sims in Create-a-Sim."""
        sim_id = frames.number(message.get("sim_id"))
        if self.role != HOST or not sim_id:
            return
        ok = False
        if sim_id in self._others_sims(peer):
            # A guest restyles only its own: another player plays this one,
            # here or elsewhere.
            self._log_for(peer, "the other player sent a look for sim {}, which another player plays; refused".format(
                sim_id))
        elif sim_id == peer.sim_id or sim_id in peer.welcomed or sim_id in peer.away:
            try:
                look = base64.b64decode(str(message.get("look") or ""))
                names = [str(name) for name in message.get("fields") or []]
                self._game.apply_appearance(sim_id, look, names)
                ok = True
            except Exception as error:
                self._log("could not give sim {} its new look: {}".format(sim_id, error))
        self._to(peer, frames.control("appearance_done", sim_id=str(sim_id), ok=ok))

    def _appearance_done(self, message):
        if self.role != GUEST or self._look is None or frames.number(message.get("sim_id")) != self._look[0]:
            return
        self._look = None
        if not message.get("ok"):
            self._tell("the host could not take the new look of your sim")

    def _relay(self, msg_id, data, to=None):
        """Host: a message for the game clients of the guests joined to us,
        or for the guest `to` alone. One that is not joined has no use for
        the stream, and its upload is what a guest that loads waits on."""
        self.relayed += 1
        frame = frames.pack(frames.MSG, msg_id, data)
        if to is not None:
            self._to(to, frame)
            return
        joined = self._joined_peers()
        if len(joined) == sum(1 for peer in self.peers.values() if peer.up):
            self.send(frame)
            return
        for peer in joined:
            self._to(peer, frame)

    def command(self, name, args):
        """The local game client issued a command. Returns False if it must
        not run locally."""
        if name == BUILD_COMMAND:
            self._build_mode(bool(args) and str(args[0]) not in ("0", "False", "false"))
        if self.role == HOST and self.holding and name in CLOCK_NAMES:
            self._log("{} refused: the other player is loading".format(name))
            held = self._held_for(but=self._executing)
            if self._executing is not None and held:
                # The guest pressed it, while the game is held for another.
                self._tell_guest(self._executing, "the game stays paused while {} {} loading".format(
                    _names(held), "is" if len(held) == 1 else "are"))
            return False
        if self.role == GUEST and name.startswith(CAS_PREFIXES) and self._muted:
            # It would open Create-a-Sim over a host's world we are loading.
            self._tell(CAS_REFUSED)
            return False
        if self._cas is not None:
            # On its way to Create-a-Sim: our own simulation answers.
            return True
        if self._muted:
            return not (_forwarded(name) or name.startswith(BLOCK_PREFIXES))
        if self.role != GUEST or not self.live:
            return True
        if _forwarded(name):
            self.forwarded += 1
            self.send(frames.command(name, args))
            return False
        if name.startswith(BLOCK_PREFIXES):
            return False
        if name not in self._seen_local:
            # A record of what still runs against the muted local simulation.
            self._seen_local.add(name)
            self._log("local command while live: {}".format(name))
        return True


def _same_place(a, b):
    """Whether two (save, lot, zone) of `Peer.place` are one lot of one
    save: by lot where both name it, which every save agrees on, else by
    zone."""
    if a is None or b is None or a[0] != b[0]:
        return False
    if a[1] and b[1]:
        return a[1] == b[1]
    return bool(a[2]) and a[2] == b[2]


def _names(peers):
    """The names of guests, for the player of another."""
    return ", ".join(peer.name or "another player" for peer in peers)


def _numbers(text):
    """The numbers in a command line, as sim ids would be."""
    return [number for number in (frames.number(word) for word in text.split()) if number]


def _forwarded(name):
    return name in FORWARD_NAMES or name.startswith(FORWARD_PREFIXES)


def _remote_field(name):
    """What the guest knows of its host, or the host of its one guest."""
    return property(lambda self: getattr(self._remote, name),
                    lambda self, value: setattr(self._remote, name, value))


for _name, _field in (("remote_in_zone", "in_zone"), ("remote_zone", "zone"), ("remote_lot", "lot"),
                      ("remote_save", "save"), ("remote_world", "world"), ("remote_sim_id", "sim_id"),
                      ("remote_building", "building"), ("_remote_version", "version"),
                      ("_reported_sim_id", "reported_sim_id"), ("_transfers", "transfers")):
    setattr(Session, _name, _remote_field(_field))
del _name, _field

