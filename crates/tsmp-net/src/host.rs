//! The host side: accepts guests and serves them the mods and the shared save.

use std::fs::File;
use std::io::{self, BufReader, BufWriter, ErrorKind, Read, Write};
use std::net::{Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use tsmp_core::manifest::{Manifest, hash_file};

use crate::discovery::{Announcer, Beacon};
use crate::live::Relay;
use crate::paths::safe_join;
use crate::protocol::{
    GuestStatus, Hello, Message, PROTOCOL_VERSION, Root, SaveInfo, Session, read_message, write_message,
};
use crate::{Error, Result, file_err};

pub struct HostConfig {
    pub hello: Hello,
    pub mods_dir: PathBuf,
    /// A scan of `mods_dir`. Only files listed here are ever served.
    pub mods: Manifest,
    /// The shared save file, if one has been chosen.
    pub save: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HostEvent {
    GuestJoined { id: u64, address: SocketAddr, hello: Hello },
    GuestStatus { id: u64, status: GuestStatus },
    GuestLeft { id: u64 },
}

struct Shared {
    hello: Hello,
    mods_dir: PathBuf,
    session: Session,
    save: Option<PathBuf>,
    live: Relay,
}

/// A running host. Dropping it disconnects every guest.
pub struct Host {
    port: u16,
    events: Receiver<HostEvent>,
    stop: Arc<AtomicBool>,
    connections: Arc<Mutex<Vec<TcpStream>>>,
    thread: Option<JoinHandle<()>>,
    shared: Arc<Shared>,
    _announcer: Option<Announcer>,
}

impl Host {
    /// Listen on `port` (0 picks a free one) and, if `announce`, broadcast a
    /// discovery beacon.
    pub fn start(config: HostConfig, port: u16, announce: bool) -> Result<Self> {
        let save_info = match &config.save {
            Some(path) => Some(SaveInfo {
                file_name: path
                    .file_name()
                    .map(|name| name.to_string_lossy().into_owned())
                    .ok_or_else(|| Error::Protocol(format!("{} is not a file", path.display())))?,
                size: path.metadata().map_err(file_err(path))?.len(),
                hash: hash_file(path)?,
            }),
            None => None,
        };
        let listener = TcpListener::bind((Ipv4Addr::UNSPECIFIED, port))?;
        listener.set_nonblocking(true)?;
        let port = listener.local_addr()?.port();

        let announcer = if announce {
            let beacon = Beacon::new(
                PROTOCOL_VERSION,
                config.hello.name.clone(),
                port,
                config.hello.game_version.clone(),
            );
            Some(Announcer::start(beacon)?)
        } else {
            None
        };

        let shared = Arc::new(Shared {
            hello: config.hello,
            mods_dir: config.mods_dir,
            session: Session {
                mods: config.mods,
                save: save_info,
            },
            save: config.save,
            live: Relay::start()?,
        });
        let (sender, events) = mpsc::channel();
        let stop = Arc::new(AtomicBool::new(false));
        let connections = Arc::new(Mutex::new(Vec::new()));
        let thread = thread::spawn({
            let stop = stop.clone();
            let connections = connections.clone();
            let shared = shared.clone();
            move || accept_loop(listener, shared, sender, stop, connections)
        });
        Ok(Self {
            port,
            events,
            stop,
            connections,
            thread: Some(thread),
            shared,
            _announcer: announcer,
        })
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    /// The live-session relay. Guests that ask to join live are attached to
    /// it; the mod in the host's game connects to its loopback port.
    pub fn live(&self) -> &Relay {
        &self.shared.live
    }

    /// Events since the last call, without blocking.
    pub fn poll(&self) -> Vec<HostEvent> {
        self.events.try_iter().collect()
    }

    pub fn wait(&self, timeout: Duration) -> Option<HostEvent> {
        self.events.recv_timeout(timeout).ok()
    }
}

impl Drop for Host {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        for stream in self.connections.lock().unwrap().drain(..) {
            let _ = stream.shutdown(Shutdown::Both);
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn accept_loop(
    listener: TcpListener,
    shared: Arc<Shared>,
    events: Sender<HostEvent>,
    stop: Arc<AtomicBool>,
    connections: Arc<Mutex<Vec<TcpStream>>>,
) {
    let mut next_id = 1u64;
    while !stop.load(Ordering::Relaxed) {
        let (stream, address) = match listener.accept() {
            Ok(accepted) => accepted,
            Err(e) if e.kind() == ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(100));
                continue;
            }
            Err(_) => continue,
        };
        // The listener is non-blocking; on some platforms accepted sockets
        // inherit that.
        if stream.set_nonblocking(false).is_err() {
            continue;
        }
        let _ = stream.set_nodelay(true);
        // Two extra handles: one lets `Host::drop` disconnect the guest, the
        // other closes the socket when serving ends (the first would
        // otherwise keep it open).
        let (Ok(for_drop), Ok(for_close)) = (stream.try_clone(), stream.try_clone()) else {
            continue;
        };
        connections.lock().unwrap().push(for_drop);
        let id = next_id;
        next_id += 1;
        let shared = shared.clone();
        let events = events.clone();
        thread::spawn(move || {
            // Errors here mean this guest went away or misbehaved; either way
            // the connection is over and the host carries on.
            match serve(stream, address, id, &shared, &events).unwrap_or(Served::Refused) {
                // The relay owns the connection now.
                Served::Live => {}
                Served::Refused => {
                    let _ = for_close.shutdown(Shutdown::Both);
                }
                Served::Synced => {
                    let _ = for_close.shutdown(Shutdown::Both);
                    let _ = events.send(HostEvent::GuestLeft { id });
                }
            }
        });
    }
}

enum Served {
    /// Never got past the handshake.
    Refused,
    /// A sync connection that joined and has now ended.
    Synced,
    /// A live connection, handed to the relay.
    Live,
}

fn serve(
    stream: TcpStream,
    address: SocketAddr,
    id: u64,
    shared: &Shared,
    events: &Sender<HostEvent>,
) -> Result<Served> {
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut writer = BufWriter::with_capacity(1 << 16, stream);

    let hello = match read_message(&mut reader)? {
        Some(Message::Hello(hello)) => hello,
        Some(Message::JoinLive(hello)) => {
            write_message(&mut writer, &Message::Hello(shared.hello.clone()))?;
            // The guest sends nothing more until it has read the reply, so
            // nothing of the frame stream can be sitting in the buffer.
            if hello.protocol != PROTOCOL_VERSION || !reader.buffer().is_empty() {
                return Ok(Served::Refused);
            }
            drop(writer);
            shared.live.add_remote(reader.into_inner())?;
            return Ok(Served::Live);
        }
        _ => {
            write_message(&mut writer, &error("expected hello"))?;
            return Ok(Served::Refused);
        }
    };
    write_message(&mut writer, &Message::Hello(shared.hello.clone()))?;
    if hello.protocol != PROTOCOL_VERSION {
        return Ok(Served::Refused);
    }
    let _ = events.send(HostEvent::GuestJoined { id, address, hello });

    let result = (|| -> Result<()> {
        while let Some(message) = read_message(&mut reader)? {
            match message {
                Message::GetSession => {
                    write_message(&mut writer, &Message::Session(shared.session.clone()))?;
                }
                Message::GetFile { root, path } => send_file(&mut writer, shared, root, &path)?,
                Message::Status(status) => {
                    let _ = events.send(HostEvent::GuestStatus { id, status });
                }
                _ => write_message(&mut writer, &error("unexpected message"))?,
            }
        }
        Ok(())
    })();
    // The guest did join, whatever ended the connection.
    let _ = result;
    Ok(Served::Synced)
}

fn send_file(writer: &mut impl Write, shared: &Shared, root: Root, path: &str) -> Result<()> {
    let local = match root {
        Root::Mods if shared.session.mods.files.contains_key(path) => safe_join(&shared.mods_dir, path),
        Root::Mods => None,
        Root::Save => shared.save.clone(),
    };
    let Some(local) = local else {
        return write_message(writer, &error(&format!("no such file: {path}")));
    };
    let opened = File::open(&local).and_then(|file| Ok((file.metadata()?.len(), file)));
    let (size, file) = match opened {
        Ok(opened) => opened,
        Err(e) => return write_message(writer, &error(&format!("{path}: {e}"))),
    };
    write_message(writer, &Message::File { size })?;
    // Exactly `size` bytes must follow, even if the file changes underneath.
    let copied = io::copy(&mut file.take(size), writer)?;
    if copied != size {
        return Err(Error::Protocol(format!("{path} shrank while being sent")));
    }
    writer.flush()?;
    Ok(())
}

fn error(message: &str) -> Message {
    Message::Error {
        message: message.to_string(),
    }
}
