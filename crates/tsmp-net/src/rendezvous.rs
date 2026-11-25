//! Meeting through a session server, for players who are not on one local
//! network.
//!
//! Neither player's computer can usually be reached from the internet, but
//! both can reach a server. The host keeps one connection to it open, the
//! *control line*, and the server gives its session a code. A guest that
//! knows the code asks the server for the session; the server tells the
//! host, the host opens a fresh connection for that guest, and from then on
//! the server copies bytes between the two. What passes through is the
//! ordinary launcher protocol ([`crate::protocol`]), so a guest reached this
//! way is served exactly like one on the local network, and a live session
//! runs over the same kind of joined connection.
//!
//! ```text
//! host  -> server: Open                    server -> host:  Opened {code, token, ping_seconds}
//! guest -> server: Join {code}             server -> host:  Incoming {tunnel, from}
//! host  -> server: Accept {token, tunnel}  (a new connection)
//! server -> host:  Accepted                server -> guest: Joined
//!                  ... the two connections are joined from here on ...
//! server -> host:  Ping                    host -> server:  Pong
//! host  -> server: Close
//! ```
//!
//! Every message is framed like the launcher protocol
//! ([`crate::protocol::write_message`]). A host whose control line drops
//! opens again with `resume` and keeps its code, so an invite stays good
//! across a short loss of the network.

use std::fmt;
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};

use crate::protocol::{expect_message_up_to, read_message_up_to, write_message};
use crate::{CONNECT_TIMEOUT, Error, Result, keep_alive, sleep_unless};

/// Bumped on any incompatible change to [`Request`] or [`Reply`].
pub const SERVER_PROTOCOL: u32 = 1;
pub const DEFAULT_SERVER_PORT: u16 = 47810;

/// How long the server's answer to a request may take. A join waits for the
/// host to answer, which the server allows a few seconds of its own.
const REPLY_TIMEOUT: Duration = Duration::from_secs(20);
/// A lost control line is opened again this often.
const REDIAL: Duration = Duration::from_secs(2);
/// Pings missed in a row before the control line counts as lost.
const MISSED_PINGS: u32 = 3;

/// The longest message either side accepts. Every one is a few hundred
/// bytes at most, so a stranger cannot make the server hold a large one.
const LONGEST_MESSAGE: usize = 4 << 10;

/// Crockford's base 32: no I, L, O or U, so a code read aloud or copied by
/// hand survives.
const ALPHABET: &[u8; 32] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const CODE_LENGTH: usize = 8;

/// The name of a session on a server: eight symbols, shown as `ABCD-EFGH`.
/// Forty random bits; the server limits how often an address may guess.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct SessionCode(String);

impl SessionCode {
    pub fn random() -> Self {
        let mut bytes = [0u8; CODE_LENGTH];
        getrandom::fill(&mut bytes).expect("the system has a source of randomness");
        Self(
            bytes
                .iter()
                .map(|byte| ALPHABET[usize::from(byte % 32)] as char)
                .collect(),
        )
    }
}

impl fmt::Display for SessionCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let (first, second) = self.0.split_at(CODE_LENGTH / 2);
        write!(f, "{first}-{second}")
    }
}

impl FromStr for SessionCode {
    type Err = Error;

    /// Accepts any case, dashes and spaces anywhere, and the letters that
    /// look like the digits they stand for.
    fn from_str(text: &str) -> Result<Self> {
        let mut code = String::with_capacity(CODE_LENGTH);
        for c in text.chars().filter(|c| *c != '-' && !c.is_whitespace()) {
            let c = match c.to_ascii_uppercase() {
                'O' => '0',
                'I' | 'L' => '1',
                c if c.is_ascii() && ALPHABET.contains(&(c as u8)) => c,
                _ => return Err(Error::Protocol(format!("{text:?} is not a session code"))),
            };
            code.push(c);
        }
        if code.len() != CODE_LENGTH {
            return Err(Error::Protocol(format!("{text:?} is not a session code")));
        }
        Ok(Self(code))
    }
}

impl TryFrom<String> for SessionCode {
    type Error = Error;

    fn try_from(text: String) -> Result<Self> {
        text.parse()
    }
}

impl From<SessionCode> for String {
    fn from(code: SessionCode) -> Self {
        code.to_string()
    }
}

/// Where a session server is: a name or an address, and a port.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ServerAddress {
    host: String,
    port: u16,
}

