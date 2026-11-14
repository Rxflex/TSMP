//! Everything the launcher does, without any UI.
//!
//! Slow work (hashing, network, file transfer) runs on worker threads that
//! report back through a channel. The UI calls [`Model::pump`] a few times a
//! second and redraws when it returns `true`.

use std::fs;
use std::mem;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::mappings::{self, Support};
use crate::update;
use tsmp_core::game::GameInstall;
use tsmp_core::manifest::{Diff, Manifest, hash_file};
use gpui::{Image, ImageFormat};
use tsmp_core::packs::{PackSet, common_packs, disable_packs_arg, installed_packs};
use tsmp_core::preview::{SavePreview, read_preview};
use tsmp_core::saves::{SaveFile, list_saves};
use tsmp_core::status::ModStatus;
use tsmp_net::discovery::{Browser, Found};
use tsmp_net::guest::{Connection, Progress, sync_mods, sync_save};
use tsmp_net::host::{Host, HostConfig, HostEvent};
use tsmp_net::live::{LiveLink, LiveStatus};
use tsmp_net::protocol::{DEFAULT_PORT, GuestStatus, Hello, PROTOCOL_VERSION, Session};

/// File name of the in-game mod inside a Mods folder.
const MOD_FILE: &str = "tsmp.ts4script";
/// A host that has not been heard from for this long is no longer listed.
const HOST_EXPIRY: Duration = Duration::from_secs(4);
const PROGRESS_INTERVAL: Duration = Duration::from_millis(50);
const STATUS_INTERVAL: Duration = Duration::from_secs(1);

/// What is known about this computer's copy of the game.
pub struct Env {
    pub game: GameInstall,
    pub packs: PackSet,
    /// Shown to the other player.
    pub name: String,
    pub script_mods_enabled: bool,
}

impl Env {
    /// `TSMP_INSTALL_DIR` and `TSMP_USER_DIR` together override detection;
    /// `TSMP_NAME` overrides the player name.
    pub fn detect() -> Result<Self, String> {
        let overrides = (std::env::var_os("TSMP_INSTALL_DIR"), std::env::var_os("TSMP_USER_DIR"));
        let game = match overrides {
            (Some(install), Some(user)) => GameInstall::at(install.into(), user.into()),
            _ => GameInstall::detect().map_err(|e| e.to_string())?,
        };
        let name = ["TSMP_NAME", "COMPUTERNAME", "HOSTNAME"]
            .iter()
            .find_map(|key| std::env::var(key).ok().filter(|name| !name.trim().is_empty()))
            .unwrap_or_else(|| "Player".into());
        Self::new(game, name)
    }

    pub fn new(game: GameInstall, name: String) -> Result<Self, String> {
        let packs = installed_packs(&game.install_dir).map_err(|e| e.to_string())?;
        let script_mods_enabled = game.script_mods_enabled().unwrap_or(false);
        Ok(Self {
            game,
            packs,
            name,
            script_mods_enabled,
        })
    }

    fn hello(&self) -> Hello {
        Hello {
            protocol: PROTOCOL_VERSION,
            name: self.name.clone(),
            game_version: self.game.version.clone(),
            packs: self.packs.clone(),
        }
    }
}

pub enum Screen {
    Loading,
    /// The game was not found; nothing can be done.
    Fatal(String),
    Home,
    Host(HostScreen),
    Join(JoinScreen),
}

/// What a save is about, with its pictures ready to draw.
pub struct Preview {
    /// The pictures are moved out of this into the fields below.
    pub about: SavePreview,
    pub portrait: Option<Arc<Image>>,
    pub lot_picture: Option<Arc<Image>>,
}

impl Preview {
    /// `None` when the file cannot be read as a save; it is then shown by
    /// its file name alone.
    pub fn read(path: &Path) -> Option<Arc<Self>> {
        let mut about = read_preview(path).ok()?;
        let picture = |bytes: Option<Vec<u8>>| bytes.map(|bytes| Arc::new(Image::from_bytes(ImageFormat::Jpeg, bytes)));
        Some(Arc::new(Self {
            portrait: picture(about.portrait.take()),
            lot_picture: picture(about.lot_picture.take()),
            about,
        }))
    }
}

pub struct HostScreen {
    pub saves: Vec<SaveFile>,
    /// What is inside each of `saves`, once it has been read.
    pub previews: Vec<Option<Arc<Preview>>>,
    /// Index into `saves`, or `None` to host without a shared save.
    pub selected: Option<usize>,
    pub stage: HostStage,
}

pub enum HostStage {
    Setup,
    /// Scanning the Mods folder.
    Starting,
    Running(Running),
    Failed(String),
}

pub struct Running {
    host: Host,
    _bridge: BridgeFile,
    pub port: u16,
    pub mod_files: usize,
    pub mod_bytes: u64,
    /// Whether the Mods folder contains the TSMP mod for guests to receive.
    pub has_mod: bool,
    /// Other multiplayer mods that were moved out of the Mods folder.
    pub set_aside: Vec<String>,
    pub save: Option<String>,
    pub guests: Vec<Guest>,
}

pub struct Guest {
    pub id: u64,
    pub address: SocketAddr,
    pub hello: Hello,
    pub status: Option<GuestStatus>,
}

pub struct JoinScreen {
    browser: Option<Browser>,
    pub hosts: Vec<SeenHost>,
    /// The host being joined, once it has answered.
    pub host: Option<Hello>,
    pub stage: JoinStage,
}

pub struct SeenHost {
    pub found: Found,
    last_seen: Instant,
}

