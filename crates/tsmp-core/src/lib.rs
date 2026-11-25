//! Game-independent core of the TSMP launcher: locating the game, working out
//! which packs two players share, and describing a Mods folder so it can be
//! synchronised.

pub mod clock;
pub mod game;
pub mod manifest;
pub mod packs;
pub mod preview;
pub mod saves;
pub mod status;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("The Sims 4 installation was not found")]
    GameNotFound,
    #[error("The Sims 4 user folder was not found under {0}")]
    UserDirNotFound(std::path::PathBuf),
    #[error("{path}: {source}")]
    Io {
        path: std::path::PathBuf,
        source: std::io::Error,
    },
    #[error("{path}: {reason}")]
    Save {
        path: std::path::PathBuf,
        reason: &'static str,
    },
    #[error("invalid manifest: {0}")]
    Manifest(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, Error>;

pub(crate) fn io_err(path: &std::path::Path) -> impl FnOnce(std::io::Error) -> Error + '_ {
    move |source| Error::Io {
        path: path.to_path_buf(),
        source,
    }
}
