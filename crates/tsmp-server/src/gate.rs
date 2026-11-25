//! Who is let in: a limit on connections, in all and from one address, and a
//! record of every open one so that stopping the server can end them all.

use std::collections::HashMap;
use std::net::{IpAddr, Shutdown, SocketAddr, TcpStream};
use std::sync::{Arc, Mutex};

#[derive(Default)]
struct Inside {
    streams: HashMap<u64, TcpStream>,
    per_address: HashMap<IpAddr, usize>,
    next_id: u64,
    closed: bool,
}

pub(crate) struct Gate {
    max_total: usize,
    max_per_address: usize,
    inside: Mutex<Inside>,
}

/// A connection the gate let in. The gate counts it until this is dropped.
pub(crate) struct Peer {
    pub stream: TcpStream,
    pub address: SocketAddr,
    gate: Arc<Gate>,
    id: u64,
}

/// Why a connection was turned away.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Turned {
    Full,
    TooManyFromAddress,
    Closed,
}

impl Gate {
    pub fn new(max_total: usize, max_per_address: usize) -> Arc<Self> {
        Arc::new(Self {
            max_total,
            max_per_address,
            inside: Mutex::new(Inside::default()),
        })
    }

    pub fn admit(self: &Arc<Self>, stream: TcpStream, address: SocketAddr) -> Result<Peer, (TcpStream, Turned)> {
        let address = SocketAddr::new(address.ip().to_canonical(), address.port());
        let Ok(handle) = stream.try_clone() else {
            return Err((stream, Turned::Closed));
        };
        let mut inside = self.inside.lock().unwrap();
        let from_address = inside.per_address.get(&address.ip()).copied().unwrap_or(0);
        let turned = if inside.closed {
            Some(Turned::Closed)
        } else if inside.streams.len() >= self.max_total {
            Some(Turned::Full)
        } else if from_address >= self.max_per_address {
            Some(Turned::TooManyFromAddress)
        } else {
            None
        };
        if let Some(turned) = turned {
            return Err((stream, turned));
        }
        let id = inside.next_id;
        inside.next_id += 1;
        inside.streams.insert(id, handle);
        *inside.per_address.entry(address.ip()).or_default() += 1;
        Ok(Peer {
            stream,
            address,
            gate: self.clone(),
            id,
        })
    }

    /// How many connections are open.
    pub fn count(&self) -> usize {
        self.inside.lock().unwrap().streams.len()
    }

    /// End every connection and let no more in.
    pub fn close(&self) {
        let mut inside = self.inside.lock().unwrap();
        inside.closed = true;
        for stream in inside.streams.values() {
            let _ = stream.shutdown(Shutdown::Both);
        }
    }

    fn leave(&self, id: u64, address: IpAddr) {
        let mut inside = self.inside.lock().unwrap();
        inside.streams.remove(&id);
        if let Some(count) = inside.per_address.get_mut(&address) {
            *count -= 1;
            if *count == 0 {
                inside.per_address.remove(&address);
            }
        }
    }
}

impl Peer {
    /// The address without the port, the way limits count it.
    pub fn ip(&self) -> IpAddr {
        self.address.ip()
    }
}

impl Drop for Peer {
    fn drop(&mut self) {
        self.gate.leave(self.id, self.address.ip());
    }
}

#[cfg(test)]
mod tests {
    use std::net::{Ipv4Addr, TcpListener};

    use super::*;

    fn connection() -> (TcpStream, SocketAddr) {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let _client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        listener.accept().unwrap()
    }

    #[test]
    fn limits_count_open_connections_only() {
        let gate = Gate::new(3, 2);
        let (stream, address) = connection();
        let first = gate.admit(stream, address).ok().unwrap();
        let (stream, address) = connection();
        let second = gate.admit(stream, address).ok().unwrap();
        let (stream, address) = connection();
        assert!(matches!(
            gate.admit(stream, address),
            Err((_, Turned::TooManyFromAddress))
        ));
        assert_eq!(gate.count(), 2);

        drop(first);
        let (stream, address) = connection();
        let third = gate.admit(stream, address).ok().unwrap();
        assert_eq!(gate.count(), 2);

        let other = SocketAddr::from((Ipv4Addr::new(127, 0, 0, 2), 1));
        let (stream, _) = connection();
        let _fourth = gate.admit(stream, other).ok().unwrap();
        let (stream, _) = connection();
        let elsewhere = SocketAddr::from((Ipv4Addr::new(127, 0, 0, 3), 1));
        assert!(matches!(gate.admit(stream, elsewhere), Err((_, Turned::Full))));
        drop((second, third));
        assert_eq!(gate.count(), 1);
    }

    #[test]
    fn a_closed_gate_ends_connections_and_takes_no_more() {
        let gate = Gate::new(10, 10);
        let (stream, address) = connection();
        let peer = gate.admit(stream, address).ok().unwrap();
        gate.close();
        let mut buffer = [0u8; 1];
        assert_eq!(std::io::Read::read(&mut &peer.stream, &mut buffer).unwrap_or(0), 0);
        let (stream, address) = connection();
        assert!(matches!(gate.admit(stream, address), Err((_, Turned::Closed))));
    }

    #[test]
    fn mapped_addresses_count_as_the_address_they_map() {
        let gate = Gate::new(10, 1);
        let mapped: SocketAddr = "[::ffff:127.0.0.1]:5".parse().unwrap();
        let (stream, _) = connection();
        let peer = gate.admit(stream, mapped).ok().unwrap();
        assert_eq!(peer.ip(), IpAddr::from(Ipv4Addr::LOCALHOST));
        let (stream, address) = connection();
        assert!(matches!(
            gate.admit(stream, address),
            Err((_, Turned::TooManyFromAddress))
        ));
    }
}
