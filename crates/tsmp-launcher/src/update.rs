//! Keeping the launcher current, and reading files from the repository.
//!
//! The launcher asks GitHub for the latest release when it starts. A newer
//! one is downloaded and put in place of the running file, which Windows
//! allows once that file is renamed; it takes effect the next time the
//! launcher starts. The mod travels inside the launcher, so it is updated
//! by the same step.
//!
//! Nothing about the player or the computer is sent: the requests carry the
//! program's name and version and nothing else.

use std::{
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
    process::Command,
    time::Duration,
};

use sha2::{Digest, Sha256};

const REPOSITORY: &str = "Rxflex/TSMP";
/// The file of a release that is the launcher.
const ASSET: &str = "tsmp.exe";
const LARGEST_DOWNLOAD: u64 = 256 * 1024 * 1024;

pub const CURRENT: &str = env!("CARGO_PKG_VERSION");

#[derive(Debug, PartialEq)]
pub struct Release {
    pub version: String,
    url: String,
    sha256: Option<String>,
}

/// How long the download of a release may go without receiving anything
/// before the server is taken for gone.
const DOWNLOAD_SILENCE: Duration = Duration::from_secs(30);

/// The agent for the launcher's requests. Each is over in two minutes, which
/// is plenty for the small ones and keeps a hung server from holding up the
/// start; the download of a release sets its own limits.
pub fn agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(120)))
        .timeout_connect(Some(Duration::from_secs(8)))
        .user_agent(concat!("tsmp-launcher/", env!("CARGO_PKG_VERSION")))
        .build()
        .into()
}

/// A file of the repository's default branch, or nothing when it is not
/// there or the network is not.
pub fn repository_file(agent: &ureq::Agent, path: &str) -> Option<String> {
    let url = format!("https://raw.githubusercontent.com/{REPOSITORY}/HEAD/{path}");
    agent.get(&url).call().ok()?.body_mut().read_to_string().ok()
}

/// The latest release, if it is newer than this launcher.
pub fn newer_release(agent: &ureq::Agent) -> Result<Option<Release>, String> {
    let url = format!("https://api.github.com/repos/{REPOSITORY}/releases/latest");
    let latest: serde_json::Value = agent
        .get(&url)
        .header("Accept", "application/vnd.github+json")
        .call()
        .and_then(|mut response| response.body_mut().read_json())
        .map_err(|e| e.to_string())?;
    Ok(release_from(&latest, CURRENT))
}

fn release_from(latest: &serde_json::Value, current: &str) -> Option<Release> {
    let version = latest["tag_name"].as_str()?.trim_start_matches('v');
    if numbers(version)? <= numbers(current)? {
        return None;
    }
    let asset = latest["assets"].as_array()?.iter().find(|asset| asset["name"] == ASSET)?;
    Some(Release {
        version: version.to_string(),
        url: asset["browser_download_url"].as_str()?.to_string(),
        sha256: asset["digest"].as_str().and_then(|digest| digest.strip_prefix("sha256:")).map(str::to_lowercase),
    })
}

/// "1.2.3" as numbers, which compare the way versions do.
fn numbers(version: &str) -> Option<Vec<u64>> {
    version.split('.').map(|part| part.parse().ok()).collect()
}

/// Downloads `release` and puts it in place of `exe`, the running launcher.
pub fn install(agent: &ureq::Agent, release: &Release, exe: &Path) -> Result<(), String> {
    let downloaded = exe.with_extension("new");
    let result = download(agent, release, &downloaded).and_then(|()| replace(exe, &downloaded));
    if result.is_err() {
        let _ = fs::remove_file(&downloaded);
    }
    result
}

