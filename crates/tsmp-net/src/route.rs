//! How a guest reaches a host: straight across the local network, or through
//! a session server with an invite. Everything after the first byte is the
//! same either way.

use std::fmt;
use std::net::{IpAddr, SocketAddr, TcpStream};
use std::str::FromStr;

use crate::protocol::DEFAULT_PORT;
use crate::rendezvous::{self, Invite};
use crate::{CONNECT_TIMEOUT, Error, Result, keep_alive};

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum Route {
    /// The host's launcher listens at this address.
    Direct(SocketAddr),
    /// The host is listed on a session server.
    Invite(Invite),
}

impl Route {
    /// A connection that carries the launcher protocol to the host.
    pub fn open(&self) -> Result<TcpStream> {
        let stream = match self {
            Self::Direct(address) => TcpStream::connect_timeout(address, CONNECT_TIMEOUT)?,
            Self::Invite(invite) => rendezvous::join(invite)?,
        };
        let _ = stream.set_nodelay(true);
        keep_alive(&stream);
        Ok(stream)
    }
}

impl From<SocketAddr> for Route {
    fn from(address: SocketAddr) -> Self {
        Self::Direct(address)
    }
}

impl From<Invite> for Route {
    fn from(invite: Invite) -> Self {
        Self::Invite(invite)
    }
}

impl fmt::Display for Route {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Direct(address) => address.fmt(f),
            Self::Invite(invite) => invite.fmt(f),
        }
    }
}

impl FromStr for Route {
    type Err = Error;

    /// What a player types or pastes: an invite (`ABCD-EFGH@server`), or an
    /// address on the local network with or without the port.
    fn from_str(text: &str) -> Result<Self> {
        let text = text.trim();
        if text.contains('@') {
            return Ok(Self::Invite(text.parse()?));
        }
        text.parse()
            .ok()
            .or_else(|| Some(SocketAddr::new(text.parse::<IpAddr>().ok()?, DEFAULT_PORT)))
            .map(Self::Direct)
            .ok_or_else(|| Error::Protocol(format!("{text:?} is neither an address nor an invite")))
    }
}

#[cfg(test)]
mod tests {
    use std::net::Ipv4Addr;

    use super::*;

    #[test]
    fn what_a_player_types_becomes_a_route() {
        let lan = |port| Route::Direct(SocketAddr::from((Ipv4Addr::new(192, 168, 0, 10), port)));
        assert_eq!("192.168.0.10".parse::<Route>().unwrap(), lan(DEFAULT_PORT));
        assert_eq!(" 192.168.0.10:5000 ".parse::<Route>().unwrap(), lan(5000));
        let Route::Invite(invite) = "abcd-efgh@play.example.org".parse::<Route>().unwrap() else {
            panic!("an invite was read as an address");
        };
        assert_eq!(invite.to_string(), "ABCD-EFGH@play.example.org");
        assert_eq!(lan(5000).to_string(), "192.168.0.10:5000");
        for bad in ["", "192.168.0", "host", "ABCD-EFGH", "nope@play.example.org"] {
            assert!(bad.parse::<Route>().is_err(), "{bad:?}");
        }
    }
}
