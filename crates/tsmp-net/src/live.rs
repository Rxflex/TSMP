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
//! go to the game, untouched. The relay adds two control frames of its own:
//!
//! * `{"type":"link","remotes":N}` to the game when it connects and whenever
//!   the number of remotes changes;
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
//! each game is in a zone, for display only.

use std::io::{self, ErrorKind, Read, Write};
use std::net::{Ipv4Addr, Shutdown, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use crate::protocol::{Hello, Message, PROTOCOL_VERSION, expect_message, write_message};
use crate::route::Route;
use crate::{Error, POLL, Result, sleep_unless};

pub const KIND_MSG: u8 = 1;
pub const KIND_COMMAND: u8 = 2;
pub const KIND_CONTROL: u8 = 3;

const HEADER: usize = 9;
const MAX_PAYLOAD: usize = 64 << 20;
/// A game that is loading does not read. Frames wait for it up to this many
/// bytes; past that the peer is dropped rather than exhausting memory.
const MAX_QUEUED: usize = 256 << 20;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);
const REDIAL: Duration = Duration::from_secs(2);
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
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct LiveStatus {
    /// The mod in the local game is connected.
    pub game_connected: bool,
    /// The local game has a zone loaded.
    pub game_in_zone: bool,
    /// Launchers on other machines connected to this one.
    pub remotes: usize,
    /// The other game has a zone loaded.
    pub remote_in_zone: bool,
}

impl LiveStatus {
    /// Both games are in a zone and joined.
    pub fn is_live(&self) -> bool {
        self.game_connected && self.game_in_zone && self.remotes > 0 && self.remote_in_zone
    }
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
    if length > MAX_PAYLOAD {
        return Err(io::Error::new(ErrorKind::InvalidData, "frame too large"));
    }
    let mut frame = vec![0u8; HEADER + length];
    frame[..HEADER].copy_from_slice(&header);
    reader.read_exact(&mut frame[HEADER..])?;
    Ok(Some(frame))
}

/// One connection of the relay. Writes go through a queue and a thread of
/// their own so a peer that is slow to read never holds up the others.
struct Peer {
    id: u64,
    stream: TcpStream,
    queue: Sender<Vec<u8>>,
    queued: Arc<AtomicUsize>,
    /// For a remote, the silence it is held to once it has shown that it
    /// beats; taken when the read timeout is set.
    silence: Option<Duration>,
}

