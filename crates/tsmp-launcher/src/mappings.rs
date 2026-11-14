//! Addresses inside the game's simulation library, one file per game version.
//!
//! Building together works on addresses that move with every build of the
//! game. They live in `mappings/<game version>.json` at the top of the
//! repository; the launcher carries the ones known when it was built and
//! asks the repository for the installed version when it has none, so a new
//! game version needs a new file there and not a new launcher.
//!
//! The mod reads them from `<user folder>/tsmp/mappings/` and checks every
//! address against the running game before it uses one.

use std::{fs, path::Path};

use rust_embed::RustEmbed;

#[derive(RustEmbed)]
#[folder = "../../mappings/"]
#[include = "*.json"]
struct BuiltIn;

/// Whether building together can work with the installed game.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Support {
    Ready,
    /// Nobody has described this game version yet.
    Missing,
    /// `GameVersion.txt` could not be read, so there is nothing to look up.
    UnknownVersion,
}

/// Puts the mappings where the mod looks for them and says whether one of
/// them is for `version`. `fetch` gets the file of a version from the
/// repository, or nothing.
pub fn install(data_dir: &Path, version: Option<&str>, fetch: impl Fn(&str) -> Option<String>) -> Support {
    let dir = data_dir.join("mappings");
    let _ = fs::create_dir_all(&dir);
    for name in BuiltIn::iter() {
        if let Some(file) = BuiltIn::get(&name) {
            let _ = fs::write(dir.join(name.as_ref()), file.data);
        }
    }
    let Some(version) = version.filter(|version| is_version(version)) else {
        return Support::UnknownVersion;
    };
    let name = format!("{version}.json");
    if BuiltIn::get(&name).is_some() {
        return Support::Ready;
    }
    let path = dir.join(&name);
    // The repository first: a mapping fetched earlier may have been corrected.
    if let Some(text) = fetch(version).filter(|text| is_for(text, version)) {
        let _ = fs::write(&path, text);
        return Support::Ready;
    }
    match fs::read_to_string(&path) {
        Ok(text) if is_for(&text, version) => Support::Ready,
        _ => Support::Missing,
    }
}

/// Versions end up in a URL and a file name.
fn is_version(text: &str) -> bool {
    !text.is_empty() && text.len() <= 32 && text.chars().all(|c| c.is_ascii_digit() || c == '.')
}

fn is_for(text: &str, version: &str) -> bool {
    serde_json::from_str::<serde_json::Value>(text)
        .is_ok_and(|mapping| mapping["format"] == 1 && mapping["game_version"] == version)
}

#[cfg(test)]
mod tests {
    use super::*;

    const KNOWN: &str = "1.129.39.1030";

    #[test]
    fn a_built_in_version_needs_no_network() {
        let dir = tempfile::tempdir().unwrap();
        let support = install(dir.path(), Some(KNOWN), |_| panic!("not asked"));
        assert_eq!(support, Support::Ready);
        assert!(dir.path().join("mappings").join(format!("{KNOWN}.json")).is_file());
    }

    #[test]
    fn every_built_in_file_is_named_by_its_version() {
        for name in BuiltIn::iter() {
            let text = String::from_utf8(BuiltIn::get(&name).unwrap().data.into_owned()).unwrap();
            assert!(is_for(&text, name.trim_end_matches(".json")), "{name}");
        }
    }

    #[test]
    fn another_version_comes_from_the_repository_and_is_kept() {
        let dir = tempfile::tempdir().unwrap();
        let fetched = r#"{"format":1,"game_version":"2.0.0.1"}"#;
        assert_eq!(install(dir.path(), Some("2.0.0.1"), |_| Some(fetched.into())), Support::Ready);
        // Offline the next time.
        assert_eq!(install(dir.path(), Some("2.0.0.1"), |_| None), Support::Ready);
    }

    #[test]
    fn a_file_for_another_version_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let wrong = r#"{"format":1,"game_version":"3.0.0.0"}"#;
        assert_eq!(install(dir.path(), Some("2.0.0.1"), |_| Some(wrong.into())), Support::Missing);
        assert_eq!(install(dir.path(), Some("2.0.0.1"), |_| Some("<html>".into())), Support::Missing);
        assert!(!dir.path().join("mappings/2.0.0.1.json").exists());
    }

    #[test]
    fn a_version_that_is_not_one_is_never_looked_up() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(install(dir.path(), None, |_| panic!("not asked")), Support::UnknownVersion);
        assert_eq!(install(dir.path(), Some("../x"), |_| panic!("not asked")), Support::UnknownVersion);
    }
}
