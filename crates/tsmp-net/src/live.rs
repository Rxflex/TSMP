//! The live session: a transparent relay between the mod in the local game
//! and the launcher on the other machine.
//!
//! ```text
//! game + mod <--127.0.0.1--> Relay <======LAN======> Relay <--127.0.0.1--> game + mod
//! ```
//!
//! Both ends of the relay speak the mod's frame format (see
//! `mod/tsmp/frames.py`): `kind u8, id u32, length u32, payload`, little
//! endian. Frames from the game go to every remote and frames from a remote
//! go to the game, untouched. The relay adds control frames of its own:
//!
//! * `{"type":"link","remotes":N}` to the game when it connects and whenever
//!   the number of remotes changes;
//! * on the host only, `{"type":"peer_up","peer":P,"name":..,"player":..}`
//!   and `{"type":"peer_gone","peer":P}` to the game, just before the `link`
//!   that counts the remote joining or leaving (and `peer_up` for each remote
//!   there is when the game connects);
//! * `{"type":"gone"}` to the remotes when the game disconnects;
//! * `{"type":"beat"}` between the relays, which never reaches a game. A relay
//!   sends one to a remote as soon as it joins and whenever it has had nothing
//!   else to send it for two seconds. A remote that has beaten and then sends
//!   nothing at all for fifteen seconds is dropped, as is one that takes
//!   nothing written to it for thirty. The connection may run through a
//!   session server that acknowledges every byte itself, so without this a
//!   far end that is gone would only be noticed after minutes, if at all.
//!
//! It also reads the `state` control frames passing through to know whether
//! each game is in a zone, for display, and which version of the session
//! protocol the local game's mod speaks.
//!
//! The host's relay takes one remote per player, up to the session's
//! `max_players - 1`; a guest's has one remote, the host. Each player is
//! given a [`PeerId`] the first time they join, and keeps it for as long as
//! the relay runs, through every reconnection. A remote of a player already
//! there replaces the old connection; one of a new player is refused while the
//! session is full.
//!
//! Once the host's game says it speaks version 3 or later, the host's relay
//! tells it who sent what: every frame from a remote reaches it wrapped in a
//! [`KIND_ROUTED`] frame whose id is the remote's peer and whose payload is
//! the whole frame as the remote sent it. The game wraps a frame for one
//! guest the same way, and the relay sends the inner frame to that guest
//! only; a frame not wrapped goes to every guest, as before. Nothing changes
//! on the wire between the launchers, nor between a guest and its game: what
//! a guest's game sends and receives is what it sent and received before:
//! neither relay passes a routed frame between the launchers, nor takes one
//! from a guest's game. A mod of an older version is never sent a wrapped
//! frame, and with it the host takes one guest, as it did then. Until the
//! host's game has said its version, what comes from the remotes waits for
//! it, so that it is wrapped or not as the game reads it; once it says a
//! version from before, the remotes past the first one are dropped.
//!
//! What waits to be written is bounded for each connection, and on the host
//! for all its remotes together, since what its game sends goes to each.

use std::collections::HashMap;
use std::io::{self, ErrorKind, Read, Write};
use std::net::{Ipv4Addr, Shutdown, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::protocol::{Hello, Message, PROTOCOL_VERSION, expect_message, write_message};
use crate::route::Route;
use crate::{Error, POLL, Result, sleep_unless};

pub const KIND_MSG: u8 = 1;
pub const KIND_COMMAND: u8 = 2;
pub const KIND_CONTROL: u8 = 3;
/// A frame from or to one guest, between the host's relay and the host's
/// game only: the id is the guest's [`PeerId`], the payload a whole frame,
/// header and all. From the game, peer [`BROADCAST`] means every guest.
pub const KIND_ROUTED: u8 = 8;

/// One player in the host's session, as its relay and game know them: small,
/// starting from 1, and the same each time the same player reconnects.
pub type PeerId = u32;
/// A routed frame from the game to this peer goes to every guest.
pub const BROADCAST: PeerId = 0;
/// The first version of the mod's session protocol (`PROTOCOL` in
/// `mod/tsmp/session.py`) that reads [`KIND_ROUTED`] frames.
pub const ROUTED_SINCE: u32 = 3;

const HEADER: usize = 9;
const MAX_PAYLOAD: usize = 64 << 20;

/// The longest payload a frame of `kind` may have: a routed frame holds a
/// whole frame of the longest kind and its header.
fn max_payload(kind: u8) -> usize {
    if kind == KIND_ROUTED { MAX_PAYLOAD + HEADER } else { MAX_PAYLOAD }
}
/// A game that is loading does not read. Frames wait for it up to this many
/// bytes; past that the peer is dropped rather than exhausting memory.
const MAX_QUEUED: usize = 256 << 20;
/// What may wait for all the remotes together. What the host's game sends
/// goes to every guest, so without this the host would hold a copy of it for
/// each one that reads slowly, up to [`MAX_QUEUED`] apiece. Past it the
/// remote furthest behind is dropped; it dials again. With one remote,
/// [`MAX_QUEUED`] comes first.
const MAX_QUEUED_REMOTES: usize = 512 << 20;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);
const REDIAL: Duration = Duration::from_secs(2);
/// Redials that keep failing slow down to this: a host that stays away is
/// still found soon after it is back, without knocking on a session server
/// many times a minute meanwhile.
const MAX_REDIAL: Duration = Duration::from_secs(16);

/// How long to wait before the next dial after `failures` failed ones in a
/// row.
fn redial_after(failures: u32) -> Duration {
    REDIAL.saturating_mul(1 << failures.min(4)).min(MAX_REDIAL)
}
/// Lines of the mods' logs kept for display.
const LOG_LINES: usize = 200;
/// How long a relay may have nothing to send a remote before it sends a beat.
const BEAT: Duration = Duration::from_secs(2);
/// How long a remote that beats may send nothing before it counts as gone:
/// several beats lost in a row, more than a busy network drops.
const SILENCE: Duration = Duration::from_secs(15);
/// How long a write to a remote may make no progress before the remote counts
/// as gone. Its relay reads all the time, whatever its game does, so a stall
/// this long means the far end is not there to read.
const STALL: Duration = Duration::from_secs(30);

/// How the relay tells a remote that is there from one that is gone. Remotes
/// may be joined through a session server that splices the connection and
/// acknowledges every byte itself, so TCP never learns that the far launcher
/// vanished; only traffic from the launcher itself shows it is still there.
/// The game on the loopback interface gets none of this: a game that is
/// loading does not read for minutes and is not gone.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Timing {
    beat: Duration,
    silence: Duration,
    stall: Duration,
}

impl Default for Timing {
    fn default() -> Self {
        Self {
            beat: BEAT,
            silence: SILENCE,
            stall: STALL,
        }
    }
}

/// What the relay knows about the session, for display.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LiveStatus {
    /// The mod in the local game is connected.
    pub game_connected: bool,
    /// The local game has a zone loaded.
    pub game_in_zone: bool,
    /// The local game is getting in step with the other: loading its state
    /// or giving it a household.
    pub game_syncing: bool,
    /// The local game says it is joined to the other.
    pub game_joined: bool,
    /// The local game says the two play on different lots or saves.
    pub game_apart: bool,
    /// The local game gave up joining the other's save until a save is
    /// loaded again.
    pub game_stopped: bool,
    /// Launchers on other machines connected to this one, in the order they
    /// joined: on a guest, the host's alone.
    pub remotes: Vec<RemoteStatus>,
}

/// One launcher on another machine connected to this one, and what its game
/// says of itself.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RemoteStatus {
    pub peer: PeerId,
    /// The player's id ([`Hello::player`]); none for a launcher from before
    /// players had one.
    pub player: Option<String>,
    pub name: String,
    /// The other game has a zone loaded.
    pub in_zone: bool,
    pub syncing: bool,
    pub joined: bool,
    pub apart: bool,
    pub stopped: bool,
}

impl LiveStatus {
    /// The local game and every other one say they are joined.
    pub fn is_live(&self) -> bool {
        self.game_connected && self.game_joined && !self.remotes.is_empty() && self.remotes.iter().all(|r| r.joined)
    }

    /// How many of the other games say they are joined.
    pub fn joined_count(&self) -> usize {
        self.remotes.iter().filter(|remote| remote.joined).count()
    }
}

/// What a game says of itself in its `state` control frames.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct GameState {
    in_zone: bool,
    syncing: bool,
    joined: bool,
    apart: bool,
    stopped: bool,
    /// The session protocol its mod speaks: 0 before a `state` frame came, 1
    /// for a mod too old to say.
    version: u32,
}

/// Builds a whole frame.
pub fn frame(kind: u8, id: u32, payload: &[u8]) -> Vec<u8> {
    let mut frame = Vec::with_capacity(HEADER + payload.len());
    frame.push(kind);
    frame.extend_from_slice(&id.to_le_bytes());
    frame.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    frame.extend_from_slice(payload);
    frame
}

fn control(value: serde_json::Value) -> Vec<u8> {
    frame(KIND_CONTROL, 0, value.to_string().as_bytes())
}

/// `inner`, a whole frame, wrapped for or from `peer`.
pub fn routed(peer: PeerId, inner: &[u8]) -> Vec<u8> {
    frame(KIND_ROUTED, peer, inner)
}

/// The frame inside a [`KIND_ROUTED`] one, and the peer it is for. `None` for
/// any other frame, or one whose payload is not exactly one frame that is not
/// routed itself.
fn unwrap_routed(frame: &[u8]) -> Option<(PeerId, &[u8])> {
    if frame.first() != Some(&KIND_ROUTED) {
        return None;
    }
    let peer = u32::from_le_bytes(frame[1..5].try_into().ok()?);
    let inner = frame.get(HEADER..)?;
    let header = inner.get(..HEADER)?;
    let length = u32::from_le_bytes([header[5], header[6], header[7], header[8]]) as usize;
    let whole = inner.len() == HEADER + length && length <= MAX_PAYLOAD && header[0] != KIND_ROUTED;
    whole.then_some((peer, inner))
}

fn beat() -> Vec<u8> {
    control(serde_json::json!({"type": "beat"}))
}

