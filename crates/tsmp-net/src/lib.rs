//! LAN networking for the TSMP launcher: finding a host, the launcher-to-
//! launcher protocol, and transferring mods and the shared save.
//!
//! Everything is blocking and thread based; callers run it off the UI thread.

pub mod discovery;
pub mod guest;
pub mod host;
pub mod live;
pub mod paths;
pub mod protocol;

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
