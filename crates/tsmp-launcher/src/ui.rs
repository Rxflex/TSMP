//! The launcher window. All behaviour lives in [`crate::model`]; this file
//! only draws the model and forwards clicks to it. Every word on screen comes
//! from [`crate::i18n`].

use std::time::{Duration, Instant, SystemTime};

use gpui::prelude::*;
use std::net::{IpAddr, Ipv4Addr, UdpSocket};

use gpui::{
    Animation, AnimationElement, AnimationExt, AnyElement, App, ClickEvent, ClipboardItem, Context,
    Div, ElementId, FontWeight, Img, KeyDownEvent, MouseButton, ObjectFit, Rgba, ScrollHandle,
    SharedString, Stateful, Svg, Window, WindowControlArea, div, img, linear_color_stop,
    linear_gradient, pulsating_between, px, relative, rgb, svg,
};
use tsmp_core::packs::{PackSet, pack_name};
use tsmp_net::live::LiveStatus;
use tsmp_net::protocol::DEFAULT_PORT;
use tsmp_net::protocol::GuestStatus;
use tsmp_net::rendezvous::{ListingStatus, ServerAddress};
use tsmp_net::route::Route;

use crate::field::{TextField, Typed};
use crate::i18n::{I18n, tr};
use crate::mappings::Support;
use crate::model::{
    Env, GameState, HostScreen, HostStage, JoinScreen, JoinStage, JoinStep, Model, Plan, Preview,
    Running, Screen, Synced, clock,
};
use crate::settings;

/// How long a button says that it has copied.
const COPIED_FOR: Duration = Duration::from_millis(2500);
/// The journal shows this many of its latest lines.
const JOURNAL_SHOWN: usize = 300;
const JOURNAL_WIDTH: f32 = 540.;

// A house plan at night: deep blue paper, pale lines, one warm lamp.
const PAPER: u32 = 0x16223a;
pub(crate) const RAIL: u32 = 0x111b30;
const PANEL: u32 = 0x1d2c49;
const PANEL_HOVER: u32 = 0x25375a;
pub(crate) const LINE: u32 = 0x34486e;
const TEXT: u32 = 0xeaf0fa;
pub(crate) const MUTED: u32 = 0x93a4c4;
pub(crate) const LAMP: u32 = 0xffc857;
const LAMP_HOVER: u32 = 0xffd985;
const INK: u32 = 0x1a1503;
const GOOD: u32 = 0x63d9b0;
const WARNING: u32 = 0xff9f5a;
pub(crate) const DANGER: u32 = 0xff6b7a;
const CLOSE_HOVER: u32 = 0xe5484d;
const SKY: u32 = 0x8fb4ff;

const BODY_FONT: &str = "Golos Text";
const DISPLAY_FONT: &str = "Unbounded";

const TITLEBAR_HEIGHT: f32 = 52.;
/// The widest a page gets; wider windows keep it in the middle.
const PAGE_WIDTH: f32 = 860.;
/// The left edge shared by the page, the title bar and the bottom bar.
const GUTTER: f32 = 56.;

/// The longest address or invite worth typing.
const ADDRESS_LENGTH: usize = 120;
/// The longest server address worth typing: a full domain name and a port.
const SERVER_LENGTH: usize = 260;
/// How many members of a household a save names before counting the rest.
const NAMED_SIMS: usize = 3;
/// How many file names a review lists before summarising the rest.
const LISTED_FILES: usize = 6;

/// The steps of a session, in the order the rail shows them.
const STEPS: [&str; 5] = [
    "step-role",
    "step-sync",
    "step-launch",
    "step-load",
    "step-play",
];

