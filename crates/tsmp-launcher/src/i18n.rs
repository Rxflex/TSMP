//! Translations of the launcher.
//!
//! Every language is one [Fluent](https://projectfluent.org) file,
//! `locales/<code>.ftl`, where `<code>` is a language tag such as `ru` or
//! `pt-BR`. The files are built into the program, and any file dropped into the
//! folder is picked up without touching the code. `en.ftl` is the reference:
//! text that a language lacks is shown in English.
//!
//! A `locales` folder next to the executable is read as well and wins over the
//! built-in files, so a translation can be tried without rebuilding.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use fluent_bundle::{FluentArgs, FluentBundle, FluentResource, FluentValue};
use rust_embed::RustEmbed;
use unic_langid::LanguageIdentifier;

#[derive(RustEmbed)]
#[folder = "locales/"]
#[include = "*.ftl"]
struct BuiltIn;

/// The language every other one falls back to.
pub const REFERENCE: &str = "en";

/// The message holding a language's own name.
const NAME_KEY: &str = "language-name";

/// Formats a message: `tr!(i18n, "key")` or `tr!(i18n, "key", n = 3, name = text)`.
macro_rules! tr {
    ($i18n:expr, $key:expr) => {
        $i18n.text($key, &[])
    };
    ($i18n:expr, $key:expr, $($name:ident = $value:expr),+ $(,)?) => {
        $i18n.text($key, &[$((stringify!($name), ::fluent_bundle::FluentValue::from($value))),+])
    };
}
pub(crate) use tr;

pub struct Language {
    /// The language tag, which is also the file name: `pt-BR`.
    pub code: String,
    /// The language's name in that language.
    pub name: String,
    bundle: FluentBundle<FluentResource>,
}

impl Language {
    /// Builds a language from its files; later sources override earlier ones.
    /// Lines that do not parse are skipped, the rest of the file is used.
    fn new(code: &str, sources: Vec<String>) -> Option<Self> {
        let id: LanguageIdentifier = code.parse().ok()?;
        let mut bundle = FluentBundle::new(vec![id]);
        // Without this Fluent wraps every placeholder in invisible direction
        // marks, which the text renderer draws as boxes.
        bundle.set_use_isolating(false);
        for source in sources {
            let resource = FluentResource::try_new(source).unwrap_or_else(|(partial, _)| partial);
            bundle.add_resource_overriding(resource);
        }
        let mut language = Self {
            code: code.to_string(),
            name: String::new(),
            bundle,
        };
        language.name = language.format(NAME_KEY, &[]).unwrap_or_else(|| code.to_string());
        Some(language)
    }

    fn format(&self, key: &str, args: &[(&str, FluentValue)]) -> Option<String> {
        let pattern = self.bundle.get_message(key)?.value()?;
        let mut fluent_args = FluentArgs::new();
        for (name, value) in args {
            fluent_args.set(*name, value.clone());
        }
        let mut errors = Vec::new();
        let text = self.bundle.format_pattern(pattern, Some(&fluent_args), &mut errors);
        errors.is_empty().then(|| text.into_owned())
    }
}

pub struct I18n {
    languages: Vec<Language>,
    current: usize,
    reference: usize,
}

impl I18n {
    /// The built-in languages plus the folder next to the executable, set to
    /// the language chosen earlier or else the system's.
    pub fn load() -> Self {
        let overrides = std::env::current_exe()
            .ok()
            .and_then(|exe| Some(exe.parent()?.join("locales")));
        let mut i18n = Self::from_sources(overrides.as_deref());
        let wanted = saved_language().into_iter().chain(sys_locale::get_locales());
        for tag in wanted {
            if let Some(index) = closest(i18n.languages.iter().map(|l| l.code.as_str()), &tag) {
                i18n.current = index;
                break;
            }
        }
        i18n
    }

