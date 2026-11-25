//! Launchers meeting through a real session server over loopback.

use std::fs;
use std::io::{Read, Write};
use std::net::{Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use tsmp_core::manifest::Manifest;
use tsmp_net::Error;
use tsmp_net::guest::{Connection, sync_mods, sync_save};
use tsmp_net::host::{Host, HostConfig, HostEvent};
use tsmp_net::live::{KIND_COMMAND, KIND_CONTROL, KIND_MSG, LiveLink, frame, read_frame};
use tsmp_net::protocol::{Hello, PROTOCOL_VERSION, write_message};
use tsmp_net::rendezvous::{
    Invite, ListingStatus, Reply, Request, Resume, SERVER_PROTOCOL, ServerAddress, SessionCode, expect, send,
};
use tsmp_net::route::Route;
use tsmp_server::{Config, Journal, Server};

const WAIT: Duration = Duration::from_secs(15);

fn hello(name: &str) -> Hello {
    Hello {
        protocol: PROTOCOL_VERSION,
        name: name.into(),
        game_version: Some("1.0".into()),
        packs: Default::default(),
    }
}

fn write(root: &Path, relative: &str, content: &str) {
    let path = root.join(relative);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, content).unwrap();
}

fn wait_for(what: &str, mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + WAIT;
    while !condition() {
        assert!(Instant::now() < deadline, "timed out waiting for {what}");
        thread::sleep(Duration::from_millis(20));
    }
}

fn quick() -> Config {
    Config {
        listen: (Ipv4Addr::LOCALHOST, 0).into(),
        ping_interval: Duration::from_millis(200),
        join_timeout: Duration::from_secs(5),
        handshake_timeout: Duration::from_secs(2),
        journal: Journal::silent(),
        ..Config::default()
    }
}

fn address_of(server: &Server) -> ServerAddress {
    ServerAddress::new("127.0.0.1", server.local_addr().port())
}

fn start_host(mods_dir: &Path, save: Option<&Path>) -> Host {
    let config = HostConfig {
        hello: hello("host"),
        mods_dir: mods_dir.to_path_buf(),
        mods: Manifest::scan(mods_dir, None).unwrap(),
        save: save.map(Path::to_path_buf),
        own_families: false,
    };
    Host::start(config, 0, false).unwrap()
}

fn invite(host: &Host) -> Invite {
    let mut invite = None;
    wait_for("the host to be listed", || {
        invite = match host.listing().map(|listing| listing.status()) {
            Some(ListingStatus::Open(invite)) => Some(invite),
            _ => None,
        };
        invite.is_some()
    });
    invite.unwrap()
}

fn refusal(result: tsmp_net::Result<impl Sized>) -> String {
    match result {
        Err(Error::Remote(message)) => message,
        Err(other) => panic!("expected the server to refuse, got {other}"),
        Ok(_) => panic!("expected the server to refuse"),
    }
}

/// Stands in for the mod: a socket on a relay's loopback port.
struct Game(TcpStream);

impl Game {
    fn connect(port: u16) -> Self {
        let stream = TcpStream::connect((Ipv4Addr::LOCALHOST, port)).unwrap();
        stream.set_read_timeout(Some(WAIT)).unwrap();
        Self(stream)
    }

    fn send(&mut self, kind: u8, payload: &[u8]) {
        self.0.write_all(&frame(kind, 7, payload)).unwrap();
    }

