//! The TSMP session server: where two launchers that cannot reach each other
//! directly meet over the internet.
//!
//! A host lists its session and gets a code; a guest asks for the code; the
//! server has the host open a connection for that guest and joins the two.
//! It never looks inside what passes between them and keeps nothing on
//! disk. The conversation is described in [`tsmp_net::rendezvous`].
//!
//! Everything is blocking and thread based, like the rest of TSMP: a thread
//! per connection, two per joined pair.

mod gate;
mod registry;
mod splice;
mod throttle;

use std::collections::VecDeque;
use std::fmt;
use std::io::{self, ErrorKind, Read};
use std::net::{Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use socket2::{Domain, Protocol, Socket, Type};
use tsmp_core::clock::UtcTime;
use tsmp_net::keep_alive;
use tsmp_net::rendezvous::{
    DEFAULT_SERVER_PORT, Reply, Request, Resume, SERVER_PROTOCOL, SessionCode, expect, receive, send,
};

use crate::gate::{Gate, Peer, Turned};
use crate::registry::{Limits, Line, Refusal, Registry};
use crate::splice::splice;
use crate::throttle::Throttle;

/// How often the server looks at its sessions when nothing else happens.
const TICK: Duration = Duration::from_millis(100);
/// Wrong codes are counted over this long.
const GUESS_WINDOW: Duration = Duration::from_secs(60);
/// The longest host name written to the journal.
const NAME_LENGTH: usize = 64;
/// How long a refused connection stays open after its refusal, for the other
/// side to read it and close first.
const LINGER: Duration = Duration::from_secs(2);
/// Refused connections kept open at once; past this the oldest is let go.
const MAX_LINGERING: usize = 256;
/// Reads of what a refused connection sent, at most, each time it is looked
/// at, so that one that keeps sending cannot hold up the accept loop.
const DRAIN_READS: usize = 16;
/// Connections waiting to be accepted, as std's `TcpListener::bind` has it.
const BACKLOG: i32 = 128;

type WriteLine = dyn Fn(&str) + Send + Sync;

/// Where the server tells what it does. Each line is one event.
#[derive(Clone)]
pub struct Journal(Option<Arc<WriteLine>>);

impl Journal {
    /// To standard error, each line after the time in UTC.
    pub fn stderr() -> Self {
        Self::to(|line| eprintln!("{} {line}", UtcTime::now()))
    }

    pub fn silent() -> Self {
        Self(None)
    }

    pub fn to(write: impl Fn(&str) + Send + Sync + 'static) -> Self {
        Self(Some(Arc::new(write)))
    }

    pub fn line(&self, text: &str) {
        if let Some(write) = &self.0 {
            write(text);
        }
    }

    fn write(&self, args: fmt::Arguments<'_>) {
        if self.0.is_some() {
            self.line(&args.to_string());
        }
    }
}

#[derive(Clone)]
pub struct Config {
    pub listen: SocketAddr,
    pub max_sessions: usize,
    pub max_sessions_per_address: usize,
    pub max_connections: usize,
    pub max_connections_per_address: usize,
    /// Guests that may wait for one host to answer at the same time.
    pub max_waiting_per_session: usize,
    /// Codes that do not exist an address may ask for in a minute.
    pub wrong_codes_per_minute: usize,
    /// How often hosts are pinged. Three missed answers lose the host.
    pub ping_interval: Duration,
    /// How long a guest waits for its host to answer.
    pub join_timeout: Duration,
    /// How long a new connection has to say what it is.
    pub handshake_timeout: Duration,
    /// How long a session outlives the loss of its host, for the host to
    /// come back to.
    pub resume_grace: Duration,
    pub journal: Journal,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            listen: (Ipv4Addr::UNSPECIFIED, DEFAULT_SERVER_PORT).into(),
            max_sessions: 1000,
            max_sessions_per_address: 4,
            max_connections: 4000,
            max_connections_per_address: 32,
            max_waiting_per_session: 8,
            wrong_codes_per_minute: 20,
            ping_interval: Duration::from_secs(15),
            join_timeout: Duration::from_secs(10),
            handshake_timeout: Duration::from_secs(10),
            resume_grace: Duration::from_secs(120),
            journal: Journal::stderr(),
        }
    }
}

