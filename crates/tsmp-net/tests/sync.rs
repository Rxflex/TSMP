//! Host and guest talking over loopback, against real folders.

use std::fs;
use std::io::Write;
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::path::Path;
use std::time::Duration;

use tsmp_core::manifest::Manifest;
use tsmp_net::Error;
use tsmp_net::guest::{Connection, sync_mods, sync_save};
use tsmp_net::host::{Host, HostConfig, HostEvent};
use tsmp_net::protocol::{GuestStatus, Hello, Message, PROTOCOL_VERSION, read_message, write_message};

fn write(root: &Path, relative: &str, content: &str) {
    let path = root.join(relative);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, content).unwrap();
}

fn read(root: &Path, relative: &str) -> String {
    fs::read_to_string(root.join(relative)).unwrap()
}

fn hello(name: &str, packs: &[&str]) -> Hello {
    Hello {
        protocol: PROTOCOL_VERSION,
        name: name.into(),
        game_version: Some("1.0".into()),
        packs: packs.iter().map(|p| p.to_string()).collect(),
    }
}

fn start_host(mods_dir: &Path, save: Option<&Path>) -> (Host, SocketAddr) {
    let host = Host::start(
        HostConfig {
            hello: hello("host", &["EP01", "GP01"]),
            mods_dir: mods_dir.to_path_buf(),
            mods: Manifest::scan(mods_dir, None).unwrap(),
            save: save.map(Path::to_path_buf),
            own_families: false,
        },
        0,
        false,
    )
    .unwrap();
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, host.port()));
    (host, address)
}

fn wait_for(host: &Host, wanted: impl Fn(&HostEvent) -> bool) -> HostEvent {
    loop {
        let event = host.wait(Duration::from_secs(5)).expect("host event did not arrive");
        if wanted(&event) {
            return event;
        }
    }
}

#[test]
fn guest_ends_up_with_the_hosts_mods_and_nothing_is_lost() {
    let host_dir = tempfile::tempdir().unwrap();
    write(host_dir.path(), "same.package", "same");
    write(host_dir.path(), "changed.package", "host version");
    write(host_dir.path(), "deep/nested/new.ts4script", "new file");
    write(host_dir.path(), "empty.package", "");
    let guest = tempfile::tempdir().unwrap();
    let mods = guest.path().join("Mods");
    let backup = guest.path().join("backup");
    write(&mods, "same.package", "same");
    write(&mods, "changed.package", "guest version");
    write(&mods, "sub/guest-only.package", "mine");
    let (host, address) = start_host(host_dir.path(), None);

    let mut connection = Connection::connect(address, &hello("guest", &["EP01"])).unwrap();
    assert_eq!(connection.host.name, "host");
    let session = connection.session().unwrap();
    let local = Manifest::scan(&mods, None).unwrap();
    let diff = local.diff_against(&session.mods);
    let mut last = None;
    sync_mods(&mut connection, &mods, &backup, &session.mods, &diff, &mut |p| last = Some(p.clone())).unwrap();
    connection.send_status(GuestStatus::Ready).unwrap();

    let after = Manifest::scan(&mods, None).unwrap();
    assert!(after.diff_against(&session.mods).is_in_sync());
    assert_eq!(read(&mods, "changed.package"), "host version");
    assert_eq!(read(&mods, "deep/nested/new.ts4script"), "new file");
    assert_eq!(read(&backup, "changed.package"), "guest version");
    assert_eq!(read(&backup, "sub/guest-only.package"), "mine");
    assert!(!mods.join("sub/guest-only.package").exists());
    let last = last.unwrap();
    assert_eq!(last.done_bytes, last.total_bytes);
    assert_eq!(last.total_bytes, diff.fetch_bytes);

    let joined = wait_for(&host, |e| matches!(e, HostEvent::GuestJoined { .. }));
    assert!(matches!(joined, HostEvent::GuestJoined { hello, .. } if hello.name == "guest"));
    wait_for(&host, |e| matches!(e, HostEvent::GuestStatus { status: GuestStatus::Ready, .. }));
    drop(connection);
    wait_for(&host, |e| matches!(e, HostEvent::GuestLeft { .. }));
}

#[test]
fn save_is_fetched_once_and_a_different_local_save_is_kept() {
    let host_dir = tempfile::tempdir().unwrap();
    let host_save = host_dir.path().join("Slot_00000002.save");
    fs::write(&host_save, "shared save").unwrap();
    let host_mods = host_dir.path().join("Mods");
    fs::create_dir(&host_mods).unwrap();
    let guest = tempfile::tempdir().unwrap();
    let saves = guest.path().join("saves");
    let backup = guest.path().join("backup");
    write(&saves, "Slot_00000002.save", "guest's own save");
    let (_host, address) = start_host(&host_mods, Some(&host_save));

    let mut connection = Connection::connect(address, &hello("guest", &[])).unwrap();
    let save = connection.session().unwrap().save.expect("host offers a save");

    assert!(sync_save(&mut connection, &saves, &backup, &save, &mut |_| {}).unwrap());
    assert_eq!(read(&saves, "Slot_00000002.save"), "shared save");
    assert_eq!(read(&backup, "Slot_00000002.save"), "guest's own save");
    // Already identical: nothing to do.
    assert!(!sync_save(&mut connection, &saves, &backup, &save, &mut |_| {}).unwrap());
}