impl ServerAddress {
    pub fn new(host: impl Into<String>, port: u16) -> Self {
        Self {
            host: host.into(),
            port,
        }
    }

    pub fn host(&self) -> &str {
        &self.host
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    /// Connect to the server, trying each address its name resolves to.
    pub fn connect(&self) -> Result<TcpStream> {
        let mut last = None;
        for address in (self.host.as_str(), self.port).to_socket_addrs()? {
            match TcpStream::connect_timeout(&address, CONNECT_TIMEOUT) {
                Ok(stream) => {
                    let _ = stream.set_nodelay(true);
                    keep_alive(&stream);
                    return Ok(stream);
                }
                Err(e) => last = Some(e),
            }
        }
        Err(last.map_or_else(|| Error::Protocol(format!("{self} has no address")), Error::from))
    }
}

impl fmt::Display for ServerAddress {
    /// The usual port is left out, to keep invites short.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let host = match self.host.contains(':') {
            true => format!("[{}]", self.host),
            false => self.host.clone(),
        };
        match self.port {
            DEFAULT_SERVER_PORT => f.write_str(&host),
            port => write!(f, "{host}:{port}"),
        }
    }
}

impl FromStr for ServerAddress {
    type Err = Error;

    /// `name`, `name:port`, `1.2.3.4[:port]`, `[::1][:port]` or a bare IPv6
    /// address.
    fn from_str(text: &str) -> Result<Self> {
        let text = text.trim();
        let refused = || Error::Protocol(format!("{text:?} is not a server address"));
        let port = |digits: &str| digits.parse::<u16>().ok().filter(|port| *port != 0).ok_or_else(refused);
        let (host, port) = if let Some(rest) = text.strip_prefix('[') {
            let (host, after) = rest.split_once(']').ok_or_else(refused)?;
            match after {
                "" => (host, DEFAULT_SERVER_PORT),
                _ => (host, port(after.strip_prefix(':').ok_or_else(refused)?)?),
            }
        } else if text.matches(':').count() > 1 {
            (text, DEFAULT_SERVER_PORT)
        } else {
            match text.rsplit_once(':') {
                Some((host, digits)) => (host, port(digits)?),
                None => (text, DEFAULT_SERVER_PORT),
            }
        };
        let fits = |c: char| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | ':');
        if host.is_empty() || host.len() > 253 || !host.chars().all(fits) {
            return Err(refused());
        }
        Ok(Self::new(host.to_ascii_lowercase(), port))
    }
}

/// What a guest needs to reach a host through a server: `ABCD-EFGH@server`.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Invite {
    pub code: SessionCode,
    pub server: ServerAddress,
}

impl fmt::Display for Invite {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}@{}", self.code, self.server)
    }
}

impl FromStr for Invite {
    type Err = Error;

    fn from_str(text: &str) -> Result<Self> {
        let (code, server) = text
            .trim()
            .split_once('@')
            .ok_or_else(|| Error::Protocol(format!("{text:?} is not an invite")))?;
        Ok(Self {
            code: code.parse()?,
            server: server.parse()?,
        })
    }
}

/// What a host sends to keep its code when it opens again.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Resume {
    pub code: SessionCode,
    pub token: String,
}

/// From a launcher to the server. The first message of a connection says
/// what the connection is.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Request {
    /// Host: list a session. This connection becomes its control line.
    Open {
        protocol: u32,
        /// Shown in the server's log.
        name: String,
        game_version: Option<String>,
        #[serde(default)]
        resume: Option<Resume>,
    },
    /// Host: this connection is for the guest waiting on `tunnel`.
    Accept { protocol: u32, token: String, tunnel: u64 },
    /// Guest: reach the host of the session `code`.
    Join { protocol: u32, code: SessionCode },
    /// Host, on the control line: the answer to `Ping`.
    Pong,
    /// Host, on the control line: the session is over.
    Close,
}

/// From the server to a launcher.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Reply {
    /// The session is listed under `code`. `token` proves the host's other
    /// connections and a later `resume` are the host's.
    Opened {
        code: SessionCode,
        token: String,
        ping_seconds: u64,
    },
    /// On the control line: a guest at `from` waits on `tunnel`.
    Incoming {
        tunnel: u64,
        from: SocketAddr,
    },
    /// To the guest: the host is on the other end from now on.
    Joined,
    /// To the host's new connection: the guest is on the other end from now on.
    Accepted,
    Ping,
    Error {
        message: String,
    },
}