/// What the server holds right now.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Stats {
    pub sessions: usize,
    /// Sessions whose host is connected.
    pub hosted: usize,
    /// Pairs of connections joined.
    pub tunnels: usize,
    pub connections: usize,
}

impl fmt::Display for Stats {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} sessions ({} with their host), {} joined pairs, {} connections",
            self.sessions, self.hosted, self.tunnels, self.connections
        )
    }
}

/// A running server. Dropping it ends every connection.
pub struct Server {
    address: SocketAddr,
    inner: Arc<Inner>,
    threads: Vec<JoinHandle<()>>,
}

impl Server {
    pub fn start(config: Config) -> io::Result<Self> {
        let listener = listen(config.listen)?;
        listener.set_nonblocking(true)?;
        let address = listener.local_addr()?;
        let inner = Arc::new(Inner {
            gate: Gate::new(config.max_connections, config.max_connections_per_address),
            registry: Mutex::new(Registry::new(Limits {
                sessions: config.max_sessions,
                sessions_per_address: config.max_sessions_per_address,
                waiting_per_session: config.max_waiting_per_session,
            })),
            throttle: Mutex::new(Throttle::new(config.wrong_codes_per_minute, GUESS_WINDOW)),
            stop: AtomicBool::new(false),
            next_line: AtomicU64::new(1),
            tunnels: AtomicUsize::new(0),
            config,
        });
        inner.config.journal.write(format_args!(
            "listening on {address}, session protocol {SERVER_PROTOCOL}"
        ));
        let threads = vec![
            thread::spawn({
                let inner = inner.clone();
                move || inner.accept_loop(listener)
            }),
            thread::spawn({
                let inner = inner.clone();
                move || inner.housekeeping()
            }),
        ];
        Ok(Self {
            address,
            inner,
            threads,
        })
    }

    pub fn local_addr(&self) -> SocketAddr {
        self.address
    }

    pub fn stats(&self) -> Stats {
        let (sessions, hosted) = self.inner.registry.lock().unwrap().counts();
        Stats {
            sessions,
            hosted,
            tunnels: self.inner.tunnels.load(Ordering::Relaxed),
            connections: self.inner.gate.count(),
        }
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.inner.stop.store(true, Ordering::Relaxed);
        self.inner.gate.close();
        for thread in self.threads.drain(..) {
            let _ = thread.join();
        }
    }
}

struct Inner {
    config: Config,
    gate: Arc<Gate>,
    registry: Mutex<Registry>,
    throttle: Mutex<Throttle>,
    stop: AtomicBool,
    next_line: AtomicU64,
    tunnels: AtomicUsize,
}

/// Counts a joined pair for as long as it lives.
struct Counted<'a>(&'a AtomicUsize);

impl<'a> Counted<'a> {
    fn new(counter: &'a AtomicUsize) -> Self {
        counter.fetch_add(1, Ordering::Relaxed);
        Self(counter)
    }
}

impl Drop for Counted<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::Relaxed);
    }
}

/// Tell the other side why, and end the conversation.
fn refuse(stream: &TcpStream, why: impl fmt::Display) {
    let _ = send(
        stream,
        &Reply::Error {
            message: why.to_string(),
        },
    );
}

/// A listener on `address`. One on an IPv6 address takes IPv4 as well:
/// Windows, and Linux with `bindv6only` set, would otherwise keep `[::]` to
/// IPv6 alone. IPv4 connections then come from mapped addresses such as
/// `::ffff:203.0.113.5`, which the gate counts as the IPv4 address they
/// stand for.
fn listen(address: SocketAddr) -> io::Result<TcpListener> {
    let socket = Socket::new(Domain::for_address(address), Type::STREAM, Some(Protocol::TCP))?;
    if address.is_ipv6() {
        // Some systems have no dual-stack sockets; IPv6 alone is still worth
        // serving there.
        let _ = socket.set_only_v6(false);
    }
    // What std does on Unix, so that a restarted server can listen again
    // while old connections linger. On Windows the same option would let
    // another program take over the port, and std leaves it off there too.
    #[cfg(not(windows))]
    socket.set_reuse_address(true)?;
    socket.bind(&address.into())?;
    socket.listen(BACKLOG)?;
    Ok(socket.into())
}