pub struct Launcher {
    model: Model,
    i18n: I18n,
    choosing_language: bool,
    /// Where the page is scrolled to, and what the scroll bar last showed.
    scroll: ScrollHandle,
    scroll_shown: (f32, f32),
    /// What the player typed as the host's address or invite, when the
    /// search finds nobody.
    address: TextField,
    /// The session server a host opens the session on, for guests outside
    /// the local network.
    server: TextField,
    /// This computer's address on the local network, for the other player to type.
    lan_address: Option<IpAddr>,
    /// The door of the home screen under the pointer, and the one it just left.
    door: Option<&'static str>,
    door_left: Option<&'static str>,
    journal_open: bool,
    /// The button that copied last, and when.
    copied: Option<(&'static str, Instant)>,
}

impl Launcher {
    pub fn new(cx: &mut Context<Self>) -> Self {
        cx.spawn(async move |this, cx| {
            loop {
                cx.background_executor()
                    .timer(Duration::from_millis(100))
                    .await;
                let alive = this.update(cx, |this, cx| {
                    // The page learns its own height only once drawn, so the
                    // scroll bar follows it from here.
                    let scroll = this.scroll_position();
                    let forgotten = this
                        .copied
                        .is_some_and(|(_, at)| at.elapsed() >= COPIED_FOR);
                    if forgotten {
                        this.copied = None;
                    }
                    if this.model.pump() || scroll != this.scroll_shown || forgotten {
                        this.scroll_shown = scroll;
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
            scroll: ScrollHandle::new(),
            scroll_shown: (0., 0.),
            address: TextField::new(cx, ADDRESS_LENGTH, |c| {
                c.is_ascii_alphanumeric() || ".:-@[]".contains(c)
            }),
            server: TextField::new(cx, SERVER_LENGTH, |c| {
                c.is_ascii_alphanumeric() || ".:-[]".contains(c)
            })
            .with_text(&remembered_server()),
            lan_address: lan_address(),
            door: None,
            door_left: None,
            journal_open: false,
            copied: None,
        }
    }

    /// How much of the page is out of sight, and how far it is scrolled.
    fn scroll_position(&self) -> (f32, f32) {
        (
            f32::from(self.scroll.max_offset().height),
            -f32::from(self.scroll.offset().y),
        )
    }

    /// Shows that the page goes on below, and how far down the player is.
    fn scrollbar(&self) -> Option<Div> {
        const EDGE: f32 = 8.;
        let (hidden, scrolled) = self.scroll_position();
        let view = f32::from(self.scroll.bounds().size.height);
        if hidden < 1. || view <= EDGE * 2. {
            return None;
        }
        let track = view - EDGE * 2.;
        let thumb = (track * view / (view + hidden)).max(48.).min(track);
        let top = EDGE + (track - thumb) * (scrolled / hidden).clamp(0., 1.);
        Some(
            div()
                .absolute()
                .top(px(top))
                .right(px(6.))
                .w(px(8.))
                .h(px(thumb))
                .rounded_full()
                .bg(Rgba {
                    a: 0.55,
                    ..rgb(MUTED)
                }),
        )
    }

    fn type_address(&mut self, event: &KeyDownEvent, _: &mut Window, cx: &mut Context<Self>) {
        if self.address.key(event, cx) == Typed::Submitted {
            self.connect_to_address();
        }
        cx.notify();
    }

    fn connect_to_address(&mut self) {
        match self.address.text().parse::<Route>() {
            Ok(route) => self.model.connect(route),
            Err(_) => self.address.refuse(),
        }
    }

    fn type_server(&mut self, event: &KeyDownEvent, _: &mut Window, cx: &mut Context<Self>) {
        if self.server.key(event, cx) == Typed::Submitted {
            self.go_online();
        }
        cx.notify();
    }

    fn go_online(&mut self) {
        match self.server.text().parse::<ServerAddress>() {
            Ok(server) => {
                settings::SERVER.store(&server.to_string());
                self.model.go_online(server);
            }
            Err(_) => self.server.refuse(),
        }
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
    /// Remembers which door of the home screen the pointer is over.
    fn door_hover(
        cx: &mut Context<Self>,
        id: &'static str,
    ) -> impl Fn(&bool, &mut Window, &mut App) + 'static {
        cx.listener(move |this, inside: &bool, _, cx| {
            if *inside {
                this.door = Some(id);
            } else if this.door == Some(id) {
                this.door = None;
                this.door_left = Some(id);
            }
            cx.notify();
        })
    }

    /// Puts `text` on the clipboard when pressed, and says so for a moment.
    fn copy_button(&self, id: &'static str, text: String, cx: &mut Context<Self>) -> Stateful<Div> {
        let done = self.copied.is_some_and(|(copied, _)| copied == id);
        chip(
            id,
            if done { "check" } else { "copy" },
            tr!(self.i18n, if done { "copied" } else { "copy" }),
            done,
        )
        .on_click(cx.listener(move |this, _, _, cx| {
            cx.write_to_clipboard(ClipboardItem::new_string(text.clone()));
            this.copied = Some((id, Instant::now()));
            cx.notify();
        }))
    }

    /// Something that went wrong, in the words it came in, with a button
    /// that copies them together with the versions they happened on.
    fn error_box(&self, id: &'static str, message: &str, cx: &mut Context<Self>) -> Div {
        div()
            .flex()
            .items_start()
            .gap_3()
            .p_4()
            .rounded(px(10.))
            .bg(Rgba {
                a: 0.1,
                ..rgb(DANGER)
            })
            .child(div().pt(px(3.)).child(icon("alert", 18., DANGER)))
            .child(div().flex_1().min_w_0().child(message.to_string()))
            .child(self.copy_button(id, self.model.report(message), cx))
    }

    /// Everything the launcher and the mods have done, newest first, over
    /// the right side of the window. A click beside it closes it.
    fn journal(&self, cx: &mut Context<Self>) -> [AnyElement; 2] {
        let t = &self.i18n;
        fn close(this: &mut Launcher, _: &ClickEvent, _: &mut Window, cx: &mut Context<Launcher>) {
            this.journal_open = false;
            cx.notify();
        }
        let backdrop = div()
            .id("journal-backdrop")
            .absolute()
            .top(px(TITLEBAR_HEIGHT))
            .left_0()
            .right_0()
            .bottom_0()
            .occlude()
            .bg(Rgba {
                a: 0.45,
                ..rgb(RAIL)
            })
            .on_click(cx.listener(close));
        let words = JOURNAL_WIDTH - 2. * 20. - 46. - 12.;
        let entries = &self.model.journal;
        let mut lines = div()
            .id("journal-lines")
            .flex_1()
            .min_h_0()
            .overflow_y_scroll()
            .px(px(20.))
            .pb_5()
            .flex()
            .flex_col()
            .gap(px(6.))
            .text_size(px(14.))
            .line_height(relative(1.4));
        if entries.is_empty() {
            lines = lines.child(small(tr!(t, "journal-empty")));
        }
        for entry in entries.iter().rev().take(JOURNAL_SHOWN) {
            lines = lines.child(
                div()
                    .flex()
                    .items_start()
                    .gap(px(12.))
                    .child(
                        div()
                            .w(px(46.))
                            .flex_none()
                            .text_color(rgb(MUTED))
                            .child(clock(entry.at)),
                    )
                    .child(
                        div()
                            .w(px(words))
                            .flex_none()
                            .when(entry.bad, |this| this.text_color(rgb(DANGER)))
                            .child(entry.text.clone()),
                    ),
            );
        }
        let panel = div()
            .id("journal")
            .absolute()
            .top(px(TITLEBAR_HEIGHT))
            .right_0()
            .bottom_0()
            .w(px(JOURNAL_WIDTH))
            .occlude()
            .flex()
            .flex_col()
            .bg(rgb(PAPER))
            .border_l_1()
            .border_color(rgb(LINE))
            .shadow_lg()
            .child(
                div()
                    .flex_none()
                    .px(px(20.))
                    .py_4()
                    .flex()
                    .items_center()
                    .gap_3()
                    .child(
                        div()
                            .flex_1()
                            .font_family(DISPLAY_FONT)
                            .font_weight(FontWeight::MEDIUM)
                            .text_size(px(18.))
                            .child(tr!(t, "journal")),
                    )
                    .child(self.copy_button("copy-journal", self.model.journal_text(), cx))
                    .child(
                        div()
                            .id("journal-close")
                            .size(px(34.))
                            .flex_none()
                            .flex()
                            .items_center()
                            .justify_center()
                            .rounded(px(8.))
                            .cursor_pointer()
                            .hover(|style| style.bg(rgb(PANEL_HOVER)))
                            .on_click(cx.listener(close))
                            .child(icon("close", 16., MUTED)),
                    ),
            )
            .child(lines);
        [backdrop.into_any_element(), panel.into_any_element()]
    }

    /// How many of [`STEPS`] are behind the player.
    fn steps_done(&self) -> usize {
        if let GameState::Running { .. } = self.model.game {
            return if self.model.live.is_some_and(|live| live.is_live()) {
                5
            } else {
                3
            };
        }
        match &self.model.screen {
            Screen::Host(HostScreen {
                stage: HostStage::Running(running),
                ..
            }) if running
                .guests
                .iter()
                .any(|guest| matches!(guest.status, Some(GuestStatus::Ready))) =>
            {
                2
            }
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

    /// Which screen is up, as a number that changes when it does, and whether
    /// the launcher is in the middle of something there.
    fn scene(&self) -> (usize, bool) {
        match &self.model.screen {
            Screen::Loading => (0, true),
            Screen::Home => (1, false),
            Screen::Fatal(_) => (2, false),
            Screen::Host(screen) => match &screen.stage {
                HostStage::Setup => (10, false),
                HostStage::Starting => (11, true),
                HostStage::Running(_) => (12, false),
                HostStage::Failed(_) => (13, false),
            },
            Screen::Join(screen) => match &screen.stage {
                JoinStage::Browsing => (20, false),
                JoinStage::Working(_) => (21, true),
                JoinStage::Review(_) => (22, false),
                JoinStage::Syncing(_) => (23, true),
                JoinStage::Ready(_) => (24, false),
                JoinStage::Failed(_) => (25, false),
            },
        }
    }

    /// Where the player is on the way into a session: a bar per step, filled
    /// as far as the player has come.
    fn progress(&self, busy: bool) -> Div {
        let t = &self.i18n;
        let done = self.steps_done();
        // One line: where the players are among the steps, and what this one is.
        let mut row = div()
            .w_full()
            .max_w(px(PAGE_WIDTH))
            .mx_auto()
            .flex()
            .items_center()
            .gap(px(6.));
        for index in 0..STEPS.len() {
            let color = match index {
                i if i < done => GOOD,
                i if i == done => LAMP,
                _ => LINE,
            };
            let bar = div()
                .w(px(if index == done { 44. } else { 22. }))
                .h(px(4.))
                .flex_none()
                .rounded_full()
                .bg(rgb(color));
            row = row.child(if busy && index == done {
                pulse(bar, "step").into_any_element()
            } else {
                bar.into_any_element()
            });
        }
        let row = row
            .child(
                div()
                    .pl_3()
                    .text_size(px(14.))
                    .font_weight(FontWeight::MEDIUM)
                    .child(tr!(
                        t,
                        match STEPS[done.min(STEPS.len() - 1)] {
                            "step-load" if self.model.own_families() => "step-load-own",
                            step => step,
                        }
                    )),
            )
            .child(div().flex_1())
            .child(small(format!(
                "{} / {}",
                (done + 1).min(STEPS.len()),
                STEPS.len()
            )));
        div().flex_none().px(px(GUTTER)).pt_5().pb_1().child(row)
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
            .top(px(TITLEBAR_HEIGHT - 4.))
            .right(px(152.))
            .w(px(240.))
            .max_h(px(460.))
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
                    .text_size(px(13.))
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
                    .h(px(40.))
                    .px_3()
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
                    .when(chosen, |this| this.child(icon("check", 16., LAMP))),
            );
        }
        [backdrop.into_any_element(), list.into_any_element()]
    }

    /// The strip the window is dragged by, with the window's own buttons.
    fn titlebar(&self, window: &Window, cx: &mut Context<Self>) -> Div {
        let control =
            |name: &'static str, area: WindowControlArea, hover: u32, fallback: fn(&mut Window)| {
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
                    .child(
                        icon(name, 16., MUTED)
                            .group_hover(name, |style| style.text_color(rgb(TEXT))),
                    )
            };
        let resize = if window.is_maximized() {
            "restore"
        } else {
            "maximize"
        };
        div()
            .h(px(TITLEBAR_HEIGHT))
            .flex_none()
            .flex()
            .bg(rgb(RAIL))
            .border_b_1()
            .border_color(rgb(LINE))
            .child(
                drag_area(div())
                    .flex_1()
                    .h_full()
                    .flex()
                    .items_center()
                    .px(px(GUTTER / 2.))
                    .font_family(DISPLAY_FONT)
                    .font_weight(FontWeight::BOLD)
                    .text_size(px(20.))
                    .text_color(rgb(LAMP))
                    .child("TSMP"),
            )
            .children(self.model.update.as_ref().map(|version| {
                let restart = self.model.can_restart();
                div().h_full().flex().items_center().pr_2().child(
                    div()
                        .id("update")
                        .h(px(36.))
                        .px_3()
                        .flex()
                        .items_center()
                        .gap_2()
                        .rounded(px(8.))
                        .border_1()
                        .border_color(rgb(LAMP))
                        .text_size(px(15.))
                        .text_color(rgb(LAMP))
                        .child(icon("download", 18., LAMP))
                        .child(if restart {
                            tr!(self.i18n, "update-restart", version = version.as_str())
                        } else {
                            tr!(self.i18n, "update-ready", version = version.as_str())
                        })
                        .when(restart, |this| {
                            this.cursor_pointer()
                                .hover(|style| style.bg(rgb(PANEL)))
                                .on_click(cx.listener(|_, _, _, cx| {
                                    if crate::update::restart().is_ok() {
                                        cx.quit();
                                    }
                                }))
                        }),
                )
            }))
            .child(
                div().h_full().flex().items_center().child(
                    div()
                        .id("journal-open")
                        .h(px(36.))
                        .px_3()
                        .flex()
                        .items_center()
                        .gap_2()
                        .rounded(px(8.))
                        .text_size(px(15.))
                        .text_color(rgb(MUTED))
                        .cursor_pointer()
                        .hover(|style| style.bg(rgb(PANEL)).text_color(rgb(TEXT)))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.journal_open = !this.journal_open;
                            cx.notify();
                        }))
                        .child(icon("list", 18., MUTED))
                        .child(tr!(self.i18n, "journal")),
                ),
            )
            .child(
                div().h_full().flex().items_center().pr_2().child(
                    div()
                        .id("language")
                        .h(px(36.))
                        .px_3()
                        .flex()
                        .items_center()
                        .gap_2()
                        .rounded(px(8.))
                        .text_size(px(15.))
                        .text_color(rgb(MUTED))
                        .cursor_pointer()
                        .hover(|style| style.bg(rgb(PANEL)).text_color(rgb(TEXT)))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.choosing_language = true;
                            cx.notify();
                        }))
                        .child(icon("globe", 18., MUTED))
                        .child(self.i18n.current().name.clone()),
                ),
            )
            .child(control(
                "minimize",
                WindowControlArea::Min,
                PANEL,
                |window| window.minimize_window(),
            ))
            .child(control(resize, WindowControlArea::Max, PANEL, |window| {
                window.zoom_window()
            }))
            .child(control(
                "close",
                WindowControlArea::Close,
                CLOSE_HOVER,
                |window| window.remove_window(),
            ))
    }

    /// The first screen is one question, so it is the whole window: a door
    /// to open and a door to walk to, with this computer's facts underneath.
    fn home(&self, env: &Env, cx: &mut Context<Self>) -> Div {
        let t = &self.i18n;
        let game = match &env.game.version {
            Some(version) => tr!(t, "rail-game", version = version.as_str()),
            None => tr!(t, "rail-game-unknown"),
        };
        let packs = if env.packs.is_empty() {
            tr!(t, "packs-none")
        } else {
            tr!(t, "packs-count", n = env.packs.len())
        };
        let building = match (self.model.building, &env.game.version) {
            (None, _) => div()
                .flex()
                .gap_2()
                .child(pulse(dot(MUTED), "checking"))
                .child(div().min_w_0().child(tr!(t, "building-checking"))),
            (Some(Support::Ready), _) => status_line(GOOD, tr!(t, "building-ready")),
            (Some(Support::Missing), Some(version)) => status_line(
                WARNING,
                tr!(t, "building-missing", version = version.as_str()),
            ),
            _ => status_line(WARNING, tr!(t, "building-unknown")),
        };
        let glow = |id: &'static str| {
            if self.door == Some(id) {
                Glow::In
            } else if self.door_left == Some(id) {
                Glow::Out
            } else {
                Glow::Rest
            }
        };
        let (host_glow, join_glow) = (glow("host"), glow("join"));
        let host = door(
            "host",
            "door-host",
            "door",
            tr!(t, "home-host"),
            tr!(t, "home-host-detail"),
            host_glow,
            Self::door_hover(cx, "host"),
            Self::act(cx, Model::open_host),
        );
        let join = door(
            "join",
            "door-join",
            "people",
            tr!(t, "home-join"),
            tr!(t, "home-join-detail"),
            join_glow,
            Self::door_hover(cx, "join"),
            Self::act(cx, Model::open_join),
        );
        div()
            .flex_1()
            .min_h_0()
            .flex()
            .flex_col()
            .child(arrive(
                div()
                    .flex_none()
                    .px(px(GUTTER / 2.))
                    .pt_6()
                    .pb_5()
                    .flex()
                    .items_end()
                    .justify_between()
                    .gap_8()
                    .child(
                        div()
                            .font_family(DISPLAY_FONT)
                            .font_weight(FontWeight::MEDIUM)
                            .text_size(px(30.))
                            .line_height(relative(1.2))
                            .child(tr!(t, "home-title")),
                    )
                    .child(
                        div()
                            .max_w(px(420.))
                            .pb_1()
                            .text_color(rgb(MUTED))
                            .child(tr!(t, "home-lead")),
                    ),
                "home-head",
                0.,
                10.,
            ))
            .when(!env.script_mods_enabled, |this| {
                this.child(
                    div()
                        .px(px(GUTTER / 2.))
                        .pb_4()
                        .child(notice(WARNING, tr!(t, "script-mods-off"))),
                )
            })
            .child(
                div()
                    .flex_1()
                    .min_h_0()
                    .flex()
                    .gap(px(1.))
                    .bg(rgb(LINE))
                    .border_t_1()
                    .border_b_1()
                    .border_color(rgb(LINE))
                    .child(arrive(host, "home-host", 0.06, 0.))
                    .child(arrive(join, "home-join", 0.16, 0.)),
            )
            .child(arrive(
                div()
                    .flex_none()
                    .bg(rgb(RAIL))
                    .px(px(GUTTER / 2.))
                    .py_4()
                    .flex()
                    .gap_8()
                    .child(
                        about(tr!(t, "home-computer"), div().child(env.name.clone())).flex_none(),
                    )
                    .child(
                        about(
                            tr!(t, "fact-game"),
                            div()
                                .child(game)
                                .child(small(env.game.install_dir.display().to_string())),
                        )
                        .flex_1(),
                    )
                    .child(about(tr!(t, "fact-packs"), div().child(packs)).flex_none())
                    .child(about(tr!(t, "fact-building"), building).flex_1()),
                "home-facts",
                0.26,
                0.,
            ))
    }

    fn host(&self, screen: &HostScreen, window: &Window, cx: &mut Context<Self>) -> AnyElement {
        let t = &self.i18n;
        match &screen.stage {
            HostStage::Setup => {
                let name = |index: usize| {
                    screen.previews[index]
                        .as_deref()
                        .and_then(|preview| preview.about.name.clone())
                };
                // Saves of one household are one line of the list, and the
                // household's page tells them apart.
                let household = |index: usize| {
                    let mut sims = screen.previews[index].as_deref()?.about.sims.clone();
                    sims.sort();
                    (!sims.is_empty()).then_some(sims)
                };
                let mut groups: Vec<(Option<Vec<String>>, Vec<usize>)> = Vec::new();
                for index in 0..screen.saves.len() {
                    let sims = household(index);
                    match groups
                        .iter_mut()
                        .find(|(known, _)| sims.is_some() && *known == sims)
                    {
                        Some((_, group)) => group.push(index),
                        None => groups.push((sims, vec![index])),
                    }
                }
                let mut list = div()
                    .w(px(LIST_WIDTH))
                    .flex_none()
                    .flex()
                    .flex_col()
                    .gap(px(4.))
                    .child(save_row(
                        0,
                        screen.selected.is_none(),
                        art("new-game"),
                        tr!(t, "save-none"),
                        tr!(t, "save-none-detail"),
                        None,
                        Self::act(cx, |model| model.select_save(None)),
                    ));
                let mut pane = screen.selected.is_none().then(|| {
                    save_pane(
                        art("new-game"),
                        tr!(t, "save-none"),
                        vec![
                            div()
                                .w(px(PANE_WORDS))
                                .text_color(rgb(MUTED))
                                .child(tr!(t, "save-none-detail")),
                        ],
                        Vec::new(),
                        None,
                    )
                });
                for (sims, group) in groups {
                    let first = group[0];
                    let chosen = screen.selected.filter(|index| group.contains(index));
                    let shown = chosen.unwrap_or(first);
                    let save = &screen.saves[shown];
                    let preview = screen.previews[shown].as_deref();
                    let title = name(shown).unwrap_or_else(|| save.file_name.clone());
                    list = list.child(save_row(
                        first + 1,
                        chosen.is_some(),
                        portrait(preview),
                        title.clone(),
                        match sims {
                            Some(_) => preview
                                .map(|preview| preview.about.sims.join(", "))
                                .unwrap_or_default(),
                            None => ago(t, save.modified),
                        },
                        (group.len() > 1).then_some(group.len()),
                        Self::act(cx, move |model| model.select_save(Some(shown))),
                    ));
                    if chosen.is_none() {
                        continue;
                    }
                    let variants = (group.len() > 1).then(|| {
                        let mut variants = div()
                            .w(px(PANE_WIDTH))
                            .pt_2()
                            .flex()
                            .flex_col()
                            .gap(px(2.))
                            .child(
                                small(tr!(t, "save-variants", n = group.len()))
                                    .px(px(12.))
                                    .pb_1(),
                            );
                        for &index in &group {
                            let save = &screen.saves[index];
                            let picked = screen.selected == Some(index);
                            variants = variants.child(
                                div()
                                    .id(("variant", index))
                                    .w(px(PANE_WIDTH))
                                    .h(px(42.))
                                    .px(px(12.))
                                    .flex()
                                    .items_center()
                                    .gap_3()
                                    .rounded(px(10.))
                                    .text_size(px(15.))
                                    .cursor_pointer()
                                    .when(picked, |this| this.bg(rgb(PANEL)))
                                    .hover(|style| style.bg(rgb(PANEL_HOVER)))
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.model.select_save(Some(index));
                                        cx.notify();
                                    }))
                                    .child(
                                        div()
                                            .size(px(16.))
                                            .flex_none()
                                            .flex()
                                            .items_center()
                                            .justify_center()
                                            .rounded_full()
                                            .border_1()
                                            .border_color(rgb(if picked { LAMP } else { MUTED }))
                                            .when(picked, |this| {
                                                this.child(
                                                    div().size(px(8.)).rounded_full().bg(rgb(LAMP)),
                                                )
                                            }),
                                    )
                                    .child(div().w(px(230.)).flex_none().truncate().child(
                                        name(index).unwrap_or_else(|| save.file_name.clone()),
                                    ))
                                    .child(
                                        small(save.file_name.clone())
                                            .w(px(150.))
                                            .flex_none()
                                            .truncate(),
                                    )
                                    .child(div().flex_1())
                                    .child(small(ago(t, save.modified)).flex_none()),
                            );
                        }
                        variants
                    });
                    let mut lines = Vec::new();
                    let mut facts = Vec::new();
                    if let Some(about) = preview.map(|preview| &preview.about) {
                        lines.extend(
                            about
                                .sims
                                .iter()
                                .take(NAMED_SIMS)
                                .map(|sim| div().w(px(PANE_NAMES)).truncate().child(sim.clone())),
                        );
                        if about.sims.len() > NAMED_SIMS {
                            lines.push(small(format!("+{}", about.sims.len() - NAMED_SIMS)));
                        }
                        let fact = |picture: &'static str, text: String| {
                            div()
                                .flex()
                                .items_center()
                                .gap(px(8.))
                                .text_color(rgb(MUTED))
                                .child(icon(picture, 17., MUTED))
                                .child(div().w(px(PANE_FACTS - 25.)).truncate().child(text))
                        };
                        facts.extend(about.lot.clone().map(|lot| fact("house", lot)));
                        facts.extend(about.funds.map(|funds| fact("coins", simoleons(funds))));
                    }
                    facts.push(
                        div()
                            .pt_1()
                            .flex()
                            .gap_4()
                            .child(small(ago(t, save.modified)))
                            .child(small(bytes(t, save.size))),
                    );
                    pane = Some(save_pane(portrait(preview), title, lines, facts, variants));
                }
                let shown = screen.selected.map_or(0, |index| index + 1);
                let own = screen.own_families;
                let modes = div()
                    .flex()
                    .gap(px(MODE_GAP))
                    .child(mode_tile(
                        "mode-shared",
                        !own,
                        "house",
                        tr!(t, "mode-shared"),
                        tr!(t, "mode-shared-detail"),
                        Self::act(cx, |model| model.choose_own_families(false)),
                    ))
                    .child(mode_tile(
                        "mode-own",
                        own,
                        "people",
                        tr!(t, "mode-own"),
                        tr!(t, "mode-own-detail"),
                        Self::act(cx, |model| model.choose_own_families(true)),
                    ));
                let body = if own {
                    arrive(own_families_guide(t), "own-guide", 0., 10.).into_any_element()
                } else {
                    div()
                        .flex()
                        .flex_col()
                        .gap_5()
                        .child(
                            div()
                                .flex()
                                .flex_col()
                                .gap_1()
                                .child(
                                    div()
                                        .font_family(DISPLAY_FONT)
                                        .font_weight(FontWeight::MEDIUM)
                                        .text_size(px(19.))
                                        .child(tr!(t, "host-setup-title")),
                                )
                                .child(
                                    div()
                                        .text_color(rgb(MUTED))
                                        .child(tr!(t, "host-setup-lead")),
                                ),
                        )
                        .child(
                            div()
                                .flex()
                                .items_start()
                                .gap(px(PANE_GAP))
                                .child(list)
                                .children(pane.map(|pane| arrive(pane, ("pane", shown), 0., 10.))),
                        )
                        .into_any_element()
                };
                page(tr!(t, "host-mode-title"), None)
                    .child(modes)
                    .child(body)
                    .into_any_element()
            }
            HostStage::Starting => page(
                tr!(t, "host-reading-title"),
                Some(tr!(t, "host-reading-body")),
            )
            .child(waiting_dots())
            .into_any_element(),
            HostStage::Running(running) => self.hosting(screen, running, window, cx),
            HostStage::Failed(message) => {
                self.failure(tr!(t, "host-failed-title"), None, message, cx)
            }
        }
    }

    /// This player's own line among the players: what their game is doing.
    fn own_state(&self) -> Div {
        let t = &self.i18n;
        match &self.model.game {
            GameState::NotStarted => player_state(GOOD, tr!(t, "guest-ready")),
            GameState::Starting { .. } => player_state(WARNING, tr!(t, "player-starting")),
            GameState::Running { .. } => player_state(GOOD, tr!(t, "player-in-game")),
            GameState::Failed(_) => player_state(DANGER, tr!(t, "player-failed")),
        }
    }

    fn own_name(&self) -> String {
        self.model
            .env
            .as_ref()
            .map(|env| env.name.clone())
            .unwrap_or_default()
    }

    fn hosting(
        &self,
        screen: &HostScreen,
        running: &Running,
        window: &Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let t = &self.i18n;
        let mut players = vec![
            player_row(
                &self.own_name(),
                small(tr!(t, "player-you-host")),
                self.own_state(),
            )
            .into_any_element(),
        ];
        for (index, guest) in running.guests.iter().enumerate() {
            let mut detail = small(guest.address.ip().to_string());
            let state = match &guest.status {
                None => player_state(MUTED, tr!(t, "guest-connected")),
                Some(GuestStatus::Syncing {
                    done_bytes,
                    total_bytes,
                }) => player_state(
                    WARNING,
                    tr!(
                        t,
                        "guest-syncing",
                        done = bytes(t, *done_bytes),
                        total = bytes(t, *total_bytes)
                    ),
                ),
                Some(GuestStatus::Ready) => player_state(GOOD, tr!(t, "guest-ready")),
                Some(GuestStatus::Failed { message }) => {
                    detail = small(message.clone()).text_color(rgb(DANGER));
                    div().child(
                        self.copy_button(
                            "copy-guest",
                            self.model
                                .report(&format!("{}: {message}", guest.hello.name)),
                            cx,
                        ),
                    )
                }
            };
            players.push(
                arrive(
                    player_row(&guest.hello.name, detail, state),
                    ("guest", index),
                    0.,
                    10.,
                )
                .into_any_element(),
            );
        }
        if running.guests.is_empty() {
            players.push(
                hollow()
                    .w(px(ROSTER_WIDTH))
                    .p_4()
                    .flex()
                    .items_start()
                    .gap_3()
                    .text_size(px(15.))
                    .text_color(rgb(MUTED))
                    .child(div().pt(px(2.)).child(icon("people", 20., MUTED)))
                    .child(
                        div()
                            .w(px(ROSTER_WIDTH - 2. * 16. - 20. - 12. - 2.))
                            .child(tr!(t, "players-empty")),
                    )
                    .into_any_element(),
            );
        }
        let address = self.lan_address.map(|address| match running.port {
            DEFAULT_PORT => address.to_string(),
            port => format!("{address}:{port}"),
        });
        let notes = vec![
            brief(tr!(t, "host-address"), address.unwrap_or_default()),
            brief(
                tr!(t, "host-mods"),
                tr!(
                    t,
                    "host-mods-value",
                    files = running.mod_files,
                    size = bytes(t, running.mod_bytes)
                ),
            ),
            self.online(running, window, cx),
        ];
        let mut warnings = Vec::new();
        if !running.has_mod {
            warnings.push(notice(WARNING, tr!(t, "host-mod-missing")));
        }
        if !running.set_aside.is_empty() {
            let names = running.set_aside.join(", ");
            warnings.push(notice(
                WARNING,
                tr!(t, "host-rivals-moved", names = names.as_str()),
            ));
        }
        self.lobby(
            tr!(t, "host-title"),
            warnings,
            players,
            notes,
            running.save.as_deref(),
            screen.selected_preview().map(|preview| &**preview),
        )
    }

    /// Opening the session to players outside the local network: the server
    /// to list it on, then the invite to send them.
    fn online(&self, running: &Running, window: &Window, cx: &mut Context<Self>) -> Div {
        let t = &self.i18n;
        let body = match &running.online {
            None => div()
                .flex()
                .flex_col()
                .gap_2()
                .child(
                    div()
                        .flex()
                        .gap_3()
                        .child(
                            self.server
                                .view("server", "play.example.org", SERVER_WIDTH, window)
                                .on_key_down(cx.listener(Self::type_server)),
                        )
                        .child(button(
                            "online-open",
                            tr!(t, "online-open"),
                            false,
                            cx.listener(|this, _, _, cx| {
                                this.go_online();
                                cx.notify();
                            }),
                        )),
                )
                .child(if self.server.refused() {
                    notice(DANGER, tr!(t, "online-bad-server"))
                } else {
                    small(tr!(t, "online-detail"))
                }),
            Some(online) => {
                let server = online.server.to_string();
                let state = match &online.status {
                    ListingStatus::Connecting => status_line(
                        WARNING,
                        tr!(t, "online-connecting", server = server.as_str()),
                    ),
                    ListingStatus::Open(_) => {
                        status_line(GOOD, tr!(t, "online-ready", server = server.as_str()))
                    }
                    ListingStatus::Retrying { reason, .. } => status_line(
                        WARNING,
                        tr!(t, "online-retrying", reason = reason.as_str()),
                    ),
                };
                let mut body = div().flex().flex_col().gap_2().child(state);
                if let Some(invite) = online.status.invite() {
                    let invite = invite.to_string();
                    body = body
                        .child(
                            div()
                                .flex()
                                .items_center()
                                .gap_3()
                                .child(
                                    div()
                                        .flex_1()
                                        .min_w_0()
                                        .truncate()
                                        .font_family(DISPLAY_FONT)
                                        .font_weight(FontWeight::MEDIUM)
                                        .text_size(px(17.))
                                        .text_color(rgb(LAMP))
                                        .child(invite.clone()),
                                )
                                .child(self.copy_button("copy-invite", invite, cx)),
                        )
                        .child(small(tr!(t, "online-invite-detail")));
                }
                body.child(
                    div().flex().child(
                        chip("online-stop", "close", tr!(t, "online-stop"), false)
                            .on_click(Self::act(cx, |model| model.go_offline())),
                    ),
                )
            }
        };
        div()
            .w(px(ROSTER_WIDTH))
            .pt_4()
            .flex()
            .flex_col()
            .gap_2()
            .child(
                div()
                    .text_size(px(15.))
                    .text_color(rgb(MUTED))
                    .child(tr!(t, "online")),
            )
            .child(body)
    }

    /// Where both players wait for each other before the game: who is here
    /// and what state they are in, beside the save they are about to load.
    fn lobby(
        &self,
        title: String,
        warnings: Vec<Div>,
        players: Vec<AnyElement>,
        notes: Vec<Div>,
        save: Option<&str>,
        preview: Option<&Preview>,
    ) -> AnyElement {
        let t = &self.i18n;
        let own = self.model.own_families();
        let hosting = matches!(self.model.screen, Screen::Host(_));
        let label = |text: String| {
            div()
                .pb_1()
                .text_size(px(15.))
                .text_color(rgb(MUTED))
                .child(text)
        };
        div()
            .flex()
            .flex_col()
            .gap_6()
            .child(heading(title, Some(tr!(t, "host-lead"))))
            .children(warnings)
            .child(
                div()
                    .flex()
                    .items_start()
                    .gap(px(LOBBY_GAP))
                    .child(
                        div()
                            .w(px(ROSTER_WIDTH))
                            .flex_none()
                            .flex()
                            .flex_col()
                            .gap(px(8.))
                            .child(label(tr!(t, "players")))
                            .children(players)
                            .child(div().pt_3().flex().flex_col().gap(px(6.)).children(notes)),
                    )
                    .child(
                        div()
                            .w(px(SAVE_WIDTH))
                            .flex_none()
                            .flex()
                            .flex_col()
                            .gap(px(8.))
                            .child(label(tr!(t, if own { "mode-label" } else { "host-save" })))
                            .child(if own {
                                lobby_own(t, hosting)
                            } else {
                                lobby_save(t, save, preview)
                            }),
                    ),
            )
            .child(sheet().children(self.session_packs()))
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
                    hosts = hosts.child(arrive(
                        card()
                            .id(("host", index))
                            .flex()
                            .items_center()
                            .gap_3()
                            .cursor_pointer()
                            .hover(|style| style.bg(rgb(PANEL_HOVER)).border_color(rgb(LAMP)))
                            .on_click(Self::act(cx, move |model| model.connect(address.into())))
                            .child(avatar(name))
                            .child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .child(
                                        div()
                                            .truncate()
                                            .font_weight(FontWeight::MEDIUM)
                                            .child(name.clone()),
                                    )
                                    .child(small(match &host.found.beacon.game_version {
                                        Some(version) => {
                                            tr!(t, "rail-game", version = version.as_str())
                                        }
                                        None => tr!(t, "rail-game-unknown"),
                                    })),
                            )
                            .child(small(address.ip().to_string()).flex_none())
                            .child(icon("arrow", 20., MUTED)),
                        ("found", index),
                        0.,
                        10.,
                    ));
                }
                if screen.hosts.is_empty() {
                    hosts = hosts.child(
                        hollow()
                            .flex()
                            .items_center()
                            .gap_5()
                            .p_5()
                            .child(beacon())
                            .child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .max_w(px(680.))
                                    .text_color(rgb(MUTED))
                                    .child(tr!(t, "join-searching")),
                            ),
                    );
                }
                let field = self
                    .address
                    .view("address", "192.168.0.10", ADDRESS_WIDTH, window)
                    .on_key_down(cx.listener(Self::type_address));
                let by_address = div()
                    .flex()
                    .flex_col()
                    .gap_2()
                    .pt_1()
                    .child(div().flex().gap_3().child(field).child(button(
                        "connect",
                        tr!(t, "join-manual-connect"),
                        true,
                        cx.listener(|this, _, _, cx| {
                            this.connect_to_address();
                            cx.notify();
                        }),
                    )))
                    .child(if self.address.refused() {
                        notice(DANGER, tr!(t, "join-manual-bad"))
                    } else {
                        small(tr!(t, "join-manual-detail"))
                    });
                scene_page("search", tr!(t, "join-title"), None)
                    .child(hosts)
                    .child(section(tr!(t, "join-manual")).child(by_address))
                    .into_any_element()
            }
            JoinStage::Working(step) => scene_page(
                "knock",
                tr!(t, "join-working-title"),
                Some(match step {
                    JoinStep::Connecting => tr!(t, "join-connecting"),
                    JoinStep::Comparing => tr!(t, "join-comparing"),
                }),
            )
            .child(waiting_dots())
            .into_any_element(),
            JoinStage::Review(plan) => self.review(screen, plan),
            JoinStage::Syncing(progress) => {
                let fraction = if progress.total_bytes == 0 {
                    0.
                } else {
                    progress.done_bytes as f32 / progress.total_bytes as f32
                };
                scene_page("copying", tr!(t, "syncing-title"), None)
                    .child(
                        div()
                            .font_family(DISPLAY_FONT)
                            .font_weight(FontWeight::MEDIUM)
                            .text_size(px(44.))
                            .line_height(relative(1.1))
                            .text_color(rgb(LAMP))
                            .child(format!("{}%", (fraction.clamp(0., 1.) * 100.) as u32)),
                    )
                    .child(
                        div()
                            .h(px(12.))
                            .w_full()
                            .rounded_full()
                            .bg(rgb(PANEL))
                            .overflow_hidden()
                            .child(
                                div()
                                    .relative()
                                    .h_full()
                                    .w(relative(fraction.clamp(0., 1.)))
                                    .rounded_full()
                                    .bg(rgb(LAMP))
                                    .overflow_hidden()
                                    .child(
                                        div()
                                            .absolute()
                                            .top_0()
                                            .bottom_0()
                                            .w(px(90.))
                                            .bg(linear_gradient(
                                                90.,
                                                linear_color_stop(Rgba { a: 0., ..rgb(TEXT) }, 0.),
                                                linear_color_stop(
                                                    Rgba {
                                                        a: 0.55,
                                                        ..rgb(TEXT)
                                                    },
                                                    1.,
                                                ),
                                            ))
                                            .with_animation(
                                                "shine",
                                                Animation::new(Duration::from_millis(1400))
                                                    .repeat(),
                                                |shine, delta| {
                                                    shine.left(relative(-0.3 + 1.4 * delta))
                                                },
                                            ),
                                    ),
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
            JoinStage::Failed(message) => self.failure(
                tr!(t, "join-failed-title"),
                Some(tr!(t, "join-failed-hint")),
                message,
                cx,
            ),
        }
    }

    fn host_name(&self, screen: &JoinScreen) -> String {
        match &screen.host {
            Some(host) => host.name.clone(),
            None => tr!(self.i18n, "host-unnamed"),
        }
    }

    fn review(&self, screen: &JoinScreen, plan: &Plan) -> AnyElement {
        let t = &self.i18n;
        let ours = self
            .model
            .env
            .as_ref()
            .and_then(|env| env.game.version.clone());
        let theirs = screen
            .host
            .as_ref()
            .and_then(|host| host.game_version.clone());
        let unknown = tr!(t, "version-unknown");
        let mut changes = sheet();
        if !plan.diff.fetch.is_empty() {
            changes = changes.child(change(
                "download",
                tr!(
                    t,
                    "review-download",
                    n = plan.diff.fetch.len(),
                    size = bytes(t, plan.diff.fetch_bytes)
                ),
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
                tr!(
                    t,
                    "review-save",
                    name = save.file_name.as_str(),
                    size = bytes(t, save.size)
                ),
                None,
            ));
        }
        if plan.session.own_families {
            changes = changes.child(change("people", tr!(t, "review-own"), None));
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
        .into_any_element()
    }

    fn ready(&self, screen: &JoinScreen, synced: &Synced, _: &mut Context<Self>) -> AnyElement {
        let t = &self.i18n;
        let players = vec![
            player_row(
                &self.host_name(screen),
                small(tr!(t, "player-host")),
                player_state(GOOD, tr!(t, "host-title")),
            )
            .into_any_element(),
            player_row(
                &self.own_name(),
                small(tr!(t, "player-you")),
                self.own_state(),
            )
            .into_any_element(),
        ];
        let line = |text: String| small(text).w(px(ROSTER_WIDTH));
        let mut notes = Vec::new();
        if synced.fetched + synced.moved > 0 {
            notes.push(line(tr!(
                t,
                "ready-changed",
                fetched = synced.fetched,
                moved = synced.moved
            )));
            notes.push(line(tr!(
                t,
                "ready-backup",
                path = format!("Mods.tsmp-backup\\{}", synced.backup_stamp)
            )));
        } else {
            notes.push(line(tr!(t, "ready-matched")));
        }
        if let Some(save) = &synced.save {
            let key = if synced.save_updated {
                "ready-save-downloaded"
            } else {
                "ready-save-matched"
            };
            notes.push(line(tr!(t, key, name = save.as_str())));
        }
        self.lobby(
            tr!(t, "guest-title"),
            Vec::new(),
            players,
            notes,
            synced.save.as_deref(),
            synced.save_preview.as_deref(),
        )
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
            facts.push(fact_with(
                tr!(t, "session-packs-off"),
                pack_chips(t, &disabled).opacity(0.6),
            ));
        }
        facts
    }

    fn failure(
        &self,
        title: String,
        hint: Option<String>,
        message: &str,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        scene_page("trouble", title, hint)
            .child(self.error_box("copy-error", message, cx))
            .into_any_element()
    }

    /// What the game is doing, once it can be started.
    fn game_status(&self, cx: &mut Context<Self>) -> Option<Div> {
        let t = &self.i18n;
        let (color, text) = match &self.model.game {
            GameState::NotStarted if !self.model.can_launch() => return None,
            GameState::NotStarted => (MUTED, tr!(t, "game-ready")),
            GameState::Starting {
                via_steam: true, ..
            } => (WARNING, tr!(t, "game-starting-steam")),
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
            GameState::Failed(message) => {
                (DANGER, tr!(t, "game-failed", message = message.as_str()))
            }
        };
        let live = match (&self.model.game, self.model.live) {
            (GameState::Running { .. }, Some(status)) => {
                Some(describe_live(&status, self.model.own_families()))
            }
            _ => None,
        };
        let state = if matches!(self.model.game, GameState::Starting { .. }) {
            div()
                .flex()
                .gap_2()
                .child(pulse(dot(color), "game-starting"))
                .child(div().min_w_0().child(text))
        } else if color == DANGER {
            div()
                .flex()
                .items_center()
                .gap_3()
                .child(status_line(color, text.clone()).flex_1().min_w_0())
                .child(self.copy_button("copy-game", self.model.report(&text), cx))
        } else {
            status_line(color, text)
        };
        Some(
            div()
                .flex()
                .flex_col()
                .gap_1()
                .child(state)
                .children(live.map(|(color, key)| status_line(color, tr!(t, key)))),
        )
    }

    /// The bar under every screen: the way back on the left, the way on at
    /// the right, and between them what the game is doing. It stays put
    /// while the page scrolls.
    fn bottom(&self, cx: &mut Context<Self>) -> Option<Div> {
        let t = &self.i18n;
        let mut primary = None;
        let mut note = None;
        let back = match &self.model.screen {
            Screen::Host(screen) => match &screen.stage {
                HostStage::Setup => {
                    primary = Some(button(
                        "start",
                        tr!(t, "host-start"),
                        true,
                        Self::act(cx, Model::start_hosting),
                    ));
                    note = Some(
                        match screen
                            .selected
                            .filter(|_| !screen.own_families)
                            .and_then(|index| Some((index, screen.saves.get(index)?)))
                        {
                            _ if screen.own_families => tr!(t, "mode-own"),
                            Some((index, save)) => screen.previews[index]
                                .as_deref()
                                .and_then(|preview| preview.about.name.clone())
                                .unwrap_or_else(|| save.file_name.clone()),
                            None => tr!(t, "save-none"),
                        },
                    );
                    Some("back")
                }
                HostStage::Starting => Some("cancel"),
                HostStage::Running(_) => Some("host-stop"),
                HostStage::Failed(_) => Some("back"),
            },
            Screen::Join(screen) => match &screen.stage {
                JoinStage::Browsing | JoinStage::Failed(_) => Some("back"),
                JoinStage::Working(_) => Some("cancel"),
                JoinStage::Review(_) => {
                    primary = Some(button(
                        "sync",
                        tr!(t, "sync"),
                        true,
                        Self::act(cx, Model::confirm_sync),
                    ));
                    Some("cancel")
                }
                JoinStage::Syncing(_) => None,
                JoinStage::Ready(_) => Some("leave"),
            },
            Screen::Home | Screen::Loading | Screen::Fatal(_) => None,
        };
        let status = self.game_status(cx);
        if primary.is_none() && self.model.can_launch() {
            primary = Some(button(
                "launch",
                tr!(t, "game-start"),
                true,
                Self::act(cx, Model::launch_game),
            ));
        }
        if back.is_none() && primary.is_none() && status.is_none() {
            return None;
        }
        let middle = match (status, note) {
            (Some(status), _) => status.flex_1().min_w_0(),
            (None, Some(note)) => div().flex_1().min_w_0().flex().justify_end().child(
                div()
                    .min_w_0()
                    .truncate()
                    .text_color(rgb(MUTED))
                    .child(note),
            ),
            (None, None) => div().flex_1(),
        };
        Some(
            div()
                .flex_none()
                .px(px(GUTTER))
                .py_4()
                .bg(rgb(RAIL))
                .border_t_1()
                .border_color(rgb(LINE))
                .child(
                    div()
                        .w_full()
                        .max_w(px(PAGE_WIDTH))
                        .mx_auto()
                        .flex()
                        .items_center()
                        .gap_6()
                        .children(back.map(|key| {
                            button("back", tr!(t, key), false, Self::act(cx, Model::back))
                        }))
                        .child(middle)
                        .children(primary),
                ),
        )
    }
}

