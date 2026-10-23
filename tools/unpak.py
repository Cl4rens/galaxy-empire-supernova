"""Extract tap4fun .pak archives used by Galaxy Empire. See docs/file-formats.md.

The name table is obfuscated, so Lua chunks are named after the source path
stored in their own bytecode header. Other entries fall back to their index.

Usage: python tools/unpak.py <file.pak> [<out_dir>]
"""

import sys
from pathlib import Path

import pak

LUA_SIGNATURE = b"\x1bLuaQ"


def lua_source_name(chunk: bytes) -> str | None:
    """Return the source name from a Lua 5.1 bytecode header, e.g. './Emoji.lua'."""
    if not chunk.startswith(LUA_SIGNATURE):
        return None
    size_t = chunk[8]
    length = int.from_bytes(chunk[12 : 12 + size_t], "little")
    name = chunk[12 + size_t : 12 + size_t + length].rstrip(b"\0")
    return name.decode("utf-8", "replace").lstrip("@")


def unpak(path: Path) -> list[tuple[str, bytes]]:
    archive = pak.read(path.read_bytes())

    files = []
    for i, blob in enumerate(entry.data for entry in archive.entries):
        name = lua_source_name(blob) or f"{path.stem}_{i:04d}.bin"
        name = name.replace("\\", "/").removeprefix("./")
        if name.endswith(".lua"):
            name = name[:-4] + ".luac"
        files.append((name, blob))
    return files


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("work/unpak") / path.stem
    for name, blob in unpak(path):
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    print(f"{path.name}: extracted to {out}")


if __name__ == "__main__":
    main()
