//! The game's Flash objects as Ruffle players, one per object, like gameswf had them.
//!
//! The scripts call methods on their Flash objects (Load, InvokeASCallback, Update, Render,
//! OnTouchPressed, ...); Flash commands come back through Ruffle's FsCommandProvider. Each
//! player draws into its own texture, transparently; `layer_textures()` gives them in the order
//! the game rendered them.
//!
//! gameswf handed a Flash command to the scripts at once, while the movie waited, and the
//! scripts could call back into it (lists fill an entry the moment they add it, and measure it
//! right after). So does this: a command goes to the handler `set_fscommand_handler()` set,
//! together with the running movie, and calls into that object meanwhile go to the running
//! movie. For that the engine calls into Ruffle only as `Call::Later` jobs, which it runs after
//! it let go of its own state.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;
use std::sync::{Arc, Mutex};

use anyhow::{Result, anyhow};
use ruffle_core::backend::ui::FontDefinition;
use ruffle_core::context::UpdateContext;
use ruffle_core::events::MouseButton;
use ruffle_core::external::{self, FsCommandProvider, Value as ExternalValue};
use ruffle_core::font::{DefaultFont, FontFileData};
use ruffle_core::limits::ExecutionLimit;
use ruffle_core::tag_utils::SwfMovieData;
use ruffle_core::{FloatDuration, Player, PlayerBuilder, PlayerEvent};
use ruffle_render_wgpu::backend::{WgpuRenderBackend, create_wgpu_instance, request_adapter_and_device};
use ruffle_render_wgpu::descriptors::Descriptors;
use ruffle_render_wgpu::target::TextureTarget;
use ruffle_render_wgpu::wgpu;
use ruffle_render::quality::StageQuality;

use crate::assets::Assets;
use crate::convert::{Converter, FONT};
use crate::engine::{Arg, Call, Screens, Texts};

/// The game's screen in its 16:9 layout (the screens' `_16x9` files)
pub const WIDTH: u32 = 640;
pub const HEIGHT: u32 = 1136;
const LAYOUT_SUFFIX: &str = "_16x9";
/// No multisampling: every screen has its own buffers, and the screens are mostly pictures
const SCREEN_QUALITY: StageQuality = StageQuality::Low;

type Commands = Arc<Mutex<Vec<(String, String, String)>>>;
/// (object, command, argument): hands a Flash command to the scripts
pub type FsCommandHandler = Rc<dyn Fn(&str, &str, &str)>;

thread_local! {
    static HANDLER: RefCell<Option<FsCommandHandler>> = const { RefCell::new(None) };
    /// the movies running a Flash command right now, innermost last: (object, its context)
    static RUNNING: RefCell<Vec<(String, *mut ())>> = const { RefCell::new(Vec::new()) };
}

/// From now on Flash commands go to `handler` at once (without one they wait in a queue for
/// `Screens::commands()`).
pub fn set_fscommand_handler(handler: Option<FsCommandHandler>) {
    HANDLER.with(|h| *h.borrow_mut() = handler);
}

struct Sender {
    object: String,
    commands: Commands,
}

/// Takes a movie off `RUNNING` when its command is done, also if the handler panics.
struct Running;

impl Drop for Running {
    fn drop(&mut self) {
        RUNNING.with(|r| r.borrow_mut().pop());
    }
}

impl FsCommandProvider for Sender {
    fn on_fs_command(&self, command: &str, args: &str) -> bool {
        tracing::debug!("{}: fscommand {command} {args}", self.object);
        self.commands.lock().unwrap().push((self.object.clone(), command.to_string(), args.to_string()));
        true
    }

    fn on_fs_command_in(&self, context: &mut UpdateContext<'_>, command: &str, args: &str) -> bool {
        let Some(handler) = HANDLER.with(|h| h.borrow().clone()) else {
            return self.on_fs_command(command, args);
        };
        tracing::debug!("{}: fscommand {command} {args}", self.object);
        let context = context as *mut UpdateContext<'_> as *mut ();
        RUNNING.with(|r| r.borrow_mut().push((self.object.clone(), context)));
        let _running = Running;
        handler(&self.object, command, args);
        true
    }
}

