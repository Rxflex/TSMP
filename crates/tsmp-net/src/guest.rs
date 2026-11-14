//! The guest side: connects to a host and makes the local Mods folder and
//! shared save identical to the host's.
//!
//! Nothing local is ever deleted. Files the host does not have, and local
//! versions of files that differ, are moved into a backup folder.

use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use tsmp_core::manifest::{Diff, Manifest, PARTIAL_SUFFIX, hash_file};

use crate::paths::safe_join;
use crate::protocol::{
    GuestStatus, Hello, Message, PROTOCOL_VERSION, Root, SaveInfo, Session, expect_message, write_message,
};
use crate::{Error, Result, file_err};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const STATUS_INTERVAL: Duration = Duration::from_millis(500);

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Progress {
    pub done_bytes: u64,
    pub total_bytes: u64,
    /// The file being transferred.
    pub current: String,
}

pub struct Connection {
    reader: BufReader<TcpStream>,
    writer: BufWriter<TcpStream>,
    /// What the host said about itself.
    pub host: Hello,
}

impl Connection {
    pub fn connect(address: SocketAddr, hello: &Hello) -> Result<Self> {
        let stream = TcpStream::connect_timeout(&address, CONNECT_TIMEOUT)?;
        let _ = stream.set_nodelay(true);
        let mut reader = BufReader::with_capacity(1 << 16, stream.try_clone()?);
        let mut writer = BufWriter::new(stream);
        write_message(&mut writer, &Message::Hello(hello.clone()))?;
        let host = match expect_message(&mut reader)? {
            Message::Hello(host) => host,
            Message::Error { message } => return Err(Error::Remote(message)),
            other => return Err(unexpected(&other)),
        };
        if host.protocol != PROTOCOL_VERSION {
            return Err(Error::VersionMismatch {
                ours: PROTOCOL_VERSION,
                theirs: host.protocol,
            });
        }
        Ok(Self { reader, writer, host })
    }

    pub fn session(&mut self) -> Result<Session> {
        write_message(&mut self.writer, &Message::GetSession)?;
        match expect_message(&mut self.reader)? {
            Message::Session(session) => Ok(session),
            Message::Error { message } => Err(Error::Remote(message)),
            other => Err(unexpected(&other)),
        }
    }

    pub fn send_status(&mut self, status: GuestStatus) -> Result<()> {
        write_message(&mut self.writer, &Message::Status(status))
    }

    /// Download one file to `<destination><PARTIAL_SUFFIX>` and check it
    /// against `expected_hash`. Returns the path of the verified partial file;
    /// the caller moves it into place.
    fn download(
        &mut self,
        root: Root,
        path: &str,
        destination: &Path,
        expected_hash: &str,
        on_bytes: &mut dyn FnMut(u64),
    ) -> Result<PathBuf> {
        write_message(
            &mut self.writer,
            &Message::GetFile {
                root,
                path: path.to_string(),
            },
        )?;
        let size = match expect_message(&mut self.reader)? {
            Message::File { size } => size,
            Message::Error { message } => return Err(Error::Remote(message)),
            other => return Err(unexpected(&other)),
        };

        let partial = partial_path(destination);
        if let Some(parent) = partial.parent() {
            fs::create_dir_all(parent).map_err(file_err(parent))?;
        }
        let mut file = File::create(&partial).map_err(file_err(&partial))?;
        let mut hasher = blake3::Hasher::new();
        let mut buffer = vec![0u8; 1 << 18];
        let mut remaining = size;
        // Once the host has announced `size` bytes they must all be read, or
        // the connection is out of step. A local write error is remembered
        // and reported after the stream has been drained.
        let mut write_error = None;
        while remaining > 0 {
            let want = remaining.min(buffer.len() as u64) as usize;
            self.reader.read_exact(&mut buffer[..want])?;
            remaining -= want as u64;
            hasher.update(&buffer[..want]);
            if write_error.is_none() {
                if let Err(e) = file.write_all(&buffer[..want]) {
                    write_error = Some(e);
                }
            }
            on_bytes(want as u64);
        }
        drop(file);
        if let Some(e) = write_error {
            let _ = fs::remove_file(&partial);
            return Err(file_err(&partial)(e));
        }
        if hasher.finalize().to_hex().as_str() != expected_hash {
            let _ = fs::remove_file(&partial);
            return Err(Error::HashMismatch(path.to_string()));
        }
        Ok(partial)
    }
}