    fn in_zone(&mut self, role: &str) {
        let state = format!(r#"{{"type":"state","role":"{role}","in_zone":true}}"#);
        self.send(KIND_CONTROL, state.as_bytes());
    }

    /// The payload of the next frame of `kind`, skipping the relay's own.
    fn receive(&mut self, kind: u8) -> Vec<u8> {
        loop {
            let frame = read_frame(&mut self.0)
                .unwrap()
                .expect("the relay closed the connection");
            if frame[0] == kind {
                return frame[9..].to_vec();
            }
        }
    }
}

#[test]
fn a_guest_syncs_and_plays_through_the_server() {
    let server = Server::start(quick()).unwrap();
    let host_dir = tempfile::tempdir().unwrap();
    write(host_dir.path(), "Mods/tsmp.ts4script", "the mod");
    write(host_dir.path(), "Mods/cc/hair.package", &"hair".repeat(100_000));
    write(host_dir.path(), "saves/Slot_00000002.save", "the save");
    let mut host = start_host(
        &host_dir.path().join("Mods"),
        Some(&host_dir.path().join("saves/Slot_00000002.save")),
    );
    host.publish(address_of(&server));
    let invite = invite(&host);
    assert_eq!(invite.server, address_of(&server));
    let route = Route::Invite(invite.clone());

    // Syncing, exactly as on the local network.
    let guest_dir = tempfile::tempdir().unwrap();
    let mods = guest_dir.path().join("Mods");
    fs::create_dir_all(&mods).unwrap();
    let mut connection = Connection::connect(&route, &hello("guest")).unwrap();
    assert_eq!(connection.host.name, "host");
    let joined = host.wait(WAIT).expect("the host did not see the guest");
    let HostEvent::GuestJoined { address, hello: guest, .. } = joined else {
        panic!("expected the guest to join, got {joined:?}");
    };
    assert_eq!(
        (address.ip(), guest.name.as_str()),
        (Ipv4Addr::LOCALHOST.into(), "guest")
    );
    let session = connection.session().unwrap();
    let diff = Manifest::scan(&mods, None).unwrap().diff_against(&session.mods);
    sync_mods(
        &mut connection,
        &mods,
        &guest_dir.path().join("backup"),
        &session.mods,
        &diff,
        &mut |_| {},
    )
    .unwrap();
    let saves = guest_dir.path().join("saves");
    let save = session.save.as_ref().unwrap();
    assert!(
        sync_save(
            &mut connection,
            &saves,
            &guest_dir.path().join("saves-backup"),
            save,
            &mut |_| {}
        )
        .unwrap()
    );
    assert_eq!(fs::read_to_string(mods.join("cc/hair.package")).unwrap().len(), 400_000);
    assert_eq!(
        fs::read_to_string(saves.join("Slot_00000002.save")).unwrap(),
        "the save"
    );
    assert_eq!(server.stats().tunnels, 1);

    // The live session goes the same way.
    let link = LiveLink::start(route, hello("guest")).unwrap();
    let mut host_game = Game::connect(host.live().port());
    let mut guest_game = Game::connect(link.port());
    wait_for("the launchers to join", || {
        host.live().status().remotes == 1 && link.status().remotes == 1
    });
    host_game.in_zone("host");
    guest_game.in_zone("guest");
    wait_for("the session to be live", || {
        host.live().status().is_live() && link.status().is_live()
    });
    let view: Vec<u8> = (0..2_000_000u32).map(|i| i as u8).collect();
    host_game.send(KIND_MSG, &view);
    assert_eq!(guest_game.receive(KIND_MSG), view);
    guest_game.send(KIND_COMMAND, b"{\"name\":\"interactions.select\"}");
    assert_eq!(host_game.receive(KIND_COMMAND), b"{\"name\":\"interactions.select\"}");
    assert_eq!(server.stats().tunnels, 2);

    // The guest leaving ends both of its connections through the server.
    drop(link);
    drop(connection);
    wait_for("the host to see the guest leave", || {
        host.live().status().remotes == 0 && server.stats().tunnels == 0
    });

    // Closing the host takes the session off the server at once.
    drop(host);
    wait_for("the session to be gone", || server.stats().sessions == 0);
    let gone = refusal(Connection::connect(&Route::Invite(invite.clone()), &hello("late")));
    assert!(gone.contains(&invite.code.to_string()), "{gone}");
}

/// A stand-in for the network between the host and the server, which can
/// drop every connection through it at once.
struct Cable {
    port: u16,
    cut: Arc<Mutex<Vec<TcpStream>>>,
}

impl Cable {
    fn to(target: SocketAddr) -> Self {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let cut = Arc::new(Mutex::new(Vec::new()));
        thread::spawn({
            let cut = cut.clone();
            move || {
                for near in listener.incoming().flatten() {
                    let Ok(far) = TcpStream::connect(target) else { continue };
                    cut.lock()
                        .unwrap()
                        .extend([near.try_clone().unwrap(), far.try_clone().unwrap()]);
                    for (mut from, mut to) in [(near.try_clone().unwrap(), far.try_clone().unwrap()), (far, near)] {
                        thread::spawn(move || {
                            let _ = std::io::copy(&mut from, &mut to);
                            let _ = to.shutdown(Shutdown::Both);
                        });
                    }
                }
            }
        });
        Self { port, cut }
    }

