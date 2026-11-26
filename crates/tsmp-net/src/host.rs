//! The host side: accepts guests and serves them the mods and the shared save.
//!
//! Guests arrive on the host's port from the local network, or through a
//! session server once the host is published there ([`Host::publish`]).
//! Either way the connection is handed to the same [`Lobby`].
//!
//! A session takes up to [`HostConfig::max_players`] players, the host
//! included. A player is told apart by the id in their `Hello`, so one who
//! comes back is let in again while the session is full; a guest that plays
//! keeps a sync connection and a live one open, and counts once.

use std::collections::HashMap;
use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, ErrorKind, Read, Write};
use std::net::{Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex, Weak};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use tsmp_core::manifest::{Manifest, hash_file};

use crate::discovery::{Announcer, Beacon};
use crate::live::{PeerId, Refusal, Relay, RemoteEvent, Role, Timing};
use crate::paths::safe_join;
use crate::protocol::{
    GuestStatus, Hello, Message, PROTOCOL_VERSION, Root, SaveInfo, Session, read_message, speaks, write_message,
};
use crate::rendezvous::{Listing, ServerAddress};
use crate::{Error, POLL, Result, file_err, keep_alive};

/// How long a write to a guest, or a message from it that has started to
/// arrive, may make no progress before the guest counts as gone. The guest
/// asked for what is being written and reads it straight away, and its own
/// messages are small and sent in one go, so a stall this long means it is
/// not there. It also bounds how long closing the host can leave a guest's
/// thread behind: on Windows, shutting a socket down does not wake a read or
/// a write on another handle of it.
const STALL: Duration = Duration::from_secs(30);

/// Players a session takes unless told otherwise, the host included.
pub const DEFAULT_MAX_PLAYERS: usize = 2;
/// The most players a session can be set to take.
pub const MAX_PLAYERS: usize = 8;
/// What a guest that would be one player too many is told, in place of the
/// host's `Hello`.
pub const SESSION_FULL: &str = "session is full";
/// What a guest is told in place of the host's `Hello` when there may be room
/// for it, but a launcher from before player ids is in the session while it
/// asks, or asks while others are: such a launcher cannot be told from
/// another of its kind, so it plays only as the one guest.
pub const SESSION_MIXED: &str =
    "a player with another version of TSMP is in the session; update TSMP on every computer";

pub struct HostConfig {
    pub hello: Hello,
    pub mods_dir: PathBuf,
    /// A scan of `mods_dir`. Only files listed here are ever served.
    pub mods: Manifest,
    /// The shared save file, if one has been chosen.
    pub save: Option<PathBuf>,
    /// Every player loads a save of their own; see [`Session::own_families`].
    pub own_families: bool,
    /// Players the session takes, the host included: [`DEFAULT_MAX_PLAYERS`]
    /// unless set, and never fewer than two or more than [`MAX_PLAYERS`].
    pub max_players: usize,
}