/// Reads one whole frame, header included. `Ok(None)` when the stream ended
/// between frames. A read timeout is an error wherever it strikes, never the
/// end of the stream, and the frame it interrupted is lost.
pub fn read_frame(reader: &mut impl Read) -> io::Result<Option<Vec<u8>>> {
    let mut header = [0u8; HEADER];
    let mut filled = 0;
    while filled < HEADER {
        match reader.read(&mut header[filled..]) {
            Ok(0) if filled == 0 => return Ok(None),
            Ok(0) => return Err(ErrorKind::UnexpectedEof.into()),
            Ok(read) => filled += read,
            Err(e) if e.kind() == ErrorKind::Interrupted => {}
            Err(e) => return Err(e),
        }
    }
    let length = u32::from_le_bytes([header[5], header[6], header[7], header[8]]) as usize;
    if length > max_payload(header[0]) {
        return Err(io::Error::new(ErrorKind::InvalidData, "frame too large"));
    }
    let mut frame = vec![0u8; HEADER + length];
    frame[..HEADER].copy_from_slice(&header);
    reader.read_exact(&mut frame[HEADER..])?;
    Ok(Some(frame))
}

/// Whether a read gave up because its timeout passed: `TimedOut` on Windows,
/// `WouldBlock` elsewhere.
fn timed_out(error: &io::Error) -> bool {
    matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut)
}

/// Reads frames from a stream whose reads time out now and then. Unlike
/// [`read_frame`], a timeout loses nothing: a frame it cut in two is finished
/// by the next call.
struct FrameReader<R> {
    reader: R,
    /// Whether a routed frame, a header longer than any other, may come:
    /// only from the host's game. A remote never sends one, and one read from
    /// it is no longer than any other frame, so that wrapped by the relay it
    /// is still one the game can read.
    routed: bool,
    /// The start of the next frame, or more than one.
    pending: Vec<u8>,
    chunk: Box<[u8]>,
    /// When the stream last gave bytes.
    heard: Instant,
}

impl<R: Read> FrameReader<R> {
    fn new(reader: R, routed: bool) -> Self {
        Self {
            reader,
            routed,
            pending: Vec::new(),
            chunk: vec![0; 1 << 16].into_boxed_slice(),
            heard: Instant::now(),
        }
    }

    /// The next whole frame, header included. `Ok(None)` when the stream
    /// ended between frames; a timed-out read is returned as its error.
    fn next(&mut self) -> io::Result<Option<Vec<u8>>> {
        loop {
            if let Some(length) = self.complete()? {
                let rest = self.pending.split_off(length);
                return Ok(Some(std::mem::replace(&mut self.pending, rest)));
            }
            match self.reader.read(&mut self.chunk) {
                Ok(0) if self.pending.is_empty() => return Ok(None),
                Ok(0) => return Err(ErrorKind::UnexpectedEof.into()),
                Ok(read) => {
                    self.heard = Instant::now();
                    self.pending.extend_from_slice(&self.chunk[..read]);
                }
                Err(e) if e.kind() == ErrorKind::Interrupted => {}
                Err(e) => return Err(e),
            }
        }
    }

    /// The length of the frame at the start of `pending`, once all of it is there.
    fn complete(&self) -> io::Result<Option<usize>> {
        let Some(header) = self.pending.get(..HEADER) else {
            return Ok(None);
        };
        let length = u32::from_le_bytes([header[5], header[6], header[7], header[8]]) as usize;
        let limit = if self.routed { max_payload(header[0]) } else { MAX_PAYLOAD };
        if length > limit {
            return Err(io::Error::new(ErrorKind::InvalidData, "frame too large"));
        }
        Ok((self.pending.len() >= HEADER + length).then_some(HEADER + length))
    }
}

/// One connection of the relay. Writes go through a queue and a thread of
/// their own so a peer that is slow to read never holds up the others.
struct Connection {
    id: u64,
    stream: TcpStream,
    queue: Sender<Vec<u8>>,
    queued: Arc<AtomicUsize>,
    /// Set once the relay is done with the peer. Its reader thread looks at
    /// it between reads: on Windows, shutting a socket down does not wake a
    /// read on another handle of it.
    closed: Arc<AtomicBool>,
}

impl Connection {
    /// `timing` is given for a remote and `None` for the game.
    fn new(id: u64, stream: &TcpStream, timing: Option<Timing>) -> io::Result<Self> {
        // Options are set on the handle they are for: on Windows they do not
        // carry over to other handles of the same socket.
        let mut writer = stream.try_clone()?;
        if let Some(timing) = timing {
            writer.set_write_timeout(Some(timing.stall))?;
        }
        let stream = stream.try_clone()?;
        let (queue, frames) = mpsc::channel::<Vec<u8>>();
        let queued = Arc::new(AtomicUsize::new(0));
        let closed = Arc::new(AtomicBool::new(false));
        let beats = timing.map(|timing| timing.beat);
        thread::spawn({
            let queued = queued.clone();
            let closed = closed.clone();
            move || {
                while let Some((frame, from_queue)) = next_frame(&frames, beats) {
                    let written = writer.write_all(&frame);
                    queued.fetch_sub(from_queue, Ordering::Relaxed);
                    if written.is_err() {
                        closed.store(true, Ordering::Relaxed);
                        let _ = writer.shutdown(Shutdown::Both);
                        break;
                    }
                }
            }
        });
        let peer = Self {
            id,
            stream,
            queue,
            queued,
            closed,
        };
        if timing.is_some() {
            // Straight away, so the far end learns that this launcher beats
            // even if the game keeps it too busy to need another for long.
            peer.send(beat());
        }
        Ok(peer)
    }

    fn send(&self, frame: Vec<u8>) {
        if self.queued.load(Ordering::Relaxed) + frame.len() > MAX_QUEUED {
            // Its reader thread sees the shutdown and removes the peer.
            self.close();
            return;
        }
        self.queued.fetch_add(frame.len(), Ordering::Relaxed);
        let _ = self.queue.send(frame);
    }

    fn close(&self) {
        self.closed.store(true, Ordering::Relaxed);
        let _ = self.stream.shutdown(Shutdown::Both);
    }

    /// The bytes waiting to be written, none once the connection is closed:
    /// those are never written, and the connection is on its way out.
    fn waiting(&self) -> usize {
        if self.closed.load(Ordering::Relaxed) { 0 } else { self.queued.load(Ordering::Relaxed) }
    }
}

/// Waits for the next frame to write to a peer: a queued one or, when `beats`
/// is given, a beat once nothing else has come for that long. Also returns how
/// many bytes of the queue the frame was, which is none for a beat. `None` once
/// the peer is gone.
fn next_frame(frames: &Receiver<Vec<u8>>, beats: Option<Duration>) -> Option<(Vec<u8>, usize)> {
    let frame = match beats {
        None => frames.recv().ok()?,
        Some(idle) => match frames.recv_timeout(idle) {
            Ok(frame) => frame,
            Err(RecvTimeoutError::Timeout) => return Some((beat(), 0)),
            Err(RecvTimeoutError::Disconnected) => return None,
        },
    };
    let length = frame.len();
    Some((frame, length))
}

/// A launcher on another machine, joined to the relay.
struct Remote {
    connection: Connection,
    peer: PeerId,
    player: Option<String>,
    name: String,
    game: GameState,
}

impl Remote {
    fn status(&self) -> RemoteStatus {
        let game = self.game;
        RemoteStatus {
            peer: self.peer,
            player: self.player.clone(),
            name: self.name.clone(),
            in_zone: game.in_zone,
            syncing: game.syncing,
            joined: game.joined,
            apart: game.apart,
            stopped: game.stopped,
        }
    }

    fn peer_up(&self) -> Vec<u8> {
        control(serde_json::json!({
            "type": "peer_up",
            "peer": self.peer,
            "name": self.name,
            "player": self.player,
        }))
    }
}

/// Which end of a session the relay is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Role {
    /// Takes a remote for each guest, up to `max_players - 1`, and tells its
    /// game which guest is which.
    Host { max_players: usize },
    /// Has one remote, the host's launcher. A new connection to it replaces
    /// the old one, and nothing the game sends or hears changes.
    Guest,
}

/// Why the host's relay does not take a remote.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refusal {
    /// Every place in the session is taken.
    Full,
    /// A launcher from before player ids asks while there are others, or is
    /// there while another asks. There may be room, but one of the two has
    /// to update TSMP first.
    Legacy,
}

/// Told of every remote that joins or leaves the host's relay. A remote that
/// replaces another of the same player joins without the other leaving.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RemoteEvent {
    Joined { peer: PeerId, player: Option<String> },
    Left { peer: PeerId },
}

pub(crate) type OnRemote = Box<dyn Fn(RemoteEvent) + Send + Sync>;

#[derive(Default)]
struct State {
    game: Option<Connection>,
    /// What the game said of itself last.
    game_state: GameState,
    remotes: Vec<Remote>,
    /// The peer of every player who has joined, so that one who comes back
    /// is the same peer. Players from before ids are all one player, `None`.
    /// Ids are never handed out twice, and never taken back: a session sees
    /// a handful of players.
    peers: HashMap<Option<String>, PeerId>,
    next_id: u64,
    /// Host: what is for a game that has connected and not yet said which
    /// version it speaks, so that what came from the remotes cannot be
    /// wrapped or not yet. Each is a frame from the remote of the peer, or
    /// with none one of the relay's own, kept behind the others so that the
    /// game hears all of it in order. A mod sends its `state` as soon as it
    /// connects, but reads nothing before its game's next tick either.
    held: Vec<(Option<PeerId>, Vec<u8>)>,
    held_bytes: usize,
    /// The latest lines both mods logged, and how many there have been.
    log: std::collections::VecDeque<String>,
    logged: u64,
}

impl State {
    fn link(&self) -> Vec<u8> {
        control(serde_json::json!({"type": "link", "remotes": self.remotes.len()}))
    }

    fn status(&self) -> LiveStatus {
        let game = self.game_state;
        LiveStatus {
            game_connected: self.game.is_some(),
            game_in_zone: game.in_zone,
            game_syncing: game.syncing,
            game_joined: game.joined,
            game_apart: game.apart,
            game_stopped: game.stopped,
            remotes: self.remotes.iter().map(Remote::status).collect(),
        }
    }

    fn peer_for(&mut self, player: &Option<String>) -> PeerId {
        let next = self.peers.len() as PeerId + 1;
        *self.peers.entry(player.clone()).or_insert(next)
    }

    /// Whether the game reads routed frames.
    fn routes(&self, role: Role) -> bool {
        matches!(role, Role::Host { .. }) && self.game_state.version >= ROUTED_SINCE
    }

    /// Whether what goes to the game waits until it says its version.
    fn holds(&self, role: Role) -> bool {
        matches!(role, Role::Host { .. }) && self.game.is_some() && self.game_state.version == 0
    }