/// Read and throw away what a non-blocking `stream` has received, without
/// waiting for more. True when the other side is done: it closed, or the
/// connection is gone.
fn drain(stream: &TcpStream) -> bool {
    let mut buffer = [0u8; 4096];
    for _ in 0..DRAIN_READS {
        match (&mut &*stream).read(&mut buffer) {
            Ok(0) => return true,
            Ok(_) => {}
            Err(e) if e.kind() == ErrorKind::Interrupted => {}
            Err(e) if e.kind() == ErrorKind::WouldBlock => return false,
            Err(_) => return true,
        }
    }
    false
}

/// Refused connections, open until the other side closes or [`LINGER`]
/// passes.
///
/// Closing a socket that has received data it did not read resets the
/// connection instead of ending it, and a Windows client that gets the reset
/// throws away the refusal it had not yet read and reports only "connection
/// reset". A launcher sends its request at once, so it is usually there by
/// the time it is turned away, and may still be on its way after. Every
/// stream here is non-blocking, so the accept loop looks at them between
/// connections without ever waiting on one.
struct Lingering {
    streams: VecDeque<(TcpStream, Instant)>,
    swept: Instant,
}

impl Lingering {
    fn new() -> Self {
        Self {
            streams: VecDeque::new(),
            swept: Instant::now(),
        }
    }

    /// When full, the oldest is let go to make room, drained one last time
    /// so that what reached it since the last sweep does not turn its close
    /// into a reset.
    fn keep(&mut self, stream: TcpStream, now: Instant) {
        if self.streams.len() >= MAX_LINGERING
            && let Some((old, _)) = self.streams.pop_front()
        {
            drain(&old);
        }
        self.streams.push_back((stream, now + LINGER));
    }

    /// Let go of those that are done, at most once a [`TICK`] so that a flood
    /// of connections does not have the loop reading them all every time.
    /// Each is drained before its time is checked, so one that runs out is
    /// still emptied before it closes.
    fn sweep(&mut self, now: Instant) {
        if now < self.swept + TICK {
            return;
        }
        self.swept = now;
        self.streams.retain(|(stream, until)| !drain(stream) && now < *until);
    }
}

/// A name as it may go into the journal: one line, not too long.
fn printable(name: &str) -> String {
    name.chars().filter(|c| !c.is_control()).take(NAME_LENGTH).collect()
}

impl Inner {
    fn stopped(&self) -> bool {
        self.stop.load(Ordering::Relaxed)
    }

    fn log(&self, args: fmt::Arguments<'_>) {
        self.config.journal.write(args);
    }

    fn accept_loop(self: Arc<Self>, listener: TcpListener) {
        let mut lingering = Lingering::new();
        while !self.stopped() {
            lingering.sweep(Instant::now());
            let (stream, address) = match listener.accept() {
                Ok(accepted) => accepted,
                Err(e) if e.kind() == ErrorKind::WouldBlock => {
                    thread::sleep(TICK);
                    continue;
                }
                Err(_) => continue,
            };
            // Windows hands out accepted sockets non-blocking when the
            // listener is; every read would then fail at once.
            if stream.set_nonblocking(false).is_err() {
                continue;
            }
            match self.gate.admit(stream, address) {
                Ok(peer) => {
                    let inner = self.clone();
                    thread::spawn(move || inner.handle(peer));
                }
                Err((stream, turned)) => {
                    let why = match turned {
                        Turned::Full => "The session server is full. Try again later.",
                        Turned::TooManyFromAddress => "Too many connections from this address.",
                        Turned::Closed => continue,
                    };
                    // Without waiting: a refusal is a few bytes the socket
                    // takes at once, or nothing is lost by not sending it.
                    if stream.set_nonblocking(true).is_err() {
                        continue;
                    }
                    // The request is read first so that it does not turn the
                    // close into a reset; see `Lingering`.
                    let done = drain(&stream);
                    refuse(&stream, why);
                    let _ = stream.shutdown(Shutdown::Write);
                    if !done {
                        lingering.keep(stream, Instant::now());
                    }
                }
            }
        }
    }

