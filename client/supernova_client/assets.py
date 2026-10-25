"""The game files inside the player's own APK: Lua scripts (data1_64.pak) and data2 files.

Nothing from the APK is copied into this repository; everything is read at run time.
"""

import io
import struct
import zipfile
import zlib
from pathlib import Path

GAME_ZIP = "assets/tap4fun.zip"
DATA_DIR = "tap4fun/galaxy_empire/AppOriginalData/Android/"
SCRIPTS_64 = DATA_DIR + "data1_64.pak"  # Lua 5.1 bytecode for 64-bit engines (8-byte size_t)

PAK_MAGIC = 0x9D7FE2AB
FLAG_ZLIB = 0x4


def _pak_entries(data: bytes) -> list[bytes]:
    """The contents of a tap4fun .pak archive (see docs/file-formats.md)."""
    magic, _, _, count = struct.unpack_from("<4I", data, 0)
    if magic != PAK_MAGIC:
        raise ValueError("not a tap4fun pak")
    out = []
    for i in range(count):
        off, size, _, flags = struct.unpack_from("<4I", data, 16 + i * 16)
        blob = data[off:off + size]
        if flags & FLAG_ZLIB:
            blob = zlib.decompress(blob[4:])
        out.append(blob)
    return out


def chunk_source(chunk: bytes) -> str:
    """The source name a compiled chunk carries, e.g. "@./GameUtils.lua"."""
    size_t = chunk[8]
    length = int.from_bytes(chunk[12:12 + size_t], "little")
    return chunk[12 + size_t:12 + size_t + length].rstrip(b"\0").decode("utf-8", "replace")


class Assets:
    def __init__(self, apk: Path):
        self.apk = Path(apk)
        with zipfile.ZipFile(self.apk) as z:
            self._game = zipfile.ZipFile(io.BytesIO(z.read(GAME_ZIP)))
        self.scripts: dict[str, bytes] = {}  # "GameUtils" -> chunk
        for chunk in _pak_entries(self._game.read(SCRIPTS_64)):
            name = chunk_source(chunk).rsplit("/", 1)[-1]
            if name.endswith(".lua"):
                self.scripts[name[:-4]] = chunk
        self._files = {n[len(DATA_DIR):]: n for n in self._game.namelist() if n.startswith(DATA_DIR)}

    def script(self, name: str) -> bytes | None:
        """A script by any of the names the game uses: "data1/GameUtils.tfl", "GameUtils.tfl"..."""
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        for suffix in (".tfl", ".lua", ".luac"):
            if base.endswith(suffix):
                base = base[:-len(suffix)]
        return self.scripts.get(base)

    def file(self, path: str) -> bytes | None:
        """A data file such as "data2/downloading.tfs" or "data2/text/menu.en"."""
        member = self._files.get(path.lstrip("./"))
        return self._game.read(member) if member else None

    def has_file(self, path: str) -> bool:
        return path.lstrip("./") in self._files
