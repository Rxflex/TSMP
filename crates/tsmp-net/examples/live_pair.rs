//! A host launcher's relay and a guest launcher's link on this machine, for
//! testing the mod's side of the bridge against the real thing
//! (`mod/tests/test_relay.py`).
//!
//! Prints `<host game port> <guest game port>` and runs until stdin closes.

use std::io::Read;
use std::net::Ipv4Addr;

use tsmp_core::manifest::Manifest;
use tsmp_net::host::{Host, HostConfig};
use tsmp_net::live::LiveLink;
use tsmp_net::protocol::{Hello, PROTOCOL_VERSION};

fn hello(name: &str) -> Hello {
    Hello {
        protocol: PROTOCOL_VERSION,
        name: name.into(),
        game_version: None,
        packs: Default::default(),
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let dir = tempfile::tempdir()?;
    let config = HostConfig {
        hello: hello("host"),
        mods_dir: dir.path().to_path_buf(),
        mods: Manifest::default(),
        save: None,
    };
    let host = Host::start(config, 0, false)?;
    let link = LiveLink::start((Ipv4Addr::LOCALHOST, host.port()).into(), hello("guest"))?;
    println!("{} {}", host.live().port(), link.port());

    let mut sink = Vec::new();
    std::io::stdin().read_to_end(&mut sink)?;
    Ok(())
}
