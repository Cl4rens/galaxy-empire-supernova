//! Play the game in a window. The mouse (or a touch screen) is the finger.
//!
//!     supernova <apk> [server] [save dir] [language]

use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::Result;
use supernova_engine::compositor::{Compositor, to_game};
use supernova_engine::engine::{Engine, Touch};
use supernova_engine::screens::RuffleScreens;
use winit::application::ApplicationHandler;
use winit::dpi::LogicalSize;
use winit::event::{ElementState, MouseButton, TouchPhase, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::window::{Window, WindowId};

const FRAME: Duration = Duration::from_micros(33_333);

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
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_some() {
            let _ = self.engine.pause(false);
            return;
        }
        let attributes = Window::default_attributes()
            .with_title("Galaxy Empire · Supernova")
            .with_inner_size(LogicalSize::new(480.0, 720.0));
        let window = Arc::new(event_loop.create_window(attributes).expect("a window"));
        let descriptors = self.screens().descriptors();
        let surface = descriptors.wgpu_instance.create_surface(window.clone()).expect("a window surface");
        let size = window.inner_size();
        self.compositor = Some(Compositor::new(descriptors, surface, size.width, size.height).expect("a compositor"));
        self.window = Some(window);
    }

    fn suspended(&mut self, _: &ActiveEventLoop) {
        let _ = self.engine.pause(true);
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

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "warn,ruffle_core=error".into()))
        .init();
    let args: Vec<String> = std::env::args().collect();
    let apk = PathBuf::from(args.get(1).map(String::as_str).unwrap_or("galaxy-empire-1-9-36.apk"));
    let server = args.get(2).map(String::as_str).unwrap_or("http://127.0.0.1:8000");
    let save = PathBuf::from(args.get(3).map(String::as_str).unwrap_or("client-save"));
    let language = args.get(4).map(String::as_str).unwrap_or("en");
    let engine = Engine::new(apk, server, save, language, Box::new(RuffleScreens::new()?))?;
    engine.boot()?;
    let event_loop = EventLoop::new()?;
    let mut app = App { engine, window: None, compositor: None, cursor: (0.0, 0.0), pressed: false, next_frame: Instant::now() };
    event_loop.run_app(&mut app)?;
    Ok(())
}
