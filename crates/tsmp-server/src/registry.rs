//! The sessions a server lists: under which code, whose control line each
//! has, and which guests wait at its door.
//!
//! Nothing here touches the network beyond holding connections; the caller
//! writes to them after letting go of the registry, so that a slow host never
//! holds up the others.

use std::collections::HashMap;
use std::fmt;
use std::net::{IpAddr, Shutdown, TcpStream};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tsmp_net::protocol::write_message;
use tsmp_net::rendezvous::{Reply, Resume, SessionCode};

use crate::gate::Peer;

/// A host's control line. The server writes to it from several threads, one
/// whole message at a time.
#[derive(Clone)]
pub(crate) struct Line {
    id: u64,
    writer: Arc<Mutex<TcpStream>>,
    /// A second handle, so that closing never waits for a write in progress.
    closer: Arc<TcpStream>,
}

impl Line {
    pub fn new(id: u64, stream: &TcpStream) -> std::io::Result<Self> {
        Ok(Self {
            id,
            writer: Arc::new(Mutex::new(stream.try_clone()?)),
            closer: Arc::new(stream.try_clone()?),
        })
    }

    pub fn send(&self, reply: &Reply) -> tsmp_net::Result<()> {
        let writer = self.writer.lock().unwrap();
        write_message(&mut &*writer, reply)
    }

    pub fn close(&self) {
        let _ = self.closer.shutdown(Shutdown::Both);
    }
}

/// Why a request about a session is refused. The text goes to the launcher,
/// which shows it to the player.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Refusal {
    Unknown(SessionCode),
    HostAway(SessionCode),
    Crowded(SessionCode),
    Full,
    TooManyFromAddress,
}

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unknown(code) => write!(f, "No session has the code {code}. Check the invite with the host."),
            Self::HostAway(code) => write!(
                f,
                "The host of {code} has lost the session server for the moment. Try again in a few seconds."
            ),
            Self::Crowded(code) => write!(f, "Too many guests are knocking at {code} at once. Try again."),
            Self::Full => f.write_str("The session server is full. Try again later."),
            Self::TooManyFromAddress => f.write_str("This address has opened too many sessions on the server."),
        }
    }
}

/// A host whose control line is in place.
pub(crate) struct Opened {
    pub code: SessionCode,
    pub token: String,
    pub resumed: bool,
    /// The line this one replaced, for the caller to close.
    pub replaced: Option<Line>,
}

/// A guest at the door of a session: the host has to be told, and the
/// host's connection will arrive on `doorstep`.
pub(crate) struct Knock {
    pub tunnel: u64,
    pub line: Line,
    pub doorstep: Receiver<Peer>,
}

struct Session {
    token: String,
    owner: IpAddr,
    line: Option<Line>,
    /// Since when the session has had no control line.
    detached_since: Option<Instant>,
    /// Guests waiting for the host, by tunnel.
    waiting: HashMap<u64, Sender<Peer>>,
}

pub(crate) struct Limits {
    pub sessions: usize,
    pub sessions_per_address: usize,
    pub waiting_per_session: usize,
}

/// How long the code of a session whose host stayed away too long is kept
/// for that host. An invite outlives a long outage of the host's network:
/// the host gets its code back when it is back, and nobody else is given it.
pub(crate) const FORGET_AFTER: Duration = Duration::from_secs(30 * 60);

/// A session whose host stayed away too long: its code, kept for its host.
struct Lapsed {
    token: String,
    since: Instant,
}

pub(crate) struct Registry {
    limits: Limits,
    sessions: HashMap<SessionCode, Session>,
    tokens: HashMap<String, SessionCode>,
    lapsed: HashMap<SessionCode, Lapsed>,
    next_tunnel: u64,
}

impl Registry {
    pub fn new(limits: Limits) -> Self {
        Self {
            limits,
            sessions: HashMap::new(),
            tokens: HashMap::new(),
            lapsed: HashMap::new(),
            next_tunnel: 1,
        }
    }

