//! The launcher window. All behaviour lives in [`crate::model`]; this file
//! only draws the model and forwards clicks to it. Every word on screen comes
//! from [`crate::i18n`].

use std::time::{Duration, SystemTime};

use gpui::prelude::*;
use std::net::{IpAddr, Ipv4Addr, SocketAddr, UdpSocket};

use gpui::{
    AnyElement, App, ClickEvent, Context, Div, FocusHandle, FontWeight, Img, KeyDownEvent, MouseButton, ObjectFit,
    Rgba, SharedString, Stateful, Svg, Window, WindowControlArea, div, img, px, relative, rgb, svg,
};
use tsmp_core::packs::{PackSet, pack_name};
use tsmp_net::protocol::DEFAULT_PORT;
use tsmp_net::live::LiveStatus;
use tsmp_net::protocol::GuestStatus;

use crate::i18n::{I18n, tr};
use crate::model::{
    Env, GameState, HostScreen, HostStage, JoinScreen, JoinStage, JoinStep, Model, Plan, Preview, Running, Screen,
    Synced,
};

/// Lines of the mods' log shown under the session status.
const MOD_LOG_LINES: usize = 8;

// A house plan at night: deep blue paper, pale lines, one warm lamp.
const PAPER: u32 = 0x16223a;
const RAIL: u32 = 0x111b30;
const PANEL: u32 = 0x1d2c49;
const PANEL_HOVER: u32 = 0x25375a;
const LINE: u32 = 0x34486e;
const TEXT: u32 = 0xeaf0fa;
const MUTED: u32 = 0x93a4c4;
const LAMP: u32 = 0xffc857;
const LAMP_HOVER: u32 = 0xffd985;
const INK: u32 = 0x1a1503;
const GOOD: u32 = 0x63d9b0;
const WARNING: u32 = 0xff9f5a;
const DANGER: u32 = 0xff6b7a;
const CLOSE_HOVER: u32 = 0xe5484d;
const SKY: u32 = 0x8fb4ff;

const BODY_FONT: &str = "Golos Text";
const DISPLAY_FONT: &str = "Unbounded";

const RAIL_WIDTH: f32 = 236.;
const TITLEBAR_HEIGHT: f32 = 40.;
/// The left edge shared by the page, the title bar and the bottom bar.
const GUTTER: f32 = 40.;

/// The longest address worth typing: `255.255.255.255:65535`.
const ADDRESS_LENGTH: usize = 21;
/// How many members of a household a save names before counting the rest.
const NAMED_SIMS: usize = 3;
/// How many file names a review lists before summarising the rest.
const LISTED_FILES: usize = 6;

/// The steps of a session, in the order the rail shows them.
const STEPS: [&str; 5] = ["step-role", "step-sync", "step-launch", "step-load", "step-play"];

pub struct Launcher {
    model: Model,
    i18n: I18n,
    choosing_language: bool,
    /// What the player typed as the host's address, when the search finds nobody.
    address: String,
    address_focus: FocusHandle,
    address_refused: bool,
    /// This computer's address on the local network, for the other player to type.
    lan_address: Option<IpAddr>,
}

impl Launcher {
    pub fn new(cx: &mut Context<Self>) -> Self {
        cx.spawn(async move |this, cx| {
            loop {
                cx.background_executor().timer(Duration::from_millis(100)).await;
                let alive = this.update(cx, |this, cx| {
                    if this.model.pump() {
                        cx.notify();
                    }
                });
                if alive.is_err() {
                    break;
                }
            }
        })
        .detach();
        Self {
            model: Model::start(),
            i18n: I18n::load(),
            choosing_language: false,
            address: String::new(),
            address_focus: cx.focus_handle(),
            address_refused: false,
            lan_address: lan_address(),
        }
    }

    /// Typing in the address field: digits, dots and a colon, nothing else.
    fn type_address(&mut self, event: &KeyDownEvent, _: &mut Window, cx: &mut Context<Self>) {
        let keystroke = &event.keystroke;
        let typed = if keystroke.modifiers.control || keystroke.modifiers.platform {
            if keystroke.key != "v" {
                return;
            }
            cx.read_from_clipboard().and_then(|item| item.text()).unwrap_or_default()
        } else {
            match keystroke.key.as_str() {
                "backspace" => {
                    self.address.pop();
                    String::new()
                }
                "enter" => return self.connect_to_address(cx),
                _ => keystroke.key_char.clone().unwrap_or_default(),
            }
        };
        let allowed = typed.chars().filter(|c| c.is_ascii_digit() || matches!(c, '.' | ':'));
        self.address.extend(allowed.take(ADDRESS_LENGTH.saturating_sub(self.address.len())));
        self.address_refused = false;
        cx.notify();
    }

    fn connect_to_address(&mut self, cx: &mut Context<Self>) {
        match parse_address(&self.address) {
            Some(address) => self.model.connect(address),
            None => self.address_refused = true,
        }
        cx.notify();
    }

