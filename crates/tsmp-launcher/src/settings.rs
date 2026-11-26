//! Small choices the launcher remembers between runs: one file each in
//! `%APPDATA%\TSMP`, or the equivalent elsewhere.

use std::fs;
use std::path::PathBuf;

/// One remembered value, by the name of its file.
pub struct Setting(&'static str);

/// The language picked from the list in the top bar.
pub const LANGUAGE: Setting = Setting("language");
/// The session server a host last opened a session on.
pub const SERVER: Setting = Setting("server");
/// Who plays on this install, so that a host knows a guest coming back from
/// a new one: made the first time it is needed and never changed.
pub const PLAYER: Setting = Setting("player");

impl Setting {
    fn path(&self) -> Option<PathBuf> {
        let base = std::env::var_os("APPDATA")
            .or_else(|| std::env::var_os("XDG_CONFIG_HOME"))
            .map(PathBuf::from)
            .or_else(|| Some(PathBuf::from(std::env::var_os("HOME")?).join(".config")))?;
        Some(base.join("TSMP").join(self.0))
    }

    pub fn load(&self) -> Option<String> {
        let text = fs::read_to_string(self.path()?).ok()?;
        let value = text.trim();
        (!value.is_empty()).then(|| value.to_string())
    }

    pub fn store(&self, value: &str) {
        // Not being able to remember a choice is not worth interrupting for.
        if let Some(path) = self.path() {
            let _ = path.parent().map(fs::create_dir_all);
            let _ = fs::write(path, value);
        }
    }
}