/// Send one message of this conversation.
pub fn send<T: Serialize>(stream: &TcpStream, message: &T) -> Result<()> {
    write_message(&mut &*stream, message)
}

/// Read one message of this conversation and nothing past it; `Ok(None)`
/// when the connection ended between messages.
pub fn receive<T: DeserializeOwned>(stream: &TcpStream) -> Result<Option<T>> {
    read_message_up_to(&mut &*stream, LONGEST_MESSAGE)
}

/// [`receive`], where the connection ending is an error.
pub fn expect<T: DeserializeOwned>(stream: &TcpStream) -> Result<T> {
    expect_message_up_to(&mut &*stream, LONGEST_MESSAGE)
}

/// Reply with an error that is not part of the conversation.
fn unexpected(reply: &Reply) -> Error {
    Error::Protocol(format!("the session server answered out of turn: {reply:?}"))
}

/// Send `request` on a new connection to `server` and wait for the reply,
/// which must be `wanted`. Afterwards the connection belongs to the caller,
/// past the reply and nothing more.
fn ask(server: &ServerAddress, request: &Request, wanted: Reply) -> Result<TcpStream> {
    let stream = server.connect()?;
    stream.set_read_timeout(Some(REPLY_TIMEOUT))?;
    send(&stream, request)?;
    // Unbuffered on purpose: nothing past the reply may be consumed here.
    match expect(&stream)? {
        reply if reply == wanted => {}
        Reply::Error { message } => return Err(Error::Remote(message)),
        other => return Err(unexpected(&other)),
    }
    stream.set_read_timeout(None)?;
    Ok(stream)
}

/// Reach the host of `invite`. The stream carries the launcher protocol to
/// that host from its first byte.
pub fn join(invite: &Invite) -> Result<TcpStream> {
    let request = Request::Join {
        protocol: SERVER_PROTOCOL,
        code: invite.code.clone(),
    };
    ask(&invite.server, &request, Reply::Joined)
}

/// How a listed session stands with its server.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ListingStatus {
    /// Reaching the server for the first time.
    Connecting,
    /// Guests can join with the invite.
    Open(Invite),
    /// The server cannot be reached or said no; trying again. A known invite
    /// is kept if the server is back soon enough.
    Retrying { invite: Option<Invite>, reason: String },
}

impl ListingStatus {
    pub fn invite(&self) -> Option<&Invite> {
        match self {
            Self::Open(invite)
            | Self::Retrying {
                invite: Some(invite), ..
            } => Some(invite),
            _ => None,
        }
    }
}

/// Receives each guest that reaches the host through the server, with the
/// guest's address as the server saw it.
pub type Doorbell = dyn Fn(TcpStream, SocketAddr) + Send + Sync;

struct ListingShared {
    server: ServerAddress,
    name: String,
    game_version: Option<String>,
    doorbell: Box<Doorbell>,
    status: Mutex<ListingStatus>,
    stop: AtomicBool,
    /// The control line while one is open, for answering pings and closing.
    line: Mutex<Option<TcpStream>>,
}

/// A session listed on a server for as long as this exists. It keeps the
/// control line open, opening it again whenever it is lost, and hands every
/// guest that arrives to its doorbell.
///
/// Dropping it never waits: the line is closed at once and the worker that
/// kept it ends on its own.
pub struct Listing {
    shared: Arc<ListingShared>,
}

impl Listing {
    pub fn start(
        server: ServerAddress,
        name: String,
        game_version: Option<String>,
        doorbell: impl Fn(TcpStream, SocketAddr) + Send + Sync + 'static,
    ) -> Self {
        let shared = Arc::new(ListingShared {
            server,
            name,
            game_version,
            doorbell: Box::new(doorbell),
            status: Mutex::new(ListingStatus::Connecting),
            stop: AtomicBool::new(false),
            line: Mutex::new(None),
        });
        thread::spawn({
            let shared = shared.clone();
            move || shared.run()
        });
        Self { shared }
    }

    pub fn server(&self) -> &ServerAddress {
        &self.shared.server
    }

    pub fn status(&self) -> ListingStatus {
        self.shared.status.lock().unwrap().clone()
    }
}

