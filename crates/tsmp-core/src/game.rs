//! Locating the game installation and the per-user data folder.

use std::fs;
use std::path::{Path, PathBuf};

use crate::{Error, Result, io_err};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GameInstall {
    /// Folder containing `Game/`, `Data/` and one folder per owned pack.
    pub install_dir: PathBuf,
    /// `Documents/Electronic Arts/The Sims 4` (the last component is localized).
    pub user_dir: PathBuf,
    /// Contents of `GameVersion.txt`, e.g. `1.129.39.1030`.
    pub version: Option<String>,
}

impl GameInstall {
    pub fn detect() -> Result<Self> {
        let install_dir = find_install_dir().ok_or(Error::GameNotFound)?;
        let documents = dirs::document_dir().ok_or(Error::GameNotFound)?;
        let user_dir = find_user_dir(&documents.join("Electronic Arts"))?;
        Ok(Self::at(install_dir, user_dir))
    }

    pub fn at(install_dir: PathBuf, user_dir: PathBuf) -> Self {
        let version = read_version(&user_dir.join("GameVersion.txt"));
        Self {
            install_dir,
            user_dir,
            version,
        }
    }

    pub fn executable(&self) -> PathBuf {
        self.install_dir.join("Game").join("Bin").join("TS4_x64.exe")
    }

    /// The program and arguments that start the game with `game_args`.
    pub fn launch_plan(&self, game_args: &[String]) -> LaunchPlan {
        launch_plan(&self.install_dir, find_steam_exe().as_deref(), game_args)
    }

    pub fn mods_dir(&self) -> PathBuf {
        self.user_dir.join("Mods")
    }

    pub fn saves_dir(&self) -> PathBuf {
        self.user_dir.join("saves")
    }

    /// Whether the game will load `.ts4script` mods at all. Both switches live
    /// in `Options.ini`.
    pub fn script_mods_enabled(&self) -> Result<bool> {
        let path = self.user_dir.join("Options.ini");
        let text = fs::read_to_string(&path).map_err(io_err(&path))?;
        Ok(script_mods_enabled(&text))
    }
}

/// Steam's application id for The Sims 4.
const STEAM_APP_ID: &str = "1222670";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchPlan {
    pub program: PathBuf,
    pub args: Vec<String>,
    /// Whether the game is started through Steam rather than directly.
    pub via_steam: bool,
}

impl LaunchPlan {
    pub fn spawn(&self) -> std::io::Result<std::process::Child> {
        std::process::Command::new(&self.program).args(&self.args).spawn()
    }
}

/// A Steam copy has to be started by Steam, which is also the documented way
/// to hand it arguments; any other copy is started directly.
fn launch_plan(install_dir: &Path, steam_exe: Option<&Path>, game_args: &[String]) -> LaunchPlan {
    let executable = install_dir.join("Game").join("Bin").join("TS4_x64.exe");
    match steam_exe.filter(|_| is_steam_install(install_dir)) {
        Some(steam) => LaunchPlan {
            program: steam.to_path_buf(),
            args: ["-applaunch".to_string(), STEAM_APP_ID.to_string()]
                .into_iter()
                .chain(game_args.iter().cloned())
                .collect(),
            via_steam: true,
        },
        None => LaunchPlan {
            program: executable,
            args: game_args.to_vec(),
            via_steam: false,
        },
    }
}

/// `<library>/steamapps/common/<game>` with the game's manifest beside it.
fn is_steam_install(install_dir: &Path) -> bool {
    install_dir
        .parent()
        .and_then(Path::parent)
        .is_some_and(|steamapps| steamapps.join(format!("appmanifest_{STEAM_APP_ID}.acf")).is_file())
}

#[cfg(windows)]
fn find_steam_exe() -> Option<PathBuf> {
    use winreg::RegKey;
    use winreg::enums::HKEY_CURRENT_USER;

    let key = RegKey::predef(HKEY_CURRENT_USER).open_subkey(r"Software\Valve\Steam").ok()?;
    let path = PathBuf::from(key.get_value::<String, _>("SteamExe").ok()?);
    path.is_file().then_some(path)
}

#[cfg(not(windows))]
fn find_steam_exe() -> Option<PathBuf> {
    None
}