    /// Pings hosts, and forgets sessions whose host has not come back.
    fn housekeeping(self: Arc<Self>) {
        let mut next_ping = Instant::now() + self.config.ping_interval;
        while !self.stopped() {
            thread::sleep(TICK);
            let now = Instant::now();
            let expired = self.registry.lock().unwrap().expire(now, self.config.resume_grace);
            for code in expired {
                self.log(format_args!("{code}: the host did not come back; the session is over"));
            }
            self.throttle.lock().unwrap().prune(now);
            if now >= next_ping {
                next_ping = now + self.config.ping_interval;
                let lines = self.registry.lock().unwrap().lines();
                for line in lines {
                    // Its reader notices and lets the session wait for the host.
                    if line.send(&Reply::Ping).is_err() {
                        line.close();
                    }
                }
            }
        }
    }

    /// The first message says what the connection is for.
    fn handle(&self, peer: Peer) {
        let timeout = Some(self.config.handshake_timeout);
        if peer.stream.set_read_timeout(timeout).is_err() || peer.stream.set_write_timeout(timeout).is_err() {
            return;
        }
        let _ = peer.stream.set_nodelay(true);
        keep_alive(&peer.stream);
        let request = match expect::<Request>(&peer.stream) {
            Ok(request) => request,
            // Not a launcher, or gone before saying anything.
            Err(_) => return,
        };
        let protocol = match &request {
            Request::Open { protocol, .. } | Request::Join { protocol, .. } | Request::Accept { protocol, .. } => {
                *protocol
            }
            Request::Pong | Request::Close => {
                return refuse(&peer.stream, "A connection opens with open, join or accept.");
            }
        };
        if protocol != SERVER_PROTOCOL {
            return refuse(
                &peer.stream,
                format_args!(
                    "This session server speaks protocol {SERVER_PROTOCOL} and the launcher {protocol}. \
                     Update the one that is older."
                ),
            );
        }
        match request {
            Request::Open {
                name,
                game_version,
                resume,
                ..
            } => self.host(peer, &name, game_version.as_deref(), resume),
            Request::Join { code, .. } => self.join(peer, code),
            Request::Accept { token, tunnel, .. } => self.accept(peer, &token, tunnel),
            Request::Pong | Request::Close => unreachable!("refused above"),
        }
    }

    /// A host's control line, for as long as it lasts.
    fn host(&self, peer: Peer, name: &str, game_version: Option<&str>, resume: Option<Resume>) {
        let id = self.next_line.fetch_add(1, Ordering::Relaxed);
        let Ok(line) = Line::new(id, &peer.stream) else { return };
        let opened = self
            .registry
            .lock()
            .unwrap()
            .open(line.clone(), peer.ip(), resume.as_ref());
        let opened = match opened {
            Ok(opened) => opened,
            Err(refusal) => return refuse(&peer.stream, refusal),
        };
        if let Some(replaced) = &opened.replaced {
            replaced.close();
        }
        let code = opened.code.clone();
        let reply = Reply::Opened {
            code: opened.code,
            token: opened.token,
            ping_seconds: self.config.ping_interval.as_secs().max(1),
        };
        if line.send(&reply).is_err() {
            self.registry.lock().unwrap().detach(&code, id, Instant::now());
            return;
        }
        self.log(format_args!(
            "{code}: {} by {} (game {}) from {}",
            if opened.resumed { "resumed" } else { "opened" },
            printable(name),
            printable(game_version.unwrap_or("unknown")),
            peer.address
        ));

        if peer
            .stream
            .set_read_timeout(Some(self.config.ping_interval * 3))
            .is_err()
        {
            return self.lose(&code, id);
        }
        loop {
            match receive::<Request>(&peer.stream) {
                Ok(Some(Request::Pong)) => {}
                Ok(Some(Request::Close)) => {
                    if self.registry.lock().unwrap().close(&code, id) {
                        self.log(format_args!("{code}: closed by the host"));
                    }
                    return;
                }
                // Gone, silent for too long, or speaking out of turn.
                Ok(None) | Ok(Some(_)) | Err(_) => return self.lose(&code, id),
            }
        }
    }

    fn lose(&self, code: &SessionCode, line: u64) {
        if self.registry.lock().unwrap().detach(code, line, Instant::now()) && !self.stopped() {
            self.log(format_args!(
                "{code}: lost the host; the session waits {} s for it",
                self.config.resume_grace.as_secs()
            ));
        }
    }

