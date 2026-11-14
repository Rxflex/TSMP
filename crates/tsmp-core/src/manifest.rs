//! A manifest describes every file in a folder by relative path, size and
//! content hash. Comparing the host's manifest with the local one tells the
//! guest exactly which files to fetch and which local files the host lacks.

use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::Read;
use std::path::Path;
use std::time::UNIX_EPOCH;

use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use walkdir::WalkDir;

use crate::{Result, io_err};

/// Files that differ per machine and must never be synchronised.
const IGNORED_SUFFIXES: [&str; 5] = [".log", ".tmp", ".bak", ".ts4script.disabled", PARTIAL_SUFFIX];

/// Appended to a file's name while it is being downloaded.
pub const PARTIAL_SUFFIX: &str = ".tsmp-part";
const IGNORED_NAMES: [&str; 2] = ["thumbs.db", "desktop.ini"];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Entry {
    pub size: u64,
    /// Modification time in nanoseconds since the Unix epoch. Only used to
    /// decide whether a cached hash is still valid; never compared across
    /// machines.
    pub mtime_ns: u64,
    /// BLAKE3 hash of the content, hex encoded.
    pub hash: String,
}

/// Relative paths use `/` separators regardless of platform.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Manifest {
    pub files: BTreeMap<String, Entry>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Diff {
    /// Paths missing locally or with different content; fetch from the host.
    pub fetch: Vec<String>,
    /// Local paths the host does not have; move aside before playing.
    pub extra: Vec<String>,
    pub fetch_bytes: u64,
}

impl Diff {
    pub fn is_in_sync(&self) -> bool {
        self.fetch.is_empty() && self.extra.is_empty()
    }
}

impl Manifest {
    /// Scan `root`. Hashing is the expensive part (a Mods folder can be tens
    /// of gigabytes), so a hash from `cache` is reused when the file's size
    /// and modification time are unchanged.
    pub fn scan(root: &Path, cache: Option<&Manifest>) -> Result<Self> {
        let mut found = Vec::new();
        for entry in WalkDir::new(root).follow_links(false) {
            let entry = entry.map_err(|e| {
                let path = e.path().unwrap_or(root).to_path_buf();
                crate::Error::Io {
                    path,
                    source: e.into(),
                }
            })?;
            if !entry.file_type().is_file() || is_ignored(&entry.file_name().to_string_lossy()) {
                continue;
            }
            let metadata = entry.metadata().map_err(|e| crate::Error::Io {
                path: entry.path().to_path_buf(),
                source: e.into(),
            })?;
            let relative = entry
                .path()
                .strip_prefix(root)
                .expect("walkdir yields paths under its root")
                .components()
                .map(|c| c.as_os_str().to_string_lossy())
                .collect::<Vec<_>>()
                .join("/");
            found.push((relative, entry.into_path(), metadata.len(), mtime_ns(&metadata)));
        }

        let files = found
            .into_par_iter()
            .map(|(relative, path, size, mtime_ns)| {
                let cached = cache
                    .and_then(|c| c.files.get(&relative))
                    .filter(|e| e.size == size && e.mtime_ns == mtime_ns);
                let hash = match cached {
                    Some(entry) => entry.hash.clone(),
                    None => hash_file(&path)?,
                };
                Ok((
                    relative,
                    Entry {
                        size,
                        mtime_ns,
                        hash,
                    },
                ))
            })
            .collect::<Result<BTreeMap<_, _>>>()?;
        Ok(Self { files })
    }

    pub fn load(path: &Path) -> Result<Self> {
        let file = File::open(path).map_err(io_err(path))?;
        Ok(serde_json::from_reader(std::io::BufReader::new(file))?)
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        let json = serde_json::to_vec(self)?;
        fs::write(path, json).map_err(io_err(path))
    }

    pub fn total_bytes(&self) -> u64 {
        self.files.values().map(|e| e.size).sum()
    }

    /// What `self` (the local folder) needs to become identical to `host`.
    pub fn diff_against(&self, host: &Manifest) -> Diff {
        let mut diff = Diff::default();
        for (path, theirs) in &host.files {
            if self.files.get(path).is_none_or(|mine| mine.hash != theirs.hash) {
                diff.fetch.push(path.clone());
                diff.fetch_bytes += theirs.size;
            }
        }
        diff.extra = self
            .files
            .keys()
            .filter(|path| !host.files.contains_key(*path))
            .cloned()
            .collect();
        diff
    }
}