    /// A click handler that changes the model and redraws.
    fn act(
        cx: &mut Context<Self>,
        action: impl Fn(&mut Model) + 'static,
    ) -> impl Fn(&ClickEvent, &mut Window, &mut App) + 'static {
        cx.listener(move |this, _, _, cx| {
            action(&mut this.model);
            cx.notify();
        })
    }

    /// The last lines the mods of both games logged.
    fn mod_log(&self) -> Option<Div> {
        let lines = &self.model.live_log;
        if lines.is_empty() {
            return None;
        }
        let from = lines.len().saturating_sub(MOD_LOG_LINES);
        Some(
            div()
                .mt_1()
                .flex()
                .flex_col()
                .children(lines[from..].iter().map(|line| small(line.clone()).text_size(px(12.)).truncate())),
        )
    }

    /// How many of [`STEPS`] are behind the player.
    fn steps_done(&self) -> usize {
        if let GameState::Running { .. } = self.model.game {
            return if self.model.live.is_some_and(|live| live.is_live()) { 5 } else { 3 };
        }
        match &self.model.screen {
            Screen::Host(HostScreen {
                stage: HostStage::Running(running),
                ..
            }) if running.guests.iter().any(|guest| matches!(guest.status, Some(GuestStatus::Ready))) => 2,
            Screen::Join(JoinScreen {
                stage: JoinStage::Ready(_),
                ..
            }) => 2,
            Screen::Host(HostScreen {
                stage: HostStage::Setup,
                ..
            })
            | Screen::Join(JoinScreen {
                stage: JoinStage::Browsing,
                ..
            }) => 0,
            Screen::Host(_) | Screen::Join(_) => 1,
            Screen::Loading | Screen::Home | Screen::Fatal(_) => 0,
        }
    }

    fn rail(&self, cx: &mut Context<Self>) -> Div {
        let t = &self.i18n;
        let done = self.steps_done();
        let mut steps = div().flex().flex_col().px_6().pt_6();
        for (index, key) in STEPS.iter().enumerate() {
            let (ring, fill, label) = match index {
                i if i < done => (GOOD, None, MUTED),
                i if i == done => (LAMP, Some(LAMP), TEXT),
                _ => (LINE, None, MUTED),
            };
            let marker = div()
                .size(px(22.))
                .flex_none()
                .flex()
                .items_center()
                .justify_center()
                .rounded_full()
                .border_1()
                .border_color(rgb(ring))
                .when_some(fill, |this, fill| this.bg(rgb(fill)))
                .text_size(px(11.))
                .font_weight(FontWeight::SEMIBOLD)
                .text_color(rgb(if fill.is_some() { INK } else { MUTED }))
                .child(if index < done {
                    icon("check", 12., GOOD).into_any_element()
                } else {
                    (index + 1).to_string().into_any_element()
                });
            let last = index == STEPS.len() - 1;
            steps = steps.child(
                div()
                    .flex()
                    .gap_3()
                    .child(
                        div().flex().flex_col().items_center().child(marker).when(!last, |this| {
                            this.child(
                                div().w(px(1.)).flex_1().min_h(px(18.)).bg(rgb(if index < done { GOOD } else { LINE })),
                            )
                        }),
                    )
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .pt(px(1.))
                            .when(!last, |this| this.pb(px(14.)))
                            .text_color(rgb(label))
                            .when(index == done, |this| this.font_weight(FontWeight::MEDIUM))
                            .child(tr!(t, *key)),
                    ),
            );
        }

        let player = self.model.env.as_ref().map(|env| {
            div()
                .px_6()
                .flex()
                .flex_col()
                .child(div().font_weight(FontWeight::MEDIUM).child(env.name.clone()))
                .child(small(match &env.game.version {
                    Some(version) => tr!(t, "rail-game", version = version.as_str()),
                    None => tr!(t, "rail-game-unknown"),
                }))
        });

        div()
            .w(px(RAIL_WIDTH))
            .flex_none()
            .h_full()
            .flex()
            .flex_col()
            .bg(rgb(RAIL))
            .border_r_1()
            .border_color(rgb(LINE))
            .child(
                drag_area(div())
                    .h(px(72.))
                    .flex_none()
                    .flex()
                    .items_center()
                    .px_6()
                    .font_family(DISPLAY_FONT)
                    .font_weight(FontWeight::BOLD)
                    .text_size(px(22.))
                    .text_color(rgb(LAMP))
                    .child("TSMP"),
            )
            .children(player)
            .child(steps)
            .child(div().flex_1())
            .child(
                div().p_3().child(
                    div()
                        .id("language")
                        .h(px(36.))
                        .px_3()
                        .flex()
                        .items_center()
                        .gap_2()
                        .rounded(px(8.))
                        .text_color(rgb(MUTED))
                        .cursor_pointer()
                        .hover(|style| style.bg(rgb(PANEL)).text_color(rgb(TEXT)))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.choosing_language = true;
                            cx.notify();
                        }))
                        .child(icon("globe", 16., MUTED))
                        .child(div().flex_1().min_w_0().truncate().child(t.current().name.clone()))
                        .child(icon("chevron", 14., MUTED)),
                ),
            )
    }

    /// The list of languages, over everything else. A click anywhere closes it.
    fn language_list(&self, cx: &mut Context<Self>) -> [AnyElement; 2] {
        let close = cx.listener(|this, _: &ClickEvent, _, cx| {
            this.choosing_language = false;
            cx.notify();
        });
        let backdrop = div()
            .id("language-backdrop")
            .absolute()
            .top_0()
            .left_0()
            .size_full()
            .occlude()
            .on_click(close);
        let current = &self.i18n.current().code;
        let mut list = div()
            .id("languages")
            .absolute()
            .left(px(12.))
            .bottom(px(54.))
            .w(px(RAIL_WIDTH - 24.))
            .max_h(px(372.))
            .overflow_y_scroll()
            .occlude()
            .flex()
            .flex_col()
            .p_1()
            .rounded(px(10.))
            .bg(rgb(PANEL))
            .border_1()
            .border_color(rgb(LINE))
            .shadow_lg()
            .child(
                div()
                    .px_2()
                    .pt_1()
                    .pb_2()
                    .text_size(px(12.))
                    .text_color(rgb(MUTED))
                    .child(tr!(self.i18n, "language")),
            );
        for (index, language) in self.i18n.languages().iter().enumerate() {
            let chosen = &language.code == current;
            let code = language.code.clone();
            list = list.child(
                div()
                    .id(("language", index))
                    .flex_none()
                    .h(px(32.))
                    .px_2()
                    .flex()
                    .items_center()
                    .justify_between()
                    .gap_2()
                    .rounded(px(6.))
                    .cursor_pointer()
                    .hover(|style| style.bg(rgb(PANEL_HOVER)))
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.i18n.choose(&code);
                        this.choosing_language = false;
                        cx.notify();
                    }))
                    .child(div().min_w_0().truncate().child(language.name.clone()))
                    .when(chosen, |this| this.child(icon("check", 14., LAMP))),
            );
        }
        [backdrop.into_any_element(), list.into_any_element()]
    }

    /// The strip the window is dragged by, with the window's own buttons.
    fn titlebar(&self, window: &Window) -> Div {
        let control = |name: &'static str, area: WindowControlArea, hover: u32, fallback: fn(&mut Window)| {
            div()
                .id(name)
                .group(name)
                .w(px(46.))
                .h_full()
                .flex()
                .items_center()
                .justify_center()
                .hover(move |style| style.bg(rgb(hover)))
                .window_control_area(area)
                // Windows presses these itself; elsewhere it is up to us.
                .when(!cfg!(target_os = "windows"), |this| {
                    this.on_click(move |_, window, _| fallback(window))
                })
                .child(icon(name, 16., MUTED).group_hover(name, |style| style.text_color(rgb(TEXT))))
        };
        let resize = if window.is_maximized() { "restore" } else { "maximize" };
        div()
            .h(px(TITLEBAR_HEIGHT))
            .flex_none()
            .flex()
            .child(drag_area(div()).flex_1().h_full())
            .child(control("minimize", WindowControlArea::Min, PANEL, |window| {
                window.minimize_window()
            }))
            .child(control(resize, WindowControlArea::Max, PANEL, |window| window.zoom_window()))
            .child(control("close", WindowControlArea::Close, CLOSE_HOVER, |window| {
                window.remove_window()
            }))
    }

    fn home(&self, env: &Env, cx: &mut Context<Self>) -> AnyElement {
        let t = &self.i18n;
        page(tr!(t, "home-title"), Some(tr!(t, "home-lead")))
            .when(!env.script_mods_enabled, |this| {
                this.child(notice(WARNING, tr!(t, "script-mods-off")))
            })
            .child(
                div()
                    .flex()
                    .gap_4()
                    .child(choice(
                        "host",
                        "host",
                        tr!(t, "home-host"),
                        tr!(t, "home-host-detail"),
                        Self::act(cx, Model::open_host),
                    ))
                    .child(choice(
                        "join",
                        "join",
                        tr!(t, "home-join"),
                        tr!(t, "home-join-detail"),
                        Self::act(cx, Model::open_join),
                    )),
            )
            .child(
                section(tr!(t, "home-computer")).child(
                    sheet()
                        .child(fact(tr!(t, "fact-game"), env.game.install_dir.display().to_string()))
                        .child(fact(tr!(t, "fact-mods"), env.game.mods_dir().display().to_string()))
                        .child(fact_with(tr!(t, "fact-packs"), pack_chips(t, &env.packs))),
                ),
            )
            .into_any_element()
    }

    fn host(&self, screen: &HostScreen, cx: &mut Context<Self>) -> AnyElement {
        let t = &self.i18n;
        match &screen.stage {
            HostStage::Setup => {
                let mut saves = div().flex().flex_col().gap_2().child(save_card(
                    ("save", 0usize),
                    screen.selected.is_none(),
                    picture(art("new-game")),
                    tr!(t, "save-none"),
                    vec![div().text_color(rgb(MUTED)).child(tr!(t, "save-none-detail"))],
                    Vec::new(),
                    Self::act(cx, |model| model.select_save(None)),
                ));
                for (index, save) in screen.saves.iter().enumerate() {
                    let preview = screen.previews[index].as_deref();
                    let mut foot = vec![bytes(t, save.size), ago(t, save.modified)];
                    let title = match preview.and_then(|preview| preview.about.name.clone()) {
                        Some(name) => {
                            foot.insert(0, save.file_name.clone());
                            name
                        }
                        None => save.file_name.clone(),
                    };
                    saves = saves.child(save_card(
                        ("save", index + 1),
                        screen.selected == Some(index),
                        save_picture(preview),
                        title,
                        save_lines(preview),
                        foot,
                        Self::act(cx, move |model| model.select_save(Some(index))),
                    ));
                }
                page(tr!(t, "host-setup-title"), Some(tr!(t, "host-setup-lead")))
                    .child(saves)
                    .child(
                        buttons()
                            .child(button("start", tr!(t, "host-start"), true, Self::act(cx, Model::start_hosting)))
                            .child(button("back", tr!(t, "back"), false, Self::act(cx, Model::back))),
                    )
                    .into_any_element()
            }
            HostStage::Starting => page(tr!(t, "host-reading-title"), Some(tr!(t, "host-reading-body")))
                .child(buttons().child(button("back", tr!(t, "cancel"), false, Self::act(cx, Model::back))))
                .into_any_element(),
            HostStage::Running(running) => self.hosting(screen, running, cx),
            HostStage::Failed(message) => self.failure(tr!(t, "host-failed-title"), message, cx),
        }
    }

    fn hosting(&self, screen: &HostScreen, running: &Running, cx: &mut Context<Self>) -> AnyElement {
        let t = &self.i18n;
        let mut players = div().flex().flex_col().gap_2().pt_1();
        if running.guests.is_empty() {
            players = players.child(
                hollow()
                    .flex()
                    .items_center()
                    .gap_3()
                    .p_4()
                    .text_color(rgb(MUTED))
                    .child(icon("people", 20., MUTED))
                    .child(div().flex_1().min_w_0().child(tr!(t, "players-empty"))),
            );
        }
        for guest in &running.guests {
            let (color, status) = match &guest.status {
                None => (MUTED, tr!(t, "guest-connected")),
                Some(GuestStatus::Syncing {
                    done_bytes,
                    total_bytes,
                }) => (
                    WARNING,
                    tr!(t, "guest-syncing", done = bytes(t, *done_bytes), total = bytes(t, *total_bytes)),
                ),
                Some(GuestStatus::Ready) => (GOOD, tr!(t, "guest-ready")),
                Some(GuestStatus::Failed { message }) => {
                    (DANGER, tr!(t, "guest-failed", message = message.as_str()))
                }
            };
            players = players.child(
                card()
                    .flex()
                    .items_center()
                    .gap_3()
                    .child(avatar(&guest.hello.name))
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .child(div().truncate().font_weight(FontWeight::MEDIUM).child(guest.hello.name.clone()))
                            .child(small(guest.address.ip().to_string()))
                            .child(small(describe_packs(t, &guest.hello.packs))),
                    )
                    .child(status_line(color, status).flex_none().max_w(px(280.))),
            );
        }
        page(tr!(t, "host-title"), Some(tr!(t, "host-lead")))
            .when(!running.has_mod, |this| this.child(notice(WARNING, tr!(t, "host-mod-missing"))))
            .when(!running.set_aside.is_empty(), |this| {
                let names = running.set_aside.join(", ");
                this.child(notice(WARNING, tr!(t, "host-rivals-moved", names = names.as_str())))
            })
            .child(section(tr!(t, "host-save")).child(div().pt_1().child(match &running.save {
                Some(file_name) => shared_save(file_name, screen.selected_preview().map(|preview| &**preview)),
                None => card()
                    .flex()
                    .items_center()
                    .gap_4()
                    .child(picture(art("new-game")))
                    .child(div().flex_1().min_w_0().child(tr!(t, "host-save-none"))),
            })))
            .child(section(tr!(t, "players")).child(players))
            .child(
                sheet()
                    .children(self.lan_address.map(|address| fact(tr!(t, "host-address"), address.to_string())))
                    .child(fact(tr!(t, "host-port"), running.port.to_string()))
                    .child(fact(
                        tr!(t, "host-mods"),
                        tr!(t, "host-mods-value", files = running.mod_files, size = bytes(t, running.mod_bytes)),
                    ))
                    .children(self.session_packs()),
            )
            .child(buttons().child(button("back", tr!(t, "host-stop"), false, Self::act(cx, Model::back))))
            .into_any_element()
    }

    fn join(&self, screen: &JoinScreen, window: &Window, cx: &mut Context<Self>) -> AnyElement {
        let t = &self.i18n;
        match &screen.stage {
            JoinStage::Browsing => {
                let mut hosts = div().flex().flex_col().gap_2();
                for (index, host) in screen.hosts.iter().enumerate() {
                    let address = host.found.address;
                    let name = &host.found.beacon.name;
                    hosts = hosts.child(
                        card()
                            .id(("host", index))
                            .flex()
                            .items_center()
                            .gap_3()
                            .cursor_pointer()
                            .hover(|style| style.bg(rgb(PANEL_HOVER)).border_color(rgb(LAMP)))
                            .on_click(Self::act(cx, move |model| model.connect(address)))
                            .child(avatar(name))
                            .child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .child(div().truncate().font_weight(FontWeight::MEDIUM).child(name.clone()))
                                    .child(small(match &host.found.beacon.game_version {
                                        Some(version) => tr!(t, "rail-game", version = version.as_str()),
                                        None => tr!(t, "rail-game-unknown"),
                                    })),
                            )
                            .child(small(address.ip().to_string()).flex_none())
                            .child(icon("arrow", 16., MUTED)),
                    );
                }
                if screen.hosts.is_empty() {
                    hosts = hosts.child(
                        hollow()
                            .flex()
                            .flex_col()
                            .items_center()
                            .gap_2()
                            .px_6()
                            .pt_6()
                            .pb_8()
                            .child(art("search").w(px(240.)).h(px(120.)))
                            .child(div().text_center().text_color(rgb(MUTED)).child(tr!(t, "join-searching"))),
                    );
                }
                let focused = self.address_focus.is_focused(window);
                let field = div()
                    .id("address")
                    .track_focus(&self.address_focus)
                    .on_key_down(cx.listener(Self::type_address))
                    .w(px(240.))
                    .h(px(38.))
                    .px_3()
                    .flex()
                    .items_center()
                    .rounded(px(8.))
                    .bg(rgb(RAIL))
                    .border_1()
                    .border_color(rgb(if self.address_refused {
                        DANGER
                    } else if focused {
                        LAMP
                    } else {
                        LINE
                    }))
                    .cursor_text()
                    .child(if self.address.is_empty() && !focused {
                        div().text_color(rgb(MUTED)).child("192.168.0.10")
                    } else {
                        div().child(self.address.clone())
                    })
                    .when(focused, |this| this.child(div().w(px(1.)).h(px(18.)).bg(rgb(LAMP))));
                let by_address = div()
                    .flex()
                    .flex_col()
                    .gap_2()
                    .pt_1()
                    .child(div().flex().gap_3().child(field).child(button(
                        "connect",
                        tr!(t, "join-manual-connect"),
                        true,
                        cx.listener(|this, _, _, cx| this.connect_to_address(cx)),
                    )))
                    .child(if self.address_refused {
                        notice(DANGER, tr!(t, "join-manual-bad"))
                    } else {
                        small(tr!(t, "join-manual-detail"))
                    });
                page(tr!(t, "join-title"), None)
                    .child(hosts)
                    .child(section(tr!(t, "join-manual")).child(by_address))
                    .child(buttons().child(button("back", tr!(t, "back"), false, Self::act(cx, Model::back))))
                    .into_any_element()
            }
            JoinStage::Working(step) => page(
                tr!(t, "join-working-title"),
                Some(match step {
                    JoinStep::Connecting => tr!(t, "join-connecting"),
                    JoinStep::Comparing => tr!(t, "join-comparing"),
                }),
            )
            .child(buttons().child(button("back", tr!(t, "cancel"), false, Self::act(cx, Model::back))))
            .into_any_element(),
            JoinStage::Review(plan) => self.review(screen, plan, cx),
            JoinStage::Syncing(progress) => {
                let fraction = if progress.total_bytes == 0 {
                    0.
                } else {
                    progress.done_bytes as f32 / progress.total_bytes as f32
                };
                page(tr!(t, "syncing-title"), None)
                    .child(
                        div().h(px(6.)).w_full().rounded_full().bg(rgb(PANEL)).child(
                            div()
                                .h_full()
                                .w(relative(fraction.clamp(0., 1.)))
                                .rounded_full()
                                .bg(rgb(LAMP)),
                        ),
                    )
                    .child(
                        div()
                            .child(tr!(
                                t,
                                "syncing-progress",
                                done = bytes(t, progress.done_bytes),
                                total = bytes(t, progress.total_bytes)
                            ))
                            .child(small(progress.current.clone())),
                    )
                    .into_any_element()
            }
            JoinStage::Ready(synced) => self.ready(screen, synced, cx),
            JoinStage::Failed(message) => self.failure(tr!(t, "join-failed-title"), message, cx),
        }
    }

    fn host_name(&self, screen: &JoinScreen) -> String {
        match &screen.host {
            Some(host) => host.name.clone(),
            None => tr!(self.i18n, "host-unnamed"),
        }
    }

    fn review(&self, screen: &JoinScreen, plan: &Plan, cx: &mut Context<Self>) -> AnyElement {
        let t = &self.i18n;
        let ours = self.model.env.as_ref().and_then(|env| env.game.version.clone());
        let theirs = screen.host.as_ref().and_then(|host| host.game_version.clone());
        let unknown = tr!(t, "version-unknown");
        let mut changes = sheet();
        if !plan.diff.fetch.is_empty() {
            changes = changes.child(change(
                "download",
                tr!(t, "review-download", n = plan.diff.fetch.len(), size = bytes(t, plan.diff.fetch_bytes)),
                Some(file_list(t, &plan.diff.fetch)),
            ));
        }
        if !plan.diff.extra.is_empty() {
            changes = changes.child(change(
                "archive",
                tr!(t, "review-move", n = plan.diff.extra.len()),
                Some(file_list(t, &plan.diff.extra)),
            ));
        }
        if let (true, Some(save)) = (plan.save_needed, &plan.session.save) {
            changes = changes.child(change(
                "file",
                tr!(t, "review-save", name = save.file_name.as_str(), size = bytes(t, save.size)),
                None,
            ));
        }
        page(
            tr!(t, "review-title", host = self.host_name(screen)),
            Some(tr!(t, "review-keep")),
        )
        .child(changes)
        .child(notice(WARNING, tr!(t, "review-trust")))
        .when(ours != theirs, |this| {
            this.child(notice(
                WARNING,
                tr!(
                    t,
                    "review-versions",
                    ours = ours.as_deref().unwrap_or(&unknown),
                    theirs = theirs.as_deref().unwrap_or(&unknown)
                ),
            ))
        })
        .child(
            buttons()
                .child(button("sync", tr!(t, "sync"), true, Self::act(cx, Model::confirm_sync)))
                .child(button("back", tr!(t, "cancel"), false, Self::act(cx, Model::back))),
        )
        .into_any_element()
    }

    fn ready(&self, screen: &JoinScreen, synced: &Synced, cx: &mut Context<Self>) -> AnyElement {
        let t = &self.i18n;
        let changed = synced.fetched + synced.moved > 0;
        let mut summary = sheet().child(change(
            "check",
            if changed {
                tr!(t, "ready-changed", fetched = synced.fetched, moved = synced.moved)
            } else {
                tr!(t, "ready-matched")
            },
            changed.then(|| {
                small(tr!(
                    t,
                    "ready-backup",
                    path = format!("Mods.tsmp-backup\\{}", synced.backup_stamp)
                ))
            }),
        ));
        if let Some(save) = &synced.save {
            let key = if synced.save_updated { "ready-save-downloaded" } else { "ready-save-matched" };
            summary = summary.child(change("file", tr!(t, key, name = save.as_str()), None));
        }
        page(tr!(t, "ready-title", host = self.host_name(screen)), None)
            .children(
                synced
                    .save
                    .as_ref()
                    .map(|file_name| shared_save(file_name, synced.save_preview.as_deref())),
            )
            .child(summary.children(self.session_packs()))
            .child(buttons().child(button("back", tr!(t, "leave"), false, Self::act(cx, Model::back))))
            .into_any_element()
    }

    /// Which packs the session uses and which of ours get switched off.
    fn session_packs(&self) -> Vec<Div> {
        let t = &self.i18n;
        let Some(env) = self.model.env.as_ref() else {
            return Vec::new();
        };
        let common = self.model.session_packs();
        let disabled: PackSet = env.packs.difference(&common).cloned().collect();
        let mut facts = vec![fact_with(tr!(t, "session-packs"), pack_chips(t, &common))];
        if !disabled.is_empty() {
            facts.push(fact_with(tr!(t, "session-packs-off"), pack_chips(t, &disabled).opacity(0.6)));
        }
        facts
    }

    fn failure(&self, title: String, message: &str, cx: &mut Context<Self>) -> AnyElement {
        page(title, None)
            .child(notice(DANGER, message.to_string()))
            .child(buttons().child(button("back", tr!(self.i18n, "back"), false, Self::act(cx, Model::back))))
            .into_any_element()
    }

    /// The bar about the game itself: its state and the button that starts it.
    fn footer(&self, cx: &mut Context<Self>) -> Option<Div> {
        let t = &self.i18n;
        let can_launch = self.model.can_launch();
        let (color, text) = match &self.model.game {
            GameState::NotStarted if !can_launch => return None,
            GameState::NotStarted => (MUTED, tr!(t, "game-ready")),
            GameState::Starting { via_steam: true, .. } => (WARNING, tr!(t, "game-starting-steam")),
            GameState::Starting { .. } => (WARNING, tr!(t, "game-starting")),
            GameState::Running { .. } if self.model.game.packs_match() == Some(true) => {
                (GOOD, tr!(t, "game-running"))
            }
            GameState::Running { expected, loaded } => (
                DANGER,
                tr!(
                    t,
                    "game-wrong-packs",
                    loaded = describe_packs(t, loaded),
                    expected = describe_packs(t, expected)
                ),
            ),
            GameState::Failed(message) => (DANGER, tr!(t, "game-failed", message = message.as_str())),
        };
        let live = match (&self.model.game, self.model.live) {
            (GameState::Running { .. }, Some(status)) => Some(describe_live(&status)),
            _ => None,
        };
        Some(
            div()
                .flex_none()
                .flex()
                .items_center()
                .justify_between()
                .gap_6()
                .px(px(GUTTER))
                .py_4()
                .border_t_1()
                .border_color(rgb(LINE))
                .child(
                    div()
                        .flex_1()
                        .min_w_0()
                        .flex()
                        .flex_col()
                        .gap_1()
                        .child(status_line(color, text))
                        .children(live.map(|(color, key)| status_line(color, tr!(t, key))))
                        .children(live.is_some().then(|| self.mod_log()).flatten()),
                )
                .when(can_launch, |this| {
                    this.child(button("launch", tr!(t, "game-start"), true, Self::act(cx, Model::launch_game)))
                }),
        )
    }
}

