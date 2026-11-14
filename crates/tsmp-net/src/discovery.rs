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
use std::time::Duration;

use serde::{Deserialize, Serialize};
use socket2::{Domain, Protocol, Socket, Type};

pub const DISCOVERY_PORT: u16 = 47801;
const MAGIC: &str = "TSMP";
const INTERVAL: Duration = Duration::from_secs(1);
const POLL: Duration = Duration::from_millis(200);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Beacon {
    magic: String,
    pub protocol: u32,
    pub name: String,
    /// TCP port the host accepts guests on.
    pub port: u16,
    pub game_version: Option<String>,
}

impl Beacon {
    pub fn new(protocol: u32, name: String, port: u16, game_version: Option<String>) -> Self {
        Self {
            magic: MAGIC.into(),
            protocol,
            name,
            port,
            game_version,
        }
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
    pub fn start(beacon: Beacon) -> std::io::Result<Self> {
        Self::start_to(beacon, (Ipv4Addr::BROADCAST, DISCOVERY_PORT).into())
    }

    pub fn start_to(beacon: Beacon, destination: SocketAddr) -> std::io::Result<Self> {
        let socket = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0))?;
        socket.set_broadcast(true)?;
        let payload = serde_json::to_vec(&beacon).expect("a beacon always serializes");
        let stop = Arc::new(AtomicBool::new(false));
        let thread = thread::spawn({
            let stop = stop.clone();
            move || {
                while !stop.load(Ordering::Relaxed) {
                    // A send can fail while the network is down; keep trying.
                    let _ = socket.send_to(&payload, destination);
                    let mut waited = Duration::ZERO;
                    while waited < INTERVAL && !stop.load(Ordering::Relaxed) {
                        thread::sleep(POLL);
                        waited += POLL;
                    }
                }
            }
        });
        Ok(Self {
            stop,
            thread: Some(thread),
        })
    }
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
                        Err(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => continue,
                        // Windows reports ICMP errors from unrelated sends here.
                        Err(_) => continue,
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