    /// Sends `frame` to the game, as from the remote of `from` or, with none,
    /// as the relay's own. On the host a frame from a remote is wrapped for a
    /// game that reads routed frames, and waits for one that has not said
    /// yet whether it does.
    fn send_to_game(&mut self, role: Role, from: Option<PeerId>, frame: Vec<u8>) {
        let Some(game) = &self.game else { return };
        if self.holds(role) && (from.is_some() || !self.held.is_empty()) {
            if self.held_bytes + frame.len() > MAX_QUEUED {
                // A game that says nothing for this long reads nothing
                // either, and goes as one would whose queue is full.
                game.close();
                return;
            }
            self.held_bytes += frame.len();
            self.held.push((from, frame));
            return;
        }
        match from {
            Some(peer) if self.routes(role) => game.send(routed(peer, &frame)),
            _ => game.send(frame),
        }
    }

    /// The game has said which version it speaks: what waited for that goes
    /// to it now. A game of a version from before several guests has one,
    /// the remote that joined first. The others, let in while its version
    /// was not known, are dropped with what they sent; they dial again, and
    /// are refused while the first is there.
    fn release(&mut self, role: Role) {
        let mut held = std::mem::take(&mut self.held);
        self.held_bytes = 0;
        if matches!(role, Role::Host { .. }) && self.game_state.version < ROUTED_SINCE {
            for extra in self.remotes.iter().skip(1) {
                extra.connection.close();
            }
            let first = self.remotes.first().map(|remote| remote.peer);
            held.retain(|(from, _)| from.is_none() || *from == first);
        }
        for (from, frame) in held {
            self.send_to_game(role, from, frame);
        }
    }

    /// Queues `frame` for the remote at `index`, within what all the remotes
    /// may have waiting together: past that the remote furthest behind is
    /// dropped, until it fits.
    fn send_to_remote(&self, index: usize, frame: Vec<u8>) {
        let mut total: usize = self.remotes.iter().map(|remote| remote.connection.waiting()).sum();
        while total + frame.len() > MAX_QUEUED_REMOTES {
            let Some(slowest) = self.remotes.iter().max_by_key(|remote| remote.connection.waiting()) else {
                break;
            };
            total -= slowest.connection.waiting();
            slowest.connection.close();
        }
        let connection = &self.remotes[index].connection;
        if !connection.closed.load(Ordering::Relaxed) {
            connection.send(frame);
        }
    }

