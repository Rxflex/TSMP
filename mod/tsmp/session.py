"""What each side of a multiplayer session does with messages and commands.

The host's simulation is the only one that counts. Its output goes to its own
game client and, as MSG frames, to the guest's game client. The guest's
simulation keeps running but is muted; commands from the guest's game client
are sent to the host as COMMAND frames and executed there.

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
        active_sim_id()                id of the locally selected sim, or 0
        deliver(msg_id, data)          hand a message to the local game client
        execute(name, args, sim_id)    run a command as the remote player
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
        self._reported_sim_id = 0
        self._remote_depth = 0
        self._was_live = False
        self._seen_local = set()
        self.forwarded = 0
        self.executed = 0
        self.relayed = 0
        self.delivered = 0

    @property
    def live(self):
        """True while the two games are joined: messages flow host -> guest
        and commands guest -> host."""
        return self.role is not None and self.remotes > 0 and self._in_zone and self.remote_in_zone

    # -- connection to the launcher -------------------------------------

    def bridge_up(self, role):
        self.role = role if role in (HOST, GUEST) else None
        self._forget_remote()
        self.remotes = 0
        self._in_zone = bool(self._game.in_zone())
        self._announce(want=True)
        self._log("bridge connected, role {}".format(self.role))
        self._check_live()

    def bridge_down(self, reason=""):
        self._log("bridge lost: {}".format(reason))
        self.role = None
        self.remotes = 0
        self._forget_remote()
        self._check_live()

    def _forget_remote(self):
        self.remote_in_zone = False
        self.remote_sim_id = 0
        self._reported_sim_id = 0

    def _announce(self, want=False):
        """Tell the other game our state. `want` asks it to answer with its
        own, which a game that has just (re)connected needs."""
        if self.role is None:
            return
        if want:
            self._send(frames.control("state", role=self.role, in_zone=self._in_zone, want=True))
        else:
            self._send(frames.control("state", role=self.role, in_zone=self._in_zone))

    def _check_live(self):
        live = self.live
        if live == self._was_live:
            return
        self._was_live = live
        self._log("session is live as {}".format(self.role) if live else "session is not live")
        if live and self.role == GUEST:
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
        in_zone = bool(self._game.in_zone())
        if in_zone != self._in_zone:
            self._in_zone = in_zone
            # The host forgets our sim when we leave a zone.
            self._reported_sim_id = 0
            self._announce()
            self._check_live()
        if self.role == GUEST and self.live:
            self._report_sim()

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

    def _control(self, message):
        kind = message.get("type")
        if kind == "link":
            self.remotes = int(message.get("remotes") or 0)
            if self.remotes == 0:
                self._forget_remote()
            # Whoever just connected has not heard our state yet, nor we theirs.
            if self.remotes:
                self._announce(want=True)
        elif kind == "gone":
            self._forget_remote()
        elif kind == "state":
            if message.get("role") == self.role:
                self._log("the other game is also a {}; ignoring it".format(self.role))
                return
            if message.get("want"):
                self._announce()
            self.remote_in_zone = bool(message.get("in_zone"))
            if not self.remote_in_zone:
                self.remote_sim_id = 0
                self._reported_sim_id = 0
        elif kind == "active_sim":
            try:
                self.remote_sim_id = int(message.get("sim_id") or 0)
            except (TypeError, ValueError):
                self.remote_sim_id = 0
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
        if not self.live:
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