impl Render for Launcher {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let t = &self.i18n;
        let loading =
            || page(tr!(t, "loading-title"), Some(tr!(t, "loading-body"))).into_any_element();
        let body = match (&self.model.screen, &self.model.env) {
            (Screen::Fatal(message), _) => self.failure(
                tr!(t, "fatal-title"),
                Some(tr!(t, "fatal-hint")),
                message,
                cx,
            ),
            (Screen::Host(screen), _) => self.host(screen, window, cx),
            (Screen::Join(screen), _) => self.join(screen, window, cx),
            (Screen::Home | Screen::Loading, _) => loading(),
        };
        let (scene, busy) = self.scene();
        let home = match (&self.model.screen, &self.model.env) {
            (Screen::Home, Some(env)) => Some(self.home(env, cx)),
            _ => None,
        };
        let frame = div()
            .relative()
            .size_full()
            .flex()
            .flex_col()
            .bg(rgb(PAPER))
            .text_color(rgb(TEXT))
            .font_family(BODY_FONT)
            .text_size(px(16.))
            .line_height(relative(1.5))
            .child(self.titlebar(window, cx));
        match home {
            Some(home) => frame.child(home),
            None => frame.child(self.progress(busy)).child(
                div()
                    .relative()
                    .flex_1()
                    .min_h_0()
                    .child(
                        div()
                            .id("body")
                            .size_full()
                            .overflow_y_scroll()
                            .track_scroll(&self.scroll)
                            .px(px(GUTTER))
                            .pt_6()
                            .pb_8()
                            .child(arrive(
                                div().w_full().max_w(px(PAGE_WIDTH)).mx_auto().child(body),
                                ("scene", scene),
                                0.,
                                14.,
                            )),
                    )
                    .children(self.scrollbar()),
            ),
        }
        .children(self.bottom(cx))
        .when(self.journal_open, |this| this.children(self.journal(cx)))
        .when(self.choosing_language, |this| {
            this.children(self.language_list(cx))
        })
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
    div().flex().flex_col().gap_8().child(heading(title, lead))
}

