//! The game's Flash objects as Ruffle players, one per object, like gameswf had them.
//!
//! The scripts call methods on their Flash objects (Load, InvokeASCallback, Update, Render,
//! OnTouchPressed, ...); Flash commands come back through Ruffle's FsCommandProvider. Each
//! player draws into its own texture, transparently; `frame()` stacks them in the order the
//! game rendered them.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use anyhow::{Result, anyhow};
use ruffle_core::backend::ui::FontDefinition;
use ruffle_core::events::MouseButton;
use ruffle_core::font::{DefaultFont, FontFileData};
use ruffle_core::external::{FsCommandProvider, Value as ExternalValue};
use ruffle_core::limits::ExecutionLimit;
use ruffle_core::tag_utils::SwfMovieData;
use ruffle_core::{FloatDuration, Player, PlayerBuilder, PlayerEvent};
use ruffle_render_wgpu::backend::{WgpuRenderBackend, create_wgpu_instance, request_adapter_and_device};
use ruffle_render_wgpu::descriptors::Descriptors;
use ruffle_render_wgpu::target::TextureTarget;
use ruffle_render_wgpu::wgpu;

use crate::assets::Assets;
use crate::convert::Converter;
use crate::engine::{Arg, Screens};

pub const WIDTH: u32 = 640;
pub const HEIGHT: u32 = 960;
/// The font gameswf drew text fields with
const FONT: &str = "data2/Arial.ttf";

type Commands = Arc<Mutex<Vec<(String, String, String)>>>;

struct Sender {
    object: String,
    commands: Commands,
}

impl FsCommandProvider for Sender {
    fn on_fs_command(&self, command: &str, args: &str) -> bool {
        tracing::debug!("{}: fscommand {command} {args}", self.object);
        self.commands.lock().unwrap().push((self.object.clone(), command.to_string(), args.to_string()));
        true
    }
}

pub struct RuffleScreens {
    descriptors: Arc<Descriptors>,
    converter: Converter,
    players: HashMap<String, Arc<Mutex<Player>>>,
    commands: Commands,
    /// objects in the order the game rendered them this frame
    pub rendered: Vec<String>,
    pub missing: HashMap<String, u32>,
    /// text fields that shrink their text to fit (SetLineMax), per object
    fits: HashMap<String, Vec<Fit>>,
    /// gameswf's native buttons, per object
    buttons: HashMap<String, Buttons>,
}

/// gameswf drove clips named "btn..." itself: a touch plays their "focus_in" frames, leaving
/// them "focus_out", and letting go on them "released" and calls their `on_clicked`.
/// SetBtnEnable switches them between "disabled" and "activated".
#[derive(Default)]
struct Buttons {
    /// the button the finger went down on, and whether it is still on it
    pressed: Option<(String, bool)>,
    disabled: std::collections::HashSet<String>,
}

const BUTTON_PREFIX: &str = "btn";

/// "_level0.panel.btnOk" as the scripts write it: "_root.panel.btnOk"
fn script_path(path: &str) -> String {
    match path.strip_prefix("_level0") {
        Some(rest) => format!("_root{rest}"),
        None => path.to_string(),
    }
}

fn play(player: &mut Player, button: &str, label: &str, callback: &str) {
    player.call_avm1_function(button, "gotoAndPlay", vec![ExternalValue::String(label.to_string())]);
    if !callback.is_empty() {
        player.call_avm1_function(button, callback, vec![]);
    }
}

impl Buttons {
    /// Returns whether the touch is on a button.
    fn touch(&mut self, player: &mut Player, method: &str, x: f64, y: f64) -> bool {
        let under = player.find_avm1_button(x, y, BUTTON_PREFIX).map(|p| script_path(&p));
        match method {
            "OnTouchPressed" => {
                let Some(button) = under else { return false };
                if !self.disabled.contains(&button) {
                    play(player, &button, "focus_in", "on_focus_in");
                    self.pressed = Some((button, true));
                }
                true
            }
            "OnTouchMoved" => {
                let Some((button, inside)) = &mut self.pressed else { return false };
                let now_inside = under.as_deref() == Some(button.as_str());
                if now_inside != *inside {
                    *inside = now_inside;
                    let (label, callback) = if now_inside { ("focus_in", "on_focus_in") } else { ("focus_out", "on_focus_out") };
                    play(player, button, label, callback);
                }
                true
            }
            _ => {
                let Some((button, inside)) = self.pressed.take() else { return false };
                if inside {
                    play(player, &button, "released", "on_clicked");
                }
                true
            }
        }
    }