fn is_ignored(file_name: &str) -> bool {
    let lower = file_name.to_lowercase();
    IGNORED_NAMES.contains(&lower.as_str()) || IGNORED_SUFFIXES.iter().any(|s| lower.ends_with(s))
}

fn mtime_ns(metadata: &fs::Metadata) -> u64 {
    metadata
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_nanos() as u64)
}

/// BLAKE3 hash of a file's content, hex encoded.
pub fn hash_file(path: &Path) -> Result<String> {
    let mut file = File::open(path).map_err(io_err(path))?;
    let mut hasher = blake3::Hasher::new();
    let mut buffer = vec![0u8; 1 << 20];
    loop {
        let read = file.read(&mut buffer).map_err(io_err(path))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hasher.finalize().to_hex().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(root: &Path, relative: &str, content: &str) {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content).unwrap();
    }

    #[test]
    fn scan_records_nested_files_with_forward_slashes() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "a.package", "aaa");
        write(dir.path(), "sub/deep/b.ts4script", "bbbb");

        let manifest = Manifest::scan(dir.path(), None).unwrap();

        assert_eq!(
            manifest.files.keys().collect::<Vec<_>>(),
            ["a.package", "sub/deep/b.ts4script"]
        );
        assert_eq!(manifest.files["a.package"].size, 3);
        assert_eq!(
            manifest.files["a.package"].hash,
            blake3::hash(b"aaa").to_hex().to_string()
        );
        assert_eq!(manifest.total_bytes(), 7);
    }

    #[test]
    fn scan_skips_machine_local_files() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "mod.package", "x");
        write(dir.path(), "mod.log", "x");
        write(dir.path(), "sub/Thumbs.db", "x");
        write(dir.path(), "Resource.cfg", "x");

        let manifest = Manifest::scan(dir.path(), None).unwrap();

        assert_eq!(
            manifest.files.keys().collect::<Vec<_>>(),
            ["Resource.cfg", "mod.package"]
        );
    }

    #[test]
    fn unchanged_files_reuse_the_cached_hash() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "same.package", "same");
        write(dir.path(), "edited.package", "old");
        let mut cache = Manifest::scan(dir.path(), None).unwrap();
        // A marker hash proves the cache was used rather than the file re-read.
        cache.files.get_mut("same.package").unwrap().hash = "from-cache".into();
        cache.files.get_mut("edited.package").unwrap().hash = "stale".into();
        write(dir.path(), "edited.package", "new content");

        let manifest = Manifest::scan(dir.path(), Some(&cache)).unwrap();

        assert_eq!(manifest.files["same.package"].hash, "from-cache");
        assert_eq!(
            manifest.files["edited.package"].hash,
            blake3::hash(b"new content").to_hex().to_string()
        );
    }

    #[test]
    fn diff_lists_missing_changed_and_extra_files() {
        let host_dir = tempfile::tempdir().unwrap();
        write(host_dir.path(), "shared.package", "same");
        write(host_dir.path(), "changed.package", "host version");
        write(host_dir.path(), "host-only.package", "12345");
        let local_dir = tempfile::tempdir().unwrap();
        write(local_dir.path(), "shared.package", "same");
        write(local_dir.path(), "changed.package", "local");
        write(local_dir.path(), "local-only.package", "x");
        let host = Manifest::scan(host_dir.path(), None).unwrap();
        let local = Manifest::scan(local_dir.path(), None).unwrap();

        let diff = local.diff_against(&host);

        assert_eq!(diff.fetch, ["changed.package", "host-only.package"]);
        assert_eq!(diff.extra, ["local-only.package"]);
        assert_eq!(diff.fetch_bytes, "host version".len() as u64 + 5);
        assert!(!diff.is_in_sync());
        assert!(host.diff_against(&host).is_in_sync());
    }

    #[test]
    fn manifest_survives_a_save_and_load() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "a.package", "aaa");
        let manifest = Manifest::scan(dir.path(), None).unwrap();
        let file = dir.path().join("manifest.json.tmp");

        manifest.save(&file).unwrap();

        assert_eq!(Manifest::load(&file).unwrap(), manifest);
    }
}
