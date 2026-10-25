"""The engine on a small fake game: our own Lua scripts in a pak inside a fake APK, a local HTTP
server that answers like Supernova. No original game files are needed."""

import base64
import io
import json
import struct
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from lupa.lua51 import LuaRuntime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from supernova_client import assets, engine  # noqa: E402

KEY = b"0123456789abcdef0123456789abcdef"

AUTO_UPDATE_INIT = r'''
require("Helper.tfl")
answers = {}
ext.RegisteFlashClass("Splash_t")
splash = Splash_t:new()
splash:Load("data2/splash.tfs", false)
splash:InvokeASCallback("_root", "setTitle", Helper.Greeting())
function Splash_t.fscommand(sender, cmd, arg)
  answers.pressed = cmd .. ":" .. arg
end
ext.http.requestBasic({"https://b.portal-platform.tap4fun.com/ge/url_get/online/world/servers.json",
  mode = "normal", method = "get", header = {sig = ext.ENC1("x")},
  callback = function(status, content) answers.servers = content.servers[1].name end})
ext.http.requestBasic({SERVER .. "/world/1/broken", mode = "normal", method = "post", body = "a=1",
  callback = function(status, content) answers.error = status .. " " .. content end})
loader = LuaLoader:new()
loader:LoadGameText("en", {"menu"})
answers.text = loader:GetGameText("LC_MENU_HELLO")
answers.secret = ext.ENC2("0123456789abcdef0123456789abcdefIGNORED", "Commander1")
ext.SaveGameData("ACCOUNT.tfl", "GameAccountUserData = {name = 'Commander1'}")
ext.dofile = ext.dofile or function(n) dofile(n) end
local f = loadfile("ACCOUNT.tfl")
f()
answers.saved = GameAccountUserData.name
frames = 0
function Update(dt) frames = frames + 1 end
'''
HELPER = '''Helper = {}
function Helper.Greeting() return "Hello from a script" end
'''


def compile_chunk(source: str, name: str) -> bytes:
    """A Lua 5.1 bytecode chunk, as the game's paks hold them (8-byte size_t on 64-bit hosts)."""
    lua = LuaRuntime(encoding=None)
    return lua.eval("function(s, n) return string.dump(assert(loadstring(s, n))) end")(source.encode(), name.encode())


def pak(entries: list[bytes]) -> bytes:
    names = b"\0".join(b"x" for _ in entries) + b"\0"
    names_off = 16 + 16 * len(entries)
    data_off = names_off + len(names)
    index, blobs = b"", b""
    for i, entry in enumerate(entries):
        index += struct.pack("<4I", data_off + len(blobs), len(entry), 2 * i, 0)
        blobs += entry
    return struct.pack("<4I", assets.PAK_MAGIC, data_off, names_off, len(entries)) + index + names + blobs


def text_table(strings: list[str]) -> bytes:
    out = struct.pack("<H", len(strings))
    for s in strings:
        raw = s.encode()
        out += struct.pack("<H", len(raw)) + raw
    return out


@pytest.fixture()
def fake_apk(tmp_path) -> Path:
    game = io.BytesIO()
    with zipfile.ZipFile(game, "w") as z:
        z.writestr(assets.SCRIPTS_64, pak([compile_chunk(AUTO_UPDATE_INIT, "@./AutoUpdateInit.lua"),
                                           compile_chunk(HELPER, "@./Helper.lua")]))
        z.writestr(assets.DATA_DIR + "data2/text/menu.idx", text_table(["HELLO"]))
        z.writestr(assets.DATA_DIR + "data2/text/menu.en", text_table(["Hello, Commander"]))
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr(assets.GAME_ZIP, game.getvalue())
    return apk


@pytest.fixture()
def server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen.append((self.command, self.path, self.rfile.read(length), {k.lower(): v for k, v in self.headers.items()}))
            if self.path.endswith("/servers.json"):
                body = base64.b64encode(engine._aes(KEY, json.dumps({"servers": [{"name": "Supernova"}]}).encode(),
                                                    False))
                self.send_response(200)
            else:
                body = b"Please log in again"
                self.send_response(401)
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _answer

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", seen
    httpd.shutdown()


def test_scripts_run_with_the_native_api(fake_apk, server, tmp_path):
    url, seen = server
    e = engine.Engine(fake_apk, url, tmp_path / "save", key=KEY, udid="ABCDEF")
    e.lua.execute(f'SERVER = "{url}"'.encode())  # other addresses are used as they are
    e.boot()
    answers = e.lua.globals()[b"answers"]
    assert e.run(5, until=lambda: answers[b"servers"] is not None and answers[b"error"] is not None)

    # requests: tap4fun's gateway is replaced by the server, answers are decrypted and parsed
    assert answers[b"servers"] == b"Supernova"
    assert answers[b"error"] == b"401 Please log in again"
    assert sorted(p for _, p, _, _ in seen) == ["/servers.json", "/world/1/broken"]
    assert [h for m, p, b, h in seen if m == "GET"][0]["sig"] == engine.hashlib.md5(b"x").hexdigest().upper()
    assert [b for m, p, b, h in seen if m == "POST"] == [b"a=1"]

    # Flash objects record what the game shows, fscommand sends button presses back
    assert ["_root", "setTitle", "Hello from a script"] in e.as_calls("Splash_t")
    e.fscommand("Splash_t", "btnStartPressed", "1")
    assert answers[b"pressed"] == b"btnStartPressed:1"

    # texts, ENC2 (the server's decrypt_param must read it), storage, the game loop
    assert answers[b"text"] == b"Hello, Commander"
    assert engine._aes(KEY, base64.b64decode(answers[b"secret"]), True) == b"Commander1"
    assert answers[b"saved"] == b"Commander1" and (tmp_path / "save" / "ACCOUNT.tfl").exists()
    assert e.lua.globals()[b"frames"] > 0
    assert not [name for name in e.missing if not name.startswith("global ")]


def test_scripts_are_found_by_every_name_the_game_uses(fake_apk):
    a = assets.Assets(fake_apk)
    assert a.script("data1/Helper.tfl") == a.script("Helper.tfl") == a.script("Helper") is not None
    assert a.script("Unknown.tfl") is None
    assert a.file("data2/text/menu.idx") == text_table(["HELLO"])


def test_lua_and_python_values():
    e = engine.Engine.__new__(engine.Engine)
    e.lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
    value = {"name": "Homeworld", "fleets": [{"type": 1, "amount": 5}], "ok": True}
    assert e.from_lua(e.to_lua(value)) == value