#[cfg(windows)]
fn find_install_dir() -> Option<PathBuf> {
    use winreg::RegKey;
    use winreg::enums::HKEY_LOCAL_MACHINE;

    // Both the EA app and Steam builds write this key.
    const KEYS: [&str; 2] = [
        r"SOFTWARE\Maxis\The Sims 4",
        r"SOFTWARE\WOW6432Node\Maxis\The Sims 4",
    ];
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    KEYS.iter()
        .filter_map(|key| hklm.open_subkey(key).ok())
        .filter_map(|key| key.get_value::<String, _>("Install Dir").ok())
        .map(PathBuf::from)
        .find(|dir| dir.join("Game").join("Bin").is_dir())
}

#[cfg(not(windows))]
fn find_install_dir() -> Option<PathBuf> {
    None
}

/// The user folder is named after the game in the game's language ("The Sims 4",
/// "Die Sims 4", "Les Sims 4", ...), so identify it by content, not by name.
pub fn find_user_dir(electronic_arts: &Path) -> Result<PathBuf> {
    let not_found = || Error::UserDirNotFound(electronic_arts.to_path_buf());
    let mut candidates: Vec<PathBuf> = fs::read_dir(electronic_arts)
        .map_err(|_| not_found())?
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|path| path.join("GameVersion.txt").is_file() && path.join("Options.ini").is_file())
        .collect();
    candidates.sort();
    candidates.into_iter().next().ok_or_else(not_found)
}

/// `GameVersion.txt` is a 4-byte little-endian length followed by the version.
fn read_version(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    let version: String = bytes
        .iter()
        .skip(4)
        .map(|&b| b as char)
        .filter(|c| c.is_ascii_digit() || *c == '.')
        .collect();
    (!version.is_empty()).then_some(version)
}

fn script_mods_enabled(options_ini: &str) -> bool {
    let value = |key: &str| {
        options_ini.lines().find_map(|line| {
            let (name, value) = line.split_once('=')?;
            (name.trim() == key).then(|| value.trim() == "1")
        })
    };
    value("scriptmodsenabled") == Some(true) && value("modsdisabled") != Some(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_length_prefixed_version() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("GameVersion.txt");
        fs::write(&path, b"\x0d\x00\x00\x001.129.39.1030").unwrap();
        assert_eq!(read_version(&path).as_deref(), Some("1.129.39.1030"));
    }

    #[test]
    fn finds_localized_user_dir_by_content() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("Some Other Game")).unwrap();
        let sims = dir.path().join("Die Sims 4");
        fs::create_dir(&sims).unwrap();
        fs::write(sims.join("GameVersion.txt"), b"").unwrap();
        fs::write(sims.join("Options.ini"), b"").unwrap();
        assert_eq!(find_user_dir(dir.path()).unwrap(), sims);
    }

    #[test]
    fn missing_user_dir_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        assert!(matches!(
            find_user_dir(dir.path()),
            Err(Error::UserDirNotFound(_))
        ));
    }

    #[test]
    fn steam_copies_launch_through_steam() {
        let dir = tempfile::tempdir().unwrap();
        let steamapps = dir.path().join("steamapps");
        let install = steamapps.join("common").join("The Sims 4");
        fs::create_dir_all(&install).unwrap();
        fs::write(steamapps.join("appmanifest_1222670.acf"), b"").unwrap();
        let steam = dir.path().join("steam.exe");
        let args = ["-disablepacks:EP01".to_string()];

        let plan = launch_plan(&install, Some(&steam), &args);

        assert!(plan.via_steam);
        assert_eq!(plan.program, steam);
        assert_eq!(plan.args, ["-applaunch", "1222670", "-disablepacks:EP01"]);
        // Without Steam itself there is nothing to launch through.
        assert!(!launch_plan(&install, None, &args).via_steam);
    }

    #[test]
    fn other_copies_launch_the_executable_directly() {
        let dir = tempfile::tempdir().unwrap();
        let install = dir.path().join("Games").join("The Sims 4");
        fs::create_dir_all(&install).unwrap();
        let args = ["-disablepacks:EP01".to_string()];

        let plan = launch_plan(&install, Some(Path::new("steam.exe")), &args);

        assert!(!plan.via_steam);
        assert_eq!(plan.program, install.join("Game").join("Bin").join("TS4_x64.exe"));
        assert_eq!(plan.args, args);
    }

    #[test]
    fn script_mods_need_both_switches() {
        assert!(script_mods_enabled("scriptmodsenabled = 1\nmodsdisabled = 0\n"));
        assert!(!script_mods_enabled("scriptmodsenabled = 0\nmodsdisabled = 0\n"));
        assert!(!script_mods_enabled("scriptmodsenabled = 1\nmodsdisabled = 1\n"));
        assert!(!script_mods_enabled("unrelated = 1\n"));
    }
}
