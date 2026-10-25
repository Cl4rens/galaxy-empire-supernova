//! Play the game with the Ruffle screens, without a window, and save what it shows at each
//! step of the login.
//!
//!     screenshots <apk> [server] [save dir] [out dir]

use std::path::PathBuf;

use anyhow::{Result, bail};
use supernova_engine::engine::Engine;
use supernova_engine::screens::{HEIGHT, RuffleScreens, WIDTH};

fn shot(engine: &mut Engine, out: &PathBuf, name: &str) -> Result<()> {
    engine.render()?;
    let mut state = engine.state.borrow_mut();
    let screens = state.screens.as_any().downcast_mut::<RuffleScreens>().unwrap();
    let rendered = screens.rendered.clone();
    let layers = screens.layers();
    let mut image = image::RgbaImage::from_pixel(WIDTH, HEIGHT, image::Rgba([0, 0, 0, 255]));
    for (object, layer) in &layers {
        image::imageops::overlay(&mut image, layer, 0, 0);
        if std::env::var_os("SUPERNOVA_LAYERS").is_some() {
            layer.save(out.join(format!("{name}-{object}.png")))?;
        }
    }
    image.save(out.join(format!("{name}.png")))?;
    let covered: Vec<String> = layers.iter()
        .map(|(o, l)| format!("{o} {}%", l.pixels().filter(|p| p[3] > 0).count() * 100 / (WIDTH * HEIGHT) as usize))
        .collect();
    println!("{name}: {rendered:?} {covered:?}");
    Ok(())
}

fn main() -> Result<()> {
    tracing_subscriber::fmt().with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "warn".into())).init();
    let args: Vec<String> = std::env::args().collect();
    let apk = PathBuf::from(args.get(1).map(String::as_str).unwrap_or("../../apk/galaxy-empire-1-9-36.apk"));
    let server = args.get(2).map(String::as_str).unwrap_or("http://127.0.0.1:8000");
    let save = PathBuf::from(args.get(3).map(String::as_str).unwrap_or("client-save"));
    let out = PathBuf::from(args.get(4).map(String::as_str).unwrap_or("screenshots"));
    std::fs::create_dir_all(&out)?;
    let mut engine = Engine::new(apk, server, save, "en", Box::new(RuffleScreens::new()?))?;
    engine.boot()?;
    engine.run(30.0, |e| e.active_objects().iter().any(|o| o == "GameObjectLoading"))?;
    engine.run(1.0, |_| false)?;
    shot(&mut engine, &out, "1-loading")?;
    if !engine.run(30.0, |e| e.active_objects().iter().any(|o| o == "GameUISplash"))? {
        bail!("the game did not reach its title screen");
    }
    engine.run(3.0, |_| false)?;
    shot(&mut engine, &out, "3-title")?;
    engine.press("GameUISplash", "btnStartPressed", "")?;
    if !engine.run(30.0, |e| e.state_name().as_deref() == Some("GameStatePlanet"))? {
        bail!("login failed");
    }
    engine.run(5.0, |_| false)?;
    shot(&mut engine, &out, "4-city")?;
    let mut state = engine.state.borrow_mut();
    {
        let screens = state.screens.as_any().downcast_mut::<RuffleScreens>().unwrap();
        for object in ["GameCommonInterface_F0", "GameNews_F0", "GameCommonBG_F0"] {
            if let Some(p) = screens.player(object) {
                let mut p = p.lock().unwrap();
                let depth = p.call_avm1_function("_root", "getNextHighestDepth", vec![]);
                let bounds = p.call_avm1_function("_root", "getBounds", vec![]);
                println!("{object}: depth {depth:?} bounds {bounds:?}");
            }
        }
    }
    println!("missing: {:?}", state.screens.as_any().downcast_mut::<RuffleScreens>().unwrap().missing);
    Ok(())
}