    /// Whether a remote of `player` may join now. `also` are players the
    /// host has in its lobby, which count as in the session too.
    ///
    /// A player already here always may: it is the same one reconnecting,
    /// and replaces its old connection. Anyone else needs a free place, of
    /// `max_players - 1`, or of one while the host's game speaks a version of
    /// the session from before several guests. A launcher from before player
    /// ids cannot be told from another of its kind, so it is taken only while
    /// it would be the one guest, and nobody else is while it is there.
    fn admits(&self, role: Role, player: &Option<String>, also: &[Option<String>]) -> std::result::Result<(), Refusal> {
        let Role::Host { max_players } = role else {
            return Ok(());
        };
        let mut present: Vec<&Option<String>> = self.remotes.iter().map(|r| &r.player).chain(also).collect();
        present.sort();
        present.dedup();
        if present.contains(&player) {
            return Ok(());
        }
        if (player.is_none() && !present.is_empty()) || present.contains(&&None) {
            return Err(Refusal::Legacy);
        }
        let legacy = (1..ROUTED_SINCE).contains(&self.game_state.version);
        let room = if legacy { 1 } else { max_players.saturating_sub(1) };
        if present.len() < room { Ok(()) } else { Err(Refusal::Full) }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Side {
    Game,
    Remote,
}

struct Inner {
    stop: AtomicBool,
    state: Mutex<State>,
    timing: Timing,
    role: Role,
    on_remote: Option<OnRemote>,
}

impl Inner {
    fn attach_game(self: &Arc<Self>, stream: TcpStream) -> io::Result<()> {
        let _ = stream.set_nodelay(true);
        let mut state = self.state.lock().unwrap();
        if self.stop.load(Ordering::Relaxed) {
            let _ = stream.shutdown(Shutdown::Both);
            return Ok(());
        }
        state.next_id += 1;
        let id = state.next_id;
        let connection = Connection::new(id, &stream, None)?;
        let closed = connection.closed.clone();
        // A game that reconnects without the old socket having died.
        if let Some(old) = state.game.replace(connection) {
            old.close();
        }
        state.game_state = GameState::default();
        // What waited for the old one is of a world it took with it.
        state.held.clear();
        state.held_bytes = 0;
        if matches!(self.role, Role::Host { .. }) {
            let ups: Vec<Vec<u8>> = state.remotes.iter().map(Remote::peer_up).collect();
            for up in ups {
                state.send_to_game(self.role, None, up);
            }
        }
        let link = state.link();
        state.send_to_game(self.role, None, link);
        drop(state);
        self.spawn_reader(Side::Game, id, stream, None, closed);
        Ok(())
    }

    fn attach_remote(self: &Arc<Self>, stream: TcpStream, player: Option<String>, name: String) -> Result<PeerId> {
        let _ = stream.set_nodelay(true);
        let mut state = self.state.lock().unwrap();
        if self.stop.load(Ordering::Relaxed) {
            let _ = stream.shutdown(Shutdown::Both);
            return Err(Error::Protocol("the session is closed".into()));
        }
        if state.admits(self.role, &player, &[]).is_err() {
            let _ = stream.shutdown(Shutdown::Both);
            return Err(Error::Full);
        }
        state.next_id += 1;
        let id = state.next_id;
        let connection = Connection::new(id, &stream, Some(self.timing))?;
        let closed = connection.closed.clone();
        let peer = state.peer_for(&player);
        // A player who is here already is reconnecting; a guest's one remote
        // is the host, whoever answered.
        let role = self.role;
        state.remotes.retain(|old| {
            let replaced = role == Role::Guest || old.player == player;
            if replaced {
                old.connection.close();
            }
            !replaced
        });
        let remote = Remote {
            connection,
            peer,
            player: player.clone(),
            name,
            game: GameState::default(),
        };
        if matches!(role, Role::Host { .. }) {
            state.send_to_game(role, None, remote.peer_up());
        }
        state.remotes.push(remote);
        let link = state.link();
        state.send_to_game(role, None, link);
        if let (Role::Host { .. }, Some(on_remote)) = (role, &self.on_remote) {
            on_remote(RemoteEvent::Joined { peer, player });
        }
        drop(state);
        self.spawn_reader(Side::Remote, id, stream, Some(self.timing), closed);
        Ok(peer)
    }

    fn spawn_reader(
        self: &Arc<Self>,
        side: Side,
        id: u64,
        stream: TcpStream,
        timing: Option<Timing>,
        closed: Arc<AtomicBool>,
    ) {
        let inner = self.clone();
        thread::spawn(move || {
            inner.read(side, id, stream, timing, &closed);
            inner.detach(side, id);
        });
    }

    /// Forwards what a peer sends until it is gone: its connection ends or
    /// fails, the relay is done with it, or, for a remote that has shown it
    /// beats, nothing comes from it for the silence it is held to. A launcher
    /// from before beats is left to TCP to notice, as it was then.
    fn read(&self, side: Side, id: u64, stream: TcpStream, timing: Option<Timing>, closed: &AtomicBool) {
        // Short reads, so that a peer the relay closed is let go soon.
        if stream.set_read_timeout(Some(POLL)).is_err() {
            return;
        }
        let routed = side == Side::Game && matches!(self.role, Role::Host { .. });
        let mut frames = FrameReader::new(&stream, routed);
        let mut silence = None;
        loop {
            match frames.next() {
                Ok(Some(frame)) => {
                    if side == Side::Remote && is_beat(&frame) {
                        silence = timing.map(|timing| timing.silence);
                    }
                    if !self.forward(side, id, frame) {
                        break;
                    }
                }
                Err(e) if timed_out(&e) => {
                    let silent = silence.is_some_and(|silence| frames.heard.elapsed() >= silence);
                    if silent || closed.load(Ordering::Relaxed) || self.stop.load(Ordering::Relaxed) {
                        break;
                    }
                }
                Ok(None) | Err(_) => break,
            }
        }
        let _ = stream.shutdown(Shutdown::Both);
    }

    /// Returns false once the peer is no longer part of the relay.
    fn forward(&self, side: Side, id: u64, mut frame: Vec<u8>) -> bool {
        let mut state = self.state.lock().unwrap();
        if side == Side::Remote && (is_beat(&frame) || frame.first() == Some(&KIND_ROUTED)) {
            // A beat only shows that the remote is there; the game never
            // hears of it. Nor of a routed frame, which only the host's relay
            // and its game exchange: from a guest it could pass for what
            // another guest sent.
            return state.remotes.iter().any(|remote| remote.connection.id == id);
        }
        // What the game sends one guest is read as what it sends them all.
        let mut to = BROADCAST;
        if side == Side::Game && frame.first() == Some(&KIND_ROUTED) {
            let peer = match self.role {
                Role::Host { .. } => unwrap_routed(&frame).map(|(peer, _)| peer),
                // A guest's game has no guests to name.
                Role::Guest => None,
            };
            let Some(peer) = peer else {
                // Not a frame any game understands: nobody is sent it.
                return state.game.as_ref().map(|game| game.id) == Some(id);
            };
            to = peer;
            frame.drain(..HEADER);
        }
        let game_state = snoop_state(&frame);
        for line in snoop_log(&frame) {
            if state.log.len() == LOG_LINES {
                state.log.pop_front();
            }
            state.log.push_back(line);
            state.logged += 1;
        }
        match side {
            Side::Game => {
                if state.game.as_ref().map(|game| game.id) != Some(id) {
                    return false;
                }
                if let Some(game_state) = game_state {
                    let first = state.game_state.version == 0;
                    state.game_state = game_state;
                    if first {
                        state.release(self.role);
                    }
                }
                if to != BROADCAST {
                    // A peer that has left hears nothing of it.
                    if let Some(index) = state.remotes.iter().position(|remote| remote.peer == to) {
                        state.send_to_remote(index, frame);
                    }
                    return true;
                }
                let Some(last) = state.remotes.len().checked_sub(1) else {
                    return true;
                };
                for index in 0..last {
                    state.send_to_remote(index, frame.clone());
                }
                state.send_to_remote(last, frame);
            }
            Side::Remote => {
                let Some(remote) = state.remotes.iter_mut().find(|remote| remote.connection.id == id) else {
                    return false;
                };
                if remote.connection.closed.load(Ordering::Relaxed) {
                    // Dropped, and about to be gone: nothing more of it, as
                    // from a remote a game of an older version has no room for.
                    return false;
                }
                match game_state {
                    Some(game_state) => remote.game = game_state,
                    None if is_gone(&frame) => remote.game = GameState::default(),
                    None => {}
                }
                let peer = remote.peer;
                state.send_to_game(self.role, Some(peer), frame);
            }
        }
        true
    }

    fn detach(&self, side: Side, id: u64) {
        let mut state = self.state.lock().unwrap();
        match side {
            Side::Game => {
                if state.game.as_ref().map(|game| game.id) != Some(id) {
                    return;
                }
                state.game = None;
                state.game_state = GameState::default();
                state.held.clear();
                state.held_bytes = 0;
                for remote in &state.remotes {
                    remote.connection.send(control(serde_json::json!({"type": "gone"})));
                }
            }
            Side::Remote => {
                let Some(index) = state.remotes.iter().position(|remote| remote.connection.id == id) else {
                    // Replaced by a newer connection of the same player, or the relay is closed.
                    return;
                };
                let remote = state.remotes.remove(index);
                let host = matches!(self.role, Role::Host { .. });
                if host {
                    let gone = control(serde_json::json!({"type": "peer_gone", "peer": remote.peer}));
                    state.send_to_game(self.role, None, gone);
                }
                let link = state.link();
                state.send_to_game(self.role, None, link);
                if let (true, Some(on_remote)) = (host, &self.on_remote) {
                    on_remote(RemoteEvent::Left { peer: remote.peer });
                }
            }
        }
    }

    fn close(&self) {
        self.stop.store(true, Ordering::Relaxed);
        let mut state = self.state.lock().unwrap();
        if let Some(game) = state.game.take() {
            game.close();
        }
        for remote in state.remotes.drain(..) {
            remote.connection.close();
        }
        state.game_state = GameState::default();
        state.held.clear();
        state.held_bytes = 0;
    }
}

fn control_type(frame: &[u8]) -> Option<serde_json::Value> {
    if frame.first() != Some(&KIND_CONTROL) {
        return None;
    }
    serde_json::from_slice(&frame[HEADER..]).ok()
}

/// What a `state` control frame says. Mods before the session protocol's
/// second version only said `in_zone`, which then also meant joined, and the
/// first said no version.
fn snoop_state(frame: &[u8]) -> Option<GameState> {
    let value = control_type(frame)?;
    if value.get("type")?.as_str()? != "state" {
        return None;
    }
    let in_zone = value.get("in_zone")?.as_bool()?;
    let flag = |name: &str| value.get(name).and_then(|flag| flag.as_bool());
    Some(GameState {
        in_zone: flag("loaded").unwrap_or(in_zone),
        syncing: flag("syncing").unwrap_or(false),
        joined: flag("joined").unwrap_or(in_zone),
        apart: flag("apart").unwrap_or(false),
        stopped: flag("stopped").unwrap_or(false),
        version: value
            .get("v")
            .and_then(|v| v.as_u64())
            .map_or(1, |v| u32::try_from(v).unwrap_or(u32::MAX).max(1)),
    })
}

/// The lines of a `log` control frame: what a mod wrote to its log file.
fn snoop_log(frame: &[u8]) -> Vec<String> {
    let lines = || {
        let value = control_type(frame)?;
        if value.get("type")?.as_str()? != "log" {
            return None;
        }
        let lines = value.get("lines")?.as_array()?;
        Some(lines.iter().filter_map(|line| line.as_str().map(str::to_owned)).collect())
    };
    lines().unwrap_or_default()
}

fn is_control(frame: &[u8], name: &str) -> bool {
    control_type(frame).is_some_and(|value| value.get("type").and_then(|kind| kind.as_str()) == Some(name))
}

fn is_gone(frame: &[u8]) -> bool {
    is_control(frame, "gone")
}

fn is_beat(frame: &[u8]) -> bool {
    // Beats are many and tiny: a longer frame is not one and is not parsed here.
    frame.len() <= HEADER + 32 && is_control(frame, "beat")
}

/// A running relay. The mod connects to `port()` on the loopback interface;
/// remotes are added with `add_remote`. Dropping it closes every connection.
pub struct Relay {
    inner: Arc<Inner>,
    port: u16,
    thread: Option<JoinHandle<()>>,
}

impl Relay {
    /// The host's relay, for a session of up to `max_players`, the host
    /// included.
    pub fn host(max_players: usize) -> Result<Self> {
        Self::with(Role::Host { max_players }, Timing::default(), None)
    }

    /// A guest's relay, whose one remote is the host.
    pub fn guest() -> Result<Self> {
        Self::with(Role::Guest, Timing::default(), None)
    }

    /// A relay that keeps its remotes to `timing`, which tests shorten, and
    /// on the host tells `on_remote` of each that joins or leaves.
    pub(crate) fn with(role: Role, timing: Timing, on_remote: Option<OnRemote>) -> Result<Self> {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
        listener.set_nonblocking(true)?;
        let port = listener.local_addr()?.port();
        let inner = Arc::new(Inner {
            stop: AtomicBool::new(false),
            state: Mutex::new(State::default()),
            timing,
            role,
            on_remote,
        });
        let thread = thread::spawn({
            let inner = inner.clone();
            move || {
                while !inner.stop.load(Ordering::Relaxed) {
                    match listener.accept() {
                        Ok((stream, _)) => {
                            if stream.set_nonblocking(false).is_ok() {
                                let _ = inner.attach_game(stream);
                            }
                        }
                        Err(_) => thread::sleep(POLL),
                    }
                }
            }
        });
        Ok(Self {
            inner,
            port,
            thread: Some(thread),
        })
    }

    /// The loopback port the mod connects to.
    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn status(&self) -> LiveStatus {
        self.inner.state.lock().unwrap().status()
    }

    /// How many lines the mods have logged so far, to notice new ones cheaply.
    pub fn logged(&self) -> u64 {
        self.inner.state.lock().unwrap().logged
    }

    /// The latest lines logged by the mods of both games, oldest first.
    pub fn log(&self) -> Vec<String> {
        self.inner.state.lock().unwrap().log.iter().cloned().collect()
    }

    /// Join a connection to another launcher of `player`, already past its
    /// handshake, to the relay, and return the player's peer. It replaces an
    /// earlier remote of the same player (on a guest, any earlier remote).
    /// [`Error::Full`] when the session has no place for another player; the
    /// connection is closed then.
    pub fn add_remote(&self, stream: TcpStream, player: Option<String>, name: String) -> Result<PeerId> {
        self.inner.attach_remote(stream, player, name)
    }

    /// Whether a remote of `player` would be taken now, with the players in
    /// `also` counted as in the session as well (see [`Relay::add_remote`]),
    /// and if not, why.
    pub fn admits(&self, player: &Option<String>, also: &[Option<String>]) -> std::result::Result<(), Refusal> {
        self.inner.state.lock().unwrap().admits(self.inner.role, player, also)
    }

    /// The players of the remotes joined now.
    pub fn players(&self) -> Vec<Option<String>> {
        let state = self.inner.state.lock().unwrap();
        state.remotes.iter().map(|remote| remote.player.clone()).collect()
    }

    /// The peer `player` is, or is to be once joined: the same for as long as
    /// the relay runs.
    pub fn peer_for(&self, player: &Option<String>) -> PeerId {
        self.inner.state.lock().unwrap().peer_for(player)
    }

    /// Close every connection and take no more, as dropping it does, for
    /// owners that cannot be sure they hold the last reference.
    pub fn close(&self) {
        self.inner.close();
    }
}

impl Drop for Relay {
    fn drop(&mut self) {
        self.inner.close();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// The guest's end of a live session: a relay kept connected to the host,
/// redialling for as long as it exists, the same way the guest first reached
/// it.
pub struct LiveLink {
    relay: Arc<Relay>,
    stop: Arc<AtomicBool>,
    /// Set while the redial thread is reaching the host, which it cannot be
    /// stopped from doing.
    dialling: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl LiveLink {
    pub fn start(host: Route, hello: Hello) -> Result<Self> {
        let relay = Arc::new(Relay::guest()?);
        let stop = Arc::new(AtomicBool::new(false));
        let dialling = Arc::new(AtomicBool::new(false));
        let thread = thread::spawn({
            let relay = relay.clone();
            let stop = stop.clone();
            let dialling = dialling.clone();
            move || {
                let mut failures = 0;
                while !stop.load(Ordering::SeqCst) {
                    if relay.status().remotes.is_empty() {
                        // Looked at again once the flag is up, so that a drop
                        // that saw it down always finds this thread done soon.
                        dialling.store(true, Ordering::SeqCst);
                        if stop.load(Ordering::SeqCst) {
                            break;
                        }
                        let dialled = dial(&host, &hello, &stop);
                        dialling.store(false, Ordering::SeqCst);
                        match dialled {
                            // A link dropped meanwhile takes nothing more.
                            Ok(_) if stop.load(Ordering::SeqCst) => break,
                            Ok((stream, host)) => {
                                failures = 0;
                                let _ = relay.add_remote(stream, host.player, host.name);
                            }
                            Err(_) => failures += 1,
                        }
                    }
                    sleep_unless(redial_after(failures), &stop);
                }
            }
        });
        Ok(Self {
            relay,
            stop,
            dialling,
            thread: Some(thread),
        })
    }

    pub fn port(&self) -> u16 {
        self.relay.port()
    }

    pub fn status(&self) -> LiveStatus {
        self.relay.status()
    }

    pub fn logged(&self) -> u64 {
        self.relay.logged()
    }

    pub fn log(&self) -> Vec<String> {
        self.relay.log()
    }
}

impl Drop for LiveLink {
    /// Closes the session at once. A redial thread that is between dials is
    /// waited for, which takes a moment at most. One that is reaching the
    /// host is not: that can take seconds (a refused connection alone takes
    /// two on Windows), and the link is dropped on the UI thread. It ends on
    /// its own once the dial does, and attaches nothing. If it was still
    /// connecting it does not ask the host to join either, so it cannot take
    /// the host away from a newer link that got there first.
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        self.relay.close();
        if let Some(thread) = self.thread.take()
            && !self.dialling.load(Ordering::SeqCst)
        {
            let _ = thread.join();
        }
    }
}

/// Connect to a host and ask to join its live session. On success the
/// stream carries frames from here on.
///
/// Connecting can take seconds, and the link may be dropped meanwhile and a
/// new one started. Once asked, the host takes this connection in place of
/// whatever it has of this player, so a link that is gone by then must not
/// ask: it would cut off the newer link's connection. A dial that has asked
/// already is not a worry: a newer link asks after it and replaces it.
///
/// Returns the stream with what the host said of itself.
fn dial(host: &Route, hello: &Hello, stop: &AtomicBool) -> Result<(TcpStream, Hello)> {
    let stream = host.open()?;
    if stop.load(Ordering::SeqCst) {
        return Err(Error::Protocol("link closed".into()));
    }
    stream.set_read_timeout(Some(HANDSHAKE_TIMEOUT))?;
    write_message(&mut &stream, &Message::JoinLive(hello.clone()))?;
    // Unbuffered on purpose: nothing past the reply may be consumed here.
    let host = match expect_message(&mut &stream)? {
        Message::Hello(host) if host.protocol == PROTOCOL_VERSION => host,
        Message::Hello(host) => {
            return Err(Error::VersionMismatch {
                ours: PROTOCOL_VERSION,
                theirs: host.protocol,
            });
        }
        Message::Error { message } => return Err(Error::Remote(message)),
        _ => return Err(Error::Protocol("unexpected reply to join_live".into())),
    };
    stream.set_read_timeout(None)?;
    Ok((stream, host))
}

#[cfg(test)]
mod tests {
    use std::time::Instant;

    use tsmp_core::manifest::Manifest;

    use std::net::SocketAddr;

    use super::*;
    use crate::host::{Host, HostConfig};
    use crate::protocol::read_message;
    use crate::rendezvous::{self, Invite, Reply, Request, ServerAddress, SessionCode};

    const WAIT: Duration = Duration::from_secs(10);
    /// Short enough for tests, long enough for a loaded machine to keep up.
    const QUICK: Timing = Timing {
        beat: Duration::from_millis(50),
        silence: Duration::from_millis(400),
        stall: Duration::from_millis(500),
    };

    fn hello(name: &str) -> Hello {
        Hello {
            protocol: PROTOCOL_VERSION,
            name: name.into(),
            game_version: None,
            packs: Default::default(),
            player: Some(format!("{name}-id")),
            ..Default::default()
        }
    }

    /// A relay for the host of a session of `max_players`, quick to notice
    /// remotes that are gone.
    fn host_relay(max_players: usize) -> Relay {
        Relay::with(Role::Host { max_players }, QUICK, None).unwrap()
    }

    /// Joins a remote of `player` to `relay`, and the far end of it.
    fn add(relay: &Relay, player: &str) -> (PeerId, Game) {
        let (near, far) = pair();
        let peer = relay.add_remote(near, Some(player.into()), player.into()).unwrap();
        (peer, Game::remote(far))
    }

    /// Stands in for the mod: a plain socket on the relay's loopback port. Or
    /// for the far launcher, on the other end of a remote connection.
    struct Game {
        stream: TcpStream,
        /// The far launcher hears beats; a game must never.
        remote: bool,
    }

    impl Game {
        fn connect(port: u16) -> Self {
            let stream = TcpStream::connect((Ipv4Addr::LOCALHOST, port)).unwrap();
            stream.set_read_timeout(Some(WAIT)).unwrap();
            Self { stream, remote: false }
        }

        fn remote(stream: TcpStream) -> Self {
            stream.set_read_timeout(Some(WAIT)).unwrap();
            Self { stream, remote: true }
        }

        fn send(&mut self, kind: u8, id: u32, payload: &[u8]) {
            self.stream.write_all(&frame(kind, id, payload)).unwrap();
        }

        fn beat(&mut self) {
            self.stream.write_all(&beat()).unwrap();
        }

        fn state(&mut self, role: &str, in_zone: bool) {
            let payload = serde_json::json!({"type": "state", "role": role, "in_zone": in_zone}).to_string();
            self.send(KIND_CONTROL, 0, payload.as_bytes());
        }

        /// The next frame, skipping the beats a far launcher hears.
        fn receive(&mut self) -> (u8, u32, Vec<u8>) {
            loop {
                let frame = read_frame(&mut self.stream).unwrap().expect("the relay closed the connection");
                if is_beat(&frame) {
                    assert!(self.remote, "a beat reached the game");
                    continue;
                }
                let id = u32::from_le_bytes(frame[1..5].try_into().unwrap());
                return (frame[0], id, frame[HEADER..].to_vec());
            }
        }

        fn receive_control(&mut self) -> serde_json::Value {
            let (kind, _, payload) = self.receive();
            assert_eq!(kind, KIND_CONTROL);
            serde_json::from_slice(&payload).unwrap()
        }

        /// The next `link`, past the `peer_up` and `peer_gone` before it.
        fn link(&mut self) -> serde_json::Value {
            loop {
                let control = self.receive_control();
                match control["type"].as_str() {
                    Some("link") => return control,
                    Some("peer_up" | "peer_gone") => {}
                    _ => panic!("expected a link, got {control}"),
                }
            }
        }

        fn state_v(&mut self, role: &str, version: u32) {
            let payload = serde_json::json!({"type": "state", "role": role, "in_zone": true, "v": version}).to_string();
            self.send(KIND_CONTROL, 0, payload.as_bytes());
        }

        /// Nothing arrives for a while.
        fn hears_nothing(&mut self) {
            self.stream.set_read_timeout(Some(Duration::from_millis(300))).unwrap();
            loop {
                match read_frame(&mut self.stream) {
                    Ok(Some(frame)) if self.remote && is_beat(&frame) => continue,
                    Ok(Some(frame)) => panic!("unexpected frame {frame:?}"),
                    Ok(None) => panic!("the relay closed the connection"),
                    Err(error) => {
                        assert!(timed_out(&error), "{error}");
                        break;
                    }
                }
            }
            self.stream.set_read_timeout(Some(WAIT)).unwrap();
        }
    }

    fn wait_for(what: &str, mut condition: impl FnMut() -> bool) {
        let deadline = Instant::now() + WAIT;
        while !condition() {
            assert!(Instant::now() < deadline, "timed out waiting for {what}");
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn pair() -> (TcpStream, TcpStream) {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let near = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (far, _) = listener.accept().unwrap();
        (near, far)
    }

    fn start_host(port: u16) -> (Host, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        let config = HostConfig {
            hello: hello("host"),
            mods_dir: dir.path().to_path_buf(),
            mods: Manifest::default(),
            save: None,
            own_families: false,
            max_players: crate::host::DEFAULT_MAX_PLAYERS,
        };
        (Host::start(config, port, false).unwrap(), dir)
    }

    #[test]
    fn redials_slow_down_while_they_fail() {
        assert_eq!(redial_after(0), REDIAL);
        assert_eq!(redial_after(1), REDIAL * 2);
        assert_eq!(redial_after(3), MAX_REDIAL);
        assert_eq!(redial_after(40), MAX_REDIAL);
    }

    #[test]
    fn frames_are_read_whole() {
        let mut bytes = frame(KIND_MSG, 11, b"hello");
        bytes.extend(frame(KIND_COMMAND, 0, b""));
        let mut reader = bytes.as_slice();
        assert_eq!(read_frame(&mut reader).unwrap(), Some(frame(KIND_MSG, 11, b"hello")));
        assert_eq!(read_frame(&mut reader).unwrap(), Some(frame(KIND_COMMAND, 0, b"")));
        assert_eq!(read_frame(&mut reader).unwrap(), None);
        assert_eq!(frame(KIND_MSG, 11, b"abc"), [1, 11, 0, 0, 0, 3, 0, 0, 0, b'a', b'b', b'c']);
    }

    /// Gives its bytes a few at a time, timing out between them as a socket
    /// with a short read timeout does on a slow link.
    struct Trickle {
        bytes: Vec<u8>,
        at: usize,
        pause: bool,
    }

    impl Read for Trickle {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            self.pause = !self.pause;
            if self.pause {
                return Err(ErrorKind::TimedOut.into());
            }
            let read = buf.len().min(3).min(self.bytes.len() - self.at);
            buf[..read].copy_from_slice(&self.bytes[self.at..self.at + read]);
            self.at += read;
            Ok(read)
        }
    }

    #[test]
    fn a_timeout_inside_a_frame_loses_nothing_of_it() {
        let mut bytes = frame(KIND_MSG, 11, b"a frame cut by timeouts");
        bytes.extend(frame(KIND_COMMAND, 0, b""));
        let mut frames = FrameReader::new(Trickle { bytes, at: 0, pause: false }, false);
        let mut read = Vec::new();
        loop {
            match frames.next() {
                Ok(Some(frame)) => read.push(frame),
                Ok(None) => break,
                Err(e) => assert!(timed_out(&e), "{e}"),
            }
        }
        assert_eq!(read, [frame(KIND_MSG, 11, b"a frame cut by timeouts"), frame(KIND_COMMAND, 0, b"")]);

        let whole = frame(KIND_MSG, 1, b"hello");
        let mut cut = FrameReader::new(&whole[..6], false);
        assert_eq!(cut.next().unwrap_err().kind(), ErrorKind::UnexpectedEof);
        let mut huge = vec![KIND_MSG, 0, 0, 0, 0];
        huge.extend_from_slice(&u32::MAX.to_le_bytes());
        assert_eq!(FrameReader::new(huge.as_slice(), false).next().unwrap_err().kind(), ErrorKind::InvalidData);
    }

    #[test]
    fn truncated_and_oversized_frames_are_errors() {
        let bytes = frame(KIND_MSG, 1, b"hello");
        assert!(read_frame(&mut &bytes[..4]).is_err());
        assert!(read_frame(&mut &bytes[..bytes.len() - 1]).is_err());
        let mut huge = vec![KIND_MSG, 0, 0, 0, 0];
        huge.extend_from_slice(&u32::MAX.to_le_bytes());
        assert!(read_frame(&mut huge.as_slice()).is_err());
    }

    /// Hands out some bytes, then times out as a socket with a read timeout does.
    struct FallsSilent<'a>(&'a [u8]);

    impl Read for FallsSilent<'_> {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            if self.0.is_empty() {
                return Err(ErrorKind::WouldBlock.into());
            }
            self.0.read(buf)
        }
    }

    #[test]
    fn a_timeout_inside_or_between_frames_is_an_error_not_an_end() {
        let bytes = frame(KIND_MSG, 1, b"hello");
        assert!(read_frame(&mut FallsSilent(&[])).is_err());
        assert!(read_frame(&mut FallsSilent(&bytes[..4])).is_err());
        assert!(read_frame(&mut FallsSilent(&bytes[..bytes.len() - 1])).is_err());
        assert_eq!(read_frame(&mut FallsSilent(&bytes)).unwrap(), Some(bytes));
    }

    #[test]
    fn relays_between_the_game_and_a_remote() {
        let relay = Relay::host(2).unwrap();
        let mut game = Game::connect(relay.port());
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));

