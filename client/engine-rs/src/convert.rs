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
const PLACE_OBJECT2: u16 = 26;
const DEFINE_SPRITE: u16 = 39;
const PLACE_OBJECT3: u16 = 70;
/// Text fields with an instance name like this get the game's text of that key (gameswf did it)
const TEXT_KEY_PREFIX: &str = "LC_";
/// Layout marks in such names, not part of the key: single line / multiple lines, cut with "..."
/// where the text leaves the field, at run time ("LC_MENU_SLHM_TAB_OFFICER" shows
/// "LC_MENU_TAB_OFFICER")
const TEXT_LAYOUT_MARKS: [&str; 2] = ["SLHM_", "MLHM_"];

/// Other single-line fields shrink their text to fit, down to this much of its size.
const MIN_TEXT_SCALE: f64 = 0.5;

/// The game's text for a text field named `name`.
fn field_text(name: &str, text: &impl Fn(&str) -> Option<String>) -> Option<String> {
    text(name).or_else(|| {
        let key = TEXT_LAYOUT_MARKS.iter().fold(name.to_string(), |key, mark| key.replacen(mark, "", 1));
        (key != name).then(|| text(&key)).flatten()
    })
}
const PVR_TAG: u32 = 0x2152_5650; // "PVR!"
/// The font gameswf drew text fields with
pub const FONT: &str = "data2/Arial.ttf";
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

/// Reads bits of a SWF record, highest first.
struct Bits<'a> {
    data: &'a [u8],
    bit: usize,
}

impl Bits<'_> {
    fn ub(&mut self, n: usize) -> u32 {
        let mut v = 0;
        for _ in 0..n {
            let byte = self.data.get(self.bit / 8).copied().unwrap_or(0);
            v = (v << 1) | ((byte >> (7 - self.bit % 8)) & 1) as u32;
            self.bit += 1;
        }
        v
    }

    /// Bytes used, up to the next whole byte.
    fn len(&self) -> usize {
        self.bit.div_ceil(8)
    }
}

fn matrix_len(data: &[u8]) -> usize {
    let mut bits = Bits { data, bit: 0 };
    for _ in 0..2 {
        if bits.ub(1) == 1 {
            let n = bits.ub(5) as usize;
            bits.ub(2 * n);
        }
    }
    let n = bits.ub(5) as usize;
    bits.ub(2 * n);
    bits.len()
}

fn color_transform_len(data: &[u8]) -> usize {
    let mut bits = Bits { data, bit: 0 };
    let (add, mult) = (bits.ub(1), bits.ub(1));
    let n = bits.ub(4) as usize;
    bits.ub(4 * n * (add + mult) as usize);
    bits.len()
}

/// The instance name each character is placed with (PlaceObject2/3, in sprites too).
fn instance_names(body: &[u8], all: &[(u16, usize, usize, std::ops::Range<usize>)], out: &mut HashMap<u16, String>) {
    for (code, _, _, range) in all {
        let d = &body[range.clone()];
        match *code {
            DEFINE_SPRITE if d.len() > 4 => instance_names(d, &tags(d, 4), out),
            PLACE_OBJECT2 | PLACE_OBJECT3 if d.len() > 3 => {
                let flags = d[0];
                let mut p = if *code == PLACE_OBJECT3 { 2 } else { 1 } + 2;
                if *code == PLACE_OBJECT3 && d[1] & 0x08 != 0 {
                    p += d[p..].iter().position(|&b| b == 0).map_or(d.len(), |e| e + 1); // class name
                }
                let id = if flags & 0x02 != 0 && p + 2 <= d.len() {
                    p += 2;
                    Some(u16_at(d, p - 2))
                } else {
                    None
                };
                if flags & 0x04 != 0 {
                    p += matrix_len(&d[p.min(d.len())..]);
                }
                if flags & 0x08 != 0 {
                    p += color_transform_len(&d[p.min(d.len())..]);
                }
                if flags & 0x10 != 0 {
                    p += 2; // ratio
                }
                if let (Some(id), true) = (id, flags & 0x20 != 0 && p < d.len()) {
                    let end = d[p..].iter().position(|&b| b == 0).map_or(d.len(), |e| p + e);
                    out.entry(id).or_insert_with(|| String::from_utf8_lossy(&d[p..end]).to_string());
                }
            }
            _ => {}
        }
    }
}

