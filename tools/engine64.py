"""The 64-bit Galaxy Empire app: the Supernova engine app plus the player's own game files.

The engine app (client/engine-rs, built with android/build_apk.py) holds nothing from the game.
This adds, from the player's original APK:

  assets/tap4fun.zip              the game files, patched like for the 32-bit app (server
                                  address and the script changes the server needs)
  assets/supernova/client.key     the client's response key (from its 32-bit library, which
                                  stays out: with it Android would install the app as 32-bit)
  assets/supernova/server.txt     the server address for the engine
  res/mipmap-*/ic_launcher.png    the game's launcher icon in place of the engine app's
"""

import hashlib
import re
import zipfile
from pathlib import Path

# as in server/supernova/crypto.py: (file, offset, length, SHA-256 of the key)
KEY_LOCATIONS = [
    ("lib/armeabi-v7a/libgalaxyempire.so", 0x1B1EA5, 32,
     "9a0e7ff690dbe1cf60884aaad9cdc3c10d9637eb1b9d3e0dd9c092a431fe7b08"),
]

GAME_ZIP = "assets/tap4fun.zip"
KEY_MEMBER = "assets/supernova/client.key"
SERVER_MEMBER = "assets/supernova/server.txt"
DEFAULT_ENGINE_APP = Path("client/engine-rs/target/android/supernova.apk")
ORIGINAL_ICON = "ge_old.png"  # res/drawable-<density>-v4/ge_old.png in Galaxy Empire 1.9.36
DENSITIES = ["ldpi", "mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"]
ENGINE_ICON = re.compile(r"res/mipmap-(?P<density>[a-z]+)(-v\d+)?/ic_launcher\.png")


def original_icons(original: zipfile.ZipFile) -> dict[str, bytes]:
    """The game's launcher icon by screen density ("mdpi" -> PNG)."""
    out = {}
    for name in original.namelist():
        parts = name.split("/")
        if len(parts) == 3 and parts[0] == "res" and parts[2] == ORIGINAL_ICON and parts[1].startswith("drawable-"):
            density = parts[1].split("-")[1]
            if density in DENSITIES:
                out[density] = original.read(name)
    return out


def icon_for(icons: dict[str, bytes], density: str) -> bytes | None:
    """The game's icon for a density, else the nearest larger one, else the largest."""
    if not icons:
        return None
    wanted = DENSITIES.index(density) if density in DENSITIES else len(DENSITIES) - 1
    larger = [d for d in DENSITIES[wanted:] if d in icons]
    return icons[larger[0] if larger else max(icons, key=DENSITIES.index)]


def client_key(original: zipfile.ZipFile) -> bytes:
    names = set(original.namelist())
    for member, offset, length, digest in KEY_LOCATIONS:
        if member in names:
            key = original.read(member)[offset:offset + length]
            if hashlib.sha256(key).hexdigest() == digest:
                return key
    raise ValueError("no known client key in the APK (unsupported version?)")


def server_url(server: str) -> str:
    """"192.168.0.10:8000" -> "http://192.168.0.10:8000" """
    url = server.strip().rstrip("/")
    return url if "://" in url else f"http://{url}"


def build(original: Path, engine_app: Path, server: str, game_data: bytes, target: Path) -> None:
    """Write the unsigned 64-bit app to `target`; `game_data` is the patched tap4fun.zip."""
    with zipfile.ZipFile(original) as src:
        key = client_key(src)
        icons = original_icons(src)
    with zipfile.ZipFile(engine_app) as app:
        if GAME_ZIP in app.namelist():
            raise ValueError(f"{engine_app} already holds game files; use the engine app as built")
        with zipfile.ZipFile(target, "w") as dst:
            for info in app.infolist():
                if info.filename.startswith("META-INF/"):
                    continue  # old signature, replaced when signing
                data = app.read(info)
                icon = ENGINE_ICON.fullmatch(info.filename)
                if icon:
                    data = icon_for(icons, icon["density"]) or data
                dst.writestr(info, data, compress_type=info.compress_type)
            # stored: the engine reads the game zip out of the app's APK in one piece
            dst.writestr(GAME_ZIP, game_data, compress_type=zipfile.ZIP_STORED)
            dst.writestr(KEY_MEMBER, key, compress_type=zipfile.ZIP_STORED)
            dst.writestr(SERVER_MEMBER, server_url(server) + "\n", compress_type=zipfile.ZIP_DEFLATED)
