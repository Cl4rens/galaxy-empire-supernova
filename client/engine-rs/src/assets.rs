//! The game files inside the player's own APK: Lua scripts (data1_64.pak) and data2 files.
//! Everything is read at run time; nothing from the APK is part of this project.

use std::collections::HashMap;
use std::io::{Cursor, Read};
use std::path::Path;

use anyhow::{Context, Result, bail};

pub const GAME_ZIP: &str = "assets/tap4fun.zip";
pub const DATA_DIR: &str = "tap4fun/galaxy_empire/AppOriginalData/Android/";
pub const SCRIPTS_64: &str = "data1_64.pak";
const PAK_MAGIC: u32 = 0x9D7F_E2AB;
const FLAG_ZLIB: u32 = 0x4;

fn u32_at(data: &[u8], pos: usize) -> u32 {
    u32::from_le_bytes(data[pos..pos + 4].try_into().unwrap())
}

/// The entries of a tap4fun .pak archive (see docs/file-formats.md).
pub fn pak_entries(data: &[u8]) -> Result<Vec<Vec<u8>>> {
    if data.len() < 16 || u32_at(data, 0) != PAK_MAGIC {
        bail!("not a tap4fun pak");
    }
    let count = u32_at(data, 12) as usize;
    let mut out = Vec::with_capacity(count);
    for i in 0..count {
        let base = 16 + i * 16;
        let (off, size, flags) = (u32_at(data, base) as usize, u32_at(data, base + 4) as usize, u32_at(data, base + 12));
        let blob = &data[off..off + size];
        if flags & FLAG_ZLIB != 0 {
            let mut raw = Vec::new();
            flate2::read::ZlibDecoder::new(&blob[4..]).read_to_end(&mut raw)?;
            out.push(raw);
        } else {
            out.push(blob.to_vec());
        }
    }
    Ok(out)
}

/// The source name a compiled Lua chunk carries, e.g. "@./GameUtils.lua".
pub fn chunk_source(chunk: &[u8]) -> Option<String> {
    if chunk.len() < 12 || &chunk[..4] != b"\x1bLua" {
        return None;
    }
    let size_t = chunk[8] as usize;
    let len = match size_t {
        4 => u32_at(chunk, 12) as usize,
        8 => u64::from_le_bytes(chunk[12..20].try_into().ok()?) as usize,
        _ => return None,
    };
    let start = 12 + size_t;
    let name = chunk.get(start..start + len)?;
    Some(String::from_utf8_lossy(name).trim_end_matches('\0').to_string())
}

/// One file of an APK (a zip), if it is there.
pub fn apk_member(apk: &Path, name: &str) -> Option<Vec<u8>> {
    let mut zip = zip::ZipArchive::new(std::fs::File::open(apk).ok()?).ok()?;
    let mut entry = zip.by_name(name).ok()?;
    let mut data = Vec::with_capacity(entry.size() as usize);
    entry.read_to_end(&mut data).ok()?;
    Some(data)
}

pub struct Assets {
    scripts: HashMap<String, Vec<u8>>, // "GameUtils" -> chunk
    files: HashMap<String, Vec<u8>>,   // "data2/Login.tfs" -> contents
}

impl Assets {
    pub fn open(apk: &Path) -> Result<Self> {
        let mut apk = zip::ZipArchive::new(std::fs::File::open(apk).context("open the APK")?)?;
        let mut game = Vec::new();
        apk.by_name(GAME_ZIP).context("no assets/tap4fun.zip in the APK")?.read_to_end(&mut game)?;
        Self::from_game_zip(game)
    }

    pub fn from_game_zip(game: Vec<u8>) -> Result<Self> {
        let mut zip = zip::ZipArchive::new(Cursor::new(game))?;
        let mut files = HashMap::new();
        for i in 0..zip.len() {
            let mut entry = zip.by_index(i)?;
            let Some(name) = entry.name().strip_prefix(DATA_DIR).map(str::to_string) else { continue };
            if entry.is_dir() || name.is_empty() {
                continue;
            }
            let mut data = Vec::with_capacity(entry.size() as usize);
            entry.read_to_end(&mut data)?;
            files.insert(name, data);
        }
        let mut scripts = HashMap::new();
        if let Some(pak) = files.get(SCRIPTS_64) {
            for chunk in pak_entries(pak)? {
                if let Some(source) = chunk_source(&chunk) {
                    let base = source.rsplit('/').next().unwrap_or(&source).to_string();
                    if let Some(name) = base.strip_suffix(".lua") {
                        scripts.insert(name.to_string(), chunk);
                    }
                }
            }
        }
        Ok(Self { scripts, files })
    }

    /// A script by any of the names the game uses: "data1/GameUtils.tfl", "GameUtils.tfl", ...
    pub fn script(&self, name: &str) -> Option<&[u8]> {
        let base = name.replace('\\', "/");
        let base = base.rsplit('/').next().unwrap_or(&base);
        let base = [".tfl", ".lua", ".luac"].iter().find_map(|s| base.strip_suffix(s)).unwrap_or(base);
        self.scripts.get(base).map(Vec::as_slice)
    }

    /// Add a file the game downloaded (such as an item picture into "data2/"), as if the APK had it.
    pub fn add_file(&mut self, path: &str, data: Vec<u8>) {
        self.files.insert(path.trim_start_matches("./").to_string(), data);
    }

    /// A data file such as "data2/Login.tfs" or "data2/text/menu.en".
    pub fn file(&self, path: &str) -> Option<&[u8]> {
        self.files.get(path.trim_start_matches("./")).map(Vec::as_slice)
    }

    pub fn file_names(&self) -> impl Iterator<Item = &str> {
        self.files.keys().map(String::as_str)
    }
}
