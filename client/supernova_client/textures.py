"""The game's GPU textures as PNG.

The big picture atlases in data2/ (main_city_res.png, ...) are not PNG files but PVR v2
textures (52-byte header ending in "PVR!") in ETC1 format, 4 bits per pixel. Their alpha
channel is a second ETC1 texture in <name>_alpha.pak (a tap4fun pak with one entry), stored as
gray. to_png() decodes both into an RGBA PNG.
"""

import struct
import zlib

from .assets import _pak_entries

PVR_TAG = 0x21525650  # "PVR!"
PVR_ETC1 = 0x36

# ETC1 modifier tables (OES_compressed_ETC1_RGB8_texture)
_MODIFIERS = [(2, 8), (5, 17), (9, 29), (13, 42), (18, 60), (24, 80), (33, 106), (47, 183)]


def is_pvr(data: bytes) -> bool:
    return len(data) >= 52 and struct.unpack_from("<I", data, 44)[0] == PVR_TAG


def _pvr_etc1(data: bytes) -> tuple[int, int, bytes]:
    header_len, height, width, _, flags, data_len = struct.unpack_from("<6I", data, 0)
    if flags & 0xFF != PVR_ETC1:
        raise ValueError(f"PVR pixel format {flags & 0xFF:#x} is not ETC1")
    return width, height, data[header_len:header_len + data_len]


def _clamp(v: int) -> int:
    return 0 if v < 0 else 255 if v > 255 else v


def decode_etc1(width: int, height: int, blocks: bytes) -> bytearray:
    """RGB pixels (3 bytes each, rows top to bottom) of an ETC1 image."""
    out = bytearray(width * height * 3)
    bw = (width + 3) // 4
    for b in range((len(blocks)) // 8):
        hi, lo = struct.unpack_from(">II", blocks, b * 8)
        bx, by = (b % bw) * 4, (b // bw) * 4
        flip, diff = hi & 1, (hi >> 1) & 1
        if diff:
            r1, g1, b1 = hi >> 27, (hi >> 19) & 31, (hi >> 11) & 31
            dr, dg, db = (hi >> 24) & 7, (hi >> 16) & 7, (hi >> 8) & 7
            r2 = r1 + (dr - 8 if dr > 3 else dr)
            g2 = g1 + (dg - 8 if dg > 3 else dg)
            b2 = b1 + (db - 8 if db > 3 else db)
            base = [((r1 << 3) | (r1 >> 2), (g1 << 3) | (g1 >> 2), (b1 << 3) | (b1 >> 2)),
                    (((r2 & 31) << 3) | ((r2 & 31) >> 2), ((g2 & 31) << 3) | ((g2 & 31) >> 2),
                     ((b2 & 31) << 3) | ((b2 & 31) >> 2))]
        else:
            base = [(((hi >> 28) & 15) * 17, ((hi >> 20) & 15) * 17, ((hi >> 12) & 15) * 17),
                    (((hi >> 24) & 15) * 17, ((hi >> 16) & 15) * 17, ((hi >> 8) & 15) * 17)]
        tables = (_MODIFIERS[(hi >> 5) & 7], _MODIFIERS[(hi >> 2) & 7])
        for i in range(16):
            x, y = i >> 2, i & 3  # pixel indices run down the columns
            px, py = bx + x, by + y
            if px >= width or py >= height:
                continue
            sub = (y >= 2) if flip else (x >= 2)
            a, bb = tables[sub]
            msb, lsb = (lo >> (16 + i)) & 1, (lo >> i) & 1
            mod = (a, bb, -a, -bb)[(msb << 1) | lsb]
            r, g, bl = base[sub]
            o = (py * width + px) * 3
            out[o] = _clamp(r + mod)
            out[o + 1] = _clamp(g + mod)
            out[o + 2] = _clamp(bl + mod)
    return out


def png(width: int, height: int, rgba: bytes) -> bytes:
    """A minimal RGBA PNG (no filtering)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    stride = width * 4
    raw = b"".join(b"\0" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def to_png(texture: bytes, alpha_pak: bytes | None = None) -> bytes:
    width, height, blocks = _pvr_etc1(texture)
    rgb = decode_etc1(width, height, blocks)
    alpha = None
    if alpha_pak is not None:
        aw, ah, ablocks = _pvr_etc1(_pak_entries(alpha_pak)[0])
        if (aw, ah) == (width, height):
            alpha = decode_etc1(aw, ah, ablocks)
    rgba = bytearray(width * height * 4)
    rgba[0::4], rgba[1::4], rgba[2::4] = rgb[0::3], rgb[1::3], rgb[2::3]
    rgba[3::4] = alpha[0::3] if alpha is not None else b"\xff" * (width * height)
    return png(width, height, bytes(rgba))
