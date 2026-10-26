//! The game in a window (desktop) or on the screen (Android): the window's events go to the
//! engine, and the engine's screens are shown with the compositor. The mouse or a finger is
//! the finger.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::Result;
use winit::application::ApplicationHandler;
use winit::dpi::LogicalSize;
use winit::event::{ElementState, Ime, MouseButton, TouchPhase, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::keyboard::{Key, NamedKey};
use winit::window::{Window, WindowId};

use crate::assets::Assets;
use crate::compositor::{Compositor, to_game};
use crate::screens::HEIGHT;
use crate::engine::{Engine, Keyboard, Touch};
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
    /// The on-screen keyboard; without it the window's input method is switched on and off
    /// (desktop).
    pub keyboard: Option<SoftKeyboard>,
}

pub struct SoftKeyboard {
    /// brings the keyboard up (true) or takes it away
    pub show: Box<dyn Fn(bool)>,
    /// the lowest window row the keyboard leaves visible, in pixels
    pub visible_bottom: Box<dyn Fn() -> Option<f64>>,
}

struct App {
    engine: Engine,
    window: Option<Arc<Window>>,
    compositor: Option<Compositor>,
    cursor: (f64, f64),
    pressed: bool,
    next_frame: Instant,
    keyboard: Option<SoftKeyboard>,
    /// where the keyboard's top edge was last told to the game, while it is up
    keyboard_top: Option<f64>,
    /// the fingers on the screen (id, window position), and whether two of them were down
    fingers: Vec<(u64, f64, f64)>,
    two_fingers: bool,
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

    /// One finger is a touch; with a second one the game gets the two (pinching, moving the
    /// map) until both are lifted.
    fn finger(&mut self, id: u64, phase: TouchPhase, x: f64, y: f64) {
        match phase {
            TouchPhase::Started => {
                self.fingers.push((id, x, y));
                match self.fingers.len() {
                    1 if !self.two_fingers => self.touch(Touch::Pressed, x, y),
                    2 => {
                        // the first finger's touch ends where nothing takes it
                        if let Err(e) = self.engine.touch(Touch::Released, -1000.0, -1000.0) {
                            tracing::warn!("touch: {e}");
                        }
                        self.two_fingers = true;
                        self.two_touch(Touch::Pressed);
                    }
                    _ => {}
                }
            }
            TouchPhase::Moved => {
                if let Some(finger) = self.fingers.iter_mut().find(|f| f.0 == id) {
                    (finger.1, finger.2) = (x, y);
                }
                if self.two_fingers {
                    self.two_touch(Touch::Moved);
                } else if self.fingers.len() == 1 {
                    self.touch(Touch::Moved, x, y);
                }
            }
            TouchPhase::Ended | TouchPhase::Cancelled => {
                if self.two_fingers {
                    if self.fingers.len() == 2 {
                        self.two_touch(Touch::Released);
                    }
                } else if self.fingers.iter().any(|f| f.0 == id) {
                    self.touch(Touch::Released, x, y);
                }
                self.fingers.retain(|f| f.0 != id);
                if self.fingers.is_empty() {
                    self.two_fingers = false;
                }
            }
        }
    }

    fn two_touch(&self, phase: Touch) {
        let (Some(compositor), [(_, x1, y1), (_, x2, y2), ..]) = (&self.compositor, self.fingers.as_slice()) else { return };
        let (w, h) = compositor.size();
        let ((x1, y1), (x2, y2)) = (to_game(w, h, *x1, *y1), to_game(w, h, *x2, *y2));
        if let Err(e) = self.engine.two_touch(phase, x1, y1, x2, y2) {
            tracing::warn!("two-finger touch: {e}");
        }
    }

    fn text_input(&self, text: &str) {
        if let Err(e) = self.engine.text_input(text) {
            tracing::warn!("text input: {e}");
        }
    }

    /// The game moves the screen up so that the text box stays above the keyboard: it needs
    /// the keyboard's top edge, as a part of the game's height.
    fn follow_keyboard(&mut self) {
        let (Some(keyboard), Some(top), Some(compositor)) = (&self.keyboard, self.keyboard_top, &self.compositor) else { return };
        let Some(bottom) = (keyboard.visible_bottom)() else { return };
        let (w, h) = compositor.size();
        let edge = if bottom < h as f64 - 1.0 { (to_game(w, h, 0.0, bottom).1 / HEIGHT as f64).clamp(0.0, 1.0) } else { 1.0 };
        if (edge - top).abs() > 0.001 {
            tracing::info!("keyboard top at {edge:.3} of the game's height");
            self.keyboard_top = Some(edge);
            if let Err(e) = self.engine.keyboard_moved(edge) {
                tracing::warn!("keyboard: {e}");
            }
        }
    }

    fn frame(&mut self) {
        if let Err(e) = self.engine.step().and_then(|_| self.engine.render()) {
            tracing::warn!("{e}");
        }
        if let Some(keyboard) = self.engine.take_keyboard() {
            let show = matches!(keyboard, Keyboard::Show { .. });
            match (&self.keyboard, &self.window) {
                (Some(keyboard), _) => (keyboard.show)(show),
                (None, Some(window)) => window.set_ime_allowed(show),
                _ => {}
            }
            self.keyboard_top = show.then_some(1.0);
        }
        self.follow_keyboard();
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
            .with_inner_size(LogicalSize::new(450.0, 800.0));
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
            WindowEvent::Touch(touch) => self.finger(touch.id, touch.phase, touch.location.x, touch.location.y),
            // typed text for the game's text box (the soft keyboard sends keys too)
            // (Android's key map gives some keys as characters: enter as "\n")
            WindowEvent::KeyboardInput { event, .. } if event.state == ElementState::Pressed => {
                tracing::debug!("key {:?} {:?}", event.logical_key, event.text);
                let text = match &event.logical_key {
                    Key::Named(NamedKey::Backspace) => Some("\u{8}".to_string()),
                    Key::Named(NamedKey::Enter) => Some("\n".to_string()),
                    Key::Character(c) => Some(c.to_string()),
                    _ => event.text.as_ref().map(|t| t.to_string()),
                };
                if let Some(text) = text.map(|t| t.replace('\r', "\n"))
                    && is_typed(&text)
                {
                    self.text_input(&text);
                }
            }
            WindowEvent::Ime(Ime::Commit(text)) => self.text_input(&text),
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
    engine.enable_audio();
    engine.boot()?;
    let mut app = App {
        engine,
        window: None,
        compositor: None,
        cursor: (0.0, 0.0),
        pressed: false,
        next_frame: Instant::now(),
        keyboard: config.keyboard,
        keyboard_top: None,
        fingers: Vec::new(),
        two_fingers: false,
    };
    event_loop.run_app(&mut app)?;
    Ok(())
}

/// Text for a text box: characters, a newline (done) or a backspace, no other control keys
fn is_typed(text: &str) -> bool {
    !text.is_empty() && text.chars().all(|c| !c.is_control() || c == '\n' || c == '\u{8}')
}
