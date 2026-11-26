//! Finding a host on the local network.
//!
//! The host broadcasts a small UDP datagram once a second; guests listen for
//! it. Nothing is trusted from a beacon beyond "there may be a host at this
//! address": the real handshake happens over TCP.

use std::io::ErrorKind;
use std::net::{Ipv4Addr, SocketAddr, UdpSocket};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use if_addrs::IfAddr;
use serde::{Deserialize, Serialize};
use socket2::{Domain, Protocol, Socket, Type};

use crate::sleep_unless;

pub const DISCOVERY_PORT: u16 = 47801;
const MAGIC: &str = "TSMP";
const INTERVAL: Duration = Duration::from_secs(1);
const POLL: Duration = Duration::from_millis(200);
/// How often the announcer looks at the network adapters again.
const REFRESH: Duration = Duration::from_secs(5);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Beacon {
    magic: String,
    pub protocol: u32,
    pub name: String,
    /// TCP port the host accepts guests on.
    pub port: u16,
    pub game_version: Option<String>,
    /// Players in the session, the host included, and how many it takes, so
    /// that a full one can be shown as such. Zero from a launcher before
    /// protocol 3, which took one guest.
    #[serde(default)]
    pub players: u32,
    #[serde(default)]
    pub max_players: u32,
}

impl Beacon {
    pub fn new(protocol: u32, name: String, port: u16, game_version: Option<String>) -> Self {
        Self {
            magic: MAGIC.into(),
            protocol,
            name,
            port,
            game_version,
            players: 0,
            max_players: 0,
        }
    }

    /// The beacon saying that `players` of `max_players` are in the session.
    pub fn with_players(self, players: u32, max_players: u32) -> Self {
        Self {
            players,
            max_players,
            ..self
        }
    }

    /// Whether the session takes no more players. A launcher from before
    /// protocol 3 does not say.
    pub fn is_full(&self) -> bool {
        self.max_players != 0 && self.players >= self.max_players
    }
}

/// A host seen on the network. `address` is where to connect.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Found {
    pub address: SocketAddr,
    pub beacon: Beacon,
}

/// Broadcasts `beacon` until dropped.
pub struct Announcer {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Announcer {
    /// Sends the beacon to the limited broadcast address and to the directed
    /// broadcast address of every local IPv4 network. Windows sends a limited
    /// broadcast from an unbound socket out of one adapter only, the one with
    /// the lowest metric, which on a PC with Hyper-V, VirtualBox or VPN
    /// adapters is often not the real LAN; the directed sends reach the rest.
    pub fn start(beacon: Beacon) -> std::io::Result<Self> {
        Self::start_with(move || beacon.clone())
    }

    /// [`Announcer::start`] for a beacon that changes: `beacon` is asked for
    /// it before every send.
    pub fn start_with(beacon: impl FnMut() -> Beacon + Send + 'static) -> std::io::Result<Self> {
        Self::spawn(Box::new(beacon), (Ipv4Addr::BROADCAST, DISCOVERY_PORT).into(), true)
    }

    /// Sends the beacon to `destination` only.
    pub fn start_to(beacon: Beacon, destination: SocketAddr) -> std::io::Result<Self> {
        Self::spawn(Box::new(move || beacon.clone()), destination, false)
    }

    fn spawn(
        mut beacon: Box<dyn FnMut() -> Beacon + Send>,
        destination: SocketAddr,
        every_network: bool,
    ) -> std::io::Result<Self> {
        let socket = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0))?;
        socket.set_broadcast(true)?;
        let stop = Arc::new(AtomicBool::new(false));
        let thread = thread::spawn({
            let stop = stop.clone();
            move || {
                let mut directed = Vec::new();
                let mut refreshed: Option<Instant> = None;
                while !stop.load(Ordering::Relaxed) {
                    // Adapters come and go (a VPN connects, Wi-Fi changes
                    // network), so the list is looked up again now and then.
                    if every_network && refreshed.is_none_or(|at| at.elapsed() >= REFRESH) {
                        directed = directed_senders(destination.port());
                        refreshed = Some(Instant::now());
                    }
                    let payload = serde_json::to_vec(&beacon()).expect("a beacon always serializes");
                    // A send can fail while the network is down; keep trying.
                    let _ = socket.send_to(&payload, destination);
                    for sender in &directed {
                        let from = sender.socket.as_ref().unwrap_or(&socket);
                        let _ = from.send_to(&payload, sender.destination);
                    }
                    sleep_unless(INTERVAL, &stop);
                }
            }
        });
        Ok(Self {
            stop,
            thread: Some(thread),
        })
    }
}