/// `html` with its text replaced by `text`, keeping the tags (alignment, font, size).
fn replace_html_text(html: &str, text: &str) -> String {
    let escaped = text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
    let mut out = String::new();
    let mut placed = false;
    let mut rest = html;
    while !rest.is_empty() {
        if rest.starts_with('<') {
            let end = rest.find('>').map_or(rest.len(), |e| e + 1);
            out.push_str(&rest[..end]);
            rest = &rest[end..];
        } else {
            let end = rest.find('<').unwrap_or(rest.len());
            if !placed && !rest[..end].trim().is_empty() {
                out.push_str(&escaped);
                placed = true;
            }
            rest = &rest[end..];
        }
    }
    if !placed {
        out.push_str(&escaped);
    }
    out
}

/// Width of `text` in pixels at font size `size`.
fn text_width(font: &ttf_parser::Face, text: &str, size: f64) -> f64 {
    let units: f64 = text
        .chars()
        .map(|c| font.glyph_index(c).and_then(|g| font.glyph_hor_advance(g)).unwrap_or(0) as f64)
        .sum();
    units * size / font.units_per_em() as f64
}

/// The font size (pixels) that fits `text` into `width` pixels on one line, if it needs one.
fn fitting_size(font: &ttf_parser::Face, text: &str, size: f64, width: f64) -> Option<f64> {
    let room = width - 4.0;
    let needed = text_width(font, text, size);
    // a field that even half the size does not fit is only an anchor: its text was meant to stick
    // out (the city's building labels), as with gameswf
    (needed > room && needed > 0.0 && room / needed >= MIN_TEXT_SCALE).then(|| (size * room / needed).floor())
}

/// Where a DefineEditText keeps what the converter changes.
struct TextField {
    width: f64,
    flags_at: usize,
    /// the font height, when the field has a font
    height_at: Option<usize>,
    /// where the initial text starts (or would start)
    text_at: usize,
    html: bool,
    multiline: bool,
    text: String,
}

fn text_field(data: &[u8]) -> Option<TextField> {
    let nbits = (data.get(2)? >> 3) as usize;
    let mut bounds = Bits { data: data.get(2..)?, bit: 5 };
    let (x_min, x_max) = (bounds.ub(nbits), bounds.ub(nbits));
    let signed = |v: u32| if nbits > 0 && v >> (nbits - 1) & 1 == 1 { v as i64 - (1i64 << nbits) } else { v as i64 };
    let width = (signed(x_max) - signed(x_min)) as f64 / 20.0;
    let flags_at = 2 + (5 + 4 * nbits).div_ceil(8);
    let (f1, f2) = (*data.get(flags_at)?, *data.get(flags_at + 1)?);
    let mut p = flags_at + 2;
    let height_at = (f1 & 0x01 != 0).then_some(p + 2);
    if f1 & 0x01 != 0 {
        p += 4; // font id, height
    }
    if f2 & 0x80 != 0 {
        p += data.get(p..)?.iter().position(|&b| b == 0)? + 1; // font class
    }
    if f1 & 0x04 != 0 {
        p += 4; // color
    }
    if f1 & 0x02 != 0 {
        p += 2; // max length
    }
    if f2 & 0x20 != 0 {
        p += 9; // layout
    }
    p += data.get(p..)?.iter().position(|&b| b == 0)? + 1; // variable name
    let text = if f1 & 0x80 != 0 {
        let end = data.get(p..)?.iter().position(|&b| b == 0).map_or(data.len(), |e| p + e);
        String::from_utf8_lossy(&data[p..end]).to_string()
    } else {
        String::new()
    };
    Some(TextField { width, flags_at, height_at, text_at: p, html: f2 & 0x02 != 0, multiline: f1 & 0x20 != 0, text })
}

/// The field with `text` as its initial text (and font height changes in `out`).
fn with_text(data: &[u8], field: &TextField, text: &str, height: Option<u16>) -> Vec<u8> {
    let mut out = data[..field.text_at].to_vec();
    if let (Some(at), Some(height)) = (field.height_at, height) {
        out[at..at + 2].copy_from_slice(&height.to_le_bytes());
    }
    out[field.flags_at] |= 0x80; // HasText
    out.extend(text.as_bytes());
    out.push(0);
    out
}

/// `html` without letter spacing: gameswf left it out, and the screens were laid out that way.
fn without_letter_spacing(html: &str) -> String {
    let mut out = String::with_capacity(html.len());
    let mut rest = html;
    while let Some(at) = rest.find("letterSpacing=\"") {
        let value = at + "letterSpacing=\"".len();
        out.push_str(&rest[..value]);
        out.push('0');
        let end = rest[value..].find('"').map_or(rest.len(), |e| value + e);
        rest = &rest[end..];
    }
    out.push_str(rest);
    out
}

