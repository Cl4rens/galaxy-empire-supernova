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
use winit::keyboard::{Key, NamedKey};
use winit::window::{Window, WindowId};

use crate::compositor::{Compositor, to_game};
use crate::engine::{Engine, Touch};
use crate::screens::RuffleScreens;

const FRAME: Duration = Duration::from_micros(33_333);

pub struct Config {
    /// the player's own Galaxy Empire 1.9.36 APK
    pub apk: PathBuf,
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
            // Android's back button: the game has no use for it, so it leaves the game (to the
            // app's setup screen)
            WindowEvent::KeyboardInput { event, .. }
                if event.state == ElementState::Pressed
                    && matches!(event.logical_key, Key::Named(NamedKey::BrowserBack | NamedKey::GoBack)) =>
            {
                event_loop.exit();
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
    let engine = Engine::new(config.apk, &config.server, config.save_dir, &config.language, Box::new(screens))?;
    engine.boot()?;
    let mut app = App { engine, window: None, compositor: None, cursor: (0.0, 0.0), pressed: false, next_frame: Instant::now() };
    event_loop.run_app(&mut app)?;
    Ok(())
}
