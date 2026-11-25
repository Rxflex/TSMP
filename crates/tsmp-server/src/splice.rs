//! Joining two connections: whatever one side sends, the other receives.

use std::io::{ErrorKind, Read, Write};
use std::net::{Shutdown, TcpStream};
use std::thread;

const BUFFER: usize = 1 << 16;

/// Copy both ways until both sides are done. A side that finishes sending
/// is passed on as the end of the stream; a side that fails ends both.
pub(crate) fn splice(a: &TcpStream, b: &TcpStream) {
    thread::scope(|scope| {
        scope.spawn(|| pipe(a, b));
        pipe(b, a);
    });
}

fn pipe(mut from: &TcpStream, mut to: &TcpStream) {
    let mut buffer = vec![0u8; BUFFER];
    loop {
        let read = match from.read(&mut buffer) {
            Ok(0) => {
                let _ = to.shutdown(Shutdown::Write);
                return;
            }
            Ok(read) => read,
            Err(e) if e.kind() == ErrorKind::Interrupted => continue,
            Err(_) => break,
        };
        if to.write_all(&buffer[..read]).is_err() {
            break;
        }
    }
    let _ = from.shutdown(Shutdown::Both);
    let _ = to.shutdown(Shutdown::Both);
}

#[cfg(test)]
mod tests {
    use std::net::{Ipv4Addr, TcpListener};

    use super::*;

    fn pair() -> (TcpStream, TcpStream) {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let near = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (far, _) = listener.accept().unwrap();
        (near, far)
    }

    #[test]
    fn bytes_cross_both_ways_and_an_end_is_passed_on() {
        let (mut alice, alice_server) = pair();
        let (mut bob, bob_server) = pair();
        let joined = thread::spawn(move || splice(&alice_server, &bob_server));

        let big: Vec<u8> = (0..3_000_000u32).map(|i| (i % 251) as u8).collect();
        let sender = thread::spawn({
            let mut alice = alice.try_clone().unwrap();
            let big = big.clone();
            move || {
                alice.write_all(&big).unwrap();
                alice.shutdown(Shutdown::Write).unwrap();
            }
        });
        bob.write_all(b"hello alice").unwrap();
        let mut received = Vec::new();
        bob.read_to_end(&mut received).unwrap();
        assert_eq!(received, big);
        sender.join().unwrap();

        let mut greeting = [0u8; 11];
        alice.read_exact(&mut greeting).unwrap();
        assert_eq!(&greeting, b"hello alice");
        drop(bob);
        let mut rest = Vec::new();
        alice.read_to_end(&mut rest).unwrap();
        assert!(rest.is_empty());
        joined.join().unwrap();
    }
}