    /// Give a host's control line a session: the one it resumes, if that is
    /// still here, or still kept for it, and the token is right, or a new
    /// one.
    pub fn open(&mut self, line: Line, owner: IpAddr, resume: Option<&Resume>) -> Result<Opened, Refusal> {
        let resumable = resume.filter(|resume| {
            self.sessions
                .get(&resume.code)
                .is_some_and(|session| session.token == resume.token)
        });
        if let Some(resume) = resumable {
            let session = self.sessions.get_mut(&resume.code).expect("checked above");
            let replaced = session.line.replace(line);
            session.detached_since = None;
            // They were announced on the line that is gone; they try again.
            session.waiting.clear();
            return Ok(Opened {
                code: resume.code.clone(),
                token: session.token.clone(),
                resumed: true,
                replaced,
            });
        }
        if self.sessions.len() >= self.limits.sessions {
            return Err(Refusal::Full);
        }
        let owned = self.sessions.values().filter(|session| session.owner == owner).count();
        if owned >= self.limits.sessions_per_address {
            return Err(Refusal::TooManyFromAddress);
        }
        let kept = resume.filter(|resume| {
            self.lapsed
                .get(&resume.code)
                .is_some_and(|lapsed| lapsed.token == resume.token)
        });
        if let Some(resume) = kept {
            self.lapsed.remove(&resume.code);
            self.insert(resume.code.clone(), resume.token.clone(), owner, line);
            return Ok(Opened {
                code: resume.code.clone(),
                token: resume.token.clone(),
                resumed: true,
                replaced: None,
            });
        }
        let code = loop {
            let code = SessionCode::random();
            if !self.sessions.contains_key(&code) && !self.lapsed.contains_key(&code) {
                break code;
            }
        };
        let token = new_token();
        self.insert(code.clone(), token.clone(), owner, line);
        Ok(Opened {
            code,
            token,
            resumed: false,
            replaced: None,
        })
    }

    fn insert(&mut self, code: SessionCode, token: String, owner: IpAddr, line: Line) {
        self.tokens.insert(token.clone(), code.clone());
        self.sessions.insert(
            code,
            Session {
                token,
                owner,
                line: Some(line),
                detached_since: None,
                waiting: HashMap::new(),
            },
        );
    }

    /// The control line `line` was lost. The session waits for its host to
    /// resume it; the guests at its door are sent away. Returns whether
    /// `line` was the session's line at all.
    pub fn detach(&mut self, code: &SessionCode, line: u64, now: Instant) -> bool {
        let Some(session) = self.sessions.get_mut(code) else {
            return false;
        };
        if session.line.as_ref().map(|current| current.id) != Some(line) {
            return false;
        }
        session.line = None;
        session.detached_since = Some(now);
        session.waiting.clear();
        true
    }

    /// The host on `line` ended its session.
    pub fn close(&mut self, code: &SessionCode, line: u64) -> bool {
        let current = self
            .sessions
            .get(code)
            .and_then(|session| session.line.as_ref())
            .map(|line| line.id);
        if current != Some(line) {
            return false;
        }
        self.remove(code);
        true
    }

    /// A guest asks for the session `code`.
    pub fn knock(&mut self, code: &SessionCode) -> Result<Knock, Refusal> {
        let Some(session) = self.sessions.get_mut(code) else {
            // Its host may still come back for it.
            if self.lapsed.contains_key(code) {
                return Err(Refusal::HostAway(code.clone()));
            }
            return Err(Refusal::Unknown(code.clone()));
        };
        let Some(line) = session.line.clone() else {
            return Err(Refusal::HostAway(code.clone()));
        };
        if session.waiting.len() >= self.limits.waiting_per_session {
            return Err(Refusal::Crowded(code.clone()));
        }
        let tunnel = self.next_tunnel;
        self.next_tunnel += 1;
        let (sender, doorstep) = mpsc::channel();
        session.waiting.insert(tunnel, sender);
        Ok(Knock { tunnel, line, doorstep })
    }