/// A page whose state has a drawing: it sits beside the title.
fn scene_page(picture: &str, title: String, lead: Option<String>) -> Div {
    div().flex().flex_col().gap_8().child(
        div()
            .flex()
            .items_center()
            .gap_8()
            .child(heading(title, lead).flex_1().min_w_0())
            // The drawings are 2:1, and so is the box, which keeps them in it.
            .child(
                art(picture)
                    .flex_none()
                    .w(px(300.))
                    .h(px(150.))
                    .rounded(px(14.)),
            ),
    )
}

fn heading(title: String, lead: Option<String>) -> Div {
    div()
        .flex()
        .flex_col()
        .gap_3()
        .child(
            div()
                .font_family(DISPLAY_FONT)
                .font_weight(FontWeight::MEDIUM)
                .text_size(px(28.))
                .line_height(relative(1.2))
                .child(title),
        )
        .children(lead.map(|lead| {
            div()
                .max_w(px(760.))
                .text_size(px(17.))
                .text_color(rgb(MUTED))
                .child(lead)
        }))
}

/// How long something takes to arrive on the screen, in seconds.
const ARRIVAL: f32 = 0.34;

/// Lets an element arrive: `delay` seconds after it first shows it fades in,
/// settling from `rise` pixels below its place.
fn arrive<E>(element: E, id: impl Into<ElementId>, delay: f32, rise: f32) -> AnimationElement<E>
where
    E: Styled + IntoElement + 'static,
{
    let total = delay + ARRIVAL;
    element.with_animation(
        id,
        Animation::new(Duration::from_secs_f32(total)),
        move |element, delta| {
            let time = ((delta * total - delay) / ARRIVAL).clamp(0., 1.);
            let eased = 1. - (1. - time).powi(3);
            let element = element.opacity(eased);
            if rise == 0. {
                element
            } else {
                element.relative().top(px((1. - eased) * rise))
            }
        },
    )
}

