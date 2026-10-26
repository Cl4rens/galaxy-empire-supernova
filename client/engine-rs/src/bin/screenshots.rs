//! Play the game with the Ruffle screens, without a window, and save what it shows at each
//! step of the login, then after each tap in SUPERNOVA_TAPS ("x,y;x,y" in the game's 640x960
//! coordinates).
//!
//!     screenshots <apk> [server] [save dir] [out dir]

use std::path::PathBuf;

use anyhow::{Result, bail};
use supernova_engine::engine::{Engine, Touch};
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
    // SUPERNOVA_TAPS="586,255;64,38": taps in the game's 640x960 coordinates, a picture after each
    let taps = std::env::var("SUPERNOVA_TAPS").unwrap_or_default();
    // "type:text" types into the open text box instead ("\n" ends it, "\b" deletes)
    for (n, tap) in taps.split(';').filter(|t| !t.is_empty()).enumerate() {
        if let Some(text) = tap.strip_prefix("type:") {
            engine.text_input(&text.replace("\\n", "\n").replace("\\b", "\u{8}"))?;
            engine.run(1.0, |_| false)?;
            println!("keyboard: {:?}", engine.take_keyboard());
            shot(&mut engine, &out, &format!("5-tap{}", n + 1))?;
            continue;
        }
        // "drag:x1,y1,x2,y2" moves a finger from one point to the other
        if let Some(points) = tap.strip_prefix("drag:") {
            let p: Vec<f64> = points.split(',').map(|v| v.trim().parse()).collect::<Result<_, _>>()?;
            let [x1, y1, x2, y2] = p[..] else { bail!("drag needs four numbers") };
            engine.touch(Touch::Pressed, x1, y1)?;
            for step in 1..=10 {
                let t = step as f64 / 10.0;
                engine.run(0.03, |_| false)?;
                engine.touch(Touch::Moved, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)?;
            }
            engine.touch(Touch::Released, x2, y2)?;
            engine.run(2.0, |_| false)?;
            shot(&mut engine, &out, &format!("5-tap{}", n + 1))?;
            continue;
        }
        let (x, y) = tap.split_once(',').unwrap_or_default();
        let (x, y) = (x.trim().parse()?, y.trim().parse()?);
        engine.touch(Touch::Pressed, x, y)?;
        engine.run(0.1, |_| false)?;
        engine.touch(Touch::Released, x, y)?;
        engine.run(3.0, |_| false)?;
        shot(&mut engine, &out, &format!("5-tap{}", n + 1))?;
    }
    // SUPERNOVA_PROBE="GameUIQuest_F0:SC_TEXT:textHeight,_height": properties of the text fields
    // of a screen whose name contains the given part
    if let Ok(probe) = std::env::var("SUPERNOVA_PROBE") {
        let mut parts = probe.splitn(3, ':');
        let (object, name, properties) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""), parts.next().unwrap_or(""));
        let mut state = engine.state.borrow_mut();
        let screens = state.screens.as_any().downcast_mut::<RuffleScreens>().unwrap();
        if let Some(player) = screens.player(object) {
            let mut p = player.lock().unwrap();
            // "@path|path": these objects instead of text fields
            let paths: Vec<String> = match name.strip_prefix('@') {
                Some(list) => list.split('|').map(str::to_string).collect(),
                None => p.text_field_paths(name),
            };
            for path in paths {
                let values: Vec<String> =
                    properties.split(',').map(|prop| format!("{prop}={:?}", p.get_avm1_property(&path, prop))).collect();
                println!("{path}: {}", values.join(" "));
            }
        }
    }
    Ok(())
}