impl Render for Launcher {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let t = &self.i18n;
        let loading = || page(tr!(t, "loading-title"), Some(tr!(t, "loading-body"))).into_any_element();
        let body = match (&self.model.screen, &self.model.env) {
            (Screen::Fatal(message), _) => page(tr!(t, "fatal-title"), Some(tr!(t, "fatal-hint")))
                .child(notice(DANGER, message.clone()))
                .into_any_element(),
            (Screen::Host(screen), _) => self.host(screen, cx),
            (Screen::Join(screen), _) => self.join(screen, window, cx),
            (Screen::Home, Some(env)) => self.home(env, cx),
            (Screen::Home | Screen::Loading, _) => loading(),
        };
        div()
            .relative()
            .size_full()
            .flex()
            .bg(rgb(PAPER))
            .text_color(rgb(TEXT))
            .font_family(BODY_FONT)
            .text_size(px(14.))
            .line_height(relative(1.45))
            .child(self.rail(cx))
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .h_full()
                    .flex()
                    .flex_col()
                    .child(self.titlebar(window))
                    .child(
                        div()
                            .id("body")
                            .flex_1()
                            .min_h_0()
                            .overflow_y_scroll()
                            .px(px(GUTTER))
                            .pt_4()
                            .pb_8()
                            .child(div().max_w(px(620.)).child(body)),
                    )
                    .children(self.footer(cx)),
            )
            .when(self.choosing_language, |this| this.children(self.language_list(cx)))
    }
}

