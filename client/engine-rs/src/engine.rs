//! The engine core: the game's own Lua scripts in a Lua 5.1 VM with the native API they expect.
//! A port of client/supernova_client/engine.py; both use the same prelude.lua.

use std::cell::RefCell;
use std::collections::HashMap;
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::mpsc::{Receiver, Sender, channel};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Result, anyhow};
use mlua::serde::{DeserializeOptions, SerializeOptions};
use mlua::{Function, Lua, LuaSerdeExt, MultiValue, Table, Value};

use crate::assets::Assets;
use crate::audio::Audio;
use crate::crypto;

const PRELUDE: &str = include_str!("../../supernova_client/prelude.lua");

/// The APK's file lists are obfuscated; the updater only needs the build's resource version.
const GENERATED_FILES: [(&str, &str); 2] =
    [("ofl.tfl", "originfilelist = {version = 6507}"), ("ofl_64.tfl", "originfilelist = {version = 6507}")];
const OFFICIAL_GATEWAYS: [&str; 2] =
    ["b.portal-platform.tap4fun.com/ge/url_get/online/world/", "b.portal-platform.t4f.cn/ge/url_get/online/world/"];

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Touch {
    Pressed,
    Moved,
    Released,
}

/// A value passed between the scripts and the screens.
#[derive(Clone, Debug, PartialEq)]
pub enum Arg {
    Nil,
    Bool(bool),
    Num(f64),
    Str(String),
}

impl Arg {
    fn from_lua(value: &Value) -> Self {
        match value {
            Value::Nil => Arg::Nil,
            Value::Boolean(b) => Arg::Bool(*b),
            Value::Integer(i) => Arg::Num(*i as f64),
            Value::Number(n) => Arg::Num(*n),
            Value::String(s) => Arg::Str(s.to_string_lossy()),
            // Flash only takes plain values (the server sends `{}` for an empty last chat line)
            Value::Table(_) => Arg::Str(String::new()),
            other => Arg::Str(text_of(other)),
        }
    }

    fn to_lua(&self, lua: &Lua) -> mlua::Result<Value> {
        Ok(match self {
            Arg::Nil => Value::Nil,
            Arg::Bool(b) => Value::Boolean(*b),
            Arg::Num(n) => Value::Number(*n),
            Arg::Str(s) => Value::String(lua.create_string(s)?),
        })
    }

    pub fn text(&self) -> String {
        match self {
            Arg::Nil => String::new(),
            Arg::Bool(b) => b.to_string(),
            Arg::Num(n) => text_of(&Value::Number(*n)),
            Arg::Str(s) => s.clone(),
        }
    }
}

/// The game's texts by key, e.g. "LC_MENU_Buy" -> "Buy"
pub type Texts = HashMap<String, String>;

/// The answer to a call on a Flash object: at once, or from a job the engine runs once it let go
/// of its state (the job may run ActionScript, which may call the scripts, which may call the
/// engine again).
pub enum Call {
    Done(Vec<Arg>),
    Later(Box<dyn FnOnce() -> Vec<Arg>>),
}

/// What the game does with its Flash screens. The headless recorder only remembers the calls;
/// the Ruffle screens draw them.
pub trait Screens {
    /// A method call on a Flash object (Load, InvokeASCallback, Update, ReplaceTexture, ...);
    /// `texts` is the game's text table (`LC_MENU_X` -> text) as loaded so far.
    fn call(&mut self, assets: &Assets, texts: &Texts, object: &str, method: &str, args: &[Arg]) -> Call;
    /// Commands the screens sent since the last call: (object, command, argument).
    fn commands(&mut self) -> Vec<(String, String, String)> {
        Vec::new()
    }
    /// A new frame starts; the game is about to call Render on what it shows.
    fn begin_frame(&mut self) {}
    /// The game let go of the object (it is gone with the scripts' garbage).
    fn free(&mut self, _object: &str) {}
    fn as_any(&mut self) -> &mut dyn std::any::Any;
}

#[derive(Default)]
pub struct Recorder {
    pub calls: Vec<(String, String, Vec<Arg>)>,
}

