//! Validating file paths received from the network.
//!
//! The guest writes files named by the host, so a path must never be able to
//! leave the folder being synchronised.

use std::path::{Path, PathBuf};

const FORBIDDEN_CHARS: [char; 8] = ['\\', ':', '*', '?', '"', '<', '>', '|'];
const RESERVED_NAMES: [&str; 22] = [
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
    "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

/// Join a `/`-separated relative path onto `root`, or `None` if the path could
/// resolve outside `root` or name something other than a regular file on
/// Windows (drive letters, alternate streams, device names, `..`).
pub fn safe_join(root: &Path, relative: &str) -> Option<PathBuf> {
    if relative.is_empty() {
        return None;
    }
    let mut path = root.to_path_buf();
    for component in relative.split('/') {
        if !is_safe_component(component) {
            return None;
        }
        path.push(component);
    }
    Some(path)
}

fn is_safe_component(component: &str) -> bool {
    if component.is_empty() || component == "." || component == ".." {
        return false;
    }
    // Only the ASCII control characters are refused: Windows cannot store them.
    // The ones above U+007F are legal and turn up in file names whose encoding
    // was mangled on the way from a download site.
    if component.chars().any(|c| c.is_ascii_control() || FORBIDDEN_CHARS.contains(&c)) {
        return false;
    }
    // Windows silently strips these, so "x." and "x" would be the same file.
    if component.ends_with('.') || component.ends_with(' ') {
        return false;
    }
    let stem = component.split('.').next().unwrap_or(component);
    !RESERVED_NAMES.iter().any(|name| name.eq_ignore_ascii_case(stem))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_ordinary_nested_paths() {
        let root = Path::new("root");
        assert_eq!(
            safe_join(root, "a/b c/мод [v1.2].package"),
            Some(root.join("a").join("b c").join("мод [v1.2].package"))
        );
        assert_eq!(safe_join(root, "Resource.cfg"), Some(root.join("Resource.cfg")));
        let mangled = "Tops (M) \u{e2}\u{80}\u{a2} CF \u{e2}\u{80}\u{94} Base.package";
        assert_eq!(safe_join(root, mangled), Some(root.join(mangled)));
    }

    #[test]
    fn rejects_paths_that_could_escape_or_alias() {
        let root = Path::new("root");
        for path in [
            "",
            "..",
            "../x",
            "a/../../x",
            "a/./b",
            "/abs",
            "a//b",
            "a/",
            "C:/x",
            "C:x",
            "a\\b",
            "\\\\server\\share",
            "file.txt:stream",
            "x.",
            "x ",
            "NUL",
            "con.txt",
            "a/COM1",
            "a\0b",
            "a\nb",
        ] {
            assert_eq!(safe_join(root, path), None, "{path:?}");
        }
    }
}