/// Makes an element move the window when dragged.
fn drag_area(element: Div) -> Div {
    element
        .window_control_area(WindowControlArea::Drag)
        // Windows moves the window itself; elsewhere it is up to us.
        .when(!cfg!(target_os = "windows"), |this| {
            this.on_mouse_down(MouseButton::Left, |_, window, _| window.start_window_move())
        })
}

fn icon(name: &str, size: f32, color: u32) -> Svg {
    svg()
        .path(format!("icons/{name}.svg"))
        .size(px(size))
        .flex_none()
        .text_color(rgb(color))
}

/// A screen: what it is about, then its parts.
fn page(title: String, lead: Option<String>) -> Div {
    div().flex().flex_col().gap_6().child(
        div()
            .flex()
            .flex_col()
            .gap_2()
            .child(
                div()
                    .font_family(DISPLAY_FONT)
                    .font_weight(FontWeight::MEDIUM)
                    .text_size(px(24.))
                    .line_height(relative(1.25))
                    .child(title),
            )
            .children(lead.map(|lead| div().text_size(px(15.)).text_color(rgb(MUTED)).child(lead))),
    )
}

/// A titled part of a screen.
fn section(title: String) -> Div {
    div()
        .flex()
        .flex_col()
        .gap_1()
        .child(div().font_weight(FontWeight::SEMIBOLD).child(title))
}