/// Makes an element breathe while something is going on.
fn pulse<E>(element: E, id: impl Into<ElementId>) -> AnimationElement<E>
where
    E: Styled + IntoElement + 'static,
{
    element.with_animation(
        id,
        Animation::new(Duration::from_millis(1300))
            .repeat()
            .with_easing(pulsating_between(0.3, 1.)),
        |element, delta| element.opacity(delta),
    )
}

/// The dot of a [`status_line`].
fn dot(color: u32) -> Div {
    div()
        .mt(px(8.))
        .size(px(9.))
        .flex_none()
        .rounded_full()
        .bg(rgb(color))
}

/// Three dots taking turns: the launcher is working and there is nothing to read yet.
fn waiting_dots() -> Div {
    let mut dots = div().flex().gap_2();
    for (index, id) in ["dot-a", "dot-b", "dot-c"].into_iter().enumerate() {
        let phase = index as f32 / 3.;
        dots = dots.child(
            div()
                .size(px(10.))
                .rounded_full()
                .bg(rgb(LAMP))
                .with_animation(
                    id,
                    Animation::new(Duration::from_millis(1200)).repeat(),
                    move |dot, delta| {
                        let wave = ((delta - phase).rem_euclid(1.) * std::f32::consts::TAU).cos()
                            * 0.5
                            + 0.5;
                        dot.opacity(0.25 + 0.75 * wave)
                    },
                ),
        );
    }
    dots
}