impl Screens for Recorder {
    fn call(&mut self, _assets: &Assets, _texts: &Texts, object: &str, method: &str, args: &[Arg]) -> Call {
        self.calls.push((object.to_string(), method.to_string(), args.to_vec()));
        Call::Done(if method == "ReplaceTexture" { vec![Arg::Num(1.0), Arg::Num(1.0)] } else { Vec::new() })
    }
    fn as_any(&mut self) -> &mut dyn std::any::Any {
        self
    }
}

struct Answer {
    name: String,
    method: String,
    url: String,
    status: u16,
    body: Option<Vec<u8>>,
    error: Option<String>,
    mode: String,
    localpath: String,
}

pub struct State {
    pub assets: Assets,
    key: [u8; 32],
    server: String,
    save_dir: PathBuf,
    udid: String,
    language: String,
    texts: Texts,
    pub missing: HashMap<String, u32>,
    pub alerts: Vec<(String, String)>,
    pub requests: Vec<(String, String, u16)>,
    agent: ureq::Agent,
    answers: (Sender<Answer>, Receiver<Answer>),
    pending: usize,
    /// The keyboard the game wants: shown (with the input type) or hidden, until the app took it.
    keyboard: Option<Keyboard>,
    /// music and sounds, once the app switched them on (the test tools stay quiet)
    audio: Option<Audio>,
    pub screens: Box<dyn Screens>,
}

/// The game opens or closes a text box (see TextEditInput_t in the prelude).
#[derive(Debug, Clone, PartialEq)]
pub enum Keyboard {
    Show { input_type: String },
    Hide,
}

pub struct Engine {
    pub lua: Lua,
    pub state: Rc<RefCell<State>>,
    last_step: Instant,
}

fn text_of(value: &Value) -> String {
    match value {
        Value::String(s) => s.to_string_lossy(),
        Value::Integer(i) => i.to_string(),
        Value::Number(n) => {
            if n.fract() == 0.0 && n.abs() < 1e15 { format!("{}", *n as i64) } else { n.to_string() }
        }
        Value::Boolean(b) => b.to_string(),
        Value::Nil => String::new(),
        other => format!("{other:?}"),
    }
}

/// Where downloads go that the game then loads as its own files (`localpath` "data2/x.png")
const DOWNLOADED_DATA: &str = "data2/";

fn safe_name(name: &str) -> String {
    name.replace('/', "__").chars().map(|c| if c.is_ascii_alphanumeric() || "_.-".contains(c) { c } else { '_' }).collect()
}

/// A text table file: u16 count, then u16-length-prefixed UTF-8 strings.
fn strings(data: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    let mut off = 2;
    while off + 2 <= data.len() {
        let n = u16::from_le_bytes([data[off], data[off + 1]]) as usize;
        off += 2;
        out.push(String::from_utf8_lossy(&data[off..(off + n).min(data.len())]).to_string());
        off += n;
    }
    out
}

impl Engine {
    pub fn new(apk: PathBuf, server: &str, save_dir: PathBuf, language: &str, screens: Box<dyn Screens>) -> Result<Self> {
        let assets = Assets::open(&apk)?;
        let key = crypto::client_key(&apk)?;
        Self::with_assets(assets, key, server, save_dir, language, screens)
    }

