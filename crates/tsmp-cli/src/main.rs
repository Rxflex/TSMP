//! Diagnostic command line for the launcher core.
//!
//!     tsmp-cli info              show the detected game, packs and mod settings
//!     tsmp-cli manifest [DIR]    scan a Mods folder (default: the game's) and print a summary
//!     tsmp-cli save FILE         show what the launcher reads from a save game

use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Instant;

use tsmp_core::game::GameInstall;
use tsmp_core::manifest::Manifest;
use tsmp_core::packs;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("info") => info(),
        Some("manifest") => manifest(args.get(1).map(PathBuf::from)),
        Some("save") if args.len() == 2 => save(PathBuf::from(&args[1])),
        _ => {
            eprintln!("usage: tsmp-cli info | manifest [DIR] | save FILE");
            return ExitCode::from(2);
        }
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::FAILURE
        }
    }
}

fn info() -> tsmp_core::Result<()> {
    let game = GameInstall::detect()?;
    let packs = packs::installed_packs(&game.install_dir)?;
    println!("install dir:  {}", game.install_dir.display());
    println!("user dir:     {}", game.user_dir.display());
    println!("version:      {}", game.version.as_deref().unwrap_or("unknown"));
    println!("script mods:  {}", if game.script_mods_enabled()? { "enabled" } else { "DISABLED" });
    println!(
        "packs ({}):    {}",
        packs.len(),
        if packs.is_empty() { "base game only".to_string() } else { packs.into_iter().collect::<Vec<_>>().join(" ") }
    );
    Ok(())
}

fn save(path: PathBuf) -> tsmp_core::Result<()> {
    let started = Instant::now();
    let preview = tsmp_core::preview::read_preview(&path)?;
    let or_none = |text: &Option<String>| text.clone().unwrap_or_else(|| "-".to_string());
    let picture = |bytes: &Option<Vec<u8>>| bytes.as_ref().map_or("-".to_string(), |b| format!("{} bytes", b.len()));
    println!("name:         {}", or_none(&preview.name));
    println!("household:    {}", or_none(&preview.household));
    println!("sims:         {}", preview.sims.join(", "));
    println!("funds:        {}", preview.funds.map_or("-".to_string(), |funds| funds.to_string()));
    println!("lot:          {}", or_none(&preview.lot));
    println!("portrait:     {}", picture(&preview.portrait));
    println!("lot picture:  {}", picture(&preview.lot_picture));
    println!("read in {:.0?}", started.elapsed());
    Ok(())
}

fn manifest(dir: Option<PathBuf>) -> tsmp_core::Result<()> {
    let game = GameInstall::detect().ok();
    let dir = match (dir, &game) {
        (Some(dir), _) => dir,
        (None, Some(game)) => game.mods_dir(),
        (None, None) => return Err(tsmp_core::Error::GameNotFound),
    };
    // The cache sits beside the mod's other data, outside the scanned folder.
    let cache_path = game.map(|g| g.user_dir.join("tsmp").join("mods-manifest.json"));
    let cache = cache_path.as_deref().and_then(|p| Manifest::load(p).ok());

    let started = Instant::now();
    let manifest = Manifest::scan(&dir, cache.as_ref())?;
    println!(
        "{}: {} files, {:.2} GiB, scanned in {:.1}s{}",
        dir.display(),
        manifest.files.len(),
        manifest.total_bytes() as f64 / (1u64 << 30) as f64,
        started.elapsed().as_secs_f64(),
        if cache.is_some() { " (with cache)" } else { "" }
    );
    if let Some(path) = cache_path {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|source| tsmp_core::Error::Io { path: parent.to_path_buf(), source })?;
        }
        manifest.save(&path)?;
    }
    Ok(())
}
