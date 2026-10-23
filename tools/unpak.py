"""Extract tap4fun .pak archives (magic AB E2 7F 9D) used by Galaxy Empire.

Layout (all integers little-endian u32):

    header   magic, first_data, names_off, count, init_off, init_size
    index    count * (name_off, flags, data_off, data_size)    at 0x18
    names    encrypted, NUL-separated name table                 at names_off
    init     u32 raw_size + zlib stream (an extra entry)         at init_off
    data     per entry: u32 raw_size + zlib stream               at data_off

The name table is obfuscated, so Lua chunks are named after the source path
stored in their own bytecode header. Other entries fall back to their index.

Usage: python tools/unpak.py <file.pak> [<out_dir>]
"""

import struct
import sys
import zlib
from pathlib import Path

MAGIC = 0x9D7FE2AB
LUA_SIGNATURE = b"\x1bLuaQ"


def read_blob(data: bytes, offset: int, size: int) -> bytes:
    raw_size = struct.unpack_from("<I", data, offset)[0]
    payload = data[offset + 4 : offset + size]
    try:
        raw = zlib.decompress(payload)
    except zlib.error:
        raw = payload
    if len(raw) != raw_size:
        raise ValueError(f"size mismatch at {offset:#x}: {len(raw)} != {raw_size}")
    return raw


def lua_source_name(chunk: bytes) -> str | None:
    """Return the source name from a Lua 5.1 bytecode header, e.g. './Emoji.lua'."""
    if not chunk.startswith(LUA_SIGNATURE):
        return None
    size_t = chunk[8]
    length = int.from_bytes(chunk[12 : 12 + size_t], "little")
    name = chunk[12 + size_t : 12 + size_t + length].rstrip(b"\0")
    return name.decode("utf-8", "replace").lstrip("@")


def unpak(pak: Path) -> list[tuple[str, bytes]]:
    data = pak.read_bytes()
    magic, _first, names_off, _count, init_off, init_size = struct.unpack_from("<6I", data, 0)
    if magic != MAGIC:
        raise ValueError(f"{pak}: not a tap4fun pak (magic {magic:#010x})")

    blobs = [read_blob(data, init_off, init_size)]
    # The header count includes the init entry, which has no index slot.
    for i in range((names_off - 0x18) // 16):
        _name_off, _flags, off, size = struct.unpack_from("<4I", data, 0x18 + i * 16)
        blobs.append(read_blob(data, off, size))

    files = []
    for i, blob in enumerate(blobs):
        name = lua_source_name(blob) or f"{pak.stem}_{i:04d}.bin"
        name = name.replace("\\", "/").removeprefix("./")
        if name.endswith(".lua"):
            name = name[:-4] + ".luac"
        files.append((name, blob))
    return files


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    pak = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("work/unpak") / pak.stem
    for name, blob in unpak(pak):
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    print(f"{pak.name}: extracted to {out}")


if __name__ == "__main__":
    main()