/// Ruled lines, like a list on paper: for facts and changes.
fn sheet() -> Div {
    div().flex().flex_col().border_t_1().border_color(rgb(LINE))
}

/// A label and its value on one line of a [`sheet`].
fn fact(label: String, value: String) -> Div {
    fact_with(label, div().child(value))
}

fn fact_with(label: String, value: Div) -> Div {
    div()
        .flex()
        .gap_4()
        .py(px(10.))
        .border_b_1()
        .border_color(rgb(LINE))
        .child(div().w(px(150.)).flex_none().text_color(rgb(MUTED)).child(label))
        .child(value.flex_1().min_w_0())
}

/// Packs as labels, named where the name is known, with a dot for the kind.
fn pack_chips(t: &I18n, packs: &PackSet) -> Div {
    if packs.is_empty() {
        return div().child(tr!(t, "packs-none"));
    }
    let mut chips = div().flex().flex_wrap().gap(px(6.));
    for code in packs {
        let color = match code.get(..2) {
            Some("EP") => LAMP,
            Some("GP") => GOOD,
            Some("SP") => SKY,
            _ => MUTED,
        };
        chips = chips.child(
            div()
                .flex()
                .items_center()
                .gap(px(6.))
                .h(px(24.))
                .px_2()
                .rounded(px(6.))
                .bg(rgb(PANEL))
                .text_size(px(13.))
                .child(div().size(px(6.)).flex_none().rounded_full().bg(rgb(color)))
                .child(pack_name(code).map_or_else(|| code.clone(), str::to_string)),
        );
    }
    chips
}