/// A lamp sending rings outwards: the launcher is looking for the host.
fn beacon() -> Div {
    const SIZE: f32 = 64.;
    let ring = |id: &'static str, phase: f32| {
        div()
            .absolute()
            .rounded_full()
            .border_2()
            .border_color(rgb(LAMP))
            .with_animation(
                id,
                Animation::new(Duration::from_millis(2600)).repeat(),
                move |ring, delta| {
                    let time = (delta + phase).fract();
                    let size = 14. + time * (SIZE - 14.);
                    let inset = (SIZE - size) / 2.;
                    ring.size(px(size))
                        .top(px(inset))
                        .left(px(inset))
                        .opacity((1. - time) * 0.8)
                },
            )
    };
    div()
        .relative()
        .size(px(SIZE))
        .flex_none()
        .child(ring("ring-a", 0.))
        .child(ring("ring-b", 0.5))
        .child(
            div()
                .absolute()
                .top(px(SIZE / 2. - 7.))
                .left(px(SIZE / 2. - 7.))
                .size(px(14.))
                .rounded_full()
                .bg(rgb(LAMP)),
        )
}

/// What the pointer has just done to a door.
#[derive(Clone, Copy, PartialEq)]
enum Glow {
    Rest,
    In,
    Out,
}

/// One fact of the strip under the doors: what it is, then what it says.
fn about(label: String, value: Div) -> Div {
    div()
        .min_w_0()
        .flex()
        .flex_col()
        .gap_1()
        .child(div().text_size(px(13.)).text_color(rgb(MUTED)).child(label))
        .child(value.text_size(px(15.)).line_height(relative(1.4)))
}

/// A titled part of a screen.
fn section(title: String) -> Div {
    div().flex().flex_col().gap_2().child(
        div()
            .text_size(px(18.))
            .font_weight(FontWeight::SEMIBOLD)
            .child(title),
    )
}

/// The lobby: the players on the left, the save they share on the right.
const ROSTER_WIDTH: f32 = 440.;
/// The server field leaves room beside it for its button.
const SERVER_WIDTH: f32 = 290.;
/// Wide enough for an invite with a long server name.
const ADDRESS_WIDTH: f32 = 400.;
const LOBBY_GAP: f32 = 24.;
const SAVE_WIDTH: f32 = PAGE_WIDTH - ROSTER_WIDTH - LOBBY_GAP;
const STATE_WIDTH: f32 = 180.;
const LOT_SIZE: f32 = 84.;

/// A player in the lobby: who they are, and on the right what they are doing.
fn player_row(name: &str, detail: Div, state: Div) -> Div {
    let words = ROSTER_WIDTH - 2. * 14. - 48. - 2. * 12. - STATE_WIDTH;
    div()
        .w(px(ROSTER_WIDTH))
        .h(px(72.))
        .px(px(14.))
        .flex_none()
        .flex()
        .items_center()
        .gap(px(12.))
        .rounded(px(12.))
        .bg(rgb(PANEL))
        .child(avatar(name))
        .child(
            div()
                .w(px(words))
                .flex_none()
                .flex()
                .flex_col()
                .child(
                    div()
                        .w(px(words))
                        .truncate()
                        .font_weight(FontWeight::MEDIUM)
                        .child(name.to_string()),
                )
                .child(detail.w(px(words)).truncate()),
        )
        .child(
            div()
                .w(px(STATE_WIDTH))
                .flex_none()
                .flex()
                .justify_end()
                .child(state),
        )
}

/// What a player is doing, as a coloured dot and a word or two.
fn player_state(color: u32, text: String) -> Div {
    div()
        .flex()
        .items_center()
        .gap_2()
        .text_size(px(15.))
        .child(div().size(px(9.)).flex_none().rounded_full().bg(rgb(color)))
        .child(text)
}

/// A fact under the players: a quiet label and its value.
fn brief(label: String, value: String) -> Div {
    div()
        .flex()
        .gap_3()
        .text_size(px(15.))
        .child(
            div()
                .w(px(96.))
                .flex_none()
                .text_color(rgb(MUTED))
                .child(label),
        )
        .child(div().w(px(ROSTER_WIDTH - 96. - 12.)).child(value))
}

/// The save both players load: its picture, who is in it and where they live.
fn lobby_save(t: &I18n, file_name: Option<&str>, preview: Option<&Preview>) -> Div {
    let about = preview.map(|preview| &preview.about);
    let picture = match file_name {
        Some(_) => portrait(preview),
        None => art("new-game"),
    };
    let title = match file_name {
        Some(file_name) => about
            .and_then(|about| about.name.clone())
            .unwrap_or_else(|| file_name.to_string()),
        None => tr!(t, "host-save-none"),
    };
    let lot = preview.and_then(|preview| preview.lot_picture.clone());
    let words = match lot {
        Some(_) => SAVE_WIDTH - LOT_SIZE - 12.,
        None => SAVE_WIDTH,
    };
    let fact = |picture: &'static str, text: String| {
        div()
            .flex()
            .items_center()
            .gap(px(8.))
            .text_color(rgb(MUTED))
            .child(icon(picture, 17., MUTED))
            .child(div().w(px(words - 25.)).truncate().child(text))
    };
    let mut lines = Vec::new();
    if let Some(about) = about {
        if !about.sims.is_empty() {
            let mut names = about
                .sims
                .iter()
                .take(NAMED_SIMS)
                .cloned()
                .collect::<Vec<_>>()
                .join(", ");
            if about.sims.len() > NAMED_SIMS {
                names.push_str(&format!(" +{}", about.sims.len() - NAMED_SIMS));
            }
            lines.push(div().w(px(words)).child(names));
        }
        lines.extend(about.lot.clone().map(|lot| fact("house", lot)));
        lines.extend(about.funds.map(|funds| fact("coins", simoleons(funds))));
        if about.name.is_some() {
            lines.extend(file_name.map(|file_name| small(file_name.to_string()).pt_1()));
        }
    }
    div()
        .w(px(SAVE_WIDTH))
        .flex()
        .flex_col()
        .gap(px(4.))
        .text_size(px(15.))
        .child(
            // An image keeps its own shape, and these are all 3:2.
            picture
                .w(px(SAVE_WIDTH))
                .h(px(SAVE_WIDTH / 1.5))
                .flex_none()
                .rounded(px(14.))
                .object_fit(ObjectFit::Cover),
        )
        .child(
            div()
                .w(px(SAVE_WIDTH))
                .pt_2()
                .flex()
                .items_start()
                .gap(px(12.))
                .child(
                    div()
                        .w(px(words))
                        .flex_none()
                        .flex()
                        .flex_col()
                        .gap(px(4.))
                        .child(
                            div()
                                .w(px(words))
                                .pb_1()
                                .font_family(DISPLAY_FONT)
                                .font_weight(FontWeight::MEDIUM)
                                .text_size(px(17.))
                                .line_height(relative(1.25))
                                .child(title),
                        )
                        .children(lines),
                )
                .children(lot.map(|lot| {
                    img(lot)
                        .size(px(LOT_SIZE))
                        .flex_none()
                        .rounded(px(10.))
                        .object_fit(ObjectFit::Cover)
                })),
        )
}