impl Default for HostConfig {
    fn default() -> Self {
        Self {
            hello: Hello::default(),
            mods_dir: PathBuf::new(),
            mods: Manifest::default(),
            save: None,
            own_families: false,
            max_players: DEFAULT_MAX_PLAYERS,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HostEvent {
    GuestJoined { id: u64, address: SocketAddr, hello: Hello },
    GuestStatus { id: u64, status: GuestStatus },
    GuestLeft { id: u64 },
    /// A guest's live connection joined the relay as `peer`, or replaced
    /// the one it had. `player` is the id in its `Hello`, which ties it to its
    /// `GuestJoined`.
    LiveJoined { player: Option<String>, peer: PeerId },
    /// The live connection of `peer` ended and no newer one replaced it.
    LiveLeft { peer: PeerId },
}

/// What every guest is served.
struct Shared {
    hello: Hello,
    mods_dir: PathBuf,
    session: Session,
    save: Option<PathBuf>,
    live: Relay,
    max_players: usize,
}

/// Takes in guests, however they reached the host, and serves each one on a
/// thread of its own.
struct Lobby {
    shared: Shared,
    events: Sender<HostEvent>,
    stop: AtomicBool,
    /// Connections being served, so that closing the lobby ends them. A
    /// connection is taken out of here once it is closed.
    connections: Mutex<HashMap<u64, TcpStream>>,
    /// The player of each connection past its `Hello`, until it ends or, for
    /// a live one, the relay has it.
    players: Mutex<HashMap<u64, Option<String>>>,
    next_id: AtomicU64,
    /// [`STALL`], shorter in tests.
    stall: Duration,
}

impl Lobby {
    fn welcome(self: &Arc<Self>, stream: TcpStream, address: SocketAddr) {
        // Some platforms hand out accepted sockets non-blocking when the
        // listener is.
        if stream.set_nonblocking(false).is_err() {
            return;
        }
        let _ = stream.set_nodelay(true);
        keep_alive(&stream);
        let Ok(handle) = stream.try_clone() else { return };
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        {
            let mut connections = self.connections.lock().unwrap();
            if self.stop.load(Ordering::Relaxed) {
                let _ = stream.shutdown(Shutdown::Both);
                return;
            }
            connections.insert(id, handle);
        }
        let lobby = self.clone();
        thread::spawn(move || {
            // Errors here mean this guest went away or misbehaved; either way
            // the connection is over and the host carries on.
            let served = lobby.serve(stream, address, id).unwrap_or(Served::Refused);
            lobby.players.lock().unwrap().remove(&id);
            if let Some(handle) = lobby.connections.lock().unwrap().remove(&id) {
                // A live connection belongs to the relay now.
                if !matches!(served, Served::Live) {
                    let _ = handle.shutdown(Shutdown::Both);
                }
            }
            if matches!(served, Served::Synced) {
                let _ = lobby.events.send(HostEvent::GuestLeft { id });
            }
        });
    }

    /// Whether the connection `id` is still being served: neither the lobby
    /// nor the connection has been closed.
    fn serving(&self, id: u64) -> bool {
        !self.stop.load(Ordering::Relaxed) && self.connections.lock().unwrap().contains_key(&id)
    }

    /// The players in the session, the host included.
    fn player_count(&self) -> u32 {
        let mut players: Vec<Option<String>> = self.players.lock().unwrap().values().cloned().collect();
        players.extend(self.shared.live.players());
        players.sort();
        players.dedup();
        players.len() as u32 + 1
    }

    fn close(&self) {
        self.stop.store(true, Ordering::Relaxed);
        for (_, stream) in self.connections.lock().unwrap().drain() {
            let _ = stream.shutdown(Shutdown::Both);
        }
        self.shared.live.close();
    }
}

/// A running host. Dropping it disconnects every guest and takes it off the
/// session server.
pub struct Host {
    port: u16,
    events: Receiver<HostEvent>,
    lobby: Arc<Lobby>,
    thread: Option<JoinHandle<()>>,
    listing: Option<Listing>,
    _announcer: Option<Announcer>,
}

impl Host {
    /// Listen on `port` (0 picks a free one) and, if `announce`, broadcast a
    /// discovery beacon.
    pub fn start(config: HostConfig, port: u16, announce: bool) -> Result<Self> {
        Self::start_with_stall(config, port, announce, STALL)
    }

    fn start_with_stall(config: HostConfig, port: u16, announce: bool, stall: Duration) -> Result<Self> {
        let save_info = match &config.save {
            Some(path) => Some(SaveInfo {
                file_name: path
                    .file_name()
                    .map(|name| name.to_string_lossy().into_owned())
                    .ok_or_else(|| Error::Protocol(format!("{} is not a file", path.display())))?,
                size: path.metadata().map_err(file_err(path))?.len(),
                hash: hash_file(path)?,
            }),
            None => None,
        };
        let listener = TcpListener::bind((Ipv4Addr::UNSPECIFIED, port))?;
        listener.set_nonblocking(true)?;
        let port = listener.local_addr()?.port();
        let max_players = config.max_players.clamp(2, MAX_PLAYERS);

        let (events, receiver) = mpsc::channel();
        let on_remote = {
            let events: Sender<HostEvent> = events.clone();
            move |event| {
                let _ = events.send(match event {
                    RemoteEvent::Joined { peer, player } => HostEvent::LiveJoined { player, peer },
                    RemoteEvent::Left { peer } => HostEvent::LiveLeft { peer },
                });
            }
        };
        let beacon = Beacon::new(
            PROTOCOL_VERSION,
            config.hello.name.clone(),
            port,
            config.hello.game_version.clone(),
        );
        let lobby = Arc::new(Lobby {
            shared: Shared {
                hello: config.hello,
                mods_dir: config.mods_dir,
                session: Session {
                    mods: config.mods,
                    save: save_info,
                    own_families: config.own_families,
                },
                save: config.save,
                live: Relay::with(Role::Host { max_players }, Timing::default(), Some(Box::new(on_remote)))?,
                max_players,
            },
            events,
            stop: AtomicBool::new(false),
            connections: Mutex::new(HashMap::new()),
            players: Mutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
            stall,
        });

        let announcer = if announce {
            // Weak: the announcer is the host's, and goes with it.
            let lobby: Weak<Lobby> = Arc::downgrade(&lobby);
            Some(Announcer::start_with(move || {
                let players = lobby.upgrade().map_or(1, |lobby| lobby.player_count());
                beacon.clone().with_players(players, max_players as u32)
            })?)
        } else {
            None
        };
        let thread = thread::spawn({
            let lobby = lobby.clone();
            move || {
                while !lobby.stop.load(Ordering::Relaxed) {
                    match listener.accept() {
                        Ok((stream, address)) => lobby.welcome(stream, address),
                        Err(e) if e.kind() == ErrorKind::WouldBlock => thread::sleep(POLL),
                        Err(_) => {}
                    }
                }
            }
        });
        Ok(Self {
            port,
            events: receiver,
            lobby,
            thread: Some(thread),
            listing: None,
            _announcer: announcer,
        })
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    /// The live-session relay. Guests that ask to join live are attached to
    /// it; the mod in the host's game connects to its loopback port.
    pub fn live(&self) -> &Relay {
        &self.lobby.shared.live
    }

    /// List the session on a session server too, so that guests outside the
    /// local network can join with an invite. Replaces an earlier listing.
    pub fn publish(&mut self, server: ServerAddress) {
        let lobby = self.lobby.clone();
        let hello = &self.lobby.shared.hello;
        self.listing = Some(Listing::start(
            server,
            hello.name.clone(),
            hello.game_version.clone(),
            move |stream, from| lobby.welcome(stream, from),
        ));
    }

    /// Take the session off the session server. Guests already in stay.
    pub fn unpublish(&mut self) {
        self.listing = None;
    }

    pub fn listing(&self) -> Option<&Listing> {
        self.listing.as_ref()
    }

    /// Events since the last call, without blocking.
    pub fn poll(&self) -> Vec<HostEvent> {
        self.events.try_iter().collect()
    }

    pub fn wait(&self, timeout: Duration) -> Option<HostEvent> {
        self.events.recv_timeout(timeout).ok()
    }
}

impl Drop for Host {
    fn drop(&mut self) {
        self.listing = None;
        self.lobby.close();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

enum Served {
    /// Never got past the handshake.
    Refused,
    /// A sync connection that joined and has now ended.
    Synced,
    /// A live connection, handed to the relay.
    Live,
}

impl Lobby {
    fn serve(&self, stream: TcpStream, address: SocketAddr, id: u64) -> Result<Served> {
        let shared = &self.shared;
        let events = &self.events;
        let mut reader = BufReader::new(stream.try_clone()?);
        let mut writer = BufWriter::with_capacity(1 << 16, stream);

        if !self.wait_for_message(&mut reader, id)? {
            return Ok(Served::Refused);
        }
        let (hello, live) = match read_message(&mut reader)? {
            Some(Message::Hello(hello)) => (hello, false),
            Some(Message::JoinLive(hello)) => (hello, true),
            _ => {
                write_message(&mut writer, &error("expected hello"))?;
                return Ok(Served::Refused);
            }
        };
        if !speaks(hello.protocol) {
            // Our own version, which the guest tells the player is not theirs.
            write_message(&mut writer, &Message::Hello(shared.hello.clone()))?;
            return Ok(Served::Refused);
        }
        let reply = match self.admit(&hello, id) {
            Ok(reply) => reply,
            // Before any `Hello`, so that the guest says why rather than that
            // the connection closed.
            Err(refusal) => {
                let message = match refusal {
                    Refusal::Full => SESSION_FULL,
                    Refusal::Legacy => SESSION_MIXED,
                };
                write_message(&mut writer, &error(message))?;
                return Ok(Served::Refused);
            }
        };
        write_message(&mut writer, &Message::Hello(reply))?;
        if live {
            // The guest sends nothing more until it has read the reply, so
            // nothing of the frame stream can be sitting in the buffer.
            if !reader.buffer().is_empty() {
                return Ok(Served::Refused);
            }
            // The relay sets the timeouts it wants on its own handles.
            reader.get_ref().set_read_timeout(None)?;
            drop(writer);
            // Full only if another player took the last place since the
            // reply; the relay has closed the connection then.
            return match shared.live.add_remote(reader.into_inner(), hello.player, hello.name) {
                Ok(_) => Ok(Served::Live),
                Err(Error::Full) => Ok(Served::Refused),
                Err(e) => Err(e),
            };
        }
        // Options are set on the handle they are for: on Windows they do not
        // carry over to other handles of the same socket.
        writer.get_ref().set_write_timeout(Some(self.stall))?;
        let _ = events.send(HostEvent::GuestJoined { id, address, hello });

        let result = (|| -> Result<()> {
            while self.wait_for_message(&mut reader, id)?
                && let Some(message) = read_message(&mut reader)?
            {
                match message {
                    Message::GetSession => {
                        write_message(&mut writer, &Message::Session(shared.session.clone()))?;
                    }
                    Message::GetFile { root, path } => send_file(&mut writer, shared, root, &path)?,
                    Message::Status(status) => {
                        let _ = events.send(HostEvent::GuestStatus { id, status });
                    }
                    _ => write_message(&mut writer, &error("unexpected message"))?,
                }
            }
            Ok(())
        })();
        // The guest did join, whatever ended the connection.
        let _ = result;
        Ok(Served::Synced)
    }

    /// The host's `Hello` to a guest that says `hello`, if the session has a
    /// place for it, and the guest counted in from now on: here, until the
    /// connection ends or, for a live one, the relay has it and counts it.
    /// Otherwise why it has none.
    ///
    /// The reply is in the guest's own version of the protocol, which is all
    /// a launcher before version 3 checks.
    fn admit(&self, hello: &Hello, id: u64) -> std::result::Result<Hello, Refusal> {
        let shared = &self.shared;
        // Held while deciding, so that two guests cannot take one place.
        let mut players = self.players.lock().unwrap();
        let mut present: Vec<Option<String>> = players.values().cloned().collect();
        shared.live.admits(&hello.player, &present)?;
        players.insert(id, hello.player.clone());
        present.extend(shared.live.players());
        present.push(hello.player.clone());
        present.sort();
        present.dedup();
        Ok(Hello {
            protocol: hello.protocol,
            peer: shared.live.peer_for(&hello.player),
            players: present.len() as u32 + 1,
            max_players: shared.max_players as u32,
            ..shared.hello.clone()
        })
    }

    /// Waits until the guest's next message starts to arrive, or its
    /// connection ends. Returns false if the lobby or this connection was
    /// closed meanwhile.
    ///
    /// A guest that has synced keeps its connection open and says nothing
    /// for as long as it plays, and on Windows shutting the connection down
    /// from [`Lobby::close`] does not wake a read on this handle. So the wait
    /// is made of short reads that look in between whether to give up. These
    /// only ever happen between messages, so none of a message is lost to
    /// them. Once its first byte is in, the rest may take up to [`STALL`]
    /// between reads, which a guest that is still there never comes near.
    fn wait_for_message(&self, reader: &mut BufReader<TcpStream>, id: u64) -> Result<bool> {
        if !reader.buffer().is_empty() {
            return Ok(true);
        }
        reader.get_ref().set_read_timeout(Some(POLL))?;
        let waited = loop {
            match reader.fill_buf() {
                // Empty when the connection ended; the read that follows says so.
                Ok(_) => break Ok(true),
                Err(e) if timed_out(&e) => {
                    if !self.serving(id) {
                        break Ok(false);
                    }
                }
                Err(e) if e.kind() == ErrorKind::Interrupted => {}
                Err(e) => break Err(e.into()),
            }
        };
        reader.get_ref().set_read_timeout(Some(self.stall))?;
        waited
    }
}

/// Whether a read gave up because its timeout passed: `TimedOut` on Windows,
/// `WouldBlock` elsewhere.
fn timed_out(error: &io::Error) -> bool {
    matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut)
}

fn send_file(writer: &mut impl Write, shared: &Shared, root: Root, path: &str) -> Result<()> {
    let local = match root {
        Root::Mods if shared.session.mods.files.contains_key(path) => safe_join(&shared.mods_dir, path),
        Root::Mods => None,
        Root::Save => shared.save.clone(),
    };
    let Some(local) = local else {
        return write_message(writer, &error(&format!("no such file: {path}")));
    };
    let opened = File::open(&local).and_then(|file| Ok((file.metadata()?.len(), file)));
    let (size, file) = match opened {
        Ok(opened) => opened,
        Err(e) => return write_message(writer, &error(&format!("{path}: {e}"))),
    };
    write_message(writer, &Message::File { size })?;
    // Exactly `size` bytes must follow, even if the file changes underneath.
    let copied = io::copy(&mut file.take(size), writer)?;
    if copied != size {
        return Err(Error::Protocol(format!("{path} shrank while being sent")));
    }
    writer.flush()?;
    Ok(())
}

fn error(message: &str) -> Message {
    Message::Error {
        message: message.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Weak;
    use std::time::Instant;

    use super::*;

    const WAIT: Duration = Duration::from_secs(10);

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

    fn start_host() -> (Host, tempfile::TempDir) {
        start_host_with(DEFAULT_MAX_PLAYERS, STALL)
    }

    fn start_host_with_stall(stall: Duration) -> (Host, tempfile::TempDir) {
        start_host_with(DEFAULT_MAX_PLAYERS, stall)
    }

    fn start_host_with(max_players: usize, stall: Duration) -> (Host, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        let config = HostConfig {
            hello: hello("host"),
            mods_dir: dir.path().to_path_buf(),
            max_players,
            ..HostConfig::default()
        };
        (Host::start_with_stall(config, 0, false, stall).unwrap(), dir)
    }

    /// A connection that has said `hello`, as a sync one or a live one, and
    /// the host's answer.
    fn say(host: &Host, hello: Hello, live: bool) -> (TcpStream, Message) {
        let guest = connect(host);
        let message = if live { Message::JoinLive(hello) } else { Message::Hello(hello) };
        write_message(&mut &guest, &message).unwrap();
        let reply = read_message(&mut &guest).unwrap().expect("the host closed the connection");
        (guest, reply)
    }

    fn wait_for_event(host: &Host, what: impl Fn(&HostEvent) -> bool) -> HostEvent {
        let deadline = Instant::now() + WAIT;
        loop {
            let left = deadline.saturating_duration_since(Instant::now());
            let event = host.wait(left).expect("timed out waiting for an event");
            if what(&event) {
                return event;
            }
        }
    }

    fn wait_until(what: &str, condition: impl Fn() -> bool) {
        let deadline = Instant::now() + WAIT;
        while !condition() {
            assert!(Instant::now() < deadline, "timed out waiting for {what}");
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn full() -> Message {
        Message::Error {
            message: SESSION_FULL.into(),
        }
    }

    #[test]
    fn the_reply_says_which_peer_the_guest_is_and_how_many_play() {
        let (host, _dir) = start_host_with(4, STALL);
        let (_a, reply) = say(&host, hello("a"), false);
        let Message::Hello(reply) = reply else { panic!("{reply:?}") };
        assert_eq!((reply.protocol, reply.peer, reply.players, reply.max_players), (PROTOCOL_VERSION, 1, 2, 4));
        assert_eq!(reply.player.as_deref(), Some("host-id"));
        let (_b, reply) = say(&host, hello("b"), false);
        let Message::Hello(reply) = reply else { panic!("{reply:?}") };
        assert_eq!((reply.peer, reply.players), (2, 3));
        // The same player on a second connection is the same peer, counted once.
        let (_a_live, reply) = say(&host, hello("a"), true);
        let Message::Hello(reply) = reply else { panic!("{reply:?}") };
        assert_eq!((reply.peer, reply.players), (1, 3));
        assert_eq!(host.lobby.player_count(), 3);
    }

    #[test]
    fn a_full_session_refuses_a_new_player_before_its_hello() {
        let (host, _dir) = start_host();
        let (_a, reply) = say(&host, hello("a"), false);
        assert!(matches!(reply, Message::Hello(_)));
        wait_for_event(&host, |event| matches!(event, HostEvent::GuestJoined { .. }));

        for live in [false, true] {
            let (b, reply) = say(&host, hello("b"), live);
            assert_eq!(reply, full());
            assert!(matches!(read_message::<Message>(&mut &b), Ok(None) | Err(_)));
        }
        assert!(host.live().status().remotes.is_empty());
        // The player who is in may open its live connection, and come back.
        let (_live, reply) = say(&host, hello("a"), true);
        assert!(matches!(reply, Message::Hello(_)));
        wait_until("a's live connection", || host.live().status().remotes.len() == 1);
        let (_again, reply) = say(&host, hello("a"), false);
        assert!(matches!(reply, Message::Hello(_)));
        // b was never let in.
        let b_joined = |event: &HostEvent| matches!(event, HostEvent::GuestJoined { hello, .. } if hello.name == "b");
        assert!(!host.poll().iter().any(b_joined));
    }

    #[test]
    fn a_guest_that_plays_counts_once_until_both_its_connections_end() {
        let (host, _dir) = start_host();
        let (sync, _) = say(&host, hello("a"), false);
        let (live, _) = say(&host, hello("a"), true);
        wait_until("a's live connection", || host.live().status().remotes.len() == 1);
        drop(sync);
        wait_for_event(&host, |event| matches!(event, HostEvent::GuestLeft { .. }));
        assert_eq!(say(&host, hello("b"), false).1, full());
        drop(live);
        wait_for_event(&host, |event| matches!(event, HostEvent::LiveLeft { .. }));
        assert!(matches!(say(&host, hello("b"), false).1, Message::Hello(_)));
    }

    #[test]
    fn live_connections_are_told_as_they_join_and_leave() {
        let (host, _dir) = start_host();
        let (first, _) = say(&host, hello("a"), true);
        assert_eq!(
            wait_for_event(&host, |event| matches!(event, HostEvent::LiveJoined { .. })),
            HostEvent::LiveJoined {
                player: Some("a-id".into()),
                peer: 1
            }
        );
        // A reconnection joins again, as the same peer, and nothing leaves.
        let (second, _) = say(&host, hello("a"), true);
        assert!(matches!(
            wait_for_event(&host, |event| matches!(event, HostEvent::LiveJoined { .. } | HostEvent::LiveLeft { .. })),
            HostEvent::LiveJoined { peer: 1, .. }
        ));
        drop(first);
        drop(second);
        assert_eq!(
            wait_for_event(&host, |event| matches!(event, HostEvent::LiveLeft { .. })),
            HostEvent::LiveLeft { peer: 1 }
        );
        assert!(host.poll().is_empty());
    }

    #[test]
    fn a_guest_of_version_2_joins_as_the_one_guest_and_is_answered_in_its_version() {
        let (host, _dir) = start_host_with(4, STALL);
        let old = Hello {
            protocol: 2,
            player: None,
            ..hello("old")
        };
        let (sync, reply) = say(&host, old.clone(), false);
        // Launchers of version 2 refuse a host that answers in any other.
        let Message::Hello(reply) = reply else { panic!("{reply:?}") };
        assert_eq!(reply.protocol, 2);
        let (stream, reply) = say(&host, old, true);
        assert!(matches!(reply, Message::Hello(Hello { protocol: 2, .. })));
        wait_until("the old guest's live connection", || host.live().status().remotes.len() == 1);
        assert_eq!(host.live().status().remotes[0].player, None);
        // Frames cross as they always did.
        let mut game = TcpStream::connect((Ipv4Addr::LOCALHOST, host.live().port())).unwrap();
        game.set_read_timeout(Some(WAIT)).unwrap();
        wait_until("the host's game", || host.live().status().game_connected);
        // Its mod says its version as soon as it connects; until then what
        // the guest sends waits for it.
        let state = serde_json::json!({"type": "state", "role": "host", "in_zone": true, "v": 2}).to_string();
        game.write_all(&crate::live::frame(crate::live::KIND_CONTROL, 0, state.as_bytes())).unwrap();
        (&stream).write_all(&crate::live::frame(crate::live::KIND_MSG, 3, b"old")).unwrap();
        loop {
            let frame = crate::live::read_frame(&mut game).unwrap().unwrap();
            if frame[0] == crate::live::KIND_MSG {
                assert_eq!(frame, crate::live::frame(crate::live::KIND_MSG, 3, b"old"));
                break;
            }
        }
        // Nobody else joins while it is there, though there would be room,
        // and is told why.
        let mixed = Message::Error {
            message: SESSION_MIXED.into(),
        };
        assert_eq!(say(&host, hello("new"), false).1, mixed);
        // Nor may another of its kind join while others play.
        drop(sync);
        drop(stream);
        wait_until("the old guest to leave", || host.lobby.player_count() == 1);
        let (_new, reply) = say(&host, hello("new"), false);
        assert!(matches!(reply, Message::Hello(_)));
        let other = Hello {
            protocol: 2,
            player: None,
            ..hello("other")
        };
        assert_eq!(say(&host, other, false).1, mixed);
    }

    fn connect(host: &Host) -> TcpStream {
        let stream = TcpStream::connect((Ipv4Addr::LOCALHOST, host.port())).unwrap();
        stream.set_read_timeout(Some(WAIT)).unwrap();
        stream
    }

    #[test]
    fn closing_the_host_lets_go_of_a_guest_that_says_nothing() {
        let (host, _dir) = start_host();
        let guest = connect(&host);
        write_message(&mut &guest, &Message::Hello(hello("guest"))).unwrap();
        assert!(matches!(read_message(&mut &guest).unwrap(), Some(Message::Hello(_))));
        assert!(matches!(host.wait(WAIT), Some(HostEvent::GuestJoined { .. })));
        let lobby: Weak<Lobby> = Arc::downgrade(&host.lobby);

        // The guest stays connected and silent, as one does while it plays.
        drop(host);
        let deadline = Instant::now() + WAIT;
        while lobby.upgrade().is_some() {
            assert!(Instant::now() < deadline, "the guest's thread kept the host alive");
            thread::sleep(Duration::from_millis(10));
        }
        assert!(matches!(read_message::<Message>(&mut &guest), Ok(None) | Err(_)));
    }

    #[test]
    fn a_message_that_arrives_slowly_is_read_whole() {
        let (host, _dir) = start_host();
        let guest = connect(&host);
        let send_slowly = |message: &Message| {
            let mut bytes = Vec::new();
            write_message(&mut bytes, message).unwrap();
            let (start, rest) = bytes.split_at(3);
            (&guest).write_all(start).unwrap();
            // Longer than the host waits between looks at whether to stop.
            thread::sleep(POLL * 3);
            (&guest).write_all(rest).unwrap();
        };

        send_slowly(&Message::Hello(hello("guest")));
        assert!(matches!(read_message(&mut &guest).unwrap(), Some(Message::Hello(_))));
        thread::sleep(POLL * 3);
        send_slowly(&Message::GetSession);
        assert!(matches!(read_message(&mut &guest).unwrap(), Some(Message::Session(_))));
    }

    #[test]
    fn a_guest_that_stops_inside_a_message_is_let_go() {
        let stall = Duration::from_millis(500);
        let (host, _dir) = start_host_with_stall(stall);
        let guest = connect(&host);
        write_message(&mut &guest, &Message::Hello(hello("guest"))).unwrap();
        assert!(matches!(read_message(&mut &guest).unwrap(), Some(Message::Hello(_))));
        assert!(matches!(host.wait(WAIT), Some(HostEvent::GuestJoined { .. })));

        // Part of a length prefix, then nothing: on Windows not even closing
        // the host would end the read that waits for the rest.
        (&guest).write_all(&[1]).unwrap();
        let since = Instant::now();
        assert!(matches!(host.wait(WAIT), Some(HostEvent::GuestLeft { .. })));
        assert!(since.elapsed() >= stall, "let go before the rest had time to arrive");
        assert!(matches!(read_message::<Message>(&mut &guest), Ok(None) | Err(_)));
    }
}