/// What the engine does to a screen's movie: through its player, or, while the movie runs a
/// Flash command, through the context it came with.
trait Movie {
    fn call(&mut self, target: &str, function: &str, args: Vec<ExternalValue>) -> ExternalValue;
    fn set_property(&mut self, target: &str, name: &str, value: ExternalValue) -> bool;
    fn gray(&mut self, target: &str, gray: bool) -> bool;
    fn replace_bitmap(&mut self, name: &str, pixels: Box<dyn FnOnce(u32, u32) -> Vec<u8>>) -> bool;
    fn fit_text(&mut self, fit: &Fit) -> Option<(f64, String)>;
    fn cut_text(&mut self, target: &str, multi_line: bool, last_text: Option<&str>) -> Option<String>;
    fn text_field_paths(&mut self, marker: &str) -> Vec<String>;
    fn find_button(&mut self, x: f64, y: f64) -> Option<String>;
}

impl Movie for Player {
    fn call(&mut self, target: &str, function: &str, args: Vec<ExternalValue>) -> ExternalValue {
        self.call_avm1_function(target, function, args)
    }
    fn set_property(&mut self, target: &str, name: &str, value: ExternalValue) -> bool {
        self.set_avm1_property(target, name, value)
    }
    fn gray(&mut self, target: &str, gray: bool) -> bool {
        self.set_avm1_gray(target, gray)
    }
    fn replace_bitmap(&mut self, name: &str, pixels: Box<dyn FnOnce(u32, u32) -> Vec<u8>>) -> bool {
        Player::replace_bitmap(self, name, pixels)
    }
    fn fit_text(&mut self, fit: &Fit) -> Option<(f64, String)> {
        self.fit_avm1_text(&fit.target, fit.min_scale, fit.base_size, fit.text.as_deref())
    }
    fn cut_text(&mut self, target: &str, multi_line: bool, last_text: Option<&str>) -> Option<String> {
        self.truncate_avm1_text(target, multi_line, last_text)
    }
    fn text_field_paths(&mut self, marker: &str) -> Vec<String> {
        Player::text_field_paths(self, marker)
    }
    fn find_button(&mut self, x: f64, y: f64) -> Option<String> {
        self.find_avm1_button(x, y, BUTTON_PREFIX)
    }
}

impl Movie for UpdateContext<'_> {
    fn call(&mut self, target: &str, function: &str, args: Vec<ExternalValue>) -> ExternalValue {
        let result = external::call_avm1_function(self, target, function, args);
        Player::run_actions(self);
        result
    }
    fn set_property(&mut self, target: &str, name: &str, value: ExternalValue) -> bool {
        external::set_avm1_property(self, target, name, value)
    }
    fn gray(&mut self, target: &str, gray: bool) -> bool {
        external::set_avm1_gray(self, target, gray)
    }
    fn replace_bitmap(&mut self, name: &str, pixels: Box<dyn FnOnce(u32, u32) -> Vec<u8>>) -> bool {
        external::replace_bitmap(self, name, pixels)
    }
    fn fit_text(&mut self, fit: &Fit) -> Option<(f64, String)> {
        external::fit_avm1_text(self, &fit.target, fit.min_scale, fit.base_size, fit.text.as_deref())
    }
    fn cut_text(&mut self, target: &str, multi_line: bool, last_text: Option<&str>) -> Option<String> {
        external::truncate_avm1_text(self, target, multi_line, last_text)
    }
    fn text_field_paths(&mut self, marker: &str) -> Vec<String> {
        external::text_field_paths(self, marker)
    }
    fn find_button(&mut self, x: f64, y: f64) -> Option<String> {
        external::find_avm1_button(self, x, y, BUTTON_PREFIX)
    }
}