    /// The guest on `tunnel` stopped waiting.
    pub fn give_up(&mut self, code: &SessionCode, tunnel: u64) {
        if let Some(session) = self.sessions.get_mut(code) {
            session.waiting.remove(&tunnel);
        }
    }

    /// The host of the session `token` names answers the guest on
    /// `tunnel`: where to hand its connection.
    pub fn answer(&mut self, token: &str, tunnel: u64) -> Option<Sender<Peer>> {
        let code = self.tokens.get(token)?;
        self.sessions.get_mut(code)?.waiting.remove(&tunnel)
    }

    /// Close sessions whose host has been away longer than `grace`, keeping
    /// their codes for their hosts for [`FORGET_AFTER`], and forget the codes
    /// kept longer.
    pub fn expire(&mut self, now: Instant, grace: Duration) -> Vec<SessionCode> {
        self.lapsed.retain(|_, lapsed| now.duration_since(lapsed.since) < FORGET_AFTER);
        let expired: Vec<SessionCode> = self
            .sessions
            .iter()
            .filter(|(_, session)| {
                session
                    .detached_since
                    .is_some_and(|since| now.duration_since(since) >= grace)
            })
            .map(|(code, _)| code.clone())
            .collect();
        for code in &expired {
            if let Some(session) = self.sessions.remove(code) {
                self.tokens.remove(&session.token);
                let lapsed = Lapsed {
                    token: session.token,
                    since: now,
                };
                self.lapsed.insert(code.clone(), lapsed);
            }
        }
        expired
    }

    /// Every control line in place.
    pub fn lines(&self) -> Vec<Line> {
        self.sessions
            .values()
            .filter_map(|session| session.line.clone())
            .collect()
    }

    /// How many sessions there are, and how many of them have their host.
    pub fn counts(&self) -> (usize, usize) {
        let hosted = self.sessions.values().filter(|session| session.line.is_some()).count();
        (self.sessions.len(), hosted)
    }

    fn remove(&mut self, code: &SessionCode) {
        if let Some(session) = self.sessions.remove(code) {
            self.tokens.remove(&session.token);
        }
    }
}