    pub fn with_assets(
        assets: Assets,
        key: [u8; 32],
        server: &str,
        save_dir: PathBuf,
        language: &str,
        screens: Box<dyn Screens>,
    ) -> Result<Self> {
        std::fs::create_dir_all(&save_dir)?;
        let mut assets = assets;
        for entry in std::fs::read_dir(save_dir.join(DOWNLOADED_DATA)).into_iter().flatten().flatten() {
            if let (Some(name), Ok(data)) = (entry.file_name().to_str(), std::fs::read(entry.path())) {
                assets.add_file(&format!("{DOWNLOADED_DATA}{name}"), data);
            }
        }
        let udid_file = save_dir.join("UDID");
        let udid = std::fs::read_to_string(&udid_file).ok().map(|s| s.trim().to_string()).unwrap_or_else(|| {
            let seed = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
            crypto::md5_hex(seed.to_string().as_bytes()).to_uppercase()
        });
        std::fs::write(&udid_file, &udid)?;
        let state = Rc::new(RefCell::new(State {
            assets,
            key,
            server: format!("{}/", server.trim_end_matches('/')),
            save_dir,
            udid,
            language: language.to_string(),
            texts: HashMap::new(),
            missing: HashMap::new(),
            alerts: Vec::new(),
            requests: Vec::new(),
            agent: ureq::AgentBuilder::new().timeout(Duration::from_secs(20)).build(),
            answers: channel(),
            pending: 0,
            keyboard: None,
            audio: None,
            screens,
        }));
        // the game uses the debug library (tracebacks), which mlua leaves out by default
        let lua = unsafe { Lua::unsafe_new() };
        let native = native_table(&lua, &state)?;
        lua.load(PRELUDE).set_name("=prelude").call::<()>(native)?;
        // Flash commands reach the scripts at once, as with gameswf (see screens.rs)
        let weak = lua.weak();
        crate::screens::set_fscommand_handler(Some(std::rc::Rc::new(move |object: &str, command: &str, arg: &str| {
            if let Some(lua) = weak.try_upgrade()
                && let Err(e) = dispatch_fscommand(&lua, object, command, arg)
            {
                tracing::warn!("{object}: fscommand {command}: {e:#}");
            }
        })));
        Ok(Self { lua, state, last_step: Instant::now() })
    }

    /// Run AutoUpdateInit, the script the native engine starts with.
    pub fn boot(&self) -> Result<()> {
        let chunk = self.state.borrow().assets.script("AutoUpdateInit").ok_or_else(|| anyhow!("no AutoUpdateInit"))?.to_vec();
        self.lua.load(chunk).set_name("@./AutoUpdateInit.lua").exec()?;
        Ok(())
    }

    /// One frame: hand finished requests to the game, then let it update (dt in milliseconds).
    pub fn step(&mut self) -> Result<()> {
        let now = Instant::now();
        let dt = now.duration_since(self.last_step).as_secs_f64() * 1000.0;
        self.last_step = now;
        loop {
            let answer = { self.state.borrow().answers.1.try_recv() };
            let Ok(answer) = answer else { break };
            self.deliver(answer)?;
        }
        let commands = self.state.borrow_mut().screens.commands();
        for (object, command, arg) in commands {
            self.fscommand(&object, &command, &arg)?;
        }
        if let Ok(update) = self.lua.globals().raw_get::<Function>("Update") {
            update.call::<()>(dt)?;
        }
        if let Ok(frame) = self.lua.globals().raw_get::<Function>("__sn_text_input_frame") {
            frame.call::<()>(())?;
        }
        // the Flash objects the scripts let go of, and made no new one of since (a step of the
        // garbage collector each frame finds them soon)
        self.lua.gc_step()?;
        if let Ok(dropped) = self.lua.globals().raw_get::<Function>("__sn_dropped_flash") {
            let names: Vec<String> = dropped.call(())?;
            let mut s = self.state.borrow_mut();
            for name in names {
                s.screens.free(&name);
            }
        }
        Ok(())
    }

    /// Let the game draw: it calls Render on every object it shows, bottom first.
    pub fn render(&mut self) -> Result<()> {
        self.state.borrow_mut().screens.begin_frame();
        if let Ok(render) = self.lua.globals().raw_get::<Function>("Render") {
            render.call::<()>(())?;
        }
        Ok(())
    }

    /// Run the game for `seconds` or until `until()` is true.
    pub fn run(&mut self, seconds: f64, mut until: impl FnMut(&Engine) -> bool) -> Result<bool> {
        let end = Instant::now() + Duration::from_secs_f64(seconds);
        while Instant::now() < end {
            self.step()?;
            if until(self) {
                return Ok(true);
            }
            std::thread::sleep(Duration::from_millis(33));
        }
        Ok(false)
    }

