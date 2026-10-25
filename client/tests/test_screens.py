"""Screens as standard SWF files: placeholder pictures filled in, GPU textures decoded."""

import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from supernova_client import swf, textures  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"picture"


def tag(code: int, data: bytes) -> bytes:
    return struct.pack("<H", code << 6 | 0x3F) + struct.pack("<I", len(data)) + data


def tfs() -> bytes:
    """A tiny compressed SWF like the game's: two placeholders exported under file names."""
    rect = bytes([0x08, 0x00])  # nbits = 1, all zero
    body = rect + struct.pack("<HH", 24 << 8, 1)
    body += tag(swf.DEFINE_BITS_LOSSLESS2, struct.pack("<HBHH", 1, 5, 4, 4))
    body += tag(swf.DEFINE_BITS_LOSSLESS, struct.pack("<HBHH", 2, 5, 4, 4))
    body += tag(swf.EXPORT_ASSETS, struct.pack("<H", 2) + struct.pack("<H", 1) + b"a.png\0"
                + struct.pack("<H", 2) + b"NA_slot.png\0")
    body += struct.pack("<H", 0)
    return b"CWS" + bytes([10]) + struct.pack("<I", len(body) + 8) + zlib.compress(body)


def test_placeholders_get_their_pictures():
    out, missing = swf.fill_bitmaps(tfs(), {"a.png": PNG}.get)
    assert out[:3] == b"FWS" and struct.unpack_from("<I", out, 4)[0] == len(out)
    tags = {code: data for code, _, _, data in swf._tags(out[8:], swf._header_end(out[8:]))}
    assert tags[swf.DEFINE_BITS_JPEG2] == struct.pack("<H", 1) + PNG
    assert missing == ["NA_slot.png"]  # filled by the game at run time
    assert tags[swf.DEFINE_BITS_LOSSLESS] == struct.pack("<HBHH", 2, 5, 4, 4)


def etc1_block(base: tuple[int, int, int], table: int, index: int, diff: bool = False) -> bytes:
    """One ETC1 block, every pixel with the same modifier index (individual mode)."""
    r, g, b = (c // 17 for c in base)
    hi = (r << 28 | r << 24 | g << 20 | g << 16 | b << 12 | b << 8 | table << 5 | table << 2)
    msb, lsb = index >> 1, index & 1
    lo = (0xFFFF if msb else 0) << 16 | (0xFFFF if lsb else 0)
    return struct.pack(">II", hi, lo)


def test_etc1_decoding():
    # base 136 gray, table 0 (2, 8): index 0 adds 2, 1 adds 8, 2 takes 2, 3 takes 8
    for index, expected in [(0, 138), (1, 144), (2, 134), (3, 128)]:
        pixels = textures.decode_etc1(4, 4, etc1_block((136, 136, 136), 0, index))
        assert set(pixels) == {expected}
    # 8x4: two blocks side by side
    pixels = textures.decode_etc1(8, 4, etc1_block((0, 0, 0), 7, 3) + etc1_block((255, 255, 255), 7, 1))
    assert pixels[0:3] == bytes(3) and pixels[4 * 3:5 * 3] == b"\xff\xff\xff"


def test_pvr_texture_with_alpha_to_png():
    def pvr(block: bytes) -> bytes:
        header = struct.pack("<13I", 52, 4, 4, 0, 0x10036, len(block), 4, 0, 0, 0, 0, textures.PVR_TAG, 1)
        return header + block

    from .test_engine import pak  # the pak writer of the engine tests
    alpha = pak([pvr(etc1_block((68, 68, 68), 0, 0))])
    png = textures.to_png(pvr(etc1_block((136, 0, 255), 0, 0)), alpha)
    assert png.startswith(b"\x89PNG") and textures.is_pvr(pvr(b"x" * 8))
    raw = zlib.decompress(png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8])
    assert raw[:5] == b"\x00" + bytes([138, 2, 255, 70])  # filter byte, then R G B A of the first pixel
