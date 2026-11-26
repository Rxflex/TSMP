//! Networking for the TSMP launcher: finding a host on the local network or
//! through a session server, the launcher-to-launcher protocol, and
//! transferring mods and the shared save.
//!
//! Everything is blocking and thread based; callers run it off the UI thread.

use std::net::TcpStream;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

pub mod discovery;
pub mod guest;
pub mod host;
pub mod live;
pub mod paths;
pub mod protocol;
pub mod rendezvous;
pub mod route;

/// How long reaching another computer may take before it counts as away.
pub(crate) const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
/// How often a waiting thread looks whether it should stop.
pub(crate) const POLL: Duration = Duration::from_millis(100);

/// Have the system probe an idle connection. Over the internet a connection
/// with nothing to say is otherwise forgotten by routers on the way, and a
/// peer that vanished without a word would never be noticed.
pub fn keep_alive(stream: &TcpStream) {
    let probes = socket2::TcpKeepalive::new()
        .with_time(Duration::from_secs(30))
        .with_interval(Duration::from_secs(10));
    // Without probes the connection still works; it is only slower to notice a loss.
    let _ = socket2::SockRef::from(stream).set_tcp_keepalive(&probes);
}

/// Sleep for `duration`, or less once `stop` is set.
pub(crate) fn sleep_unless(duration: Duration, stop: &AtomicBool) {
    let mut waited = Duration::ZERO;
    while waited < duration && !stop.load(Ordering::Relaxed) {
        std::thread::sleep(POLL);
        waited += POLL;
    }
}

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("network error: {0}")]
    Io(#[from] std::io::Error),
    #[error("{path}: {source}")]
    File {
        path: std::path::PathBuf,
        source: std::io::Error,
    },
    #[error("protocol error: {0}")]
    Protocol(String),
    #[error("the other side reported: {0}")]
    Remote(String),
    #[error("the other launcher speaks protocol {theirs}, this one speaks {ours}; update both to the same version")]
    VersionMismatch { ours: u32, theirs: u32 },
    /// The host has as many players as it takes, and the one asking is not
    /// one of them coming back.
    #[error("the session is full")]
    Full,
    #[error("the host sent an unsafe file path: {0:?}")]
    UnsafePath(String),
    #[error("{0} arrived damaged (content does not match the host's manifest)")]
    HashMismatch(String),
    #[error(transparent)]
    Core(#[from] tsmp_core::Error),
}

pub type Result<T> = std::result::Result<T, Error>;

pub(crate) fn file_err(path: &std::path::Path) -> impl FnOnce(std::io::Error) -> Error + '_ {
    move |source| Error::File {
        path: path.to_path_buf(),
        source,
    }
}
