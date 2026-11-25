//! A one-line text field: what the player types or pastes, kept to the
//! characters it may hold.

use gpui::prelude::*;
use gpui::{App, Div, FocusHandle, KeyDownEvent, Stateful, Window, div, px, rgb};

use crate::ui::{DANGER, LAMP, LINE, MUTED, RAIL};

/// What a key press did to the field.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Typed {
    Changed,
    /// Enter: the player is done.
    Submitted,
    Ignored,
}

pub struct TextField {
    text: String,
    focus: FocusHandle,
    /// The text was found wrong; shown until the next change.
    refused: bool,
    limit: usize,
    accepts: fn(char) -> bool,
}

impl TextField {
    pub fn new(cx: &App, limit: usize, accepts: fn(char) -> bool) -> Self {
        Self {
            text: String::new(),
            focus: cx.focus_handle(),
            refused: false,
            limit,
            accepts,
        }
    }

    pub fn with_text(mut self, text: &str) -> Self {
        self.insert(text);
        self
    }

    pub fn text(&self) -> &str {
        &self.text
    }

    pub fn refused(&self) -> bool {
        self.refused
    }

    pub fn refuse(&mut self) {
        self.refused = true;
    }

    /// Typing: characters it accepts, backspace, Enter, and pasting with
    /// Ctrl+V (Cmd+V on a Mac).
    pub fn key(&mut self, event: &KeyDownEvent, cx: &App) -> Typed {
        let keystroke = &event.keystroke;
        if keystroke.modifiers.control || keystroke.modifiers.platform {
            if keystroke.key != "v" {
                return Typed::Ignored;
            }
            let pasted = cx.read_from_clipboard().and_then(|item| item.text()).unwrap_or_default();
            self.insert(&pasted);
        } else {
            match keystroke.key.as_str() {
                "backspace" => {
                    self.text.pop();
                }
                "enter" => return Typed::Submitted,
                _ => match &keystroke.key_char {
                    Some(typed) => self.insert(typed),
                    None => return Typed::Ignored,
                },
            }
        }
        self.refused = false;
        Typed::Changed
    }

    fn insert(&mut self, typed: &str) {
        let room = self.limit.saturating_sub(self.text.chars().count());
        self.text.extend(typed.trim().chars().filter(|c| (self.accepts)(*c)).take(room));
    }

    /// The field, `width` wide; its owner adds what a key press does.
    pub fn view(&self, id: &'static str, placeholder: &str, width: f32, window: &Window) -> Stateful<Div> {
        let focused = self.focus.is_focused(window);
        div()
            .id(id)
            .track_focus(&self.focus)
            .w(px(width))
            .h(px(48.))
            .px_4()
            .flex_none()
            .flex()
            .items_center()
            .overflow_hidden()
            .rounded(px(10.))
            .bg(rgb(RAIL))
            .border_1()
            .border_color(rgb(if self.refused {
                DANGER
            } else if focused {
                LAMP
            } else {
                LINE
            }))
            .cursor_text()
            .child(if self.text.is_empty() && !focused {
                div().text_color(rgb(MUTED)).child(placeholder.to_string())
            } else {
                div().flex_none().child(self.text.clone())
            })
            .when(focused, |this| this.child(div().flex_none().w(px(1.)).h(px(22.)).bg(rgb(LAMP))))
    }
}
