//! Listing save games so the host can pick the one to share.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use crate::{Result, io_err};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SaveFile {
    pub path: PathBuf,
    /// `Slot_00000002.save`
    pub file_name: String,
    pub size: u64,
    pub modified: SystemTime,
}

/// Primary save files, newest first. The game keeps numbered backups next to
/// each (`.save.ver0`, ...); those are not listed.
pub fn list_saves(saves_dir: &Path) -> Result<Vec<SaveFile>> {
    let mut saves = Vec::new();
    for entry in fs::read_dir(saves_dir).map_err(io_err(saves_dir))? {
        let entry = entry.map_err(io_err(saves_dir))?;
        let file_name = entry.file_name().to_string_lossy().into_owned();
        if !is_save_name(&file_name) {
            continue;
        }
        let path = entry.path();
        let metadata = entry.metadata().map_err(io_err(&path))?;
        if !metadata.is_file() {
            continue;
        }
        saves.push(SaveFile {
            path,
            file_name,
            size: metadata.len(),
            modified: metadata.modified().unwrap_or(SystemTime::UNIX_EPOCH),
        });
    }
    saves.sort_by(|a, b| b.modified.cmp(&a.modified).then_with(|| a.file_name.cmp(&b.file_name)));
    Ok(saves)
}

fn is_save_name(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    lower.starts_with("slot_") && lower.ends_with(".save")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lists_only_primary_saves() {
        let dir = tempfile::tempdir().unwrap();
        for name in [
            "Slot_00000002.save",
            "Slot_00000002.save.ver0",
            "Slot_00000002.save.day.ver0",
            "Slot_00000003.save",
            "steam_autocloud.vdf",
        ] {
            fs::write(dir.path().join(name), b"data").unwrap();
        }
        fs::create_dir(dir.path().join("Slot_99999999.save")).unwrap();

        let mut names: Vec<String> = list_saves(dir.path())
            .unwrap()
            .into_iter()
            .map(|s| s.file_name)
            .collect();
        names.sort();

        assert_eq!(names, ["Slot_00000002.save", "Slot_00000003.save"]);
    }

    #[test]
    fn missing_folder_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        assert!(list_saves(&dir.path().join("saves")).is_err());
    }
}