fn download(agent: &ureq::Agent, release: &Release, target: &Path) -> Result<(), String> {
    // The launcher is tens of megabytes, which a slow connection takes longer
    // than two minutes to bring, so the download has no limit on its whole
    // length. It still ends when the server stops sending.
    let response = agent
        .get(&release.url)
        .config()
        .timeout_global(None)
        .timeout_per_read(Some(DOWNLOAD_SILENCE))
        .build()
        .call()
        .map_err(|e| e.to_string())?;
    let mut reader = response.into_body().into_with_config().limit(LARGEST_DOWNLOAD).reader();
    let mut bytes = Vec::new();
    reader.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    check(&bytes, release.sha256.as_deref())?;
    fs::write(target, bytes).map_err(|e| format!("{}: {e}", target.display()))
}

fn check(bytes: &[u8], sha256: Option<&str>) -> Result<(), String> {
    if !bytes.starts_with(b"MZ") {
        return Err("the download is not a program".into());
    }
    let actual: String = Sha256::digest(bytes).iter().map(|byte| format!("{byte:02x}")).collect();
    match sha256 {
        Some(expected) if expected != actual => Err("the download does not match its checksum".into()),
        _ => Ok(()),
    }
}

fn previous(exe: &Path) -> PathBuf {
    exe.with_extension("old")
}

/// A running program cannot be overwritten but can be renamed.
fn replace(exe: &Path, downloaded: &Path) -> Result<(), String> {
    let old = previous(exe);
    let _ = fs::remove_file(&old);
    fs::rename(exe, &old).map_err(|e| format!("{}: {e}", exe.display()))?;
    fs::rename(downloaded, exe).map_err(|e| {
        let _ = fs::rename(&old, exe);
        format!("{}: {e}", exe.display())
    })
}

/// Removes what the last update left behind.
pub fn clean_up(exe: &Path) {
    let _ = fs::remove_file(previous(exe));
}

/// Starts the launcher again; the caller then quits.
pub fn restart() -> io::Result<()> {
    Command::new(std::env::current_exe()?).spawn().map(drop)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn latest(tag: &str) -> serde_json::Value {
        json!({
            "tag_name": tag,
            "assets": [
                {"name": "notes.txt", "browser_download_url": "https://example.invalid/notes"},
                {"name": "tsmp.exe", "browser_download_url": "https://example.invalid/tsmp", "digest": "sha256:ABCD"},
            ],
        })
    }

    #[test]
    fn only_a_higher_version_is_an_update() {
        assert_eq!(release_from(&latest("v0.2.0"), "0.2.0"), None);
        assert_eq!(release_from(&latest("v0.1.9"), "0.2.0"), None);
        let release = release_from(&latest("v0.10.0"), "0.9.3").unwrap();
        assert_eq!(release.version, "0.10.0");
        assert_eq!(release.url, "https://example.invalid/tsmp");
        assert_eq!(release.sha256.as_deref(), Some("abcd"));
    }

    #[test]
    fn a_release_without_the_launcher_or_with_an_odd_tag_is_skipped() {
        assert_eq!(release_from(&json!({"tag_name": "v9.0.0", "assets": []}), "0.1.0"), None);
        assert_eq!(release_from(&latest("nightly"), "0.1.0"), None);
        assert_eq!(release_from(&json!({"message": "Not Found"}), "0.1.0"), None);
    }

    #[test]
    fn a_download_must_be_a_program_with_the_right_checksum() {
        let program = b"MZ and the rest";
        let sum: String = Sha256::digest(program).iter().map(|byte| format!("{byte:02x}")).collect();
        assert!(check(program, None).is_ok());
        assert!(check(program, Some(&sum)).is_ok());
        assert!(check(program, Some("00")).is_err());
        assert!(check(b"<html>", None).is_err());
    }

    #[test]
    fn the_new_file_takes_the_place_of_the_old_one() {
        let dir = tempfile::tempdir().unwrap();
        let exe = dir.path().join("tsmp.exe");
        let new = exe.with_extension("new");
        fs::write(&exe, "old").unwrap();
        fs::write(&new, "new").unwrap();
        replace(&exe, &new).unwrap();
        assert_eq!(fs::read_to_string(&exe).unwrap(), "new");
        assert_eq!(fs::read_to_string(previous(&exe)).unwrap(), "old");
        clean_up(&exe);
        assert!(!previous(&exe).exists());
    }
}
