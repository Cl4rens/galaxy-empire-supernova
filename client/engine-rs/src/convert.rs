//! The game's screens (.tfs) as standard SWF files with their pictures; a port of
//! client/supernova_client/swf.py and textures.py (see there for the formats).

use std::collections::HashMap;
use std::io::Read;

use image::ImageEncoder;

use anyhow::{Result, bail};

use crate::assets::{Assets, pak_entries};

const DEFINE_BITS_LOSSLESS: u16 = 20;
const DEFINE_BITS_JPEG2: u16 = 21;
const DEFINE_EDIT_TEXT: u16 = 37;
const DEFINE_BITS_LOSSLESS2: u16 = 36;
const EXPORT_ASSETS: u16 = 56;
const PVR_TAG: u32 = 0x2152_5650; // "PVR!"
const MODIFIERS: [(i32, i32); 8] = [(2, 8), (5, 17), (9, 29), (13, 42), (18, 60), (24, 80), (33, 106), (47, 183)];

fn u16_at(d: &[u8], p: usize) -> u16 {
    u16::from_le_bytes([d[p], d[p + 1]])
}

fn u32_at(d: &[u8], p: usize) -> u32 {
    u32::from_le_bytes(d[p..p + 4].try_into().unwrap())
}

/// (code, start, header length, data range) of every tag from `pos` on.
fn tags(body: &[u8], mut pos: usize) -> Vec<(u16, usize, usize, std::ops::Range<usize>)> {
    let mut out = Vec::new();
    while pos + 2 <= body.len() {
        let start = pos;
        let code_len = u16_at(body, pos);
        pos += 2;
        let (code, mut len) = (code_len >> 6, (code_len & 0x3F) as usize);
        if len == 0x3F {
            len = u32_at(body, pos) as usize;
            pos += 4;
        }
        out.push((code, start, pos - start, pos..pos + len));
        pos += len;
        if code == 0 {
            break;
        }
    }
    out
}

fn tag(code: u16, data: &[u8]) -> Vec<u8> {
    let mut out = ((code << 6) | 0x3F).to_le_bytes().to_vec();
    out.extend((data.len() as u32).to_le_bytes());
    out.extend(data);
    out
}

/// A DefineEditText that draws with the device font: the screens embed only the glyphs their
/// sample texts use, while gameswf drew every text field with data2/Arial.ttf.
fn device_font_text(data: &[u8]) -> Vec<u8> {
    let mut out = data.to_vec();
    let nbits = (data[2] >> 3) as usize;
    let flags = 2 + (5 + 4 * nbits).div_ceil(8) + 1;
    if let Some(f) = out.get_mut(flags) {
        *f &= !0x01; // UseOutlines
    }
    out
}

/// The SWF (uncompressed) with every placeholder picture filled by `picture(name)` and every
/// text field set to the device font.
pub fn fill_bitmaps(swf: &[u8], mut picture: impl FnMut(&str) -> Option<Vec<u8>>) -> Result<Vec<u8>> {
    let body = match &swf[..3] {
        b"CWS" => {
            let mut out = Vec::new();
            flate2::read::ZlibDecoder::new(&swf[8..]).read_to_end(&mut out)?;
            out
        }
        b"FWS" => swf[8..].to_vec(),
        other => bail!("unsupported SWF signature {other:?}"),
    };
    let nbits = (body[0] >> 3) as usize;
    let first = (5 + 4 * nbits).div_ceil(8) + 4;
    let all = tags(&body, first);
    let mut names = HashMap::new();
    for (code, _, _, range) in &all {
        if *code == EXPORT_ASSETS {
            let d = &body[range.clone()];
            let mut p = 2;
            for _ in 0..u16_at(d, 0) {
                let id = u16_at(d, p);
                let end = d[p + 2..].iter().position(|&b| b == 0).map(|e| p + 2 + e).unwrap_or(d.len());
                names.insert(id, String::from_utf8_lossy(&d[p + 2..end]).to_string());
                p = end + 1;
            }
        }
    }
    let mut out = body[..first].to_vec();
    for (code, start, head, range) in &all {
        if (*code == DEFINE_BITS_LOSSLESS || *code == DEFINE_BITS_LOSSLESS2) && range.len() == 7 {
            let id = u16_at(&body, range.start);
            if let Some(data) = names.get(&id).and_then(|n| picture(n)) {
                if data.starts_with(b"\x89PNG\r\n\x1a\n") || data.starts_with(b"\xff\xd8") {
                    let mut content = id.to_le_bytes().to_vec();
                    content.extend(&data);
                    out.extend(tag(DEFINE_BITS_JPEG2, &content));
                    continue;
                }
            }
        }
        if *code == DEFINE_EDIT_TEXT {
            out.extend(tag(DEFINE_EDIT_TEXT, &device_font_text(&body[range.clone()])));
            continue;
        }
        out.extend(&body[*start..*start + head + range.len()]);
    }
    let mut swf_out = b"FWS".to_vec();
    swf_out.push(swf[3].max(8));
    swf_out.extend(((out.len() + 8) as u32).to_le_bytes());
    swf_out.extend(out);
    Ok(swf_out)
}

pub fn is_pvr(data: &[u8]) -> bool {
    data.len() >= 52 && u32_at(data, 44) == PVR_TAG
}

