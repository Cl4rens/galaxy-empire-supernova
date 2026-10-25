//! Boot the game without graphics, log in and print what the city shows.
//!
//!     headless <apk> [server] [save dir]

use std::path::PathBuf;

use anyhow::{Result, bail};
use supernova_engine::engine::{Engine, Recorder};

fn status(engine: &Engine) -> Result<String> {
    Ok(engine
        .lua
        .load(
            r#"local p = GameGlobalData:GetMainPlanetData()
            local e = GameGlobalData:GetData("empire")
            return string.format("%s on %s: metal %d, crystal %d, deuterium %d, dark matter %d",
              tostring(GameGlobalData:GetData("username")), tostring(p.planet_name), p.metal, p.crystal,
              p.deuterium, e.dark_matter_count)"#,
        )
        .eval()?)
}

fn main() -> Result<()> {
    tracing_subscriber::fmt().with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "warn".into())).init();
    let args: Vec<String> = std::env::args().collect();
    let apk = PathBuf::from(args.get(1).map(String::as_str).unwrap_or("../../apk/galaxy-empire-1-9-36.apk"));
    let server = args.get(2).map(String::as_str).unwrap_or("http://127.0.0.1:8000");
    let save = PathBuf::from(args.get(3).map(String::as_str).unwrap_or("client-save"));
    let mut engine = Engine::new(apk, server, save, "en", Box::new(Recorder::default()))?;
    engine.boot()?;
    if !engine.run(30.0, |e| e.active_objects().iter().any(|o| o == "GameObjectLoading"))? {
        bail!("the game did not reach its loading screen");
    }
    engine.press("GameObjectLoading", "LoadingOver", "")?;
    if !engine.run(30.0, |e| e.active_objects().iter().any(|o| o == "GameUISplash"))? {
        bail!("the game did not reach its title screen");
    }
    engine.run(1.0, |_| false)?;
    engine.press("GameUISplash", "btnStartPressed", "")?;
    if !engine.run(30.0, |e| e.state_name().as_deref() == Some("GameStatePlanet"))? {
        bail!("login failed: {:?}", engine.state.borrow().requests.last());
    }
    engine.run(2.0, |_| false)?;
    println!("{}", status(&engine)?);
    let state = engine.state.borrow();
    let mut missing: Vec<&String> = state.missing.keys().filter(|k| !k.starts_with("global ")).collect();
    missing.sort();
    println!("{} requests; native functions not implemented yet: {missing:?}", state.requests.len());
    Ok(())
}