/// A DefineEditText the way gameswf showed it: its HTML without letter spacing.
fn gameswf_text(data: &[u8]) -> Option<Vec<u8>> {
    let field = text_field(data)?;
    let plain = without_letter_spacing(&field.text);
    (field.html && plain != field.text).then(|| with_text(data, &field, &plain, None))
}

/// A DefineEditText with `text` as its initial text; with `font`, a smaller font size where the
/// text would not fit on one line and the field has only one (and is not `marked` with a layout
/// mark: those are cut at run time).
fn with_initial_text(data: &[u8], text: &str, font: Option<&ttf_parser::Face>, marked: bool) -> Option<Vec<u8>> {
    let field = text_field(data)?;
    let fit = font.filter(|_| !marked && !field.multiline);
    if field.html {
        let mut html = replace_html_text(&field.text, text);
        if let Some(font) = fit {
            // the size of the first <font size="..."> sets the line
            if let Some(start) = html.find("size=\"").map(|i| i + 6) {
                let end = start + html[start..].find('"').unwrap_or(0);
                if let Some(size) = html[start..end].parse::<f64>().ok().and_then(|size| fitting_size(font, text, size, field.width)) {
                    html.replace_range(start..end, &size.to_string());
                }
            }
        }
        Some(with_text(data, &field, &html, None))
    } else {
        let height = match (fit, field.height_at) {
            (Some(font), Some(at)) => fitting_size(font, text, u16_at(data, at) as f64 / 20.0, field.width).map(|s| (s * 20.0) as u16),
            _ => None,
        };
        Some(with_text(data, &field, text, height))
    }
}