/// 128 random bits as hex: what proves a connection comes from a session's
/// host.
fn new_token() -> String {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).expect("the system has a source of randomness");
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use std::net::{Ipv4Addr, TcpListener};

    use super::*;

    fn line(id: u64) -> Line {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let stream = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        Line::new(id, &stream).unwrap()
    }

    fn registry() -> Registry {
        Registry::new(Limits {
            sessions: 3,
            sessions_per_address: 2,
            waiting_per_session: 2,
        })
    }

    const HOME: IpAddr = IpAddr::V4(Ipv4Addr::new(203, 0, 113, 5));

    #[test]
    fn a_host_that_comes_back_keeps_its_code() {
        let mut registry = registry();
        let first = registry.open(line(1), HOME, None).unwrap();
        assert!(!first.resumed);
        let now = Instant::now();
        assert!(registry.detach(&first.code, 1, now));
        assert!(matches!(registry.knock(&first.code), Err(Refusal::HostAway(_))));

        let resume = Resume {
            code: first.code.clone(),
            token: first.token.clone(),
        };
        let again = registry.open(line(2), HOME, Some(&resume)).unwrap();
        assert!(again.resumed && again.replaced.is_none());
        assert_eq!((again.code, again.token), (first.code.clone(), first.token));
        assert!(registry.knock(&first.code).is_ok());
        // The old line going away later changes nothing.
        assert!(!registry.detach(&first.code, 1, now));
        assert_eq!(registry.counts(), (1, 1));
    }

    #[test]
    fn a_wrong_token_or_a_forgotten_code_gets_a_new_session() {
        let mut registry = registry();
        let first = registry.open(line(1), HOME, None).unwrap();
        let wrong = Resume {
            code: first.code.clone(),
            token: "guess".into(),
        };
        let other = registry.open(line(2), HOME, Some(&wrong)).unwrap();
        assert!(!other.resumed);
        assert_ne!(other.code, first.code);

        let now = Instant::now();
        registry.detach(&first.code, 1, now);
        assert!(registry.expire(now, Duration::from_secs(60)).is_empty());
        let expired = registry.expire(now + Duration::from_secs(60), Duration::from_secs(60));
        assert_eq!(expired.len(), 1);
        assert_eq!(expired[0], first.code);
        let late = Resume {
            code: first.code.clone(),
            token: first.token.clone(),
        };
        // Its code is kept for it: a guest hears the host is away, and the
        // host gets the same code back.
        assert!(matches!(registry.knock(&first.code), Err(Refusal::HostAway(_))));
        let back = registry.open(line(3), HOME, Some(&late)).unwrap();
        assert!(back.resumed);
        assert_eq!(back.code, first.code);
        assert!(registry.knock(&first.code).is_ok());

        // Kept no longer than FORGET_AFTER.
        registry.detach(&first.code, 3, now);
        let later = now + Duration::from_secs(60);
        assert_eq!(registry.expire(later, Duration::from_secs(60)).len(), 1);
        assert!(registry.expire(later + FORGET_AFTER, Duration::from_secs(60)).is_empty());
        assert!(matches!(registry.knock(&first.code), Err(Refusal::Unknown(_))));
        assert!(!registry.open(line(4), HOME, Some(&late)).unwrap().resumed);
    }

    #[test]
    fn a_second_line_for_a_live_session_replaces_the_first() {
        let mut registry = registry();
        let first = registry.open(line(1), HOME, None).unwrap();
        let resume = Resume {
            code: first.code.clone(),
            token: first.token,
        };
        let again = registry.open(line(2), HOME, Some(&resume)).unwrap();
        assert_eq!(again.replaced.map(|line| line.id), Some(1));
        assert!(!registry.close(&first.code, 1));
        assert!(registry.close(&first.code, 2));
        assert!(matches!(registry.knock(&first.code), Err(Refusal::Unknown(_))));
    }

    #[test]
    fn guests_wait_until_the_host_answers_or_leaves() {
        let mut registry = registry();
        let opened = registry.open(line(1), HOME, None).unwrap();
        let first = registry.knock(&opened.code).unwrap();
        let second = registry.knock(&opened.code).unwrap();
        assert_ne!(first.tunnel, second.tunnel);
        assert!(matches!(registry.knock(&opened.code), Err(Refusal::Crowded(_))));

        assert!(registry.answer("not the token", first.tunnel).is_none());
        assert!(registry.answer(&opened.token, first.tunnel).is_some());
        assert!(registry.answer(&opened.token, first.tunnel).is_none());
        registry.give_up(&opened.code, second.tunnel);
        assert!(registry.answer(&opened.token, second.tunnel).is_none());

        let third = registry.knock(&opened.code).unwrap();
        registry.detach(&opened.code, 1, Instant::now());
        assert!(
            third.doorstep.recv().is_err(),
            "a guest is sent away when its host is lost"
        );
    }

    #[test]
    fn sessions_are_limited_in_all_and_per_address() {
        let mut registry = registry();
        registry.open(line(1), HOME, None).unwrap();
        registry.open(line(2), HOME, None).unwrap();
        assert_eq!(
            registry.open(line(3), HOME, None).err(),
            Some(Refusal::TooManyFromAddress)
        );
        let elsewhere = IpAddr::V4(Ipv4Addr::new(198, 51, 100, 1));
        registry.open(line(4), elsewhere, None).unwrap();
        assert_eq!(registry.open(line(5), elsewhere, None).err(), Some(Refusal::Full));
        assert_eq!(registry.lines().len(), 3);
    }
}