    /// A guest asking for a session: tell its host, wait for the host's
    /// connection, and join the two.
    fn join(&self, guest: Peer, code: SessionCode) {
        if !self.throttle.lock().unwrap().allows(guest.ip(), Instant::now()) {
            return refuse(
                &guest.stream,
                "Too many wrong codes from this address. Wait a minute and try again.",
            );
        }
        let knock = self.registry.lock().unwrap().knock(&code);
        let knock = match knock {
            Ok(knock) => knock,
            Err(refusal) => {
                if matches!(refusal, Refusal::Unknown(_)) {
                    self.throttle.lock().unwrap().miss(guest.ip(), Instant::now());
                }
                return refuse(&guest.stream, refusal);
            }
        };
        let incoming = Reply::Incoming {
            tunnel: knock.tunnel,
            from: guest.address,
        };
        if knock.line.send(&incoming).is_err() {
            knock.line.close();
            self.registry.lock().unwrap().give_up(&code, knock.tunnel);
            return refuse(&guest.stream, Refusal::HostAway(code));
        }
        let host = match knock.doorstep.recv_timeout(self.config.join_timeout) {
            Ok(host) => host,
            Err(_) => {
                self.registry.lock().unwrap().give_up(&code, knock.tunnel);
                return refuse(
                    &guest.stream,
                    "The host did not answer. Check that their launcher is still open, then try again.",
                );
            }
        };
        // From here the server only copies, however long either side is
        // quiet; the system's keep-alive probes notice a side that vanished.
        let joined = [&guest.stream, &host.stream]
            .iter()
            .all(|stream| stream.set_read_timeout(None).is_ok() && stream.set_write_timeout(None).is_ok());
        if !joined || send(&host.stream, &Reply::Accepted).is_err() || send(&guest.stream, &Reply::Joined).is_err() {
            return;
        }
        let _counted = Counted::new(&self.tunnels);
        splice(&guest.stream, &host.stream);
    }

    /// A host's connection for a waiting guest.
    fn accept(&self, host: Peer, token: &str, tunnel: u64) {
        let waiting = self.registry.lock().unwrap().answer(token, tunnel);
        match waiting {
            // The guest's thread answers both and joins them.
            Some(guest) => {
                if let Err(unsent) = guest.send(host) {
                    refuse(&unsent.0.stream, "The guest stopped waiting.");
                }
            }
            None => refuse(&host.stream, "No guest waits on this connection any more."),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    /// A server end and a client end of one connection through `listener`.
    fn pair(listener: &TcpListener) -> (TcpStream, TcpStream) {
        let client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (server, _) = listener.accept().unwrap();
        server.set_nonblocking(true).unwrap();
        (server, client)
    }

    /// A refused connection on the server side whose request has arrived and
    /// is still unread, with the client's end.
    fn refused_with_request(listener: &TcpListener) -> (TcpStream, TcpStream) {
        let (server, mut client) = pair(listener);
        client.write_all(b"a request nobody reads").unwrap();
        // Wait until the bytes are there without taking them away.
        server.set_nonblocking(false).unwrap();
        server.peek(&mut [0u8; 1]).unwrap();
        server.set_nonblocking(true).unwrap();
        client.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
        (server, client)
    }

    /// The client must see the connection end, not reset.
    fn assert_closed_cleanly(mut client: TcpStream) {
        let mut rest = Vec::new();
        client.read_to_end(&mut rest).expect("the connection was reset");
        assert!(rest.is_empty());
    }

    #[test]
    fn one_that_runs_out_of_time_is_drained_before_it_closes() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let mut lingering = Lingering::new();
        let (server, client) = refused_with_request(&listener);
        let now = Instant::now();
        lingering.keep(server, now);
        lingering.sweep(now + LINGER + TICK);
        assert!(lingering.streams.is_empty());
        assert_closed_cleanly(client);
    }

    #[test]
    fn one_let_go_to_make_room_is_drained_before_it_closes() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let mut lingering = Lingering::new();
        let (oldest, client) = refused_with_request(&listener);
        let now = Instant::now();
        lingering.keep(oldest, now);
        let mut clients = Vec::new();
        while lingering.streams.len() < MAX_LINGERING {
            let (server, other) = pair(&listener);
            lingering.keep(server, now);
            clients.push(other);
        }
        let (server, other) = pair(&listener);
        lingering.keep(server, now);
        clients.push(other);
        assert_eq!(lingering.streams.len(), MAX_LINGERING);
        assert_closed_cleanly(client);
    }
}