    fn from_sources(overrides: Option<&Path>) -> Self {
        let mut sources: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for file in BuiltIn::iter() {
            let (Some(code), Some(data)) = (file.strip_suffix(".ftl"), BuiltIn::get(&file)) else {
                continue;
            };
            if let Ok(text) = String::from_utf8(data.data.into_owned()) {
                sources.entry(code.to_string()).or_default().push(text);
            }
        }
        for path in overrides.and_then(|dir| fs::read_dir(dir).ok()).into_iter().flatten().flatten() {
            let path = path.path();
            if path.extension().is_some_and(|e| e == "ftl")
                && let (Some(code), Ok(text)) = (path.file_stem().and_then(|s| s.to_str()), fs::read_to_string(&path))
            {
                sources.entry(code.to_string()).or_default().push(text);
            }
        }
        let mut languages: Vec<Language> = sources
            .into_iter()
            .filter_map(|(code, sources)| Language::new(&code, sources))
            .collect();
        languages.sort_by_cached_key(|language| language.name.to_lowercase());
        let reference = languages
            .iter()
            .position(|language| language.code == REFERENCE)
            .expect("locales/en.ftl is built in");
        Self {
            languages,
            current: reference,
            reference,
        }
    }

    pub fn languages(&self) -> &[Language] {
        &self.languages
    }

    pub fn current(&self) -> &Language {
        &self.languages[self.current]
    }

    /// Switches language and remembers the choice for the next start.
    pub fn choose(&mut self, code: &str) {
        if let Some(index) = self.languages.iter().position(|language| language.code == code) {
            self.current = index;
            save_language(code);
        }
    }

    /// The message in the current language, else in English, else its key.
    pub fn text(&self, key: &str, args: &[(&str, FluentValue)]) -> String {
        self.languages[self.current]
            .format(key, args)
            .or_else(|| self.languages[self.reference].format(key, args))
            .unwrap_or_else(|| key.to_string())
    }
}

/// The index of the language closest to `wanted`: the same tag, else the same
/// language in another region (`ru-RU` finds `ru`, `pt-PT` finds `pt-BR`).
fn closest<'a>(codes: impl Iterator<Item = &'a str> + Clone, wanted: &str) -> Option<usize> {
    let wanted = wanted.replace('_', "-").to_lowercase();
    let language = |tag: &str| tag.split('-').next().unwrap_or_default().to_string();
    codes
        .clone()
        .position(|code| code.to_lowercase() == wanted)
        .or_else(|| codes.clone().position(|code| language(&code.to_lowercase()) == language(&wanted)))
}

/// `%APPDATA%\TSMP\language`, or the equivalent elsewhere.
fn settings_file() -> Option<PathBuf> {
    let base = std::env::var_os("APPDATA")
        .or_else(|| std::env::var_os("XDG_CONFIG_HOME"))
        .map(PathBuf::from)
        .or_else(|| Some(PathBuf::from(std::env::var_os("HOME")?).join(".config")))?;
    Some(base.join("TSMP").join("language"))
}

fn saved_language() -> Option<String> {
    let text = fs::read_to_string(settings_file()?).ok()?;
    let code = text.trim();
    (!code.is_empty()).then(|| code.to_string())
}

fn save_language(code: &str) {
    // Not being able to remember the choice is not worth interrupting for.
    if let Some(path) = settings_file() {
        let _ = path.parent().map(fs::create_dir_all);
        let _ = fs::write(path, code);
    }
}

#[cfg(test)]
pub(crate) fn english() -> I18n {
    I18n::from_sources(None)
}

#[cfg(test)]
mod tests {
    use std::collections::{BTreeMap, BTreeSet};

    use fluent_syntax::ast::{Entry, Expression, InlineExpression, Pattern, PatternElement};

    use super::*;

    /// Every message of a file with the variables it uses.
    fn messages(code: &str) -> BTreeMap<String, BTreeSet<String>> {
        let data = BuiltIn::get(&format!("{code}.ftl")).unwrap().data.into_owned();
        let resource = FluentResource::try_new(String::from_utf8(data).unwrap())
            .unwrap_or_else(|(_, errors)| panic!("{code}.ftl does not parse: {errors:?}"));
        let mut found = BTreeMap::new();
        for entry in resource.entries() {
            if let Entry::Message(message) = entry {
                let mut variables = BTreeSet::new();
                let value = message.value.as_ref().unwrap_or_else(|| panic!("{code}: {} is empty", message.id.name));
                collect(value, &mut variables);
                assert!(
                    found.insert(message.id.name.to_string(), variables).is_none(),
                    "{code}: {} is defined twice",
                    message.id.name
                );
            }
        }
        found
    }