pub enum JoinStage {
    Browsing,
    /// Waiting for the host or reading the local Mods folder.
    Working(JoinStep),
    /// Waiting for the player to approve the changes.
    Review(Plan),
    Syncing(Progress),
    Ready(Synced),
    Failed(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JoinStep {
    Connecting,
    /// Hashing the local Mods folder to compare it with the host's.
    Comparing,
}

/// What joining would change on this computer.
pub struct Plan {
    connection: Connection,
    address: SocketAddr,
    pub session: Session,
    pub diff: Diff,
    /// Whether the shared save has to be downloaded.
    pub save_needed: bool,
}

impl HostScreen {
    pub fn selected_preview(&self) -> Option<&Arc<Preview>> {
        self.previews.get(self.selected?)?.as_ref()
    }
}

impl Plan {
    pub fn is_noop(&self) -> bool {
        self.diff.is_in_sync() && !self.save_needed
    }
}

pub struct Synced {
    /// Held so the host keeps seeing this guest.
    _connection: Connection,
    /// The live session with the host's game.
    live: LiveLink,
    _bridge: BridgeFile,
    pub fetched: usize,
    pub moved: usize,
    pub save: Option<String>,
    pub save_preview: Option<Arc<Preview>>,
    pub save_updated: bool,
    /// Folder name under the user folder holding displaced mods, if any.
    pub backup_stamp: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GameState {
    NotStarted,
    /// Launched; waiting for the in-game mod to report.
    Starting {
        launched_at: u64,
        expected: PackSet,
        via_steam: bool,
    },
    Running {
        expected: PackSet,
        loaded: PackSet,
    },
    Failed(String),
}

impl GameState {
    /// Whether the running game has exactly the session's packs.
    pub fn packs_match(&self) -> Option<bool> {
        match self {
            GameState::Running { expected, loaded } => Some(expected == loaded),
            _ => None,
        }
    }
}

/// `<user folder>/tsmp/bridge.json`: tells the mod in the game where this
/// launcher's relay listens and which role to play. It exists for as long as
/// the session does.
struct BridgeFile(PathBuf);

impl BridgeFile {
    fn write(env: &Env, port: u16, role: &str) -> Result<Self, String> {
        let dir = env.game.user_dir.join("tsmp");
        let path = dir.join("bridge.json");
        let body = format!(r#"{{"port":{port},"role":"{role}"}}"#);
        fs::create_dir_all(&dir)
            .and_then(|()| fs::write(&path, body))
            .map_err(|e| format!("{}: {e}", path.display()))?;
        Ok(Self(path))
    }
}

impl Drop for BridgeFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

enum Event {
    Detected(Result<Env, String>),
    /// Whether building together knows the installed game version.
    Mapped(Support),
    /// A newer launcher is in place and runs after a restart.
    Updated(String),
    /// What is inside the save at this index of the host screen's list.
    Previewed(usize, Option<Arc<Preview>>),
    HostStarted(Result<Running, String>),
    JoinStep(JoinStep),
    Planned(Result<Plan, String>),
    Progress(Progress),
    Synced(Result<Synced, String>),
}

pub struct Model {
    sender: Sender<Event>,
    events: Receiver<Event>,
    pub env: Option<Arc<Env>>,
    /// Not known until the mappings have been looked up.
    pub building: Option<Support>,
    /// The version waiting for a restart, once an update is in place.
    pub update: Option<String>,
    pub screen: Screen,
    pub game: GameState,
    /// The live session, while hosting or after joining.
    pub live: Option<LiveStatus>,
    /// What the mods of both games logged lately, oldest first.
    pub live_log: Vec<String>,
    live_logged: u64,
    /// Whether a host broadcasts itself on the network.
    announce: bool,
    last_status_check: Instant,
}

impl Model {
    /// Starts detecting the game in the background.
    pub fn start() -> Self {
        let model = Self::empty(Screen::Loading, None, true);
        let sender = model.sender.clone();
        thread::spawn(move || {
            let _ = sender.send(Event::Detected(Env::detect()));
        });
        // `TSMP_NO_UPDATE` keeps the file as it is; so does a debug build.
        if !cfg!(debug_assertions) && std::env::var_os("TSMP_NO_UPDATE").is_none() {
            let sender = model.sender.clone();
            thread::spawn(move || {
                let Ok(exe) = std::env::current_exe() else { return };
                update::clean_up(&exe);
                let agent = update::agent();
                if let Ok(Some(release)) = update::newer_release(&agent)
                    && update::install(&agent, &release, &exe).is_ok()
                {
                    let _ = sender.send(Event::Updated(release.version));
                }
            });
        }
        model
    }

    /// Whether restarting now would interrupt nothing.
    pub fn can_restart(&self) -> bool {
        matches!(self.screen, Screen::Loading | Screen::Home | Screen::Fatal(_))
            && matches!(self.game, GameState::NotStarted | GameState::Failed(_))
    }

    #[cfg(test)]
    pub fn with_env(env: Env, announce: bool) -> Self {
        Self::empty(Screen::Home, Some(Arc::new(env)), announce)
    }

    fn empty(screen: Screen, env: Option<Arc<Env>>, announce: bool) -> Self {
        let (sender, events) = mpsc::channel();
        Self {
            sender,
            events,
            env,
            building: None,
            update: None,
            screen,
            game: GameState::NotStarted,
            live: None,
            live_log: Vec::new(),
            live_logged: 0,
            announce,
            last_status_check: Instant::now(),
        }
    }

    /// Back to the home screen, abandoning whatever was in progress. Workers
    /// still running report into a channel nobody reads any more.
    pub fn back(&mut self) {
        if self.env.is_none() {
            return;
        }
        let (sender, events) = mpsc::channel();
        self.sender = sender;
        self.events = events;
        self.screen = Screen::Home;
    }

    pub fn open_host(&mut self) {
        let Some(env) = &self.env else { return };
        let saves = list_saves(&env.game.saves_dir()).unwrap_or_default();
        let paths: Vec<PathBuf> = saves.iter().map(|save| save.path.clone()).collect();
        self.screen = Screen::Host(HostScreen {
            selected: None,
            previews: vec![None; saves.len()],
            saves,
            stage: HostStage::Setup,
        });
        let sender = self.sender.clone();
        thread::spawn(move || {
            for (index, path) in paths.iter().enumerate() {
                if sender.send(Event::Previewed(index, Preview::read(path))).is_err() {
                    break;
                }
            }
        });
    }

    pub fn select_save(&mut self, selected: Option<usize>) {
        if let Screen::Host(screen) = &mut self.screen {
            if matches!(screen.stage, HostStage::Setup) {
                screen.selected = selected.filter(|&index| index < screen.saves.len());
            }
        }
    }

    pub fn start_hosting(&mut self) {
        let (Some(env), Screen::Host(screen)) = (&self.env, &mut self.screen) else {
            return;
        };
        if !matches!(screen.stage, HostStage::Setup) {
            return;
        }
        screen.stage = HostStage::Starting;
        let save = screen.selected.map(|index| screen.saves[index].clone());
        let (env, sender, announce) = (env.clone(), self.sender.clone(), self.announce);
        thread::spawn(move || {
            let _ = sender.send(Event::HostStarted(start_host(&env, save, announce)));
        });
    }

    pub fn open_join(&mut self) {
        if self.env.is_none() {
            return;
        }
        let (browser, stage) = match Browser::start() {
            Ok(browser) => (Some(browser), JoinStage::Browsing),
            Err(e) => (None, JoinStage::Failed(format!("Cannot listen for hosts: {e}"))),
        };
        self.screen = Screen::Join(JoinScreen {
            browser,
            hosts: Vec::new(),
            host: None,
            stage,
        });
    }

    /// Connect to a host and work out what joining it would change.
    pub fn connect(&mut self, address: SocketAddr) {
        let Some(env) = &self.env else { return };
        match &mut self.screen {
            Screen::Join(screen) if matches!(screen.stage, JoinStage::Browsing) => {
                screen.stage = JoinStage::Working(JoinStep::Connecting);
            }
            Screen::Home => {
                self.screen = Screen::Join(JoinScreen {
                    browser: None,
                    hosts: Vec::new(),
                    host: None,
                    stage: JoinStage::Working(JoinStep::Connecting),
                });
            }
            _ => return,
        }
        let (env, sender) = (env.clone(), self.sender.clone());
        thread::spawn(move || {
            let plan = make_plan(&env, address, &sender);
            let _ = sender.send(Event::Planned(plan));
        });
    }

    /// Apply the reviewed plan: move displaced files aside and download.
    pub fn confirm_sync(&mut self) {
        let (Some(env), Screen::Join(screen)) = (&self.env, &mut self.screen) else {
            return;
        };
        if !matches!(screen.stage, JoinStage::Review(_)) {
            return;
        }
        let JoinStage::Review(plan) = mem::replace(&mut screen.stage, JoinStage::Syncing(Progress::default())) else {
            unreachable!("checked above");
        };
        let (env, sender) = (env.clone(), self.sender.clone());
        thread::spawn(move || {
            let synced = run_sync(&env, plan, &sender);
            let _ = sender.send(Event::Synced(synced));
        });
    }

    /// The packs this session is played with: those every player owns.
    pub fn session_packs(&self) -> PackSet {
        let Some(env) = &self.env else {
            return PackSet::new();
        };
        match &self.screen {
            Screen::Host(HostScreen {
                stage: HostStage::Running(running),
                ..
            }) => common_packs(std::iter::once(&env.packs).chain(running.guests.iter().map(|g| &g.hello.packs))),
            Screen::Join(JoinScreen { host: Some(host), .. }) => common_packs([&env.packs, &host.packs]),
            _ => env.packs.clone(),
        }
    }

    /// Arguments the game has to be started with for this session.
    pub fn launch_args(&self) -> Vec<String> {
        let Some(env) = &self.env else { return Vec::new() };
        disable_packs_arg(&env.packs, &self.session_packs()).into_iter().collect()
    }

    pub fn can_launch(&self) -> bool {
        let in_session = match &self.screen {
            Screen::Host(screen) => matches!(screen.stage, HostStage::Running(_)),
            Screen::Join(screen) => matches!(screen.stage, JoinStage::Ready(_)),
            _ => false,
        };
        in_session && !matches!(self.game, GameState::Starting { .. })
    }

    pub fn launch_game(&mut self) {
        if !self.can_launch() {
            return;
        }
        let Some(env) = &self.env else { return };
        let plan = env.game.launch_plan(&self.launch_args());
        self.game = match plan.spawn() {
            Ok(_) => GameState::Starting {
                launched_at: unix_now(),
                expected: self.session_packs(),
                via_steam: plan.via_steam,
            },
            Err(e) => GameState::Failed(format!("Cannot start {}: {e}", plan.program.display())),
        };
    }

    /// Take in whatever happened since the last call. Returns whether
    /// anything visible changed.
    pub fn pump(&mut self) -> bool {
        let mut changed = false;
        while let Ok(event) = self.events.try_recv() {
            self.apply(event);
            changed = true;
        }
        changed |= self.pump_network();
        changed |= self.pump_game();
        let live = match &self.screen {
            Screen::Host(HostScreen {
                stage: HostStage::Running(running),
                ..
            }) => {
                let relay = running.host.live();
                Some((relay.status(), relay.logged(), relay.log()))
            }
            Screen::Join(JoinScreen {
                stage: JoinStage::Ready(synced),
                ..
            }) => Some((synced.live.status(), synced.live.logged(), synced.live.log())),
            _ => None,
        };
        let live = live.map(|(status, logged, log)| {
            if logged != self.live_logged {
                self.live_logged = logged;
                self.live_log = log;
                changed = true;
            }
            status
        });
        changed |= live != self.live;
        self.live = live;
        changed
    }

    fn apply(&mut self, event: Event) {
        match event {
            Event::Detected(Ok(env)) => {
                let (sender, data_dir, version) =
                    (self.sender.clone(), env.game.user_dir.join("tsmp"), env.game.version.clone());
                thread::spawn(move || {
                    let agent = update::agent();
                    let fetch = |version: &str| update::repository_file(&agent, &format!("mappings/{version}.json"));
                    let _ = sender.send(Event::Mapped(mappings::install(&data_dir, version.as_deref(), fetch)));
                });
                self.env = Some(Arc::new(env));
                self.screen = Screen::Home;
            }
            Event::Mapped(support) => self.building = Some(support),
            Event::Updated(version) => self.update = Some(version),
            Event::Detected(Err(message)) => self.screen = Screen::Fatal(message),
            Event::Previewed(index, preview) => {
                if let Screen::Host(screen) = &mut self.screen {
                    if let Some(slot) = screen.previews.get_mut(index) {
                        *slot = preview;
                    }
                }
            }
            Event::HostStarted(result) => {
                if let Screen::Host(screen) = &mut self.screen {
                    screen.stage = match result {
                        Ok(running) => HostStage::Running(running),
                        Err(message) => HostStage::Failed(message),
                    };
                }
            }
            Event::JoinStep(next) => {
                if let Screen::Join(JoinScreen {
                    stage: JoinStage::Working(step),
                    ..
                }) = &mut self.screen
                {
                    *step = next;
                }
            }
            Event::Planned(result) => {
                let Screen::Join(screen) = &mut self.screen else { return };
                match result {
                    Ok(plan) => {
                        screen.host = Some(plan.connection.host.clone());
                        let nothing_to_do = plan.is_noop();
                        screen.stage = JoinStage::Review(plan);
                        if nothing_to_do {
                            self.confirm_sync();
                        }
                    }
                    Err(message) => screen.stage = JoinStage::Failed(message),
                }
            }
            Event::Progress(progress) => {
                if let Screen::Join(JoinScreen {
                    stage: JoinStage::Syncing(current),
                    ..
                }) = &mut self.screen
                {
                    *current = progress;
                }
            }
            Event::Synced(result) => {
                if let Screen::Join(screen) = &mut self.screen {
                    screen.stage = match result {
                        Ok(synced) => JoinStage::Ready(synced),
                        Err(message) => JoinStage::Failed(message),
                    };
                }
            }
        }
    }

    fn pump_network(&mut self) -> bool {
        match &mut self.screen {
            Screen::Host(HostScreen {
                stage: HostStage::Running(running),
                ..
            }) => {
                let events = running.host.poll();
                for event in &events {
                    match event.clone() {
                        HostEvent::GuestJoined { id, address, hello } => running.guests.push(Guest {
                            id,
                            address,
                            hello,
                            status: None,
                        }),
                        HostEvent::GuestStatus { id, status } => {
                            if let Some(guest) = running.guests.iter_mut().find(|g| g.id == id) {
                                guest.status = Some(status);
                            }
                        }
                        HostEvent::GuestLeft { id } => running.guests.retain(|g| g.id != id),
                    }
                }
                !events.is_empty()
            }
            Screen::Join(screen) => {
                let Some(browser) = &screen.browser else { return false };
                let before = screen.hosts.len();
                let mut changed = false;
                for found in browser.poll() {
                    match screen.hosts.iter_mut().find(|h| h.found.address == found.address) {
                        Some(seen) => {
                            changed |= seen.found != found;
                            seen.found = found;
                            seen.last_seen = Instant::now();
                        }
                        None => screen.hosts.push(SeenHost {
                            found,
                            last_seen: Instant::now(),
                        }),
                    }
                }
                screen.hosts.retain(|h| h.last_seen.elapsed() < HOST_EXPIRY);
                changed || screen.hosts.len() != before
            }
            _ => false,
        }
    }

    fn pump_game(&mut self) -> bool {
        let (Some(env), GameState::Starting { launched_at, expected, .. }) = (&self.env, &self.game) else {
            return false;
        };
        if self.last_status_check.elapsed() < STATUS_INTERVAL {
            return false;
        }
        self.last_status_check = Instant::now();
        let Some(status) = ModStatus::read(&env.game.user_dir) else {
            return false;
        };
        match game_started(&status, *launched_at, expected) {
            Some(state) => {
                self.game = state;
                true
            }
            None => false,
        }
    }
}

/// The state to move to once the mod has reported from a game started at or
/// after `launched_at`; `None` while the report is from an earlier run.
fn game_started(status: &ModStatus, launched_at: u64, expected: &PackSet) -> Option<GameState> {
    (status.started_at >= launched_at).then(|| GameState::Running {
        expected: expected.clone(),
        loaded: status.loaded_packs(),
    })
}

fn scan_mods(env: &Env) -> Result<Manifest, String> {
    let mods_dir = env.game.mods_dir();
    fs::create_dir_all(&mods_dir).map_err(|e| format!("{}: {e}", mods_dir.display()))?;
    // Hashes of unchanged files are remembered between runs.
    let cache_dir = env.game.user_dir.join("tsmp");
    let cache_path = cache_dir.join("mods-manifest.json");
    let cache = Manifest::load(&cache_path).ok();
    let manifest = Manifest::scan(&mods_dir, cache.as_ref()).map_err(|e| e.to_string())?;
    if fs::create_dir_all(&cache_dir).is_ok() {
        let _ = manifest.save(&cache_path);
    }
    Ok(manifest)
}

/// The mod built into the launcher; empty when it was compiled without one.
const BUILT_IN_MOD: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/tsmp.ts4script"));

/// The mod to install: a file beside the launcher if there is one, so a newer
/// mod can be tried without rebuilding, otherwise the one built in.
fn bundled_mod() -> Option<Vec<u8>> {
    let beside = std::env::current_exe().ok().and_then(|exe| fs::read(exe.parent()?.join(MOD_FILE)).ok());
    beside.or_else(|| (!BUILT_IN_MOD.is_empty() && !cfg!(test)).then(|| BUILT_IN_MOD.to_vec()))
}

/// Puts the mod into `mods_dir`, replacing a copy that differs
/// wherever in the folder it is. Returns whether anything was written.
fn install_mod(wanted: &[u8], mods_dir: &std::path::Path) -> Result<bool, String> {
    fn find(dir: &std::path::Path) -> Option<PathBuf> {
        let mut folders = Vec::new();
        for entry in fs::read_dir(dir).ok()?.flatten() {
            let path = entry.path();
            if path.is_dir() {
                folders.push(path);
            } else if entry.file_name().to_string_lossy().eq_ignore_ascii_case(MOD_FILE) {
                return Some(path);
            }
        }
        folders.iter().find_map(|folder| find(folder))
    }
    fs::create_dir_all(mods_dir).map_err(|e| format!("{}: {e}", mods_dir.display()))?;
    let target = find(mods_dir).unwrap_or_else(|| mods_dir.join(MOD_FILE));
    if fs::read(&target).is_ok_and(|installed| installed == wanted) {
        return Ok(false);
    }
    fs::write(&target, wanted)
        .map_err(|e| format!("Cannot update {} (is the game running?): {e}", target.display()))?;
    Ok(true)
}

/// The top-level Python package of "Sims 4 Multiplayer" (S4MP). It hooks the
/// same parts of the game as TSMP, so the two cannot run side by side.
const RIVAL_PACKAGE: &[u8] = b"sims4multiplayer/";
const RIVAL_PREFIX: &str = "s4mp";

/// Whether a script mod is another multiplayer mod: by its name, or by the
/// package inside it (file names are stored as plain text in the archive).
fn is_rival(path: &std::path::Path) -> bool {
    let name = path.file_name().unwrap_or_default().to_string_lossy().to_ascii_lowercase();
    if !name.ends_with(".ts4script") {
        return false;
    }
    name.starts_with(RIVAL_PREFIX)
        || fs::read(path).is_ok_and(|bytes| bytes.windows(RIVAL_PACKAGE.len()).any(|part| part == RIVAL_PACKAGE))
}

/// Moves other multiplayer mods out of the Mods folder into the backup folder
/// beside it, with the packages that share their name. Nothing is deleted.
/// The guest's copies go the same way when they sync, as the host has none.
fn set_rivals_aside(env: &Env) -> Result<Vec<String>, String> {
    fn scripts(dir: &std::path::Path, found: &mut Vec<PathBuf>) {
        for entry in fs::read_dir(dir).into_iter().flatten().flatten() {
            let path = entry.path();
            if path.is_dir() {
                scripts(&path, found);
            } else if is_rival(&path) {
                found.push(path);
            }
        }
    }
    let mods_dir = env.game.mods_dir();
    let mut rivals = Vec::new();
    scripts(&mods_dir, &mut rivals);
    for script in rivals.clone() {
        let package = script.with_extension("package");
        if package.is_file() {
            rivals.push(package);
        }
    }
    let backup = env.game.user_dir.join("Mods.tsmp-backup").join(backup_stamp(unix_now()));
    let mut moved = Vec::new();
    for path in rivals {
        let relative = path.strip_prefix(&mods_dir).unwrap_or(&path);
        let target = backup.join(relative);
        let failed = |e| format!("Cannot move {} out of Mods (is the game running?): {e}", path.display());
        fs::create_dir_all(target.parent().unwrap_or(&backup)).map_err(failed)?;
        fs::rename(&path, &target).map_err(failed)?;
        moved.push(relative.to_string_lossy().replace('\\', "/"));
    }
    Ok(moved)
}

fn start_host(env: &Env, save: Option<SaveFile>, announce: bool) -> Result<Running, String> {
    if let Some(source) = bundled_mod() {
        install_mod(&source, &env.game.mods_dir())?;
    }
    let set_aside = set_rivals_aside(env)?;
    let mods = scan_mods(env)?;
    let mod_files = mods.files.len();
    let mod_bytes = mods.total_bytes();
    let has_mod = mods
        .files
        .keys()
        .any(|path| path.rsplit('/').next().is_some_and(|name| name.eq_ignore_ascii_case(MOD_FILE)));
    let config = || HostConfig {
        hello: env.hello(),
        mods_dir: env.game.mods_dir(),
        mods: mods.clone(),
        save: save.as_ref().map(|save| save.path.clone()),
    };
    // The usual port may be taken, e.g. by another launcher on this machine.
    // Guests find the host by its beacon, so any port will do.
    let host = Host::start(config(), DEFAULT_PORT, announce)
        .or_else(|_| Host::start(config(), 0, announce))
        .map_err(|e| e.to_string())?;
    let bridge = BridgeFile::write(env, host.live().port(), "host")?;
    Ok(Running {
        port: host.port(),
        host,
        _bridge: bridge,
        mod_files,
        mod_bytes,
        has_mod,
        set_aside,
        save: save.map(|save| save.file_name),
        guests: Vec::new(),
    })
}

fn make_plan(env: &Env, address: SocketAddr, sender: &Sender<Event>) -> Result<Plan, String> {
    let mut connection = Connection::connect(address, &env.hello()).map_err(|e| e.to_string())?;
    let session = connection.session().map_err(|e| e.to_string())?;
    let _ = sender.send(Event::JoinStep(JoinStep::Comparing));
    let local = scan_mods(env)?;
    let diff = local.diff_against(&session.mods);
    let save_needed = match &session.save {
        Some(save) => {
            let local_save = env.game.saves_dir().join(&save.file_name);
            !(local_save.is_file() && hash_file(&local_save).is_ok_and(|hash| hash == save.hash))
        }
        None => false,
    };
    Ok(Plan {
        connection,
        address,
        session,
        diff,
        save_needed,
    })
}

fn run_sync(env: &Env, plan: Plan, sender: &Sender<Event>) -> Result<Synced, String> {
    let Plan {
        mut connection,
        address,
        session,
        diff,
        ..
    } = plan;
    let backup_stamp = backup_stamp(unix_now());
    let mods_backup = env.game.user_dir.join("Mods.tsmp-backup").join(&backup_stamp);
    let saves_backup = env.game.user_dir.join("saves.tsmp-backup").join(&backup_stamp);

    let mut last_report: Option<Instant> = None;
    let mut report = |progress: &Progress| {
        let finished = progress.done_bytes == progress.total_bytes;
        if finished || last_report.is_none_or(|at| at.elapsed() >= PROGRESS_INTERVAL) {
            last_report = Some(Instant::now());
            let _ = sender.send(Event::Progress(progress.clone()));
        }
    };
    let result = (|| -> tsmp_net::Result<bool> {
        sync_mods(
            &mut connection,
            &env.game.mods_dir(),
            &mods_backup,
            &session.mods,
            &diff,
            &mut report,
        )?;
        match &session.save {
            Some(save) => sync_save(&mut connection, &env.game.saves_dir(), &saves_backup, save, &mut report),
            None => Ok(false),
        }
    })();
    match result {
        Ok(save_updated) => {
            let live = LiveLink::start(address, env.hello()).map_err(|e| e.to_string())?;
            let bridge = BridgeFile::write(env, live.port(), "guest")?;
            connection.send_status(GuestStatus::Ready).map_err(|e| e.to_string())?;
            Ok(Synced {
                _connection: connection,
                live,
                _bridge: bridge,
                fetched: diff.fetch.len(),
                moved: diff.extra.len(),
                save_preview: session
                    .save
                    .as_ref()
                    .and_then(|save| Preview::read(&env.game.saves_dir().join(&save.file_name))),
                save: session.save.map(|save| save.file_name),
                save_updated,
                backup_stamp,
            })
        }
        Err(e) => {
            let message = e.to_string();
            let _ = connection.send_status(GuestStatus::Failed {
                message: message.clone(),
            });
            Err(message)
        }
    }
}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_secs())
}

/// `2026-11-13_22-42-54` (UTC), usable as a folder name.
fn backup_stamp(unix_seconds: u64) -> String {
    let days = unix_seconds / 86_400;
    let seconds = unix_seconds % 86_400;
    // Days since 1970-01-01 to a calendar date (Howard Hinnant's algorithm).
    let shifted = days + 719_468;
    let era = shifted / 146_097;
    let day_of_era = shifted % 146_097;
    let year_of_era = (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_index = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_index + 2) / 5 + 1;
    let month = if month_index < 10 { month_index + 3 } else { month_index - 9 };
    let year = year_of_era + era * 400 + u64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02}_{:02}-{:02}-{:02}",
        seconds / 3_600,
        seconds % 3_600 / 60,
        seconds % 60
    )
}