    fn set_enabled(&mut self, player: &mut Player, button: &str, enabled: bool) {
        let button = script_path(button);
        if enabled {
            if self.disabled.remove(&button) {
                play(player, &button, "activated", "");
            }
        } else {
            if self.pressed.as_ref().is_some_and(|(b, _)| *b == button) {
                self.pressed = None;
            }
            play(player, &button, "disabled", "");
            self.disabled.insert(button);
        }
    }
}

struct Fit {
    target: String,
    min_scale: f64,
    base_size: Option<f64>,
    text: Option<String>,
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
        // the screens' ActionScript was written for gameswf, which ignored the case of names
        // (`htmltext` for `htmlText`)
        ruffle_core::external::set_avm1_case_insensitive(true);
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
            players: HashMap::new(),
            commands: Arc::default(),
            rendered: Vec::new(),
            missing: HashMap::new(),
            fits: HashMap::new(),
            buttons: HashMap::new(),
        })
    }

    /// RGBA pixels of a picture the game names for ReplaceTexture: a data2 file or a
    /// downloaded one, with its size.
    fn picture(&mut self, assets: &Assets, name: &str) -> Option<image::RgbaImage> {
        let data = self.converter.picture(assets, name).or_else(|| std::fs::read(name).ok())?;
        image::load_from_memory(&data).ok().map(|i| i.to_rgba8())
    }

    pub fn descriptors(&self) -> Arc<Descriptors> {
        self.descriptors.clone()
    }

    fn load(&mut self, assets: &Assets, object: &str, path: &str) -> Result<()> {
        let swf = self.converter.screen(assets, path)?;
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
            .with_autoplay(true)
            .build();
        {
            let mut p = player.lock().unwrap();
            if let Some(font) = assets.file(FONT) {
                p.register_device_font(FontDefinition::FontFile {
                    name: "Arial".into(),
                    is_bold: false,
                    is_italic: false,
                    data: FontFileData::new(font.to_vec()),
                    index: 0,
                });
                for default in [DefaultFont::Sans, DefaultFont::Serif, DefaultFont::Typewriter] {
                    p.set_default_font(default, vec!["Arial".into()]);
                }
            }
            p.set_window_mode("transparent");
            p.preload(&mut ExecutionLimit::none());
            p.run_frame();
        }
        self.players.insert(object.to_string(), player);
        Ok(())
    }

    /// Every screen the game rendered this frame, bottom first, as RGBA pixels.
    pub fn layers(&mut self) -> Vec<(String, image::RgbaImage)> {
        let mut out = Vec::new();
        for object in self.rendered.clone() {
            let Some(player) = self.players.get(&object) else { continue };
            let mut p = player.lock().unwrap();
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
            let Some(player) = self.players.get(object) else { continue };
            let mut p = player.lock().unwrap();
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
        self.players.get(object).cloned()
    }

    pub fn objects(&self) -> impl Iterator<Item = (&String, &Arc<Mutex<Player>>)> {
        self.players.iter()
    }
}