/// A raised block: a player, a host, a save.
fn card() -> Div {
    div().p_3().rounded(px(12.)).bg(rgb(PANEL)).border_1().border_color(rgb(LINE))
}

/// A dashed outline where something is still missing.
fn hollow() -> Div {
    div().rounded(px(12.)).border_1().border_dashed().border_color(rgb(LINE))
}

/// A player, as the first letter of their computer's name.
fn avatar(name: &str) -> Div {
    let letter: String = name.trim().chars().next().map(|c| c.to_uppercase().collect()).unwrap_or_default();
    div()
        .size(px(38.))
        .flex_none()
        .flex()
        .items_center()
        .justify_center()
        .rounded_full()
        .bg(Rgba { a: 0.14, ..rgb(LAMP) })
        .font_family(DISPLAY_FONT)
        .font_weight(FontWeight::MEDIUM)
        .text_color(rgb(LAMP))
        .child(letter)
}

/// One of the launcher's own drawings.
fn art(name: &str) -> Img {
    img(format!("art/{name}.svg"))
}

/// A picture the size of a save's portrait.
fn picture(image: Img) -> Img {
    image.w(px(132.)).h(px(88.)).flex_none().rounded(px(8.)).object_fit(ObjectFit::Cover)
}

/// The household of a save, or a drawing when the save holds no portrait.
fn save_picture(preview: Option<&Preview>) -> Img {
    picture(match preview.and_then(|preview| preview.portrait.clone()) {
        Some(portrait) => img(portrait),
        None => art("no-picture"),
    })
}