#[cfg(test)]
mod tests {
    use std::net::Ipv4Addr;
    use std::path::Path;

    use super::*;

    fn write(root: &Path, relative: &str, content: &str) {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content).unwrap();
    }

    /// A pretend game: an install folder with `packs` and a user folder.
    fn player(root: &Path, name: &str, packs: &[&str]) -> Env {
        let install = root.join("install");
        let user = root.join("user");
        for pack in packs {
            fs::create_dir_all(install.join(pack)).unwrap();
        }
        fs::create_dir_all(install.join("Game").join("Bin")).unwrap();
        fs::create_dir_all(user.join("Mods")).unwrap();
        fs::create_dir_all(user.join("saves")).unwrap();
        fs::write(user.join("GameVersion.txt"), b"\x03\x00\x00\x001.0").unwrap();
        fs::write(user.join("Options.ini"), "scriptmodsenabled = 1\nmodsdisabled = 0\n").unwrap();
        Env::new(GameInstall::at(install, user), name.into()).unwrap()
    }

    fn pump_until(models: &mut [&mut Model], done: impl Fn(&[&mut Model]) -> bool) {
        let deadline = Instant::now() + Duration::from_secs(20);
        while !done(models) {
            assert!(Instant::now() < deadline, "timed out");
            for model in models.iter_mut() {
                model.pump();
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn host_running(model: &Model) -> Option<&Running> {
        match &model.screen {
            Screen::Host(HostScreen {
                stage: HostStage::Running(running),
                ..
            }) => Some(running),
            _ => None,
        }
    }

    fn join_stage(model: &Model) -> &JoinStage {
        match &model.screen {
            Screen::Join(screen) => &screen.stage,
            _ => panic!("not on the join screen"),
        }
    }

    #[test]
    fn other_multiplayer_mods_are_moved_out_of_mods() {
        let dir = tempfile::tempdir().unwrap();
        let env = player(dir.path(), "host", &[]);
        let user = env.game.user_dir.clone();
        write(&user, "Mods/S4MP_release.ts4script", "by name");
        write(&user, "Mods/S4MP_release.package", "its package");
        write(&user, "Mods/deep/renamed.ts4script", "PK..sims4multiplayer/__init__.pyc..");
        write(&user, "Mods/other.ts4script", "PK..othermod/__init__.pyc..");
        write(&user, "Mods/s4mp-notes.package", "not a script");

        let mut moved = set_rivals_aside(&env).unwrap();
        moved.sort();
        assert_eq!(moved, ["S4MP_release.package", "S4MP_release.ts4script", "deep/renamed.ts4script"]);
        assert!(user.join("Mods/other.ts4script").is_file());
        assert!(user.join("Mods/s4mp-notes.package").is_file());
        assert!(!user.join("Mods/S4MP_release.ts4script").exists());
        let stamp = fs::read_dir(user.join("Mods.tsmp-backup")).unwrap().next().unwrap().unwrap().path();
        assert_eq!(fs::read_to_string(stamp.join("deep/renamed.ts4script")).unwrap().len(), 35);
        assert!(set_rivals_aside(&env).unwrap().is_empty());
    }

    #[test]
    fn the_bundled_mod_replaces_a_different_copy_where_it_already_is() {
        let dir = tempfile::tempdir().unwrap();
        let source = b"new";
        let mods = dir.path().join("Mods");

        assert!(install_mod(source, &mods).unwrap());
        assert_eq!(fs::read_to_string(mods.join("tsmp.ts4script")).unwrap(), "new");
        assert!(!install_mod(source, &mods).unwrap());

        fs::remove_file(mods.join("tsmp.ts4script")).unwrap();
        write(&mods, "scripts/TSMP.ts4script", "old");
        assert!(install_mod(source, &mods).unwrap());
        assert_eq!(fs::read_to_string(mods.join("scripts/TSMP.ts4script")).unwrap(), "new");
        assert!(!mods.join("tsmp.ts4script").exists());
    }

    #[test]
    fn two_launchers_end_up_in_sync_with_common_packs() {
        let host_dir = tempfile::tempdir().unwrap();
        let guest_dir = tempfile::tempdir().unwrap();
        let host_env = player(host_dir.path(), "host", &["EP01"]);
        let guest_env = player(guest_dir.path(), "guest", &["EP01", "EP02", "GP03"]);
        let host_user = host_env.game.user_dir.clone();
        let guest_user = guest_env.game.user_dir.clone();
        write(&host_user, "Mods/tsmp.ts4script", "the mod");
        write(&host_user, "Mods/cc/hair.package", "hair");
        write(&host_user, "Mods/shared.package", "host version");
        write(&host_user, "saves/Slot_00000002.save", "shared save");
        write(&guest_user, "Mods/cc/hair.package", "hair");
        write(&guest_user, "Mods/shared.package", "guest version");
        write(&guest_user, "Mods/only-mine.package", "mine");
        write(&guest_user, "saves/Slot_00000002.save", "guest's own");

        let mut host = Model::with_env(host_env, false);
        host.open_host();
        host.select_save(Some(0));
        host.start_hosting();
        pump_until(&mut [&mut host], |m| host_running(m[0]).is_some());
        let running = host_running(&host).unwrap();
        assert!(running.has_mod);
        assert_eq!(running.mod_files, 3);
        assert_eq!(running.save.as_deref(), Some("Slot_00000002.save"));
        let address = SocketAddr::from((Ipv4Addr::LOCALHOST, running.port));

        let mut guest = Model::with_env(guest_env, false);
        guest.connect(address);
        pump_until(&mut [&mut host, &mut guest], |m| {
            !matches!(join_stage(m[1]), JoinStage::Working(_))
        });
        let JoinStage::Review(plan) = join_stage(&guest) else {
            panic!("expected a plan to review");
        };
        assert_eq!(plan.diff.fetch, ["shared.package", "tsmp.ts4script"]);
        assert_eq!(plan.diff.extra, ["only-mine.package"]);
        assert!(plan.save_needed);
        // Nothing has been touched before the player agrees.
        assert_eq!(fs::read_to_string(guest_user.join("Mods/shared.package")).unwrap(), "guest version");
        assert!(!guest.can_launch());

        guest.confirm_sync();
        pump_until(&mut [&mut host, &mut guest], |m| {
            !matches!(join_stage(m[1]), JoinStage::Syncing(_))
                && host_running(m[0]).unwrap().guests.iter().any(|g| g.status == Some(GuestStatus::Ready))
        });
        let JoinStage::Ready(synced) = join_stage(&guest) else {
            panic!("sync failed");
        };
        assert_eq!((synced.fetched, synced.moved, synced.save_updated), (2, 1, true));
        let backup = guest_user.join("Mods.tsmp-backup").join(&synced.backup_stamp);
        let saves_backup = guest_user.join("saves.tsmp-backup").join(&synced.backup_stamp);
        assert_eq!(fs::read_to_string(guest_user.join("Mods/shared.package")).unwrap(), "host version");
        assert_eq!(fs::read_to_string(guest_user.join("Mods/tsmp.ts4script")).unwrap(), "the mod");
        assert_eq!(fs::read_to_string(backup.join("shared.package")).unwrap(), "guest version");
        assert_eq!(fs::read_to_string(backup.join("only-mine.package")).unwrap(), "mine");
        assert!(!guest_user.join("Mods/only-mine.package").exists());
        assert_eq!(fs::read_to_string(guest_user.join("saves/Slot_00000002.save")).unwrap(), "shared save");
        assert_eq!(fs::read_to_string(saves_backup.join("Slot_00000002.save")).unwrap(), "guest's own");

        // Both sides agree on the packs, and only the guest disables any.
        let common: PackSet = ["EP01".to_string()].into();
        assert_eq!(host.session_packs(), common);
        assert_eq!(guest.session_packs(), common);
        assert_eq!(host.launch_args(), Vec::<String>::new());
        assert_eq!(guest.launch_args(), ["-disablepacks:EP02,GP03"]);
        assert!(host.can_launch() && guest.can_launch());

        // Each mod is told where its launcher's relay is, and once both games
        // are in a zone the session is live on both sides.
        let host_port = host_running(&host).unwrap().host.live().port();
        let guest_port = synced.live.port();
        assert_eq!(
            fs::read_to_string(host_user.join("tsmp/bridge.json")).unwrap(),
            format!(r#"{{"port":{host_port},"role":"host"}}"#)
        );
        assert_eq!(
            fs::read_to_string(guest_user.join("tsmp/bridge.json")).unwrap(),
            format!(r#"{{"port":{guest_port},"role":"guest"}}"#)
        );
        let in_zone = |port: u16, role: &str| {
            use std::io::Write;
            let mut game = std::net::TcpStream::connect((Ipv4Addr::LOCALHOST, port)).unwrap();
            let state = format!(r#"{{"type":"state","role":"{role}","in_zone":true}}"#);
            game.write_all(&tsmp_net::live::frame(tsmp_net::live::KIND_CONTROL, 0, state.as_bytes()))
                .unwrap();
            game
        };
        pump_until(&mut [&mut host, &mut guest], |m| {
            m.iter().all(|model| model.live.is_some_and(|live| live.remotes == 1))
        });
        assert!(!host.live.unwrap().is_live());
        let _host_game = in_zone(host_port, "host");
        let _guest_game = in_zone(guest_port, "guest");
        pump_until(&mut [&mut host, &mut guest], |m| {
            m.iter().all(|model| model.live.is_some_and(|live| live.is_live()))
        });

        // The guest closing its launcher is noticed by the host.
        guest.back();
        pump_until(&mut [&mut host], |m| {
            host_running(m[0]).unwrap().guests.is_empty() && m[0].live.unwrap().remotes == 0
        });
        guest.pump();
        assert_eq!(guest.live, None);
        assert!(!guest_user.join("tsmp/bridge.json").exists());
        assert_eq!(host.session_packs(), common);
        host.back();
        assert!(!host_user.join("tsmp/bridge.json").exists());
    }

    #[test]
    fn a_guest_already_in_sync_is_ready_without_review() {
        let host_dir = tempfile::tempdir().unwrap();
        let guest_dir = tempfile::tempdir().unwrap();
        let host_env = player(host_dir.path(), "host", &[]);
        let guest_env = player(guest_dir.path(), "guest", &[]);
        write(&host_env.game.user_dir, "Mods/a.package", "a");
        write(&guest_env.game.user_dir, "Mods/a.package", "a");

        let mut host = Model::with_env(host_env, false);
        host.open_host();
        host.start_hosting();
        pump_until(&mut [&mut host], |m| host_running(m[0]).is_some());
        let running = host_running(&host).unwrap();
        assert!(!running.has_mod);
        let address = SocketAddr::from((Ipv4Addr::LOCALHOST, running.port));

        let mut guest = Model::with_env(guest_env, false);
        guest.connect(address);
        pump_until(&mut [&mut host, &mut guest], |m| {
            matches!(join_stage(m[1]), JoinStage::Ready(_) | JoinStage::Failed(_))
        });

        let JoinStage::Ready(synced) = join_stage(&guest) else {
            panic!("expected to be ready");
        };
        assert_eq!((synced.fetched, synced.moved, synced.save_updated), (0, 0, false));
    }

    #[test]
    fn an_unreachable_host_is_reported() {
        let dir = tempfile::tempdir().unwrap();
        let mut guest = Model::with_env(player(dir.path(), "guest", &[]), false);
        // A port nothing listens on.
        let listener = std::net::TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let address = listener.local_addr().unwrap();
        drop(listener);

        guest.connect(address);
        pump_until(&mut [&mut guest], |m| !matches!(join_stage(m[0]), JoinStage::Working(_)));

        assert!(matches!(join_stage(&guest), JoinStage::Failed(_)));
        guest.back();
        assert!(matches!(guest.screen, Screen::Home));
    }

    #[test]
    fn game_is_running_only_once_the_mod_reports_from_this_launch() {
        let expected: PackSet = ["EP01".to_string()].into();
        let status = |started_at, packs: &[&str]| ModStatus {
            mod_version: "0.0.1".into(),
            packs: packs.iter().map(|p| p.to_string()).collect(),
            started_at,
        };

        assert_eq!(game_started(&status(99, &["BASE_GAME", "EP01"]), 100, &expected), None);
        let good = game_started(&status(100, &["BASE_GAME", "EP01"]), 100, &expected).unwrap();
        assert_eq!(good.packs_match(), Some(true));
        // `-disablepacks:` was ignored: the game loaded a pack the other player lacks.
        let bad = game_started(&status(130, &["BASE_GAME", "EP01", "EP02"]), 100, &expected).unwrap();
        assert_eq!(bad.packs_match(), Some(false));
    }

    #[test]
    fn backup_stamps_are_calendar_dates() {
        assert_eq!(backup_stamp(0), "1970-01-01_00-00-00");
        assert_eq!(backup_stamp(951_782_400), "2000-02-29_00-00-00");
        assert_eq!(backup_stamp(1_794_606_174), "2026-11-13_21-42-54");
    }
}
