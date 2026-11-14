//! The TSMP launcher.

// No console window behind the launcher in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod i18n;
mod mappings;
mod model;
mod ui;
mod update;

use std::borrow::Cow;

use gpui::{
    App, AppContext, Application, AssetSource, Bounds, SharedString, TitlebarOptions, WindowBounds, WindowOptions,
    px, size,
};
use rust_embed::RustEmbed;

/// Icons, drawings and fonts, built into the program.
#[derive(RustEmbed)]
#[folder = "assets/"]
#[include = "icons/*.svg"]
#[include = "art/*.jpg"]
#[include = "fonts/*.ttf"]
struct Assets;

impl AssetSource for Assets {
    fn load(&self, path: &str) -> gpui::Result<Option<Cow<'static, [u8]>>> {
        Ok(Self::get(path).map(|file| file.data))
    }

    fn list(&self, path: &str) -> gpui::Result<Vec<SharedString>> {
        Ok(Self::iter().filter(|file| file.starts_with(path)).map(|file| file.to_string().into()).collect())
    }
}

fn main() {
    Application::new().with_assets(Assets).run(|cx: &mut App| {
        let fonts = Assets::iter()
            .filter(|file| file.ends_with(".ttf"))
            .filter_map(|file| Some(Assets::get(&file)?.data))
            .collect();
        cx.text_system().add_fonts(fonts).expect("the built-in fonts load");

        let bounds = Bounds::centered(None, size(px(1120.), px(840.)), cx);
        let options = WindowOptions {
            window_bounds: Some(WindowBounds::Windowed(bounds)),
            window_min_size: Some(size(px(980.), px(700.))),
            // The launcher draws its own title bar and window buttons.
            titlebar: Some(TitlebarOptions {
                title: Some("TSMP".into()),
                appears_transparent: true,
                traffic_light_position: None,
            }),
            ..Default::default()
        };
        cx.open_window(options, |_, cx| cx.new(ui::Launcher::new))
            .expect("the launcher window opens");
        cx.on_window_closed(|cx| cx.quit()).detach();
        cx.activate(true);
    });
}