impl Screens for RuffleScreens {
    fn call(&mut self, assets: &Assets, object: &str, method: &str, args: &[Arg]) -> Vec<Arg> {
        let text = |i: usize| args.get(i).map(Arg::text).unwrap_or_default();
        tracing::trace!("{object}.{method}");
        if method == "Load" {
            if let Err(e) = self.load(assets, object, &text(0)) {
                tracing::warn!("{object}: cannot load {}: {e}", text(0));
            }
            return Vec::new();
        }
        let Some(player) = self.players.get(object).cloned() else {
            return Vec::new();
        };
        let mut p = player.lock().unwrap();
        let point = || (args.first().map(Arg::text).and_then(|s| s.parse().ok()).unwrap_or(0.0),
                        args.get(1).map(Arg::text).and_then(|s| s.parse().ok()).unwrap_or(0.0));
        match method {
            "InvokeASCallback" => {
                let target = match args.first() {
                    Some(Arg::Str(t)) if !t.is_empty() => t.clone(),
                    _ => "_root".to_string(),
                };
                let values: Vec<ExternalValue> = args.iter().skip(2).map(to_external).collect();
                let result = p.call_avm1_function(&target, &text(1), values.clone());
                tracing::debug!("{object}: {target}.{}({values:?}) = {result:?}", text(1));
                vec![from_external(result)]
            }
            "Update" => {
                let dt = args.first().map(Arg::text).and_then(|s| s.parse::<f64>().ok()).unwrap_or(33.0);
                p.tick(FloatDuration::from_millis(dt));
                for fit in self.fits.get_mut(object).into_iter().flatten() {
                    if let Some((base, text)) = p.fit_avm1_text(&fit.target, fit.min_scale, fit.base_size, fit.text.as_deref()) {
                        fit.base_size = Some(base);
                        fit.text = Some(text);
                    }
                }
                Vec::new()
            }
            "Render" => {
                drop(p);
                self.rendered.retain(|o| o != object);
                self.rendered.push(object.to_string());
                Vec::new()
            }
            "OnTouchPressed" | "OnTouchMoved" | "OnTouchReleased" => {
                // true when the finger is on something of this screen that reacts to it, so the
                // game does not hand the touch to the screens below
                let (x, y) = point();
                let on_button = self.buttons.entry(object.to_string()).or_default().touch(&mut p, method, x, y);
                let (_, was_pressing) = p.mouse_target();
                let event = match method {
                    "OnTouchPressed" => PlayerEvent::MouseDown { x, y, button: MouseButton::Left, index: None },
                    "OnTouchMoved" => PlayerEvent::MouseMove { x, y },
                    _ => PlayerEvent::MouseUp { x, y, button: MouseButton::Left },
                };
                p.handle_event(event);
                let (hovering, pressing) = p.mouse_target();
                vec![Arg::Bool(on_button || hovering || pressing || was_pressing)]
            }
            // a picture slot (an exported bitmap such as "NA_Item_3.png") gets a picture; the
            // game falls back to a default picture when the size comes back 0, 0
            "ReplaceTexture" => {
                let Some(picture) = self.picture(assets, &text(1)) else {
                    return vec![Arg::Num(0.0), Arg::Num(0.0)];
                };
                let (w, h) = picture.dimensions();
                let replaced = p.replace_bitmap(&text(0), |width, height| {
                    image::imageops::resize(&picture, width, height, image::imageops::FilterType::Triangle).into_raw()
                });
                if !replaced {
                    tracing::debug!("{object}: no picture slot {}", text(0));
                }
                vec![Arg::Num(w as f64), Arg::Num(h as f64)]
            }
            "SetBtnEnable" => {
                let enabled = truthy(args.get(1));
                p.set_avm1_property(&text(0), "enabled", ExternalValue::Bool(enabled));
                self.buttons.entry(object.to_string()).or_default().set_enabled(&mut p, &text(0), enabled);
                Vec::new()
            }
            "SetCustomEffect" => {
                p.set_avm1_gray(&text(0), text(1) == "Gray" && truthy(args.get(2)));
                Vec::new()
            }
            "SetLineMax" => {
                let fit = Fit {
                    target: text(0),
                    min_scale: args.get(2).map(Arg::text).and_then(|s| s.parse().ok()).unwrap_or(0.6),
                    base_size: None,
                    text: None,
                };
                let fits = self.fits.entry(object.to_string()).or_default();
                fits.retain(|f| f.target != fit.target);
                if truthy(args.get(1)) {
                    fits.push(fit);
                }
                Vec::new()
            }
            // gameswf settings without a meaning here
            "SetTextBufferingEnabled" | "clearFonts" => Vec::new(),
            _ => {
                *self.missing.entry(method.to_string()).or_default() += 1;
                Vec::new()
            }
        }
    }

    fn commands(&mut self) -> Vec<(String, String, String)> {
        std::mem::take(&mut *self.commands.lock().unwrap())
    }

    fn begin_frame(&mut self) {
        self.rendered.clear();
    }

    fn as_any(&mut self) -> &mut dyn std::any::Any {
        self
    }
}
