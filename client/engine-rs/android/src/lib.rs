//! Galaxy Empire on Android through the Supernova engine, as a NativeActivity.
//!
//! The app ships nothing from the game. It plays the player's own APK, which the player copies
//! to the app's folder `Android/data/<app>/files/` (any `*.apk` there). A `server.txt` in the
//! same folder holds the server address; without one the app uses the computer an emulator
//! runs on. Errors go to logcat (tag "supernova") and to `error.txt` in that folder.

#![cfg(target_os = "android")]

use std::ffi::{CString, c_char, c_int};
use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::{Result, anyhow};
use supernova_engine::app::{Config, run};
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

fn find_apk(files: &Path) -> Result<PathBuf> {
    std::fs::read_dir(files)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .find(|p| p.extension().is_some_and(|x| x.eq_ignore_ascii_case("apk")))
        .ok_or_else(|| anyhow!("no Galaxy Empire APK in {}", files.display()))
}

fn config(app: &AndroidApp, files: &Path) -> Result<Config> {
    let server = std::fs::read_to_string(files.join("server.txt"))
        .ok()
        .map(|s| s.trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_SERVER.to_string());
    let internal = app.internal_data_path().ok_or_else(|| anyhow!("no app data folder"))?;
    Ok(Config { apk: find_apk(files)?, server, save_dir: internal.join("save"), language: "en".into() })
}

#[unsafe(no_mangle)]
fn android_main(app: AndroidApp) {
    tracing_subscriber::fmt()
        .with_env_filter("warn,ruffle_core=error,supernova_engine=info")
        .with_ansi(false)
        .without_time()
        .with_writer(Logcat::default)
        .init();
    let files = app
        .external_data_path()
        .or_else(|| app.internal_data_path())
        .expect("an app folder");
    let _ = std::fs::create_dir_all(&files);
    let _ = std::fs::remove_file(files.join("error.txt"));
    let result = config(&app, &files).and_then(|config| {
        tracing::info!("playing {} against {}", config.apk.display(), config.server);
        let event_loop = EventLoop::builder().with_android_app(app).build()?;
        run(event_loop, config)
    });
    if let Err(e) = result {
        tracing::error!("{e:#}");
        let _ = std::fs::write(files.join("error.txt"), format!("{e:#}\n"));
    }
}
