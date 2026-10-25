"""The game's Flash files (.tfs) as standard SWF files.

A .tfs is a normal SWF (AS2), except for its pictures: every bitmap is an empty placeholder
(DefineBitsLossless or DefineBitsLossless2 with only id, format, width and height) exported under a file name such as
"ui_main_city.png", and gameswf loads the picture from data2/ at run time. fill_bitmaps() puts
the pictures in, as DefineBitsJPEG2 tags with PNG data (allowed since SWF 8), so any SWF player
(Ruffle) can show the screen.
"""

import struct
import zlib

DEFINE_BITS_LOSSLESS, DEFINE_BITS_JPEG2, DEFINE_BITS_LOSSLESS2, EXPORT_ASSETS, END = 20, 21, 36, 56, 0
PLACEHOLDERS = (DEFINE_BITS_LOSSLESS, DEFINE_BITS_LOSSLESS2)


def _tags(body: bytes, pos: int):
    """(code, start, header length, data) for each tag, from `pos` on."""
    while pos < len(body):
        start = pos
        code_len = struct.unpack_from("<H", body, pos)[0]
        pos += 2
        code, length = code_len >> 6, code_len & 0x3F
        if length == 0x3F:
            length = struct.unpack_from("<I", body, pos)[0]
            pos += 4
        yield code, start, pos - start, body[pos:pos + length]
        pos += length
        if code == END:
            return


def _tag(code: int, data: bytes) -> bytes:
    if len(data) < 0x3F:
        return struct.pack("<H", code << 6 | len(data)) + data
    return struct.pack("<HI", code << 6 | 0x3F, len(data)) + data


def _header_end(body: bytes) -> int:
    """Offset of the first tag in the uncompressed body (after RECT, frame rate and count)."""
    nbits = body[0] >> 3
    rect_bytes = (5 + 4 * nbits + 7) // 8
    return rect_bytes + 4


def exported_names(data: bytes) -> dict[int, str]:
    names = {}
    count = struct.unpack_from("<H", data, 0)[0]
    pos = 2
    for _ in range(count):
        char_id = struct.unpack_from("<H", data, pos)[0]
        end = data.index(b"\0", pos + 2)
        names[char_id] = data[pos + 2:end].decode("utf-8", "replace")
        pos = end + 1
    return names


def fill_bitmaps(swf: bytes, read_file) -> tuple[bytes, list[str]]:
    """Return the SWF (uncompressed, "FWS") with the placeholder pictures filled from
    read_file(name) -> bytes | None, and the names that were not found."""
    signature, version = swf[:3], swf[3]
    body = zlib.decompress(swf[8:]) if signature == b"CWS" else swf[8:]
    if signature not in (b"CWS", b"FWS"):
        raise ValueError(f"unsupported SWF signature {signature!r}")
    first = _header_end(body)
    tags = list(_tags(body, first))
    names: dict[int, str] = {}
    for code, _, _, data in tags:
        if code == EXPORT_ASSETS:
            names.update(exported_names(data))
    out = [body[:first]]
    missing = []
    for code, start, head, data in tags:
        if code in PLACEHOLDERS and len(data) == 7:  # a placeholder: id, format, width, height
            char_id = struct.unpack_from("<H", data, 0)[0]
            picture = read_file(names.get(char_id, "")) if char_id in names else None
            # the files are PNG or JPEG whatever their name says; DefineBitsJPEG2 takes both
            if picture is not None and (picture[:8] == b"\x89PNG\r\n\x1a\n" or picture[:2] == b"\xff\xd8"):
                out.append(_tag(DEFINE_BITS_JPEG2, struct.pack("<H", char_id) + picture))
                continue
            missing.append(names.get(char_id, f"#{char_id}"))
        out.append(body[start:start + head + len(data)])
    new_body = b"".join(out)
    return b"FWS" + bytes([max(version, 8)]) + struct.pack("<I", len(new_body) + 8) + new_body, missing