    fn deliver(&self, a: Answer) -> Result<()> {
        let content: Value = {
            let mut state = self.state.borrow_mut();
            state.pending -= 1;
            state.requests.push((a.method.clone(), a.url.clone(), a.status));
            match (&a.error, a.body) {
                (Some(_), _) | (None, None) => Value::Nil,
                // pictures the scripts fetch into data2/ (item icons): kept, and from now on
                // found like the game's own files under their name
                (None, Some(body)) if a.mode == "download" && a.status == 200 && a.localpath.trim_start_matches("./").starts_with(DOWNLOADED_DATA) => {
                    let name = safe_name(a.localpath.rsplit('/').next().unwrap_or_default());
                    let dir = state.save_dir.join(DOWNLOADED_DATA);
                    std::fs::create_dir_all(&dir)?;
                    let path = dir.join(&name);
                    std::fs::write(&path, &body)?;
                    state.assets.add_file(&format!("{DOWNLOADED_DATA}{name}"), body);
                    Value::String(self.lua.create_string(path.to_string_lossy().as_bytes())?)
                }
                (None, Some(body)) if a.mode == "download" => {
                    let dir = state.save_dir.join("downloads");
                    std::fs::create_dir_all(&dir)?;
                    let path = dir.join(safe_name(if a.localpath.is_empty() { &a.url } else { &a.localpath }));
                    std::fs::write(&path, body)?;
                    Value::String(self.lua.create_string(path.to_string_lossy().as_bytes())?)
                }
                (None, Some(body)) if a.status != 200 => Value::String(self.lua.create_string(&body)?),
                (None, Some(body)) => {
                    let text = if a.mode == "normal" { crypto::decrypt(&state.key, &body).unwrap_or(body.clone()) } else { body.clone() };
                    match serde_json::from_slice::<serde_json::Value>(&text) {
                        Ok(json) => self.lua.to_value_with(&json, json_to_lua())?,
                        Err(_) => Value::String(self.lua.create_string(&body)?),
                    }
                }
            }
        };
        let done: Function = self.lua.globals().raw_get("__sn_http_done")?;
        done.call::<()>((a.name, a.status, content, a.error))?;
        Ok(())
    }

    /// What a Flash file does when the player presses something: it sends a command.
    pub fn fscommand(&self, class: &str, command: &str, arg: &str) -> Result<()> {
        dispatch_fscommand(&self.lua, class, command, arg)
    }

    /// A finger (or the mouse) on the screen, in the game's 640x960 coordinates: the game's
    /// global OnTouchPressed/OnTouchMoved/OnTouchReleased hand it to its objects, top first.
    pub fn touch(&self, phase: Touch, x: f64, y: f64) -> Result<()> {
        let name = match phase {
            Touch::Pressed => "OnTouchPressed",
            Touch::Moved => "OnTouchMoved",
            Touch::Released => "OnTouchReleased",
        };
        if let Ok(handler) = self.lua.globals().raw_get::<Function>(name) {
            handler.call::<()>((x, y))?;
        }
        Ok(())
    }

    /// Typed text for the open text box: a backspace character deletes, a newline closes the box.
    pub fn text_input(&self, text: &str) -> Result<()> {
        if let Ok(input) = self.lua.globals().raw_get::<Function>("__sn_text_input") {
            input.call::<()>(text)?;
        }
        Ok(())
    }

    /// The on-screen keyboard's top edge moved (a part of the game's height from the top, 1
    /// when it is gone): the game moves the screen with the open text box above it.
    pub fn keyboard_moved(&self, top: f64) -> Result<()> {
        let keyboard: Table = self.lua.globals().raw_get::<Table>("ext")?.raw_get("keyboard")?;
        if let Ok(Value::Function(shown)) = keyboard.raw_get::<Value>("onKeyboardShow") {
            // the game takes the keyboard's height, as a part of the screen's
            shown.call::<()>((0.0, 1.0 - top))?;
        }
        Ok(())
    }

    /// Whether the keyboard should come up or go away, once after the game asked for it.
    pub fn take_keyboard(&self) -> Option<Keyboard> {
        self.state.borrow_mut().keyboard.take()
    }

    /// Play the game's music and sounds on the system's sound output.
    pub fn enable_audio(&self) {
        self.state.borrow_mut().audio = Audio::open();
    }

    /// Two fingers on the screen, in the game's coordinates: OnMultiTouchBegin/Move/End.
    pub fn two_touch(&self, phase: Touch, x1: f64, y1: f64, x2: f64, y2: f64) -> Result<()> {
        let name = match phase {
            Touch::Pressed => "OnMultiTouchBegin",
            Touch::Moved => "OnMultiTouchMove",
            Touch::Released => "OnMultiTouchEnd",
        };
        if let Ok(handler) = self.lua.globals().raw_get::<Function>(name) {
            handler.call::<()>((x1, y1, x2, y2))?;
        }
        Ok(())
    }