impl Peer {
    /// `timing` is given for a remote and `None` for the game.
    fn new(id: u64, stream: &TcpStream, timing: Option<Timing>) -> io::Result<Self> {
        let mut writer = stream.try_clone()?;
        if let Some(timing) = timing {
            writer.set_write_timeout(Some(timing.stall))?;
        }
        let stream = stream.try_clone()?;
        let (queue, frames) = mpsc::channel::<Vec<u8>>();
        let queued = Arc::new(AtomicUsize::new(0));
        let beats = timing.map(|timing| timing.beat);
        thread::spawn({
            let queued = queued.clone();
            move || {
                while let Some((frame, from_queue)) = next_frame(&frames, beats) {
                    let written = writer.write_all(&frame);
                    queued.fetch_sub(from_queue, Ordering::Relaxed);
                    if written.is_err() {
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
            silence: timing.map(|timing| timing.silence),
        };
        if timing.is_some() {
            // Straight away, so the far end learns that this launcher beats
            // even if the game keeps it too busy to need another for long.
            peer.send(beat());
        }
        Ok(peer)
    }

    /// A remote that beats can be held to it: from now on, silence ends it.
    /// A launcher from before beats is left to TCP to notice, as it was then.
    fn heard_beat(&mut self) {
        if let Some(silence) = self.silence.take() {
            // Options set on one handle of a stream apply to all of them, the
            // reader thread's included.
            let _ = self.stream.set_read_timeout(Some(silence));
        }
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
        let _ = self.stream.shutdown(Shutdown::Both);
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

#[derive(Default)]
struct State {
    game: Option<Peer>,
    remotes: Vec<Peer>,
    next_id: u64,
    status: LiveStatus,
    /// The latest lines both mods logged, and how many there have been.
    log: std::collections::VecDeque<String>,
    logged: u64,
}

impl State {
    fn link(&self) -> Vec<u8> {
        control(serde_json::json!({"type": "link", "remotes": self.remotes.len()}))
    }

    fn tell_game_about_remotes(&mut self) {
        self.status.remotes = self.remotes.len();
        if self.remotes.is_empty() {
            self.status.remote_in_zone = false;
        }
        if let Some(game) = &self.game {
            game.send(self.link());
        }
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
}

impl Inner {
    fn attach(self: &Arc<Self>, side: Side, stream: TcpStream) -> io::Result<()> {
        let _ = stream.set_nodelay(true);
        let mut state = self.state.lock().unwrap();
        if self.stop.load(Ordering::Relaxed) {
            let _ = stream.shutdown(Shutdown::Both);
            return Ok(());
        }
        state.next_id += 1;
        let id = state.next_id;
        let peer = Peer::new(id, &stream, (side == Side::Remote).then_some(self.timing))?;
        match side {
            Side::Game => {
                // A game that reconnects without the old socket having died.
                if let Some(old) = state.game.replace(peer) {
                    old.close();
                }
                state.status.game_connected = true;
                state.status.game_in_zone = false;
                let link = state.link();
                state.game.as_ref().unwrap().send(link);
            }
            Side::Remote => {
                // Two players: a new remote is the same one reconnecting.
                for old in state.remotes.drain(..) {
                    old.close();
                }
                state.remotes.push(peer);
                state.status.remote_in_zone = false;
                state.tell_game_about_remotes();
            }
        }
        drop(state);
        let inner = self.clone();
        thread::spawn(move || {
            let mut reader = io::BufReader::with_capacity(1 << 16, stream);
            // A remote's read timeout, once set, ends this like any error:
            // the remote has gone silent.
            while let Ok(Some(frame)) = read_frame(&mut reader) {
                if !inner.forward(side, id, frame) {
                    break;
                }
            }
            let _ = reader.get_ref().shutdown(Shutdown::Both);
            inner.detach(side, id);
        });
        Ok(())
    }

    /// Returns false once the peer is no longer part of the relay.
    fn forward(&self, side: Side, id: u64, frame: Vec<u8>) -> bool {
        let mut state = self.state.lock().unwrap();
        if side == Side::Remote && is_beat(&frame) {
            // A beat only shows that the remote is there; the game never hears of it.
            return match state.remotes.iter_mut().find(|remote| remote.id == id) {
                Some(remote) => {
                    remote.heard_beat();
                    true
                }
                None => false,
            };
        }
        let in_zone = snoop_state(&frame);
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
                if let Some(in_zone) = in_zone {
                    state.status.game_in_zone = in_zone;
                }
                let (last, rest) = match state.remotes.split_last() {
                    Some(split) => split,
                    None => return true,
                };
                for remote in rest {
                    remote.send(frame.clone());
                }
                last.send(frame);
            }
            Side::Remote => {
                if !state.remotes.iter().any(|remote| remote.id == id) {
                    return false;
                }
                match in_zone {
                    Some(in_zone) => state.status.remote_in_zone = in_zone,
                    None if is_gone(&frame) => state.status.remote_in_zone = false,
                    None => {}
                }
                if let Some(game) = &state.game {
                    game.send(frame);
                }
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
                state.status.game_connected = false;
                state.status.game_in_zone = false;
                for remote in &state.remotes {
                    remote.send(control(serde_json::json!({"type": "gone"})));
                }
            }
            Side::Remote => {
                let before = state.remotes.len();
                state.remotes.retain(|remote| remote.id != id);
                if state.remotes.len() != before {
                    state.tell_game_about_remotes();
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
            remote.close();
        }
        state.status = LiveStatus::default();
    }
}

fn control_type(frame: &[u8]) -> Option<serde_json::Value> {
    if frame.first() != Some(&KIND_CONTROL) {
        return None;
    }
    serde_json::from_slice(&frame[HEADER..]).ok()
}

/// The `in_zone` of a `state` control frame.
fn snoop_state(frame: &[u8]) -> Option<bool> {
    let value = control_type(frame)?;
    if value.get("type")?.as_str()? != "state" {
        return None;
    }
    value.get("in_zone")?.as_bool()
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
    pub fn start() -> Result<Self> {
        Self::with_timing(Timing::default())
    }

    /// A relay that keeps its remotes to `timing`; tests shorten it.
    pub(crate) fn with_timing(timing: Timing) -> Result<Self> {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
        listener.set_nonblocking(true)?;
        let port = listener.local_addr()?.port();
        let inner = Arc::new(Inner {
            stop: AtomicBool::new(false),
            state: Mutex::new(State::default()),
            timing,
        });
        let thread = thread::spawn({
            let inner = inner.clone();
            move || {
                while !inner.stop.load(Ordering::Relaxed) {
                    match listener.accept() {
                        Ok((stream, _)) => {
                            if stream.set_nonblocking(false).is_ok() {
                                let _ = inner.attach(Side::Game, stream);
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
        self.inner.state.lock().unwrap().status
    }

    /// How many lines the mods have logged so far, to notice new ones cheaply.
    pub fn logged(&self) -> u64 {
        self.inner.state.lock().unwrap().logged
    }

    /// The latest lines logged by the mods of both games, oldest first.
    pub fn log(&self) -> Vec<String> {
        self.inner.state.lock().unwrap().log.iter().cloned().collect()
    }

    /// Join a connection to another launcher, already past its handshake,
    /// to the relay. It replaces any earlier remote.
    pub fn add_remote(&self, stream: TcpStream) -> Result<()> {
        Ok(self.inner.attach(Side::Remote, stream)?)
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
    thread: Option<JoinHandle<()>>,
}

impl LiveLink {
    pub fn start(host: Route, hello: Hello) -> Result<Self> {
        let relay = Arc::new(Relay::start()?);
        let stop = Arc::new(AtomicBool::new(false));
        let thread = thread::spawn({
            let relay = relay.clone();
            let stop = stop.clone();
            move || {
                while !stop.load(Ordering::Relaxed) {
                    if relay.status().remotes == 0
                        && let Ok(stream) = dial(&host, &hello)
                    {
                        let _ = relay.add_remote(stream);
                    }
                    sleep_unless(REDIAL, &stop);
                }
            }
        });
        Ok(Self {
            relay,
            stop,
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
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.relay.close();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// Connect to a host and ask to join its live session. On success the
/// stream carries frames from here on.
fn dial(host: &Route, hello: &Hello) -> Result<TcpStream> {
    let stream = host.open()?;
    stream.set_read_timeout(Some(HANDSHAKE_TIMEOUT))?;
    write_message(&mut &stream, &Message::JoinLive(hello.clone()))?;
    // Unbuffered on purpose: nothing past the reply may be consumed here.
    match expect_message(&mut &stream)? {
        Message::Hello(host) if host.protocol == PROTOCOL_VERSION => {}
        Message::Hello(host) => {
            return Err(Error::VersionMismatch {
                ours: PROTOCOL_VERSION,
                theirs: host.protocol,
            });
        }
        Message::Error { message } => return Err(Error::Remote(message)),
        _ => return Err(Error::Protocol("unexpected reply to join_live".into())),
    }
    stream.set_read_timeout(None)?;
    Ok(stream)
}

#[cfg(test)]
mod tests {
    use std::time::Instant;

    use tsmp_core::manifest::Manifest;

    use std::net::SocketAddr;

    use super::*;
    use crate::host::{Host, HostConfig};

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
        }
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
        };
        (Host::start(config, port, false).unwrap(), dir)
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
        let relay = Relay::start().unwrap();
        let mut game = Game::connect(relay.port());
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));

        let (near, far) = pair();
        relay.add_remote(near).unwrap();
        let mut remote = Game::remote(far);
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));

        game.send(KIND_MSG, 11, b"to the remote");
        assert_eq!(remote.receive(), (KIND_MSG, 11, b"to the remote".to_vec()));
        remote.send(KIND_COMMAND, 0, b"to the game");
        assert_eq!(game.receive(), (KIND_COMMAND, 0, b"to the game".to_vec()));

        game.state("host", true);
        remote.receive();
        remote.state("guest", true);
        game.receive();
        assert!(relay.status().is_live());

        drop(remote);
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));
        assert_eq!(
            relay.status(),
            LiveStatus {
                game_connected: true,
                game_in_zone: true,
                remotes: 0,
                remote_in_zone: false
            }
        );
    }

    #[test]
    fn a_remote_hears_when_the_game_goes_away() {
        let relay = Relay::start().unwrap();
        let (near, far) = pair();
        relay.add_remote(near).unwrap();
        let mut remote = Game::remote(far);

        let mut game = Game::connect(relay.port());
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
        let relay = Relay::start().unwrap();
        let mut old = Game::connect(relay.port());
        old.receive_control();
        let mut new = Game::connect(relay.port());
        new.receive_control();

        // The old connection is closed by the relay.
        assert!(matches!(read_frame(&mut old.stream), Ok(None) | Err(_)));
        let (near, far) = pair();
        relay.add_remote(near).unwrap();
        let mut remote = Game::remote(far);
        new.receive_control();
        new.send(KIND_MSG, 1, b"x");
        assert_eq!(remote.receive(), (KIND_MSG, 1, b"x".to_vec()));
        assert!(relay.status().game_connected);
    }

    #[test]
    fn large_frames_cross_intact_while_the_other_way_is_busy() {
        let relay = Relay::start().unwrap();
        let mut game = Game::connect(relay.port());
        game.receive_control();
        let (near, far) = pair();
        relay.add_remote(near).unwrap();
        let mut remote = Game::remote(far);
        game.receive_control();

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
        let relay = Relay::with_timing(QUICK).unwrap();
        let mut game = Game::connect(relay.port());
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));
        let (near, far) = pair();
        relay.add_remote(near).unwrap();
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 1}));
        (relay, game, Game::remote(far))
    }

    #[test]
    fn a_remote_that_falls_silent_is_dropped() {
        let (relay, mut game, mut remote) = joined();
        let since = Instant::now();
        remote.beat();

        // The far end stays open but writes and reads nothing more.
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));
        assert!(since.elapsed() >= QUICK.silence);
        assert_eq!(relay.status().remotes, 0);
        drop(remote);
    }

    #[test]
    fn a_remote_that_never_beats_is_not_held_to_silence() {
        let (relay, mut game, mut remote) = joined();
        thread::sleep(QUICK.silence * 3);
        assert_eq!(relay.status().remotes, 1);
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
        assert_eq!(relay.status().remotes, 1);
    }

    #[test]
    fn an_idle_remote_hears_beats_and_the_game_does_not() {
        let (_relay, mut game, remote) = joined();
        let since = Instant::now();
        for _ in 0..3 {
            let frame = read_frame(&mut &remote.stream).unwrap().unwrap();
            assert!(is_beat(&frame));
        }
        // The first comes at once, the next two after the remote has been idle.
        assert!(since.elapsed() >= QUICK.beat * 2);

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
        assert_eq!(game.receive_control(), serde_json::json!({"type": "link", "remotes": 0}));
        drop(remote);
    }

    #[test]
    fn guest_and_host_games_are_joined_through_both_launchers() {
        let (host, _dir) = start_host(0);
        let link = LiveLink::start(Route::Direct((Ipv4Addr::LOCALHOST, host.port()).into()), hello("guest")).unwrap();

        let mut host_game = Game::connect(host.live().port());
        let mut guest_game = Game::connect(link.port());
        wait_for("the launchers to connect", || {
            host.live().status().remotes == 1 && link.status().remotes == 1
        });

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
        wait_for("the guest to see the host's game leave", || !link.status().remote_in_zone);
    }

    #[test]
    fn the_guest_keeps_dialling_until_the_host_appears() {
        // Reserve a port, then free it for the host that starts late.
        let port = TcpListener::bind((Ipv4Addr::UNSPECIFIED, 0)).unwrap().local_addr().unwrap().port();
        let link = LiveLink::start(Route::Direct((Ipv4Addr::LOCALHOST, port).into()), hello("guest")).unwrap();
        thread::sleep(Duration::from_millis(300));
        assert_eq!(link.status().remotes, 0);

        let (host, _dir) = start_host(port);
        wait_for("the guest to reach the late host", || link.status().remotes == 1);
        wait_for("the host to see the guest", || host.live().status().remotes == 1);

        drop(host);
        wait_for("the guest to notice the host left", || link.status().remotes == 0);
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
        assert_eq!(host.live().status().remotes, 0);
    }
}