/// Who lives in a save and where, one line each.
fn save_lines(preview: Option<&Preview>) -> Vec<Div> {
    let Some(about) = preview.map(|preview| &preview.about) else {
        return Vec::new();
    };
    let line = |picture: &'static str, text: String| {
        div()
            .flex()
            .items_center()
            .gap(px(6.))
            .child(icon(picture, 14., MUTED))
            .child(div().flex_1().min_w_0().truncate().child(text))
    };
    let mut lines = Vec::new();
    if !about.sims.is_empty() {
        let mut names = about.sims.iter().take(NAMED_SIMS).cloned().collect::<Vec<_>>().join(", ");
        if about.sims.len() > NAMED_SIMS {
            names.push_str(&format!(" +{}", about.sims.len() - NAMED_SIMS));
        }
        lines.push(line("people", names));
    }
    let place = match (&about.lot, about.funds) {
        (Some(lot), Some(funds)) => Some(format!("{lot}, {}", simoleons(funds))),
        (Some(lot), None) => Some(lot.clone()),
        (None, Some(funds)) => Some(simoleons(funds)),
        (None, None) => None,
    };
    lines.extend(place.map(|place| line("house", place).text_color(rgb(MUTED))));
    lines
}

/// An address as a player types it: with the port, or without for the usual one.
fn parse_address(text: &str) -> Option<SocketAddr> {
    let text = text.trim();
    text.parse().ok().or_else(|| Some(SocketAddr::new(text.parse().ok()?, DEFAULT_PORT)))
}

/// This computer's address on the local network. Nothing is sent: a UDP socket
/// only asks the system which of its addresses it would send from.
fn lan_address() -> Option<IpAddr> {
    let socket = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0)).ok()?;
    [[192, 168, 0, 1], [10, 0, 0, 1], [172, 16, 0, 1], [8, 8, 8, 8]]
        .into_iter()
        .filter_map(|target| {
            socket.connect((Ipv4Addr::from(target), 9)).ok()?;
            Some(socket.local_addr().ok()?.ip())
        })
        .find(|address| !address.is_loopback() && !address.is_unspecified())
}

/// Household funds the way the game writes them: `§12 500`.
fn simoleons(funds: u64) -> String {
    let digits = funds.to_string();
    let mut text = String::from("§");
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index) % 3 == 0 {
            text.push(' ');
        }
        text.push(digit);
    }
    text
}