/// Where to send one directed broadcast, and the socket to send it from.
struct DirectedSender {
    destination: SocketAddr,
    /// Bound to the interface's own address, which is what makes Windows send
    /// the datagram out of that adapter. `None` if binding failed (the address
    /// may have just gone away); the shared socket is used then, which still
    /// works wherever routing picks the right adapter, as Linux does.
    socket: Option<UdpSocket>,
}

fn directed_senders(port: u16) -> Vec<DirectedSender> {
    directed_senders_for(&broadcast_targets(local_ipv4_networks()), port)
}

/// One sender per `(local address, broadcast address)` target.
fn directed_senders_for(targets: &[(Ipv4Addr, Ipv4Addr)], port: u16) -> Vec<DirectedSender> {
    targets
        .iter()
        .map(|&(ip, broadcast)| DirectedSender {
            destination: (broadcast, port).into(),
            socket: UdpSocket::bind((ip, 0))
                .and_then(|socket| socket.set_broadcast(true).map(|()| socket))
                .ok(),
        })
        .collect()
}

/// Interface index, address and prefix length of every IPv4 interface that is
/// up. An error listing them just means no directed broadcasts this round.
///
/// The prefix length is used rather than the netmask because on Windows
/// if-addrs takes the length from the address itself (`OnLinkPrefixLength`)
/// but guesses the netmask from the first matching entry of the adapter's
/// prefix list, which can be a /32 host route or nothing at all. On Linux both
/// come from the same netmask.
fn local_ipv4_networks() -> Vec<(Option<u32>, Ipv4Addr, u8)> {
    if_addrs::get_if_addrs()
        .unwrap_or_default()
        .into_iter()
        .filter(|interface| interface.is_oper_up())
        .filter_map(|interface| match interface.addr {
            IfAddr::V4(v4) => Some((interface.index, v4.ip, v4.prefixlen)),
            IfAddr::V6(_) => None,
        })
        .collect()
}

/// Pairs each local address worth broadcasting from with its network's
/// broadcast address. Loopback is skipped, as are networks without one. An
/// interface with several addresses on one network gets one send, not one per
/// address, but the same network on two interfaces gets a send on each: a VPN
/// or virtual adapter can reuse the LAN's subnet, and two routers can both
/// hand out the default one, and on Windows only the send bound to an
/// interface's address goes out of that interface.
fn broadcast_targets(
    networks: impl IntoIterator<Item = (Option<u32>, Ipv4Addr, u8)>,
) -> Vec<(Ipv4Addr, Ipv4Addr)> {
    let mut seen: Vec<(Option<u32>, Ipv4Addr)> = Vec::new();
    let mut targets = Vec::new();
    for (interface, ip, prefixlen) in networks {
        if ip.is_loopback() || ip.is_unspecified() {
            continue;
        }
        let Some(broadcast) = directed_broadcast(ip, prefixlen) else {
            continue;
        };
        if !seen.contains(&(interface, broadcast)) {
            seen.push((interface, broadcast));
            targets.push((ip, broadcast));
        }
    }
    targets
}

/// The broadcast address of the network `ip` is on: all host bits set. A /31
/// (point-to-point, RFC 3021) or /32 network has no broadcast address, and a
/// /0 one would give 255.255.255.255, which is sent anyway.
fn directed_broadcast(ip: Ipv4Addr, prefixlen: u8) -> Option<Ipv4Addr> {
    if prefixlen == 0 || prefixlen >= 31 {
        return None;
    }
    Some(Ipv4Addr::from_bits(ip.to_bits() | (u32::MAX >> prefixlen)))
}

impl Drop for Announcer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// Listens for beacons until dropped. Every beacon received is delivered, so
/// the same host shows up about once a second while it is alive.
pub struct Browser {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
    found: Receiver<Found>,
    port: u16,
}

impl Browser {
    pub fn start() -> std::io::Result<Self> {
        Self::start_on(DISCOVERY_PORT)
    }

