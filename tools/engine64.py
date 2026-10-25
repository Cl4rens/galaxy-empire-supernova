"""The 64-bit Galaxy Empire app: the Supernova engine app plus the player's own game files.

The engine app (client/engine-rs, built with android/build_apk.py) holds nothing from the game.
This adds, from the player's original APK:

  assets/tap4fun.zip              the game files, patched like for the 32-bit app (server
                                  address and the script changes the server needs)
  assets/supernova/client.key     the client's response key (from its 32-bit library, which
                                  stays out: with it Android would install the app as 32-bit)
  assets/supernova/server.txt     the server address for the engine
"""

import hashlib
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
    with zipfile.ZipFile(engine_app) as app:
        if GAME_ZIP in app.namelist():
            raise ValueError(f"{engine_app} already holds game files; use the engine app as built")
        with zipfile.ZipFile(target, "w") as dst:
            for info in app.infolist():
                if info.filename.startswith("META-INF/"):
                    continue  # old signature, replaced when signing
                dst.writestr(info, app.read(info), compress_type=info.compress_type)
            # stored: the engine reads the game zip out of the app's APK in one piece
            dst.writestr(GAME_ZIP, game_data, compress_type=zipfile.ZIP_STORED)
            dst.writestr(KEY_MEMBER, key, compress_type=zipfile.ZIP_STORED)
            dst.writestr(SERVER_MEMBER, server_url(server) + "\n", compress_type=zipfile.ZIP_DEFLATED)
