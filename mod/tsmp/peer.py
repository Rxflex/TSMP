"""One other game of a session, as this one knows it.

A guest has one: the host. A host has one for each guest its launcher
relays for, under the number the launcher gave it (see tsmp/frames.py,
ROUTED). The launcher keeps that number for a player who comes back, so what
the host keeps for a guest whose link dropped, such as the hold of a guest
that is loading, is still there when it does.

A launcher from before version 3 names no guest: everything it relays comes
from, and goes to, its one remote, kept here under LEGACY.

Pure Python, no game imports.
"""

from tsmp import transfer

# The peer of a launcher that names none; also, in a ROUTED frame, every
# guest at once.
LEGACY = 0


class Peer:
    """`send(frame, to)` queues a packed frame for the peer numbered `to`;
    `clock()` tells the time in seconds."""

    def __init__(self, ident, send, clock, name="", player=""):
        self.ident = ident
        # Who it is, as its launcher says; "" from a launcher that does not.
        self.name = name
        self.player = player
        # Its launcher is connected to ours.
        self.up = False
        # Its transfers carry the ids of its own requests, which do not
        # mean anything to another peer. The peer's number is read at each
        # send, as a remote that a launcher before version 3 relayed keeps
        # what it had once a newer one names it.
        self.transfers = transfer.Transfers(lambda frame: send(frame, self.ident), clock)
        self.forget()
        # Host: the request being answered, (id, zone, lot), and the one to
        # answer once the snapshot under way is taken.
        self.saving = None
        self.asked = None
        # Host: sims the guest travels to our lot with, and the household it
        # said it plays when it last asked for our state.
        self.travellers = []
        self.household = 0
        # Host: the guest plays a family of its own, brought in from a save
        # of its own (see Session.cross_save). Its lots are not ours, and
        # what it plays is only its household.
        self.cross = False
        # Host: until when the game is held paused for the guest, which
        # loads; how far our game was when the hold began, and whether the
        # hold ran out with the guest perhaps still loading what we saved,
        # checked once a second.
        self.hold_until = 0.0
        self.held_at = ""
        self.lapsed = False
        self.looked_at = 0.0
        # Host: the request we hold for, and how far our game was when its
        # snapshot was taken.
        self.held_for = 0
        self.taken_at = ""
        # Host: how often the guest was sent back to loading since it was
        # last joined, as our game moved on while it loaded; and, once it
        # was let in all the same, what had moved (see Session._state).
        self.moved_on = 0
        self.drift = ""
        # Host: we sent the guest back to loading, and it has not said since
        # that it left its zone for it.
        self.bumped = False
        # Host: since when the link has been down while we hold, 0 while up.
        self.link_lost_at = 0.0
        # Host: sims made the guest's on our lot, and those it plays on
        # another, which our lot is to be kept free of; and the world our
        # lot was last cleared of them in.
        self.welcomed = []
        self.away = []
        self.sent_off_in = ""
        # Host: how often our game moved on from a state saved for this
        # guest alone, which it is not to join on (see Session._world_for);
        # 0 again with every load of our zone.
        self.epoch = 0
        # Host: whether it was joined at the last look, to see it join.
        self.was_live = False
        # Host: who it was last told builds on our lot with it, by name.
        self.building_sent = ()
        # Host: for the last states sent to it, by request, how many
        # households and looks our game had taken in when their save began,
        # what it gives once it loaded one being as new as that, and from
        # which arrival on the lots apart it plays count (see
        # Session._merge). A state sent may never be loaded.
        self.bases = {}
        self.forget_places()

    @property
    def label(self):
        """What the host's log calls it: its name, else its number; nothing
        for the one remote of a launcher that names none."""
        return self.name or ("guest {}".format(self.ident) if self.ident != LEGACY else "")

    def forget(self):
        """What the other game said of itself is no longer true: it is gone,
        or cannot be reached."""
        self.in_zone = False
        self.zone = 0
        self.lot = ""
        self.save = 0
        # The world its game client shows.
        self.world = ""
        # The sim its player selected.
        self.sim_id = 0
        self.building = False
        # The version of the mod it speaks, None until it says: it is taken
        # to be ours until then.
        self.version = None
        # The sim we last told it our player selected.
        self.reported_sim_id = 0
        # Guest: who the host said builds on its lot besides us, by name.
        self.builders = ()

    def forget_places(self):
        """Host: where the guest played apart from us. Not forgotten when
        its link drops: a guest that was first on a lot is still first once
        back on it, and one gone for good stands in nobody's way, as only
        guests whose links are up count (Session._first_on)."""
        # (save, lot, zone) of the lot apart from ours it is on, None on
        # ours or when not known; and how many arrivals on such lots came
        # before it came there.
        self.place = None
        self.place_order = 0
        # (place, place_order) of each lot apart from ours it played
        # since the last state of ours sent to it, which what it gives
        # carries: also once it came to our lot with it.
        self.played = []

    def reset(self):
        """Our launcher is gone: so is everything under way with the peer.
        What its sims are doing, which our game does not forget, stays."""
        self.up = False
        self.forget()
        self.transfers.forget()
        self.saving = None
        self.asked = None
        self.hold_until = 0.0
        self.lapsed = False
        self.welcomed = []
        self.travellers = []
        self.household = 0
        self.cross = False
        self.epoch = 0
        self.moved_on = 0
        self.drift = ""
        self.bumped = False
        self.building_sent = ()
        self.forget_places()
        # Another launcher may give this number to another player, and what
        # was sent through the old one is not known to have been loaded.
        self.bases = {}

    def __repr__(self):
        return "Peer({}{})".format(self.ident, " " + self.name if self.name else "")