    /// The app goes to the background or comes back (OnPause/OnResume).
    pub fn pause(&self, paused: bool) -> Result<()> {
        if let Some(audio) = &mut self.state.borrow_mut().audio {
            audio.pause(paused);
        }
        let name = if paused { "OnPause" } else { "OnResume" };
        if let Ok(handler) = self.lua.globals().raw_get::<Function>(name) {
            handler.call::<()>(())?;
        }
        Ok(())
    }

    /// Send a Flash command to a screen object by its LuaObjectManager name.
    pub fn press(&self, object: &str, command: &str, arg: &str) -> Result<()> {
        let class: String = self
            .lua
            .load("local o = LuaObjectManager:GetLuaObject(...); return o and o:GetFlashObjectClassName()")
            .call(object)?;
        self.fscommand(&class, command, arg)
    }

    pub fn state_name(&self) -> Option<String> {
        self.lua
            .load(
                r#"if rawget(_G, "GameStateManager") == nil then return nil end
                local current = GameStateManager:GetCurrentGameState()
                for k, v in pairs(GameStateManager) do
                  if v == current and type(k) == "string" and string.sub(k, 1, 9) == "GameState" then return k end
                end"#,
            )
            .eval()
            .ok()
            .flatten()
    }

    pub fn active_objects(&self) -> Vec<String> {
        let names: String = self
            .lua
            .load(
                r#"local out = {}
                if rawget(_G, "GameStateManager") == nil then return "" end
                local state = GameStateManager:GetCurrentGameState()
                for _, o in ipairs(state and state.m_menuObjectList or {}) do table.insert(out, o.m_objectName or "?") end
                return table.concat(out, ",")"#,
            )
            .eval()
            .unwrap_or_default();
        names.split(',').filter(|s| !s.is_empty()).map(str::to_string).collect()
    }
}

/// Hand a Flash command to the scripts: the fscommand handler of the object's Flash class.
/// PLAYSFX (a button's click) the engine plays itself, as gameswf did.
fn dispatch_fscommand(lua: &Lua, class: &str, command: &str, arg: &str) -> Result<()> {
    if command == "PLAYSFX" {
        let audio: Table = lua.globals().raw_get::<Table>("ext")?.raw_get("audio")?;
        audio.get::<Function>("playEffect")?.call::<()>(format!("sound/{arg}"))?;
        return Ok(());
    }
    let classes: Table = lua.globals().raw_get("__sn_flash_classes")?;
    let class_table: Table = classes.get(class).map_err(|_| anyhow!("no Flash class {class}"))?;
    let handler: Function = class_table.get("fscommand").map_err(|_| anyhow!("{class} has no fscommand handler"))?;
    handler.call::<()>((lua.create_table()?, command, arg))?;
    Ok(())
}

fn json_to_lua() -> SerializeOptions {
    SerializeOptions::new().serialize_none_to_null(false).serialize_unit_to_null(false).set_array_metatable(false)
}

fn rewrite(server: &str, url: &str) -> String {
    for scheme in ["https://", "http://"] {
        for gateway in OFFICIAL_GATEWAYS {
            if let Some(rest) = url.strip_prefix(&format!("{scheme}{gateway}")) {
                return format!("{server}{rest}");
            }
        }
    }
    url.strip_prefix("https://").map(|rest| format!("http://{rest}")).unwrap_or_else(|| url.to_string())
}