    fn collect(pattern: &Pattern<&str>, variables: &mut BTreeSet<String>) {
        for element in &pattern.elements {
            let PatternElement::Placeable { expression } = element else {
                continue;
            };
            match expression {
                Expression::Inline(inline) => variable(inline, variables),
                Expression::Select { selector, variants } => {
                    variable(selector, variables);
                    for variant in variants {
                        collect(&variant.value, variables);
                    }
                }
            }
        }
    }

    fn variable(inline: &InlineExpression<&str>, variables: &mut BTreeSet<String>) {
        if let InlineExpression::VariableReference { id } = inline {
            variables.insert(id.name.to_string());
        }
    }

    fn codes() -> Vec<String> {
        BuiltIn::iter().filter_map(|file| Some(file.strip_suffix(".ftl")?.to_string())).collect()
    }

    #[test]
    fn every_language_has_the_messages_and_variables_of_english() {
        let reference = messages(REFERENCE);
        for code in codes() {
            assert_eq!(messages(&code), reference, "{code}.ftl differs from en.ftl");
        }
    }

    #[test]
    fn every_message_formats_for_every_number() {
        let reference = messages(REFERENCE);
        for code in codes() {
            let language = Language::new(&code, vec![
                String::from_utf8(BuiltIn::get(&format!("{code}.ftl")).unwrap().data.into_owned()).unwrap(),
            ])
            .unwrap_or_else(|| panic!("{code} is not a language tag"));
            assert_ne!(language.name, code, "{code}.ftl has no {NAME_KEY}");
            for (key, variables) in &reference {
                for number in [0u32, 1, 2, 5, 11, 21, 100] {
                    let args: Vec<(&str, FluentValue)> =
                        variables.iter().map(|name| (name.as_str(), FluentValue::from(number))).collect();
                    let text = language.format(key, &args);
                    assert!(text.is_some_and(|text| !text.trim().is_empty()), "{code}: {key} with {number}");
                }
            }
        }
    }

    #[test]
    fn plurals_follow_the_language() {
        let mut i18n = english();
        assert_eq!(tr!(i18n, "packs-count", n = 1), "1 pack");
        assert_eq!(tr!(i18n, "packs-count", n = 4), "4 packs");
        i18n.current = i18n.languages.iter().position(|l| l.code == "ru").unwrap();
        let words: Vec<String> = [1, 3, 5, 21].iter().map(|n| tr!(i18n, "packs-count", n = *n)).collect();
        assert_eq!(words[0].split(' ').nth(1), words[3].split(' ').nth(1));
        assert_ne!(words[0].split(' ').nth(1), words[1].split(' ').nth(1));
        assert_ne!(words[1].split(' ').nth(1), words[2].split(' ').nth(1));
    }

    #[test]
    fn missing_text_falls_back_to_english_then_to_the_key() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("tlh.ftl"), "language-name = tlhIngan Hol\nback = chegh\n").unwrap();
        fs::write(dir.path().join("en.ftl"), "back = Go back\n").unwrap();
        let mut i18n = I18n::from_sources(Some(dir.path()));
        assert_eq!(tr!(i18n, "back"), "Go back", "a file next to the program overrides the built-in one");
        assert_eq!(tr!(i18n, "cancel"), "Cancel", "and keeps the messages it does not mention");
        i18n.current = i18n.languages.iter().position(|l| l.code == "tlh").unwrap();
        assert_eq!(i18n.current().name, "tlhIngan Hol");
        assert_eq!(tr!(i18n, "back"), "chegh");
        assert_eq!(tr!(i18n, "cancel"), "Cancel");
        assert_eq!(tr!(i18n, "no-such-message"), "no-such-message");
    }

    #[test]
    fn the_system_language_finds_the_closest_file() {
        let codes = ["de", "en", "pt-BR", "ru", "zh-CN"];
        let find = |wanted| closest(codes.iter().copied(), wanted).map(|index| codes[index]);
        assert_eq!(find("ru-RU"), Some("ru"));
        assert_eq!(find("pt_BR"), Some("pt-BR"));
        assert_eq!(find("pt-PT"), Some("pt-BR"));
        assert_eq!(find("zh-Hans-CN"), Some("zh-CN"));
        assert_eq!(find("ja-JP"), None);
    }
}