impl Drop for Listing {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Relaxed);
        if let Some(line) = self.shared.line.lock().unwrap().take() {
            // Lets the server free the code at once; it would anyway, later.
            let _ = line.set_write_timeout(Some(CONNECT_TIMEOUT));
            let _ = send(&line, &Request::Close);
            let _ = line.shutdown(std::net::Shutdown::Both);
        }
    }
}

impl ListingShared {
    fn stopped(&self) -> bool {
        self.stop.load(Ordering::Relaxed)
    }

    fn set_status(&self, status: ListingStatus) {
        *self.status.lock().unwrap() = status;
    }

    fn run(self: Arc<Self>) {
        let mut resume = None;
        while !self.stopped() {
            if let Err(e) = self.serve(&mut resume) {
                if self.stopped() {
                    break;
                }
                let invite = resume.as_ref().map(|resume: &Resume| self.invite(&resume.code));
                self.set_status(ListingStatus::Retrying {
                    invite,
                    reason: e.to_string(),
                });
            }
            self.line.lock().unwrap().take();
            sleep_unless(REDIAL, &self.stop);
        }
    }

    fn invite(&self, code: &SessionCode) -> Invite {
        Invite {
            code: code.clone(),
            server: self.server.clone(),
        }
    }

    /// One control line, from opening it until it is lost. Returns `Ok`
    /// only when the listing was dropped meanwhile.
    fn serve(self: &Arc<Self>, resume: &mut Option<Resume>) -> Result<()> {
        let stream = self.server.connect()?;
        {
            let mut line = self.line.lock().unwrap();
            if self.stopped() {
                return Ok(());
            }
            *line = Some(stream.try_clone()?);
        }
        stream.set_read_timeout(Some(REPLY_TIMEOUT))?;
        let open = Request::Open {
            protocol: SERVER_PROTOCOL,
            name: self.name.clone(),
            game_version: self.game_version.clone(),
            resume: resume.clone(),
        };
        send(&stream, &open)?;
        let (code, token, ping_seconds) = match expect(&stream)? {
            Reply::Opened {
                code,
                token,
                ping_seconds,
            } => (code, token, ping_seconds),
            Reply::Error { message } => return Err(Error::Remote(message)),
            other => return Err(unexpected(&other)),
        };
        let quiet = Duration::from_secs(ping_seconds.max(1)) * MISSED_PINGS;
        stream.set_read_timeout(Some(quiet))?;
        *resume = Some(Resume {
            code: code.clone(),
            token: token.clone(),
        });
        self.set_status(ListingStatus::Open(self.invite(&code)));

        loop {
            match receive(&stream)? {
                None => return Err(Error::Protocol("the session server closed the connection".into())),
                Some(Reply::Ping) => match &*self.line.lock().unwrap() {
                    Some(line) => send(line, &Request::Pong)?,
                    None => return Ok(()),
                },
                Some(Reply::Incoming { tunnel, from }) => self.answer(&token, tunnel, from),
                Some(Reply::Error { message }) => return Err(Error::Remote(message)),
                Some(other) => return Err(unexpected(&other)),
            }
        }
    }