/// Ruled lines, like a list on paper: for facts and changes.
fn sheet() -> Div {
    div().flex().flex_col().border_t_1().border_color(rgb(LINE))
}

/// A label and its value on one line of a [`sheet`].
fn fact_with(label: String, value: Div) -> Div {
    div()
        .flex()
        .gap_4()
        .py(px(14.))
        .border_b_1()
        .border_color(rgb(LINE))
        .child(
            div()
                .w(px(200.))
                .flex_none()
                .text_color(rgb(MUTED))
                .child(label),
        )
        .child(value.flex_1().min_w_0())
}

/// Packs as labels, named where the name is known, with a dot for the kind.
fn pack_chips(t: &I18n, packs: &PackSet) -> Div {
    if packs.is_empty() {
        return div().child(tr!(t, "packs-none"));
    }
    let mut chips = div().flex().flex_wrap().gap(px(8.));
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
                .gap(px(8.))
                .h(px(30.))
                .px_3()
                .rounded(px(8.))
                .bg(rgb(PANEL))
                .text_size(px(14.))
                .child(div().size(px(7.)).flex_none().rounded_full().bg(rgb(color)))
                .child(pack_name(code).map_or_else(|| code.clone(), str::to_string)),
        );
    }
    chips
}

/// A raised block: a player, a host, a save.
fn card() -> Div {
    div()
        .p_4()
        .rounded(px(14.))
        .bg(rgb(PANEL))
        .border_1()
        .border_color(rgb(LINE))
}

/// A dashed outline where something is still missing.
fn hollow() -> Div {
    div()
        .rounded(px(14.))
        .border_1()
        .border_dashed()
        .border_color(rgb(LINE))
}

/// A player, as the first letter of their computer's name.
fn avatar(name: &str) -> Div {
    let letter: String = name
        .trim()
        .chars()
        .next()
        .map(|c| c.to_uppercase().collect())
        .unwrap_or_default();
    div()
        .size(px(48.))
        .flex_none()
        .flex()
        .items_center()
        .justify_center()
        .rounded_full()
        .bg(Rgba {
            a: 0.14,
            ..rgb(LAMP)
        })
        .font_family(DISPLAY_FONT)
        .font_weight(FontWeight::MEDIUM)
        .text_size(px(19.))
        .text_color(rgb(LAMP))
        .child(letter)
}

/// One of the launcher's own drawings.
fn art(name: &str) -> Img {
    img(format!("art/{name}.jpg"))
}

fn portrait(preview: Option<&Preview>) -> Img {
    match preview.and_then(|preview| preview.portrait.clone()) {
        Some(portrait) => img(portrait),
        None => art("no-picture"),
    }
}

/// The server a host last opened a session on, unless `TSMP_SERVER` names
/// another.
fn remembered_server() -> String {
    std::env::var("TSMP_SERVER")
        .ok()
        .filter(|server| !server.trim().is_empty())
        .or_else(|| settings::SERVER.load())
        .unwrap_or_default()
}

/// This computer's address on the local network. Nothing is sent: a UDP socket
/// only asks the system which of its addresses it would send from.
fn lan_address() -> Option<IpAddr> {
    let socket = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0)).ok()?;
    [
        [192, 168, 0, 1],
        [10, 0, 0, 1],
        [172, 16, 0, 1],
        [8, 8, 8, 8],
    ]
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

/// The host picks a save from a list on the left, and sees it on the right.
const LIST_WIDTH: f32 = 288.;
const PANE_GAP: f32 = 24.;
const PANE_WIDTH: f32 = PAGE_WIDTH - LIST_WIDTH - PANE_GAP;
/// Under the picture of the chosen save: who is in it on the left, the rest
/// about it on the right. A save with nothing on the right has the whole
/// width for its words.
const PANE_WORDS: f32 = PANE_WIDTH;
const PANE_FACTS: f32 = 220.;
const PANE_NAMES: f32 = PANE_WIDTH - PANE_FACTS - 24.;

const MODE_GAP: f32 = 16.;
const MODE_WIDTH: f32 = (PAGE_WIDTH - MODE_GAP) / 2.;
const MODE_WORDS: f32 = MODE_WIDTH - 2. * 18. - 2. - 40. - 14.;
const GUIDE_PICTURE: f32 = 360.;
const GUIDE_WORDS: f32 = PAGE_WIDTH - GUIDE_PICTURE - 32. - 34. - 14.;

/// One of the two ways to play, on the host's first screen.
fn mode_tile(
    id: &'static str,
    chosen: bool,
    picture: &'static str,
    title: String,
    detail: String,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    div()
        .id(id)
        .w(px(MODE_WIDTH))
        .p(px(18.))
        .flex_none()
        .flex()
        .items_start()
        .gap(px(14.))
        .rounded(px(14.))
        .border_1()
        .border_color(rgb(if chosen { LAMP } else { LINE }))
        .when(chosen, |this| this.bg(rgb(PANEL)))
        .cursor_pointer()
        .hover(|style| style.bg(rgb(PANEL_HOVER)))
        .on_click(on_click)
        .child(
            div()
                .size(px(40.))
                .flex_none()
                .flex()
                .items_center()
                .justify_center()
                .rounded_full()
                .bg(rgb(if chosen { LAMP } else { RAIL }))
                .child(icon(picture, 21., if chosen { INK } else { MUTED })),
        )
        .child(
            div()
                .w(px(MODE_WORDS))
                .flex_none()
                .flex()
                .flex_col()
                .gap(px(4.))
                .child(
                    div()
                        .w(px(MODE_WORDS))
                        .font_family(DISPLAY_FONT)
                        .font_weight(FontWeight::MEDIUM)
                        .text_size(px(17.))
                        .line_height(relative(1.3))
                        .child(title),
                )
                .child(
                    div()
                        .w(px(MODE_WORDS))
                        .text_size(px(15.))
                        .text_color(rgb(MUTED))
                        .child(detail),
                ),
        )
}

/// What happens when everyone brings a family of their own, step by step.
fn own_families_guide(t: &I18n) -> Div {
    let step = |number: usize, text: String| {
        div()
            .flex()
            .items_start()
            .gap(px(14.))
            .child(
                div()
                    .size(px(34.))
                    .flex_none()
                    .flex()
                    .items_center()
                    .justify_center()
                    .rounded_full()
                    .border_1()
                    .border_color(rgb(LAMP))
                    .font_family(DISPLAY_FONT)
                    .font_weight(FontWeight::MEDIUM)
                    .text_size(px(15.))
                    .text_color(rgb(LAMP))
                    .child(number.to_string()),
            )
            .child(
                div()
                    .w(px(GUIDE_WORDS))
                    .pt(px(4.))
                    .flex_none()
                    .text_size(px(16.))
                    .child(text),
            )
    };
    div()
        .flex()
        .flex_col()
        .gap_6()
        .child(
            div()
                .flex()
                .items_start()
                .gap(px(32.))
                .child(
                    div()
                        .flex()
                        .flex_col()
                        .gap_5()
                        .child(step(1, tr!(t, "own-step-1")))
                        .child(step(2, tr!(t, "own-step-2")))
                        .child(step(3, tr!(t, "own-step-3"))),
                )
                .child(
                    // The drawings are 2:1, and so is the box.
                    art("knock")
                        .w(px(GUIDE_PICTURE))
                        .h(px(GUIDE_PICTURE / 2.))
                        .flex_none()
                        .rounded(px(14.))
                        .object_fit(ObjectFit::Cover),
                ),
        )
        .child(notice(SKY, tr!(t, "own-world")))
        .child(notice(WARNING, tr!(t, "own-new")))
}

/// In the lobby, in place of the shared save: everyone loads their own.
fn lobby_own(t: &I18n, hosting: bool) -> Div {
    div()
        .w(px(SAVE_WIDTH))
        .flex()
        .flex_col()
        .gap(px(4.))
        .text_size(px(15.))
        .child(
            art("knock")
                .w(px(SAVE_WIDTH))
                .h(px(SAVE_WIDTH / 2.))
                .flex_none()
                .rounded(px(14.))
                .object_fit(ObjectFit::Cover),
        )
        .child(
            div()
                .w(px(SAVE_WIDTH))
                .pt_2()
                .pb_1()
                .font_family(DISPLAY_FONT)
                .font_weight(FontWeight::MEDIUM)
                .text_size(px(17.))
                .line_height(relative(1.25))
                .child(tr!(t, "mode-own")),
        )
        .child(
            div()
                .w(px(SAVE_WIDTH))
                .text_color(rgb(MUTED))
                .child(tr!(t, if hosting { "lobby-own-host" } else { "lobby-own-guest" })),
        )
        .when(!hosting, |this| {
            this.child(
                div()
                    .w(px(SAVE_WIDTH))
                    .pt_3()
                    .child(notice(WARNING, tr!(t, "lobby-own-keep"))),
            )
        })
}

/// A household in the list of saves: a small picture and who it is.
fn save_row(
    index: usize,
    chosen: bool,
    picture: Img,
    title: String,
    detail: String,
    count: Option<usize>,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    let words = LIST_WIDTH - 2. * 9. - 2. - 54. - 12. - if count.is_some() { 38. } else { 0. };
    div()
        .id(("save", index))
        .w(px(LIST_WIDTH))
        .h(px(68.))
        .px(px(9.))
        .flex_none()
        .flex()
        .items_center()
        .gap(px(12.))
        .rounded(px(12.))
        .border_1()
        .border_color(rgb(if chosen { LAMP } else { PAPER }))
        .when(chosen, |this| this.bg(rgb(PANEL)))
        .cursor_pointer()
        .hover(|style| style.bg(rgb(PANEL_HOVER)))
        .on_click(on_click)
        .child(
            // An image keeps its own shape, and these are all 3:2.
            picture
                .w(px(54.))
                .h(px(36.))
                .flex_none()
                .rounded(px(8.))
                .object_fit(ObjectFit::Cover),
        )
        .child(
            div()
                .w(px(words))
                .flex_none()
                .flex()
                .flex_col()
                .gap(px(2.))
                .child(
                    div()
                        .w(px(words))
                        .truncate()
                        .text_size(px(15.))
                        .font_weight(FontWeight::SEMIBOLD)
                        .child(title),
                )
                .child(small(detail).w(px(words)).truncate()),
        )
        .children(count.map(|count| {
            div()
                .w(px(26.))
                .h(px(22.))
                .flex_none()
                .flex()
                .items_center()
                .justify_center()
                .rounded_full()
                .bg(rgb(RAIL))
                .text_size(px(13.))
                .text_color(rgb(MUTED))
                .child(count.to_string())
        }))
}

