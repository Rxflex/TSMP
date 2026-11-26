//! The launcher-to-launcher protocol.
//!
//! One TCP connection per guest. Every message is a frame: a 4-byte
//! little-endian length followed by that many bytes of JSON. The one exception
//! is `Message::File`, which is followed by exactly `size` raw bytes.
//!
//! The guest drives the conversation:
//!
//! ```text
//! guest: Hello            host: Hello | Error
//! guest: GetSession       host: Session | Error
//! guest: GetFile          host: File + bytes | Error
//! guest: Status           (no reply)
//! ```
//!
//! A second connection carries the live session. It opens with `JoinLive`
//! in place of `Hello`; after the host's `Hello` reply the stream switches to
//! the relay's frame format for good (see [`crate::live`]).
//!
//! A host speaks every version from [`OLDEST_PROTOCOL_VERSION`] up to its own
//! and answers a guest's `Hello` in the guest's version, which is all a guest
//! checks: launchers from before version 3 refuse any other. A guest sends
//! its own version and joins only a host that answers in it. A host that is
//! full answers `Error` in place of `Hello`.

use std::io::{ErrorKind, Read, Write};

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use tsmp_core::manifest::Manifest;
use tsmp_core::packs::PackSet;

use crate::{Error, Result};

/// Bumped on any incompatible change to the messages below.
///
/// 3: a `Hello` names the player, and the host's reply says which peer of
/// its live session the guest is and how many players there are.
pub const PROTOCOL_VERSION: u32 = 3;
/// The oldest version a host still takes guests of. Version 2 is version 3
/// without the fields it added, so a guest of it joins as the session's one
/// guest from before players had names (see [`crate::live`]).
pub const OLDEST_PROTOCOL_VERSION: u32 = 2;
pub const DEFAULT_PORT: u16 = 47800;

/// A manifest of a few thousand mods is well under a megabyte; anything near
/// this limit is not a real message.
const MAX_FRAME: usize = 64 << 20;

/// Whether a host takes a guest that speaks `protocol`.
pub fn speaks(protocol: u32) -> bool {
    (OLDEST_PROTOCOL_VERSION..=PROTOCOL_VERSION).contains(&protocol)
}

/// A new id for a player: made once per install and kept, so that the host
/// knows a guest that comes back from one that is new.
pub fn new_player_id() -> String {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).expect("the system has a source of randomness");
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// The fields added since version 2 are left out of the JSON while they hold
/// nothing, and older launchers ignore them when they do not.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Hello {
    pub protocol: u32,
    /// Shown to the other player; the computer name by default.
    pub name: String,
    pub game_version: Option<String>,
    pub packs: PackSet,
    /// Who is playing, from [`new_player_id`]. A name may change and two
    /// computers may share one; this does not. Launchers before version 3
    /// send none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub player: Option<String>,
    /// In the host's reply: the guest's peer in the live session, the same
    /// every time the same player joins (see [`crate::live::PeerId`]).
    #[serde(default, skip_serializing_if = "is_zero")]
    pub peer: u32,
    /// In the host's reply: the players in the session with this guest,
    /// the host and the guest included.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub players: u32,
    /// In the host's reply: how many players the session takes.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub max_players: u32,
}