        let (near, far) = pair();
        assert_eq!(relay.add_remote(near, Some("guest-id".into()), "guest".into()).unwrap(), 1);
        let mut remote = Game::remote(far);
        assert_eq!(
            game.receive_control(),
            serde_json::json!({"type": "peer_up", "peer": 1, "name": "guest", "player": "guest-id"})
        );
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));

        game.send(KIND_MSG, 11, b"to the remote");
        assert_eq!(remote.receive(), (KIND_MSG, 11, b"to the remote".to_vec()));
        // What the remote sends waits for the game to say its version: a mod
        // that says none is of the first, and reads it as it is.
        remote.send(KIND_COMMAND, 0, b"to the game");
        game.hears_nothing();
        game.state("host", true);
        remote.receive();
        assert_eq!(game.receive(), (KIND_COMMAND, 0, b"to the game".to_vec()));
        remote.state("guest", true);
        game.receive();
        assert!(relay.status().is_live());

        drop(remote);
        assert_eq!(game.receive_control(), serde_json::json!({"type": "peer_gone", "peer": 1}));
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));
        assert_eq!(
            relay.status(),
            LiveStatus {
                game_connected: true,
                game_in_zone: true,
                game_joined: true,
                ..LiveStatus::default()
            }
        );
    }

    #[test]
    fn a_games_state_says_whether_it_is_loaded_getting_in_step_or_joined() {
        let state = |value: serde_json::Value| snoop_state(&control(value));
        let game = |in_zone, syncing, joined| {
            Some(GameState {
                in_zone,
                syncing,
                joined,
                apart: false,
                stopped: false,
                version: 1,
            })
        };
        assert_eq!(
            state(
                serde_json::json!({"type": "state", "in_zone": false, "loaded": true, "syncing": true, "joined": false})
            ),
            game(true, true, false)
        );
        assert_eq!(
            state(serde_json::json!({"type": "state", "in_zone": true, "loaded": true, "joined": false})),
            game(true, false, false)
        );
        // An older mod says only whether it is in a zone.
        assert_eq!(
            state(serde_json::json!({"type": "state", "in_zone": true})),
            game(true, false, true)
        );
        assert_eq!(state(serde_json::json!({"type": "log", "lines": []})), None);
        let version = |v| {
            let state = serde_json::json!({"type": "state", "in_zone": true, "v": v});
            snoop_state(&control(state)).unwrap().version
        };
        assert_eq!((version(2), version(3), version(0)), (2, 3, 1));
    }

    #[test]
    fn a_remote_hears_when_the_game_goes_away() {
        let relay = Relay::host(2).unwrap();
        let (_, mut remote) = add(&relay, "guest");

        let mut game = Game::connect(relay.port());
        // The game hears of the remotes there already.
        assert_eq!(
            game.receive_control(),
            serde_json::json!({"type": "peer_up", "peer": 1, "name": "guest", "player": "guest"})
        );
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));
        game.state("host", true);
        remote.receive();
        drop(game);

        assert_eq!(remote.receive_control(), serde_json::json!({"type": "gone"}));
        wait_for("the game to be forgotten", || !relay.status().game_connected);
        assert!(!relay.status().game_in_zone);
    }

    #[test]
    fn a_reconnecting_game_replaces_the_old_one() {
        let relay = Relay::host(2).unwrap();
        let mut old = Game::connect(relay.port());
        old.receive_control();
        let mut new = Game::connect(relay.port());
        new.receive_control();

        // The old connection is closed by the relay.
        assert!(matches!(read_frame(&mut old.stream), Ok(None) | Err(_)));
        let (_, mut remote) = add(&relay, "guest");
        new.link();
        new.send(KIND_MSG, 1, b"x");
        assert_eq!(remote.receive(), (KIND_MSG, 1, b"x".to_vec()));
        assert!(relay.status().game_connected);
    }

    #[test]
    fn large_frames_cross_intact_while_the_other_way_is_busy() {
        let relay = Relay::host(2).unwrap();
        let mut game = Game::connect(relay.port());
        game.receive_control();
        let (_, mut remote) = add(&relay, "guest");
        game.link();

        let big: Vec<u8> = (0..4_000_000u32).map(|i| i as u8).collect();
        let writer = thread::spawn({
            let mut stream = game.stream.try_clone().unwrap();
            let big = big.clone();
            move || {
                for id in 0..5 {
                    stream.write_all(&frame(KIND_MSG, id, &big)).unwrap();
                }
            }
        });
        for id in 0..5 {
            assert_eq!(remote.receive(), (KIND_MSG, id, big.clone()));
        }
        writer.join().unwrap();
    }

    /// A relay with a game and a remote, past the link frames.
    fn joined() -> (Relay, Game, Game) {
        let relay = host_relay(2);
        let mut game = Game::connect(relay.port());
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));
        let (_, mut remote) = add(&relay, "guest");
        assert_eq!(game.link(), serde_json::json!({"type": "link", "remotes": 1}));
        // As a mod does once it connects; until then what the remote sends
        // waits for it.
        game.state("host", true);
        remote.receive();
        (relay, game, remote)
    }

    #[test]
    fn a_remote_that_falls_silent_is_dropped() {
        let (relay, mut game, mut remote) = joined();
        let since = Instant::now();
        remote.beat();

        // The far end stays open but writes and reads nothing more.
        assert_eq!(game.link(), serde_json::json!({"type": "link", "remotes": 0}));
        assert!(since.elapsed() >= QUICK.silence);
        assert!(relay.status().remotes.is_empty());
        drop(remote);
    }

    #[test]
    fn a_remote_that_never_beats_is_not_held_to_silence() {
        let (relay, mut game, mut remote) = joined();
        thread::sleep(QUICK.silence * 3);
        assert_eq!(relay.status().remotes.len(), 1);
        remote.send(KIND_MSG, 5, b"still here");
        assert_eq!(game.receive(), (KIND_MSG, 5, b"still here".to_vec()));
    }

    #[test]
    fn beats_from_a_remote_never_reach_the_game() {
        let (relay, mut game, mut remote) = joined();
        remote.beat();
        remote.beat();
        remote.send(KIND_MSG, 3, b"after the beats");
        // `receive` fails on a beat for the game.
        assert_eq!(game.receive(), (KIND_MSG, 3, b"after the beats".to_vec()));
        assert_eq!(relay.logged(), 0);
        assert_eq!(relay.status().remotes.len(), 1);
    }

    #[test]
    fn an_idle_remote_hears_beats_and_the_game_does_not() {
        let relay = host_relay(2);
        let mut game = Game::connect(relay.port());
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));
        let (_, mut remote) = add(&relay, "guest");
        assert_eq!(game.link(), serde_json::json!({"type": "link", "remotes": 1}));
        // Timed from before the game's state, which the remote hears first:
        // every beat after it waits for the remote to be idle for a beat,
        // however late this thread is to read them.
        let since = Instant::now();
        game.state("host", true);
        remote.receive();
        for _ in 0..3 {
            let frame = read_frame(&mut &remote.stream).unwrap().unwrap();
            assert!(is_beat(&frame));
        }
        assert!(since.elapsed() >= QUICK.beat * 3);

        game.stream.set_read_timeout(Some(QUICK.beat * 4)).unwrap();
        let error = read_frame(&mut game.stream).unwrap_err();
        assert!(matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut), "{error}");
    }

    #[test]
    fn a_remote_that_stops_reading_is_dropped() {
        let (_relay, mut game, remote) = joined();
        // More than the sockets on the way hold, so the relay's writes stall.
        let big = vec![7u8; 4 << 20];
        for id in 0..16 {
            game.send(KIND_MSG, id, &big);
        }
        assert_eq!(game.link(), serde_json::json!({"type": "link", "remotes": 0}));
        drop(remote);
    }

    #[test]
    fn guest_and_host_games_are_joined_through_both_launchers() {
        let (host, _dir) = start_host(0);
        let link = LiveLink::start(Route::Direct((Ipv4Addr::LOCALHOST, host.port()).into()), hello("guest")).unwrap();

        let mut host_game = Game::connect(host.live().port());
        let mut guest_game = Game::connect(link.port());
        wait_for("the launchers to connect", || {
            host.live().status().remotes.len() == 1 && link.status().remotes.len() == 1
        });
        // The guest's relay knows the host by what it said of itself.
        let status = link.status();
        assert_eq!((status.remotes[0].name.as_str(), status.remotes[0].player.as_deref()), ("host", Some("host-id")));
        assert_eq!(host.live().status().remotes[0].player.as_deref(), Some("guest-id"));

        host_game.state("host", true);
        guest_game.state("guest", true);
        wait_for("both games to be in a zone", || {
            host.live().status().is_live() && link.status().is_live()
        });

        host_game.send(KIND_MSG, 11, b"view update");
        loop {
            let (kind, id, payload) = guest_game.receive();
            if kind == KIND_MSG {
                assert_eq!((id, payload), (11, b"view update".to_vec()));
                break;
            }
        }
        guest_game.send(KIND_COMMAND, 0, b"{}");
        loop {
            let (kind, _, payload) = host_game.receive();
            if kind == KIND_COMMAND {
                assert_eq!(payload, b"{}");
                break;
            }
        }

        drop(host_game);
        loop {
            if guest_game.receive_control() == serde_json::json!({"type": "gone"}) {
                break;
            }
        }
        wait_for("the guest to see the host's game leave", || !link.status().remotes[0].in_zone);
    }

    #[test]
    fn the_guest_keeps_dialling_until_the_host_appears() {
        // Reserve a port, then free it for the host that starts late.
        let port = TcpListener::bind((Ipv4Addr::UNSPECIFIED, 0)).unwrap().local_addr().unwrap().port();
        let link = LiveLink::start(Route::Direct((Ipv4Addr::LOCALHOST, port).into()), hello("guest")).unwrap();
        thread::sleep(Duration::from_millis(300));
        assert!(link.status().remotes.is_empty());

        let (host, _dir) = start_host(port);
        wait_for("the guest to reach the late host", || link.status().remotes.len() == 1);
        wait_for("the host to see the guest", || host.live().status().remotes.len() == 1);

        drop(host);
        wait_for("the guest to notice the host left", || link.status().remotes.is_empty());
    }

    #[test]
    fn dropping_a_link_does_not_wait_for_a_dial_and_the_dial_attaches_nothing() {
        // A host that takes the connection and is slow to answer it.
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let link = LiveLink::start(Route::Direct(listener.local_addr().unwrap()), hello("guest")).unwrap();
        let (host, _) = listener.accept().unwrap();
        host.set_read_timeout(Some(WAIT)).unwrap();
        assert!(matches!(expect_message(&mut &host).unwrap(), Message::JoinLive(_)));
        wait_for("the guest to be dialling", || link.dialling.load(Ordering::SeqCst));

        let relay = link.relay.clone();
        let since = Instant::now();
        drop(link);
        assert!(since.elapsed() < HANDSHAKE_TIMEOUT / 2, "the drop waited for the dial");

        // The dial now succeeds, too late to be kept.
        write_message(&mut &host, &Message::Hello(hello("host"))).unwrap();
        wait_for("the redial thread to end", || Arc::strong_count(&relay) == 1);
        assert!(relay.status().remotes.is_empty());
        assert!(matches!(read_frame(&mut &host), Ok(None) | Err(_)));
    }

    #[test]
    fn a_link_dropped_while_connecting_never_asks_the_host() {
        // A session server that is slow to put the guest through.
        let server = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let invite = Invite {
            code: SessionCode::random(),
            server: ServerAddress::new("127.0.0.1", server.local_addr().unwrap().port()),
        };
        let link = LiveLink::start(Route::Invite(invite), hello("guest")).unwrap();
        let (tunnel, _) = server.accept().unwrap();
        tunnel.set_read_timeout(Some(WAIT)).unwrap();
        assert!(matches!(rendezvous::expect(&tunnel).unwrap(), Request::Join { .. }));
        wait_for("the guest to be dialling", || link.dialling.load(Ordering::SeqCst));

        let relay = link.relay.clone();
        drop(link);
        // Put through now, the stream reaches the host, which must hear
        // nothing on it: a join would replace a newer link's connection.
        rendezvous::send(&tunnel, &Reply::Joined).unwrap();
        wait_for("the redial thread to end", || Arc::strong_count(&relay) == 1);
        assert!(matches!(read_message::<Message>(&mut &tunnel), Ok(None) | Err(_)));
    }

    #[test]
    fn a_launcher_with_another_protocol_is_refused() {
        let (host, _dir) = start_host(0);
        let mut old = hello("old");
        old.protocol = PROTOCOL_VERSION + 1;
        let address: SocketAddr = (Ipv4Addr::LOCALHOST, host.port()).into();

        // The host answers with its own version and joins nothing.
        let stream = TcpStream::connect(address).unwrap();
        stream.set_read_timeout(Some(WAIT)).unwrap();
        write_message(&mut &stream, &Message::JoinLive(old)).unwrap();
        assert!(matches!(expect_message(&mut &stream).unwrap(), Message::Hello(_)));
        assert!(matches!(read_frame(&mut &stream), Ok(None) | Err(_)));
        assert!(host.live().status().remotes.is_empty());
    }

    /// Whether the relay closed this far end, past the beats it sent before.
    /// Whether the relay ends the connection. What it sent before that, such
    /// as the game's state reaching a remote just as it is replaced, is
    /// passed over.
    fn closed(remote: &mut Game) -> bool {
        loop {
            match read_frame(&mut remote.stream) {
                Ok(Some(_)) => continue,
                Ok(None) => return true,
                Err(error) => return !timed_out(&error),
            }
        }
    }

    fn peer_up(peer: PeerId, player: &str) -> serde_json::Value {
        serde_json::json!({"type": "peer_up", "peer": peer, "name": player, "player": player})
    }

    #[test]
    fn routed_frames_hold_exactly_one_frame() {
        let inner = frame(KIND_MSG, 4, b"abc");
        let outer = routed(2, &inner);
        assert_eq!(outer[..HEADER], [KIND_ROUTED, 2, 0, 0, 0, 12, 0, 0, 0]);
        assert_eq!(outer[HEADER..], inner[..]);
        assert_eq!(unwrap_routed(&outer), Some((2, inner.as_slice())));
        assert_eq!(unwrap_routed(&inner), None);
        assert_eq!(unwrap_routed(&frame(KIND_ROUTED, 2, &inner[..inner.len() - 1])), None);
        let mut longer = inner.clone();
        longer.push(0);
        assert_eq!(unwrap_routed(&frame(KIND_ROUTED, 2, &longer)), None);
        assert_eq!(unwrap_routed(&routed(1, &outer)), None);
        assert_eq!(unwrap_routed(&frame(KIND_ROUTED, 1, b"")), None);
    }

    #[test]
    fn a_routed_frame_may_hold_the_largest_frame_and_no_more() {
        let header = |kind: u8, length: usize| {
            let mut header = vec![kind, 1, 0, 0, 0];
            header.extend_from_slice(&(length as u32).to_le_bytes());
            header
        };
        // The length is taken, and the reader waits for the rest.
        let largest = header(KIND_ROUTED, MAX_PAYLOAD + HEADER);
        assert_eq!(FrameReader::new(largest.as_slice(), true).next().unwrap_err().kind(), ErrorKind::UnexpectedEof);
        for too_long in [header(KIND_ROUTED, MAX_PAYLOAD + HEADER + 1), header(KIND_MSG, MAX_PAYLOAD + 1)] {
            assert_eq!(FrameReader::new(too_long.as_slice(), true).next().unwrap_err().kind(), ErrorKind::InvalidData);
            assert_eq!(read_frame(&mut too_long.as_slice()).unwrap_err().kind(), ErrorKind::InvalidData);
        }
        // From a remote, nothing is longer than the longest plain frame, so
        // that wrapped for the game it is never too long.
        let longest = header(KIND_ROUTED, MAX_PAYLOAD);
        assert_eq!(FrameReader::new(longest.as_slice(), false).next().unwrap_err().kind(), ErrorKind::UnexpectedEof);
        assert_eq!(FrameReader::new(largest.as_slice(), false).next().unwrap_err().kind(), ErrorKind::InvalidData);
    }

    #[test]
    fn a_routed_frame_never_comes_from_a_remote() {
        let relay = host_relay(4);
        let mut game = Game::connect(relay.port());
        game.link();
        let (_, mut a) = add(&relay, "a");
        let (_, _b) = add(&relay, "b");
        game.link();
        game.link();
        // Neither while the game's version is not known nor once it is.
        let posing = routed(2, &frame(KIND_COMMAND, 0, b"as b"));
        a.stream.write_all(&posing).unwrap();
        game.state_v("host", 3);
        a.receive();
        a.stream.write_all(&posing).unwrap();
        a.send(KIND_COMMAND, 0, b"as a");
        assert_eq!(game.receive(), (KIND_ROUTED, 1, frame(KIND_COMMAND, 0, b"as a")));
        game.hears_nothing();
        assert_eq!(relay.status().remotes.len(), 2);
    }

    #[test]
    fn a_guests_game_cannot_send_a_routed_frame() {
        let relay = Relay::with(Role::Guest, QUICK, None).unwrap();
        let mut game = Game::connect(relay.port());
        game.link();
        let (_, mut host) = add(&relay, "host");
        game.link();
        game.stream.write_all(&routed(1, &frame(KIND_MSG, 1, b"routed"))).unwrap();
        game.send(KIND_MSG, 2, b"plain");
        // In order: had the routed one gone, it would have come first.
        assert_eq!(host.receive(), (KIND_MSG, 2, b"plain".to_vec()));
    }

    #[test]
    fn remotes_that_fall_behind_together_are_dropped_slowest_first() {
        let streams: Vec<(TcpStream, TcpStream)> = (0..3).map(|_| pair()).collect();
        let remote = |stream: &TcpStream, peer: PeerId| Remote {
            connection: Connection::new(peer.into(), stream, Some(Timing::default())).unwrap(),
            peer,
            player: None,
            name: String::new(),
            game: GameState::default(),
        };
        let state = State {
            remotes: streams.iter().zip(1..).map(|((near, _), peer)| remote(near, peer)).collect(),
            ..Default::default()
        };
        // Each is behind by less than it may be alone.
        for (remote, behind) in state.remotes.iter().zip([150usize, 200, 140]) {
            remote.connection.queued.store(behind << 20, Ordering::Relaxed);
        }
        let is_closed = |state: &State, index: usize| state.remotes[index].connection.closed.load(Ordering::Relaxed);

        state.send_to_remote(0, frame(KIND_MSG, 1, &vec![0; 16 << 20]));
        assert!((0..3).all(|index| !is_closed(&state, index)));
        // Together they would be past what all may have waiting: the one
        // furthest behind goes, though the frame is for another. The frame
        // is large enough for that whether or not the first one has been
        // written by now: Windows takes all of it into the socket at once.
        state.send_to_remote(2, frame(KIND_MSG, 2, &vec![0; 24 << 20]));
        assert!(!is_closed(&state, 0) && !is_closed(&state, 2));
        assert!(is_closed(&state, 1));
        assert_eq!(state.remotes[1].connection.waiting(), 0);
    }

    #[test]
    fn the_same_player_replaces_its_connection_and_keeps_its_peer() {
        let relay = host_relay(2);
        let mut game = Game::connect(relay.port());
        game.link();
        game.state("host", true);
        let (peer, mut old) = add(&relay, "a");
        assert_eq!(peer, 1);
        assert_eq!(game.receive_control(), peer_up(1, "a"));
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));

        let (again, mut new) = add(&relay, "a");
        assert_eq!(again, 1);
        // Up again on a new connection; the count stays and nobody left.
        assert_eq!(game.receive_control(), peer_up(1, "a"));
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));
        assert!(closed(&mut old));
        game.hears_nothing();
        new.send(KIND_MSG, 1, b"x");
        assert_eq!(game.receive(), (KIND_MSG, 1, b"x".to_vec()));
        assert_eq!(relay.status().remotes.len(), 1);
    }

    #[test]
    fn a_new_player_is_refused_while_the_session_is_full() {
        let relay = host_relay(2);
        let mut game = Game::connect(relay.port());
        game.link();
        game.state("host", true);
        let (_, mut a) = add(&relay, "a");
        game.link();
        assert_eq!(relay.admits(&Some("a".into()), &[]), Ok(()));
        assert_eq!(relay.admits(&Some("b".into()), &[]), Err(Refusal::Full));

        let (near, far) = pair();
        assert!(matches!(relay.add_remote(near, Some("b".into()), "b".into()), Err(Error::Full)));
        assert!(closed(&mut Game::remote(far)));
        game.hears_nothing();
        a.send(KIND_MSG, 2, b"still a");
        assert_eq!(game.receive(), (KIND_MSG, 2, b"still a".to_vec()));
        assert_eq!(relay.players(), [Some("a".to_string())]);

        // Once a has left there is a place, unless a player the host has
        // elsewhere takes it.
        drop(a);
        assert_eq!(game.receive_control(), serde_json::json!({"type": "peer_gone", "peer": 1}));
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));
        assert_eq!(relay.admits(&Some("b".into()), &[]), Ok(()));
        assert_eq!(relay.admits(&Some("b".into()), &[Some("c".into())]), Err(Refusal::Full));
        assert_eq!(relay.admits(&Some("c".into()), &[Some("c".into())]), Ok(()));
    }

    #[test]
    fn players_keep_their_peer_across_reconnects() {
        let relay = host_relay(4);
        let mut game = Game::connect(relay.port());
        game.link();
        let (a, first_a) = add(&relay, "a");
        let (b, _b) = add(&relay, "b");
        assert_eq!((a, b), (1, 2));
        game.link();
        game.link();

        drop(first_a);
        assert_eq!(game.receive_control(), serde_json::json!({"type": "peer_gone", "peer": 1}));
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));
        let (c, _c) = add(&relay, "c");
        let (back, _a) = add(&relay, "a");
        assert_eq!((c, back), (3, 1));
        assert_eq!(relay.peer_for(&Some("b".into())), 2);
        let peers: Vec<PeerId> = relay.status().remotes.iter().map(|remote| remote.peer).collect();
        assert_eq!(peers, [2, 3, 1]);

        // Three guests fill a session of four.
        let (near, _far) = pair();
        assert!(matches!(relay.add_remote(near, Some("d".into()), "d".into()), Err(Error::Full)));
    }

    #[test]
    fn frames_from_remotes_are_routed_only_to_a_game_that_reads_them() {
        let relay = host_relay(4);
        let mut game = Game::connect(relay.port());
        game.link();
        let (_, mut a) = add(&relay, "a");
        let (_, b) = add(&relay, "b");
        game.link();
        game.link();

        // Before the game has said which version it speaks, frames wait for
        // it, and the relay's own wait behind them.
        a.send(KIND_COMMAND, 0, b"early");
        wait_for("the early frame", || relay.inner.state.lock().unwrap().held.len() == 1);
        drop(b);
        game.hears_nothing();
        game.state_v("host", 3);
        assert_eq!(game.receive(), (KIND_ROUTED, 1, frame(KIND_COMMAND, 0, b"early")));
        assert_eq!(game.receive_control(), serde_json::json!({"type": "peer_gone", "peer": 2}));
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));
        a.receive();
        let (_, mut b) = add(&relay, "b");
        game.link();
        a.send(KIND_COMMAND, 0, b"from a");
        assert_eq!(game.receive(), (KIND_ROUTED, 1, frame(KIND_COMMAND, 0, b"from a")));
        b.send(KIND_MSG, 7, b"from b");
        assert_eq!(game.receive(), (KIND_ROUTED, 2, frame(KIND_MSG, 7, b"from b")));

        // A state, or a gone, from a guest's launcher is that guest's alone.
        a.state("guest", true);
        b.state("guest", true);
        game.receive();
        game.receive();
        let gone = control(serde_json::json!({"type": "gone"}));
        b.stream.write_all(&gone).unwrap();
        assert_eq!(game.receive(), (KIND_ROUTED, 2, gone));
        let status = relay.status();
        assert_eq!((status.remotes[0].in_zone, status.remotes[1].in_zone), (true, false));

        // A game that connects again is waited for until it says its version
        // again. One of an older version is never wrapped for, and keeps
        // only the guest that came first, which is a.
        drop(game);
        assert_eq!(a.receive_control(), serde_json::json!({"type": "gone"}));
        assert_eq!(b.receive_control(), serde_json::json!({"type": "gone"}));
        let mut game = Game::connect(relay.port());
        assert_eq!(game.receive_control(), peer_up(1, "a"));
        assert_eq!(game.receive_control(), peer_up(2, "b"));
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 2}));
        a.send(KIND_MSG, 1, b"plain");
        b.send(KIND_MSG, 1, b"from b");
        wait_for("both frames", || relay.inner.state.lock().unwrap().held.len() == 2);
        game.state_v("host", 2);
        assert_eq!(game.receive(), (KIND_MSG, 1, b"plain".to_vec()));
        assert_eq!(game.receive_control(), serde_json::json!({"type": "peer_gone", "peer": 2}));
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));
        assert!(closed(&mut b));
        a.receive();
        a.send(KIND_MSG, 2, b"still plain");
        assert_eq!(game.receive(), (KIND_MSG, 2, b"still plain".to_vec()));
        assert_eq!(relay.admits(&Some("b".into()), &[]), Err(Refusal::Full));
    }

    #[test]
    fn the_game_sends_a_routed_frame_to_its_peer_and_a_plain_one_to_all() {
        let relay = host_relay(4);
        let mut game = Game::connect(relay.port());
        game.link();
        let mut remotes: Vec<Game> = ["a", "b", "c"].iter().map(|player| add(&relay, player).1).collect();
        for _ in 0..3 {
            game.link();
        }
        game.state_v("host", 3);
        for remote in &mut remotes {
            remote.receive_control();
        }

        game.stream.write_all(&routed(2, &frame(KIND_MSG, 5, b"for b"))).unwrap();
        game.send(KIND_MSG, 6, b"for all");
        assert_eq!(remotes[1].receive(), (KIND_MSG, 5, b"for b".to_vec()));
        for remote in &mut remotes {
            assert_eq!(remote.receive(), (KIND_MSG, 6, b"for all".to_vec()));
        }

        // Peer 0 is everyone. A peer that is not there hears nothing, nor
        // does anyone of a routed frame that holds no frame.
        game.stream.write_all(&routed(BROADCAST, &frame(KIND_MSG, 7, b"all again"))).unwrap();
        game.stream.write_all(&routed(9, &frame(KIND_MSG, 8, b"nobody"))).unwrap();
        game.send(KIND_ROUTED, 1, b"not a frame");
        game.send(KIND_MSG, 9, b"last");
        for remote in &mut remotes {
            assert_eq!(remote.receive(), (KIND_MSG, 7, b"all again".to_vec()));
            assert_eq!(remote.receive(), (KIND_MSG, 9, b"last".to_vec()));
        }
        assert!(relay.status().game_connected);
    }

    #[test]
    fn a_game_of_an_older_version_has_one_guest_and_no_routed_frames() {
        let relay = host_relay(4);
        let mut game = Game::connect(relay.port());
        game.link();
        game.state_v("host", 2);
        wait_for("the game's state", || relay.status().game_in_zone);

        let (_, mut a) = add(&relay, "a");
        game.link();
        a.send(KIND_COMMAND, 0, b"plain");
        assert_eq!(game.receive(), (KIND_COMMAND, 0, b"plain".to_vec()));
        assert_eq!(relay.admits(&Some("b".into()), &[]), Err(Refusal::Full));
        let (near, _far) = pair();
        assert!(matches!(relay.add_remote(near, Some("b".into()), "b".into()), Err(Error::Full)));
        // The one guest still comes back.
        let (peer, _a) = add(&relay, "a");
        assert_eq!(peer, 1);
    }

    #[test]
    fn a_launcher_without_a_player_id_is_the_one_guest_or_none() {
        let relay = host_relay(4);
        let (near, _old) = pair();
        assert_eq!(relay.add_remote(near, None, "old".into()).unwrap(), 1);
        assert_eq!(relay.admits(&Some("a".into()), &[]), Err(Refusal::Legacy));
        // It comes back as itself.
        let (near, _again) = pair();
        assert_eq!(relay.add_remote(near, None, "old".into()).unwrap(), 1);
        assert_eq!(relay.status().remotes.len(), 1);
        assert_eq!(relay.status().remotes[0].player, None);

        let relay = host_relay(4);
        let (_, _a) = add(&relay, "a");
        assert_eq!(relay.admits(&None, &[]), Err(Refusal::Legacy));
        let (near, _far) = pair();
        assert!(matches!(relay.add_remote(near, None, "old".into()), Err(Error::Full)));
    }

    #[test]
    fn a_guests_relay_wraps_nothing_and_takes_one_remote() {
        let relay = Relay::with(Role::Guest, QUICK, None).unwrap();
        let mut game = Game::connect(relay.port());
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));
        let (_, mut old) = add(&relay, "host");
        // No peers on a guest: the game hears what it heard before.
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));
        game.state_v("guest", 3);
        old.receive();
        old.send(KIND_MSG, 3, b"view");
        assert_eq!(game.receive(), (KIND_MSG, 3, b"view".to_vec()));

        // A new connection to the host replaces the old, whoever answered.
        let (_, mut new) = add(&relay, "someone else");
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));
        assert!(closed(&mut old));
        new.send(KIND_MSG, 4, b"view");
        assert_eq!(game.receive(), (KIND_MSG, 4, b"view".to_vec()));
        drop(new);
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));
    }

    #[test]
    fn the_host_hears_of_remotes_joining_and_leaving() {
        let (sender, events) = mpsc::channel();
        let on_remote = move |event| {
            let _ = sender.send(event);
        };
        let relay = Relay::with(Role::Host { max_players: 3 }, QUICK, Some(Box::new(on_remote))).unwrap();
        let joined = |peer, player: &str| RemoteEvent::Joined {
            peer,
            player: Some(player.into()),
        };

        let (_, first) = add(&relay, "a");
        assert_eq!(events.recv_timeout(WAIT).unwrap(), joined(1, "a"));
        // Replaced: it joins again, and the old connection leaves without a word.
        let (_, _again) = add(&relay, "a");
        assert_eq!(events.recv_timeout(WAIT).unwrap(), joined(1, "a"));
        drop(first);
        let (_, b) = add(&relay, "b");
        assert_eq!(events.recv_timeout(WAIT).unwrap(), joined(2, "b"));
        drop(b);
        assert_eq!(events.recv_timeout(WAIT).unwrap(), RemoteEvent::Left { peer: 2 });
        assert!(events.recv_timeout(Duration::from_millis(300)).is_err());
    }

    #[test]
    fn a_guest_hears_plain_frames_from_a_host_that_routes() {
        let (host, _dir) = start_host(0);
        let link = LiveLink::start(Route::Direct((Ipv4Addr::LOCALHOST, host.port()).into()), hello("guest")).unwrap();
        let mut host_game = Game::connect(host.live().port());
        let mut guest_game = Game::connect(link.port());
        // Each launcher takes the other in on a thread of its own.
        wait_for("the launchers to connect", || {
            link.status().remotes.len() == 1 && host.live().status().remotes.len() == 1
        });
        let peer = host.live().status().remotes[0].peer;

        host_game.state_v("host", 3);
        wait_for("the host's state to be seen", || link.status().remotes[0].in_zone);
        guest_game.send(KIND_COMMAND, 0, b"{}");
        loop {
            let (kind, id, payload) = host_game.receive();
            if kind == KIND_ROUTED {
                assert_eq!((id, payload), (peer, frame(KIND_COMMAND, 0, b"{}")));
                break;
            }
        }
        host_game.stream.write_all(&routed(peer, &frame(KIND_MSG, 11, b"for the guest"))).unwrap();
        loop {
            let (kind, id, payload) = guest_game.receive();
            assert_ne!(kind, KIND_ROUTED, "a guest's game was sent a routed frame");
            if kind == KIND_MSG {
                assert_eq!((id, payload), (11, b"for the guest".to_vec()));
                break;
            }
        }
    }
}
