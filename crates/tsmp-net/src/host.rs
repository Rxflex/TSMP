//! The host side: accepts guests and serves them the mods and the shared save.
//!
//! Guests arrive on the host's port from the local network, or through a
//! session server once the host is published there ([`Host::publish`]).
//! Either way the connection is handed to the same [`Lobby`].

use std::collections::HashMap;
use std::fs::File;
use std::io::{self, BufReader, BufWriter, ErrorKind, Read, Write};
use std::net::{Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
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
use crate::rendezvous::{Listing, ServerAddress};
use crate::{Error, POLL, Result, file_err, keep_alive};

pub struct HostConfig {
    pub hello: Hello,
    pub mods_dir: PathBuf,
    /// A scan of `mods_dir`. Only files listed here are ever served.
    pub mods: Manifest,
    /// The shared save file, if one has been chosen.
    pub save: Option<PathBuf>,
    /// Every player loads a save of their own; see [`Session::own_families`].
    pub own_families: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HostEvent {
    GuestJoined { id: u64, address: SocketAddr, hello: Hello },
    GuestStatus { id: u64, status: GuestStatus },
    GuestLeft { id: u64 },
}

/// What every guest is served.
struct Shared {
    hello: Hello,
    mods_dir: PathBuf,
    session: Session,
    save: Option<PathBuf>,
    live: Relay,
}

/// Takes in guests, however they reached the host, and serves each one on a
/// thread of its own.
struct Lobby {
    shared: Shared,
    events: Sender<HostEvent>,
    stop: AtomicBool,
    /// Connections being served, so that closing the lobby ends them.
    connections: Mutex<HashMap<u64, TcpStream>>,
    next_id: AtomicU64,
}

impl Lobby {
    fn welcome(self: &Arc<Self>, stream: TcpStream, address: SocketAddr) {
        // Some platforms hand out accepted sockets non-blocking when the
        // listener is.
        if stream.set_nonblocking(false).is_err() {
            return;
        }
        let _ = stream.set_nodelay(true);
        keep_alive(&stream);
        let Ok(handle) = stream.try_clone() else { return };
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        {
            let mut connections = self.connections.lock().unwrap();
            if self.stop.load(Ordering::Relaxed) {
                let _ = stream.shutdown(Shutdown::Both);
                return;
            }
            connections.insert(id, handle);
        }
        let lobby = self.clone();
        thread::spawn(move || {
            // Errors here mean this guest went away or misbehaved; either way
            // the connection is over and the host carries on.
            let served = lobby.serve(stream, address, id).unwrap_or(Served::Refused);
            if let Some(handle) = lobby.connections.lock().unwrap().remove(&id) {
                // A live connection belongs to the relay now.
                if !matches!(served, Served::Live) {
                    let _ = handle.shutdown(Shutdown::Both);
                }
            }
            if matches!(served, Served::Synced) {
                let _ = lobby.events.send(HostEvent::GuestLeft { id });
            }
        });
    }

    fn close(&self) {
        self.stop.store(true, Ordering::Relaxed);
        for (_, stream) in self.connections.lock().unwrap().drain() {
            let _ = stream.shutdown(Shutdown::Both);
        }
        self.shared.live.close();
    }
}

/// A running host. Dropping it disconnects every guest and takes it off the
/// session server.
pub struct Host {
    port: u16,
    events: Receiver<HostEvent>,
    lobby: Arc<Lobby>,
    thread: Option<JoinHandle<()>>,
    listing: Option<Listing>,
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

        let (events, receiver) = mpsc::channel();
        let lobby = Arc::new(Lobby {
            shared: Shared {
                hello: config.hello,
                mods_dir: config.mods_dir,
                session: Session {
                    mods: config.mods,
                    save: save_info,
                    own_families: config.own_families,
                },
                save: config.save,
                live: Relay::start()?,
            },
            events,
            stop: AtomicBool::new(false),
            connections: Mutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
        });
        let thread = thread::spawn({
            let lobby = lobby.clone();
            move || {
                while !lobby.stop.load(Ordering::Relaxed) {
                    match listener.accept() {
                        Ok((stream, address)) => lobby.welcome(stream, address),
                        Err(e) if e.kind() == ErrorKind::WouldBlock => thread::sleep(POLL),
                        Err(_) => {}
                    }
                }
            }
        });
        Ok(Self {
            port,
            events: receiver,
            lobby,
            thread: Some(thread),
            listing: None,
            _announcer: announcer,
        })
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    /// The live-session relay. Guests that ask to join live are attached to
    /// it; the mod in the host's game connects to its loopback port.
    pub fn live(&self) -> &Relay {
        &self.lobby.shared.live
    }

    /// List the session on a session server too, so that guests outside the
    /// local network can join with an invite. Replaces an earlier listing.
    pub fn publish(&mut self, server: ServerAddress) {
        let lobby = self.lobby.clone();
        let hello = &self.lobby.shared.hello;
        self.listing = Some(Listing::start(
            server,
            hello.name.clone(),
            hello.game_version.clone(),
            move |stream, from| lobby.welcome(stream, from),
        ));
    }

    /// Take the session off the session server. Guests already in stay.
    pub fn unpublish(&mut self) {
        self.listing = None;
    }

    pub fn listing(&self) -> Option<&Listing> {
        self.listing.as_ref()
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
        self.listing = None;
        self.lobby.close();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
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

impl Lobby {
    fn serve(&self, stream: TcpStream, address: SocketAddr, id: u64) -> Result<Served> {
        let shared = &self.shared;
        let events = &self.events;
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
