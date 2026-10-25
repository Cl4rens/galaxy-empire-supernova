"""The 64-bit app: the engine app plus the game files, key and server from the player's APK."""

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import engine64  # noqa: E402

KEY = bytes(range(32))
LIB = "lib/armeabi-v7a/libgalaxyempire.so"


@pytest.fixture(autouse=True)
def known_key(monkeypatch):
    """A made-up key at a small offset, standing in for the real client's."""
    monkeypatch.setattr(engine64, "KEY_LOCATIONS", [(LIB, 8, 32, hashlib.sha256(KEY).hexdigest())])


def original_apk(path: Path, key: bytes = KEY) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", b"original manifest")
        z.writestr("classes.dex", b"original code")
        z.writestr(LIB, b"\0" * 8 + key + b"\0" * 8)
        z.writestr("assets/tap4fun.zip", b"original game files")
    return path


def engine_app(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", b"engine manifest")
        z.writestr("classes.dex", b"setup screen")
        z.writestr("lib/arm64-v8a/libsupernova.so", b"engine")
        z.writestr("META-INF/CERT.SF", b"old signature")
    return path


def test_app_holds_engine_game_key_and_server(tmp_path):
    out = tmp_path / "app.apk"
    engine64.build(original_apk(tmp_path / "ge.apk"), engine_app(tmp_path / "engine.apk"),
                   "192.168.0.10:8000", b"patched game files", out)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert z.read("AndroidManifest.xml") == b"engine manifest"
        assert z.read("classes.dex") == b"setup screen"
        assert z.read("lib/arm64-v8a/libsupernova.so") == b"engine"
        assert z.read("assets/tap4fun.zip") == b"patched game files"
        assert z.getinfo("assets/tap4fun.zip").compress_type == zipfile.ZIP_STORED
        assert z.read("assets/supernova/client.key") == KEY
        assert z.read("assets/supernova/server.txt") == b"http://192.168.0.10:8000\n"
    # no old signature, and no 32-bit library: Android would install the app as 32-bit
    assert not any(n.startswith("META-INF/") for n in names)
    assert LIB not in names


def test_unknown_key_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no known client key"):
        engine64.build(original_apk(tmp_path / "ge.apk", key=b"x" * 32), engine_app(tmp_path / "engine.apk"),
                       "host:8000", b"", tmp_path / "app.apk")


def test_engine_app_with_game_files_is_refused(tmp_path):
    engine = engine_app(tmp_path / "engine.apk")
    with zipfile.ZipFile(engine, "a") as z:
        z.writestr("assets/tap4fun.zip", b"someone's game")
    with pytest.raises(ValueError, match="already holds game files"):
        engine64.build(original_apk(tmp_path / "ge.apk"), engine, "host:8000", b"", tmp_path / "app.apk")


@pytest.mark.parametrize("given, url", [
    ("192.168.0.10:8000", "http://192.168.0.10:8000"),
    ("http://example.org:8000/", "http://example.org:8000"),
    ("https://example.org", "https://example.org"),
])
def test_server_url(given, url):
    assert engine64.server_url(given) == url
