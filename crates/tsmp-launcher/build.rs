//! Puts the in-game mod inside the launcher, so that `tsmp.exe` is the only
//! file a player needs.
//!
//! The mod is Python 3.7 bytecode and is built by `mod/build.py`, not by Cargo.
//! When it has not been built, the launcher still compiles, and looks for
//! `tsmp.ts4script` beside itself at run time instead.

use std::{env, fs, path::PathBuf};

fn main() {
    let built = env::var_os("TSMP_MOD").map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap()).join("../../dist/tsmp.ts4script")
    });
    println!("cargo:rerun-if-env-changed=TSMP_MOD");
    println!("cargo:rerun-if-changed={}", built.display());
    let bytes = fs::read(&built).unwrap_or_else(|_| {
        println!("cargo:warning=the mod is not built (run mod/build.py); the launcher will not contain it");
        Vec::new()
    });
    let target = PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("tsmp.ts4script");
    fs::write(target, bytes).unwrap();
}