    /// Open the connection a waiting guest is joined to, off the control
    /// line's thread so that it keeps answering.
    fn answer(self: &Arc<Self>, token: &str, tunnel: u64, from: SocketAddr) {
        let shared = self.clone();
        let accept = Request::Accept {
            protocol: SERVER_PROTOCOL,
            token: token.to_string(),
            tunnel,
        };
        thread::spawn(move || {
            // A guest that gave up meanwhile is no loss; it tries again.
            if let Ok(stream) = ask(&shared.server, &accept, Reply::Accepted)
                && !shared.stopped()
            {
                (shared.doorbell)(stream, from);
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_are_read_the_way_people_type_them() {
        let code: SessionCode = "abcd-efgh".parse().unwrap();
        assert_eq!(code.to_string(), "ABCD-EFGH");
        assert_eq!(" AB CD EF GH ".parse::<SessionCode>().unwrap(), code);
        assert_eq!("o1l0-IIII".parse::<SessionCode>().unwrap().to_string(), "0110-1111");
        for bad in ["", "ABCD-EFG", "ABCD-EFGHJ", "ABCD-EFGU", "ABCD-ÉFGH", "ABCD_EFGH"] {
            assert!(bad.parse::<SessionCode>().is_err(), "{bad:?}");
        }
    }

    #[test]
    fn random_codes_differ_and_read_back() {
        let codes: std::collections::HashSet<SessionCode> = (0..1000).map(|_| SessionCode::random()).collect();
        assert_eq!(codes.len(), 1000);
        for code in codes {
            assert_eq!(code.to_string().parse::<SessionCode>().unwrap(), code);
        }
    }

    #[test]
    fn server_addresses_take_the_usual_forms() {
        let parse = |text: &str| {
            text.parse::<ServerAddress>()
                .ok()
                .map(|a| (a.host().to_string(), a.port()))
        };
        let usual = DEFAULT_SERVER_PORT;
        assert_eq!(parse("Play.Example.org"), Some(("play.example.org".into(), usual)));
        assert_eq!(
            parse(" play.example.org:9000 "),
            Some(("play.example.org".into(), 9000))
        );
        assert_eq!(parse("203.0.113.5"), Some(("203.0.113.5".into(), usual)));
        assert_eq!(parse("203.0.113.5:1"), Some(("203.0.113.5".into(), 1)));
        assert_eq!(parse("[2001:db8::1]:9000"), Some(("2001:db8::1".into(), 9000)));
        assert_eq!(parse("[2001:db8::1]"), Some(("2001:db8::1".into(), usual)));
        assert_eq!(parse("2001:db8::1"), Some(("2001:db8::1".into(), usual)));
        for bad in [
            "",
            ":9000",
            "host:",
            "host:0",
            "host:65536",
            "host:x",
            "a b",
            "a/b",
            "[::1",
            "[::1]x",
            "user@host",
        ] {
            assert_eq!(parse(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn invites_round_trip_and_leave_out_the_usual_port() {
        let invite: Invite = "abcd-efgh@play.example.org".parse().unwrap();
        assert_eq!(invite.to_string(), "ABCD-EFGH@play.example.org");
        assert_eq!(invite.server.port(), DEFAULT_SERVER_PORT);
        let elsewhere: Invite = "ABCDEFGH@[::1]:9000".parse().unwrap();
        assert_eq!(elsewhere.to_string(), "ABCD-EFGH@[::1]:9000");
        assert_eq!(elsewhere.to_string().parse::<Invite>().unwrap(), elsewhere);
        assert!("ABCD-EFGH".parse::<Invite>().is_err());
        assert!("ABCD-EFGH@".parse::<Invite>().is_err());
    }

    #[test]
    fn messages_survive_a_round_trip() {
        let code = SessionCode::random();
        let requests = [
            Request::Open {
                protocol: SERVER_PROTOCOL,
                name: "Хост".into(),
                game_version: Some("1.129.39.1030".into()),
                resume: Some(Resume {
                    code: code.clone(),
                    token: "t".into(),
                }),
            },
            Request::Accept {
                protocol: SERVER_PROTOCOL,
                token: "t".into(),
                tunnel: 7,
            },
            Request::Join {
                protocol: SERVER_PROTOCOL,
                code: code.clone(),
            },
            Request::Pong,
            Request::Close,
        ];
        let replies = [
            Reply::Opened {
                code,
                token: "t".into(),
                ping_seconds: 15,
            },
            Reply::Incoming {
                tunnel: 7,
                from: "203.0.113.5:4000".parse().unwrap(),
            },
            Reply::Joined,
            Reply::Accepted,
            Reply::Ping,
            Reply::Error { message: "no".into() },
        ];
        let mut buffer = Vec::new();
        for request in &requests {
            write_message(&mut buffer, request).unwrap();
        }
        for reply in &replies {
            write_message(&mut buffer, reply).unwrap();
        }
        let mut reader = buffer.as_slice();
        for request in requests {
            assert_eq!(expect_message_up_to::<Request>(&mut reader, LONGEST_MESSAGE).unwrap(), request);
        }
        for reply in replies {
            assert_eq!(expect_message_up_to::<Reply>(&mut reader, LONGEST_MESSAGE).unwrap(), reply);
        }
    }

    #[test]
    fn a_code_on_the_wire_is_checked() {
        let body = br#"{"type":"join","protocol":1,"code":"ABCD-EFGU"}"#;
        let mut buffer = (body.len() as u32).to_le_bytes().to_vec();
        buffer.extend_from_slice(body);
        assert!(expect_message_up_to::<Request>(&mut buffer.as_slice(), LONGEST_MESSAGE).is_err());
    }
}