/// The chosen save: its picture, beside it who is in it, below its other saves.
fn save_pane(
    picture: Img,
    title: String,
    lines: Vec<Div>,
    facts: Vec<Div>,
    variants: Option<Div>,
) -> Div {
    let words = if facts.is_empty() {
        PANE_WORDS
    } else {
        PANE_NAMES
    };
    div()
        .w(px(PANE_WIDTH))
        .flex_none()
        .flex()
        .flex_col()
        .gap_4()
        .child(
            picture
                .w(px(PANE_WIDTH))
                .h(px(PANE_WIDTH / 1.5))
                .flex_none()
                .rounded(px(14.))
                .object_fit(ObjectFit::Cover),
        )
        .child(
            div()
                .w(px(PANE_WIDTH))
                .flex()
                .items_start()
                .gap(px(24.))
                .text_size(px(16.))
                .child(
                    div()
                        .w(px(words))
                        .flex_none()
                        .flex()
                        .flex_col()
                        .gap(px(4.))
                        .child(
                            div()
                                .w(px(words))
                                .pb_1()
                                .font_family(DISPLAY_FONT)
                                .font_weight(FontWeight::MEDIUM)
                                .text_size(px(19.))
                                .line_height(relative(1.3))
                                .child(title),
                        )
                        .children(lines),
                )
                .when(!facts.is_empty(), |this| {
                    this.child(
                        div()
                            .w(px(PANE_FACTS))
                            .pt(px(4.))
                            .flex_none()
                            .flex()
                            .flex_col()
                            .gap(px(8.))
                            .children(facts),
                    )
                }),
        )
        .children(variants)
}

/// One thing that will change or has changed, on a [`sheet`].
fn change(picture: &'static str, text: String, detail: Option<Div>) -> Div {
    div()
        .flex()
        .gap_3()
        .py(px(14.))
        .border_b_1()
        .border_color(rgb(LINE))
        .child(div().pt(px(3.)).child(icon(picture, 18., LAMP)))
        .child(
            div()
                .flex_1()
                .min_w_0()
                .flex()
                .flex_col()
                .gap_1()
                .child(text)
                .children(detail),
        )
}

/// A small framed button with an icon: for something done on the side.
fn chip(id: &'static str, picture: &str, label: String, done: bool) -> Stateful<Div> {
    div()
        .id(id)
        .flex_none()
        .h(px(34.))
        .px_3()
        .flex()
        .items_center()
        .gap_2()
        .rounded(px(8.))
        .border_1()
        .border_color(rgb(LINE))
        .text_size(px(14.))
        .text_color(rgb(if done { GOOD } else { TEXT }))
        .cursor_pointer()
        .hover(|style| style.bg(rgb(PANEL_HOVER)))
        .child(icon(picture, 16., if done { GOOD } else { MUTED }))
        .child(label)
}

fn small(text: impl Into<SharedString>) -> Div {
    div()
        .text_size(px(14.))
        .text_color(rgb(MUTED))
        .child(text.into())
}

/// A coloured dot and what it stands for.
fn status_line(color: u32, text: String) -> Div {
    div()
        .flex()
        .gap_2()
        .child(
            div()
                .mt(px(8.))
                .size(px(9.))
                .flex_none()
                .rounded_full()
                .bg(rgb(color)),
        )
        .child(div().min_w_0().child(text))
}

/// Something the player has to know before going on.
fn notice(color: u32, text: String) -> Div {
    div()
        .flex()
        .gap_3()
        .p_4()
        .rounded(px(10.))
        .bg(Rgba {
            a: 0.1,
            ..rgb(color)
        })
        .child(div().pt(px(3.)).child(icon("alert", 18., color)))
        .child(div().flex_1().min_w_0().child(text))
}

fn file_list(t: &I18n, paths: &[String]) -> Div {
    let mut list = div()
        .flex()
        .flex_col()
        .text_size(px(14.))
        .text_color(rgb(MUTED));
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
        .h(px(48.))
        .px_6()
        .flex()
        .items_center()
        .rounded(px(10.))
        .bg(rgb(background))
        .text_color(rgb(text))
        .font_weight(if primary {
            FontWeight::SEMIBOLD
        } else {
            FontWeight::MEDIUM
        })
        .border_1()
        .border_color(rgb(border))
        .cursor_pointer()
        .hover(move |style| style.bg(rgb(hover)))
        .on_click(on_click)
        .child(label)
}

/// Half of the home screen: a drawing as tall as the window allows, and
/// over its quiet lower part what choosing it does.
fn door(
    id: &'static str,
    picture: &'static str,
    symbol: &'static str,
    title: String,
    detail: String,
    glow: Glow,
    on_hover: impl Fn(&bool, &mut Window, &mut App) + 'static,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    let night = rgb(RAIL);
    // The drawing comes a little closer while the pointer is over its door.
    let near = |frame: Div, by: f32| {
        let out = px(-12. * by);
        frame.top(out).left(out).right(out).bottom(out)
    };
    let frame = div()
        .absolute()
        .child(art(picture).size_full().object_fit(ObjectFit::Cover));
    let turn = || Animation::new(Duration::from_millis(420)).with_easing(gpui::ease_out_quint());
    let drawing = match glow {
        Glow::Rest => near(frame, 0.).into_any_element(),
        Glow::In => frame
            .with_animation("near", turn(), move |frame, delta| near(frame, delta))
            .into_any_element(),
        Glow::Out => frame
            .with_animation("far", turn(), move |frame, delta| near(frame, 1. - delta))
            .into_any_element(),
    };
    div()
        .id(id)
        .group(id)
        .relative()
        .flex_1()
        .min_w_0()
        .h_full()
        .overflow_hidden()
        .bg(night)
        .cursor_pointer()
        .on_hover(on_hover)
        .on_click(on_click)
        .child(drawing)
        // Night gathers at the bottom, where the words are.
        .child(
            div()
                .absolute()
                .left_0()
                .right_0()
                .bottom_0()
                .h(relative(0.6))
                .bg(linear_gradient(
                    180.,
                    linear_color_stop(Rgba { a: 0., ..night }, 0.),
                    linear_color_stop(Rgba { a: 0.94, ..night }, 0.8),
                )),
        )
        .child(
            div()
                .absolute()
                .left_0()
                .right_0()
                .bottom_0()
                .p_8()
                .flex()
                .items_end()
                .justify_between()
                .gap_6()
                .child(
                    div()
                        .min_w_0()
                        .flex()
                        .flex_col()
                        .gap_2()
                        .child(
                            div()
                                .flex()
                                .items_center()
                                .gap_3()
                                .child(icon(symbol, 26., LAMP))
                                .child(
                                    div()
                                        .font_family(DISPLAY_FONT)
                                        .font_weight(FontWeight::MEDIUM)
                                        .text_size(px(26.))
                                        .line_height(relative(1.25))
                                        .group_hover(id, |style| style.text_color(rgb(LAMP)))
                                        .child(title),
                                ),
                        )
                        .child(
                            div()
                                .max_w(px(380.))
                                .text_size(px(17.))
                                .text_color(rgb(TEXT))
                                .opacity(0.82)
                                .child(detail),
                        ),
                )
                .child(
                    div()
                        .flex_none()
                        .size(px(52.))
                        .flex()
                        .items_center()
                        .justify_center()
                        .rounded_full()
                        .border_1()
                        .border_color(rgb(MUTED))
                        .group_hover(id, |style| style.bg(rgb(LAMP)).border_color(rgb(LAMP)))
                        // An icon takes its colour from itself, not from what holds it.
                        .child(
                            svg()
                                .path("icons/arrow.svg")
                                .size(px(24.))
                                .text_color(rgb(TEXT))
                                .group_hover(id, |style| style.text_color(rgb(INK))),
                        ),
                ),
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
fn describe_live(status: &LiveStatus, own_families: bool) -> (u32, &'static str) {
    if !status.game_connected {
        (WARNING, "live-no-mod")
    } else if status.remotes == 0 {
        (WARNING, "live-no-remote")
    } else if !status.game_in_zone {
        (WARNING, if own_families { "live-load-own" } else { "live-load-save" })
    } else if status.game_syncing || status.remote_syncing {
        (WARNING, "live-syncing")
    } else if !status.remote_in_zone {
        (
            WARNING,
            if own_families {
                "live-wait-remote-own"
            } else {
                "live-wait-remote"
            },
        )
    } else if !status.is_live() {
        (WARNING, "live-apart")
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
    let seconds = SystemTime::now()
        .duration_since(time)
        .map_or(0, |d| d.as_secs());
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
        assert_eq!(describe_live(&status, false).1, "live-no-mod");
        status.game_connected = true;
        assert_eq!(describe_live(&status, false).1, "live-no-remote");
        status.remotes = 1;
        assert_eq!(describe_live(&status, false).1, "live-load-save");
        assert_eq!(describe_live(&status, true).1, "live-load-own");
        status.game_in_zone = true;
        assert_eq!(describe_live(&status, false).1, "live-wait-remote");
        assert_eq!(describe_live(&status, true).1, "live-wait-remote-own");
        status.remote_in_zone = true;
        assert_eq!(describe_live(&status, true).1, "live-apart");
        status.game_syncing = true;
        assert_eq!(describe_live(&status, true).1, "live-syncing");
        status.game_syncing = false;
        status.remote_syncing = true;
        assert_eq!(describe_live(&status, false).1, "live-syncing");
        status.remote_syncing = false;
        status.game_joined = true;
        status.remote_joined = true;
        assert_eq!(describe_live(&status, true), (GOOD, "live-on"));
    }

    #[test]
    fn packs_and_times_read_naturally() {
        let t = english();
        assert_eq!(describe_packs(&t, &PackSet::new()), "Base game only");
        assert_eq!(
            describe_packs(&t, &["EP01".to_string()].into()),
            "1 pack: EP01"
        );
        assert_eq!(
            ago(&t, SystemTime::now() - Duration::from_secs(2 * 86_400)),
            "2 days ago"
        );
        assert_eq!(
            ago(&t, SystemTime::now() - Duration::from_secs(86_400)),
            "1 day ago"
        );
        assert_eq!(simoleons(940), "§940");
        assert_eq!(simoleons(12_500), "§12 500");
        assert_eq!(simoleons(1_000_000), "§1 000 000");
    }
}