#[test]
fn host_only_serves_files_from_its_manifest() {
    let host_dir = tempfile::tempdir().unwrap();
    let mods = host_dir.path().join("Mods");
    write(&mods, "ok.package", "ok");
    write(host_dir.path(), "secret.txt", "secret");
    let (_host, address) = start_host(&mods, None);

    let mut stream = TcpStream::connect(address).unwrap();
    write_message(&mut stream, &Message::Hello(hello("guest", &[]))).unwrap();
    assert!(matches!(read_message(&mut stream).unwrap(), Some(Message::Hello(_))));
    for path in ["../secret.txt", "missing.package", "", "ok.package/../../secret.txt"] {
        let request = Message::GetFile {
            root: tsmp_net::protocol::Root::Mods,
            path: path.into(),
        };
        write_message(&mut stream, &request).unwrap();
        assert!(
            matches!(read_message(&mut stream).unwrap(), Some(Message::Error { .. })),
            "{path:?}"
        );
    }
    // No save was configured either.
    let request = Message::GetFile {
        root: tsmp_net::protocol::Root::Save,
        path: String::new(),
    };
    write_message(&mut stream, &request).unwrap();
    assert!(matches!(read_message(&mut stream).unwrap(), Some(Message::Error { .. })));
}

#[test]
fn guest_refuses_unsafe_paths_before_touching_anything() {
    let host_dir = tempfile::tempdir().unwrap();
    write(host_dir.path(), "ok.package", "ok");
    let guest = tempfile::tempdir().unwrap();
    let mods = guest.path().join("Mods");
    write(&mods, "mine.package", "mine");
    let (_host, address) = start_host(host_dir.path(), None);
    let mut connection = Connection::connect(address, &hello("guest", &[])).unwrap();
    let mut hostile = connection.session().unwrap().mods;
    let entry = hostile.files["ok.package"].clone();
    hostile.files.insert("../escaped.package".into(), entry);

    let diff = Manifest::scan(&mods, None).unwrap().diff_against(&hostile);
    let result = sync_mods(&mut connection, &mods, &guest.path().join("backup"), &hostile, &diff, &mut |_| {});

    assert!(matches!(result, Err(Error::UnsafePath(path)) if path == "../escaped.package"));
    assert_eq!(read(&mods, "mine.package"), "mine");
    assert!(!mods.join("ok.package").exists());
    assert!(!guest.path().join("escaped.package").exists());
}

#[test]
fn damaged_content_is_rejected_and_leaves_the_old_file() {
    let host_dir = tempfile::tempdir().unwrap();
    write(host_dir.path(), "mod.package", "original");
    let guest = tempfile::tempdir().unwrap();
    let mods = guest.path().join("Mods");
    write(&mods, "mod.package", "old local");
    let (_host, address) = start_host(host_dir.path(), None);
    let mut connection = Connection::connect(address, &hello("guest", &[])).unwrap();
    let session = connection.session().unwrap();
    // The file changes on the host after its manifest was taken.
    write(host_dir.path(), "mod.package", "tampered");

    let diff = Manifest::scan(&mods, None).unwrap().diff_against(&session.mods);
    let result = sync_mods(&mut connection, &mods, &guest.path().join("backup"), &session.mods, &diff, &mut |_| {});

    assert!(matches!(result, Err(Error::HashMismatch(path)) if path == "mod.package"));
    assert_eq!(read(&mods, "mod.package"), "old local");
    assert_eq!(fs::read_dir(&mods).unwrap().count(), 1, "no partial file left behind");
}

#[test]
fn mismatched_protocol_versions_are_reported() {
    let host_dir = tempfile::tempdir().unwrap();
    let (host, address) = start_host(host_dir.path(), None);
    let mut old = hello("guest", &[]);
    old.protocol = PROTOCOL_VERSION + 1;

    // The host answers, then hangs up on a guest it cannot talk to.
    let mut stream = TcpStream::connect(address).unwrap();
    write_message(&mut stream, &Message::Hello(old)).unwrap();
    assert!(matches!(read_message(&mut stream).unwrap(), Some(Message::Hello(_))));
    assert_eq!(read_message(&mut stream).unwrap(), None);
    assert_eq!(host.wait(Duration::from_millis(300)), None, "no join event for an incompatible guest");
}

#[test]
fn dropping_the_host_disconnects_guests() {
    let host_dir = tempfile::tempdir().unwrap();
    let (host, address) = start_host(host_dir.path(), None);
    let mut connection = Connection::connect(address, &hello("guest", &[])).unwrap();
    connection.session().unwrap();

    drop(host);

    assert!(connection.session().is_err());
    let mut probe = TcpStream::connect_timeout(&address, Duration::from_millis(500));
    if let Ok(stream) = probe.as_mut() {
        // Something else may have taken the port; it must not be our host.
        let _ = stream.write_all(b"x");
    }
}
