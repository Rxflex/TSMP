//! Working out which packs two players have in common.
//!
//! Pack content lives in a folder per pack directly under the install dir
//! (`EP01`, `GP04`, `SP12`, `FP01`). The `Delta/` folder also has one folder
//! per pack, but it holds patch data for every pack whether owned or not, so
//! it says nothing about ownership.

use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

use crate::{Result, io_err};

pub type PackSet = BTreeSet<String>;

/// `EP01`, `GP12`, `SP87`, `FP01`.
pub fn is_pack_code(name: &str) -> bool {
    let bytes = name.as_bytes();
    bytes.len() == 4
        && matches!(&bytes[..2], b"EP" | b"GP" | b"SP" | b"FP")
        && bytes[2..].iter().all(u8::is_ascii_digit)
}

/// The name a pack is sold under, for the packs whose code is well known.
/// Kits and newer packs are shown by their code.
pub fn pack_name(code: &str) -> Option<&'static str> {
    Some(match code {
        "EP01" => "Get to Work",
        "EP02" => "Get Together",
        "EP03" => "City Living",
        "EP04" => "Cats & Dogs",
        "EP05" => "Seasons",
        "EP06" => "Get Famous",
        "EP07" => "Island Living",
        "EP08" => "Discover University",
        "EP09" => "Eco Lifestyle",
        "EP10" => "Snowy Escape",
        "EP11" => "Cottage Living",
        "EP12" => "High School Years",
        "EP13" => "Growing Together",
        "EP14" => "Horse Ranch",
        "EP15" => "For Rent",
        "EP16" => "Lovestruck",
        "EP17" => "Life & Death",
        "EP18" => "Businesses & Hobbies",
        "GP01" => "Outdoor Retreat",
        "GP02" => "Spa Day",
        "GP03" => "Dine Out",
        "GP04" => "Vampires",
        "GP05" => "Parenthood",
        "GP06" => "Jungle Adventure",
        "GP07" => "StrangerVille",
        "GP08" => "Realm of Magic",
        "GP09" => "Journey to Batuu",
        "GP10" => "Dream Home Decorator",
        "GP11" => "My Wedding Stories",
        "GP12" => "Werewolves",
        "SP01" => "Luxury Party",
        "SP02" => "Perfect Patio",
        "SP03" => "Cool Kitchen",
        "SP04" => "Spooky",
        "SP05" => "Movie Hangout",
        "SP06" => "Romantic Garden",
        "SP07" => "Kids Room",
        "SP08" => "Backyard",
        "SP09" => "Vintage Glamour",
        "SP10" => "Bowling Night",
        "SP11" => "Fitness",
        "SP12" => "Toddler",
        "SP13" => "Laundry Day",
        "SP14" => "My First Pet",
        "SP15" => "Moschino",
        "SP16" => "Tiny Living",
        "SP17" => "Nifty Knitting",
        "SP18" => "Paranormal",
        "FP01" => "Holiday Celebration",
        _ => return None,
    })
}

pub fn installed_packs(install_dir: &Path) -> Result<PackSet> {
    let mut packs = PackSet::new();
    for entry in fs::read_dir(install_dir).map_err(io_err(install_dir))? {
        let entry = entry.map_err(io_err(install_dir))?;
        let name = entry.file_name().to_string_lossy().to_uppercase();
        if is_pack_code(&name) && entry.path().is_dir() {
            packs.insert(name);
        }
    }
    Ok(packs)
}

/// Packs every player has.
pub fn common_packs<'a>(players: impl IntoIterator<Item = &'a PackSet>) -> PackSet {
    let mut players = players.into_iter();
    let Some(first) = players.next() else {
        return PackSet::new();
    };
    players.fold(first.clone(), |common, packs| {
        common.intersection(packs).cloned().collect()
    })
}

/// The launch argument that makes a player with `local` packs run with only
/// the `common` ones, or `None` when nothing has to be disabled.
pub fn disable_packs_arg(local: &PackSet, common: &PackSet) -> Option<String> {
    let extra: Vec<&str> = local.difference(common).map(String::as_str).collect();
    (!extra.is_empty()).then(|| format!("-disablepacks:{}", extra.join(",")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn set(codes: &[&str]) -> PackSet {
        codes.iter().map(|c| c.to_string()).collect()
    }

    #[test]
    fn recognises_pack_codes() {
        for code in ["EP01", "GP12", "SP87", "FP01"] {
            assert!(is_pack_code(code), "{code}");
        }
        for name in ["Data", "Delta", "EP1", "EP001", "XP01", "EPAB", "Game"] {
            assert!(!is_pack_code(name), "{name}");
        }
    }

    #[test]
    fn scans_only_pack_folders_at_the_root() {
        let dir = tempfile::tempdir().unwrap();
        for name in ["Data", "Game", "EP01", "gp04", "Delta/EP02", "Delta/SP03"] {
            fs::create_dir_all(dir.path().join(name)).unwrap();
        }
        fs::write(dir.path().join("SP09"), b"a file, not a pack").unwrap();
        assert_eq!(installed_packs(dir.path()).unwrap(), set(&["EP01", "GP04"]));
    }

    #[test]
    fn common_is_the_intersection_of_all_players() {
        let a = set(&["EP01", "EP02", "GP01"]);
        let b = set(&["EP02", "GP01", "SP05"]);
        let c = set(&["GP01", "EP02"]);
        assert_eq!(common_packs([&a, &b, &c]), set(&["EP02", "GP01"]));
        assert_eq!(common_packs([&a, &PackSet::new()]), PackSet::new());
        assert_eq!(common_packs([]), PackSet::new());
    }

    #[test]
    fn disables_exactly_the_packs_others_lack() {
        let mine = set(&["EP01", "EP02", "GP01"]);
        let common = set(&["EP02"]);
        assert_eq!(
            disable_packs_arg(&mine, &common).as_deref(),
            Some("-disablepacks:EP01,GP01")
        );
        assert_eq!(disable_packs_arg(&common, &common), None);
    }
}
