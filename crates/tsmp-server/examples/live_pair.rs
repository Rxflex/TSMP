//! A host launcher's relay and a guest launcher's link on this machine, for
//! testing the mod's side of the bridge against the real thing
//! (`mod/tests/test_relay.py`).
//!
//! With `online` the two launchers meet through a session server, also on
//! this machine, the way players on different networks do; without, the
//! guest dials the host directly.
//!
//! With `players=N` the session takes N players, the host included, and N - 1
//! guest launchers join it, one after the other, so that the relay numbers
//! them 1, 2, ... in that order. Only for testing several guests: a launcher
//! as it ships keeps to [`DEFAULT_MAX_PLAYERS`]. The first guest is named
//! `guest`, the next `guest2` and so on.
//!
//! Prints `<host game port> <guest game port>...` and runs until stdin closes.
//! A line `drop N` on stdin drops the link of guest N, as a player whose
//! launcher goes.

use std::io::BufRead;
use std::net::Ipv4Addr;
use std::thread;
use std::time::{Duration, Instant};

use tsmp_core::manifest::Manifest;
use tsmp_net::host::{DEFAULT_MAX_PLAYERS, Host, HostConfig, MAX_PLAYERS};
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
        player: Some(format!("{name}-id")),
        ..Default::default()
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let online = args.iter().any(|arg| arg == "online");
    let players = match args.iter().find_map(|arg| arg.strip_prefix("players=")) {
        Some(count) => count.parse::<usize>()?.clamp(2, MAX_PLAYERS),
        None => DEFAULT_MAX_PLAYERS,
    };
    let dir = tempfile::tempdir()?;
    let config = HostConfig {
        hello: hello("host"),
        mods_dir: dir.path().to_path_buf(),
        mods: Manifest::default(),
        save: None,
        own_families: false,
        max_players: players,
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
    let mut links = Vec::new();
    let mut ports = vec![host.live().port().to_string()];
    for number in 1..players {
        let name = if number == 1 { "guest".to_string() } else { format!("guest{number}") };
        let link = LiveLink::start(route.clone(), hello(&name))?;
        // Each joins before the next dials, which keeps the order of their
        // numbers.
        let deadline = Instant::now() + Duration::from_secs(10);
        while !host.live().players().contains(&Some(format!("{name}-id"))) {
            if Instant::now() > deadline {
                return Err(format!("{name} did not join the host's relay").into());
            }
            thread::sleep(Duration::from_millis(20));
        }
        ports.push(link.port().to_string());
        links.push(Some(link));
    }
    println!("{}", ports.join(" "));

    for line in std::io::stdin().lock().lines() {
        if let Some(number) = line?.trim().strip_prefix("drop ") {
            let number: usize = number.parse()?;
            if let Some(link) = links.get_mut(number.wrapping_sub(1)) {
                link.take();
            }
        }
    }
    Ok(())
}