fn pvr_etc1(data: &[u8]) -> Result<(usize, usize, &[u8])> {
    let (header, height, width, flags, len) =
        (u32_at(data, 0) as usize, u32_at(data, 4) as usize, u32_at(data, 8) as usize, u32_at(data, 16), u32_at(data, 20) as usize);
    if flags & 0xFF != 0x36 {
        bail!("PVR format {:#x} is not ETC1", flags & 0xFF);
    }
    Ok((width, height, &data[header..header + len]))
}

/// RGB pixels of an ETC1 image, rows top to bottom.
pub fn decode_etc1(width: usize, height: usize, blocks: &[u8]) -> Vec<u8> {
    let mut out = vec![0u8; width * height * 3];
    let bw = width.div_ceil(4);
    let ext5 = |v: i32| ((v << 3) | (v >> 2)) as u8;
    for (b, block) in blocks.chunks_exact(8).enumerate() {
        let hi = u32::from_be_bytes(block[..4].try_into().unwrap());
        let lo = u32::from_be_bytes(block[4..].try_into().unwrap());
        let (bx, by) = ((b % bw) * 4, (b / bw) * 4);
        let (flip, diff) = (hi & 1, (hi >> 1) & 1);
        let base: [[u8; 3]; 2] = if diff != 0 {
            let c1 = [(hi >> 27) as i32, ((hi >> 19) & 31) as i32, ((hi >> 11) & 31) as i32];
            let d = [((hi >> 24) & 7) as i32, ((hi >> 16) & 7) as i32, ((hi >> 8) & 7) as i32];
            let c2: Vec<i32> = (0..3).map(|i| (c1[i] + if d[i] > 3 { d[i] - 8 } else { d[i] }) & 31).collect();
            [[ext5(c1[0]), ext5(c1[1]), ext5(c1[2])], [ext5(c2[0]), ext5(c2[1]), ext5(c2[2])]]
        } else {
            let n = |s: u32| ((hi >> s) & 15) as u8 * 17;
            [[n(28), n(20), n(12)], [n(24), n(16), n(8)]]
        };
        let tables = [MODIFIERS[((hi >> 5) & 7) as usize], MODIFIERS[((hi >> 2) & 7) as usize]];
        for i in 0..16 {
            let (x, y) = (i >> 2, i & 3);
            let (px, py) = (bx + x, by + y);
            if px >= width || py >= height {
                continue;
            }
            let sub = if flip != 0 { (y >= 2) as usize } else { (x >= 2) as usize };
            let (a, bb) = tables[sub];
            let index = (((lo >> (16 + i)) & 1) << 1) | ((lo >> i) & 1);
            let m = [a, bb, -a, -bb][index as usize];
            let o = (py * width + px) * 3;
            for c in 0..3 {
                out[o + c] = (base[sub][c] as i32 + m).clamp(0, 255) as u8;
            }
        }
    }
    out
}

/// A PVR/ETC1 texture (with its alpha pak) as PNG.
pub fn texture_to_png(texture: &[u8], alpha_pak: Option<&[u8]>) -> Result<Vec<u8>> {
    let (w, h, blocks) = pvr_etc1(texture)?;
    let rgb = decode_etc1(w, h, blocks);
    let alpha = match alpha_pak {
        Some(pak) => {
            let entry = pak_entries(pak)?.into_iter().next();
            match entry {
                Some(e) => {
                    let (aw, ah, ab) = pvr_etc1(&e)?;
                    if (aw, ah) == (w, h) { Some(decode_etc1(aw, ah, ab)) } else { None }
                }
                None => None,
            }
        }
        None => None,
    };
    let mut rgba = Vec::with_capacity(w * h * 4);
    for i in 0..w * h {
        rgba.extend(&rgb[i * 3..i * 3 + 3]);
        rgba.push(alpha.as_ref().map(|a| a[i * 3]).unwrap_or(255));
    }
    let mut png = Vec::new();
    image::codecs::png::PngEncoder::new(&mut png).write_image(&rgba, w as u32, h as u32, image::ExtendedColorType::Rgba8)?;
    Ok(png)
}

/// Converted screens and decoded pictures, made once per run.
pub struct Converter {
    pictures: HashMap<String, Option<Vec<u8>>>,
    screens: HashMap<String, Vec<u8>>,
}

impl Default for Converter {
    fn default() -> Self {
        Self { pictures: HashMap::new(), screens: HashMap::new() }
    }
}

impl Converter {
    pub fn picture(&mut self, assets: &Assets, name: &str) -> Option<Vec<u8>> {
        if let Some(found) = self.pictures.get(name) {
            return found.clone();
        }
        let mut data = assets.file(&format!("data2/{name}")).map(<[u8]>::to_vec);
        if let Some(d) = &data
            && is_pvr(d)
        {
            let alpha_name = format!("data2/{}_alpha.pak", name.trim_end_matches(".png"));
            data = texture_to_png(d, assets.file(&alpha_name)).ok();
        }
        self.pictures.insert(name.to_string(), data.clone());
        data
    }

    /// A screen ("data2/Login.tfs") as a standard SWF.
    pub fn screen(&mut self, assets: &Assets, path: &str) -> Result<Vec<u8>> {
        if let Some(swf) = self.screens.get(path) {
            return Ok(swf.clone());
        }
        let Some(tfs) = assets.file(path.trim_start_matches("./")) else { bail!("no screen {path}") };
        let tfs = tfs.to_vec();
        let swf = fill_bitmaps(&tfs, |name| self.picture(assets, name))?;
        self.screens.insert(path.to_string(), swf.clone());
        Ok(swf)
    }
}