fn native_table(lua: &Lua, state: &Rc<RefCell<State>>) -> Result<Table> {
    let t = lua.create_table()?;
    macro_rules! native {
        ($name:literal, |$st:ident, $lua:ident, $args:ident| $body:expr) => {{
            let state = state.clone();
            t.set(
                $name,
                lua.create_function(#[allow(unused_variables)] move |$lua, $args: MultiValue| {
                    #[allow(unused_variables)]
                    let $st = &state;
                    #[allow(unused_variables)]
                    let $args: Vec<Value> = $args.into_iter().collect();
                    $body
                })?,
            )?;
        }};
    }
    let arg = |args: &Vec<Value>, i: usize| args.get(i).map(text_of).unwrap_or_default();

    native!("missing", |st, lua, args| {
        let name = arg(&args, 0);
        let mut s = st.borrow_mut();
        let n = s.missing.entry(name.clone()).or_default();
        if *n == 0 {
            tracing::info!("missing native {name}");
        }
        *n += 1;
        Ok(Value::Nil)
    });
    native!("missing_global", |st, lua, args| {
        *st.borrow_mut().missing.entry(format!("global {}", arg(&args, 0))).or_default() += 1;
        Ok(Value::Nil)
    });
    native!("udid", |st, lua, args| Ok(Value::String(lua.create_string(&st.borrow().udid)?)));
    native!("language", |st, lua, args| Ok(Value::String(lua.create_string(&st.borrow().language)?)));
    native!("now", |st, lua, args| {
        let ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64() * 1000.0;
        let _ = st;
        Ok(Value::Number(ms))
    });
    native!("log", |st, lua, args| {
        tracing::debug!("lua: {}", arg(&args, 0));
        Ok(Value::Nil)
    });
    native!("alert", |st, lua, args| {
        st.borrow_mut().alerts.push((arg(&args, 0), arg(&args, 1)));
        Ok(Value::Nil)
    });
    native!("save", |st, lua, args| {
        if let Some(Value::String(text)) = args.get(1) {
            let path = st.borrow().save_dir.join(safe_name(&arg(&args, 0)));
            std::fs::write(path, text.as_bytes()).map_err(mlua::Error::external)?;
        }
        Ok(Value::Nil)
    });
    native!("load", |st, lua, args| {
        let name = arg(&args, 0);
        let base = name.replace('\\', "/");
        let base = base.rsplit('/').next().unwrap_or(&base).to_string();
        if let Some((_, text)) = GENERATED_FILES.iter().find(|(n, _)| *n == base) {
            return Ok(Value::String(lua.create_string(text)?));
        }
        let path = st.borrow().save_dir.join(safe_name(&name));
        match std::fs::read(path) {
            Ok(data) => Ok(Value::String(lua.create_string(&data)?)),
            Err(_) => Ok(Value::Nil),
        }
    });
    native!("script", |st, lua, args| {
        match st.borrow().assets.script(&arg(&args, 0)) {
            Some(chunk) => Ok(Value::String(lua.create_string(chunk)?)),
            None => Ok(Value::Nil),
        }
    });
    native!("crc", |st, lua, args| Ok(Value::Number(crc32fast::hash(arg(&args, 0).as_bytes()) as f64)));
    native!("md5", |st, lua, args| {
        let data = match args.first() { Some(Value::String(s)) => s.as_bytes().to_vec(), other => other.map(text_of).unwrap_or_default().into_bytes() };
        Ok(Value::String(lua.create_string(crypto::md5_hex(&data).to_uppercase())?))
    });
    native!("enc2", |st, lua, args| {
        let key = match args.first() { Some(Value::String(s)) if s.as_bytes().len() >= 32 => s.as_bytes().to_vec(), _ => st.borrow().key.to_vec() };
        let data = match args.get(1) { Some(Value::String(s)) => s.as_bytes().to_vec(), other => other.map(text_of).unwrap_or_default().into_bytes() };
        Ok(Value::String(lua.create_string(crypto::encrypt(&key, &data))?))
    });
    native!("copy", |st, lua, args| Ok(Value::Nil));
    native!("audio", |st, lua, args| {
        let (command, value) = (arg(&args, 0), arg(&args, 1));
        let mut s = st.borrow_mut();
        let State { assets, audio, .. } = &mut *s;
        let Some(audio) = audio else { return Ok(Value::Nil) };
        let sound = || assets.file(value.trim_start_matches("./")).map(<[u8]>::to_vec);
        let volume = || value.parse::<f32>().unwrap_or(1.0);
        match command.as_str() {
            "music" => match sound() {
                Some(data) => audio.play_music(data),
                None => tracing::warn!("no music {value}"),
            },
            "stop" => audio.stop_music(),
            "effect" => match sound() {
                Some(data) => audio.play_effect(data),
                None => tracing::warn!("no sound {value}"),
            },
            "music_volume" => audio.set_music_volume(volume()),
            "sound_volume" => audio.set_sound_volume(volume()),
            _ => {}
        }
        Ok(Value::Nil)
    });
    native!("keyboard", |st, lua, args| {
        let show = matches!(args.first(), Some(Value::Boolean(true)));
        st.borrow_mut().keyboard = Some(if show { Keyboard::Show { input_type: arg(&args, 1) } } else { Keyboard::Hide });
        Ok(Value::Nil)
    });
    native!("json_parse", |st, lua, args| {
        let json: serde_json::Value = serde_json::from_str(&arg(&args, 0)).map_err(mlua::Error::external)?;
        lua.to_value_with(&json, json_to_lua())
    });
    native!("json_generate", |st, lua, args| {
        let value = args.first().cloned().unwrap_or(Value::Nil);
        let json: serde_json::Value = lua.from_value_with(value, DeserializeOptions::new().deny_unsupported_types(false))?;
        Ok(Value::String(lua.create_string(json.to_string())?))
    });
    native!("http", |st, lua, args| {
        let (name, url, param, body, method, mode) =
            (arg(&args, 0), arg(&args, 1), arg(&args, 2), arg(&args, 3), arg(&args, 4).to_uppercase(), arg(&args, 5));
        let mut headers = Vec::new();
        if let Some(Value::Table(h)) = args.get(6) {
            for pair in h.pairs::<Value, Value>() {
                let (k, v) = pair?;
                let k = text_of(&k);
                if k != "Content-Length" && k != "Accept-Encoding" {
                    headers.push((k, text_of(&v)));
                }
            }
        }
        let localpath = arg(&args, 7);
        let mut s = st.borrow_mut();
        let url = rewrite(&s.server, &url);
        let full = if method == "GET" { format!("{url}{param}") } else { url };
        s.pending += 1;
        let (agent, tx) = (s.agent.clone(), s.answers.0.clone());
        std::thread::spawn(move || {
            let mut request = agent.request(&method, &full);
            for (k, v) in &headers {
                request = request.set(k, v);
            }
            tracing::debug!("{method} {full} {body}");
            let result = if method == "POST" { request.send_string(&body) } else { request.call() };
            let (status, body, error) = match result {
                Ok(r) | Err(ureq::Error::Status(_, r)) => {
                    let status = r.status();
                    let mut data = Vec::new();
                    let _ = std::io::Read::read_to_end(&mut r.into_reader(), &mut data);
                    (status, Some(data), None)
                }
                Err(e) => (0, None, Some(e.to_string())),
            };
            let _ = tx.send(Answer { name, method, url: full, status, body, error, mode, localpath });
        });
        Ok(Value::Nil)
    });
    native!("flash", |st, lua, args| {
        let object = arg(&args, 0);
        let method = arg(&args, 1);
        let rest: Vec<Arg> = args.iter().skip(2).map(Arg::from_lua).collect();
        let call = {
            let mut s = st.borrow_mut();
            let State { assets, texts, screens, .. } = &mut *s;
            screens.call(assets, texts, &object, &method, &rest)
        };
        let result = match call {
            Call::Done(result) => result,
            Call::Later(job) => job(),
        };
        let values: Vec<Value> = result.iter().map(|r| r.to_lua(lua)).collect::<mlua::Result<_>>()?;
        Ok(MultiValue::from_vec(values))
    });
    native!("load_text", |st, lua, args| {
        let (locale, name) = (arg(&args, 0), arg(&args, 1));
        let mut s = st.borrow_mut();
        let (Some(index), Some(values)) = (
            s.assets.file(&format!("data2/text/{name}.idx")).map(strings),
            s.assets.file(&format!("data2/text/{name}.{locale}")).map(strings),
        ) else {
            return Ok(Value::Boolean(false));
        };
        let prefix = format!("LC_{}_", name.to_uppercase());
        for (k, v) in index.into_iter().zip(values) {
            s.texts.insert(format!("{prefix}{k}"), v);
        }
        Ok(Value::Boolean(true))
    });
    native!("text", |st, lua, args| {
        let key = arg(&args, 0);
        let s = st.borrow();
        Ok(Value::String(lua.create_string(s.texts.get(&key).unwrap_or(&key))?))
    });
    Ok(t)
}
