//! A host launcher's relay and a guest launcher's link on this machine, for
//! testing the mod's side of the bridge against the real thing
//! (`mod/tests/test_relay.py`).
//!
//! With `online` the two launchers meet through a session server, also on
//! this machine, the way players on different networks do; without, the
//! guest dials the host directly.
//!
//! Prints `<host game port> <guest game port>` and runs until stdin closes.

use std::io::Read;
use std::net::Ipv4Addr;
use std::thread;
use std::time::{Duration, Instant};

use tsmp_core::manifest::Manifest;
use tsmp_net::host::{Host, HostConfig};
use tsmp_net::live::LiveLink;
use tsmp_net::protocol::{Hello, PROTOCOL_VERSION};
use tsmp_net::rendezvous::ServerAddress;
use tsmp_net::route::Route;
use tsmp_server::{Config, Journal, Server};

fn hello(name: &str) -> Hello {
    Hello {
        protocol: PROTOCOL_VERSION,
        name: name.into(),
        game_version: None,
        packs: Default::default(),
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let online = std::env::args().nth(1).as_deref() == Some("online");
    let dir = tempfile::tempdir()?;
    let config = HostConfig {
        hello: hello("host"),
        mods_dir: dir.path().to_path_buf(),
        mods: Manifest::default(),
        save: None,
        own_families: false,
    };
    let mut host = Host::start(config, 0, false)?;
    let (_server, route) = if online {
        let server = Server::start(Config {
            listen: (Ipv4Addr::LOCALHOST, 0).into(),
            journal: Journal::silent(),
            ..Config::default()
        })?;
        host.publish(ServerAddress::new("127.0.0.1", server.local_addr().port()));
        let deadline = Instant::now() + Duration::from_secs(10);
        let invite = loop {
            if let Some(invite) = host.listing().and_then(|listing| listing.status().invite().cloned()) {
                break invite;
            }
            if Instant::now() > deadline {
                return Err("the host was not listed on the server".into());
            }
            thread::sleep(Duration::from_millis(20));
        };
        (Some(server), Route::Invite(invite))
    } else {
        (None, Route::Direct((Ipv4Addr::LOCALHOST, host.port()).into()))
    };
    let link = LiveLink::start(route, hello("guest"))?;
    println!("{} {}", host.live().port(), link.port());

    let mut sink = Vec::new();
    std::io::stdin().read_to_end(&mut sink)?;
    Ok(())
}
