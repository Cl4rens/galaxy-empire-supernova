//! Convert screens from the APK to standard SWF files, as the engine loads them (for
//! screen-test, avm1-dump or a Flash decompiler), and pictures to PNG.
//!
//!     screen-convert <apk> <out dir> <screen.tfs or picture.png>...   e.g. ItemShop.tfs a1.png

use std::path::PathBuf;

use anyhow::Result;
use supernova_engine::assets::Assets;
use supernova_engine::convert::Converter;

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 4 {
        anyhow::bail!("usage: screen-convert <apk> <out dir> <screen.tfs>...");
    }
    let assets = Assets::open(&PathBuf::from(&args[1]))?;
    let out = PathBuf::from(&args[2]);
    std::fs::create_dir_all(&out)?;
    let mut converter = Converter::default();
    for name in &args[3..] {
        let name = name.trim_start_matches("data2/");
        let target = if name.ends_with(".tfs") {
            let target = out.join(name.replace(".tfs", ".swf"));
            std::fs::write(&target, converter.screen(&assets, &Default::default(), &format!("data2/{name}"))?)?;
            target
        } else {
            let picture = converter.picture(&assets, name).ok_or_else(|| anyhow::anyhow!("no picture {name}"))?;
            std::fs::write(out.join(name), picture)?;
            out.join(name)
        };
        println!("{}", target.display());
    }
    Ok(())
}