/// A save to pick on the host's first screen.
fn save_card(
    id: (&'static str, usize),
    chosen: bool,
    picture: Img,
    title: String,
    lines: Vec<Div>,
    foot: Vec<String>,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    card()
        .id(id)
        .flex()
        .items_center()
        .gap_4()
        .pr_4()
        .border_color(rgb(if chosen { LAMP } else { LINE }))
        .cursor_pointer()
        .hover(|style| style.bg(rgb(PANEL_HOVER)))
        .on_click(on_click)
        .child(picture)
        .child(
            div()
                .flex_1()
                .min_w_0()
                .flex()
                .flex_col()
                .gap(px(3.))
                .child(div().truncate().text_size(px(15.)).font_weight(FontWeight::SEMIBOLD).child(title))
                .children(lines)
                .when(!foot.is_empty(), |this| {
                    this.child(div().flex().gap_3().children(foot.into_iter().map(small)))
                }),
        )
        .child(
            div()
                .size(px(18.))
                .flex_none()
                .flex()
                .items_center()
                .justify_center()
                .rounded_full()
                .border_1()
                .border_color(rgb(if chosen { LAMP } else { MUTED }))
                .when(chosen, |this| this.child(div().size(px(8.)).rounded_full().bg(rgb(LAMP)))),
        )
}

/// The save both players load: who is in it and where they live.
fn shared_save(file_name: &str, preview: Option<&Preview>) -> Div {
    let title = preview.and_then(|preview| preview.about.name.clone());
    card()
        .flex()
        .items_center()
        .gap_4()
        .child(save_picture(preview))
        .child(
            div()
                .flex_1()
                .min_w_0()
                .flex()
                .flex_col()
                .gap(px(3.))
                .child(
                    div()
                        .truncate()
                        .text_size(px(15.))
                        .font_weight(FontWeight::SEMIBOLD)
                        .child(title.clone().unwrap_or_else(|| file_name.to_string())),
                )
                .children(save_lines(preview))
                .when(title.is_some(), |this| this.child(small(file_name.to_string()))),
        )
        .children(
            preview
                .and_then(|preview| preview.lot_picture.clone())
                .map(|lot| img(lot).size(px(88.)).flex_none().rounded(px(8.)).object_fit(ObjectFit::Cover)),
        )
}

/// One thing that will change or has changed, on a [`sheet`].
fn change(picture: &'static str, text: String, detail: Option<Div>) -> Div {
    div()
        .flex()
        .gap_3()
        .py(px(10.))
        .border_b_1()
        .border_color(rgb(LINE))
        .child(div().pt(px(2.)).child(icon(picture, 16., LAMP)))
        .child(div().flex_1().min_w_0().flex().flex_col().gap_1().child(text).children(detail))
}

fn buttons() -> Div {
    div().flex().gap_3().pt_2()
}

fn small(text: impl Into<SharedString>) -> Div {
    div().text_size(px(13.)).text_color(rgb(MUTED)).child(text.into())
}

/// A coloured dot and what it stands for.
fn status_line(color: u32, text: String) -> Div {
    div()
        .flex()
        .gap_2()
        .child(div().mt(px(7.)).size(px(7.)).flex_none().rounded_full().bg(rgb(color)))
        .child(div().min_w_0().child(text))
}

/// Something the player has to know before going on.
fn notice(color: u32, text: String) -> Div {
    div()
        .flex()
        .gap_3()
        .p_3()
        .rounded(px(8.))
        .bg(Rgba { a: 0.1, ..rgb(color) })
        .child(div().pt(px(2.)).child(icon("alert", 16., color)))
        .child(div().flex_1().min_w_0().child(text))
}

fn file_list(t: &I18n, paths: &[String]) -> Div {
    let mut list = div().flex().flex_col().text_size(px(13.)).text_color(rgb(MUTED));
    for path in paths.iter().take(LISTED_FILES) {
        list = list.child(path.clone());
    }
    if paths.len() > LISTED_FILES {
        list = list.child(tr!(t, "review-more", n = paths.len() - LISTED_FILES));
    }
    list
}

fn button(
    id: &'static str,
    label: String,
    primary: bool,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    let (background, hover, text, border) = if primary {
        (LAMP, LAMP_HOVER, INK, LAMP)
    } else {
        (PAPER, PANEL, TEXT, LINE)
    };
    div()
        .id(id)
        .flex_none()
        .h(px(38.))
        .px_5()
        .flex()
        .items_center()
        .rounded(px(8.))
        .bg(rgb(background))
        .text_color(rgb(text))
        .font_weight(if primary { FontWeight::SEMIBOLD } else { FontWeight::MEDIUM })
        .border_1()
        .border_color(rgb(border))
        .cursor_pointer()
        .hover(move |style| style.bg(rgb(hover)))
        .on_click(on_click)
        .child(label)
}

/// One of the two ways into a session on the home screen.
fn choice(
    id: &'static str,
    picture: &'static str,
    title: String,
    detail: String,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    div()
        .id(id)
        .flex_1()
        .min_w_0()
        .flex()
        .flex_col()
        .rounded(px(14.))
        .bg(rgb(PANEL))
        .border_1()
        .border_color(rgb(LINE))
        .cursor_pointer()
        .hover(|style| style.bg(rgb(PANEL_HOVER)).border_color(rgb(LAMP)))
        .on_click(on_click)
        .child(
            art(picture)
                .w_full()
                .h(px(124.))
                .rounded_t(px(13.))
                .object_fit(ObjectFit::Cover),
        )
        .child(
            div()
                .flex()
                .flex_col()
                .gap_1()
                .px_5()
                .pt_4()
                .pb_5()
                .child(div().text_size(px(17.)).font_weight(FontWeight::SEMIBOLD).child(title))
                .child(div().text_color(rgb(MUTED)).child(detail)),
        )
}

fn describe_packs(t: &I18n, packs: &PackSet) -> String {
    if packs.is_empty() {
        tr!(t, "packs-none")
    } else {
        format!(
            "{}: {}",
            tr!(t, "packs-count", n = packs.len()),
            packs.iter().cloned().collect::<Vec<_>>().join(", ")
        )
    }
}

/// Where the multiplayer session stands, as the first thing still missing.
fn describe_live(status: &LiveStatus) -> (u32, &'static str) {
    if !status.game_connected {
        (WARNING, "live-no-mod")
    } else if status.remotes == 0 {
        (WARNING, "live-no-remote")
    } else if !status.game_in_zone {
        (WARNING, "live-load-save")
    } else if !status.remote_in_zone {
        (WARNING, "live-wait-remote")
    } else {
        (GOOD, "live-on")
    }
}

fn bytes(t: &I18n, size: u64) -> String {
    const UNITS: [&str; 4] = ["size-kb", "size-mb", "size-gb", "size-tb"];
    if size < 1024 {
        return tr!(t, "size-b", n = size.to_string());
    }
    let mut value = size as f64 / 1024.;
    let mut unit = 0;
    while value >= 1024. && unit < UNITS.len() - 1 {
        value /= 1024.;
        unit += 1;
    }
    tr!(t, UNITS[unit], n = format!("{value:.1}"))
}

fn ago(t: &I18n, time: SystemTime) -> String {
    let seconds = SystemTime::now().duration_since(time).map_or(0, |d| d.as_secs());
    match seconds {
        0..60 => tr!(t, "ago-now"),
        60..3_600 => tr!(t, "ago-minutes", n = seconds / 60),
        3_600..86_400 => tr!(t, "ago-hours", n = seconds / 3_600),
        _ => tr!(t, "ago-days", n = seconds / 86_400),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::i18n::english;

    #[test]
    fn sizes_are_readable() {
        let t = english();
        assert_eq!(bytes(&t, 0), "0 B");
        assert_eq!(bytes(&t, 1023), "1023 B");
        assert_eq!(bytes(&t, 1536), "1.5 KB");
        assert_eq!(bytes(&t, 18 << 30), "18.0 GB");
    }

    #[test]
    fn live_status_names_the_first_missing_step() {
        let mut status = LiveStatus::default();
        assert_eq!(describe_live(&status).1, "live-no-mod");
        status.game_connected = true;
        assert_eq!(describe_live(&status).1, "live-no-remote");
        status.remotes = 1;
        assert_eq!(describe_live(&status).1, "live-load-save");
        status.game_in_zone = true;
        assert_eq!(describe_live(&status).1, "live-wait-remote");
        status.remote_in_zone = true;
        assert_eq!(describe_live(&status), (GOOD, "live-on"));
    }

    #[test]
    fn packs_and_times_read_naturally() {
        let t = english();
        assert_eq!(describe_packs(&t, &PackSet::new()), "Base game only");
        assert_eq!(describe_packs(&t, &["EP01".to_string()].into()), "1 pack: EP01");
        assert_eq!(ago(&t, SystemTime::now() - Duration::from_secs(2 * 86_400)), "2 days ago");
        assert_eq!(ago(&t, SystemTime::now() - Duration::from_secs(86_400)), "1 day ago");
        assert_eq!(parse_address(" 192.168.0.10 "), Some(SocketAddr::from(([192, 168, 0, 10], DEFAULT_PORT))));
        assert_eq!(parse_address("10.0.0.2:5000"), Some(SocketAddr::from(([10, 0, 0, 2], 5000))));
        assert_eq!(parse_address("192.168.0"), None);
        assert_eq!(parse_address(""), None);
        assert_eq!(simoleons(940), "§940");
        assert_eq!(simoleons(12_500), "§12 500");
        assert_eq!(simoleons(1_000_000), "§1 000 000");
    }
}
