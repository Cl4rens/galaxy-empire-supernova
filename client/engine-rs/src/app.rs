//! The game in a window (desktop) or on the screen (Android): the window's events go to the
//! engine, and the engine's screens are shown with the compositor. The mouse or a finger is
//! the finger.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::Result;
use winit::application::ApplicationHandler;
use winit::dpi::LogicalSize;
use winit::event::{ElementState, MouseButton, TouchPhase, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::window::{Window, WindowId};

use crate::assets::Assets;
use crate::compositor::{Compositor, to_game};
use crate::engine::{Engine, Touch};
use crate::screens::RuffleScreens;

const FRAME: Duration = Duration::from_micros(33_333);

/// Where the game files come from
pub enum Game {
    /// the player's own Galaxy Empire 1.9.36 APK
    Apk(PathBuf),
    /// the game files and client key as tools/patch_apk.py --64bit put them into the app
    Files { game_zip: Vec<u8>, key: [u8; 32] },
}

pub struct Config {
    pub game: Game,
    pub server: String,
    /// where the game keeps its saved files
    pub save_dir: PathBuf,
    pub language: String,
}

struct App {
    engine: Engine,
    window: Option<Arc<Window>>,
    compositor: Option<Compositor>,
    cursor: (f64, f64),
    pressed: bool,
    next_frame: Instant,
}

impl App {
    fn screens(&self) -> std::cell::RefMut<'_, RuffleScreens> {
        std::cell::RefMut::map(self.engine.state.borrow_mut(), |s| {
            s.screens.as_any().downcast_mut::<RuffleScreens>().expect("Ruffle screens")
        })
    }

    fn touch(&self, phase: Touch, x: f64, y: f64) {
        let Some(compositor) = &self.compositor else { return };
        let (w, h) = compositor.size();
        let (gx, gy) = to_game(w, h, x, y);
        if let Err(e) = self.engine.touch(phase, gx, gy) {
            tracing::warn!("touch: {e}");
        }
    }

    fn frame(&mut self) {
        if let Err(e) = self.engine.step().and_then(|_| self.engine.render()) {
            tracing::warn!("{e}");
        }
        let layers = self.screens().layer_textures();
        if let Some(compositor) = &mut self.compositor {
            compositor.draw(&layers);
        }
    }
}

impl ApplicationHandler for App {
    // On Android the window goes away whenever the app goes to the background, and a new one
    // comes with every resume.
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        let first = self.window.is_none() && self.compositor.is_none();
        let attributes = Window::default_attributes()
            .with_title("Galaxy Empire · Supernova")
            .with_inner_size(LogicalSize::new(480.0, 720.0));
        let window = Arc::new(event_loop.create_window(attributes).expect("a window"));
        let descriptors = self.screens().descriptors();
        let surface = descriptors.wgpu_instance.create_surface(window.clone()).expect("a window surface");
        let size = window.inner_size();
        self.compositor = Some(Compositor::new(descriptors, surface, size.width, size.height).expect("a compositor"));
        self.window = Some(window);
        if !first {
            let _ = self.engine.pause(false);
        }
    }

    fn suspended(&mut self, _: &ActiveEventLoop) {
        let _ = self.engine.pause(true);
        self.compositor = None;
        self.window = None;
    }

    fn window_event(&mut self, event_loop: &ActiveEventLoop, _: WindowId, event: WindowEvent) {
        match event {
            WindowEvent::CloseRequested => event_loop.exit(),
            WindowEvent::Resized(size) => {
                if let Some(compositor) = &mut self.compositor {
                    compositor.resize(size.width, size.height);
                }
            }
            WindowEvent::CursorMoved { position, .. } => {
                self.cursor = (position.x, position.y);
                if self.pressed {
                    self.touch(Touch::Moved, position.x, position.y);
                }
            }
            WindowEvent::MouseInput { state, button: MouseButton::Left, .. } => {
                self.pressed = state == ElementState::Pressed;
                let phase = if self.pressed { Touch::Pressed } else { Touch::Released };
                self.touch(phase, self.cursor.0, self.cursor.1);
            }
            WindowEvent::Touch(touch) => {
                let phase = match touch.phase {
                    TouchPhase::Started => Touch::Pressed,
                    TouchPhase::Moved => Touch::Moved,
                    TouchPhase::Ended | TouchPhase::Cancelled => Touch::Released,
                };
                self.touch(phase, touch.location.x, touch.location.y);
            }
            WindowEvent::RedrawRequested => self.frame(),
            _ => {}
        }
    }

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        let now = Instant::now();
        if now >= self.next_frame {
            self.next_frame = (self.next_frame + FRAME).max(now);
            if let Some(window) = &self.window {
                window.request_redraw();
            }
        }
        event_loop.set_control_flow(ControlFlow::WaitUntil(self.next_frame));
    }
}

/// Start the game and run it until its window closes.
pub fn run(event_loop: EventLoop<()>, config: Config) -> Result<()> {
    let screens = RuffleScreens::new()?;
    let screens = Box::new(screens);
    let engine = match config.game {
        Game::Apk(apk) => Engine::new(apk, &config.server, config.save_dir, &config.language, screens)?,
        Game::Files { game_zip, key } => {
            Engine::with_assets(Assets::from_game_zip(game_zip)?, key, &config.server, config.save_dir, &config.language, screens)?
        }
    };
    engine.boot()?;
    let mut app = App { engine, window: None, compositor: None, cursor: (0.0, 0.0), pressed: false, next_frame: Instant::now() };
    event_loop.run_app(&mut app)?;
    Ok(())
}
