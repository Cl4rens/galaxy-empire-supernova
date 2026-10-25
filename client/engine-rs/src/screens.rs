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
}

fn to_external(arg: &Arg) -> ExternalValue {
    match arg {
        Arg::Nil => ExternalValue::Undefined,
        Arg::Bool(b) => ExternalValue::Bool(*b),
        Arg::Num(n) => ExternalValue::Number(*n),
        Arg::Str(s) => ExternalValue::String(s.clone()),
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
        })
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
                Vec::new()
            }
            "Render" => {
                drop(p);
                self.rendered.retain(|o| o != object);
                self.rendered.push(object.to_string());
                Vec::new()
            }
            "OnTouchPressed" | "OnTouchMoved" | "OnTouchReleased" => {
                let (x, y) = point();
                let event = match method {
                    "OnTouchPressed" => PlayerEvent::MouseDown { x, y, button: MouseButton::Left, index: None },
                    "OnTouchMoved" => PlayerEvent::MouseMove { x, y },
                    _ => PlayerEvent::MouseUp { x, y, button: MouseButton::Left },
                };
                vec![Arg::Bool(p.handle_event(event))]
            }
            // pictures the game puts in at run time: not yet (they keep their placeholder)
            "ReplaceTexture" => vec![Arg::Num(1.0), Arg::Num(1.0)],
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
