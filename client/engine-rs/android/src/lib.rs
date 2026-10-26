//! Galaxy Empire on Android through the Supernova engine, as a NativeActivity.
//!
//! The app as built here ships nothing from the game: tools/patch_apk.py --64bit adds the game
//! files from the player's own APK to it on their computer, as assets: `tap4fun.zip` (the game
//! files), `supernova/client.key` and `supernova/server.txt` (the server, fixed when patching).
//! Without a server there the app uses the computer an emulator runs on. Errors go to logcat
//! (tag "supernova") and to `error.txt` in the app's folder `Android/data/<app>/files/`.
//!
//! The game ends its process when it closes: winit's event loop can be made only once per
//! process, and the next start gets a new one.

#![cfg(target_os = "android")]

use std::ffi::{CString, c_char, c_int};
use std::io::Write;

use anyhow::{Result, anyhow};
use supernova_engine::app::{Config, Game, SoftKeyboard, run};
use supernova_engine::crypto::checked_key;
use winit::event_loop::EventLoop;
use winit::platform::android::EventLoopBuilderExtAndroid;
use winit::platform::android::activity::AndroidApp;

/// An emulator's address for the computer it runs on
const DEFAULT_SERVER: &str = "http://10.0.2.2:8000";

#[link(name = "log")]
unsafe extern "C" {
    fn __android_log_write(priority: c_int, tag: *const c_char, text: *const c_char) -> c_int;
}

/// tracing output to logcat, one message per event
#[derive(Default)]
struct Logcat(Vec<u8>);

impl Write for Logcat {
    fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
        self.0.extend_from_slice(data);
        Ok(data.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl Drop for Logcat {
    fn drop(&mut self) {
        let text = String::from_utf8_lossy(&self.0).replace('\0', " ");
        let (Ok(tag), Ok(text)) = (CString::new("supernova"), CString::new(text.trim_end())) else { return };
        // ANDROID_LOG_INFO
        unsafe { __android_log_write(4, tag.as_ptr(), text.as_ptr()) };
    }
}

/// A file the patcher put into the app's assets.
fn asset(app: &AndroidApp, name: &str) -> Option<Vec<u8>> {
    let name = CString::new(name).ok()?;
    let mut asset = app.asset_manager().open(&name)?;
    let mut data = Vec::new();
    std::io::Read::read_to_end(&mut asset, &mut data).ok()?;
    Some(data)
}

fn config(app: &AndroidApp) -> Result<Config> {
    let game_zip = asset(app, "tap4fun.zip")
        .ok_or_else(|| anyhow!("no game files: make this app with tools/patch_apk.py --64bit from your own APK"))?;
    let key = checked_key(&asset(app, "supernova/client.key").ok_or_else(|| anyhow!("no client key in the app"))?)?;
    let server = asset(app, "supernova/server.txt")
        .map(|s| String::from_utf8_lossy(&s).trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_SERVER.to_string());
    let internal = app.internal_data_path().ok_or_else(|| anyhow!("no app data folder"))?;
    let (soft_input, content) = (app.clone(), app.clone());
    let keyboard = SoftKeyboard {
        show: Box::new(move |show| {
            if show { soft_input.show_soft_input(true) } else { soft_input.hide_soft_input(false) }
        }),
        // the activity's visible area ends where the keyboard begins
        visible_bottom: Box::new(move || {
            let rect = content.content_rect();
            (rect.bottom > rect.top).then_some(rect.bottom as f64)
        }),
    };
    Ok(Config {
        game: Game::Files { game_zip, key },
        server,
        save_dir: internal.join("save"),
        language: "en".into(),
        keyboard: Some(keyboard),
    })
}

#[unsafe(no_mangle)]
fn android_main(app: AndroidApp) {
    tracing_subscriber::fmt()
        .with_env_filter("warn,ruffle_core=error,supernova_engine=info")
        .with_ansi(false)
        .without_time()
        .with_writer(Logcat::default)
        .try_init()
        .ok();
    let files = app
        .external_data_path()
        .or_else(|| app.internal_data_path())
        .expect("an app folder");
    let _ = std::fs::create_dir_all(&files);
    let _ = std::fs::remove_file(files.join("error.txt"));
    let result = config(&app).and_then(|config| {
        tracing::info!("playing against {}", config.server);
        let event_loop = EventLoop::builder().with_android_app(app).build()?;
        run(event_loop, config)
    });
    if let Err(e) = result {
        tracing::error!("{e:#}");
        let _ = std::fs::write(files.join("error.txt"), format!("{e:#}\n"));
    }
    std::process::exit(0);
}
