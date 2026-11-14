//! What the in-game mod reports about the running game.
//!
//! On startup the mod writes `<user dir>/tsmp/status.json`. The launcher reads
//! it to confirm that the mod loaded and that the game really started with the
//! intended packs: `-disablepacks:` is not honoured by every way of launching.

use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::packs::{PackSet, is_pack_code};

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct ModStatus {
    pub mod_version: String,
    /// Pack names as the game reports them: `BASE_GAME`, `EP01`, ...
    pub packs: Vec<String>,
    /// Unix time the mod started, in seconds.
    pub started_at: u64,
}

pub fn status_path(user_dir: &Path) -> PathBuf {
    user_dir.join("tsmp").join("status.json")
}

impl ModStatus {
    /// `None` if the mod has never run or the file is unreadable.
    pub fn read(user_dir: &Path) -> Option<Self> {
        let text = fs::read_to_string(status_path(user_dir)).ok()?;
        serde_json::from_str(&text).ok()
    }

    /// DLC packs the game loaded (everything except the base game).
    pub fn loaded_packs(&self) -> PackSet {
        self.packs.iter().filter(|p| is_pack_code(p)).cloned().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_what_the_mod_writes() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("tsmp")).unwrap();
        fs::write(
            status_path(dir.path()),
            r#"{"mod_version": "0.0.1", "python": "3.7.0", "packs": ["BASE_GAME", "EP01", "GP04"],
                "has_socket": true, "has_select": true, "started_at": 1794606174}"#,
        )
        .unwrap();

        let status = ModStatus::read(dir.path()).unwrap();

        assert_eq!(status.mod_version, "0.0.1");
        assert_eq!(status.started_at, 1794606174);
        assert_eq!(
            status.loaded_packs(),
            ["EP01".to_string(), "GP04".to_string()].into()
        );
    }

    #[test]
    fn missing_or_broken_status_is_none() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(ModStatus::read(dir.path()), None);
        fs::create_dir(dir.path().join("tsmp")).unwrap();
        fs::write(status_path(dir.path()), "{ not json").unwrap();
        assert_eq!(ModStatus::read(dir.path()), None);
    }
}