    fn cut(&self) {
        for stream in self.cut.lock().unwrap().drain(..) {
            let _ = stream.shutdown(Shutdown::Both);
        }
    }
}

#[test]
fn a_host_that_loses_the_server_comes_back_under_the_same_code() {
    let server = Server::start(quick()).unwrap();
    let cable = Cable::to(server.local_addr());
    let dir = tempfile::tempdir().unwrap();
    let mut host = start_host(dir.path(), None);
    host.publish(ServerAddress::new("127.0.0.1", cable.port));
    let before = invite(&host);

    cable.cut();
    wait_for("the host to notice", || {
        matches!(host.listing().unwrap().status(), ListingStatus::Retrying { .. })
    });
    assert_eq!(host.listing().unwrap().status().invite(), Some(&before));
    assert_eq!(invite(&host), before);
    assert_eq!(server.stats().sessions, 1);

    // The invite still works after the host is back.
    let direct = Invite {
        code: before.code.clone(),
        server: address_of(&server),
    };
    let connection = Connection::connect(&Route::Invite(direct), &hello("guest")).unwrap();
    assert_eq!(connection.host.name, "host");
}

#[test]
fn a_session_outlives_a_silent_host_only_for_a_while() {
    let server = Server::start(Config {
        resume_grace: Duration::from_millis(500),
        ..quick()
    })
    .unwrap();
    let line = address_of(&server).connect().unwrap();
    let open = Request::Open {
        protocol: SERVER_PROTOCOL,
        name: "silent".into(),
        game_version: None,
        resume: None,
    };
    send(&line, &open).unwrap();
    let Reply::Opened {
        code,
        token,
        ping_seconds,
    } = expect(&line).unwrap()
    else {
        panic!("not opened");
    };
    assert_eq!(ping_seconds, 1);
    assert_eq!(expect::<Reply>(&line).unwrap(), Reply::Ping);
    wait_for("the server to give up on the host", || server.stats().hosted == 0);

    let invite = Invite {
        code: code.clone(),
        server: address_of(&server),
    };
    let away = refusal(Connection::connect(&Route::Invite(invite.clone()), &hello("guest")));
    assert!(away.contains("lost the session server"), "{away}");
    wait_for("the session to expire", || server.stats().sessions == 0);
    // Its code is kept for its host, so the invite still says to wait.
    let still = refusal(Connection::connect(&Route::Invite(invite), &hello("guest")));
    assert!(still.contains("lost the session server"), "{still}");

    // The host is back, long after: it gets its code again.
    let again = address_of(&server).connect().unwrap();
    let resume = Request::Open {
        protocol: SERVER_PROTOCOL,
        name: "silent".into(),
        game_version: None,
        resume: Some(Resume { code: code.clone(), token }),
    };
    send(&again, &resume).unwrap();
    let Reply::Opened { code: back, .. } = expect(&again).unwrap() else {
        panic!("not opened again");
    };
    assert_eq!(back, code);
}

#[test]
fn a_host_that_does_not_answer_a_guest_lets_the_guest_go() {
    let server = Server::start(Config {
        join_timeout: Duration::from_millis(300),
        ..quick()
    })
    .unwrap();
    let line = address_of(&server).connect().unwrap();
    let open = Request::Open {
        protocol: SERVER_PROTOCOL,
        name: "busy".into(),
        game_version: None,
        resume: None,
    };
    send(&line, &open).unwrap();
    let Reply::Opened { code, token, .. } = expect(&line).unwrap() else {
        panic!("not opened");
    };
    let invite = Invite {
        code,
        server: address_of(&server),
    };
    let late = thread::spawn(move || refusal(Connection::connect(&Route::Invite(invite), &hello("guest"))));
    let tunnel = loop {
        match expect::<Reply>(&line).unwrap() {
            Reply::Incoming { tunnel, from } => {
                assert_eq!(from.ip(), Ipv4Addr::LOCALHOST);
                break tunnel;
            }
            Reply::Ping => send(&line, &Request::Pong).unwrap(),
            other => panic!("unexpected {other:?}"),
        }
    };
    assert!(late.join().unwrap().starts_with("The host did not answer"));

    // Answering now is too late, and says so.
    let accept = address_of(&server).connect().unwrap();
    send(
        &accept,
        &Request::Accept {
            protocol: SERVER_PROTOCOL,
            token,
            tunnel,
        },
    )
    .unwrap();
    assert!(matches!(expect::<Reply>(&accept).unwrap(), Reply::Error { .. }));
}

#[test]
fn guessing_codes_is_held_back() {
    let server = Server::start(Config {
        wrong_codes_per_minute: 3,
        ..quick()
    })
    .unwrap();
    let guess = || {
        let invite = Invite {
            code: SessionCode::random(),
            server: address_of(&server),
        };
        refusal(Connection::connect(&Route::Invite(invite), &hello("guesser")))
    };
    for _ in 0..3 {
        assert!(guess().starts_with("No session has the code"));
    }
    assert!(guess().starts_with("Too many wrong codes"));
}

#[test]
fn strangers_are_turned_away_without_harm() {
    let server = Server::start(Config {
        max_connections_per_address: 3,
        ..quick()
    })
    .unwrap();
    let address = server.local_addr();
    let closes = |mut stream: TcpStream| {
        stream.set_read_timeout(Some(WAIT)).unwrap();
        let mut rest = Vec::new();
        let _ = stream.read_to_end(&mut rest);
        rest
    };

    // Not a launcher at all.
    let mut http = TcpStream::connect(address).unwrap();
    http.write_all(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n").unwrap();
    assert!(closes(http).is_empty());
    // A message far longer than any of the conversation's.
    let mut huge = TcpStream::connect(address).unwrap();
    huge.write_all(&(1u32 << 20).to_le_bytes()).unwrap();
    assert!(closes(huge).is_empty());
    // A launcher of another version is told why.
    let other = address_of(&server).connect().unwrap();
    write_message(
        &mut &other,
        &Request::Join {
            protocol: SERVER_PROTOCOL + 1,
            code: SessionCode::random(),
        },
    )
    .unwrap();
    let Reply::Error { message } = expect(&other).unwrap() else {
        panic!("expected a refusal");
    };
    assert!(message.contains("Update"), "{message}");
    // Someone who says nothing is let go.
    let quiet = TcpStream::connect(address).unwrap();
    let started = Instant::now();
    assert!(closes(quiet).is_empty());
    assert!(started.elapsed() < WAIT);

    // One address cannot hold every connection.
    let held: Vec<TcpStream> = (0..3).map(|_| TcpStream::connect(address).unwrap()).collect();
    wait_for("the server to count them", || server.stats().connections == 3);
    let crowd = TcpStream::connect(address).unwrap();
    crowd.set_read_timeout(Some(WAIT)).unwrap();
    let Reply::Error { message } = expect(&crowd).unwrap() else {
        panic!("expected a refusal");
    };
    assert!(message.starts_with("Too many connections"), "{message}");
    drop(held);
    wait_for("the server to let them go", || server.stats().connections == 0);
}

#[test]
fn stopping_the_server_ends_every_session() {
    let server = Server::start(quick()).unwrap();
    let dir = tempfile::tempdir().unwrap();
    let mut host = start_host(dir.path(), None);
    host.publish(address_of(&server));
    let invite = invite(&host);
    let mut connection = Connection::connect(&Route::Invite(invite), &hello("guest")).unwrap();
    drop(server);
    assert!(connection.session().is_err());
    wait_for("the host to notice", || {
        matches!(host.listing().unwrap().status(), ListingStatus::Retrying { .. })
    });
}