/// The SWF (uncompressed) with every placeholder picture filled by `picture(name)`, every text
/// field set to the device font, and the text fields named after a game text (`LC_...`) filled
/// by `text(key)`; single-line ones shrink the text to fit, measured with `font`.
pub fn fill_bitmaps(
    swf: &[u8],
    mut picture: impl FnMut(&str) -> Option<Vec<u8>>,
    text: impl Fn(&str) -> Option<String>,
    font: Option<&[u8]>,
) -> Result<Vec<u8>> {
    let font = font.and_then(|f| ttf_parser::Face::parse(f, 0).ok());
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
    let mut instances = HashMap::new();
    instance_names(&body, &all, &mut instances);
    let mut out = body[..first].to_vec();
    for (code, start, head, range) in &all {
        if (*code == DEFINE_BITS_LOSSLESS || *code == DEFINE_BITS_LOSSLESS2) && range.len() == 7 {
            let id = u16_at(&body, range.start);
            let data = names
                .get(&id)
                .and_then(|n| picture(n))
                .filter(|d| d.starts_with(b"\x89PNG\r\n\x1a\n") || d.starts_with(b"\xff\xd8"));
            // pictures the game puts in at run time (ReplaceTexture) start transparent
            let data = match data {
                Some(d) => d,
                None => transparent_png(u16_at(&body, range.start + 3).into(), u16_at(&body, range.start + 5).into())?,
            };
            let mut content = id.to_le_bytes().to_vec();
            content.extend(&data);
            out.extend(tag(DEFINE_BITS_JPEG2, &content));
            continue;
        }
        if *code == DEFINE_EDIT_TEXT {
            let mut field = device_font_text(&body[range.clone()]);
            if let Some(plain) = gameswf_text(&field) {
                field = plain;
            }
            let key = instances.get(&u16_at(&body, range.start)).filter(|n| n.starts_with(TEXT_KEY_PREFIX));
            let marked = key.is_some_and(|k| TEXT_LAYOUT_MARKS.iter().any(|m| k.contains(m)));
            if let Some(localized) =
                key.and_then(|k| field_text(k, &text)).and_then(|t| with_initial_text(&field, &t, font.as_ref(), marked))
            {
                field = localized;
            }
            out.extend(tag(DEFINE_EDIT_TEXT, &field));
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

fn transparent_png(width: u32, height: u32) -> Result<Vec<u8>> {
    let (width, height) = (width.max(1), height.max(1));
    let mut png = Vec::new();
    image::codecs::png::PngEncoder::new(&mut png).write_image(
        &vec![0; (width * height * 4) as usize],
        width,
        height,
        image::ExtendedColorType::Rgba8,
    )?;
    Ok(png)
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
    pictures: Recent,
    screens: Recent,
}

impl Default for Converter {
    fn default() -> Self {
        Self { pictures: Recent::new(CACHE_BYTES), screens: Recent::new(CACHE_BYTES) }
    }
}

/// How much each of the converter's caches keeps
const CACHE_BYTES: usize = 24 << 20;

/// The files used last, up to a number of bytes.
struct Recent {
    files: Vec<(String, Vec<u8>)>,
    limit: usize,
}

impl Recent {
    fn new(limit: usize) -> Self {
        Self { files: Vec::new(), limit }
    }

    fn get(&mut self, name: &str) -> Option<Vec<u8>> {
        let i = self.files.iter().position(|(n, _)| n == name)?;
        let file = self.files.remove(i);
        let data = file.1.clone();
        self.files.push(file);
        Some(data)
    }

    fn insert(&mut self, name: &str, data: Vec<u8>) {
        self.files.retain(|(n, _)| n != name);
        self.files.push((name.to_string(), data));
        while self.files.len() > 1 && self.bytes() > self.limit {
            self.files.remove(0);
        }
    }

    fn bytes(&self) -> usize {
        self.files.iter().map(|(_, d)| d.len()).sum()
    }
}

/// A part of an atlas picture as a square PNG: "ShipCityTech_1.png#316,8,568,182" names the
/// rectangle (left, top, right, bottom) of a ship, for item icons the game has no picture of.
fn atlas_part(atlas: &[u8], rect: &str) -> Option<Vec<u8>> {
    let n: Vec<u32> = rect.split(',').map(|v| v.trim().parse().ok()).collect::<Option<_>>()?;
    let [left, top, right, bottom] = n[..] else { return None };
    let image = image::load_from_memory(atlas).ok()?.to_rgba8();
    if right <= left || bottom <= top || right > image.width() || bottom > image.height() {
        return None;
    }
    let part = image::imageops::crop_imm(&image, left, top, right - left, bottom - top).to_image();
    let side = part.width().max(part.height());
    let mut square = image::RgbaImage::new(side, side);
    image::imageops::overlay(&mut square, &part, ((side - part.width()) / 2) as i64, ((side - part.height()) / 2) as i64);
    let mut out = std::io::Cursor::new(Vec::new());
    square.write_to(&mut out, image::ImageFormat::Png).ok()?;
    Some(out.into_inner())
}

impl Converter {
    /// Bytes kept: (pictures, converted screens)
    pub fn cached_bytes(&self) -> (usize, usize) {
        (self.pictures.bytes(), self.screens.bytes())
    }

    pub fn picture(&mut self, assets: &Assets, name: &str) -> Option<Vec<u8>> {
        if let Some(found) = self.pictures.get(name) {
            return Some(found);
        }
        if let Some((atlas, rect)) = name.split_once('#') {
            let data = atlas_part(&self.picture(assets, atlas)?, rect)?;
            self.pictures.insert(name, data.clone());
            return Some(data);
        }
        let mut data = assets.file(&format!("data2/{name}")).map(<[u8]>::to_vec);
        if let Some(d) = &data
            && is_pvr(d)
        {
            let alpha_name = format!("data2/{}_alpha.pak", name.trim_end_matches(".png"));
            data = texture_to_png(d, assets.file(&alpha_name)).ok();
        }
        // a picture the game has not got may still come (downloaded item icons)
        if let Some(data) = &data {
            self.pictures.insert(name, data.clone());
        }
        data
    }

    /// A screen ("data2/Login.tfs") as a standard SWF, with the game texts in `texts`. Recent
    /// ones are kept for the next time once the game has loaded its texts.
    pub fn screen(&mut self, assets: &Assets, texts: &HashMap<String, String>, path: &str) -> Result<Vec<u8>> {
        if let Some(swf) = self.screens.get(path) {
            return Ok(swf);
        }
        let Some(tfs) = assets.file(path.trim_start_matches("./")) else { bail!("no screen {path}") };
        let tfs = tfs.to_vec();
        let font = assets.file(FONT).map(<[u8]>::to_vec);
        let swf = fill_bitmaps(&tfs, |name| self.picture(assets, name), |key| texts.get(key).cloned(), font.as_deref())?;
        if !texts.is_empty() {
            self.screens.insert(path, swf.clone());
        }
        Ok(swf)
    }
}
