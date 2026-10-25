//! Play the game in a window. The mouse (or a touch screen) is the finger.
//!
//!     supernova <apk> [server] [save dir] [language]

use std::path::PathBuf;

use anyhow::Result;
use supernova_engine::app::{Config, run};
use winit::event_loop::EventLoop;

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "warn,ruffle_core=error".into()))
        .init();
    let args: Vec<String> = std::env::args().collect();
    let config = Config {
        apk: PathBuf::from(args.get(1).map(String::as_str).unwrap_or("galaxy-empire-1-9-36.apk")),
        server: args.get(2).cloned().unwrap_or_else(|| "http://127.0.0.1:8000".into()),
        save_dir: PathBuf::from(args.get(3).map(String::as_str).unwrap_or("client-save")),
        language: args.get(4).cloned().unwrap_or_else(|| "en".into()),
    };
    run(EventLoop::new()?, config)
}