/// Bring `mods_dir` in line with the host. `diff` must come from
/// `local.diff_against(host)`. Displaced local files go to `backup_dir`,
/// keeping their relative paths.
pub fn sync_mods(
    connection: &mut Connection,
    mods_dir: &Path,
    backup_dir: &Path,
    host: &Manifest,
    diff: &Diff,
    on_progress: &mut dyn FnMut(&Progress),
) -> Result<()> {
    // Check every path before touching anything.
    let resolve = |path: &String| -> Result<(PathBuf, PathBuf)> {
        match (safe_join(mods_dir, path), safe_join(backup_dir, path)) {
            (Some(local), Some(backup)) => Ok((local, backup)),
            _ => Err(Error::UnsafePath(path.clone())),
        }
    };
    let extra = diff.extra.iter().map(resolve).collect::<Result<Vec<_>>>()?;
    let fetch = diff.fetch.iter().map(resolve).collect::<Result<Vec<_>>>()?;

    for (local, backup) in &extra {
        move_file(local, backup)?;
    }

    let mut progress = Progress {
        total_bytes: diff.fetch_bytes,
        ..Progress::default()
    };
    let mut last_status = Instant::now() - STATUS_INTERVAL;
    for (path, (local, backup)) in diff.fetch.iter().zip(&fetch) {
        let entry = host
            .files
            .get(path)
            .ok_or_else(|| Error::Protocol(format!("{path} is not in the host's manifest")))?;
        progress.current = path.clone();
        on_progress(&progress);
        if last_status.elapsed() >= STATUS_INTERVAL {
            last_status = Instant::now();
            connection.send_status(GuestStatus::Syncing {
                done_bytes: progress.done_bytes,
                total_bytes: progress.total_bytes,
            })?;
        }
        let partial = connection.download(Root::Mods, path, local, &entry.hash, &mut |bytes| {
            progress.done_bytes += bytes;
            on_progress(&progress);
        })?;
        if local.exists() {
            move_file(local, backup)?;
        }
        fs::rename(&partial, local).map_err(file_err(local))?;
    }
    Ok(())
}

/// Fetch the shared save into `saves_dir` unless an identical file is already
/// there. A different local file of the same name goes to `backup_dir` first.
/// Returns whether anything was downloaded.
pub fn sync_save(
    connection: &mut Connection,
    saves_dir: &Path,
    backup_dir: &Path,
    save: &SaveInfo,
    on_progress: &mut dyn FnMut(&Progress),
) -> Result<bool> {
    let unsafe_name = || Error::UnsafePath(save.file_name.clone());
    if save.file_name.contains('/') {
        return Err(unsafe_name());
    }
    let local = safe_join(saves_dir, &save.file_name).ok_or_else(unsafe_name)?;
    let backup = safe_join(backup_dir, &save.file_name).ok_or_else(unsafe_name)?;

    if local.is_file() && hash_file(&local)? == save.hash {
        return Ok(false);
    }
    let mut progress = Progress {
        total_bytes: save.size,
        current: save.file_name.clone(),
        ..Progress::default()
    };
    on_progress(&progress);
    let partial = connection.download(Root::Save, &save.file_name, &local, &save.hash, &mut |bytes| {
        progress.done_bytes += bytes;
        on_progress(&progress);
    })?;
    if local.exists() {
        move_file(&local, &backup)?;
    }
    fs::rename(&partial, &local).map_err(file_err(&local))?;
    Ok(true)
}

fn partial_path(destination: &Path) -> PathBuf {
    let mut name = destination.as_os_str().to_os_string();
    name.push(PARTIAL_SUFFIX);
    PathBuf::from(name)
}

/// Move without ever overwriting: an existing target gets a numbered sibling.
fn move_file(from: &Path, to: &Path) -> Result<()> {
    if let Some(parent) = to.parent() {
        fs::create_dir_all(parent).map_err(file_err(parent))?;
    }
    let mut target = to.to_path_buf();
    let mut attempt = 1;
    while target.exists() {
        let mut name = to.as_os_str().to_os_string();
        name.push(format!(".{attempt}"));
        target = PathBuf::from(name);
        attempt += 1;
    }
    match fs::rename(from, &target) {
        Ok(()) => Ok(()),
        // A different volume: copy, then remove the original.
        Err(_) => {
            fs::copy(from, &target).map_err(file_err(from))?;
            fs::remove_file(from).map_err(file_err(from))
        }
    }
}

fn unexpected(message: &Message) -> Error {
    let kind = match message {
        Message::Hello(_) => "hello",
        Message::JoinLive(_) => "join_live",
        Message::GetSession => "get_session",
        Message::Session(_) => "session",
        Message::GetFile { .. } => "get_file",
        Message::File { .. } => "file",
        Message::Status(_) => "status",
        Message::Error { .. } => "error",
    };
    Error::Protocol(format!("unexpected {kind} message"))
}