fn is_zero(value: &u32) -> bool {
    *value == 0
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SaveInfo {
    /// File name inside the `saves` folder, e.g. `Slot_00000002.save`.
    pub file_name: String,
    pub size: u64,
    pub hash: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Session {
    pub mods: Manifest,
    pub save: Option<SaveInfo>,
    /// Every player loads a save of their own, and the games bring the
    /// households together in the host's. No save is shared then.
    #[serde(default)]
    pub own_families: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Root {
    Mods,
    Save,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum GuestStatus {
    Syncing { done_bytes: u64, total_bytes: u64 },
    Ready,
    Failed { message: String },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Message {
    Hello(Hello),
    /// Opens a live-session connection instead of a sync one.
    JoinLive(Hello),
    GetSession,
    Session(Session),
    /// For `Root::Save` the path is ignored: the host has one shared save.
    GetFile { root: Root, path: String },
    /// Followed by `size` raw bytes.
    File { size: u64 },
    Status(GuestStatus),
    Error { message: String },
}

/// Write one frame. Every conversation of TSMP over TCP uses this framing:
/// between launchers ([`Message`]) and with a session server
/// ([`crate::rendezvous`]).
pub fn write_message<T: Serialize>(writer: &mut impl Write, message: &T) -> Result<()> {
    let body = serde_json::to_vec(message).map_err(|e| Error::Protocol(e.to_string()))?;
    if body.len() > MAX_FRAME {
        return Err(Error::Protocol(format!("message of {} bytes is too large", body.len())));
    }
    writer.write_all(&(body.len() as u32).to_le_bytes())?;
    writer.write_all(&body)?;
    writer.flush()?;
    Ok(())
}

/// `Ok(None)` when the other side closed the connection between messages.
///
/// Reads exactly one frame and nothing past it, so a stream that changes
/// format after a message can be read with it unbuffered.
pub fn read_message<T: DeserializeOwned>(reader: &mut impl Read) -> Result<Option<T>> {
    read_message_up_to(reader, MAX_FRAME)
}

/// Read a message where the connection closing is itself an error.
pub fn expect_message<T: DeserializeOwned>(reader: &mut impl Read) -> Result<T> {
    expect_message_up_to(reader, MAX_FRAME)
}

/// [`read_message`] for a conversation whose messages are never longer than
/// `limit`: a longer one is refused before anything is set aside for it.
pub fn read_message_up_to<T: DeserializeOwned>(reader: &mut impl Read, limit: usize) -> Result<Option<T>> {
    let mut length = [0u8; 4];
    let mut filled = 0;
    while filled < length.len() {
        match reader.read(&mut length[filled..]) {
            Ok(0) if filled == 0 => return Ok(None),
            Ok(0) => return Err(Error::Protocol("connection closed inside a message".into())),
            Ok(read) => filled += read,
            Err(e) if e.kind() == ErrorKind::Interrupted => {}
            Err(e) => return Err(e.into()),
        }
    }
    let length = u32::from_le_bytes(length) as usize;
    if length > limit {
        return Err(Error::Protocol(format!("message of {length} bytes is too large")));
    }
    let mut body = vec![0u8; length];
    reader.read_exact(&mut body)?;
    serde_json::from_slice(&body)
        .map(Some)
        .map_err(|e| Error::Protocol(format!("unreadable message: {e}")))
}

/// [`expect_message`] with a limit, as in [`read_message_up_to`].
pub fn expect_message_up_to<T: DeserializeOwned>(reader: &mut impl Read, limit: usize) -> Result<T> {
    read_message_up_to(reader, limit)?.ok_or_else(|| Error::Protocol("connection closed".into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn round_trip(message: Message) {
        let mut buffer = Vec::new();
        write_message(&mut buffer, &message).unwrap();
        let mut reader = buffer.as_slice();
        assert_eq!(read_message(&mut reader).unwrap(), Some(message));
        assert_eq!(read_message::<Message>(&mut reader).unwrap(), None);
    }

    #[test]
    fn a_session_from_an_older_host_shares_its_save() {
        let session: Session = serde_json::from_str(r#"{"mods":{"files":{}},"save":null}"#).unwrap();
        assert!(!session.own_families);
    }

    #[test]
    fn every_message_survives_a_round_trip() {
        round_trip(Message::Hello(Hello {
            protocol: PROTOCOL_VERSION,
            name: "Хост".into(),
            game_version: Some("1.129.39.1030".into()),
            packs: ["EP01".to_string(), "GP04".to_string()].into(),
            player: Some(new_player_id()),
            peer: 2,
            players: 3,
            max_players: 4,
        }));
        round_trip(Message::JoinLive(Hello {
            protocol: PROTOCOL_VERSION,
            name: "guest".into(),
            game_version: None,
            packs: Default::default(),
            ..Default::default()
        }));
        round_trip(Message::GetSession);
        round_trip(Message::Session(Session {
            mods: Manifest::default(),
            save: Some(SaveInfo {
                file_name: "Slot_00000002.save".into(),
                size: 5,
                hash: "abc".into(),
            }),
            own_families: false,
        }));
        round_trip(Message::Session(Session {
            mods: Manifest::default(),
            save: None,
            own_families: true,
        }));
        round_trip(Message::GetFile {
            root: Root::Mods,
            path: "a/b.package".into(),
        });
        round_trip(Message::File { size: 1 << 40 });
        round_trip(Message::Status(GuestStatus::Syncing {
            done_bytes: 1,
            total_bytes: 2,
        }));
        round_trip(Message::Status(GuestStatus::Ready));
        round_trip(Message::Error { message: "no".into() });
    }

    #[test]
    fn a_hello_reads_the_same_across_versions_2_and_3() {
        // What a launcher of version 2 sends and reads.
        let old = r#"{"type":"hello","protocol":2,"name":"old","game_version":null,"packs":["EP01"]}"#;
        let Message::Hello(hello) = serde_json::from_str(old).unwrap() else {
            panic!("not a hello");
        };
        assert_eq!((hello.protocol, hello.player, hello.peer, hello.players), (2, None, 0, 0));
        assert!(speaks(hello.protocol));
        assert!(!speaks(1) && !speaks(PROTOCOL_VERSION + 1));

        // A hello without the new fields is written as version 2 wrote it.
        let bare = Hello {
            protocol: 2,
            name: "old".into(),
            packs: ["EP01".to_string()].into(),
            ..Default::default()
        };
        assert_eq!(serde_json::to_string(&Message::Hello(bare)).unwrap(), old);

        // Version 2 ignored fields it did not know, so it reads a reply of
        // version 3; and what version 3 adds survives.
        let reply = Hello {
            protocol: 2,
            name: "host".into(),
            player: Some("abc".into()),
            peer: 1,
            players: 2,
            max_players: 2,
            ..Default::default()
        };
        let text = serde_json::to_value(Message::Hello(reply.clone())).unwrap();
        assert_eq!(text["player"], "abc");
        let number = |name: &str| text[name].as_u64();
        assert_eq!((number("peer"), number("players"), number("max_players")), (Some(1), Some(2), Some(2)));
        assert_eq!(serde_json::from_value::<Message>(text).unwrap(), Message::Hello(reply));
    }

    #[test]
    fn player_ids_are_random() {
        let id = new_player_id();
        assert_eq!(id.len(), 32);
        assert_ne!(id, new_player_id());
    }

    #[test]
    fn several_messages_are_read_in_order() {
        let mut buffer = Vec::new();
        write_message(&mut buffer, &Message::GetSession).unwrap();
        write_message(&mut buffer, &Message::File { size: 3 }).unwrap();
        let mut reader = buffer.as_slice();
        assert_eq!(read_message(&mut reader).unwrap(), Some(Message::GetSession));
        assert_eq!(read_message(&mut reader).unwrap(), Some(Message::File { size: 3 }));
    }

    #[test]
    fn truncated_and_oversized_frames_are_errors() {
        let mut buffer = Vec::new();
        write_message(&mut buffer, &Message::GetSession).unwrap();
        buffer.pop();
        assert!(read_message::<Message>(&mut buffer.as_slice()).is_err());
        assert!(read_message::<Message>(&mut [1u8, 0].as_slice()).is_err());
        assert!(read_message::<Message>(&mut u32::MAX.to_le_bytes().as_slice()).is_err());
        assert!(expect_message::<Message>(&mut [].as_slice()).is_err());
    }

    #[test]
    fn a_limit_refuses_longer_messages() {
        let mut buffer = Vec::new();
        write_message(&mut buffer, &Message::GetSession).unwrap();
        let length = buffer.len() - 4;
        assert!(read_message_up_to::<Message>(&mut buffer.as_slice(), length - 1).is_err());
        assert_eq!(read_message_up_to(&mut buffer.as_slice(), length).unwrap(), Some(Message::GetSession));
    }

    #[test]
    fn garbage_json_is_a_protocol_error() {
        let body = b"{\"type\":\"nope\"}";
        let mut buffer = (body.len() as u32).to_le_bytes().to_vec();
        buffer.extend_from_slice(body);
        assert!(matches!(read_message::<Message>(&mut buffer.as_slice()), Err(Error::Protocol(_))));
    }
}