    /// `port` 0 picks a free port (for tests); see [`Browser::port`].
    pub fn start_on(port: u16) -> std::io::Result<Self> {
        // Address reuse lets several launchers on one machine all listen.
        let socket = Socket::new(Domain::IPV4, Type::DGRAM, Some(Protocol::UDP))?;
        socket.set_reuse_address(true)?;
        socket.bind(&SocketAddr::from((Ipv4Addr::UNSPECIFIED, port)).into())?;
        let socket: UdpSocket = socket.into();
        socket.set_read_timeout(Some(POLL))?;
        let port = socket.local_addr()?.port();

        let (sender, found) = mpsc::channel();
        let stop = Arc::new(AtomicBool::new(false));
        let thread = thread::spawn({
            let stop = stop.clone();
            move || {
                let mut buffer = [0u8; 2048];
                while !stop.load(Ordering::Relaxed) {
                    let (length, from) = match socket.recv_from(&mut buffer) {
                        Ok(received) => received,
                        // The read timeout: WouldBlock on Linux, TimedOut on Windows.
                        Err(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => continue,
                        // Windows reports an ICMP port unreachable for an
                        // earlier send on this socket as WSAECONNRESET on the
                        // next receive; the socket itself is fine.
                        Err(e) if e.kind() == ErrorKind::ConnectionReset => continue,
                        // Anything else (Windows also fails a datagram too big
                        // for the buffer instead of truncating it) is not worth
                        // stopping for either, but waiting a little keeps an
                        // error that repeats at once from spinning the CPU.
                        Err(_) => {
                            sleep_unless(POLL, &stop);
                            continue;
                        }
                    };
                    let Ok(beacon) = serde_json::from_slice::<Beacon>(&buffer[..length]) else {
                        continue;
                    };
                    if beacon.magic != MAGIC {
                        continue;
                    }
                    let address = SocketAddr::new(from.ip(), beacon.port);
                    if sender.send(Found { address, beacon }).is_err() {
                        break;
                    }
                }
            }
        });
        Ok(Self {
            stop,
            thread: Some(thread),
            found,
            port,
        })
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    /// Beacons received since the last call, without blocking.
    pub fn poll(&self) -> Vec<Found> {
        self.found.try_iter().collect()
    }

    pub fn wait(&self, timeout: Duration) -> Option<Found> {
        self.found.recv_timeout(timeout).ok()
    }
}

impl Drop for Browser {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn browser_sees_an_announced_host() {
        let browser = Browser::start_on(0).unwrap();
        let beacon = Beacon::new(1, "host-pc".into(), 4242, Some("1.2.3".into()));
        let _announcer =
            Announcer::start_to(beacon.clone(), (Ipv4Addr::LOCALHOST, browser.port()).into()).unwrap();

        let found = browser.wait(Duration::from_secs(5)).expect("no beacon received");

        assert_eq!(found.beacon, beacon);
        assert_eq!(found.address, SocketAddr::from((Ipv4Addr::LOCALHOST, 4242)));
    }

    #[test]
    fn beacons_of_versions_2_and_3_read_each_other() {
        // What a launcher of version 2 sends: it took one guest and said nothing of it.
        let old = br#"{"magic":"TSMP","protocol":2,"name":"old","port":47800,"game_version":null}"#;
        let beacon: Beacon = serde_json::from_slice(old).unwrap();
        assert_eq!((beacon.players, beacon.max_players), (0, 0));
        assert!(!beacon.is_full());

        // Version 2 ignored fields it did not know.
        let new = Beacon::new(3, "new".into(), 47800, None).with_players(2, 2);
        let text = serde_json::to_value(&new).unwrap();
        assert_eq!((text["players"].as_u64(), text["max_players"].as_u64()), (Some(2), Some(2)));
        assert_eq!(serde_json::from_value::<Beacon>(text).unwrap(), new);
        assert!(new.is_full());
        assert!(!new.with_players(1, 2).is_full());
    }

    #[test]
    fn an_announcer_sends_the_beacon_as_it_is_now() {
        let browser = Browser::start_on(0).unwrap();
        let players = Arc::new(std::sync::atomic::AtomicU32::new(1));
        let beacon = Beacon::new(3, "host-pc".into(), 4242, None);
        let _announcer = Announcer::spawn(
            Box::new({
                let players = players.clone();
                move || beacon.clone().with_players(players.load(Ordering::SeqCst), 2)
            }),
            (Ipv4Addr::LOCALHOST, browser.port()).into(),
            false,
        )
        .unwrap();
        assert_eq!(browser.wait(Duration::from_secs(5)).unwrap().beacon.players, 1);
        players.store(2, Ordering::SeqCst);
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while !browser.wait(Duration::from_secs(5)).unwrap().beacon.is_full() {
            assert!(std::time::Instant::now() < deadline, "the beacon never changed");
        }
    }

    /// Uses the real broadcast address and port, so it needs a network and
    /// may trigger a firewall prompt: `cargo test -- --ignored`.
    #[test]
    #[ignore]
    fn broadcast_reaches_a_browser_on_the_same_machine() {
        let browser = Browser::start().unwrap();
        let beacon = Beacon::new(1, "broadcast-test".into(), 4242, None);
        let _announcer = Announcer::start(beacon.clone()).unwrap();

        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        loop {
            let left = deadline.saturating_duration_since(std::time::Instant::now());
            let found = browser.wait(left).expect("no broadcast received");
            if found.beacon == beacon {
                break;
            }
        }
    }

    #[test]
    fn directed_broadcast_sets_the_host_bits() {
        let broadcast = |ip: [u8; 4], prefixlen| directed_broadcast(ip.into(), prefixlen);
        assert_eq!(broadcast([192, 168, 1, 37], 24), Some([192, 168, 1, 255].into()));
        assert_eq!(broadcast([10, 4, 200, 9], 16), Some([10, 4, 255, 255].into()));
        assert_eq!(broadcast([172, 20, 5, 1], 20), Some([172, 20, 15, 255].into()));
        assert_eq!(broadcast([26, 1, 2, 3], 8), Some([26, 255, 255, 255].into()));
        assert_eq!(broadcast([192, 168, 1, 2], 30), Some([192, 168, 1, 3].into()));
        // No broadcast address on /31, /32, and /0 is the limited broadcast.
        assert_eq!(broadcast([10, 0, 0, 0], 31), None);
        assert_eq!(broadcast([10, 0, 0, 1], 32), None);
        assert_eq!(broadcast([10, 0, 0, 1], 0), None);
    }

    #[test]
    fn broadcast_targets_skip_loopback_and_repeats() {
        let networks = [
            (Some(1), [127, 0, 0, 1].into(), 8),
            (Some(2), [192, 168, 1, 37].into(), 24),
            (Some(2), [192, 168, 1, 38].into(), 24),
            (Some(3), [172, 27, 160, 1].into(), 20),
            (Some(4), [10, 8, 0, 6].into(), 32),
            (Some(5), [0, 0, 0, 0].into(), 24),
            // A second adapter on a network that looks the same, such as a
            // VPN reusing the LAN's subnet, still needs its own send.
            (Some(6), [192, 168, 1, 90].into(), 24),
            (Some(6), [192, 168, 1, 91].into(), 24),
        ];

        assert_eq!(
            broadcast_targets(networks),
            vec![
                ([192, 168, 1, 37].into(), [192, 168, 1, 255].into()),
                ([172, 27, 160, 1].into(), [172, 27, 175, 255].into()),
                ([192, 168, 1, 90].into(), [192, 168, 1, 255].into()),
            ]
        );
    }

    #[test]
    fn directed_senders_are_bound_to_their_interface_address() {
        let targets = [(Ipv4Addr::LOCALHOST, Ipv4Addr::new(127, 255, 255, 255))];
        let senders = directed_senders_for(&targets, DISCOVERY_PORT);

        assert_eq!(senders.len(), 1);
        let socket = senders[0].socket.as_ref().expect("bind to the interface address failed");
        assert_eq!(socket.local_addr().unwrap().ip(), Ipv4Addr::LOCALHOST);
        assert_eq!(senders[0].destination, SocketAddr::from(([127, 255, 255, 255], DISCOVERY_PORT)));
    }

    /// Whatever adapters the machine running the tests has, each directed send
    /// goes from a socket bound to that adapter's own address. Falling back to
    /// the shared socket would send everything out of one adapter on Windows.
    /// Binding works for every listed address on both CI runners: Windows only
    /// lists addresses whose duplicate address detection has finished, and
    /// Linux does none for IPv4.
    #[test]
    fn directed_senders_cover_the_local_networks() {
        let targets = broadcast_targets(local_ipv4_networks());
        let senders = directed_senders_for(&targets, DISCOVERY_PORT);

        assert_eq!(senders.len(), targets.len());
        for (sender, &(ip, broadcast)) in senders.iter().zip(&targets) {
            let socket = sender.socket.as_ref().unwrap_or_else(|| panic!("could not bind to {ip}"));
            assert_eq!(socket.local_addr().unwrap().ip(), ip);
            assert_eq!(sender.destination, SocketAddr::from((broadcast, DISCOVERY_PORT)));
            assert!(!sender.destination.ip().is_loopback());
        }
    }

    #[test]
    fn foreign_datagrams_are_ignored() {
        let browser = Browser::start_on(0).unwrap();
        let socket = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let target = (Ipv4Addr::LOCALHOST, browser.port());
        socket.send_to(b"not json", target).unwrap();
        socket
            .send_to(br#"{"magic":"OTHER","protocol":1,"name":"x","port":1,"game_version":null}"#, target)
            .unwrap();

        assert_eq!(browser.wait(Duration::from_millis(600)), None);
    }
}