/// Run `f` on the movie of `object`: the running one if it is running a Flash command, else
/// through its player. None when the player is busy elsewhere.
fn with_movie<R>(object: &str, player: &Mutex<Player>, f: impl FnOnce(&mut dyn Movie) -> R) -> Option<R> {
    let running = RUNNING.with(|r| r.borrow().iter().rev().find(|(o, _)| o == object).map(|(_, c)| *c));
    match running {
        // SAFETY: the movie is running a Flash command further up this thread's stack, which
        // handed out its context and waits for the command to come back
        Some(context) => Some(f(unsafe { &mut *(context as *mut UpdateContext<'static>) })),
        None => match player.try_lock() {
            Ok(mut p) => Some(f(&mut *p)),
            Err(_) => {
                tracing::warn!("{object}: the movie is busy");
                None
            }
        },
    }
}

fn running(object: &str) -> bool {
    RUNNING.with(|r| r.borrow().iter().any(|(o, _)| o == object))
}

/// One Flash object: its player and what the engine keeps for it.
struct Screen {
    player: Arc<Mutex<Player>>,
    extras: Rc<RefCell<Extras>>,
}

#[derive(Default)]
struct Extras {
    /// text fields that shrink their text to fit
    fits: Vec<Fit>,
    buttons: Buttons,
}

pub struct RuffleScreens {
    descriptors: Arc<Descriptors>,
    converter: Converter,
    screens: HashMap<String, Screen>,
    commands: Commands,
    /// the device font every screen draws its text with, read once
    font: Option<Arc<dyn AsRef<[u8]>>>,
    /// objects in the order the game rendered them this frame
    pub rendered: Vec<String>,
    pub missing: HashMap<String, u32>,
}

/// gameswf drove clips named "btn..." itself: a touch plays their "focus_in" frames, leaving
/// them "focus_out", and letting go on them "released" and calls their `on_clicked`.
/// SetBtnEnable switches them between "disabled" and "activated".
#[derive(Default)]
struct Buttons {
    /// the button the finger went down on, and whether it is still on it
    pressed: Option<(String, bool)>,
    disabled: HashSet<String>,
}

const BUTTON_PREFIX: &str = "btn";

/// What to play on a button: (button, frame label, callback or "")
type ButtonAction = (String, &'static str, &'static str);

/// "_level0.panel.btnOk" as the scripts write it: "_root.panel.btnOk"
fn script_path(path: &str) -> String {
    match path.strip_prefix("_level0") {
        Some(rest) => format!("_root{rest}"),
        None => path.to_string(),
    }
}

fn play(movie: &mut dyn Movie, (button, label, callback): &ButtonAction) {
    movie.call(button, "gotoAndPlay", vec![ExternalValue::String(label.to_string())]);
    if !callback.is_empty() {
        movie.call(button, callback, vec![]);
    }
}

impl Buttons {
    /// What a touch on `under` (the button there, if any) does; and whether it is on a button.
    fn touch(&mut self, method: &str, under: Option<String>) -> (bool, Option<ButtonAction>) {
        match method {
            "OnTouchPressed" => {
                let Some(button) = under else { return (false, None) };
                if self.disabled.contains(&button) {
                    return (true, None);
                }
                self.pressed = Some((button.clone(), true));
                (true, Some((button, "focus_in", "on_focus_in")))
            }
            "OnTouchMoved" => {
                let Some((button, inside)) = &mut self.pressed else { return (false, None) };
                let now_inside = under.as_deref() == Some(button.as_str());
                if now_inside == *inside {
                    return (true, None);
                }
                *inside = now_inside;
                let (label, callback) = if now_inside { ("focus_in", "on_focus_in") } else { ("focus_out", "on_focus_out") };
                (true, Some((button.clone(), label, callback)))
            }
            _ => match self.pressed.take() {
                Some((button, true)) => (true, Some((button, "released", "on_clicked"))),
                Some(_) => (true, None),
                None => (false, None),
            },
        }
    }

    fn set_enabled(&mut self, button: &str, enabled: bool) -> Option<ButtonAction> {
        let button = script_path(button);
        if enabled {
            self.disabled.remove(&button).then(|| (button, "activated", ""))
        } else {
            if self.pressed.as_ref().is_some_and(|(b, _)| *b == button) {
                self.pressed = None;
            }
            self.disabled.insert(button.clone());
            Some((button, "disabled", ""))
        }
    }
}

#[derive(Clone)]
struct Fit {
    target: String,
    min_scale: f64,
    base_size: Option<f64>,
    text: Option<String>,
    /// found by its name (`SINGLE_LINE_MARK`, `MULTI_LINE_MARK`), not asked for by SetLineMax:
    /// its text is cut with "..." (on one line or several), not made smaller
    by_name: Option<bool>,
}

/// Text fields named with these show what fits of their text, ending with "...", on one line
/// or as many as the field has (gameswf did it)
const SINGLE_LINE_MARK: &str = "_SLHM";
const MULTI_LINE_MARK: &str = "_MLHM";

/// Keep the fitted text of the fields, find new marked fields and fit what changed.
fn fit_texts(movie: &mut dyn Movie, extras: &RefCell<Extras>) {
    // the fitting runs ActionScript, which may call the scripts and they this object: no borrow
    let mut fits = std::mem::take(&mut extras.borrow_mut().fits);
    let mut named: HashMap<String, bool> = HashMap::new();
    for (mark, multi_line) in [(SINGLE_LINE_MARK, false), (MULTI_LINE_MARK, true)] {
        named.extend(movie.text_field_paths(mark).into_iter().map(|target| (target, multi_line)));
    }
    fits.retain(|f| f.by_name.is_none() || named.contains_key(&f.target));
    for (target, multi_line) in named {
        if !fits.iter().any(|f| f.target == target) {
            fits.push(Fit { target, min_scale: 1.0, base_size: None, text: None, by_name: Some(multi_line) });
        }
    }
    for fit in fits.iter_mut() {
        match fit.by_name {
            Some(multi_line) => {
                if let Some(text) = movie.cut_text(&fit.target, multi_line, fit.text.as_deref()) {
                    fit.text = Some(text);
                }
            }
            None => {
                if let Some((base, text)) = movie.fit_text(fit) {
                    fit.base_size = Some(base);
                    fit.text = Some(text);
                }
            }
        }
    }
    let mut extras = extras.borrow_mut();
    let added = std::mem::take(&mut extras.fits); // SetLineMax meanwhile
    fits.retain(|f| !added.iter().any(|a| a.target == f.target));
    fits.extend(added);
    extras.fits = fits;
}

fn to_external(arg: &Arg) -> ExternalValue {
    match arg {
        Arg::Nil => ExternalValue::Undefined,
        Arg::Bool(b) => ExternalValue::Bool(*b),
        Arg::Num(n) => ExternalValue::Number(*n),
        Arg::Str(s) => ExternalValue::String(s.clone()),
    }
}

fn truthy(arg: Option<&Arg>) -> bool {
    match arg {
        Some(Arg::Bool(b)) => *b,
        Some(Arg::Num(n)) => *n != 0.0,
        Some(Arg::Str(s)) => !s.is_empty() && s != "false" && s != "0",
        _ => false,
    }
}

fn from_external(value: ExternalValue) -> Arg {
    match value {
        ExternalValue::Bool(b) => Arg::Bool(b),
        ExternalValue::Number(n) => Arg::Num(n),
        ExternalValue::String(s) => Arg::Str(s),
        _ => Arg::Nil,
    }
}

impl RuffleScreens {
    pub fn new() -> Result<Self> {
        // the screens' ActionScript was written for gameswf: it ignored the case of names
        // (`htmltext` for `htmlText`), left the empty piece after a trailing delimiter out of
        // `split` (lists built from "a\x01b\x01"), and drew text past the bottom of its field
        external::set_gameswf_compat(true);
        let instance = create_wgpu_instance(wgpu::Backends::all(), wgpu::BackendOptions::default(), None);
        let (adapter, device, queue) = futures::executor::block_on(request_adapter_and_device(
            wgpu::Backends::all(),
            &instance,
            None,
            wgpu::PowerPreference::HighPerformance,
        ))
        .map_err(|e| anyhow!(e.to_string()))?;
        Ok(Self {
            descriptors: Arc::new(Descriptors::new(instance, adapter, device, queue)),
            converter: Converter::default(),
            screens: HashMap::new(),
            commands: Arc::default(),
            font: None,
            rendered: Vec::new(),
            missing: HashMap::new(),
        })
    }

    /// RGBA pixels of a picture the game names for ReplaceTexture (a data2 file or a downloaded
    /// one), premultiplied by their alpha as Ruffle keeps bitmaps.
    fn picture(&mut self, assets: &Assets, name: &str) -> Option<image::RgbaImage> {
        let data = self.converter.picture(assets, name).or_else(|| std::fs::read(name).ok())?;
        let mut picture = image::load_from_memory(&data).ok()?.to_rgba8();
        for pixel in picture.pixels_mut() {
            let a = pixel[3] as u16;
            for c in 0..3 {
                pixel[c] = ((pixel[c] as u16 * a + 127) / 255) as u8;
            }
        }
        Some(picture)
    }

    pub fn descriptors(&self) -> Arc<Descriptors> {
        self.descriptors.clone()
    }

    /// Make the player of a screen; its first frame runs in the returned job.
    fn load(&mut self, assets: &Assets, texts: &Texts, object: &str, path: &str) -> Result<Call> {
        // the screen's 16:9 layout where it has one, as gameswf picked it on a phone
        let tall = path.strip_suffix(".tfs").map(|base| format!("{base}{LAYOUT_SUFFIX}.tfs"));
        let path = match tall {
            Some(tall) if assets.file(tall.trim_start_matches("./")).is_some() => tall,
            _ => path.to_string(),
        };
        let path = path.as_str();
        let swf = self.converter.screen(assets, texts, path)?;
        tracing::debug!("{object}: load {path}");
        if let Some(dir) = std::env::var_os("SUPERNOVA_DUMP_SCREENS") {
            let name = path.rsplit('/').next().unwrap_or(path).replace(".tfs", ".swf");
            let _ = std::fs::write(std::path::Path::new(&dir).join(name), &swf);
        }
        let movie = SwfMovieData::from_data(&swf, format!("file:///{path}"), None, None).map_err(|e| anyhow!(e.to_string()))?;
        let target = TextureTarget::new(&self.descriptors.device, (WIDTH, HEIGHT)).map_err(|e| anyhow!(e.to_string()))?;
        let renderer = WgpuRenderBackend::new(self.descriptors.clone(), target).map_err(|e| anyhow!(e.to_string()))?;
        let player = PlayerBuilder::new()
            .with_renderer(renderer)
            .with_fs_commands(Box::new(Sender { object: object.to_string(), commands: self.commands.clone() }))
            .with_movie(movie)
            .with_viewport_dimensions(WIDTH, HEIGHT, 1.0)
            .with_quality(SCREEN_QUALITY)
            .with_autoplay(true)
            .build();
        {
            let mut p = player.lock().unwrap();
            if self.font.is_none() {
                self.font = assets.file(FONT).map(|font| Arc::new(font.to_vec()) as Arc<dyn AsRef<[u8]>>);
            }
            if let Some(font) = &self.font {
                p.register_device_font(FontDefinition::FontFile {
                    name: "Arial".into(),
                    is_bold: false,
                    is_italic: false,
                    data: FontFileData::new_shared(font.clone()),
                    index: 0,
                });
                for default in [DefaultFont::Sans, DefaultFont::Serif, DefaultFont::Typewriter] {
                    p.set_default_font(default, vec!["Arial".into()]);
                }
            }
            p.set_window_mode("transparent");
        }
        self.screens.insert(object.to_string(), Screen { player: player.clone(), extras: Rc::default() });
        Ok(Call::Later(Box::new(move || {
            let mut p = player.lock().unwrap();
            p.preload(&mut ExecutionLimit::none());
            p.run_frame();
            Vec::new()
        })))
    }

    /// Every screen the game rendered this frame, bottom first, as RGBA pixels.
    pub fn layers(&mut self) -> Vec<(String, image::RgbaImage)> {
        let mut out = Vec::new();
        for object in self.rendered.clone() {
            let Some(screen) = self.screens.get(&object) else { continue };
            let mut p = screen.player.lock().unwrap();
            p.render();
            let Some(renderer) = <dyn std::any::Any>::downcast_mut::<WgpuRenderBackend<TextureTarget>>(p.renderer_mut()) else { continue };
            if let Some(layer) = renderer.capture_frame() {
                out.push((object, layer));
            }
        }
        out
    }

    /// Draw every screen the game rendered this frame into its texture; the textures bottom
    /// first, for the window to stack.
    pub fn layer_textures(&mut self) -> Vec<wgpu::Texture> {
        let mut out = Vec::new();
        for object in &self.rendered {
            let Some(screen) = self.screens.get(object) else { continue };
            let mut p = screen.player.lock().unwrap();
            p.render();
            if let Some(renderer) = <dyn std::any::Any>::downcast_ref::<WgpuRenderBackend<TextureTarget>>(p.renderer()) {
                out.push(renderer.target().texture.clone());
            }
        }
        out
    }

    /// The layers stacked into one picture.
    pub fn snapshot(&mut self) -> image::RgbaImage {
        let mut out = image::RgbaImage::from_pixel(WIDTH, HEIGHT, image::Rgba([0, 0, 0, 255]));
        for (_, layer) in self.layers() {
            image::imageops::overlay(&mut out, &layer, 0, 0);
        }
        out
    }

    pub fn player(&self, object: &str) -> Option<Arc<Mutex<Player>>> {
        self.screens.get(object).map(|s| s.player.clone())
    }

    /// Bytes the converter keeps: (pictures, converted screens), and how many screens are loaded
    pub fn cached(&self) -> (usize, usize, usize) {
        let (pictures, screens) = self.converter.cached_bytes();
        (pictures, screens, self.screens.len())
    }

    pub fn objects(&self) -> impl Iterator<Item = (&String, &Arc<Mutex<Player>>)> {
        self.screens.iter().map(|(o, s)| (o, &s.player))
    }
}

impl Screens for RuffleScreens {
    fn call(&mut self, assets: &Assets, texts: &Texts, object: &str, method: &str, args: &[Arg]) -> Call {
        let text = |i: usize| args.get(i).map(Arg::text).unwrap_or_default();
        tracing::trace!("{object}.{method}");
        if method == "Load" {
            return self.load(assets, texts, object, &text(0)).unwrap_or_else(|e| {
                tracing::warn!("{object}: cannot load {}: {e}", text(0));
                Call::Done(Vec::new())
            });
        }
        if method == "Render" {
            self.rendered.retain(|o| o != object);
            self.rendered.push(object.to_string());
            return Call::Done(Vec::new());
        }
        let Some(screen) = self.screens.get(object) else {
            return Call::Done(Vec::new());
        };
        let (player, extras, name) = (screen.player.clone(), screen.extras.clone(), object.to_string());
        match method {
            "InvokeASCallback" => {
                let target = match args.first() {
                    Some(Arg::Str(t)) if !t.is_empty() => t.clone(),
                    _ => "_root".to_string(),
                };
                let (function, values): (String, Vec<ExternalValue>) = (text(1), args.iter().skip(2).map(to_external).collect());
                Call::Later(Box::new(move || {
                    tracing::debug!("{name}: {target}.{function}({values:?})");
                    let result = with_movie(&name, &player, |m| m.call(&target, &function, values));
                    vec![from_external(result.unwrap_or(ExternalValue::Undefined))]
                }))
            }
            "Update" => {
                let dt = args.first().map(Arg::text).and_then(|s| s.parse::<f64>().ok()).unwrap_or(33.0);
                Call::Later(Box::new(move || {
                    if running(&name) {
                        return Vec::new(); // the movie is in the middle of a command
                    }
                    if let Ok(mut p) = player.try_lock() {
                        p.tick(FloatDuration::from_millis(dt));
                        fit_texts(&mut *p, &extras);
                    }
                    Vec::new()
                }))
            }
            "OnTouchPressed" | "OnTouchMoved" | "OnTouchReleased" => {
                let x = args.first().map(Arg::text).and_then(|s| s.parse().ok()).unwrap_or(0.0);
                let y = args.get(1).map(Arg::text).and_then(|s| s.parse().ok()).unwrap_or(0.0);
                let method = method.to_string();
                Call::Later(Box::new(move || {
                    // true when the finger is on something of this screen that reacts to it, so
                    // the game does not hand the touch to the screens below
                    let Ok(mut p) = player.try_lock() else { return vec![Arg::Bool(false)] };
                    let under = p.find_button(x, y).map(|b| script_path(&b));
                    let (on_button, action) = extras.borrow_mut().buttons.touch(&method, under);
                    if let Some(action) = &action {
                        play(&mut *p, action);
                    }
                    let (_, was_pressing) = p.mouse_target();
                    let event = match method.as_str() {
                        "OnTouchPressed" => PlayerEvent::MouseDown { x, y, button: MouseButton::Left, index: None },
                        "OnTouchMoved" => PlayerEvent::MouseMove { x, y },
                        _ => PlayerEvent::MouseUp { x, y, button: MouseButton::Left },
                    };
                    p.handle_event(event);
                    let (hovering, pressing) = p.mouse_target();
                    vec![Arg::Bool(on_button || hovering || pressing || was_pressing)]
                }))
            }
            // a picture slot (an exported bitmap such as "NA_Item_3.png") gets a picture; the
            // game falls back to a default picture when the size comes back 0, 0
            "ReplaceTexture" => {
                let Some(picture) = self.picture(assets, &text(1)) else {
                    return Call::Done(vec![Arg::Num(0.0), Arg::Num(0.0)]);
                };
                let (w, h) = picture.dimensions();
                let (slot, source) = (text(0), text(1));
                Call::Later(Box::new(move || {
                    let pixels: Box<dyn FnOnce(u32, u32) -> Vec<u8>> = Box::new(move |width, height| {
                        image::imageops::resize(&picture, width, height, image::imageops::FilterType::Triangle).into_raw()
                    });
                    let replaced = with_movie(&name, &player, |m| m.replace_bitmap(&slot, pixels));
                    tracing::debug!("{name}: picture slot {slot} <- {source} ({w}x{h}, slot found: {replaced:?})");
                    vec![Arg::Num(w as f64), Arg::Num(h as f64)]
                }))
            }
            "SetBtnEnable" => {
                let (button, enabled) = (text(0), truthy(args.get(1)));
                Call::Later(Box::new(move || {
                    let action = extras.borrow_mut().buttons.set_enabled(&button, enabled);
                    with_movie(&name, &player, |m| {
                        m.set_property(&button, "enabled", ExternalValue::Bool(enabled));
                        if let Some(action) = &action {
                            play(m, action);
                        }
                    });
                    Vec::new()
                }))
            }
            "SetCustomEffect" => {
                let (target, gray) = (text(0), text(1) == "Gray" && truthy(args.get(2)));
                Call::Later(Box::new(move || {
                    with_movie(&name, &player, |m| m.gray(&target, gray));
                    Vec::new()
                }))
            }
            "SetLineMax" => {
                let fit = Fit {
                    target: text(0),
                    min_scale: args.get(2).map(Arg::text).and_then(|s| s.parse().ok()).unwrap_or(0.6),
                    base_size: None,
                    text: None,
                    by_name: None,
                };
                let fits = &mut extras.borrow_mut().fits;
                fits.retain(|f| f.target != fit.target);
                if truthy(args.get(1)) {
                    fits.push(fit);
                }
                Call::Done(Vec::new())
            }
            // the field under a text box being typed in: the typed text, shown again (the
            // screen hides it while the box is open); without a text only shown again
            "SetEditText" => {
                let field = text(0);
                let typed = match args.get(1) {
                    None | Some(Arg::Nil) => None,
                    Some(typed) => Some(typed.text()),
                };
                Call::Later(Box::new(move || {
                    with_movie(&name, &player, |m| {
                        if let Some(typed) = typed {
                            m.set_property(&field, "text", ExternalValue::String(typed));
                        }
                        m.set_property(&field, "_visible", ExternalValue::Bool(true));
                    });
                    Vec::new()
                }))
            }
            // for the native text box drawn over the field; the field itself shows the text here
            "GetFontSize" => Call::Done(vec![Arg::Num(20.0)]),
            "GetFontColor" => Call::Done(vec![Arg::Num(0xFFFFFF as f64)]),
            // gameswf settings without a meaning here
            "SetTextBufferingEnabled" | "clearFonts" => Call::Done(Vec::new()),
            _ => {
                *self.missing.entry(method.to_string()).or_default() += 1;
                Call::Done(Vec::new())
            }
        }
    }

    fn commands(&mut self) -> Vec<(String, String, String)> {
        std::mem::take(&mut *self.commands.lock().unwrap())
    }

    fn begin_frame(&mut self) {
        self.rendered.clear();
    }

    fn free(&mut self, object: &str) {
        if self.screens.remove(object).is_some() {
            tracing::debug!("{object}: freed");
        }
        self.rendered.retain(|o| o != object);
    }

    fn as_any(&mut self) -> &mut dyn std::any::Any {
        self
    }
}
